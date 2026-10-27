"""CLI entry point for the simulation.

Logs only to stderr. Output on stdout is reserved for JSON results and live streaming protocol.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from sim.runner import run_simulation

# Configure logging strictly to stderr
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sim.main")


def main() -> None:
    parser = argparse.ArgumentParser(description="Intelligent Traffic Signal Control Simulation CLI")
    parser.add_argument("--scenario", type=str, default="normal", help="Scenario name or YAML file path")
    parser.add_argument("--controller", type=str, default="fixed", help="Controller name (fixed, adaptive, etc.)")
    parser.add_argument("--seed", type=int, default=1, help="Random seed")
    parser.add_argument("--gui", action="store_true", help="Launch SUMO with GUI")
    parser.add_argument("--params", type=str, default=None, help="Path to custom parameters YAML file")
    parser.add_argument("--run-id", type=str, default="r1", help="Identifier for this run")
    parser.add_argument("--live", action="store_true", help="Enable live streaming mode")
    parser.add_argument("--max-steps", type=int, default=None, help="Override maximum simulation steps")

    # Module 4b / v2 CLI additions
    parser.add_argument(
        "--nav",
        type=str,
        default="none",
        choices=["none", "astar", "aco", "aco_tuned"],
        help="Navigation mode",
    )
    parser.add_argument("--penetration", type=float, default=1.0, help="OBU penetration rate (0.0 to 1.0)")
    parser.add_argument("--nav-fraction", type=float, default=0.3, help="Fraction of navigating vehicles (0.0 to 1.0)")
    parser.add_argument("--loss", type=float, default=0.0, help="Base channel loss probability (0.0 to 1.0)")
    parser.add_argument("--range", type=float, default=300.0, dest="range_m", help="Wireless communication range in meters")
    parser.add_argument("--relay", type=str, default="off", choices=["on", "off"], help="V2V multi-hop relaying")
    parser.add_argument("--aco-params", type=str, default=None, help="Path to custom ACO parameters YAML file")

    args = parser.parse_args()

    param_overrides: Optional[Dict[str, Any]] = None
    if args.params:
        params_path = Path(args.params)
        if params_path.exists():
            with open(params_path, "r", encoding="utf-8") as f:
                param_overrides = yaml.safe_load(f)
        else:
            err_msg = f"Params file not found: {args.params}"
            logger.error(err_msg)
            print(
                json.dumps({
                    "type": "status",
                    "run_id": args.run_id,
                    "status": "error",
                    "message": err_msg,
                }),
                flush=True,
            )
            sys.exit(1)

    try:
        logger.info(
            "Starting simulation run %s: scenario=%s, controller=%s, seed=%d, nav=%s, live=%s",
            args.run_id,
            args.scenario,
            args.controller,
            args.seed,
            args.nav,
            args.live,
        )

        result = run_simulation(
            scenario=args.scenario,
            controller=args.controller,
            seed=args.seed,
            params=param_overrides,
            gui=args.gui,
            run_id=args.run_id,
            live=args.live,
            max_steps=args.max_steps,
            nav=args.nav,
            penetration=args.penetration,
            nav_fraction=args.nav_fraction,
            loss=args.loss,
            range_m=args.range_m,
            relay=args.relay,
            aco_params=args.aco_params,
        )

        # In non-live mode, output result at the end as JSON on stdout
        if not args.live:
            print(json.dumps(result), flush=True)

        sys.exit(0)

    except NotImplementedError as e:
        logger.error("Feature not implemented: %s", e)
        print(
            json.dumps({
                "type": "status",
                "run_id": args.run_id,
                "status": "error",
                "message": f"NotImplementedError: {e}",
            }),
            flush=True,
        )
        sys.exit(1)

    except Exception as e:
        logger.exception("Simulation execution failed: %s", e)
        print(
            json.dumps({
                "type": "status",
                "run_id": args.run_id,
                "status": "error",
                "message": str(e),
            }),
            flush=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
