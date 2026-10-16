"""Start SUMO and connect TraCI reliably.

traci.start() connects to "localhost" with no time limit. On some Windows machines a connection attempt
to a port that is not open yet hangs instead of being refused, and "localhost" may resolve to IPv6 ::1
first, where nothing listens. The first attempt, made while SUMO is still loading, then never returns
and the simulation never starts. Here every attempt goes to 127.0.0.1 with a time limit, and failed
attempts are retried until SUMO accepts. The finished connection has no time limit, because one
simulation step may take long.
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
from typing import Any, List

HOST = "127.0.0.1"
ATTEMPT_TIMEOUT_S = 3.0


# Start SUMO with a TraCI port and connect to it (libsumo runs in-process and needs no connection).
def start_sumo(api: Any, cmd: List[str], is_libsumo: bool = False, num_retries: int = 60) -> None:
    if is_libsumo:
        api.start(cmd)
        return
    from sumolib.miscutils import getFreeSocketPort

    port = getFreeSocketPort()
    proc = subprocess.Popen(list(cmd) + ["--remote-port", str(port)])
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(ATTEMPT_TIMEOUT_S)  # each connection attempt gives up instead of hanging
    try:
        # TraCI prints its retry messages to stdout, which carries the live JSON protocol: send them to stderr
        with contextlib.redirect_stdout(sys.stderr):
            api.init(port, numRetries=num_retries, host=HOST, proc=proc)
    except Exception:
        proc.kill()
        raise
    finally:
        socket.setdefaulttimeout(previous)
    try:
        api.getConnection()._socket.settimeout(None)  # a long simulation step must not time out
    except Exception:
        pass
