"""Offline regression coverage for the simplified PS5 Connect setup flow."""
from __future__ import annotations

import asyncio
import base64
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "intg-ps5"


class FakeConfigManager:
    def __init__(self, *configs):
        self.records = {config.identifier: config for config in configs}

    def get(self, identifier):
        return self.records.get(identifier)

    def all(self):
        return list(self.records.values())


class FakeBaseSetup:
    def __init__(self, config, *, driver=None, **kwargs):
        self.config = config
        self.driver = driver or types.SimpleNamespace(loop=None)
        self._selected_config_id = None
        self._pending_device_config = None

    @property
    def selected_config_entry(self):
        return self.config.get(self._selected_config_id) if self._selected_config_id else None

    async def handle_driver_setup(self, msg):
        return msg


class FakeRequestInput:
    def __init__(self, title, settings):
        self.title = title
        self.settings = settings


class FakeError:
    pass


class FakeStart:
    pass


class FakeAbort:
    pass


def account_id_base64(account_id):
    if account_id.isdigit():
        return base64.b64encode(int(account_id).to_bytes(8, "little")).decode()
    if len(base64.b64decode(account_id, validate=True)) != 8:
        raise ValueError("Incorrect account identifier")
    return account_id


def setup_module():
    config_spec = importlib.util.spec_from_file_location("ps5_setup_test_const", ROOT / "const.py")
    config_module = importlib.util.module_from_spec(config_spec)
    with patch.dict(sys.modules, {"ps5_setup_test_const": config_module}):
        config_spec.loader.exec_module(config_module)
    substitutes = {
        "const": config_module,
        "api": types.SimpleNamespace(PlayStationNetwork=lambda *args: (_ for _ in ()).throw(
            AssertionError("Unexpected network authentication")
        )),
        "controller": types.SimpleNamespace(
            account_id_to_base64=account_id_base64,
            register_console=lambda *args: (_ for _ in ()).throw(
                AssertionError("Unexpected PS5 pairing")
            ),
        ),
        "psnawp_api": types.ModuleType("psnawp_api"),
        "psnawp_api.utils": types.ModuleType("psnawp_api.utils"),
        "psnawp_api.utils.misc": types.SimpleNamespace(
            parse_npsso_token=lambda token: token or None
        ),
        "ucapi": types.SimpleNamespace(
            IntegrationSetupError=types.SimpleNamespace(OTHER="other"),
            RequestUserInput=FakeRequestInput, SetupError=FakeError,
            DriverSetupRequest=FakeStart, AbortDriverSetup=FakeAbort,
        ),
        "ucapi_framework": types.SimpleNamespace(BaseSetupFlow=FakeBaseSetup),
    }
    with patch.dict(sys.modules, substitutes):
        spec = importlib.util.spec_from_file_location("ps5_test_setup_flow", ROOT / "setup_flow.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module, config_module.PSNConfig


SETUP, PSNConfig = setup_module()


class SetupFlowTests(unittest.IsolatedAsyncioTestCase):
    def paired_config(self):
        return PSNConfig(
            identifier="ps5_10_1_1_30",
            name="My PS5",
            npsso="previous-saved-token",
            psn_account_id="1",
            ps_device={
                "device_ip": "10.1.1.30",
                "user": "ps5-remote",
                "profiles": {"ps5-remote": {"id": account_id_base64("1")}},
            },
            console_host_id="existing-host-id",
            library_view="psn_games",
        )

    def existing_flow(self):
        config = self.paired_config()
        flow = SETUP.PSNSetupFlow(FakeConfigManager(config))
        flow._selected_config_id = config.identifier
        return flow, config

    def test_reconfigure_form_only_has_required_ps5_fields(self):
        flow, cfg = self.existing_flow()
        screen = flow.get_manual_entry_form()
        values = {field["id"]: next(iter(field["field"].values())).get("value")
                  for field in screen.settings}
        self.assertEqual(screen.title["en"], "Edit PlayStation 5")
        self.assertEqual(values["account_id"], cfg.psn_account_id)
        self.assertEqual(values["npsso"], "")
        self.assertFalse(values["re_pair"])
        self.assertEqual(set(values), {
            "npsso", "account_id", "add_control", "re_pair", "launch_device_id"
        })

    async def test_update_preserves_pairing_and_browser_without_second_pin(self):
        flow, old = self.existing_flow()
        new = await flow.query_device({
            "account_id": "1", "add_control": "true", "launch_device_id": ""
        })
        self.assertIsInstance(new, PSNConfig)
        self.assertEqual(new.ps_device, old.ps_device)
        self.assertEqual(new.npsso, old.npsso)
        self.assertEqual(new.library_view, "psn_games")
        self.assertIsNone(flow._pending_device_config)

    async def test_first_time_setup_does_not_reference_unbound_existing(self):
        flow = SETUP.PSNSetupFlow(FakeConfigManager())
        with patch.object(SETUP, "discover_ps5", return_value=[]):
            screen = await flow.query_device({
                "account_id": "1", "add_control": "true"
            })
        self.assertEqual(screen.title["en"], "Select PS5")
        self.assertIsInstance(flow._pending_device_config, PSNConfig)
        self.assertEqual(flow._pending_device_config.psn_account_id, "1")

    async def test_explicit_repair_retains_config_id(self):
        flow, original = self.existing_flow()
        with patch.object(SETUP, "discover_ps5", return_value=[]):
            screen = await flow.query_device({
                "account_id": "1", "add_control": "true", "re_pair": "true"
            })
        self.assertEqual(screen.title["en"], "Select PS5")
        self.assertEqual(flow._pending_device_config.identifier, original.identifier)

    async def test_existing_legacy_account_can_be_reused(self):
        flow, cfg = self.existing_flow()
        cfg.psn_account_id = ""
        new = await flow.query_device({"add_control": "true"})
        self.assertIsInstance(new, PSNConfig)
        self.assertEqual(new.psn_account_id, account_id_base64("1"))
        self.assertEqual(new.npsso, cfg.npsso)

    async def test_no_core_api_key_required(self):
        flow, _ = self.existing_flow()
        new = await flow.query_device({
            "account_id": "1", "add_control": "true",
        })
        self.assertIsInstance(new, PSNConfig)
        self.assertFalse(hasattr(new, "remote_api_key"))

    async def test_abandoned_flow_is_reset(self):
        flow, cfg = self.existing_flow()
        flow._pending_device_config = cfg
        flow._step = "pin"
        await flow.handle_driver_setup(FakeStart())
        self.assertIsNone(flow._pending_device_config)
        self.assertIsNone(flow._selected_config_id)
        self.assertEqual(flow._step, "")


if __name__ == "__main__":
    unittest.main()
