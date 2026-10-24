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
    if (
        not parsed_device_id
        or not entity_id.startswith("sensor.")
        or not any(parsed_device_id.endswith("_" + suffix)
                   for suffix in ("authentication", "installed_titles", "connection"))
    ):
        return parsed_device_id
    if configuration_exists(parsed_device_id):
        return parsed_device_id
    for suffix in ("authentication", "installed_titles", "connection"):
        if parsed_device_id.endswith("_" + suffix):
            candidate = parsed_device_id.removesuffix("_" + suffix)
            if candidate and configuration_exists(candidate):
                return candidate
    return parsed_device_id
