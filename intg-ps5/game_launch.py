"""Experimental direct PSN game launch adapter.

PXPlay v3.0+ can launch PS5 games by title ID, but its request transport
is proprietary and no verified public launch endpoint is published.
This adapter intentionally has NO default endpoint: developers can plug
in a captured, validated Sony API route without a third-party daemon.
Never claim game launching succeeds when no route has been configured.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_LOG = logging.getLogger(__name__)
_TITLE_ID = re.compile(r"^[A-Z][A-Z0-9_-]{4,31}$")
_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{5,128}$")


class LaunchUnavailable(RuntimeError):
    """No validated PSN direct-launch transport has been configured."""


class LaunchRejected(RuntimeError):
    """Sony explicitly rejected a launch request."""


def normalize_title_id(title_id: object) -> str:
    """Validate PSN titles, never interpolate unchecked media IDs into URLs."""
    value = str(title_id or "").strip().upper()
    if not _TITLE_ID.fullmatch(value):
        raise ValueError("Invalid PSN title ID")
    return value


def validate_sony_endpoint(value: str) -> str:
    """Reject plain HTTP, redirects to local addresses, credentials and SSRF URLs."""
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
        or not host
        or not (host.endswith(".playstation.com") or host.endswith(".playstation.net"))
        or parsed.query
        or not parsed.path.startswith("/")
        or "//" in parsed.path
    ):
        raise ValueError("Launch endpoint must be an HTTPS PlayStation API URL")
    return value.strip()


@dataclass(frozen=True)
class LaunchRequest:
    endpoint: str
    device_id: str
    title_id: str

    def __post_init__(self):
        validate_sony_endpoint(self.endpoint)
        normalize_title_id(self.title_id)
        if not _DEVICE_ID.fullmatch(self.device_id):
            raise ValueError("Invalid Sony console device identifier")


class SonyDirectLauncher:
    """Backend-bound launch requests using PSNAWP's refresh-aware authenticator.

    The JSON fields are provisional, so this remains EXPERIMENTAL until the
    specific Sony endpoint and its payload have been verified on hardware.
    """

    def __init__(self, endpoint: str = "", device_id: str = "") -> None:
        self.endpoint = validate_sony_endpoint(endpoint) if endpoint else ""
        self.device_id = device_id.strip()
        if self.device_id and not _DEVICE_ID.fullmatch(self.device_id):
            raise ValueError("Invalid PSN device identifier")
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint and self.device_id)

    def prepare(self, title_id: str) -> LaunchRequest:
        if not self.enabled:
            raise LaunchUnavailable(
                "PSN launch is unavailable: Sony's game-launch API request "
                "has not been verified or configured."
            )
        return LaunchRequest(
            endpoint=self.endpoint,
            device_id=self.device_id,
            title_id=normalize_title_id(title_id),
        )

    async def launch(self, title_id: str, authenticator) -> bool:
        request = self.prepare(title_id)
        if authenticator is None:
            raise LaunchUnavailable("PSN authentication is unavailable")
        # Serialize game launch attempts; requests can wake consoles and
        # switch the foreground game, so never fire them concurrently.
        async with self._lock:
            payload = {
                "deviceId": request.device_id,
                "titleId": request.title_id,
            }
            def call():
                return authenticator.post(
                    url=request.endpoint,
                    json=payload,
                    headers={"Accept": "application/json"},
                    timeout=15,
                    allow_redirects=False,
                )
            try:
                response = await asyncio.to_thread(call)
            except Exception as exc:
                _LOG.warning("PSN launch request failed (%s)", type(exc).__name__)
                raise LaunchRejected("PSN launch request failed") from exc
            # 200/202 signal request acceptance, not proof the console has
            # started the game. Subsequent PSN presence should confirm it.
            if response.status_code in (200, 202, 204):
                _LOG.info("Sony accepted PSN game-launch request for %s", request.title_id)
                return True
            _LOG.warning("PSN launch rejected with HTTP %s", response.status_code)
            raise LaunchRejected(f"Sony rejected the launch request (HTTP {response.status_code})")
