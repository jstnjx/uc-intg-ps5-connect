"""PSN metadata for installed PS5 games.

The console installed-title inventory supplies npTitleId but often no name or
artwork. PXPlay PC obtains those fields by paging Sony's gamelist endpoint:
GET https://m.np.playstation.com/api/gamelist/v2/users/me/titles
This module independently implements that documented-by-client request.
"""
from __future__ import annotations

import html
import logging
import re
import time

import requests

from game_launch import normalize_title_id

_LOG = logging.getLogger(__name__)
_URL = "https://m.np.playstation.com/api/gamelist/v2/users/me/titles"
_PAGE_SIZE = 50
_MAX_PAGES = 16
_TOTAL_BUDGET = 14.0


_NOISE = re.compile(r"\\s*(?:\\((?:TM|R|PS4|PS5|PlayStation\\s*[45]|PlayStation\\s*[®™]\\s*[45])\\)|[™®])\\s*$", re.I)
_BAD_NAMES = {"unknown playstation title", "unknown game", "ps5_native_game", "ps4_game"}


def clean_game_title(raw: object) -> str:
    """Normalize HTML entities, platform branding and trademark suffixes."""
    name = html.unescape(str(raw or "")).strip()
    name = re.sub(r"<[^>]*>", "", name)
    name = re.sub(r"\\s+", " ", name)
    previous = None
    while name and previous != name:
        previous = name
        name = _NOISE.sub("", name).strip(" -–")
    if name.casefold() in _BAD_NAMES or re.fullmatch(r"(?:CUSA|PPSA|NPXS)\\d+(?:_00)?", name, re.I):
        return ""
    return name[:255]


def fetch_trophy_game_metadata(authenticator, title_ids: set[str]) -> dict[str, dict[str, str]]:
    """Sony's trophy title endpoint can name games absent from played-history."""
    found = {}
    ids = sorted(title_ids)
    for offset in range(0, min(len(ids), 120), 20):
        batch = ids[offset:offset + 20]
        try:
            response = authenticator.get(
                url="https://m.np.playstation.com/api/trophy/v1/users/me/titles/trophyTitles",
                params={"npTitleIds": ",".join(title + "_00" for title in batch)},
                timeout=5,
            )
            if response.status_code != 200:
                break
            for game in response.json().get("titles", []):
                try:
                    key = normalize_title_id(game.get("npTitleId"))
                except ValueError:
                    continue
                variants = game.get("trophyTitles") or []
                if not variants:
                    continue
                record = variants[0]
                found[key] = {
                    "name": clean_game_title(record.get("trophyTitleName")),
                    "image": record.get("trophyTitleIconUrl") or "",
                    "category": "",
                }
        except Exception as error:
            _LOG.debug("Sony trophy metadata not available: %s", type(error).__name__)
            break
    return found


def fetch_legacy_store_metadata(title_ids: set[str], *, max_items: int = 8) -> dict[str, dict[str, str]]:
    """2017-era Chihiro store fallback for CUSA IDs only. May be retired/404.

    Never infer a title or image from a bare identifier on failed requests.
    """
    result = {}
    for title in sorted(t for t in title_ids if t.startswith("CUSA"))[:max_items]:
        try:
            url = ("https://store.playstation.com/store/api/chihiro/"
                   f"00_09_000/titlecontainer/US/en/999/{title}_00")
            response = requests.get(url, timeout=3, allow_redirects=False)
            if response.status_code != 200:
                continue
            data = response.json()
            image = next((v.get("url", "") for v in data.get("images", [])
                          if isinstance(v, dict) and v.get("type") in (1, 2)
                          and str(v.get("url", "")).startswith("https://")), "")
            title_name = clean_game_title(data.get("name") or data.get("title"))
            if title_name or image:
                result[title] = {"name": title_name, "image": image, "category": ""}
        except (requests.RequestException, ValueError, TypeError):
            continue
    return result


def _title_metadata(row: object) -> tuple[str, dict[str, str]] | None:
    if not isinstance(row, dict):
        return None
    try:
        key = normalize_title_id(row.get("titleId"))
    except ValueError:
        return None
    title = clean_game_title(row.get("localizedName") or row.get("name"))
    image = str(row.get("localizedImageUrl") or row.get("imageUrl") or "").strip()
    # Remote browse thumbnails accept image URLs; never forward non-web schemes.
    if not image.startswith("https://"):
        image = ""
    category = str(row.get("category") or "").strip()[:60]
    return key, {"name": title, "image": image, "category": category}


def parse_titles_page(payload: object) -> tuple[list[tuple[str, dict[str, str]]], bool]:
    """Return valid title metadata and whether the page is usable."""
    if not isinstance(payload, dict) or not isinstance(payload.get("titles"), list):
        return [], False
    games = []
    for row in payload["titles"]:
        parsed = _title_metadata(row)
        if parsed:
            games.append(parsed)
    return games, True


def _merge_metadata(old: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    # A later page can provide localized artwork that earlier pages omitted.
    return {name: old.get(name) or new.get(name, "") for name in ("name", "image", "category")}


def fetch_installed_metadata(authenticator, required_ids: set[str],
                             *, budget: float = _TOTAL_BUDGET) -> dict[str, dict[str, str]]:
    """Resolve titles using PSN account history, paged until found or exhausted.

    Missing games are not fabricated: Sony may withhold metadata for games not
    played by the current account.  Always return partial valid results after
    transient failure instead of blocking the Remote's game browser.
    """
    if not required_ids:
        return {}
    target = {normalize_title_id(title) for title in required_ids}
    metadata: dict[str, dict[str, str]] = {}
    deadline = time.monotonic() + max(1.0, budget)
    for page in range(_MAX_PAGES):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            response = authenticator.get(
                url=_URL,
                params={"limit": _PAGE_SIZE, "offset": page * _PAGE_SIZE},
                headers={"Accept": "application/json"},
                timeout=min(7.0, max(1.0, remaining)),
                allow_redirects=False,
            )
            if response.status_code in (401, 403):
                _LOG.warning("PSN installed-title metadata authentication failed")
                break
            if not 200 <= response.status_code < 300:
                _LOG.warning("PSN title metadata unavailable: HTTP %s", response.status_code)
                break
            raw = response.json()
            entries, valid = parse_titles_page(raw)
            if not valid:
                break
            for key, info in entries:
                if key in target:
                    metadata[key] = _merge_metadata(metadata.get(key, {}), info)
            unresolved = [
                key for key in target
                if not metadata.get(key, {}).get("name")
                or not metadata.get(key, {}).get("image")
            ]
            if not unresolved or len(raw["titles"]) < _PAGE_SIZE:
                break
        except Exception as exc:
            _LOG.debug("PSN title-metadata page %s failed: %s", page, type(exc).__name__)
            break
    return metadata


def display_installed_title(row: dict[str, str], metadata: dict[str, str] | None = None) -> dict[str, object]:
    """Present a friendly fallback instead of a bare raw ID or fake artwork."""
    metadata = metadata or {}
    title_id = row["title_id"]
    label = clean_game_title(metadata.get("name") or row.get("name"))
    missing_name = not label or label.strip().upper() == title_id.upper()
    image = metadata.get("image") or row.get("image") or ""
    return {
        **row,
        "name": "Unknown PlayStation title" if missing_name else label,
        "image": image if image.startswith("https://") else "",
        "title_id": title_id,
        "category": metadata.get("category") or row.get("category") or "",
        "missing_name": missing_name,
    }
