"""Full PS5 control and PSN game-browser media player (MPL-2.0).

Adapted from Jack Powell's uc-intg-psn media player and sbr-labs' MIT
PS5 button mapping. No external daemon is required.
"""
from __future__ import annotations

import logging
from typing import Any

from const import PSNConfig
from controller import BUTTONS
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
        if not self._device.has_control:
            return StatusCodes.SERVICE_UNAVAILABLE
        params = params or {}
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
        titles, total = await self._device.get_game_library(
            limit=paging.limit, offset=paging.offset
        )
        items = [
            BrowseMediaItem(
                media_id=game.title_id or "",
                title=game.name or "Unknown game",
                subtitle=game.last_played_date_time.strftime("%d %b %Y")
                if game.last_played_date_time else None,
                media_class=media_player.MediaClass.GAME,
                can_browse=False,
                can_play=False,
                thumbnail=str(game.image_url) if game.image_url else None,
            )
            for game in titles
        ]
        return BrowseResults(
            media=BrowseMediaItem(
                media_id="psn_games",
                title="Played games",
                media_class=media_player.MediaClass.GAME,
                can_browse=True,
                items=items,
            ),
            pagination=Pagination(
                page=paging.page,
                limit=len(items),
                count=total if total is not None else 0,
            ),
        )
