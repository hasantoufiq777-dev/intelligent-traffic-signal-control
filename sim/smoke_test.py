"""Smoke test script for SUMO environment and TraCI connection.

Checks SUMO_HOME, starts SUMO headless, steps 10 times via TraCI, and prints OK.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def verify_sumo_home() -> str:
    """Verify SUMO_HOME is set and valid, adding tools to sys.path."""
    sumo_home = os.environ.get("SUMO_HOME")
    if not sumo_home:
        # Check standard default installation paths on Windows
        standard_paths = [
            r"C:\Program Files (x86)\SUMO",
            r"C:\Program Files\Eclipse\SUMO",
        ]
        for sp in standard_paths:
            if os.path.isdir(sp):
                sumo_home = sp
                os.environ["SUMO_HOME"] = sumo_home
                break

    if not sumo_home or not os.path.isdir(sumo_home):
        print(f"ERROR: SUMO_HOME is not set or directory does not exist: {sumo_home}", file=sys.stderr)
        sys.exit(1)

    tools_dir = os.path.join(sumo_home, "tools")
    if tools_dir not in sys.path:
        sys.path.append(tools_dir)

    return sumo_home


def main() -> None:
    sumo_home = verify_sumo_home()

    try:
        import traci
        import sumolib
    except ImportError as e:
        print(f"ERROR: Failed to import traci/sumolib from tools ({e})", file=sys.stderr)
        sys.exit(1)

    sumo_bin = sumolib.checkBinary("sumo")
    sim_cfg = Path(__file__).resolve().parent / "sumo" / "sim.sumocfg"

    if not sim_cfg.exists():
        print(f"ERROR: Simulation config not found at {sim_cfg}", file=sys.stderr)
        sys.exit(1)

    sumo_cmd = [
        sumo_bin,
        "-c",
        str(sim_cfg),
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
    ]

    try:
        traci.start(sumo_cmd)
        for _ in range(10):
            traci.simulationStep()
        traci.close()
        print("OK")
    except Exception as e:
        print(f"ERROR during TraCI simulation: {e}", file=sys.stderr)
        try:
            traci.close()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
