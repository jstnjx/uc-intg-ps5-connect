"""Synchronize only auto-generated PS5 game pages to a configured Core Remote.

Core Integration API exposes entity state and *initial* UI metadata only.
Configured Remote UI pages require the authenticated Core REST endpoints.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote
import logging
import re

_LOG = logging.getLogger(__name__)
_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_GAME_PAGE = re.compile(r"^games_[1-9][0-9]*$")


def valid_refresh_time(value: str) -> bool:
    return isinstance(value, str) and bool(_TIME.fullmatch(value))


def due_for_refresh(config, now: datetime | None = None) -> bool:
    if not config.game_refresh_enabled or not config.remote_api_key or not config.remote_url:
        return False
    try:
        local = (now or datetime.now(tz=ZoneInfo(config.game_refresh_timezone))).astimezone(
            ZoneInfo(config.game_refresh_timezone)
        )
    except (ValueError, KeyError):
        return False
    if not valid_refresh_time(config.game_refresh_time):
        return False
    return (
        local.strftime("%H:%M") >= config.game_refresh_time
        and config.game_refresh_last_date != local.date().isoformat()
    )


def refresh_date(config) -> str:
    return datetime.now(ZoneInfo(config.game_refresh_timezone)).date().isoformat()


def as_core_page(page) -> dict:
    """Keep the page JSON aligned with UC's ActivityUserInterfacePageUpdate."""
    record = asdict(page)
    return {"name": record["name"], "grid": record["grid"], "items": record["items"]}


async def update_core_library_pages(config, entity_id: str, pages: list) -> list[str]:
    """Refresh owned game pages without touching user-defined Remote pages.

    Core assigns IDs to newly POSTed pages. Keep those IDs in the integration
    configuration rather than assuming Core uses our proposed page IDs.
    """
    if not pages:
        return []
    from unfurled.api import CoreAPI
    prefix = f"remotes/{quote(entity_id, safe='')}/ui/pages"
    async with CoreAPI(config.remote_url, api_key=config.remote_api_key, timeout=15) as api:
        current = await api.request("GET", prefix)
        if not isinstance(current, list):
            raise ValueError("Core returned an invalid Remote UI page list")
        existing = {
            str(item.get("page_id")): item for item in current
            if isinstance(item, dict) and item.get("page_id")
        }
        # Adopt pages from the original integration registration, or use IDs
        # returned by Core during previous daily synchronizations.
        owned = list(config.game_refresh_page_ids)
        for name in sorted(existing):
            if _GAME_PAGE.fullmatch(name) and name not in owned:
                owned.append(name)
        current_owned = [value for value in owned if value in existing]
        output_ids = []
        for index, page in enumerate(pages):
            payload = as_core_page(page)
            if index < len(current_owned):
                ident = current_owned[index]
                previous = existing[ident]
                if any(previous.get(k) != v for k, v in payload.items()):
                    await api.request("PATCH", prefix + "/" + quote(ident, safe=""),
                                      json=payload)
            else:
                response = await api.request("POST", prefix, json=payload)
                ident = str(response.get("page_id", "")) if isinstance(response, dict) else ""
                if not ident:
                    raise ValueError("Core did not return a generated page ID")
            output_ids.append(ident)
        # Only remove pages which the integration owns. Unknown/custom pages
        # and pages owned by other integrations remain untouched.
        for stale in current_owned[len(pages):]:
            await api.request("DELETE", prefix + "/" + quote(stale, safe=""))
        _LOG.info("Updated %d PS5 game-library pages in Core", len(output_ids))
    return output_ids
