"""Create 85×85 inline SVG game icons for the Remote's Qt StyledText buttons.

Each cover is resized before embedding; full-sized Sony artwork is never
placed into an Integration API WebSocket payload. Game labels are XML-escaped.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import html
import logging
import re

_LOG = logging.getLogger(__name__)
SIZE = 85
MAX_DOWNLOAD = 2 * 1024 * 1024
MAX_ICONS = 384


def _svg_uri(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def make_icon(title: str, art_base64: str = "") -> str:
    """Return an 85px SVG containing artwork, or a centered title fallback."""
    if art_base64:
        image = f'<image x="0" y="0" width="85" height="85" preserveAspectRatio="xMidYMid meet" href="data:image/jpeg;base64,{art_base64}"/>'
    else:
        label = html.escape(title[:24], quote=True)
        image = (
            '<rect width="85" height="85" rx="9" fill="#26333b"/>'
            '<path d="M19 34h47a9 9 0 0 1 9 7l4 18q1 8-6 8-4 0-8-5l-6-8H25l-6 8q-4 5-8 5-7 0-6-8l4-18a9 9 0 0 1 10-7Z" '
            'stroke="#c08b57" stroke-width="3" fill="none"/>'
            '<path d="M26 40v12m-6-6h12" stroke="#c08b57" stroke-width="3"/>'
            f'<text x="42.5" y="24" text-anchor="middle" font-family="sans-serif" '
            f'font-size="9" fill="#ffffff">{label}</text>'
        )
    return _svg_uri(
        '<svg xmlns="http://www.w3.org/2000/svg" width="85" height="85" '
        'viewBox="0 0 85 85">' + image + '</svg>'
    )


def _resized_cover(url: str) -> str:
    if not isinstance(url, str) or not url.startswith("https://"):
        return ""
    try:
        import requests
        from PIL import Image, ImageOps
        from urllib.parse import urlparse
        parsed = urlparse(url)
        # Never fetch local or arbitrary endpoints from Sony metadata.
        host = (parsed.hostname or "").lower()
        trusted = ("playstation.net", "playstation.com", "sony.com")
        if not any(host == allowed or host.endswith("." + allowed) for allowed in trusted):
            return ""
        response = requests.get(url, timeout=5, allow_redirects=False,
                                headers={"Accept": "image/jpeg,image/png,image/webp"})
        if response.status_code != 200 or len(response.content) > MAX_DOWNLOAD:
            return ""
        with Image.open(BytesIO(response.content)) as original:
            if original.width > 4096 or original.height > 4096:
                return ""
            image = ImageOps.contain(original.convert("RGB"), (SIZE, SIZE))
            output = BytesIO()
            image.save(output, format="JPEG", quality=66, optimize=True)
            return base64.b64encode(output.getvalue()).decode("ascii")
    except Exception as exc:
        _LOG.debug("Artwork thumbnail not available: %s", type(exc).__name__)
        return ""


def build_game_icons(games: list[dict], metadata: dict, existing: dict[str, str] | None = None
                     ) -> dict[str, str]:
    """Generate bounded page tiles with parallel, compressed cover downloads."""
    from game_metadata import clean_game_title
    from game_launch import normalize_title_id

    existing = existing or {}
    records = []
    for game in games[:MAX_ICONS]:
        try:
            title = normalize_title_id(game.get("title_id"))
        except (ValueError, TypeError, AttributeError):
            continue
        info = metadata.get(title, {})
        name = clean_game_title(info.get("name") or game.get("name")) or title
        url = str(info.get("image") or game.get("image") or "")
        records.append((title, name, url))
    result = dict(existing)
    missing = [(t, n, u) for t, n, u in records if t not in result]
    # Fetch artwork only for new/missing cache entries, with limited concurrency.
    with ThreadPoolExecutor(max_workers=5) as pool:
        artworks = list(pool.map(lambda row: _resized_cover(row[2]), missing))
    for (title, label, _), cover in zip(missing, artworks):
        result[title] = make_icon(label, cover)
    return {title: result[title] for title, _, _ in records if title in result}


def styled_image(data_uri: str) -> str:
    """Qt's stock Text.AutoText supports Base64 SVG <img> inside text items."""
    # The value is generated internally, never arbitrary user HTML.
    if not data_uri.startswith("data:image/svg+xml;base64,"):
        raise ValueError("Invalid internal SVG icon")
    return (
        '<p align="center" style="margin:0">'
        f'<img src="{data_uri}" align="middle" width="85" height="85">'
        '</p>'
    )
