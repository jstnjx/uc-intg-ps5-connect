"""Entity ID helpers for the unified PS5 integration.

A single PS5 owns several UC entities. The NPSSO authentication sensor
uses a suffix that is NOT part of the configured device identifier.
"""
from __future__ import annotations

from typing import Callable


def resolve_device_id(
    entity_id: str,
    parsed_device_id: str | None,
    configuration_exists: Callable[[str], bool],
) -> str | None:
    """Map a PSN authentication sensor to its configured PS5 device.

    Preserve all other entity IDs and identifiers that genuinely end in
    '_authentication'. Resolve the suffix only when a matching base device
    is known, so device IDs cannot be accidentally truncated.
    """
    if not parsed_device_id or configuration_exists(parsed_device_id):
        return parsed_device_id
    suffixes = (
        "authentication", "installed_titles", "connection",
        "active_title_id", "psn_health", "profile", "library_view",
        "trophy_level", "platinum_trophies", "earned_trophies",
        "current_game_trophies", "game_library",
    )
    if not entity_id.startswith(("sensor.", "select.", "remote.")):
        return parsed_device_id
    for suffix in sorted(suffixes, key=len, reverse=True):
        if parsed_device_id.endswith("_" + suffix):
            base = parsed_device_id[: -(len(suffix) + 1)]
            if base and configuration_exists(base):
                return base
    return parsed_device_id
