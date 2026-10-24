"""Native Unfolded Circle Remote entity for a paired PlayStation 5."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ucapi import StatusCodes, media_player, remote
from ucapi.entity import EntityTypes
from ucapi.ui import Buttons, EntityCommand, Size, UiPage, create_btn_mapping, create_ui_text
from ucapi_framework import create_entity_id
from ucapi_framework.entities import RemoteEntity

from const import PSNConfig
from controller import BUTTONS
from psn import PSNAccount

_LOG = logging.getLogger(__name__)
SESSION_COMMANDS = ("SESSION_DISCONNECT", "SESSION_RECONNECT")
SIMPLE_COMMANDS = sorted(BUTTONS) + list(SESSION_COMMANDS)


def _send(name: str) -> EntityCommand:
    return remote.create_send_cmd(name)


def _button_mapping():
    return [
        create_btn_mapping(Buttons.POWER, EntityCommand("remote.toggle")),
        create_btn_mapping(Buttons.HOME, _send("PS")),
        create_btn_mapping(Buttons.BACK, _send("CIRCLE")),
        create_btn_mapping(Buttons.MENU, _send("OPTIONS")),
        create_btn_mapping(Buttons.DPAD_UP, _send("UP")),
        create_btn_mapping(Buttons.DPAD_DOWN, _send("DOWN")),
        create_btn_mapping(Buttons.DPAD_LEFT, _send("LEFT")),
        create_btn_mapping(Buttons.DPAD_RIGHT, _send("RIGHT")),
        create_btn_mapping(Buttons.DPAD_MIDDLE, _send("CROSS")),
        create_btn_mapping(Buttons.RED, _send("CIRCLE")),
        create_btn_mapping(Buttons.GREEN, _send("TRIANGLE")),
        create_btn_mapping(Buttons.BLUE, _send("CROSS")),
        create_btn_mapping(Buttons.YELLOW, _send("SQUARE")),
        create_btn_mapping(Buttons.CHANNEL_DOWN, _send("L1")),
        create_btn_mapping(Buttons.CHANNEL_UP, _send("R1")),
    ]


def _ui_page():
    names = [
        ("PS", 0, 0), ("OPTIONS", 2, 0), ("SHARE", 3, 0),
        ("UP", 1, 1), ("TRIANGLE", 3, 1),
        ("LEFT", 0, 2), ("CROSS", 1, 2), ("RIGHT", 2, 2), ("CIRCLE", 3, 2),
        ("DOWN", 1, 3), ("SQUARE", 3, 3),
        ("L1", 0, 4), ("R1", 3, 4),
        ("L2", 0, 5), ("TOUCHPAD", 1, 5), ("R2", 3, 5),
    ]
    return UiPage(
        "ps5_controls", "PS5 Controller", grid=Size(4, 6),
        items=[create_ui_text(
            name, x, y, cmd=_send(name),
        ) for name, x, y in names],
    )


def command_parameters(params: dict[str, Any], *, sequence: bool) -> tuple[list[str], int, int, int]:
    """Normalize inputs before any action. Never execute arbitrary command strings."""
    raw = params.get("sequence") if sequence else [params.get("command")]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 32:
        raise ValueError("Invalid PS5 command sequence")
    names = []
    for command in raw:
        if not isinstance(command, str):
            raise ValueError("Invalid controller command")
        name = command.strip().upper()
        if name not in BUTTONS and name not in SESSION_COMMANDS:
            raise ValueError("Unsupported PS5 command")
        names.append(name)
    def bounded(key: str, minimum: int, maximum: int, default: int) -> int:
        value = params.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError("Invalid PS5 command timing")
        return value
    repeat = bounded("repeat", 1, 3, 1)
    delay = bounded("delay", 0, 1500, 0)
    hold = bounded("hold", 0, 2500, 0)
    if len(names) * repeat > 64 or (len(names) * repeat * (delay + hold)) > 60000:
        raise ValueError("PS5 command sequence too long")
    if sequence and any(name in SESSION_COMMANDS for name in names):
        raise ValueError("Session commands cannot be part of a sequence")
    return names, repeat, delay, hold


class PS5Remote(RemoteEntity):
    """Native Remote controls and UI, sharing the existing Remote Play session."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        super().__init__(
            create_entity_id(EntityTypes.REMOTE, device_config.identifier),
            f"{device_config.name} Controller",
            features=[remote.Features.ON_OFF, remote.Features.TOGGLE, remote.Features.SEND_CMD],
            attributes={remote.Attributes.STATE: remote.States.UNKNOWN},
            simple_commands=SIMPLE_COMMANDS,
            button_mapping=_button_mapping(),
            ui_pages=[_ui_page()],
        )
        self._device = device
        self._command_lock = asyncio.Lock()
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        state = self._device.psn_state
        if state in (media_player.States.ON, media_player.States.PLAYING):
            mapped = remote.States.ON
        elif state == media_player.States.OFF:
            mapped = remote.States.OFF
        else:
            mapped = remote.States.UNAVAILABLE
        self.update({remote.Attributes.STATE: mapped})

    async def _issue(self, name: str, hold: int) -> bool:
        if name == "SESSION_DISCONNECT":
            return await self._device.release_session()
        if name == "SESSION_RECONNECT":
            return await self._device.reconnect_session()
        if not hold:
            return await self._device.send_button(name)
        if not await self._device.send_button(name, "press"):
            return False
        try:
            await asyncio.sleep(hold / 1000)
        finally:
            released = await self._device.send_button(name, "release")
        return released

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any,
    ) -> StatusCodes:
        if not self._device.has_control:
            return StatusCodes.SERVICE_UNAVAILABLE
        params = params or {}
        try:
            if cmd_id == remote.Commands.ON:
                success = await self._device.power_on()
            elif cmd_id == remote.Commands.OFF:
                success = await self._device.power_off()
            elif cmd_id == remote.Commands.TOGGLE:
                success = await self._device.power_toggle()
            elif cmd_id in (remote.Commands.SEND_CMD, remote.Commands.SEND_CMD_SEQUENCE):
                sequence = cmd_id == remote.Commands.SEND_CMD_SEQUENCE
                names, repeat, delay, hold = command_parameters(params, sequence=sequence)
                async with self._command_lock:
                    success = True
                    for _ in range(repeat):
                        for name in names:
                            if not await self._issue(name, hold):
                                success = False
                                break
                            if delay:
                                await asyncio.sleep(delay / 1000)
                        if not success:
                            break
            else:
                return StatusCodes.NOT_IMPLEMENTED
            return StatusCodes.OK if success else StatusCodes.SERVER_ERROR
        except ValueError:
            return StatusCodes.BAD_REQUEST
        except Exception:
            _LOG.exception("PS5 remote command failed: %s", cmd_id)
            return StatusCodes.SERVER_ERROR
