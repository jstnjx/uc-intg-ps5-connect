"""
PlayStation Network Game Title Sensor entity for Unfolded Circle Remote Two.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from const import PSNConfig
from psn import PSNAccount
from ucapi import sensor
from ucapi.entity import EntityTypes
from ucapi_framework import create_entity_id
from ucapi_framework.entities import SensorEntity

_LOG = logging.getLogger(__name__)


class PSNSensor(SensorEntity):
    """Sensor entity that reports the currently playing game title."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        """
        Initialize PSN game title sensor entity.

        :param device_config: PSN device configuration
        :param device: PSN account device interface
        """
        entity_id = create_entity_id(EntityTypes.SENSOR, device_config.identifier)

        super().__init__(
            entity_id,
            f"{device_config.name} Game",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options={sensor.Options.CUSTOM_UNIT: "game"},
        )

        self._device: PSNAccount = device
        self._device_config = device_config

        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync sensor state from device to Remote."""
        from ucapi import media_player  # avoid circular at module level

        title = self._device.psn_media_title

        if self._device.psn_state == media_player.States.PLAYING and title:
            state = sensor.States.ON
        else:
            state = sensor.States.UNKNOWN

        self.update(
            {
                sensor.Attributes.STATE: state,
                sensor.Attributes.VALUE: title,
            }
        )


class PSNAuthenticationSensor(SensorEntity):
    """Sensor entity that reports whether the NPSSO token is valid."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        """Initialize the NPSSO authentication-status sensor."""
        entity_id = create_entity_id(
            EntityTypes.SENSOR, f"{device_config.identifier}_authentication"
        )

        super().__init__(
            entity_id,
            f"{device_config.name} NPSSO Token",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "Checking",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options={},
        )

        self._device: PSNAccount = device
        self._device_config = device_config

        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync NPSSO authentication state from the device."""
        authenticated = self._device.psn_authenticated
        if authenticated is True:
            state = sensor.States.ON
            value = "Valid"
        elif authenticated is False:
            state = sensor.States.ON
            value = "Invalid - update NPSSO token"
        else:
            state = sensor.States.UNKNOWN
            value = "Not configured" if not self._device.has_psn else "Checking"

        self.update(
            {
                sensor.Attributes.STATE: state,
                sensor.Attributes.VALUE: value,
            }
        )


class PSNInstalledCountSensor(SensorEntity):
    """Per-console installed title count; unknown != zero installed titles."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        super().__init__(
            create_entity_id(EntityTypes.SENSOR, f"{device_config.identifier}_installed_titles"),
            f"{device_config.name} Installed Titles",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options={sensor.Options.CUSTOM_UNIT: "titles"},
        )
        self._device = device
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        count = self._device.installed_count
        known = self._device.installed_inventory_available
        self.update({
            sensor.Attributes.STATE: sensor.States.ON if known else sensor.States.UNKNOWN,
            sensor.Attributes.VALUE: count if known and count is not None else "",
        })


class PSNConnectionSensor(SensorEntity):
    """Distinguish console reachability from an active Remote Play session."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        super().__init__(
            create_entity_id(EntityTypes.SENSOR, f"{device_config.identifier}_connection"),
            f"{device_config.name} Connection",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "Checking",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options={},
        )
        self._device = device
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        from ucapi import media_player
        device = self._device
        if not device.has_control:
            state, value = sensor.States.ON, "PSN only"
        elif device._control and device._control.connected:
            state, value = sensor.States.ON, "Remote Play connected"
        elif device.psn_state == media_player.States.OFF:
            state, value = sensor.States.ON, "Offline / rest"
        elif device.psn_state in (media_player.States.ON, media_player.States.PLAYING):
            state, value = sensor.States.ON, "PS5 reachable (controller idle)"
        else:
            state, value = sensor.States.UNKNOWN, "Console unreachable"
        self.update({
            sensor.Attributes.STATE: state,
            sensor.Attributes.VALUE: value,
        })


class PSNActiveTitleIdSensor(SensorEntity):
    """PSN/console title ID for game-aware activity rules."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        super().__init__(
            create_entity_id(EntityTypes.SENSOR, f"{device_config.identifier}_active_title_id"),
            f"{device_config.name} Active Title ID",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options={},
        )
        self._device = device
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        from ucapi import media_player

        active = self._device.psn_state == media_player.States.PLAYING
        title_id = self._device.psn_media_title_id if active else ""
        self.update({
            sensor.Attributes.STATE: sensor.States.ON if title_id else sensor.States.UNKNOWN,
            sensor.Attributes.VALUE: title_id,
        })


class PSNAPISensor(SensorEntity):
    """PSN login and API availability status, separate from LAN connectivity."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        super().__init__(
            create_entity_id(EntityTypes.SENSOR, f"{device_config.identifier}_psn_health"),
            f"{device_config.name} PSN API Health",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "Checking",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options={},
        )
        self._device = device
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        device = self._device
        if not device.has_psn:
            state, value = sensor.States.UNKNOWN, "Not configured"
        elif device.psn_authenticated is True:
            state = sensor.States.ON
            value = "Degraded" if device.psn_error else "Connected"
        elif device.psn_authenticated is False:
            state = sensor.States.ON
            error = device.psn_error.lower()
            if "expired" in error or "update npsso" in error:
                value = "Authentication expired"
            elif "sign-in" in error or "authorization" in error:
                value = "Authentication failed"
            else:
                value = "Reconnecting"
        else:
            state, value = sensor.States.UNKNOWN, "Checking"
        self.update({
            sensor.Attributes.STATE: state,
            sensor.Attributes.VALUE: value,
        })


class PSNTrophySensor(SensorEntity):
    """Account trophy totals or current active game's trophy percentage."""

    TYPES = {
        "trophy_level": ("Trophy Level", "level", ""),
        "platinum_trophies": ("Platinum Trophies", "platinum", "trophies"),
        "earned_trophies": ("Earned Trophies", "earned_total", "trophies"),
        "current_game_trophies": ("Current Game Trophies", "active_progress", "%"),
    }

    def __init__(self, device_config: PSNConfig, device: PSNAccount, kind: str):
        self._kind = kind
        display_name, _, units = self.TYPES[kind]
        super().__init__(
            create_entity_id(EntityTypes.SENSOR, f"{device_config.identifier}_{kind}"),
            f"{device_config.name} {display_name}",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options={sensor.Options.CUSTOM_UNIT: units} if units else {},
        )
        self._device = device
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        data = self._device.trophy_summary
        if data is None or (self._kind == "current_game_trophies"
                           and self._device.active_trophy_progress is None):
            self.update({
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "",
            })
            return
        if self._kind == "earned_trophies":
            value = sum(data.get(key, 0) for key in ("bronze", "silver", "gold", "platinum"))
        elif self._kind == "current_game_trophies":
            value = self._device.active_trophy_progress
        else:
            value = data[self.TYPES[self._kind][1]]
        self.update({
            sensor.Attributes.STATE: sensor.States.ON,
            sensor.Attributes.VALUE: value,
        })
