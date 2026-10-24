"""Learn-once game navigation sequences, stored with the paired PS5 config.

This is a local Remote Play macro fallback, NOT Sony's unpublished direct
game-launch PSN API. No external daemon, console exploit or video decoder.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable

from controller import BUTTONS
from game_launch import normalize_title_id

MAX_STEPS = 96
MAX_GAP_MS = 6000
MAX_REPLAY_SECONDS = 90


class RecordingError(ValueError):
    pass


def normalize_steps(raw: object) -> list[dict]:
    """Validate persisted scripts before sending *any* controller inputs."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STEPS:
        raise RecordingError("Expected 1–96 controller inputs")
    steps = []
    total_ms = 0
    held = set()
    for row in raw:
        if not isinstance(row, dict):
            raise RecordingError("Controller input must be an object")
        button = str(row.get("button") or "").upper().strip()
        action = str(row.get("action") or "tap").lower().strip()
        delay = row.get("delay_ms", 0)
        if button not in BUTTONS or action not in ("tap", "press", "release"):
            raise RecordingError("Unknown controller button or action")
        if isinstance(delay, bool) or not isinstance(delay, int) or not 0 <= delay <= MAX_GAP_MS:
            raise RecordingError("Invalid controller timing")
        if action == "press":
            if button in held:
                raise RecordingError("Button pressed twice without release")
            held.add(button)
        elif action == "release":
            if button not in held:
                raise RecordingError("Button released without press")
            held.remove(button)
        total_ms += delay
        steps.append({"button": button, "action": action, "delay_ms": delay})
    if held:
        raise RecordingError("Recording ends with a held button")
    if total_ms > MAX_REPLAY_SECONDS * 1000:
        raise RecordingError("Recording exceeds maximum replay duration")
    return steps


class GameLaunchRecorder:
    def __init__(self) -> None:
        self.game_id: str | None = None
        self.steps: list[dict] = []
        self._last_input: float | None = None

    @property
    def active(self) -> bool:
        return self.game_id is not None

    def start(self, game_id: str) -> None:
        self.game_id = normalize_title_id(game_id)
        self.steps = []
        self._last_input = None

    def add(self, button: str, action: str = "tap", *, now: float | None = None) -> None:
        if not self.active:
            return
        instant = time.monotonic() if now is None else now
        delay_ms = (
            min(MAX_GAP_MS, max(0, round((instant - self._last_input) * 1000)))
            if self._last_input is not None else 0
        )
        if len(self.steps) >= MAX_STEPS:
            self.cancel()
            raise RecordingError("Too many inputs; recording discarded")
        proposed = self.steps + [{"button": button.upper(), "action": action.lower(), "delay_ms": delay_ms}]
        # Intermediate recordings may end in a held button, so check syntax only here.
        entry = proposed[-1]
        if entry["button"] not in BUTTONS or entry["action"] not in ("tap", "press", "release"):
            raise RecordingError("Unsupported controller event")
        self.steps = proposed
        self._last_input = instant

    def finish(self) -> tuple[str, list[dict]]:
        if self.game_id is None:
            raise RecordingError("No active game recording")
        name = self.game_id
        commands = normalize_steps(self.steps)
        self.cancel()
        return name, commands

    def cancel(self) -> None:
        self.game_id = None
        self.steps = []
        self._last_input = None


async def replay(
    raw: object,
    send: Callable[[str, str], Awaitable[bool]],
    *,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """Replay a validated, bounded game navigation sequence."""
    steps = normalize_steps(raw)
    for item in steps:
        delay = item["delay_ms"] / 1000
        if delay:
            await sleeper(delay)
        if not await send(item["button"], item["action"]):
            return False
    return True
