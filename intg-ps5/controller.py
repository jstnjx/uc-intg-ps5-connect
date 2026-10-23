"""Local PS5 Remote Play controller (no HTTP daemon).

Adapted from the MIT-licensed sbr-labs/ps5-control-uc session manager.
Remote Play is required for navigation; Sony exposes no public LAN button API.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
from typing import Any

from pyremoteplay import RPDevice
from pyremoteplay.profile import Profiles, UserProfile

_LOG = logging.getLogger(__name__)
BUTTONS = frozenset({
    "UP", "DOWN", "LEFT", "RIGHT", "CROSS", "CIRCLE", "TRIANGLE", "SQUARE",
    "L1", "R1", "L2", "R2", "L3", "R3", "OPTIONS", "SHARE", "PS", "TOUCHPAD"
})
RAPID_BUTTONS = frozenset({"UP", "DOWN", "LEFT", "RIGHT", "L1", "R1", "L2", "R2"})


def account_id_to_base64(account_id: str) -> str:
    """Convert PSN decimal account ID to pyremoteplay's little-endian Base64."""
    text = str(account_id).strip()
    if text.isdecimal():
        value = int(text)
        if value >= 1 << 64:
            raise ValueError("PSN account ID is out of range")
        return base64.b64encode(value.to_bytes(8, "little")).decode("ascii")
    raw = base64.b64decode(text, validate=True)
    if len(raw) != 8:
        raise ValueError("Expected an eight-byte Base64 account ID")
    return base64.b64encode(raw).decode("ascii")


def register_console(host: str, account_id: str, pin: str, username: str = "ps5-remote") -> dict[str, Any]:
    """Blocking registration; call via asyncio.to_thread during setup."""
    host = str(ipaddress.ip_address(host))
    if len(pin) != 8 or not pin.isdecimal():
        raise ValueError("Enter the fresh eight-digit Remote Play PIN")
    profiles = Profiles()
    profiles.update_user(UserProfile(username, {"id": account_id_to_base64(account_id), "hosts": {}}))
    device = RPDevice(host)
    if not device.get_status():
        raise ConnectionError("PS5 is unreachable; turn it on and enable Remote Play")
    if not device.register(username, pin, profiles=profiles, save=False):
        raise ConnectionError("Registration failed; regenerate PIN and verify PSN account ID")
    return {
        "device_ip": host,
        "device_type": "PS5",
        "user": username,
        "profiles": dict(profiles),
    }


class PS5Controller:
    """Owns one PS5 connection and its short-lived, headless Remote Play session."""

    def __init__(self, credentials: dict[str, Any], *, device_factory=RPDevice, idle_seconds: int = 90) -> None:
        self.host = str(ipaddress.ip_address(credentials["device_ip"]))
        self.username = credentials.get("user") or "ps5-remote"
        self.profiles = Profiles(credentials["profiles"])
        self.device = device_factory(self.host)
        self.idle_seconds = idle_seconds
        self._lock = asyncio.Lock()
        self._last_press = 0.0
        self._idle_task: asyncio.Task | None = None
        self._wake_task: asyncio.Task | None = None
        self._rapid_last: dict[str, float] = {}
        self._rapid_tasks: dict[str, asyncio.Task] = {}
        self._paused_until = 0.0
        self._last_state: dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        try:
            return bool(self.device.connected and self.device.controller and self.device.controller.running)
        except (AttributeError, RuntimeError):
            return False

    async def status(self) -> dict[str, Any]:
        result = await asyncio.to_thread(self.device.get_status)
        self._last_state = result or {}
        return self._last_state

    @property
    def awake(self) -> bool:
        return bool(self.device.is_on)

    async def ensure_session(self) -> bool:
        if asyncio.get_running_loop().time() < self._paused_until:
            return False
        async with self._lock:
            if self.connected:
                self._last_press = asyncio.get_running_loop().time()
                return True
            await self.status()
            if not self.awake:
                return False
            try:
                self.device.disconnect()
            except Exception:
                _LOG.debug("Stale Remote Play disconnect failed", exc_info=True)
            try:
                session = self.device.create_session(
                    user=self.username, profiles=self.profiles,
                    resolution="360p", fps="low", quality="very_low", codec="h264",
                )
                if session is None:
                    return False
                if not await asyncio.wait_for(self.device.connect(), timeout=25):
                    self.device.disconnect()
                    return False
                if not await self.device.async_wait_for_session(timeout=20):
                    self.device.disconnect()
                    return False
                self.device.controller.start()
                await asyncio.sleep(0.3)
                self._last_press = asyncio.get_running_loop().time()
                self._ensure_idle_task()
                return True
            except asyncio.CancelledError:
                self.device.disconnect()
                raise
            except Exception:
                _LOG.exception("Remote Play session could not start")
                self.device.disconnect()
                return False

    def _ensure_idle_task(self) -> None:
        if self.idle_seconds > 0 and (self._idle_task is None or self._idle_task.done()):
            self._idle_task = asyncio.create_task(self._idle_watcher())

    async def _idle_watcher(self) -> None:
        try:
            while self.connected:
                await asyncio.sleep(10)
                if asyncio.get_running_loop().time() - self._last_press >= self.idle_seconds:
                    await self._drop_session()
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("Idle session watcher failed")

    async def _drop_session(self) -> None:
        controller = getattr(self.device, "controller", None)
        if controller:
            try:
                controller.stop()
            except Exception:
                _LOG.debug("Controller stop failed", exc_info=True)
        try:
            self.device.disconnect()
        except Exception:
            _LOG.debug("Session disconnect failed", exc_info=True)

    async def release_session(self, pause_seconds: int = 120) -> bool:
        self._paused_until = asyncio.get_running_loop().time() + max(0, pause_seconds)
        for task in list(self._rapid_tasks.values()):
            task.cancel()
        self._rapid_tasks.clear()
        async with self._lock:
            await self._drop_session()
        return True

    async def reconnect(self) -> bool:
        self._paused_until = 0.0
        return await self.ensure_session()

    async def wake(self) -> bool:
        try:
            await asyncio.to_thread(self.device.wakeup, self.username, self.profiles)
            if self._wake_task is None or self._wake_task.done():
                self._wake_task = asyncio.create_task(self._warm_after_wakeup())
            return True
        except Exception:
            _LOG.exception("Wake request failed")
            return False

    async def _warm_after_wakeup(self) -> None:
        try:
            if await self.device.async_wait_for_wakeup(timeout=60):
                await self.ensure_session()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.debug("PS5 wake warmup failed", exc_info=True)

    async def standby(self) -> bool:
        try:
            if not await self.ensure_session():
                return False
            return bool(await self.device.standby(self.username, self.profiles))
        except Exception:
            _LOG.exception("Standby request failed")
            return False
        finally:
            await self.release_session(pause_seconds=5)

    async def button(self, button: str, action: str = "tap") -> bool:
        button = button.upper().strip()
        action = action.lower().strip()
        if button not in BUTTONS or action not in {"tap", "press", "release"}:
            return False
        if not await self.ensure_session():
            return False
        try:
            now = asyncio.get_running_loop().time()
            self._last_press = now
            await self.device.controller.async_button(button, action, delay=0.04)
            if action == "tap" and button in RAPID_BUTTONS:
                previous = self._rapid_last.get(button, 0)
                self._rapid_last[button] = now
                if now - previous < 0.15:
                    task = self._rapid_tasks.get(button)
                    if task is None or task.done():
                        self._rapid_tasks[button] = asyncio.create_task(self._rapid_repeat(button))
            return True
        except Exception:
            _LOG.exception("PS5 button failed: %s/%s", button, action)
            return False

    async def _rapid_repeat(self, button: str) -> None:
        try:
            while self.connected:
                await asyncio.sleep(0.05)
                if asyncio.get_running_loop().time() - self._rapid_last.get(button, 0) > 0.18:
                    break
                await self.device.controller.async_button(button, "tap", delay=0.04)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.debug("Rapid repeat failed", exc_info=True)
        finally:
            self._rapid_tasks.pop(button, None)

    async def close(self) -> None:
        for task in [self._idle_task, self._wake_task, *list(self._rapid_tasks.values())]:
            if task and not task.done():
                task.cancel()
        await self.release_session(pause_seconds=0)
