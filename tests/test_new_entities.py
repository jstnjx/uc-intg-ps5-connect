"""Offline tests for PS5 Remote and Select command semantics.

These stubs exercise the new entity modules without PSN credentials,
pyremoteplay, a UC Core, or hardware.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from enum import StrEnum
from unittest.mock import patch


class RemoteCommands(StrEnum):
    ON = "on"
    OFF = "off"
    TOGGLE = "toggle"
    SEND_CMD = "send_cmd"
    SEND_CMD_SEQUENCE = "send_cmd_sequence"


class SelectCommands(StrEnum):
    SELECT_OPTION = "select_option"
    SELECT_FIRST = "select_first"
    SELECT_LAST = "select_last"
    SELECT_NEXT = "select_next"
    SELECT_PREVIOUS = "select_previous"


class FakeEntity:
    def __init__(self, *args, **kwargs):
        self.attributes = kwargs.get("attributes", {})
        self.options = kwargs
        self.last_update = None

    def subscribe_to_device(self, device):
        self.device = device

    def update(self, attributes):
        self.last_update = attributes
        self.attributes.update(attributes)


def module(name, **attributes):
    result = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(result, key, value)
    return result


def import_under_test(name, filename):
    base = Path(__file__).resolve().parents[1] / "intg-ps5"
    spec = importlib.util.spec_from_file_location(name, base / filename)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


class FakeUI:
    pass


def prepare():
    remote = module(
        "ucapi.remote", Commands=RemoteCommands,
        Features=module("Features", ON_OFF="on_off", TOGGLE="toggle", SEND_CMD="send_cmd"),
        Attributes=module("Attributes", STATE="state"),
        States=module("States", UNKNOWN="UNKNOWN", UNAVAILABLE="UNAVAILABLE", ON="ON", OFF="OFF"),
        create_send_cmd=lambda name: types.SimpleNamespace(cmd_id="send_cmd", params={"command": name}),
    )
    select = module(
        "ucapi.select", Commands=SelectCommands,
        Attributes=module("Attributes", STATE="state", OPTIONS="options", CURRENT_OPTION="current_option"),
        States=module("States", ON="ON", UNAVAILABLE="UNAVAILABLE"),
    )
    media = module("ucapi.media_player", States=module("States", ON="ON", PLAYING="PLAYING", OFF="OFF"))
    status = module("StatusCodes", OK=200, BAD_REQUEST=400, NOT_IMPLEMENTED=501, SERVICE_UNAVAILABLE=503, SERVER_ERROR=500, CONFLICT=409)
    class Buttons:
        POWER="POWER"
        HOME="HOME"
        BACK="BACK"
        MENU="MENU"
        DPAD_UP="DPAD_UP"
        DPAD_DOWN="DPAD_DOWN"
        DPAD_LEFT="DPAD_LEFT"
        DPAD_RIGHT="DPAD_RIGHT"
        DPAD_MIDDLE="DPAD_MIDDLE"
        RED="RED"
        GREEN="GREEN"
        BLUE="BLUE"
        YELLOW="YELLOW"
        CHANNEL_DOWN="CHANNEL_DOWN"
        CHANNEL_UP="CHANNEL_UP"
    ui = module(
        "ucapi.ui", Buttons=Buttons,
        EntityCommand=lambda *args, **kwargs: types.SimpleNamespace(cmd_id=args[0]),
        Size=lambda *args: args,
        UiPage=lambda *args, **kwargs: (args, kwargs),
        create_btn_mapping=lambda *args, **kwargs: (args, kwargs),
        create_ui_text=lambda *args, **kwargs: (args, kwargs),
    )
    substitutions = {
        "ucapi": module("ucapi", remote=remote, select=select, media_player=media, StatusCodes=status),
        "ucapi.remote": remote,
        "ucapi.select": select,
        "ucapi.media_player": media,
        "ucapi.ui": ui,
        "ucapi.entity": module("ucapi.entity", EntityTypes=module("EntityTypes", REMOTE="remote", SELECT="select")),
        "ucapi_framework": module("ucapi_framework", create_entity_id=lambda kind, name: f"{kind}.{name}"),
        "ucapi_framework.entities": module("ucapi_framework.entities", RemoteEntity=FakeEntity, SelectEntity=FakeEntity),
        "const": module("const", PSNConfig=object),
        "controller": module("controller", BUTTONS=frozenset({"PS", "CROSS", "CIRCLE", "UP", "DOWN", "LEFT", "RIGHT", "OPTIONS", "TRIANGLE", "SQUARE", "SHARE", "TOUCHPAD", "L1", "L2", "R1", "R2"})),
        "psn": module("psn", PSNAccount=object),
    }
    with patch.dict(sys.modules, substitutions):
        return (
            import_under_test("ps5_test_remote", "remote_control.py"),
            import_under_test("ps5_test_selection", "selection.py"),
        )


remote_module, select_module = prepare()


class FakeDevice:
    has_control = True
    has_psn = True
    psn_state = "ON"
    profile_options = ["primary", "secondary"]
    selected_profile = "primary"
    library_view = "installed_games"
    calls = None

    def __init__(self):
        self.calls = []

    async def send_button(self, name, action="tap"):
        self.calls.append(("button", name, action))
        return True

    async def power_on(self):
        self.calls.append("on")
        return True

    async def power_off(self):
        self.calls.append("off")
        return True

    async def power_toggle(self):
        self.calls.append("toggle")
        return True

    async def release_session(self):
        self.calls.append("release")
        return True

    async def reconnect_session(self):
        self.calls.append("reconnect")
        return True

    async def select_profile(self, profile):
        self.calls.append(("profile", profile))
        self.selected_profile = profile
        return True

    async def select_library_view(self, selected):
        self.calls.append(("library", selected))
        self.library_view = selected
        return True


class RemoteInputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.device = FakeDevice()
        self.remote = remote_module.PS5Remote(
            types.SimpleNamespace(identifier="test", name="PlayStation 5"), self.device,
        )

    def test_reject_unknown_and_unbounded_controls(self):
        for args in (
            {"command": "FORMAT_HDD"},
            {"command": "UP", "repeat": 999},
            {"command": "CROSS", "hold": -1},
            {"command": "UP", "repeat": True},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                remote_module.command_parameters(args, sequence=False)

    async def test_simple_button_and_power(self):
        self.assertEqual(await self.remote.command("send_cmd", {"command": "CROSS"}, websocket=None), 200)
        self.assertEqual(await self.remote.command("on", websocket=None), 200)
        self.assertEqual(self.device.calls, [("button", "CROSS", "tap"), "on"])

    async def test_replay_sequence_only_explicitly_requested(self):
        result = await self.remote.command(
            "send_cmd_sequence",
            {"sequence": ["LEFT", "CROSS"], "repeat": 2}, websocket=None,
        )
        self.assertEqual(result, 200)
        self.assertEqual(len(self.device.calls), 4)

    async def test_reject_session_command_in_sequence(self):
        result = await self.remote.command(
            "send_cmd_sequence",
            {"sequence": ["SESSION_DISCONNECT"]}, websocket=None,
        )
        self.assertEqual(result, 400)
        self.assertEqual(self.device.calls, [])


class SelectInputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.device = FakeDevice()
        self.config = types.SimpleNamespace(identifier="test", name="PlayStation 5")

    async def test_profile_select_and_change(self):
        entity = select_module.PS5ProfileSelect(self.config, self.device)
        self.assertEqual(
            await entity.command("select_next", websocket=None), 200,
        )
        self.assertEqual(self.device.selected_profile, "secondary")
        self.assertEqual(
            await entity.command("select_option", {"option": "unknown"}, websocket=None), 400,
        )

    async def test_library_view_changes_default(self):
        entity = select_module.PS5LibraryViewSelect(self.config, self.device)
        self.assertEqual(
            await entity.command("select_option", {"option": "Recently played"}, websocket=None), 200,
        )
        self.assertEqual(self.device.library_view, "psn_games")

    def test_option_rotation_and_validation(self):
        options = ["primary", "secondary"]
        self.assertEqual(
            select_module.resolve_selection("select_previous", {}, options, "primary"),
            "secondary",
        )
        with self.assertRaises(ValueError):
            select_module.resolve_selection("select_option", {"option": "other"}, options, "primary")


if __name__ == "__main__":
    unittest.main()
