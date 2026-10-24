"""Offline game-launch tests. No PSN credentials or PS5 required."""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import types
import unittest


def load_launch_module():
    path = pathlib.Path(__file__).resolve().parents[1] / "intg-ps5" / "game_launch.py"
    spec = importlib.util.spec_from_file_location("ps5_game_launch_tests", path)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve __module__ at class creation time.
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


launch = load_launch_module()


class FakeResponse:
    def __init__(self, code=202):
        self.status_code = code


class FakeAuthenticator:
    def __init__(self, code=202):
        self.code = code
        self.calls = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.code)


class LaunchValidationTests(unittest.TestCase):
    def test_valid_title_id(self):
        self.assertEqual(launch.normalize_title_id(" ppsa01411 "), "PPSA01411")
        self.assertEqual(launch.normalize_title_id(" CUSA00001 "), "CUSA00001")

    def test_bad_title_rejected(self):
        for value in ("", "../../test", "http://malicious", "ABC", "PPSA01411?q=x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                launch.normalize_title_id(value)

    def test_only_sony_https_endpoint(self):
        endpoint = "https://m.np.playstation.com/api/test-launch"
        self.assertEqual(launch.validate_sony_endpoint(endpoint), endpoint)
        for value in (
            "http://m.np.playstation.com/api/test-launch",
            "https://playstation.com.evil.test/api/launch",
            "https://127.0.0.1/api/launch",
            "https://user:password@m.np.playstation.com/api/launch",
            "https://m.np.playstation.com:8443/api/launch",
            "https://m.np.playstation.com/api/launch#fragment",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                launch.validate_sony_endpoint(value)

    def test_no_default_transport(self):
        backend = launch.SonyDirectLauncher()
        self.assertFalse(backend.enabled)
        with self.assertRaises(launch.LaunchUnavailable):
            backend.prepare("PPSA01411")


class LaunchDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.backend = launch.SonyDirectLauncher(
            "https://m.np.playstation.com/api/test-launch",
            "ps5_console_12345",
        )

    async def test_dispatch_accepted_only_with_real_backend(self):
        auth = FakeAuthenticator(202)
        result = await self.backend.launch("PPSA01411", auth)
        self.assertTrue(result)
        self.assertEqual(len(auth.calls), 1)
        request = auth.calls[0]
        self.assertEqual(request["json"]["titleId"], "PPSA01411")
        self.assertEqual(request["json"]["deviceId"], "ps5_console_12345")
        self.assertEqual(request["url"], self.backend.endpoint)
        self.assertEqual(request["allow_redirects"], False)

    async def test_auth_absent_rejected_before_network(self):
        with self.assertRaises(launch.LaunchUnavailable):
            await self.backend.launch("PPSA01411", None)

    async def test_http_failure_is_not_claimed_as_success(self):
        auth = FakeAuthenticator(401)
        with self.assertRaises(launch.LaunchRejected):
            await self.backend.launch("PPSA01411", auth)

    async def test_bad_title_never_submitted(self):
        auth = FakeAuthenticator()
        with self.assertRaises(ValueError):
            await self.backend.launch("../../test", auth)
        self.assertEqual(auth.calls, [])


if __name__ == "__main__":
    unittest.main()
