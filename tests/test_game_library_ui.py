"""Offline contract tests for game-library Remote and subtitle-free browser."""
from __future__ import annotations
import ast
import asyncio
import importlib.util
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "intg-ps5"


def mock_module(name, **kwargs):
    mod = types.ModuleType(name)
    mod.__dict__.update(kwargs)
    return mod


class FakeEntity:
    def __init__(self, identifier, name, **kwargs):
        self.id = identifier
        self.name = name
        self.ui_pages = kwargs.get("ui_pages", [])
        self.simple_commands = kwargs.get("simple_commands", [])

    def subscribe_to_device(self, device):
        self.device = device


class FakeDevice:
    can_launch_games = True
    _installed_titles = [
        {"title_id": f"CUSA{i:05d}", "name": f"Game {i}"}
        for i in range(25)
    ]
    _recent_art = {}
    psn_state = "ON"

    def __init__(self):
        self.launched = []

    async def launch_game(self, title):
        self.launched.append(title)
        return True


def load_game_remote():
    features = types.SimpleNamespace(SEND_CMD="send_cmd")
    commands = types.SimpleNamespace(SEND_CMD="send_cmd")
    states = types.SimpleNamespace(UNKNOWN="UNKNOWN", ON="ON", OFF="OFF", UNAVAILABLE="UNAVAILABLE")
    remote_mod = mock_module(
        "ucapi.remote", Features=features, Commands=commands, States=states,
        Attributes=types.SimpleNamespace(STATE="state"),
        create_send_cmd=lambda command: types.SimpleNamespace(cmd_id="send_cmd", command=command),
    )
    ui_mod = mock_module(
        "ucapi.ui",
        Size=lambda x, y: (x, y),
        create_ui_text=lambda text, x, y, cmd: types.SimpleNamespace(text=text, x=x, y=y, cmd=cmd),
        UiPage=lambda page_id, name, **kwargs: types.SimpleNamespace(
            page_id=page_id, name=name, grid=kwargs["grid"], items=kwargs["items"]
        ),
    )
    substitutes = {
        "ucapi": mock_module("ucapi", StatusCodes=types.SimpleNamespace(
            OK=200, BAD_REQUEST=400, NOT_IMPLEMENTED=501, NOT_FOUND=404,
            SERVER_ERROR=500, SERVICE_UNAVAILABLE=503),
            remote=remote_mod, media_player=mock_module(
                "ucapi.media_player", States=types.SimpleNamespace(ON="ON", PLAYING="PLAYING", OFF="OFF")
            )),
        "ucapi.remote": remote_mod,
        "ucapi.ui": ui_mod,
        "ucapi.entity": mock_module("ucapi.entity", EntityTypes=types.SimpleNamespace(REMOTE="remote")),
        "ucapi_framework": mock_module("ucapi_framework", create_entity_id=lambda kind, suffix: f"{kind}.{suffix}"),
        "ucapi_framework.entities": mock_module("ucapi_framework.entities", RemoteEntity=FakeEntity),
        "game_launch": mock_module("game_launch", normalize_title_id=lambda x: re.fullmatch(
            r"(CUSA|PPSA)[0-9]{5}", str(x)
        ).group(0)),
        "game_metadata": mock_module("game_metadata", clean_game_title=lambda x: str(x or "")),
    }
    with patch.dict(sys.modules, substitutes):
        spec = importlib.util.spec_from_file_location("test_ps5_game_remote", CODE / "game_remote.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


class GameRemoteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_game_remote()
        self.device = FakeDevice()

    def test_four_columns_six_rows_24_buttons_per_page(self):
        pages, commands = self.module.make_game_pages(
            self.device._installed_titles, self.device._recent_art
        )
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].grid, (4, 6))
        self.assertEqual(len(pages[0].items), 24)
        self.assertEqual(len(pages[1].items), 1)
        self.assertEqual((pages[0].items[-1].x, pages[0].items[-1].y), (3, 5))
        self.assertTrue(all(len(item) <= 20 for item in commands))

    async def test_game_commands_use_direct_launch_not_navigation(self):
        entity = self.module.PS5GameLibraryRemote(
            types.SimpleNamespace(identifier="ps5_test", name="PlayStation 5"),
            self.device,
        )
        cmd = entity.simple_commands[0]
        self.assertEqual(await entity.command("send_cmd", {"command": cmd}, websocket=None), 200)
        self.assertEqual(self.device.launched, ["CUSA00000"])
        self.assertEqual(await entity.command("send_cmd", {"command": "GAME_PPSA99999"}, websocket=None), 400)

    def test_media_browser_has_no_subtitles(self):
        tree = ast.parse((CODE / "media_player.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "BrowseMediaItem":
                self.assertNotIn("subtitle", [kw.arg for kw in node.keywords])


if __name__ == "__main__":
    unittest.main()
