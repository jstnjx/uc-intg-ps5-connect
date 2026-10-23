"""Remote-hosted PS5 + optional PlayStation Network setup flow.

MPL-2.0. Based on Jack Powell's uc-intg-psn setup structure.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from typing import Any

from api import PlayStationNetwork
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

    def get_manual_entry_form(self) -> RequestUserInput:
        return self._account_form()

    def _account_form(self, error: str | None = None) -> RequestUserInput:
        fields = [_label(
            "Enter an NPSSO cookie for PSN presence, artwork, and game-library browsing. "
            "Get it after signing in at https://ca.account.sony.com/api/v1/ssocookie . "
            "Alternatively, supply your PSN Account ID (decimal or Base64) for LOCAL control without PSN. "
            "No daemon, Docker, or additional host is needed."
        )]
        if error:
            fields.append(_label(error, "error"))
        fields += [
            {"id": "npsso", "label": {"en": "NPSSO token (optional with account ID)"},
             "field": {"text": {"value": ""}}},
            {"id": "account_id", "label": {"en": "PSN Account ID (optional with NPSSO)"},
             "field": {"text": {"value": ""}}},
            {"id": "add_control", "label": {"en": "Pair PS5 for local navigation and power"},
             "field": {"checkbox": {"value": True}}},
        ]
        return _form("PlayStation 5 account", fields)

    async def query_device(self, input_values: dict[str, Any]):
        token_value = str(input_values.get("npsso") or "").strip()
        account_value = str(input_values.get("account_id") or "").strip()
        control = input_values.get("add_control", True)
        control = control is True or str(control).lower() in ("true", "1", "on")
        if not token_value and not account_value:
            return self._account_form("Provide an NPSSO token or PSN Account ID.")
        self._npsso = ""
        self._psn_name = "PlayStation 5"
        self._account_id = ""
        if token_value:
            token = parse_npsso_token(token_value)
            if not token:
                return self._account_form("The NPSSO token is invalid.")
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
                _LOG.warning("PSN sign-in failed", exc_info=True)
                return self._account_form("Could not authenticate with Sony. Obtain a new NPSSO token.")
        else:
            self._account_id = account_value
        try:
            account_id_to_base64(self._account_id)
        except (ValueError, OverflowError):
            return self._account_form("Account ID must be decimal or an eight-byte Base64 value.")
        if not control:
            return PSNConfig(
                identifier="psn_" + self._account_id.replace("/", "_").replace("+", "_").replace("=", ""),
                name=self._psn_name,
                npsso=self._npsso,
            )
        self._pending_device_config = PSNConfig(
            identifier="ps5_pending",
            name=self._psn_name,
            npsso=self._npsso,
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
            _label("Turn on the PS5, enable Remote Play, and ensure it is on the same LAN as the Remote."),
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
            "On PS5: Settings → System → Remote Play → Link Device. "
            "Generate a fresh eight-digit PIN immediately before confirming (it expires quickly)."
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
        return _form("Pair Remote Play", fields)

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
                "Could not pair. Check Remote Play, same-network access, PSN Account ID, and a NEW PIN.",
                host=host,
            )
        existing = next(
            (cfg for cfg in self.config.all()
             if cfg.ps_device.get("device_ip") == host), None
        )
        return PSNConfig(
            identifier=existing.identifier if existing else "ps5_" + host.replace(".", "_"),
            name=self._psn_name if self._psn_name != "PlayStation 5" else f"PlayStation 5 ({host})",
            npsso=self._npsso,
            ps_device=credentials,
        )
