"""Tests for fail-closed local PS5 discovery identity parsing."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


source = Path(__file__).resolve().parents[1] / "intg-ps5" / "lan_identity.py"
spec = importlib.util.spec_from_file_location("test_ps5_lan_identity", source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class LocalIdentityTests(unittest.TestCase):
    def test_case_insensitive_ddp_keys(self):
        parsed = module.parse_ddp_fields(
            b"HTTP/1.1 200 OK\r\nHost-ID:  AA:BB:CC:DD:EE:FF  \r\nhost-name: Living Room\r\n"
        )
        self.assertEqual(parsed["host-id"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(parsed["host-name"], "Living Room")

    def test_normalization(self):
        self.assertEqual(
            module.normalize_host_id("  AA:BB:CC  "),
            module.normalize_host_id("aa:bb:cc"),
        )

    def test_no_discovery_without_pinned_identity(self):
        self.assertIsNone(module.find_host_ip(""))
        self.assertIsNone(module.find_host_ip("short"))


if __name__ == "__main__":
    unittest.main()
