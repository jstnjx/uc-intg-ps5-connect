"""Unit tests for UC media-player/sensor entity identifier routing."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


def import_helpers():
    target = Path(__file__).resolve().parents[1] / "intg-ps5" / "entity_ids.py"
    spec = importlib.util.spec_from_file_location("ps5_entity_ids", target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


helper = import_helpers()


class EntityIdRoutingTests(unittest.TestCase):
    def test_auth_sensor_routes_to_parent_ps5(self):
        exists = {"ps5_10_1_1_30"}.__contains__
        self.assertEqual(
            helper.resolve_device_id(
                "sensor.ps5_10_1_1_30_authentication",
                "ps5_10_1_1_30_authentication",
                exists,
            ),
            "ps5_10_1_1_30",
        )

    def test_other_entities_are_unchanged(self):
        exists = {"ps5_10_1_1_30"}.__contains__
        for entity_id, device_id in [
            ("sensor.ps5_10_1_1_30", "ps5_10_1_1_30"),
            ("media_player.ps5_10_1_1_30", "ps5_10_1_1_30"),
            ("switch.ps5_10_1_1_30", "ps5_10_1_1_30"),
        ]:
            with self.subTest(entity_id=entity_id):
                self.assertEqual(helper.resolve_device_id(entity_id, device_id, exists), device_id)

    def test_genuine_suffix_on_device_id_kept(self):
        exists = {"my_ps5_authentication", "my_ps5"}.__contains__
        self.assertEqual(
            helper.resolve_device_id(
                "sensor.my_ps5_authentication",
                "my_ps5_authentication",
                exists,
            ),
            "my_ps5_authentication",
        )

    def test_unknown_authentication_sensor_not_mapped_arbitrarily(self):
        exists = set().__contains__
        self.assertEqual(
            helper.resolve_device_id(
                "sensor.unknown_authentication",
                "unknown_authentication",
                exists,
            ),
            "unknown_authentication",
        )

    def test_empty_id(self):
        self.assertIsNone(helper.resolve_device_id("", None, lambda _: False))


if __name__ == "__main__":
    unittest.main()
