"""Release manifest, driver identity, and versioned archive consistency."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PS5ConnectReleaseTests(unittest.TestCase):
    def test_manifest_name_version_and_new_driver_id(self):
        manifest = json.loads((ROOT / "driver.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"]["en"], "PS5 Connect")
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(manifest["driver_id"], "ps5_connect_driver")
        self.assertEqual(manifest["setup_data_schema"]["title"]["en"], "PS5 Connect setup")
        code = (ROOT / "intg-ps5" / "driver.py").read_text(encoding="utf-8")
        self.assertIn('driver_id="ps5_connect_driver"', code)

    def test_archive_filename_comes_from_manifest_version(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        script = (ROOT / "build.sh").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertEqual(readme.splitlines()[0], "# PS5 Connect")
        self.assertIn("uc-intg-ps5-connect-v1.0.0-aarch64.tar.gz", readme)
        self.assertIn('ARCHIVE="uc-intg-ps5-connect-v${VERSION}-aarch64.tar.gz"', script)
        self.assertIn('ARCHIVE="uc-intg-ps5-connect-v${VERSION}-aarch64.tar.gz"', workflow)
        self.assertIn("steps.build.outputs.archive", workflow)
        self.assertIn("ps5-connect-aarch64-package", workflow)
        self.assertNotIn("uc-intg-ps5-connect-aarch64.tar.gz", workflow)

    def test_game_browser_stays_and_generated_remote_is_removed(self):
        sources = ROOT / "intg-ps5"
        for name in ("game_remote.py", "game_artwork.py", "game_refresh.py", "game_ws.py"):
            self.assertFalse((sources / name).exists(), name)
        psn = (sources / "psn.py").read_text(encoding="utf-8")
        media = (sources / "media_player.py").read_text(encoding="utf-8")
        self.assertIn("get_installed_game_library", psn)
        self.assertIn('folder == "installed_games"', media)
        self.assertNotIn("_publish_game_remote", psn)
        self.assertNotIn("game_refresh", psn)


if __name__ == "__main__":
    unittest.main()
