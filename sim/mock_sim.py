"""Mock simulation script for development and testing without SUMO.

Emits realistic fake state, event, and result lines conforming to the live streaming protocol v2.
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


class MockAmbulances:
    """Ambulances sent from the dashboard (fake timing, same events and result fields as sim/dispatch.py)."""

    MAX_ACTIVE = 3

    def __init__(self, tls_ids: List[str], controller: str) -> None:
        self.tls_ids = list(tls_ids)
        self.native = controller.startswith("adaptive_ev")
        self.trips: List[Dict[str, Any]] = []
        self.wrapped: List[str] = []

    def handle(self, cmd: Dict[str, Any], t: int) -> List[Dict[str, Any]]:
        active = [r for r in self.trips if r["arrived_t"] is None]
        if len(active) >= self.MAX_ACTIVE:
            return [("ambulance_rejected", "", f"Ambulance not sent: {self.MAX_ACTIVE} dispatched ambulances "
                                                "are already on the road")]
        origin, dest = str(cmd.get("from") or "S2J0"), str(cmd.get("to") or "J02N")
        if origin == dest:
            return [("ambulance_rejected", "", "Ambulance not sent: start and destination are the same road")]
        if not self.trips and not self.native:
            self.wrapped = list(self.tls_ids)
        vid = f"amb_dispatch_{len(self.trips) + 1}"
        self.trips.append({"id": vid, "from": origin, "to": dest, "dispatched_t": t, "depart_t": t,
                           "arrived_t": None, "travel_time": None, "junctions": [self.tls_ids[0]], "priority_at": []})
        how = ("ambulance priority switched on at " + ", ".join(self.wrapped)) if self.wrapped else \
            "the junctions' own ambulance priority"
        return [("ambulance_dispatched", self.tls_ids[0],
                 f"Ambulance {vid} sent from {origin} to {dest} through {self.tls_ids[0]} ({how})")]

    def step(self, t: int) -> List[Any]:
        out: List[Any] = []
        for r in self.trips:
            if r["arrived_t"] is not None:
                continue
            age = t - r["dispatched_t"]
            j = r["junctions"][0]
            if age == 3:
                r["priority_at"] = [j]
                out.append(("preempt_start", j, f"Emergency preemption initiated for {r['id']} on approach S"))
            elif age == 10:
                out.append(("preempt_end", j, f"Emergency vehicle {r['id']} cleared intersection; preemption ended"))
            elif age >= 12:
                r["arrived_t"], r["travel_time"] = t, age
                out.append(("ambulance_arrived", j, f"Ambulance {r['id']} reached {r['to']} after {age} s; "
                                                    f"green given at 1 of 1 junctions"))
        return out

    def ev_list(self, t: int) -> List[Dict[str, Any]]:
        out = []
        for r in self.trips:
            age = t - r["dispatched_t"]
            if r["arrived_t"] is None and age < 10:
                dist = max(0.0, 150.0 - age * 15.0)
                out.append({"id": r["id"], "tls": r["junctions"][0], "eta": round(dist / 15.0, 1),
                            "dist": dist, "speed": 15.0, "alert_age": 0.2})
        return out

    def metrics(self) -> Dict[str, Any]:
        done = [r for r in self.trips if r["travel_time"] is not None]
        return {
            "dispatched_ambulances": len(self.trips),
            "dispatched_arrived": len(done),
            "dispatched_avg_travel_time": round(sum(r["travel_time"] for r in done) / len(done), 1) if done else None,
            "dispatched_priority_added": self.wrapped,
            "dispatched_trips": [dict(r) for r in self.trips],
        }


def main() -> None:
    global _STOP_REQUESTED

    parser = argparse.ArgumentParser(description="Mock Traffic Simulation Engine v2")
    parser.add_argument("--scenario", type=str, default="emergency", help="Scenario name")
    parser.add_argument("--controller", type=str, default="adaptive_ev", help="Controller name")
    parser.add_argument("--seed", type=int, default=1, help="Random seed")
    parser.add_argument("--gui", action="store_true", help="Ignored in mock")
    parser.add_argument("--params", type=str, default=None, help="Path to params YAML")
    parser.add_argument("--run-id", type=str, default="r1", help="Run identifier")
    parser.add_argument("--live", action="store_true", help="Stream live JSON protocol")
    parser.add_argument("--network", type=str, default=None, help="Network type (single | grid2x2)")
    parser.add_argument("--max-steps", type=int, default=60, help="Simulation duration in steps")
    parser.add_argument("--step-delay", type=float, default=0.02, help="Real-world delay per step in seconds")

    # Module 4b / v2 flags
    parser.add_argument(
        "--nav",
        type=str,
        default="none",
        choices=["none", "astar", "aco", "aco_tuned"],
        help="Navigation mode",
    )
    parser.add_argument("--penetration", type=float, default=0.5, help="OBU penetration rate (0.0 to 1.0)")
    parser.add_argument("--nav-fraction", type=float, default=0.3, help="Fraction of navigating vehicles")
    parser.add_argument("--loss", type=float, default=0.0, help="Base channel loss probability")
    parser.add_argument("--range", type=float, default=300.0, dest="range_m", help="Wireless communication range")
    parser.add_argument("--relay", type=str, default="off", choices=["on", "off"], help="V2V multi-hop relaying")
    parser.add_argument("--aco-params", type=str, default=None, help="Path to custom ACO params YAML")
    parser.add_argument("--eco-fraction", type=float, default=0.0, help="Share of equipped cars following GLOSA advice")
    parser.add_argument("--ev-routing", type=str, default="off", choices=["on", "off"], help="Ignored in mock")
    parser.add_argument("--demand", type=float, default=1.0, help="Traffic demand factor (scales the mock's cars)")
    parser.add_argument("--driver", action="store_true", help="One-driver mode: drive commands as JSON lines on stdin")

    args = parser.parse_args()

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, AttributeError):
        pass

    valid_scenarios = {"normal", "congested", "emergency", "incident", "navigation", "ev_incident", "ev_congested",
                       "accident_zone", "city"}
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

    # Determine network type
    network = args.network
    if not network:
        if Path(args.scenario).exists():
            try:
                import yaml
                with open(args.scenario, "r", encoding="utf-8") as f:
                    sc_data = yaml.safe_load(f)
                    network = sc_data.get("network", "single")
            except Exception:
                network = "single"
        elif args.scenario in ("navigation", "incident", "ev_incident", "ev_congested", "accident_zone", "city"):
            network = "grid2x2"
        else:
            network = "single"

    if network in ("grid2x2_zone", "grid2x2_city"):
        # The mock has no work zone or city ring: it shows the plain grid
        network = "grid2x2"
    tls_ids = ["J0", "J1", "J2", "J3"] if network == "grid2x2" else ["J0"]
    try:
        from sim.mock_traffic import MockTraffic, mock_spat
    except ImportError:  # run as a script from sim/
        from mock_traffic import MockTraffic, mock_spat  # type: ignore
    traffic = MockTraffic(network, random.Random(args.seed + 7), args.penetration, args.eco_fraction, args.nav,
                          with_ev=args.scenario in ("emergency", "incident"))
    commands = None
    playback = None  # pause / resume / speed from the dashboard (wall-clock only)
    if args.driver and args.live:
        try:
            from sim.commands import CommandReader, Playback
        except ImportError:  # run as a script from sim/
            from commands import CommandReader, Playback  # type: ignore
        commands = CommandReader(sys.stdin)
        playback = Playback(args.step_delay, emit_json, args.run_id)
    ambulances = MockAmbulances(tls_ids, args.controller)

    if args.live:
        emit_json({
            "type": "status",
            "run_id": args.run_id,
            "status": "started",
            "message": f"Mock simulation started for scenario '{args.scenario}' with controller '{args.controller}' on network '{network}'",
        })
        emit_json(traffic.network_message(args.run_id))

    t = 0
    phase_name = "NS_GREEN"
    signals = {"N": "G", "S": "G", "E": "r", "W": "r"}
    preempt_active = False
    ev_cleared = False
    max_queue_recorded = 0
    total_sent = 0
    total_lost = 0

    try:
        while t < args.max_steps:
            if _STOP_REQUESTED:
                break

            t += 1

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

            base_q = 6 if args.scenario == "congested" else 3
            q_n = max(0, int(base_q + rng.randint(-2, 3)))
            q_s = max(0, int(base_q + rng.randint(-2, 3)))
            q_e = max(0, int(base_q + rng.randint(-2, 3)))
            q_w = max(0, int(base_q + rng.randint(-2, 3)))

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

            # Estimated queues with small noise for VANET
            q_est_n = max(0, q_n + (rng.choice([-1, 0, 1]) if rng.random() > 0.7 else 0))
            q_est_s = max(0, q_s + (rng.choice([-1, 0, 1]) if rng.random() > 0.7 else 0))
            q_est_e = max(0, q_e + (rng.choice([-1, 0, 1]) if rng.random() > 0.7 else 0))
            q_est_w = max(0, q_w + (rng.choice([-1, 0, 1]) if rng.random() > 0.7 else 0))

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
                    "alert_age": round(rng.uniform(0.1, 0.8), 2),
                })

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

            # Module 10: incident lifecycle events (start -> VANET detection -> end)
            if args.scenario == "incident" and args.live:
                inc_events = {
                    15: ("incident_start", "J1", "Incident on J0J1: closed (all lanes blocked) at 115 m for 30s"),
                    20: ("incident_detected", "J0", "TMC marked J0J1 as edge_blocked via RSU J0 (detection delay 5.0s)"),
                    45: ("incident_end", "J1", "Incident on J0J1 cleared; edge reopened"),
                }
                if t in inc_events and not (t == 20 and args.penetration < 0.3):
                    kind, tls_ev, detail = inc_events[t]
                    emit_json({
                        "type": "event",
                        "run_id": args.run_id,
                        "t": t,
                        "kind": kind,
                        "tls": tls_ev,
                        "detail": detail,
                    })

            n_vehicles = max(10, int(20 + 5 * rng.random() + (q_n + q_s + q_e + q_w)))
            n_vehicles = max(1, int(round(n_vehicles * args.demand)))  # demand setting (fake numbers)
            avg_wait = round(float(8.0 + (t / 10.0) % 6 + rng.random()), 1)
            throughput = round(float(950.0 + (t * 5.0) + rng.randint(0, 50)), 1)

            # Simulated communication stats
            step_sent = int(n_vehicles * args.penetration)
            step_lost = int(step_sent * args.loss)
            total_sent += step_sent
            total_lost += step_lost
            pdr = round(float((total_sent - total_lost) / max(total_sent, 1)), 2)

            spat = mock_spat(t, preempted=preempt_active)
            if commands is not None:
                amb_events: List[Any] = []
                for cmd in playback.split(commands.poll(), t):
                    if cmd.get("cmd") == "ambulance":
                        amb_events += ambulances.handle(cmd, t)
                    elif cmd.get("cmd") == "routes":
                        # No road graph in the mock: one direct "route", marked as mock data
                        emit_json({"type": "routes", "run_id": args.run_id, "t": t, "qid": str(cmd.get("qid", "")),
                                   "ok": True, "mock": True, "origin": cmd.get("from"), "destination": cmd.get("to"),
                                   "vclass": cmd.get("vclass"), "chosen": 0, "shorter_not_faster": False,
                                   "routes": [{"path": [cmd.get("from"), cmd.get("to")], "distance_m": 500.0,
                                               "time_free_s": 40.0, "time_now_s": 45.0, "signals": 1,
                                               "congestion": 1.12, "fastest": True, "shortest": True}],
                                   "astar": {"h_start_s": 30.0, "straight_m": 416.7, "v_max": 13.89, "g_goal_s": 45.0,
                                             "expansions": 3, "expansions_distance": 3, "signal_wait_s": 8.0}})
                    else:
                        traffic.handle_driver(cmd, t)
                amb_events += ambulances.step(t)
                ev_list += ambulances.ev_list(t)
                if args.live:
                    for kind, tls_ev, detail in amb_events:
                        emit_json({"type": "event", "run_id": args.run_id, "t": t, "kind": kind,
                                   "tls": tls_ev, "detail": detail})
            traffic.step(t, spat)
            if args.live:
                for line in traffic.pop_driver_output(args.run_id):
                    emit_json(line)
            tls_data: Dict[str, Any] = {}
            for j_idx, jid in enumerate(tls_ids):
                # Vary the fake queues per junction so charts show distinct series
                d = (j_idx * 2 + t // 7) % 4 - 1
                tls_data[jid] = {
                    "phase": phase_name,
                    "queues": {"N": max(0, q_n + d), "S": max(0, q_s - d), "E": max(0, q_e + j_idx), "W": q_w},
                    "queues_est": {"N": max(0, q_est_n + d), "S": max(0, q_est_s - d), "E": max(0, q_est_e + j_idx),
                                   "W": q_est_w},
                    "signal": signals,
                    "spat": spat,
                }

            if args.live:
                emit_json({
                    "type": "state",
                    "run_id": args.run_id,
                    "t": t,
                    "tls": tls_data,
                    "ev": ev_list,
                    "metrics": {
                        "avg_wait": avg_wait,
                        "n_vehicles": n_vehicles,
                        "throughput": throughput,
                    },
                    "comm": {
                        "pdr": pdr,
                        "latency_ms": round(float(15.0 + rng.uniform(0, 10)), 1),
                        "penetration": args.penetration,
                        "sent": total_sent,
                        "lost": total_lost,
                    },
                    "nav": {
                        "mode": args.nav,
                        "navigating": int(n_vehicles * args.penetration * args.nav_fraction) if args.nav != "none" else 0,
                        "avg_tt_nav": round(float(48.0 + rng.uniform(0, 5)), 1) if args.nav != "none" else 0.0,
                        "avg_tt_non": round(float(55.0 + rng.uniform(0, 5)), 1) if args.nav != "none" else 0.0,
                        "max_edge_load": 12,
                        "edge_load_cv": 0.35,
                    },
                    "eco": {
                        "eco_fraction": args.eco_fraction,
                        "eco_active": int(n_vehicles * args.penetration * args.eco_fraction),
                        "advised_now": int(n_vehicles * args.penetration * args.eco_fraction * 0.2),
                        "co2_g_per_km_eco": round(228.0 + rng.uniform(0, 6), 1) if args.eco_fraction > 0 and t > 20 else None,
                        "co2_g_per_km_non": round(252.0 + rng.uniform(0, 6), 1) if t > 20 else None,
                        "fuel_l_100km_eco": round(9.9 + rng.uniform(0, 0.3), 2) if args.eco_fraction > 0 and t > 20 else None,
                        "fuel_l_100km_non": round(10.9 + rng.uniform(0, 0.3), 2) if t > 20 else None,
                        "stops_eco": round(0.6 + rng.uniform(0, 0.1), 2) if args.eco_fraction > 0 and t > 20 else None,
                        "stops_non": round(1.5 + rng.uniform(0, 0.1), 2) if t > 20 else None,
                        "spat_pdr": 0.9 if args.eco_fraction > 0 else None,
                    },
                })
                emit_json(traffic.vehicles_message(args.run_id, t))
                drv_msg = traffic.driver_message(args.run_id, t)
                if drv_msg is not None:
                    emit_json(drv_msg)

            delay = playback.step_delay if playback is not None else args.step_delay
            if delay > 0:
                time.sleep(delay)
            if playback is not None and playback.paused:
                playback.wait_while_paused(commands, t, lambda: _STOP_REQUESTED)

    finally:
        pass

    if args.live and traffic.driver is not None:
        traffic.handle_driver({"cmd": "drive_end", "reason": "run_ended"}, t)
        for line in traffic.pop_driver_output(args.run_id):
            emit_json(line)

    result_metrics = {
        "avg_waiting_time": round(float(9.5 + rng.random() * 2), 2),
        "avg_time_loss": round(float(16.0 + rng.random() * 2), 2),
        "avg_travel_time": round(float(52.0 + rng.random() * 4), 2),
        "ev_travel_time": 22.5 if args.scenario in ("emergency", "incident") else 0.0,
        "max_queue": float(max_queue_recorded or 8.0),
        "throughput_veh_per_hr": round(float(1150.0 + rng.randint(0, 100)), 2),
        "seed": args.seed,
        "controller": args.controller,
        "scenario": args.scenario,
        "pdr": 0.98 if args.loss == 0.0 else round(1.0 - args.loss, 2),
        "avg_latency_ms": 16.4,
        "avg_hops": 1.0,
        "missed_preemptions": 0,
        "ev_alert_delivery_time": 0.45 if args.scenario in ("emergency", "incident") else None,
        "queue_estimate_mae": 0.65,
        "nav_mode": args.nav,
        "nav_avg_travel_time": 48.2 if args.nav != "none" else None,
        "nonnav_avg_travel_time": 54.8 if args.nav != "none" else None,
        "edge_load_cv": 0.35 if args.nav != "none" else None,
        "route_search_expansions": 14 if args.nav != "none" else None,
        "route_search_ms": 2.1 if args.nav != "none" else None,
        "reroute_count": 8 if args.nav != "none" else None,
        "collisions": 0,
    }

    if ambulances.trips:  # ambulances sent from the dashboard
        result_metrics.update(ambulances.metrics())

    # Eco-driving / emission fields
    eco_on = args.eco_fraction > 0
    result_metrics.update({
        "eco_fraction": args.eco_fraction,
        "co2_total_kg": 412.5, "fuel_total_l": 177.2, "co2_g_per_km": 238.0 if eco_on else 252.0,
        "fuel_l_per_100km": 10.3 if eco_on else 10.9, "stops_per_vehicle": 0.9 if eco_on else 1.5,
        "co2_g_per_km_eco": 228.0 if eco_on else None, "co2_g_per_km_non": 252.0 if eco_on else None,
        "fuel_l_per_100km_eco": 9.9 if eco_on else None, "fuel_l_per_100km_non": 10.9 if eco_on else None,
        "stops_per_vehicle_eco": 0.6 if eco_on else None, "stops_per_vehicle_non": 1.5 if eco_on else None,
        "travel_time_eco": 95.0 if eco_on else None, "travel_time_non": 99.0 if eco_on else None,
        "eco_advised_vehicles": 120 if eco_on else None, "eco_advice_share_slow": 0.2 if eco_on else None,
        "eco_advice_share_stop": 0.1 if eco_on else None, "spat_pdr": 0.9 if eco_on else None,
    })

    # Module 10 incident fields (null outside the incident scenario)
    is_inc = args.scenario == "incident"
    inc_detected = is_inc and args.penetration >= 0.3
    result_metrics.update({
        "incident_type": "closure" if is_inc else None,
        "incident_edge": "J0J1" if is_inc else None,
        "incident_detected": inc_detected if is_inc else None,
        "incident_detection_delay": 5.0 if inc_detected else None,
        "incident_detected_kind": "edge_blocked" if inc_detected else None,
        "incident_false_flags": 0 if is_inc else None,
        "incident_local_detours": 12 if is_inc else None,
        "incident_rerouted_vehicles": 18 if is_inc else None,
        "incident_nav_entries_after_detection": (0 if args.nav != "none" else None) if inc_detected else None,
        "incident_nonnav_entries_after_detection": 6 if inc_detected else None,
        "incident_added_delay": None,
    })

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
        print(json.dumps(result_metrics), flush=True)


if __name__ == "__main__":
    main()
