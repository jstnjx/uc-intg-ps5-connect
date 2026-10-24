"""Combined local PlayStation 5 and PSN account device.

MPL-2.0; PSN access is based on Jack Powell's uc-intg-psn.
Local control is provided by controller.py, without any external daemon.
"""
from __future__ import annotations

import asyncio
import logging
from itertools import islice
from typing import Any

from api import PlayStationNetwork, PlayStationNetworkData
from const import PSNConfig
from controller import PS5Controller
from game_launch import SonyDirectLauncher, LaunchUnavailable, LaunchRejected, normalize_title_id
from launch_recorder import GameLaunchRecorder, RecordingError, normalize_steps, replay
from psnawp_api.core.psnawp_exceptions import PSNAWPAuthenticationError
from ucapi import media_player
from ucapi_framework.device import PollingDevice

_LOG = logging.getLogger(__name__)


class PSNAccount(PollingDevice):
    """A PS5 console, optionally enriched by a linked PSN account."""

    def __init__(self, device_config: PSNConfig, loop=None, config_manager=None, driver=None):
        super().__init__(device_config, loop, poll_interval=10, config_manager=config_manager, driver=driver)
        self._psn: PlayStationNetwork | None = None
        self._psn_data: PlayStationNetworkData | None = None
        self._control: PS5Controller | None = None
        self._launcher = SonyDirectLauncher(
            device_config.launch_endpoint, device_config.launch_device_id,
        )
        self._recorder = GameLaunchRecorder()
        self._macro_lock = asyncio.Lock()
        self._macro_config = config_manager
        self._connect_lock = asyncio.Lock()
        self._next_psn_poll = 0.0
        self._total_game_count: int | None = None
        self.psn_state = media_player.States.UNKNOWN
        self.psn_media_title = ""
        self.psn_media_artist = ""
        self.psn_media_image_url = ""
        self.psn_authenticated: bool | None = None

    @property
    def identifier(self) -> str:
        return self._device_config.identifier

    @property
    def name(self) -> str:
        return self._device_config.name

    @property
    def address(self) -> str | None:
        return self._device_config.ps_device.get("device_ip")

    @property
    def is_on(self) -> bool:
        return self.psn_state in (media_player.States.ON, media_player.States.PLAYING)

    @property
    def log_id(self) -> str:
        return self.name

    @property
    def has_control(self) -> bool:
        p = self._device_config.ps_device
        return bool(p.get("device_ip") and p.get("profiles") and p.get("user"))

    @property
    def has_psn(self) -> bool:
        return bool(self._device_config.npsso)

    @property
    def can_launch_games(self) -> bool:
        # A media player can execute configured macros or a configured PSN
        # transport, but it must not promise direct launch by title ID.
        return self.has_control and self.has_psn

    def can_launch_game(self, title_id: str) -> bool:
        try:
            title_id = normalize_title_id(title_id)
        except ValueError:
            return False
        return title_id in self._device_config.launch_macros or self._launcher.enabled

    def start_game_recording(self, title_id: str) -> bool:
        if not self.has_control or self._macro_lock.locked():
            return False
        self._recorder.start(title_id)
        _LOG.info("[%s] Started local launch recording for %s", self.log_id, title_id)
        return True

    def cancel_game_recording(self) -> bool:
        if not self._recorder.active:
            return False
        self._recorder.cancel()
        return True

    def save_game_recording(self) -> bool:
        if not self._recorder.active or not self._macro_config:
            return False
        try:
            name, steps = self._recorder.finish()
        except RecordingError:
            _LOG.warning("[%s] Cannot save invalid game launch recording", self.log_id)
            return False
        config = self._macro_config.get(self.identifier)
        if config is None:
            return False
        config.launch_macros = {**config.launch_macros, name: steps}
        if not self._macro_config.update(config):
            return False
        self._device_config.launch_macros = config.launch_macros
        _LOG.info("[%s] Stored %d local launch steps for %s", self.log_id, len(steps), name)
        return True

    async def launch_game(self, title_id: str) -> bool:
        title_id = normalize_title_id(title_id)
        if not self.has_control:
            raise LaunchUnavailable("PS5 Remote Play control is not paired")
        if self._recorder.active:
            raise LaunchUnavailable("Finish or cancel the active launch recording first")
        # Local recorded sequence is reliable as a *sequence of inputs*, not
        # proof that the selected game started. Keep PSN transport optional.
        steps = self._device_config.launch_macros.get(title_id)
        if steps is not None:
            if not self._control:
                raise LaunchUnavailable("PS5 Remote Play controller is offline")
            async with self._macro_lock:
                return await asyncio.wait_for(
                    replay(steps, self._control.button),
                    timeout=100,
                )
        if not self._launcher.enabled or not self._psn or not self.psn_authenticated:
            raise LaunchUnavailable("No recorded launch or verified PSN transport for this game")
        return await self._launcher.launch(title_id, self._psn.psn.authenticator)

    @property
    def device_type(self) -> str:
        return "PS5" if self.has_control else "PSN"

    async def connect(self) -> bool:
        if self.is_connected:
            return True
        async with self._connect_lock:
            if self.is_connected:
                return True
            return await super().connect()

    async def establish_connection(self) -> None:
        """Never let cloud authentication failures disable local PS5 commands."""
        if self.has_control:
            self._control = PS5Controller(self._device_config.ps_device)
        if self.has_psn:
            try:
                self._psn = await asyncio.to_thread(
                    PlayStationNetwork, self._device_config.npsso,
                    asyncio.get_running_loop(),
                )
                self.psn_authenticated = True
            except PSNAWPAuthenticationError:
                _LOG.warning("[%s] PSN authentication expired; local PS5 control remains available", self.log_id)
                self.psn_authenticated = False
                self._psn = None
            except Exception:
                _LOG.exception("[%s] PSN initialization failed; local control remains available", self.log_id)
                self.psn_authenticated = False
                self._psn = None
        await self.poll_device()

    async def disconnect(self) -> None:
        await super().disconnect()
        if self._control:
            await self._control.close()
            self._control = None
        if self._psn:
            await asyncio.to_thread(self._psn.close)
            self._psn = None

    async def poll_device(self) -> None:
        """Local DDP every 10s; PSN presence at most every 45s."""
        local_online: bool | None = None
        if self._control:
            try:
                status = await self._control.status()
                local_online = bool(status and self._control.awake)
            except Exception:
                _LOG.debug("[%s] Local discovery/status unavailable", self.log_id, exc_info=True)
        now = asyncio.get_running_loop().time()
        if self._psn and now >= self._next_psn_poll:
            self._next_psn_poll = now + 45
            try:
                self._psn_data = await asyncio.to_thread(self._psn.get_data)
                self.psn_authenticated = True
            except PSNAWPAuthenticationError:
                self.psn_authenticated = False
                self._psn_data = None
                _LOG.warning("[%s] PSN authentication expired", self.log_id)
            except Exception:
                _LOG.warning("[%s] PSN presence fetch failed", self.log_id, exc_info=True)
        data = self._psn_data
        presence_online = bool(
            data and data.platform
            and data.platform.get("onlineStatus") == "online"
        )
        active = bool(data and data.title_metadata and data.title_metadata.get("npTitleId"))
        # A local console's LAN status is authoritative, even for private PSN presence.
        if local_online is True:
            self.psn_state = media_player.States.PLAYING if active else media_player.States.ON
        elif local_online is False:
            self.psn_state = media_player.States.OFF
        elif self._control:
            self.psn_state = media_player.States.UNAVAILABLE
        else:
            self.psn_state = (
                media_player.States.PLAYING if presence_online and active
                else media_player.States.ON if presence_online
                else media_player.States.OFF if data
                else media_player.States.UNAVAILABLE
            )
        title = data.title_metadata if active and self.psn_state == media_player.States.PLAYING else {}
        self.psn_media_title = str(title.get("titleName") or "")
        self.psn_media_artist = str(title.get("format") or "")
        self.psn_media_image_url = str(
            title.get("conceptIconUrl") or title.get("npTitleIconUrl") or ""
        )
        # PS5 firmware versions that still publish DDP application info can
        # provide titles even when PSN is disabled or unavailable.
        if self._control and local_online and not self.psn_media_title:
            status = self._control.device.status or {}
            self.psn_media_title = str(status.get("running-app-name") or "")
            self.psn_media_artist = "PS5" if self.psn_media_title else ""
            try:
                media = self._control.device.media_info
                self.psn_media_image_url = str(media.cover_art or "") if media else ""
            except Exception:
                pass
            if self.psn_media_title:
                self.psn_state = media_player.States.PLAYING
        self._state = str(self.psn_state)
        self.push_update()

    async def get_game_library(self, limit: int = 10, offset: int = 0) -> tuple[list, int | None]:
        if self._psn is None:
            return [], self._total_game_count
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        try:
            def get_page():
                iterator = self._psn.client.title_stats(offset=offset, page_size=limit)
                titles = list(islice(iterator, limit))
                return titles, getattr(iterator, "_total_item_count", 0)
            titles, total = await asyncio.to_thread(get_page)
            self._total_game_count = int(total) if total else self._total_game_count
            return titles, self._total_game_count
        except Exception:
            _LOG.warning("[%s] Game-library request failed", self.log_id, exc_info=True)
            return [], self._total_game_count

    async def power_on(self) -> bool:
        if not self._control:
            return False
        success = await self._control.wake()
        if success:
            self.psn_state = media_player.States.ON
            self.push_update()
        return success

    async def power_off(self) -> bool:
        if not self._control:
            return False
        success = await self._control.standby()
        if success:
            self.psn_state = media_player.States.OFF
            self.psn_media_title = ""
            self.psn_media_image_url = ""
            self.push_update()
        return success

    async def power_toggle(self) -> bool:
        return await (self.power_off() if self.is_on else self.power_on())

    async def go_home(self) -> bool:
        return await self.send_button("PS")

    async def send_button(self, button: str, action: str = "tap") -> bool:
        if not self._control or self._macro_lock.locked():
            return False
        ok = await self._control.button(button, action)
        if ok and self._recorder.active:
            try:
                self._recorder.add(button, action)
            except RecordingError:
                _LOG.warning("[%s] Launch recording exceeded limits; discarded", self.log_id)
        return ok

    async def release_session(self) -> bool:
        return await self._control.release_session() if self._control else False

    async def reconnect_session(self) -> bool:
        return await self._control.reconnect() if self._control else False
