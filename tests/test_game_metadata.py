"""Offline PlayStation game metadata regression tests.

No PSN credentials, network access or real console required.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


BASE = Path(__file__).resolve().parents[1] / "intg-ps5"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


game_launch = load("game_launch", "game_launch.py")
metadata = load("ps5_test_metadata", "game_metadata.py")


class Response:
    def __init__(self, code: int, data: object):
        self.status_code = code
        self._data = data

    def json(self):
        return self._data


class GamelistAuthenticator:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        index = kwargs["params"]["offset"] // 50
        return self.pages.get(index, Response(200, {"titles": []}))


class MetadataTests(unittest.TestCase):
    def test_clean_titles_trademarks_and_html_entities(self):
        for name, expected in (
            ("Astro Bot (TM)", "Astro Bot"),
            ("My Game (Playstatio&#x6E;®5)", "My Game"),
            ("Ratchet &amp; Clank™", "Ratchet & Clank"),
            ("Demon's Souls (PS5)", "Demon's Souls"),
            ("ps5_native_game", ""),
            ("CUSA00314", ""),
        ):
            with self.subTest(name=name):
                self.assertEqual(metadata.clean_game_title(name), expected)

    def test_trophy_title_metadata_is_normalized(self):
        class TrophyAuth:
            def get(self, **kwargs):
                self.request = kwargs
                return Response(200, {"titles": [
                    {"npTitleId": "PPSA28183_00", "trophyTitles": [{
                        "trophyTitleName": "Returnal (PlayStation®5)",
                        "trophyTitleIconUrl": "https://example.org/returnal.png",
                    }]},
                ]})
        auth = TrophyAuth()
        result = metadata.fetch_trophy_game_metadata(auth, {"PPSA28183"})
        self.assertEqual(result["PPSA28183"]["name"], "Returnal")
        self.assertTrue(result["PPSA28183"]["image"].startswith("https://"))
        self.assertEqual(auth.request["params"]["npTitleIds"], "PPSA28183_00")

    def test_localized_name_and_image_preferred(self):
        parsed, ok = metadata.parse_titles_page({"titles": [{
            "titleId": "PPSA28183_00",
            "name": "Original",
            "localizedName": "Localized",
            "imageUrl": "https://example.org/original.jpg",
            "localizedImageUrl": "https://example.org/local.jpg",
        }]})
        self.assertTrue(ok)
        self.assertEqual(parsed[0][0], "PPSA28183")
        self.assertEqual(parsed[0][1]["name"], "Localized")
        self.assertEqual(parsed[0][1]["image"], "https://example.org/local.jpg")

    def test_invalid_page_and_unsafe_artwork(self):
        self.assertFalse(metadata.parse_titles_page({})[1])
        parsed, ok = metadata.parse_titles_page({"titles": [
            {"titleId": "CUSA00605_00", "name": "Game", "imageUrl": "file:///etc/passwd"},
            {"titleId": "not-a-title", "name": "Bad"},
        ]})
        self.assertTrue(ok)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0][1]["image"], "")

    def test_paginate_past_old_150_game_cap(self):
        pages = {}
        for p in range(4):
            rows = [
                {"titleId": f"CUSA{p * 50 + i:05d}_00",
                 "localizedName": f"Game {p * 50 + i}",
                 "localizedImageUrl": "https://image.api.playstation.com/demo.jpg"}
                for i in range(50)
            ]
            pages[p] = Response(200, {"titles": rows})
        auth = GamelistAuthenticator(pages)
        results = metadata.fetch_installed_metadata(auth, {"CUSA00187"}, budget=20)
        self.assertEqual(results["CUSA00187"]["name"], "Game 187")
        self.assertEqual(len(auth.calls), 4)
        self.assertEqual(
            auth.calls[0]["url"],
            "https://m.np.playstation.com/api/gamelist/v2/users/me/titles",
        )
        self.assertEqual(auth.calls[3]["params"]["offset"], 150)

    def test_missing_titles_return_partial_data(self):
        auth = GamelistAuthenticator({
            0: Response(200, {"titles": [
                {"titleId": "PPSA00001", "name": "Known", "imageUrl": "https://example.com/1.png"},
            ]}),
        })
        result = metadata.fetch_installed_metadata(auth, {"PPSA00001", "CUSA00002"})
        self.assertEqual(result["PPSA00001"]["name"], "Known")
        self.assertNotIn("CUSA00002", result)

    def test_http_auth_failure_does_not_leak_token(self):
        auth = GamelistAuthenticator({0: Response(401, {})})
        self.assertEqual(
            metadata.fetch_installed_metadata(auth, {"PPSA00001"}), {}
        )

    def test_unresolved_title_is_friendly_and_preserves_id(self):
        result = metadata.display_installed_title({
            "title_id": "PPSA00001", "name": "", "category": "",
        })
        self.assertEqual(result["name"], "Unknown PlayStation title")
        self.assertTrue(result["missing_name"])
        self.assertEqual(result["title_id"], "PPSA00001")
        self.assertEqual(result["image"], "")

    def test_metadata_merges_with_installed_title(self):
        result = metadata.display_installed_title(
            {"title_id": "PPSA00001", "name": "", "category": ""},
            {"name": "My Game", "image": "https://example.org/my.jpg"},
        )
        self.assertEqual(result["name"], "My Game")
        self.assertEqual(result["image"], "https://example.org/my.jpg")
        self.assertFalse(result["missing_name"])


if __name__ == "__main__":
    unittest.main()
