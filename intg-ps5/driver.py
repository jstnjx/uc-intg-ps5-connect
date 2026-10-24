"""Unified PlayStation 5 driver for Unfolded Circle Remote Two/3 (MPL-2.0)."""
import asyncio
import logging
import os

from const import PSNConfig
from entity_ids import resolve_device_id
from media_player import PSNMediaPlayer
from psn import PSNAccount
from sensor import PSNAuthenticationSensor, PSNSensor
from setup_flow import PSNSetupFlow
from switch import PSNSwitch
from ucapi_framework import BaseConfigManager, BaseIntegrationDriver, get_config_path


class PS5IntegrationDriver(BaseIntegrationDriver):
    """Framework driver that understands the NPSSO sensor sub-entity suffix."""

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
        entity_classes=[PSNMediaPlayer, PSNSwitch, PSNSensor, PSNAuthenticationSensor],
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
