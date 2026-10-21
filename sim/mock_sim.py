"""Mock simulation script for development and testing without SUMO.

Emits realistic fake state, event, and result lines conforming to the live streaming protocol.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import random
import signal
import sys
import time
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] mock_sim: %(message)s",
)
logger = logging.getLogger("mock_sim")

_STOP_REQUESTED = False


def _handle_signal(signum: int, frame: Any) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    logger.warning("Mock simulation received signal %d, stopping.", signum)


def emit_json(data: Dict[str, Any]) -> None:
    """Emit JSON line to stdout and flush."""
    print(json.dumps(data), flush=True)


def main() -> None:
    global _STOP_REQUESTED

    parser = argparse.ArgumentParser(description="Mock Traffic Simulation Engine")
    parser.add_argument("--scenario", type=str, default="emergency", help="Scenario name")
    parser.add_argument("--controller", type=str, default="adaptive_ev", help="Controller name")
    parser.add_argument("--seed", type=int, default=1, help="Random seed")
    parser.add_argument("--gui", action="store_true", help="Ignored in mock")
    parser.add_argument("--params", type=str, default=None, help="Path to params YAML")
    parser.add_argument("--run-id", type=str, default="r1", help="Run identifier")
    parser.add_argument("--live", action="store_true", help="Stream live JSON protocol")
    parser.add_argument("--max-steps", type=int, default=60, help="Simulation duration in steps")
    parser.add_argument("--step-delay", type=float, default=0.02, help="Real-world delay per step in seconds")

    args = parser.parse_args()

    # Handle OS signals
    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, AttributeError):
        pass

    # Validate scenario
    valid_scenarios = {"normal", "congested", "emergency", "incident"}
    if args.scenario not in valid_scenarios and not Path(args.scenario).exists():
        err_msg = f"Unknown scenario: {args.scenario}"
        logger.error(err_msg)
        emit_json({
            "type": "status",
            "run_id": args.run_id,
            "status": "error",
            "message": err_msg,
        })
        sys.exit(1)

    rng = random.Random(args.seed)

    if args.live:
        emit_json({
            "type": "status",
            "run_id": args.run_id,
            "status": "started",
            "message": f"Mock simulation started for scenario '{args.scenario}' with controller '{args.controller}'",
        })

    # State tracking
    t = 0
    phase_name = "NS_GREEN"
    signals = {"N": "G", "S": "G", "E": "r", "W": "r"}
    preempt_active = False
    ev_cleared = False
    max_queue_recorded = 0

    try:
        while t < args.max_steps:
            if _STOP_REQUESTED:
                break

            t += 1

            # Determine traffic light phase and signals
            if preempt_active:
                phase_name = "EV_GREEN"
                signals = {"N": "r", "S": "G", "E": "r", "W": "r"}
            else:
                cycle_pos = t % 60
                if cycle_pos < 25:
                    phase_name = "NS_GREEN"
                    signals = {"N": "G", "S": "G", "E": "r", "W": "r"}
                elif cycle_pos < 28:
                    phase_name = "NS_YELLOW"
                    signals = {"N": "y", "S": "y", "E": "r", "W": "r"}
                elif cycle_pos < 30:
                    phase_name = "ALL_RED"
                    signals = {"N": "r", "S": "r", "E": "r", "W": "r"}
                elif cycle_pos < 55:
                    phase_name = "EW_GREEN"
                    signals = {"N": "r", "S": "r", "E": "G", "W": "G"}
                elif cycle_pos < 58:
                    phase_name = "EW_YELLOW"
                    signals = {"N": "r", "S": "r", "E": "y", "W": "y"}
                else:
                    phase_name = "ALL_RED"
                    signals = {"N": "r", "S": "r", "E": "r", "W": "r"}

            # Fluctuate queues realistically
            base_q = 6 if args.scenario == "congested" else 3
            q_n = max(0, int(base_q + rng.randint(-2, 3)))
            q_s = max(0, int(base_q + rng.randint(-2, 3)))
            q_e = max(0, int(base_q + rng.randint(-2, 3)))
            q_w = max(0, int(base_q + rng.randint(-2, 3)))

            # If green, slowly discharge queue; if red, accumulate
            if signals["N"] == "G":
                q_n = max(0, q_n - 2)
            if signals["S"] == "G":
                q_s = max(0, q_s - 2)
            if signals["E"] == "G":
                q_e = max(0, q_e - 2)
            if signals["W"] == "G":
                q_w = max(0, q_w - 2)

            step_max = max(q_n, q_s, q_e, q_w)
            if step_max > max_queue_recorded:
                max_queue_recorded = step_max

            # Emergency vehicle logic (for emergency scenario)
            ev_list: List[Dict[str, Any]] = []
            if args.scenario in ("emergency", "incident") and (10 <= t <= 35) and not ev_cleared:
                ev_speed = 15.0
                dist_remaining = max(0.0, 300.0 - (t - 10) * ev_speed)
                ev_eta = round(dist_remaining / max(ev_speed, 0.1), 1)

                ev_list.append({
                    "id": "amb1",
                    "tls": "J0",
                    "eta": ev_eta,
                    "dist": round(dist_remaining, 1),
                    "speed": round(ev_speed, 1),
                })

                # Trigger preemption at ETA <= 18s (around t=18)
                if ev_eta <= 18.0 and not preempt_active:
                    preempt_active = True
                    if args.live:
                        emit_json({
                            "type": "event",
                            "run_id": args.run_id,
                            "t": t,
                            "kind": "preempt_start",
                            "tls": "J0",
                            "detail": "Emergency preemption initiated for amb1 on approach S",
                        })

                # Clear preemption when ambulance arrives at junction (dist <= 0)
                if dist_remaining <= 0.0 and preempt_active:
                    preempt_active = False
                    ev_cleared = True
                    if args.live:
                        emit_json({
                            "type": "event",
                            "run_id": args.run_id,
                            "t": t,
                            "kind": "preempt_end",
                            "tls": "J0",
                            "detail": "Emergency vehicle amb1 cleared intersection; preemption ended",
                        })

            # Calculate active metrics
            n_vehicles = max(10, int(20 + 5 * rng.random() + (q_n + q_s + q_e + q_w)))
            avg_wait = round(float(8.0 + (t / 10.0) % 6 + rng.random()), 1)
            throughput = round(float(950.0 + (t * 5.0) + rng.randint(0, 50)), 1)

            if args.live:
                emit_json({
                    "type": "state",
                    "run_id": args.run_id,
                    "t": t,
                    "tls": {
                        "J0": {
                            "phase": phase_name,
                            "queues": {"N": q_n, "S": q_s, "E": q_e, "W": q_w},
                            "signal": signals,
                        }
                    },
                    "ev": ev_list,
                    "metrics": {
                        "avg_wait": avg_wait,
                        "n_vehicles": n_vehicles,
                        "throughput": throughput,
                    },
                })

            if args.step_delay > 0:
                time.sleep(args.step_delay)

    finally:
        pass

    # Final result calculation
    result_metrics = {
        "avg_waiting_time": round(float(9.5 + rng.random() * 2), 2),
        "avg_time_loss": round(float(16.0 + rng.random() * 2), 2),
        "avg_travel_time": round(float(52.0 + rng.random() * 4), 2),
        "ev_travel_time": 22.5 if args.scenario in ("emergency", "incident") else 0.0,
        "max_queue": float(max_queue_recorded or 8.0),
        "throughput_veh_per_hr": round(float(1150.0 + rng.randint(0, 100)), 2),
    }

    if args.live:
        if _STOP_REQUESTED:
            emit_json({
                "type": "status",
                "run_id": args.run_id,
                "status": "stopped",
                "message": "Mock simulation interrupted by signal",
            })
        else:
            emit_json({
                "type": "status",
                "run_id": args.run_id,
                "status": "finished",
                "message": "Mock simulation finished successfully",
            })

        emit_json({
            "type": "result",
            "run_id": args.run_id,
            "metrics": result_metrics,
            "seed": args.seed,
            "controller": args.controller,
            "scenario": args.scenario,
        })
    else:
        # Non-live mode prints the result dictionary
        result_payload = {
            **result_metrics,
            "seed": args.seed,
            "controller": args.controller,
            "scenario": args.scenario,
        }
        print(json.dumps(result_payload), flush=True)


if __name__ == "__main__":
    main()
