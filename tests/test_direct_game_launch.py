"""Offline regression tests for Sony's PS5 cloud-assisted game-launch protocol.

No real PSN credentials or console calls. Executed by the CI check job.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


def load_module():
    source = Path(__file__).resolve().parents[1] / "intg-ps5" / "game_launch.py"
    spec = importlib.util.spec_from_file_location("ps5_direct_game_launch", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


launch = load_module()


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self.payload = payload

    def json(self):
        return self.payload


class FakeAuthenticator:
    def __init__(self, consoles=None, *, command_id="command-123", status="completed"):
        self.consoles = consoles or [{
            "platform": "PS5",
            "duid": "duid-console-123",
            "device": {"name": "Living Room PS5"},
        }]
        self.command_id = command_id
        self.status = status
        self.requests = []

    def get(self, **kwargs):
        self.requests.append(("GET", kwargs))
        if kwargs["url"].endswith("/clients"):
            return FakeResponse(200, {"clients": self.consoles})
        return FakeResponse(200, {
            "commands": [{"commandId": "command-123", "commandStatus": self.status}],
        })

    def post(self, **kwargs):
        self.requests.append(("POST", kwargs))
        return FakeResponse(202, {"commandId": self.command_id})


class ProtocolTests(unittest.TestCase):
    def test_title_id_normalization(self):
        self.assertEqual(launch.normalize_title_id("ppsa28183_00"), "PPSA28183")
        self.assertEqual(launch.normalize_title_id("CUSA00605"), "CUSA00605")

    def test_rejects_invalid_title_ids(self):
        for value in ("", "../../passwd", "PPSA28183_01", "PPSA12", "https://example.com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                launch.normalize_title_id(value)

    def test_exact_sony_command_payload(self):
        self.assertEqual(
            launch.build_launch_payload("duid-console-123", "PPSA28183_00"),
            {"commandDetail": {
                "platform": "PS5",
                "duid": "duid-console-123",
                "commandType": "launchTitle",
                "parameters": {"titleIds": ["PPSA28183"]},
            }},
        )

    def test_auto_single_device(self):
        self.assertEqual(
            launch.choose_console([{"duid": "duid-console-123"}]),
            "duid-console-123",
        )

    def test_ambiguous_device_fails_closed(self):
        consoles = [{"duid": "duid-console-123"}, {"duid": "duid-console-999"}]
        with self.assertRaises(launch.ConsoleSelectionError):
            launch.choose_console(consoles)
        self.assertEqual(
            launch.choose_console(consoles, requested_duid="duid-console-999"),
            "duid-console-999",
        )

    def test_match_unique_ps5_by_name(self):
        consoles = [
            {"duid": "duid-console-123", "device": {"name": "Living Room PS5"}},
            {"duid": "duid-console-999", "device": {"name": "Office PS5"}},
        ]
        self.assertEqual(launch.choose_console(consoles, local_name="office ps5"), "duid-console-999")

    def test_submit_and_poll_completed(self):
        auth = FakeAuthenticator()
        with patch.object(launch.time, "sleep", return_value=None):
            self.assertTrue(launch._launch_sync(auth, "PPSA28183_00", "", ""))
        methods = [method for method, _ in auth.requests]
        self.assertEqual(methods, ["GET", "POST", "GET"])
        sent = auth.requests[1][1]
        self.assertEqual(sent["json"]["commandDetail"]["parameters"]["titleIds"], ["PPSA28183"])
        self.assertEqual(sent["url"], "https://web.np.playstation.com/api/cloudAssistedNavigation/v2/users/me/commands")
        self.assertFalse(sent["allow_redirects"])
        query = auth.requests[2][1]["params"]
        self.assertEqual(query["commandIds"], "command-123")
        self.assertEqual(query["commandStatus"], "executing,completed")

    def test_http_accepted_without_command_id_does_not_succeed(self):
        auth = FakeAuthenticator(command_id="")
        with self.assertRaisesRegex(launch.GameLaunchError, "command ID"):
            launch._launch_sync(auth, "PPSA28183_00", "", "")

    def test_failed_psn_command_is_error(self):
        auth = FakeAuthenticator(status="failed")
        with self.assertRaises(launch.GameLaunchError):
            launch._launch_sync(auth, "PPSA28183_00", "", "")

    def test_wrong_command_id_rejected(self):
        with self.assertRaises(launch.GameLaunchError):
            launch._command_status({
                "commands": [{"commandId": "wrong-command", "commandStatus": "completed"}]
            }, "command-123")

    def test_http_error_is_not_success(self):
        with self.assertRaisesRegex(launch.GameLaunchError, "HTTP 403"):
            launch._response_json(FakeResponse(403, {}), "game-launch")


if __name__ == "__main__":
    unittest.main()
