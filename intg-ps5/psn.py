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
from controller import PS5Controller, account_id_to_base64
from game_launch import SonyGameLauncher, GameLaunchError, ConsoleSelectionError, GameLaunchTimeout, PSNAuthorizationError, normalize_title_id
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
        self._launcher = SonyGameLauncher()
        self._connect_lock = asyncio.Lock()
        self._profile_lock = asyncio.Lock()
        self._next_psn_poll = 0.0
        self._total_game_count: int | None = None
        self._recent_art: dict[str, dict[str, str]] = {}
        self._installed_titles: list[dict[str, str]] = []
        self.installed_count: int | None = None
        self.installed_inventory_available = False
        self._inventory_expiry = 0.0
        self._inventory_lock = asyncio.Lock()
        self._inventory_task: asyncio.Task | None = None
        self.psn_error = ""
        self._psn_failures = 0
        self._psn_retry_after = 0.0
        self._reauth_lock = asyncio.Lock()
        self.console_duid = device_config.launch_device_id
        self.console_name = device_config.console_name
        self.console_host_id = device_config.console_host_id
        self.psn_state = media_player.States.UNKNOWN
        self.psn_media_title = ""
        self.psn_media_title_id = ""
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
        return self.has_control and self.has_psn

    @property
    def profile_options(self) -> list[str]:
        """Only registered Remote Play profiles for this console are selectable."""
        if not self.has_control:
            return []
        profiles = self._device_config.ps_device.get("profiles", {})
        return sorted(str(name) for name, data in profiles.items()
                      if isinstance(data, dict) and data.get("id"))

    @property
    def selected_profile(self) -> str:
        return str(self._device_config.ps_device.get("user") or "")

    @property
    def library_view(self) -> str:
        selected = self._device_config.library_view
        return selected if selected in ("installed_games", "psn_games") else "installed_games"

    async def select_library_view(self, selection: str) -> bool:
        if selection not in ("installed_games", "psn_games") or not self.has_psn:
            return False
        if selection == self.library_view:
            return True
        if not self._config_manager:
            return False
        config = self._config_manager.get(self.identifier)
        if not config:
            return False
        config.library_view = selection
        if not self._config_manager.update(config):
            return False
        self._device_config.library_view = selection
        self.push_update()
        return True

    async def select_profile(self, selection: str) -> bool:
        """Switch this PS5's paired local profile, never its console/DUID."""
        if selection not in self.profile_options or not self.has_control:
            return False
        async with self._profile_lock:
            if selection == self.selected_profile:
                return True
            if not self._config_manager:
                return False
            config = self._config_manager.get(self.identifier)
            if not config:
                return False
            # The PSN cloud account and Remote Play identity must not diverge.
            profile = config.ps_device.get("profiles", {}).get(selection, {})
            if config.npsso and config.psn_account_id:
                try:
                    if profile.get("id") != account_id_to_base64(config.psn_account_id):
                        _LOG.warning("[%s] Refusing profile from a different PSN account", self.log_id)
                        return False
                except ValueError:
                    return False
            controller = self._control
            if controller:
                await controller.release_session(pause_seconds=0)
            config.ps_device = {**config.ps_device, "user": selection}
            if not self._config_manager.update(config):
                return False
            self._device_config.ps_device = config.ps_device
            if controller:
                controller.username = selection
            self.push_update()
            return True

    def _local_console_name(self) -> str:
        if self._control:
            try:
                status = self._control.device.status or {}
                return str(status.get("host-name") or self.console_name or "")
            except (AttributeError, TypeError):
                pass
        return self.console_name or ""

    def _save_identity(self, *, duid: str = "", name: str = "",
                       host_id: str = "", account_id: str = "",
                       ip: str = "") -> None:
        """Persist only confirmed, non-conflicting identifiers to the same device."""
        if not self._config_manager:
            return
        config = self._config_manager.get(self.identifier)
        if config is None:
            return
        dirty = False
        for attr, new in (
            ("launch_device_id", duid), ("console_name", name),
            ("console_host_id", host_id), ("psn_account_id", account_id),
        ):
            old = getattr(config, attr, "")
            if new and old and old.casefold() != new.casefold() and attr != "console_name":
                _LOG.warning("[%s] Console identity conflict for %s; retaining existing value", self.log_id, attr)
                continue
            if new and old != new:
                setattr(config, attr, new)
                setattr(self._device_config, attr, new)
                dirty = True
        if ip and config.ps_device.get("device_ip") != ip:
            config.ps_device = {**config.ps_device, "device_ip": ip}
            self._device_config.ps_device = config.ps_device
            dirty = True
        if dirty and not self._config_manager.update(config):
            _LOG.warning("[%s] Failed to persist console identity", self.log_id)
        self.console_duid = self._device_config.launch_device_id
        self.console_name = self._device_config.console_name
        self.console_host_id = self._device_config.console_host_id

    async def _recover_psn(self) -> bool:
        """Rebuild an expired/disconnected PSN session using stored NPSSO.

        Only one recovery runs at a time. Local Remote Play remains unaffected.
        """
        if not self.has_psn:
            return False
        async with self._reauth_lock:
            loop = asyncio.get_running_loop()
            client = None
            try:
                client = await asyncio.to_thread(PlayStationNetwork, self._device_config.npsso, loop)
                await asyncio.to_thread(client.get_user)
            except Exception as exc:
                if client is not None:
                    await asyncio.to_thread(client.close)
                _LOG.warning("[%s] PSN reauthentication unsuccessful: %s", self.log_id, type(exc).__name__)
                self.psn_error = "PSN sign-in unavailable; update NPSSO if expired"
                self.psn_authenticated = False
                self._psn_failures += 1
                self._psn_retry_after = loop.time() + min(900, 60 * (2 ** min(self._psn_failures, 4)))
                return False
            previous = self._psn
            self._psn = client
            self.psn_authenticated = True
            self.psn_error = ""
            self._psn_failures = 0
            self._psn_retry_after = 0.0
            self._next_psn_poll = 0.0
            if previous and previous is not client:
                await asyncio.to_thread(previous.close)
            return True

    def _invalidate_inventory(self, reason: str) -> None:
        """Avoid presenting a stale count as a live installed-title reading."""
        self.installed_inventory_available = False
        self.installed_count = None
        self.psn_error = reason
        self.push_update()

    async def refresh_installed_titles(self, *, force: bool = False) -> bool:
        """Load the per-console inventory; never conflate missing data and zero games."""
        if not self.has_psn:
            return False
        now = asyncio.get_running_loop().time()
        if not force and now < self._inventory_expiry:
            return self.installed_inventory_available
        async with self._inventory_lock:
            now = asyncio.get_running_loop().time()
            if not force and now < self._inventory_expiry:
                return self.installed_inventory_available
            if not self._psn or self.psn_authenticated is False:
                if now < self._psn_retry_after or not await self._recover_psn():
                    self._invalidate_inventory(self.psn_error or "PSN authentication unavailable")
                    return False
            try:
                duid, name, installed, available = await self._launcher.fetch_console_inventory(
                    self._psn.psn.authenticator,
                    duid=self.console_duid,
                    local_name=self._local_console_name(),
                )
            except PSNAuthorizationError:
                if not await self._recover_psn():
                    self._inventory_expiry = now + 120
                    self._invalidate_inventory(self.psn_error or "PSN authorization failed")
                    return False
                try:
                    duid, name, installed, available = await self._launcher.fetch_console_inventory(
                        self._psn.psn.authenticator,
                        duid=self.console_duid, local_name=self._local_console_name(),
                    )
                except GameLaunchError as exc:
                    self._inventory_expiry = now + 60
                    self._invalidate_inventory(str(exc))
                    return False
            except GameLaunchError as exc:
                self._inventory_expiry = now + 60
                self._invalidate_inventory(str(exc))
                _LOG.warning("[%s] PSN inventory: %s", self.log_id, exc)
                return False
            self._save_identity(duid=duid, name=name)
            self._installed_titles = installed
            self.installed_inventory_available = available
            self.installed_count = len(installed) if available else None
            self._inventory_expiry = now + 300
            self.psn_error = "" if available else "Sony did not include installed-title data for this console"
            self.push_update()
            return available

    async def get_installed_game_library(
        self, limit: int = 20, offset: int = 0,
    ) -> tuple[list[dict[str, str]], int] | None:
        if not await self.refresh_installed_titles():
            return None
        # Build a bounded history index for artwork. Games never played on
        # this account still appear by title ID, not as fabricated entries.
        if self._psn and not self._recent_art:
            try:
                def history():
                    rows = list(islice(self._psn.client.title_stats(offset=0, page_size=50), 150))
                    return rows
                rows = await asyncio.to_thread(history)
                for item in rows:
                    try:
                        key = normalize_title_id(item.title_id)
                    except (ValueError, AttributeError):
                        continue
                    self._recent_art[key] = {
                        "name": str(item.name or ""),
                        "image": str(item.image_url or ""),
                    }
            except Exception:
                _LOG.debug("[%s] Optional game metadata lookup unavailable", self.log_id)
        limit, offset = max(1, min(int(limit), 50)), max(0, int(offset))
        pages = []
        for title in self._installed_titles[offset: offset + limit]:
            art = self._recent_art.get(title["title_id"], {})
            pages.append({
                **title,
                "name": art.get("name") or title["name"] or title["title_id"],
                "image": art.get("image") or "",
            })
        return pages, len(self._installed_titles)

    async def launch_game(self, title_id: str) -> bool:
        if not self.can_launch_games:
            raise GameLaunchError("PSN account and paired PS5 are required for launching games")
        if not self._psn or self.psn_authenticated is False:
            if not await self._recover_psn():
                raise GameLaunchError("PSN authentication is unavailable")
        for attempt in range(2):
            try:
                return await self._launcher.launch(
                    authenticator=self._psn.psn.authenticator,
                    title_id=title_id,
                    duid=self.console_duid,
                    local_name=self._local_console_name(),
                )
            except PSNAuthorizationError:
                if attempt or not await self._recover_psn():
                    raise
        return False

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
                self.psn_error = "PSN authentication expired"
                self._psn_retry_after = asyncio.get_running_loop().time() + 120
                self._psn = None
            except Exception:
                _LOG.exception("[%s] PSN initialization failed; local control remains available", self.log_id)
                self.psn_authenticated = False
                self.psn_error = "PSN initialization failed"
                self._psn_retry_after = asyncio.get_running_loop().time() + 120
                self._psn = None
        await self.poll_device()

    async def disconnect(self) -> None:
        if self._inventory_task and not self._inventory_task.done():
            self._inventory_task.cancel()
            try:
                await self._inventory_task
            except asyncio.CancelledError:
                pass
        self._inventory_task = None
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
                if status:
                    host_id = str(status.get("host-id") or "").strip()
                    if host_id and self.console_host_id and host_id.casefold() != self.console_host_id.casefold():
                        _LOG.warning("[%s] LAN console host ID changed unexpectedly; ignoring status", self.log_id)
                        local_online = None
                    else:
                        self._save_identity(host_id=host_id, name=str(status.get("host-name") or ""),
                                            ip=self._control.host)
                        if self._config_manager:
                            config = self._config_manager.get(self.identifier)
                            if config and host_id and not config.ps_device.get("host_id"):
                                config.ps_device = {**config.ps_device, "host_id": host_id}
                                if self._config_manager.update(config):
                                    self._device_config.ps_device = config.ps_device
            except Exception:
                _LOG.debug("[%s] Local discovery/status unavailable", self.log_id, exc_info=True)
        now = asyncio.get_running_loop().time()
        if (
            self.has_psn and self.psn_authenticated is False
            and now >= self._psn_retry_after
        ):
            await self._recover_psn()
        if self._psn and now >= self._next_psn_poll:
            self._next_psn_poll = now + 45
            try:
                self._psn_data = await asyncio.to_thread(self._psn.get_data)
                self.psn_authenticated = True
                self.psn_error = ""
                self._psn_failures = 0
                account_id = str(self._psn_data.account_id or "")
                # A PSN account must never be silently rebound.
                if account_id:
                    self._save_identity(account_id=account_id)
            except PSNAWPAuthenticationError:
                self.psn_authenticated = False
                self._psn_data = None
                self._invalidate_inventory("PSN token expired")
                self._psn_retry_after = now + 120
                self._psn_failures += 1
                self._next_psn_poll = self._psn_retry_after
                _LOG.warning("[%s] PSN authentication expired", self.log_id)
            except Exception as exc:
                self._psn_failures += 1
                self._next_psn_poll = now + min(600, 45 * (2 ** min(4, self._psn_failures)))
                self.psn_error = "PSN temporarily unreachable"
                _LOG.warning("[%s] PSN presence fetch failed: %s", self.log_id, type(exc).__name__)
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
        self.psn_media_title_id = str(title.get("npTitleId") or "")
        self.psn_media_artist = str(title.get("format") or "")
        self.psn_media_image_url = str(
            title.get("conceptIconUrl") or title.get("npTitleIconUrl") or ""
        )
        # PS5 firmware versions that still publish DDP application info can
        # provide titles even when PSN is disabled or unavailable.
        if self._control and local_online and not self.psn_media_title:
            status = self._control.device.status or {}
            self.psn_media_title = str(status.get("running-app-name") or "")
            self.psn_media_title_id = str(status.get("running-app-titleid") or "")
            self.psn_media_artist = "PS5" if self.psn_media_title else ""
            try:
                media = self._control.device.media_info
                self.psn_media_image_url = str(media.cover_art or "") if media else ""
            except Exception:
                pass
            if self.psn_media_title:
                self.psn_state = media_player.States.PLAYING
        self._state = str(self.psn_state)
        if (
            self._psn and self.psn_authenticated is True
            and now >= self._inventory_expiry
            and (self._inventory_task is None or self._inventory_task.done())
        ):
            self._inventory_task = asyncio.create_task(self.refresh_installed_titles())
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
            self.psn_media_title_id = ""
            self.psn_media_image_url = ""
            self.push_update()
        return success

    async def power_toggle(self) -> bool:
        return await (self.power_off() if self.is_on else self.power_on())

    async def go_home(self) -> bool:
        return await self.send_button("PS")

    async def send_button(self, button: str, action: str = "tap") -> bool:
        if not self._control:
            return False
        return await self._control.button(button, action)

    async def release_session(self) -> bool:
        return await self._control.release_session() if self._control else False

    async def reconnect_session(self) -> bool:
        return await self._control.reconnect() if self._control else False
