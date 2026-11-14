"""JSON-line commands on stdin from the Node gateway (one-driver mode). Standard library only,
so the mock simulation can use it too."""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Dict, IO, List

logger = logging.getLogger(__name__)


class CommandReader:
    """Reads JSON-line commands from a text stream on a background thread."""

    def __init__(self, stream: IO[str]) -> None:
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, args=(stream,), daemon=True, name="driver-commands")
        self._thread.start()

    def _run(self, stream: IO[str]) -> None:
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    logger.warning("Ignoring a command that is not JSON: %.80s", line)
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("cmd"), str):
                    self._q.put(obj)
        except Exception as e:  # stream closed
            logger.debug("Command stream ended: %s", e)

    def poll(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out


# ---------------------------------------------------------------------------- playback
SPEEDS = (0.5, 1.0, 2.0, 4.0)  # allowed playback speeds, relative to the run's --step-delay
PLAYBACK_COMMANDS = ("pause", "resume", "speed")


class Playback:
    """Live pacing chosen from the dashboard: pause, resume and speed.

    Only wall-clock pacing changes. The simulated seconds, and every decision in them, are the same
    as without these commands: while paused SUMO is simply not stepped. Each change is confirmed
    with a ``playback`` line, so the dashboard shows what the simulation actually does.
    """

    def __init__(self, step_delay: float, emit: Any, run_id: Any) -> None:
        self.base_delay = max(0.0, float(step_delay))
        self.step_delay = self.base_delay
        self.speed = 1.0
        self.paused = False
        self._emit = emit
        self._run_id = run_id
        self._held: List[Dict[str, Any]] = []  # other commands that arrived while paused

    def split(self, cmds: List[Dict[str, Any]], t: float) -> List[Dict[str, Any]]:
        """Apply the playback commands in ``cmds``; return the others (driver commands), in order."""
        others, self._held = self._held, []
        for cmd in cmds:
            if cmd.get("cmd") in PLAYBACK_COMMANDS:
                self._apply(cmd, t)
            else:
                others.append(cmd)
        return others

    def _apply(self, cmd: Dict[str, Any], t: float) -> None:
        kind = cmd.get("cmd")
        if kind == "pause":
            self.paused = True
        elif kind == "resume":
            self.paused = False
        elif kind == "speed":
            try:
                speed = float(cmd.get("speed"))
            except (TypeError, ValueError):
                logger.warning("Ignoring playback speed %r", cmd.get("speed"))
                return
            if speed not in SPEEDS:
                logger.warning("Ignoring playback speed %r (allowed: %s)", speed, SPEEDS)
                return
            self.speed = speed
            self.step_delay = self.base_delay / speed
        self._emit({"type": "playback", "run_id": self._run_id, "t": t, "paused": self.paused,
                    "speed": self.speed, "step_delay": round(self.step_delay, 4)})

    def wait_while_paused(self, reader: Any, t: float, should_stop: Any, tick: float = 0.1) -> None:
        """Block between two simulated seconds while paused; keep reading commands meanwhile."""
        import time

        while self.paused and not should_stop():
            time.sleep(tick)
            if reader is not None:
                # split() replaces self._held, so take its result first, then keep it for later
                others = self.split(reader.poll(), t)
                self._held.extend(others)
