"""Offline regression tests for registration, session, commands and release.

Pyremoteplay is stubbed so the fast CI job needs no PS5 or native dependencies.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import sys
import types
import unittest
from pathlib import Path


class FakeProfile:
    def __init__(self, username, details):
        self.username = username
        self.details = details


class FakeProfiles(dict):
    def update_user(self, profile):
        self[profile.username] = profile.details


class FakeButtonController:
    def __init__(self):
        self.running = False
        self.presses = []

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    async def async_button(self, button, action, delay=0.0):
        self.presses.append((button, action))


class FakePS5:
    instances = []

    def __init__(self, host):
        self.host = host
        self.is_on = True
        self.connected = False
        self.controller = FakeButtonController()
        self.registered = None
        self.create_count = 0
        self.status_calls = 0
        self.__class__.instances.append(self)

    def get_status(self):
        self.status_calls += 1
        return {"host-type": "PS5"}

    def register(self, username, pin, profiles=None, save=False):
        self.registered = (username, pin, dict(profiles), save)
        return True

    def create_session(self, **kwargs):
        self.create_count += 1
        return object()

    async def connect(self):
        self.connected = True
        return True

    async def async_wait_for_session(self, timeout=20):
        return True

    def disconnect(self):
        self.connected = False
        self.controller.stop()

    def wakeup(self, username, profiles):
        self.is_on = True

    async def async_wait_for_wakeup(self, timeout=60):
        return True

    async def standby(self, username, profiles):
        self.is_on = False
        return True


def import_controller():
    pyremoteplay = types.ModuleType("pyremoteplay")
    pyremoteplay.RPDevice = FakePS5
    profile = types.ModuleType("pyremoteplay.profile")
    profile.Profiles = FakeProfiles
    profile.UserProfile = FakeProfile
    sys.modules["pyremoteplay"] = pyremoteplay
    sys.modules["pyremoteplay.profile"] = profile
    path = Path(__file__).resolve().parents[1] / "intg-ps5" / "controller.py"
    spec = importlib.util.spec_from_file_location("ps5_control_for_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


control = import_controller()


class RegistrationTests(unittest.TestCase):
    def test_decimal_account_id_is_little_endian_base64(self):
        value = 1234567890
        expected = base64.b64encode(value.to_bytes(8, "little")).decode()
        self.assertEqual(control.account_id_to_base64(str(value)), expected)
        self.assertEqual(control.account_id_to_base64(expected), expected)

    def test_invalid_id_rejected(self):
        for candidate in ("bad-base64", "999999999999999999999999999", ""):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    control.account_id_to_base64(candidate)

    def test_pairing_stores_no_pin_or_cookie(self):
        c = control.register_console("192.168.1.77", "123", "12345678")
        self.assertEqual(c["device_ip"], "192.168.1.77")
        self.assertIn("ps5-remote", c["profiles"])
        self.assertNotIn("pin", str(c).lower())
        self.assertEqual(FakePS5.instances[-1].registered[1], "12345678")

    def test_bad_pin_rejected_before_connecting(self):
        with self.assertRaises(ValueError):
            control.register_console("192.168.1.77", "123", "1234")


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        creds = control.register_console("192.168.1.77", "123", "12345678")
        self.controller = control.PS5Controller(creds, idle_seconds=0)

    async def asyncTearDown(self):
        await self.controller.close()

    async def test_session_and_all_buttons(self):
        for button in sorted(control.BUTTONS):
            self.assertTrue(await self.controller.button(button))
        self.assertEqual(len(self.controller.device.controller.presses), len(control.BUTTONS))
        self.assertEqual(self.controller.device.create_count, 1)

    async def test_reject_unknown_commands(self):
        self.assertFalse(await self.controller.button("FORMAT_HDD"))
        self.assertFalse(await self.controller.button("CROSS", "invalid"))
        self.assertEqual(self.controller.device.create_count, 0)

    async def test_disconnect_pause_and_reconnect(self):
        self.assertTrue(await self.controller.button("CROSS"))
        self.assertTrue(await self.controller.release_session(pause_seconds=120))
        self.assertFalse(self.controller.connected)
        self.assertFalse(await self.controller.button("CROSS"))
        self.assertTrue(await self.controller.reconnect())
        self.assertTrue(self.controller.connected)

    async def test_wakeup_and_standby(self):
        self.assertTrue(await self.controller.wake())
        self.assertTrue(await self.controller.standby())
        self.assertFalse(self.controller.connected)

    async def test_device_off_rejects_button(self):
        self.controller.device.is_on = False
        self.assertFalse(await self.controller.button("CROSS"))


if __name__ == "__main__":
    unittest.main()
