"""Select entities for registered PS5 profiles and the default library view."""
from __future__ import annotations

import logging
from typing import Any

from ucapi import StatusCodes, select
from ucapi.entity import EntityTypes
from ucapi_framework import create_entity_id
from ucapi_framework.entities import SelectEntity

from const import PSNConfig
from psn import PSNAccount

_LOG = logging.getLogger(__name__)

LIBRARY_LABELS = {
    "installed_games": "Installed games & apps",
    "psn_games": "Recently played",
}


def resolve_selection(cmd_id: str, params: dict[str, Any], options: list[str], current: str) -> str:
    """Validate UC Select commands and cycle only through advertised options."""
    if not options:
        raise ValueError("No selectable options")
    if cmd_id == select.Commands.SELECT_OPTION:
        candidate = params.get("option")
        if candidate not in options:
            raise ValueError("Unknown select option")
        return candidate
    if cmd_id == select.Commands.SELECT_FIRST:
        return options[0]
    if cmd_id == select.Commands.SELECT_LAST:
        return options[-1]
    if cmd_id in (select.Commands.SELECT_NEXT, select.Commands.SELECT_PREVIOUS):
        index = options.index(current) if current in options else 0
        delta = 1 if cmd_id == select.Commands.SELECT_NEXT else -1
        return options[(index + delta) % len(options)]
    raise NotImplementedError


class PS5ProfileSelect(SelectEntity):
    """Choose a *registered profile on this console*, never another console."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        options = device.profile_options
        super().__init__(
            create_entity_id(EntityTypes.SELECT, f"{device_config.identifier}_profile"),
            f"{device_config.name} Profile",
            attributes={
                select.Attributes.STATE: select.States.ON if options else select.States.UNAVAILABLE,
                select.Attributes.CURRENT_OPTION: device.selected_profile if options else "",
                select.Attributes.OPTIONS: options,
            },
        )
        self._device = device
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        options = self._device.profile_options
        # Force the options list into configured entity state. UC's available
        # entity description deliberately excludes attributes, and an unchanged
        # options list otherwise does not generate a state-change event.
        self.update({
            select.Attributes.STATE: select.States.ON if options else select.States.UNAVAILABLE,
            select.Attributes.CURRENT_OPTION: self._device.selected_profile if options else "",
            select.Attributes.OPTIONS: options,
        }, force=True)

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any,
    ) -> StatusCodes:
        if not self._device.has_control:
            return StatusCodes.SERVICE_UNAVAILABLE
        try:
            new_value = resolve_selection(
                cmd_id, params or {}, self._device.profile_options, self._device.selected_profile,
            )
            success = await self._device.select_profile(new_value)
            if success:
                await self.sync_state()
            return StatusCodes.OK if success else StatusCodes.CONFLICT
        except ValueError:
            return StatusCodes.BAD_REQUEST
        except NotImplementedError:
            return StatusCodes.NOT_IMPLEMENTED
        except Exception:
            _LOG.exception("PS5 profile selection failed")
            return StatusCodes.SERVER_ERROR


class PS5LibraryViewSelect(SelectEntity):
    """Select which PSN game library folder opens by default."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        current = LIBRARY_LABELS[device.library_view]
        super().__init__(
            create_entity_id(EntityTypes.SELECT, f"{device_config.identifier}_library_view"),
            f"{device_config.name} Library View",
            attributes={
                select.Attributes.STATE: select.States.ON if device.has_psn else select.States.UNAVAILABLE,
                select.Attributes.CURRENT_OPTION: current,
                select.Attributes.OPTIONS: list(LIBRARY_LABELS.values()),
            },
        )
        self._device = device
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        self.update({
            select.Attributes.STATE: select.States.ON if self._device.has_psn else select.States.UNAVAILABLE,
            select.Attributes.CURRENT_OPTION: LIBRARY_LABELS[self._device.library_view],
            select.Attributes.OPTIONS: list(LIBRARY_LABELS.values()),
        }, force=True)

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any,
    ) -> StatusCodes:
        if not self._device.has_psn:
            return StatusCodes.SERVICE_UNAVAILABLE
        options = list(LIBRARY_LABELS.values())
        current = LIBRARY_LABELS[self._device.library_view]
        try:
            selection = resolve_selection(cmd_id, params or {}, options, current)
            value = next(key for key, label in LIBRARY_LABELS.items() if label == selection)
            success = await self._device.select_library_view(value)
            if success:
                await self.sync_state()
            return StatusCodes.OK if success else StatusCodes.SERVER_ERROR
        except ValueError:
            return StatusCodes.BAD_REQUEST
        except NotImplementedError:
            return StatusCodes.NOT_IMPLEMENTED
        except Exception:
            _LOG.exception("PS5 library view selection failed")
            return StatusCodes.SERVER_ERROR
