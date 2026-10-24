"""PlayStation 5 setup and pairing flow.

MPL-2.0. Based on Jack Powell's uc-intg-psn setup structure.
"""
from __future__ import annotations

import asyncio
import binascii
from dataclasses import replace
import ipaddress
import logging
import socket
import time
from typing import Any
from zoneinfo import ZoneInfo

from api import PlayStationNetwork
from game_refresh import valid_refresh_time
from const import PSNConfig
from controller import account_id_to_base64, register_console
from psnawp_api.utils.misc import parse_npsso_token
from ucapi import IntegrationSetupError, RequestUserInput, SetupError
from ucapi_framework import BaseSetupFlow

_LOG = logging.getLogger(__name__)
_MANUAL = "__manual__"


def _label(message: str, identifier: str = "information") -> dict:
    return {"id": identifier, "label": {"en": "Information"}, "field": {"label": {"value": {"en": message}}}}


def _form(title: str, fields: list[dict]) -> RequestUserInput:
    return RequestUserInput({"en": title}, fields)


def discover_ps5(timeout: float = 2.0) -> list[tuple[str, str]]:
    """Broadcast Sony DDP discovery; no separate discovery service is needed."""
    found: dict[str, str] = {}
    message = b"SRCH * HTTP/1.1\r\ndevice-discovery-protocol-version:00030010\r\n\r\n"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", 0))
        sock.settimeout(0.2)
        for destination in ("255.255.255.255",):
            try:
                sock.sendto(message, (destination, 9302))
            except OSError:
                _LOG.debug("DDP broadcast blocked", exc_info=True)
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            try:
                payload, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            raw = payload.decode("utf-8", "replace")
            if "PS5" not in raw.upper() and "PLAYSTATION" not in raw.upper():
                continue
            name = next(
                (line.split(":", 1)[1].strip() for line in raw.splitlines()
                 if line.lower().startswith("host-name:")), "PlayStation 5"
            )
            found[addr[0]] = name
    return sorted(found.items())


class PSNSetupFlow(BaseSetupFlow[PSNConfig]):
    """Set up cloud-only presence or paired on-device local PS5 control."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._npsso = ""
        self._account_id = ""
        self._psn_name = "PlayStation 5"
        self._host = ""
        self._step = ""
        self._launch_device_id = ""
        self._game_refresh_enabled = True
        self._game_refresh_time = "03:00"
        self._game_refresh_timezone = "Europe/Berlin"
        self._remote_url = ""
        self._remote_api_key = ""
        self._account_value = ""
        self._form_refresh_enabled: bool | None = None

    def get_manual_entry_form(self) -> RequestUserInput:
        return self._account_form()

    def _account_form(self, error: str | None = None) -> RequestUserInput:
        """Collect only editable settings; never ask for stored secrets again."""
        existing = self.selected_config_entry
        paired = bool(existing and existing.ps_device.get("profiles")
                      and existing.ps_device.get("device_ip"))
        default_refresh = bool(existing and existing.game_refresh_enabled
                               and existing.remote_api_key)
        fields = []
        if error:
            fields.append(_label(error, "error"))
        fields.extend([
            {
                "id": "npsso",
                "label": {"en": "PSN token (NPSSO, leave blank to keep saved token)" if existing
                          else "PSN token (NPSSO)"},
                "field": {"password": {"value": ""}},
            },
            {
                "id": "account_id",
                "label": {"en": "PSN Account ID (Base64 or decimal)"},
                "field": {"text": {"value": self._account_value or (
                    existing.psn_account_id if existing else ""
                )}},
            },
            {
                "id": "add_control",
                "label": {"en": "Enable PS5 controller and power"},
                "field": {"checkbox": {"value": paired if existing else True}},
            },
        ])
        if paired:
            fields.append({
                "id": "re_pair",
                "label": {"en": "Re-pair PS5 (only if pairing changed)"},
                "field": {"checkbox": {"value": False}},
            })
        fields.extend([
            {
                "id": "launch_device_id",
                "label": {"en": "PSN console ID (optional)"},
                "field": {"text": {"value": self._launch_device_id or (
                    existing.launch_device_id if existing else ""
                )}},
            },
            {
                "id": "game_refresh_enabled",
                "label": {"en": "Enable daily game library updates"},
                "field": {"checkbox": {"value": self._form_refresh_enabled
                                      if self._form_refresh_enabled is not None
                                      else default_refresh}},
            },
            {
                "id": "game_refresh_time",
                "label": {"en": "Daily update time (HH:MM)"},
                "field": {"text": {"value": self._game_refresh_time or (
                    existing.game_refresh_time if existing else "03:00"
                )}},
            },
            {
                "id": "game_refresh_timezone",
                "label": {"en": "Timezone"},
                "field": {"text": {"value": self._game_refresh_timezone or (
                    existing.game_refresh_timezone if existing else "Europe/Berlin"
                )}},
            },
            {
                "id": "remote_url",
                "label": {"en": "Remote address (daily updates only)"},
                "field": {"text": {"value": self._remote_url or (
                    existing.remote_url if existing else ""
                )}},
            },
            {
                "id": "remote_pin",
                "label": {"en": "Web Configurator PIN (daily updates only)"},
                "field": {"password": {"value": ""}},
            },
        ])
        return _form("Edit PlayStation 5" if existing else "Connect PlayStation 5", fields)

    async def query_device(self, input_values: dict[str, Any]):
        """Configure PSN once; reuse already-paired PS5 on ordinary updates."""
        existing = self.selected_config_entry
        token_value = str(input_values.get("npsso") or "").strip()
        self._account_value = str(input_values.get("account_id") or "").strip()
        self._launch_device_id = str(input_values.get("launch_device_id") or (
            existing.launch_device_id if existing else ""
        )).strip()
        control = str(input_values.get("add_control", True)).lower() in ("true", "1", "on")
        re_pair = str(input_values.get("re_pair", False)).lower() in ("true", "1", "on")
        self._form_refresh_enabled = (
            str(input_values.get("game_refresh_enabled", False)).lower()
            in ("true", "1", "on")
        )
        self._game_refresh_enabled = self._form_refresh_enabled
        self._game_refresh_time = str(input_values.get("game_refresh_time") or (
            existing.game_refresh_time if existing else "03:00"
        )).strip()
        self._game_refresh_timezone = str(input_values.get("game_refresh_timezone") or (
            existing.game_refresh_timezone if existing else "Europe/Berlin"
        )).strip()
        self._remote_url = str(input_values.get("remote_url") or (
            existing.remote_url if existing else ""
        )).strip().rstrip("/")
        self._remote_api_key = existing.remote_api_key if existing else ""

        if self._launch_device_id and not (8 <= len(self._launch_device_id) <= 256):
            return self._account_form("Check the PSN console ID or leave it blank.")
        if not valid_refresh_time(self._game_refresh_time):
            return self._account_form("Enter a valid update time (HH:MM).")
        try:
            ZoneInfo(self._game_refresh_timezone)
        except (KeyError, ValueError):
            return self._account_form("Enter a valid timezone (e.g. Europe/Berlin).")

        # A blank token on reconfigure means retain the working PSN account.
        self._npsso = existing.npsso if existing else ""
        self._psn_name = existing.name if existing else "PlayStation 5"
        self._account_id = self._account_value or (
            existing.psn_account_id if existing else ""
        )
        if not self._account_id and existing:
            profiles = existing.ps_device.get("profiles", {})
            profile = profiles.get(existing.ps_device.get("user", ""), {})
            if isinstance(profile, dict):
                self._account_id = str(profile.get("id") or "")

        if token_value:
            token = parse_npsso_token(token_value)
            if not token:
                return self._account_form("Check the PSN token.")
            try:
                def resolve_account():
                    psn = PlayStationNetwork(token, self.driver.loop)
                    try:
                        user = psn.get_user()
                        return str(user.account_id), str(user.online_id)
                    finally:
                        psn.close()
                self._account_id, self._psn_name = await asyncio.to_thread(resolve_account)
                self._npsso = token
            except Exception:
                _LOG.warning("PSN authentication failed during setup", exc_info=True)
                return self._account_form("PSN sign-in failed. Check your token.")

        if not self._account_id:
            return self._account_form("Enter a PSN token or Account ID.")
        try:
            account_id_to_base64(self._account_id)
        except (ValueError, OverflowError, binascii.Error):
            return self._account_form("Enter a valid PSN Account ID.")

        # Changing accounts invalidates an existing Remote Play registration.
        if existing and control and not re_pair and existing.ps_device.get("profiles"):
            old_id = existing.psn_account_id or ""
            if old_id and account_id_to_base64(old_id) != account_id_to_base64(self._account_id):
                return self._account_form("Select Re-pair PS5 to change PSN accounts.")

        if self._game_refresh_enabled:
            from urllib.parse import urlsplit
            address = urlsplit(self._remote_url)
            if address.scheme not in ("http", "https") or not address.hostname:
                return self._account_form("Enter the Remote address for daily updates.")
            pin = str(input_values.get("remote_pin") or "").strip()
            if pin:
                try:
                    import secrets
                    from unfurled import Remote
                    async with Remote(self._remote_url, pin=pin, wake_if_asleep=False) as remote:
                        new_key = await remote.auth.create_key(
                            "UC PS5 Game Library " + secrets.token_hex(3)
                        )
                    if not new_key:
                        raise ValueError("Remote returned no API key")
                    self._remote_api_key = new_key
                except Exception:
                    _LOG.warning("Could not authenticate Core API for library updates",
                                 exc_info=True)
                    return self._account_form("Check the Remote address and PIN, or disable daily updates.")
            elif not (existing and existing.remote_url == self._remote_url
                      and existing.remote_api_key):
                return self._account_form("Enter the Remote PIN or disable daily updates.")
        else:
            self._remote_url = ""
            self._remote_api_key = ""

        if existing and not re_pair and control and existing.ps_device.get("profiles"):
            # Updating schedules, token, and metadata must not trigger DDP discovery
            # and a second eight-digit pairing code for the same console.
            return replace(
                existing,
                npsso=self._npsso,
                psn_account_id=self._account_id,
                launch_device_id=self._launch_device_id,
                game_refresh_enabled=self._game_refresh_enabled,
                game_refresh_time=self._game_refresh_time,
                game_refresh_timezone=self._game_refresh_timezone,
                remote_url=self._remote_url,
                remote_api_key=self._remote_api_key,
            )

        if not control:
            if not self._npsso:
                return self._account_form("A PSN token is required without controller pairing.")
            return replace(
                existing, npsso=self._npsso, psn_account_id=self._account_id,
                launch_device_id=self._launch_device_id,
                game_refresh_enabled=self._game_refresh_enabled,
                game_refresh_time=self._game_refresh_time,
                game_refresh_timezone=self._game_refresh_timezone,
                remote_url=self._remote_url, remote_api_key=self._remote_api_key,
            ) if existing else PSNConfig(
                identifier="psn_" + self._account_id.replace("/", "_").replace("+", "_").replace("=", ""),
                name=self._psn_name,
                npsso=self._npsso,
                psn_account_id=self._account_id,
                launch_device_id=self._launch_device_id,
                game_refresh_enabled=self._game_refresh_enabled,
                game_refresh_time=self._game_refresh_time,
                game_refresh_timezone=self._game_refresh_timezone,
                remote_url=self._remote_url,
                remote_api_key=self._remote_api_key,
            )

        # Only a new PS5 (or explicit re-pair) needs discovery and the PIN.
        self._pending_device_config = replace(
            existing, npsso=self._npsso, psn_account_id=self._account_id,
            launch_device_id=self._launch_device_id,
            game_refresh_enabled=self._game_refresh_enabled,
            game_refresh_time=self._game_refresh_time,
            game_refresh_timezone=self._game_refresh_timezone,
            remote_url=self._remote_url, remote_api_key=self._remote_api_key,
        ) if existing else PSNConfig(
            identifier="ps5_pending",
            name=self._psn_name,
            npsso=self._npsso,
            psn_account_id=self._account_id,
            launch_device_id=self._launch_device_id,
            game_refresh_enabled=self._game_refresh_enabled,
            game_refresh_time=self._game_refresh_time,
            game_refresh_timezone=self._game_refresh_timezone,
            remote_url=self._remote_url,
            remote_api_key=self._remote_api_key,
        )
        self._step = "select"
        return await self._selection_form()

    async def _selection_form(self) -> RequestUserInput:
        try:
            devices = await asyncio.wait_for(asyncio.to_thread(discover_ps5, 2.0), timeout=4)
        except Exception:
            _LOG.info("Discovery unavailable; manual IP remains supported", exc_info=True)
            devices = []
        items = [
            {"id": ip, "label": {"en": f"{name} ({ip})"}}
            for ip, name in devices
        ]
        items.append({"id": _MANUAL, "label": {"en": "Enter IP address manually"}})
        return _form("Select PS5", [
            _label("Select the PS5 to pair. Turn it on if it is not listed."),
            {"id": "console", "label": {"en": "PS5 console"},
             "field": {"dropdown": {"items": items, "value": items[0]["id"]}}},
        ])

    async def handle_additional_configuration_response(self, msg: Any):
        values = msg.input_values
        if self._step == "select":
            choice = str(values.get("console") or _MANUAL)
            self._host = "" if choice == _MANUAL else choice
            self._step = "pin"
            return self._pin_form()
        if self._step == "pin":
            return await self._pair(values)
        return SetupError(error_type=IntegrationSetupError.OTHER)

    def _pin_form(self, error: str | None = None, host: str = "") -> RequestUserInput:
        fields = [_label(
            "On PS5: Settings → System → Remote Play → Link Device. Enter the displayed code."
        )]
        if error:
            fields.append(_label(error, "error"))
        if not self._host:
            fields.append({
                "id": "manual_ip", "label": {"en": "PS5 IPv4 address"},
                "field": {"text": {"value": host}},
            })
        fields.append({
            "id": "pin", "label": {"en": "8-digit Remote Play PIN"},
            "field": {"text": {"value": ""}},
        })
        return _form("Pair PlayStation 5", fields)

    async def _pair(self, values: dict):
        host = self._host or str(values.get("manual_ip") or "").strip()
        pin = str(values.get("pin") or "").strip().replace(" ", "")
        try:
            host = str(ipaddress.IPv4Address(host))
        except ipaddress.AddressValueError:
            return self._pin_form("Enter a valid IPv4 address.", host=host)
        if len(pin) != 8 or not pin.isdecimal():
            return self._pin_form("PIN must contain exactly eight digits.", host=host)
        try:
            credentials = await asyncio.wait_for(
                asyncio.to_thread(register_console, host, self._account_id, pin),
                timeout=45,
            )
        except Exception:
            _LOG.warning("PS5 pairing failed", exc_info=True)
            return self._pin_form(
                "Pairing failed. Check the PS5 and enter a new PIN.",
                host=host,
            )
        existing = next(
            (cfg for cfg in self.config.all()
             if cfg.ps_device.get("device_ip") == host
             or (
                 credentials.get("host_id")
                 and credentials["host_id"].casefold() ==
                     str(cfg.console_host_id or cfg.ps_device.get("host_id") or "").casefold()
             )), None
        )
        return PSNConfig(
            identifier=existing.identifier if existing else "ps5_" + host.replace(".", "_"),
            name=self._psn_name if self._psn_name != "PlayStation 5"
                else str(credentials.get("host_name") or f"PlayStation 5 ({host})"),
            npsso=self._npsso or (existing.npsso if existing else ""),
            ps_device=credentials,
            launch_device_id=self._launch_device_id or (existing.launch_device_id if existing else ""),
            console_host_id=str(credentials.get("host_id") or (existing.console_host_id if existing else "")),
            console_name=str(credentials.get("host_name") or (existing.console_name if existing else "")),
            psn_account_id=self._account_id or (existing.psn_account_id if existing else ""),
            library_view=existing.library_view if existing else "installed_games",
            game_refresh_enabled=self._game_refresh_enabled,
            game_refresh_time=self._game_refresh_time,
            game_refresh_timezone=self._game_refresh_timezone,
            game_refresh_last_date=existing.game_refresh_last_date if existing else "",
            game_refresh_page_ids=list(existing.game_refresh_page_ids) if existing else [],
            remote_url=self._remote_url if self._game_refresh_enabled else "",
            remote_api_key=self._remote_api_key if self._game_refresh_enabled else "",
        )
