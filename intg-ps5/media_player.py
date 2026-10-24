"""Full PS5 control and PSN game-browser media player (MPL-2.0).

Adapted from Jack Powell's uc-intg-psn media player and sbr-labs' MIT
PS5 button mapping. No external daemon is required.
"""
from __future__ import annotations

import logging
from typing import Any

from const import PSNConfig
from controller import BUTTONS
from game_launch import GameLaunchError, ConsoleSelectionError, GameLaunchTimeout, normalize_title_id
from game_metadata import clean_game_title
from psn import PSNAccount
from ucapi import StatusCodes, media_player
from ucapi.api_definitions import Pagination, Paging
from ucapi.entity import EntityTypes
from ucapi.media_player import BrowseMediaItem, BrowseResults
from ucapi_framework import create_entity_id
from ucapi_framework.entities import MediaPlayerEntity

_LOG = logging.getLogger(__name__)
COMMAND_BUTTONS = {
    media_player.Commands.CURSOR_UP: "UP",
    media_player.Commands.CURSOR_DOWN: "DOWN",
    media_player.Commands.CURSOR_LEFT: "LEFT",
    media_player.Commands.CURSOR_RIGHT: "RIGHT",
    media_player.Commands.CURSOR_ENTER: "CROSS",
    media_player.Commands.BACK: "CIRCLE",
    media_player.Commands.HOME: "PS",
    media_player.Commands.MENU: "OPTIONS",
    media_player.Commands.INFO: "TOUCHPAD",
    media_player.Commands.PLAY_PAUSE: "CROSS",
}
SESSION_COMMANDS = ("SESSION_DISCONNECT", "SESSION_RECONNECT")
SIMPLE_COMMANDS = sorted(BUTTONS) + list(SESSION_COMMANDS)


class PSNMediaPlayer(MediaPlayerEntity):
    """Browsable game library with full on-device Remote Play button mapping."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        features = []
        if device.has_psn:
            features.append(media_player.Features.BROWSE_MEDIA)
        if device.can_launch_games:
            features.append(media_player.Features.PLAY_MEDIA)
        if device.has_control:
            features += [
                media_player.Features.ON_OFF,
                media_player.Features.TOGGLE,
                media_player.Features.DPAD,
                media_player.Features.HOME,
                media_player.Features.MENU,
                media_player.Features.INFO,
                media_player.Features.PLAY_PAUSE,
            ]
        super().__init__(
            create_entity_id(EntityTypes.MEDIA_PLAYER, device_config.identifier),
            device_config.name,
            features=features,
            attributes={
                media_player.Attributes.STATE: media_player.States.UNKNOWN,
                media_player.Attributes.MEDIA_TITLE: "",
                media_player.Attributes.MEDIA_ARTIST: "",
                media_player.Attributes.MEDIA_IMAGE_URL: "",
            },
            device_class=media_player.DeviceClasses.STREAMING_BOX,
            options={
                media_player.Options.SIMPLE_COMMANDS: SIMPLE_COMMANDS,
            } if device.has_control else {},
        )
        self._device = device
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        self.update({
            media_player.Attributes.STATE: self._device.psn_state,
            media_player.Attributes.MEDIA_TITLE: self._device.psn_media_title,
            media_player.Attributes.MEDIA_ARTIST: self._device.psn_media_artist,
            media_player.Attributes.MEDIA_IMAGE_URL: self._device.psn_media_image_url,
        })

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any
    ) -> StatusCodes:
        params = params or {}
        if cmd_id == media_player.Commands.PLAY_MEDIA:
            if not self._device.can_launch_games:
                return StatusCodes.NOT_IMPLEMENTED
            media_id = params.get(media_player.Attributes.MEDIA_ID) or params.get("media_id")
            if not media_id:
                return StatusCodes.BAD_REQUEST
            try:
                return (
                    StatusCodes.OK if await self._device.launch_game(str(media_id))
                    else StatusCodes.SERVER_ERROR
                )
            except ConsoleSelectionError as exc:
                _LOG.warning("Game launch needs console selection: %s", exc)
                return StatusCodes.CONFLICT
            except (GameLaunchError, GameLaunchTimeout) as exc:
                _LOG.warning("Game launch failed: %s", exc)
                return StatusCodes.SERVICE_UNAVAILABLE
            except ValueError:
                return StatusCodes.BAD_REQUEST
            except Exception:
                _LOG.exception("Unexpected direct PSN title launch error")
                return StatusCodes.SERVER_ERROR
        if not self._device.has_control:
            return StatusCodes.SERVICE_UNAVAILABLE
        try:
            if cmd_id == media_player.Commands.ON:
                ok = await self._device.power_on()
            elif cmd_id == media_player.Commands.OFF:
                ok = await self._device.power_off()
            elif cmd_id == media_player.Commands.TOGGLE:
                ok = await self._device.power_toggle()
            else:
                raw = str(
                    params.get("command") or params.get("simple_command")
                    or cmd_id
                ).upper()
                if raw in SESSION_COMMANDS:
                    ok = await (
                        self._device.release_session() if raw == "SESSION_DISCONNECT"
                        else self._device.reconnect_session()
                    )
                else:
                    button = COMMAND_BUTTONS.get(cmd_id)
                    if not button and raw in BUTTONS:
                        button = raw
                    if not button:
                        return StatusCodes.NOT_IMPLEMENTED
                    action = str(params.get("action") or "tap").lower()
                    ok = await self._device.send_button(button, action)
            return StatusCodes.OK if ok else StatusCodes.SERVER_ERROR
        except Exception:
            _LOG.exception("PS5 command failed: %s", cmd_id)
            return StatusCodes.SERVER_ERROR

    async def browse(
        self, options: media_player.BrowseOptions
    ) -> BrowseResults | StatusCodes:
        if not self._device.has_psn or not self._device.psn_authenticated:
            return StatusCodes.SERVICE_UNAVAILABLE
        paging: Paging = options.paging
        folder = options.media_id or self._device.library_view

        if folder == "psn_library":
            folders = [
                BrowseMediaItem(
                    media_id="installed_games",
                    title="Installed games & apps",
                    media_class=media_player.MediaClass.DIRECTORY,
                    can_browse=True, can_play=False,
                ),
                BrowseMediaItem(
                    media_id="psn_games",
                    title="Recently played",
                    media_class=media_player.MediaClass.DIRECTORY,
                    can_browse=True, can_play=False,
                ),
                BrowseMediaItem(
                    media_id="game_details",
                    title="Game details & playtime",
                    media_class=media_player.MediaClass.DIRECTORY,
                    can_browse=True, can_play=False,
                ),
            ]
            return BrowseResults(
                media=BrowseMediaItem(
                    media_id="psn_library", title="PlayStation library",
                    media_class=media_player.MediaClass.DIRECTORY,
                    can_browse=True,
                    items=folders[paging.offset:paging.offset + paging.limit],
                ),
                pagination=Pagination(
                    page=paging.page, limit=paging.limit, count=len(folders),
                ),
            )

        if folder == "installed_games":
            result = await self._device.get_installed_game_library(
                limit=paging.limit, offset=paging.offset,
            )
            if result is None:
                _LOG.warning("Installed PS5 library unavailable: %s", self._device.psn_error)
                return StatusCodes.SERVICE_UNAVAILABLE
            entries, total = result
            items = [
                BrowseMediaItem(
                    media_id=item["title_id"],
                    title=clean_game_title(item["name"]) or "Unknown PlayStation title",
                    media_class=media_player.MediaClass.GAME,
                    can_browse=False, can_play=self._device.can_launch_games,
                    thumbnail=item.get("image") or "icon://uc:gamepad-modern",
                )
                for item in entries
            ]
            return BrowseResults(
                media=BrowseMediaItem(
                    media_id="installed_games", title="Installed games & apps",
                    media_class=media_player.MediaClass.DIRECTORY,
                    can_browse=True, items=items,
                ),
                pagination=Pagination(page=paging.page, limit=paging.limit, count=total),
            )

        if folder == "game_details":
            result = await self._device.get_installed_game_library(
                limit=paging.limit, offset=paging.offset,
            )
            if result is None:
                return StatusCodes.SERVICE_UNAVAILABLE
            entries, total = result
            return BrowseResults(
                media=BrowseMediaItem(
                    media_id="game_details", title="Game details & playtime",
                    media_class=media_player.MediaClass.DIRECTORY,
                    can_browse=True,
                    items=[
                        BrowseMediaItem(
                            media_id=f"details:{game['title_id']}",
                            title=clean_game_title(game["name"]) or "Unknown PlayStation title",
                            media_class=media_player.MediaClass.GAME,
                            can_browse=True, can_play=False,
                            thumbnail=game.get("image") or "icon://uc:gamepad-modern",
                        )
                        for game in entries
                    ],
                ),
                pagination=Pagination(page=paging.page, limit=paging.limit, count=total),
            )

        if folder.startswith("details:"):
            try:
                title_id = normalize_title_id(folder.split(":", 1)[1])
            except ValueError:
                return StatusCodes.BAD_REQUEST
            details = await self._device.get_game_details(title_id)
            seconds = details.get("playtime")
            playtime = (
                f"Playtime: {seconds // 3600} h {(seconds % 3600) // 60} min"
                if isinstance(seconds, int) else "Playtime unavailable"
            )
            count = details.get("play_count")
            last = details.get("last_played")
            first = details.get("first_played")
            rows = [
                BrowseMediaItem(
                    media_id=title_id,
                    title="Launch game",
                    media_class=media_player.MediaClass.GAME,
                    can_browse=False, can_play=self._device.can_launch_games,
                    thumbnail=details.get("image") or "icon://uc:gamepad-modern",
                ),
                BrowseMediaItem(media_id=f"stats:playtime:{title_id}", title=playtime,
                                can_browse=False, can_play=False),
                BrowseMediaItem(
                    media_id=f"stats:count:{title_id}",
                    title=f"Times played: {count}" if count is not None else "Play count unavailable",
                    can_browse=False, can_play=False,
                ),
                BrowseMediaItem(
                    media_id=f"stats:last:{title_id}",
                    title=f"Last played: {last:%d %b %Y}" if last else "Last played unavailable",
                    can_browse=False, can_play=False,
                ),
                BrowseMediaItem(
                    media_id=f"stats:first:{title_id}",
                    title=f"First played: {first:%d %b %Y}" if first else "First played unavailable",
                    can_browse=False, can_play=False,
                ),
            ]
            return BrowseResults(
                media=BrowseMediaItem(
                    media_id=folder, title=clean_game_title(details.get("name"))
                    or "Game details", media_class=media_player.MediaClass.DIRECTORY,
                    can_browse=True, items=rows[paging.offset:paging.offset+paging.limit],
                ),
                pagination=Pagination(page=paging.page, limit=paging.limit, count=len(rows)),
            )

        if folder != "psn_games":
            return StatusCodes.NOT_FOUND

        titles, total = await self._device.get_game_library(
            limit=paging.limit, offset=paging.offset,
        )
        items = [
            BrowseMediaItem(
                media_id=game.title_id or "",
                title=clean_game_title(game.name) or "Unknown PlayStation title",
                media_class=media_player.MediaClass.GAME,
                can_browse=False,
                can_play=self._device.can_launch_games,
                thumbnail=str(game.image_url) if game.image_url else None,
            )
            for game in titles if game.title_id
        ]
        return BrowseResults(
            media=BrowseMediaItem(
                media_id="psn_games", title="Recently played",
                media_class=media_player.MediaClass.DIRECTORY,
                can_browse=True, items=items,
            ),
            pagination=Pagination(
                page=paging.page, limit=paging.limit,
                count=total if total is not None else 0,
            ),
        )
