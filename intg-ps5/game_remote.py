"""Game library Remote pages with embedded 85px SVG covers in Qt StyledText.

The stock UC Remote UI uses Text.AutoText for native text buttons, which accepts
the same inline Base64 SVG <img> markup as the Custom Select integration.
"""
from __future__ import annotations
import logging
from typing import Any
from ucapi import StatusCodes, remote, media_player
from ucapi.entity import EntityTypes
from ucapi.ui import UiPage, Size, create_ui_text
from ucapi_framework import create_entity_id
from ucapi_framework.entities import RemoteEntity
from game_launch import normalize_title_id
from game_metadata import clean_game_title
from game_artwork import make_icon, styled_image

_LOG = logging.getLogger(__name__)
COLS = 4
ROWS = 6
PAGE_SIZE = COLS * ROWS
MAX_PAGES = 32


def game_command(title_id: str) -> str:
    command = "GAME_" + normalize_title_id(title_id)
    if len(command) > 20:
        raise ValueError("Command exceeds UC Core limit")
    return command


def make_game_pages(rows: list[dict], metadata: dict, icons: dict[str, str] | None = None) -> tuple[list[UiPage], list[str]]:
    pages: list[UiPage] = []
    commands: list[str] = []
    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    icons = icons or {}
    for row in rows:
        try:
            title = normalize_title_id(row.get("title_id"))
            command = game_command(title)
        except (ValueError, AttributeError, TypeError):
            continue
        if command in seen:
            continue
        seen.add(command)
        label = clean_game_title(metadata.get(title, {}).get("name") or row.get("name"))
        if not label:
            # Keep every installed title launchable, even when Sony has no
            # public title metadata. Never invent the game name.
            label = title
        icon = icons.get(title) or make_icon(label)
        items.append((styled_image(icon), command))
        commands.append(command)
        if len(items) >= MAX_PAGES * PAGE_SIZE:
            break
    for start in range(0, len(items), PAGE_SIZE):
        chunk = items[start:start + PAGE_SIZE]
        page_num = start // PAGE_SIZE + 1
        pages.append(UiPage(
            f"games_{page_num}", f"Games {page_num}",
            grid=Size(COLS, ROWS),
            items=[
                create_ui_text(label, i % COLS, i // COLS,
                               cmd=remote.create_send_cmd(cmd))
                for i, (label, cmd) in enumerate(chunk)
            ],
        ))
    return pages, commands


class PS5GameLibraryRemote(RemoteEntity):
    """Snapshot of installed titles, each as a 4x6 launch button."""

    def __init__(self, device_config, device):
        pages, commands = make_game_pages(device._installed_titles, device._recent_art,
                                          getattr(device, "_game_icons", {}))
        if not pages:
            raise ValueError("No installed titles available for a library Remote")
        super().__init__(
            create_entity_id(EntityTypes.REMOTE, f"{device_config.identifier}_game_library"),
            f"{device_config.name} Game Library",
            features=[remote.Features.SEND_CMD],
            attributes={remote.Attributes.STATE: remote.States.UNKNOWN},
            simple_commands=commands,
            ui_pages=pages,
        )
        self._device = device
        self._registered_commands = set(commands)
        self.subscribe_to_device(device)

    def refresh_layout(self, pages: list[UiPage], commands: list[str]) -> None:
        """Update in-memory routing after regenerating the Core UI pages."""
        from dataclasses import asdict
        self._registered_commands = set(commands)
        self.options["simple_commands"] = list(commands)
        self.options["user_interface"] = {"pages": [asdict(page) for page in pages]}

    async def sync_state(self):
        state = self._device.psn_state
        self.update({remote.Attributes.STATE:
                     remote.States.ON if state in (media_player.States.ON, media_player.States.PLAYING)
                     else remote.States.OFF if state == media_player.States.OFF
                     else remote.States.UNAVAILABLE})

    async def command(self, cmd_id: str, params: dict[str, Any] | None = None,
                      *, websocket: Any) -> StatusCodes:
        if cmd_id != remote.Commands.SEND_CMD:
            return StatusCodes.NOT_IMPLEMENTED
        params = params or {}
        value = params.get("command")
        if not isinstance(value, str) or value not in self._registered_commands:
            return StatusCodes.BAD_REQUEST
        title = value.removeprefix("GAME_")
        current = {row["title_id"] for row in self._device._installed_titles}
        if title not in current:
            return StatusCodes.NOT_FOUND
        if not self._device.can_launch_games:
            return StatusCodes.SERVICE_UNAVAILABLE
        try:
            return StatusCodes.OK if await self._device.launch_game(title) else StatusCodes.SERVER_ERROR
        except Exception:
            _LOG.warning("PS5 game launch failed for %s", title, exc_info=True)
            return StatusCodes.SERVICE_UNAVAILABLE
