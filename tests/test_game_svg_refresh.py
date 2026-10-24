"""Qt StyledText SVG game icons and scheduled Core REST page refresh tests."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parents[1] / "intg-ps5"


def load(filename, name):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


art = load("game_artwork.py", "ps5_test_svg_art")
refresh = load("game_refresh.py", "ps5_test_game_refresh")


@dataclass
class Page:
    page_id: str
    name: str
    grid: dict
    items: list


class Config:
    game_refresh_enabled = True
    game_refresh_time = "03:00"
    game_refresh_timezone = "Europe/Berlin"
    game_refresh_last_date = ""
    game_refresh_page_ids = []
    remote_url = "http://127.0.0.1"
    remote_api_key = "test-not-real-key"


class SvgMarkupTests(unittest.TestCase):
    def test_exact_size_and_center(self):
        uri = art.make_icon("Test <Game>")
        svg = base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")
        self.assertIn('width="85" height="85"', svg)
        self.assertIn("&lt;Game&gt;", svg)
        markup = art.styled_image(uri)
        self.assertIn('align="middle"', markup)
        self.assertIn('align="center"', markup)
        self.assertIn('width="85" height="85"', markup)
        self.assertIn(uri, markup)

    def test_escaped_svg_rejects_invalid_source(self):
        with self.assertRaises(ValueError):
            art.styled_image("https://not-a-data-uri/")


class ScheduleTests(unittest.TestCase):
    def test_local_time_and_persisted_day(self):
        cfg = Config()
        before = datetime(2026, 10, 25, 2, 59, tzinfo=ZoneInfo("Europe/Berlin"))
        after = datetime(2026, 10, 25, 3, 1, tzinfo=ZoneInfo("Europe/Berlin"))
        self.assertFalse(refresh.due_for_refresh(cfg, before))
        self.assertTrue(refresh.due_for_refresh(cfg, after))
        cfg.game_refresh_last_date = "2026-10-25"
        self.assertFalse(refresh.due_for_refresh(cfg, after))

    def test_config_validation(self):
        self.assertFalse(refresh.valid_refresh_time("25:01"))
        self.assertTrue(refresh.valid_refresh_time("03:00"))
        cfg = Config()
        cfg.remote_api_key = ""
        self.assertFalse(refresh.due_for_refresh(cfg, datetime.now(ZoneInfo("Europe/Berlin"))))


class FakeCoreAPI:
    calls = []
    pages = None

    def __init__(self, url, **kwargs):
        self.pages = list(self.__class__.pages)
        self.calls = self.__class__.calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json")))
        if method == "GET":
            return list(self.pages)
        if method == "POST":
            return {"page_id": "core-created-123"}
        return None


class CorePageTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_owned_pages_updated_and_stale_deleted(self):
        cfg = Config()
        cfg.game_refresh_page_ids = ["old-generated-1", "old-generated-2"]
        FakeCoreAPI.pages = [
            {"page_id": "old-generated-1", "name": "Games 1", "grid": {}, "items": []},
            {"page_id": "old-generated-2", "name": "Games 2", "grid": {}, "items": []},
            {"page_id": "my-custom", "name": "Custom", "grid": {}, "items": []},
        ]
        FakeCoreAPI.calls = []
        with patch.dict(sys.modules, {
            "unfurled": types.ModuleType("unfurled"),
            "unfurled.api": types.SimpleNamespace(CoreAPI=FakeCoreAPI),
        }):
            result = await refresh.update_core_library_pages(
                cfg, "remote.ps5_game_library",
                [Page("games_1", "Games 1", {"width": 4, "height": 6}, [])],
            )
        self.assertEqual(result, ["old-generated-1"])
        self.assertEqual([row[0] for row in FakeCoreAPI.calls], ["GET", "PATCH", "DELETE"])
        self.assertFalse(any("my-custom" in row[1] for row in FakeCoreAPI.calls))

    async def test_new_pages_keep_core_generated_ids(self):
        cfg = Config()
        cfg.game_refresh_page_ids = []
        FakeCoreAPI.pages = [
            {"page_id": "my-custom", "name": "Custom", "items": []}
        ]
        FakeCoreAPI.calls = []
        with patch.dict(sys.modules, {
            "unfurled": types.ModuleType("unfurled"),
            "unfurled.api": types.SimpleNamespace(CoreAPI=FakeCoreAPI),
        }):
            result = await refresh.update_core_library_pages(
                cfg, "remote.ps5_game_library",
                [Page("games_1", "Games 1", {"width": 4, "height": 6}, [])],
            )
        self.assertEqual(result, ["core-created-123"])
        self.assertEqual([row[0] for row in FakeCoreAPI.calls], ["GET", "POST"])


if __name__ == "__main__":
    unittest.main()
