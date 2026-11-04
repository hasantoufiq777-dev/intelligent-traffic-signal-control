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
