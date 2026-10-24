"""The installed game library remains inside the Media Player."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "intg-ps5"


class MediaBrowserContractTests(unittest.TestCase):
    def test_media_browser_exposes_installed_games_and_details(self):
        src = (ROOT / "media_player.py").read_text(encoding="utf-8")
        self.assertIn('folder == "installed_games"', src)
        self.assertIn('folder == "game_details"', src)
        self.assertIn("get_installed_game_library(", src)

    def test_browse_media_items_have_no_subtitles(self):
        source = (ROOT / "media_player.py").read_text(encoding="utf-8")
        root = ast.parse(source)
        for node in ast.walk(root):
            if (isinstance(node, ast.Call) and
                    isinstance(node.func, ast.Name) and
                    node.func.id == "BrowseMediaItem"):
                self.assertNotIn("subtitle", [kw.arg for kw in node.keywords])


if __name__ == "__main__":
    unittest.main()
