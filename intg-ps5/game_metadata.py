"""PSN metadata for installed PS5 games.

The console installed-title inventory supplies npTitleId but often no name or
artwork. PXPlay PC obtains those fields by paging Sony's gamelist endpoint:
GET https://m.np.playstation.com/api/gamelist/v2/users/me/titles
This module independently implements that documented-by-client request.
"""
from __future__ import annotations

import logging
import time

from game_launch import normalize_title_id

_LOG = logging.getLogger(__name__)
_URL = "https://m.np.playstation.com/api/gamelist/v2/users/me/titles"
_PAGE_SIZE = 50
_MAX_PAGES = 16
_TOTAL_BUDGET = 14.0


def _title_metadata(row: object) -> tuple[str, dict[str, str]] | None:
    if not isinstance(row, dict):
        return None
    try:
        key = normalize_title_id(row.get("titleId"))
    except ValueError:
        return None
    title = str(row.get("localizedName") or row.get("name") or "").strip()[:255]
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
    label = metadata.get("name") or row.get("name")
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
