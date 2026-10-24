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
from game_metadata import (fetch_installed_metadata, display_installed_title, fetch_trophy_game_metadata, fetch_legacy_store_metadata, clean_game_title)
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
        self._game_details: dict[str, dict] = {}
        self._details_lock = asyncio.Lock()
        self.trophy_summary: dict[str, int] | None = None
        self.active_trophy_progress: int | None = None
        self._trophy_title_id = ""
        self._trophy_expiry = 0.0
        self._trophy_task: asyncio.Task | None = None
        self._game_details: dict[str, dict] = {}
        self._details_lock = asyncio.Lock()
        self.trophy_summary: dict[str, int] | None = None
        self.active_trophy_progress: int | None = None
        self._trophy_title_id = ""
        self._trophy_expiry = 0.0
        self._trophy_task: asyncio.Task | None = None
        self._metadata_expiry = 0.0
        self._metadata_task: asyncio.Task | None = None
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
        """Expose valid registered users plus the currently paired user.

        pyremoteplay versions and migrated configs can carry profile records in
        slightly different shapes. The selected, successfully paired user must
        never disappear from the chooser merely due to missing metadata.
        """
        if not self.has_control:
            return []
        profiles = self._device_config.ps_device.get("profiles", {})
        selected = self.selected_profile
        valid = set()
        if isinstance(profiles, dict):
            for name, data in profiles.items():
                if isinstance(data, dict) and data.get("id"):
                    valid.add(str(name))
        if selected:
            # has_control already verifies this user has paired credentials.
            valid.add(selected)
        return sorted(valid)

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
            if available and self._metadata_expiry <= now and (
                self._metadata_task is None or self._metadata_task.done()
            ):
                self._metadata_expiry = now + 900
                self._metadata_task = asyncio.create_task(self._enrich_installed_titles())
            self.installed_count = len(installed) if available else None
            self._inventory_expiry = now + 300
            self.psn_error = "" if available else "Sony did not include installed-title data for this console"
            self.push_update()
            return available

    async def _enrich_installed_titles(self) -> None:
        """Get localized Sony title names/artwork with a bounded request budget."""
        if not self._psn or not self._installed_titles:
            return
        try:
            required = {item["title_id"] for item in self._installed_titles}
            metadata = await asyncio.to_thread(
                fetch_installed_metadata, self._psn.psn.authenticator, required,
            )
            # Preserve the PSNAWP path used before v0.6.1 as a fallback for
            # sparse game-list responses or transient Sony API changes.
            unresolved = {
                title for title in required
                if not (metadata.get(title, {}).get("name") and metadata.get(title, {}).get("image"))
            }
            if unresolved:
                try:
                    def history():
                        return list(islice(
                            self._psn.client.title_stats(offset=0, page_size=50), 300,
                        ))
                    for item in await asyncio.to_thread(history):
                        try:
                            title = normalize_title_id(item.title_id)
                        except (ValueError, AttributeError):
                            continue
                        if title not in unresolved:
                            continue
                        current = metadata.get(title, {})
                        metadata[title] = {
                            "name": current.get("name") or clean_game_title(item.name),
                            "image": current.get("image") or str(item.image_url or ""),
                            "category": current.get("category") or "",
                        }
                except Exception:
                    _LOG.debug("[%s] PSNAWP metadata fallback unavailable", self.log_id)
            remaining = {
                title for title in required
                if not (metadata.get(title, {}).get("name") and metadata.get(title, {}).get("image"))
            }
            if remaining:
                trophies = await asyncio.to_thread(
                    fetch_trophy_game_metadata, self._psn.psn.authenticator, remaining,
                )
                for title, info in trophies.items():
                    prev = metadata.get(title, {})
                    metadata[title] = {
                        key: prev.get(key) or info.get(key, "")
                        for key in ("name", "image", "category")
                    }
                remaining = {
                    title for title in remaining
                    if not (metadata.get(title, {}).get("name") and metadata.get(title, {}).get("image"))
                }
            if remaining:
                old_store = await asyncio.to_thread(fetch_legacy_store_metadata, remaining)
                for title, info in old_store.items():
                    prev = metadata.get(title, {})
                    metadata[title] = {
                        key: prev.get(key) or info.get(key, "")
                        for key in ("name", "image", "category")
                    }
            # Keep earlier metadata if a transient PSN failure returns nothing.
            for title, fields in metadata.items():
                existing = self._recent_art.get(title, {})
                self._recent_art[title] = {
                    k: fields.get(k) or existing.get(k, "")
                    for k in ("name", "image", "category")
                }
            if metadata:
                self.push_update()
            await self._publish_game_remote()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.debug("[%s] Installed-title enrichment unavailable", self.log_id, exc_info=True)
        finally:
            # Cache misses too; never hammer Sony on each media-browser visit.
            self._metadata_expiry = asyncio.get_running_loop().time() + 900

    async def get_installed_game_library(
        self, limit: int = 20, offset: int = 0,
    ) -> tuple[list[dict[str, str]], int] | None:
        if not await self.refresh_installed_titles():
            return None
        now = asyncio.get_running_loop().time()
        if (
            self._psn and now >= self._metadata_expiry
            and (self._metadata_task is None or self._metadata_task.done())
        ):
            self._metadata_expiry = now + 900
            self._metadata_task = asyncio.create_task(self._enrich_installed_titles())
        if self._metadata_task and not self._metadata_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._metadata_task), timeout=12)
            except asyncio.TimeoutError:
                _LOG.debug("[%s] PSN artwork loading continues in background", self.log_id)
        limit, offset = max(1, min(int(limit), 50)), max(0, int(offset))
        pages = [
            display_installed_title(title, self._recent_art.get(title["title_id"]))
            for title in self._installed_titles[offset:offset + limit]
        ]
        return pages, len(self._installed_titles)

    async def _publish_game_remote(self) -> None:
        """Advertise a named game library after Sony metadata is populated.

        UC user_interface is initial configuration, not a runtime attribute;
        refreshing installed titles cannot overwrite a user's customized UI.
        """
        if not self.driver or not self._installed_titles or not self.can_launch_games:
            return
        try:
            from game_remote import PS5GameLibraryRemote
            identifier = f"remote.{self.identifier}_game_library"
            if not self.driver.api.available_entities.contains(identifier):
                self.driver.add_entity(PS5GameLibraryRemote(self._device_config, self))
        except ValueError:
            return
        except Exception:
            _LOG.debug("[%s] Game library registration failed", self.log_id, exc_info=True)

    async def get_game_details(self, title_id: str) -> dict:
        """Look up real PSN playtime and timestamps for the selected game."""
        title_id = normalize_title_id(title_id)
        if title_id in self._game_details:
            return dict(self._game_details[title_id])
        async with self._details_lock:
            if title_id in self._game_details:
                return dict(self._game_details[title_id])
            result = {
                "title_id": title_id, "name": "", "image": "",
                "playtime": None, "play_count": None,
                "first_played": None, "last_played": None,
            }
            if self._psn:
                try:
                    def stats():
                        return list(islice(self._psn.client.title_stats(page_size=100), 800))
                    for item in await asyncio.to_thread(stats):
                        try:
                            key = normalize_title_id(item.title_id)
                        except (ValueError, AttributeError):
                            continue
                        if key != title_id:
                            continue
                        duration = getattr(item, "play_duration", None)
                        result.update(
                            name=clean_game_title(getattr(item, "name", "")),
                            image=str(getattr(item, "image_url", "") or ""),
                            playtime=int(duration.total_seconds()) if duration is not None else None,
                            play_count=getattr(item, "play_count", None),
                            first_played=getattr(item, "first_played_date_time", None),
                            last_played=getattr(item, "last_played_date_time", None),
                        )
                        break
                except Exception:
                    _LOG.debug("[%s] PSN playtime lookup failed", self.log_id)
            cached = self._recent_art.get(title_id, {})
            result["name"] = result["name"] or clean_game_title(cached.get("name"))
            result["image"] = result["image"] or cached.get("image", "")
            self._game_details[title_id] = result
            return dict(result)

    async def _refresh_trophies(self) -> None:
        """Periodically refresh aggregate trophies and active-title progress."""
        try:
            if not self._psn:
                return
            summary = await asyncio.to_thread(self._psn.client.trophy_summary)
            totals = summary.earned_trophies
            self.trophy_summary = {
                "level": int(summary.trophy_level),
                "level_progress": int(summary.progress),
                "platinum": int(totals.platinum),
                "gold": int(totals.gold),
                "silver": int(totals.silver),
                "bronze": int(totals.bronze),
            }
            self.active_trophy_progress = None
            active_id = self.psn_media_title_id
            self._trophy_title_id = active_id
            if active_id:
                try:
                    raw_id = normalize_title_id(active_id)
                    trophies = await asyncio.to_thread(
                        lambda: list(self._psn.client.trophy_titles_for_title([raw_id + "_00"]))
                    )
                    progress = next((row.progress for row in trophies
                                     if row.progress is not None), None)
                    self.active_trophy_progress = int(progress) if progress is not None else None
                except Exception:
                    _LOG.debug("[%s] Current-game trophies unavailable", self.log_id)
            self.push_update()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.debug("[%s] Trophy summary unavailable", self.log_id, exc_info=True)
        finally:
            self._trophy_expiry = asyncio.get_running_loop().time() + 1800

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
        if self._trophy_task and not self._trophy_task.done():
            self._trophy_task.cancel()
            try:
                await self._trophy_task
            except asyncio.CancelledError:
                pass
        self._trophy_task = None

        if self._metadata_task and not self._metadata_task.done():
            self._metadata_task.cancel()
            try:
                await self._metadata_task
            except asyncio.CancelledError:
                pass
        self._metadata_task = None
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
        if self._psn and self.psn_authenticated is True and now >= self._trophy_expiry:
            if self._trophy_task is None or self._trophy_task.done():
                self._trophy_expiry = now + 1800
                self._trophy_task = asyncio.create_task(self._refresh_trophies())
        if self.psn_media_title_id != self._trophy_title_id:
            self._trophy_expiry = 0.0
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
