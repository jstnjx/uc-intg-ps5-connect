"""Unified PlayStation 5 driver for Unfolded Circle Remote Two/3 (MPL-2.0)."""
import asyncio
import logging
import os

from const import PSNConfig
from entity_ids import resolve_device_id
from media_player import PSNMediaPlayer
from remote_control import PS5Remote
from selection import PS5ProfileSelect, PS5LibraryViewSelect
from psn import PSNAccount
from sensor import (
    PSNAuthenticationSensor, PSNSensor, PSNInstalledCountSensor,
    PSNConnectionSensor, PSNActiveTitleIdSensor, PSNAPISensor, PSNTrophySensor,
)
from setup_flow import PSNSetupFlow
from switch import PSNSwitch
from ucapi_framework import BaseConfigManager, BaseIntegrationDriver, get_config_path


class PS5IntegrationDriver(BaseIntegrationDriver):
    """Resolve multiple sensors and selectors to their configured PS5."""

    async def on_device_connected(self, device_id: str) -> None:
        await super().on_device_connected(device_id)
        # The framework intentionally skips coordinator-style entities here.
        # Select widgets need a fresh options/current_option state message after
        # subscription, not just the initial available-entities description.
        for entity_id in self.get_entity_ids_for_device(device_id):
            entity = self.api.configured_entities.get(entity_id)
            if isinstance(entity, (PS5ProfileSelect, PS5LibraryViewSelect)):
                await entity.sync_state()

    def device_from_entity_id(self, entity_id: str) -> str | None:
        parsed = super().device_from_entity_id(entity_id)
        return resolve_device_id(
            entity_id,
            parsed,
            lambda candidate: self.get_device_config(candidate) is not None,
        )


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("UC_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    driver = PS5IntegrationDriver(
        device_class=PSNAccount,
        entity_classes=[
            PSNMediaPlayer, PSNSwitch, PSNSensor, PSNAuthenticationSensor,
            PSNInstalledCountSensor, PSNConnectionSensor,
            PSNActiveTitleIdSensor, PSNAPISensor,
            lambda cfg, dev: [
                PSNTrophySensor(cfg, dev, kind)
                for kind in PSNTrophySensor.TYPES
            ] if dev.has_psn else [],
            lambda cfg, dev: PS5Remote(cfg, dev) if dev.has_control else [],
            lambda cfg, dev: PS5ProfileSelect(cfg, dev) if dev.has_control else [],
            lambda cfg, dev: PS5LibraryViewSelect(cfg, dev) if dev.has_psn else [],
        ],
        driver_id="ps5_control_driver",
    )
    driver.config_manager = BaseConfigManager(
        get_config_path(driver.api.config_dir_path),
        driver.on_device_added,
        driver.on_device_removed,
        config_class=PSNConfig,
    )
    await driver.register_all_device_instances()
    await driver.api.init("driver.json", PSNSetupFlow.create_handler(driver))
    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
