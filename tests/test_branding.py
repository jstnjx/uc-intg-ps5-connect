"""Ensure the PS5 Connect rename preserves existing Remote identities."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = "uc-intg-ps5-connect-aarch64.tar.gz"


class PS5ConnectBrandingTests(unittest.TestCase):
    def test_public_name_and_compatible_driver_id(self):
        manifest = json.loads((ROOT / "driver.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"]["en"], "PS5 Connect")
        self.assertEqual(manifest["driver_id"], "ps5_control_driver")
        self.assertEqual(manifest["setup_data_schema"]["title"]["en"], "PS5 Connect setup")

    def test_documentation_and_build_artifact_are_consistent(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        build_script = (ROOT / "build.sh").read_text(encoding="utf-8")
        ci = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("# PS5 Connect\\n"))
        for content in (readme, build_script, ci):
            self.assertIn(ARCHIVE, content)
            self.assertNotIn("uc-intg-ps5-control-aarch64.tar.gz", content)


if __name__ == "__main__":
    unittest.main()
