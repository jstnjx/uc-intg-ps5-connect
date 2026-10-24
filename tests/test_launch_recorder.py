"""Offline tests for the learn-once PS5 game-launch macro backend."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

# Test only the local recording engine. The production pyremoteplay and ucapi
# dependencies are unavailable in the fast offline-check runner.
fake_controller = types.ModuleType("controller")
fake_controller.BUTTONS = frozenset({
    "PS", "CROSS", "CIRCLE", "UP", "DOWN", "LEFT", "RIGHT",
    "L1", "R1", "L2", "R2", "L3", "R3", "OPTIONS", "SQUARE",
    "TRIANGLE", "SHARE", "TOUCHPAD",
})
sys.modules["controller"] = fake_controller

launch_path = Path(__file__).resolve().parents[1] / "intg-ps5"
spec = importlib.util.spec_from_file_location("game_launch", launch_path / "game_launch.py")
game_launch = importlib.util.module_from_spec(spec)
sys.modules["game_launch"] = game_launch
spec.loader.exec_module(game_launch)

spec = importlib.util.spec_from_file_location("launch_recorder", launch_path / "launch_recorder.py")
engine = importlib.util.module_from_spec(spec)
sys.modules["launch_recorder"] = engine
spec.loader.exec_module(engine)


class MacroRecordingTests(unittest.TestCase):
    def test_record_and_finish(self):
        recorder = engine.GameLaunchRecorder()
        recorder.start("PPSA28183_00")
        recorder.add("PS", now=1.0)
        recorder.add("RIGHT", now=1.2)
        recorder.add("CROSS", now=1.6)
        title, steps = recorder.finish()
        self.assertEqual(title, "PPSA28183_00")
        self.assertEqual([s["button"] for s in steps], ["PS", "RIGHT", "CROSS"])
        self.assertEqual([s["delay_ms"] for s in steps], [0, 200, 400])
        self.assertFalse(recorder.active)

    def test_long_wait_clamped(self):
        recorder = engine.GameLaunchRecorder()
        recorder.start("PPSA01721_00")
        recorder.add("PS", now=1)
        recorder.add("CROSS", now=360)
        _, steps = recorder.finish()
        self.assertEqual(steps[1]["delay_ms"], engine.MAX_GAP_MS)

    def test_requires_balanced_buttons(self):
        recorder = engine.GameLaunchRecorder()
        recorder.start("CUSA00605_00")
        recorder.add("PS", "press", now=0)
        with self.assertRaises(engine.RecordingError):
            recorder.finish()

    def test_no_empty_recording(self):
        recorder = engine.GameLaunchRecorder()
        recorder.start("CUSA00605_00")
        with self.assertRaises(engine.RecordingError):
            recorder.finish()

    def test_rejects_unsupported_actions(self):
        for raw in (
            [{"button": "REBOOT", "action": "tap", "delay_ms": 0}],
            [{"button": "PS", "action": "press", "delay_ms": 0}],
            [{"button": "PS", "action": "release", "delay_ms": 0}],
            [{"button": "PS", "action": "tap", "delay_ms": -1}],
            [{"button": "PS", "action": "tap", "delay_ms": True}],
        ):
            with self.subTest(raw=raw), self.assertRaises(engine.RecordingError):
                engine.normalize_steps(raw)

    def test_valid_press_and_release(self):
        self.assertEqual(
            len(engine.normalize_steps([
                {"button": "PS", "action": "press", "delay_ms": 0},
                {"button": "PS", "action": "release", "delay_ms": 1000},
            ])),
            2,
        )


class MacroPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_and_timing(self):
        actions = []
        sleeps = []

        async def send(button, action):
            actions.append((button, action))
            return True

        async def sleep(delay):
            sleeps.append(delay)

        result = await engine.replay([
            {"button": "PS", "action": "tap", "delay_ms": 0},
            {"button": "RIGHT", "action": "tap", "delay_ms": 350},
            {"button": "CROSS", "action": "tap", "delay_ms": 100},
        ], send, sleeper=sleep)
        self.assertTrue(result)
        self.assertEqual(actions, [("PS", "tap"), ("RIGHT", "tap"), ("CROSS", "tap")])
        self.assertEqual(sleeps, [0.35, 0.1])

    async def test_replay_stops_on_failed_input(self):
        calls = []

        async def send(button, action):
            calls.append(button)
            return button != "RIGHT"

        result = await engine.replay([
            {"button": name, "action": "tap", "delay_ms": 0}
            for name in ("PS", "RIGHT", "CROSS")
        ], send)
        self.assertFalse(result)
        self.assertEqual(calls, ["PS", "RIGHT"])


if __name__ == "__main__":
    unittest.main()
