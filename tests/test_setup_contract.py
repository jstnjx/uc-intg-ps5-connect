"""Source-level checks for the setup flow and public README structure."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SetupContractTests(unittest.TestCase):
    def test_setup_module_parses_and_config_has_no_duplicate_fields(self):
        source = (ROOT / "intg-ps5" / "setup_flow.py").read_text(encoding="utf-8")
        root = ast.parse(source)
        for node in ast.walk(root):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "PSNConfig":
                continue
            names = [item.arg for item in node.keywords if item.arg is not None]
            self.assertEqual(len(names), len(set(names)))

    def test_no_credentials_in_psn_only_setup_path(self):
        source = (ROOT / "intg-ps5" / "setup_flow.py").read_text(encoding="utf-8")
        segment = source.split("        if not control:", 1)[1].split(
            "        self._pending_device_config = PSNConfig(", 1,
        )[0]
        self.assertNotIn("credentials", segment)

    def test_setup_labels_are_direct(self):
        source = (ROOT / "intg-ps5" / "setup_flow.py").read_text(encoding="utf-8")
        form = source.split("    def _account_form(", 1)[1].split(
            "    async def query_device(", 1,
        )[0]
        for unwanted in ("daemon", "Docker", "self-contained", "additional host"):
            self.assertNotIn(unwanted, form)


class ReadmeContractTests(unittest.TestCase):
    def test_five_requested_sections_in_order(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
        self.assertEqual(headings, [
            "1. Features",
            "2. Installation",
            "3. Manual Build",
            "4. Acknowledgments",
            "5. Licenses",
        ])


if __name__ == "__main__":
    unittest.main()
