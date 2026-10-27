"""Simulation runner module.

Executes SUMO simulations with the selected controller, scenario, and random seed.
Provides a unified wrapper for traci / libsumo and streams live JSON protocol v2 lines.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import signal
import sys
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from sim.analytics.metrics import StepMetricsLogger, parse_tripinfo
from sim.controllers.fixed import FixedTimeController
from sim.scenarios.generate_routes import generate_routes, load_scenario_config
from sim.sensing import OracleStateProvider, StateProvider

logger = logging.getLogger(__name__)

_STOP_REQUESTED = False


def _handle_signal(signum: int, frame: Any) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    logger.warning("Received signal %d; requesting clean simulation termination.", signum)


def get_sumo_backend(gui: bool = False) -> Tuple[Any, bool]:
    """Retrieve TraCI or libsumo backend behind a unified wrapper."""
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        tools_path = os.path.join(sumo_home, "tools")
        if tools_path not in sys.path:
            sys.path.append(tools_path)

    if gui:
        import traci
        return traci, False

    try:
        import libsumo
        return libsumo, True
    except Exception:
        import traci
        return traci, False


def create_controller(
    controller_name: str,
    tls_id: str,
    params: Dict[str, Any],
    provider: StateProvider,
    sumo_api: Any = None,
) -> Any:
    """Factory to instantiate the appropriate controller with injected StateProvider."""
    ctrl_lower = controller_name.lower()
    if ctrl_lower == "fixed":
        return FixedTimeController(tls_id, params=params, provider=provider, sumo_api=sumo_api)
    elif ctrl_lower in ("adaptive", "adaptive_ev", "adaptive_ev_tuned"):
        try:
            from sim.controllers.adaptive import AdaptiveController
            if ctrl_lower == "adaptive":
                return AdaptiveController(tls_id, params=params, provider=provider, sumo_api=sumo_api)
        except ImportError:
            pass

        try:
            from sim.controllers.preemption import PreemptionController
            if ctrl_lower in ("adaptive_ev", "adaptive_ev_tuned"):
                return PreemptionController(tls_id, params=params, provider=provider, sumo_api=sumo_api)
        except ImportError:
            pass

    elif ctrl_lower in ("adaptive_vanet", "adaptive_ev_vanet", "adaptive_ev_vanet_tuned"):
        raise NotImplementedError(f"VANET controller '{controller_name}' is scheduled for Module 6/8.")

    raise ValueError(f"Controller '{controller_name}' is not supported or not implemented yet.")


def emit_json_line(data: Dict[str, Any]) -> None:
    """Emit a single line of JSON to stdout and flush immediately."""
    print(json.dumps(data), flush=True)


def run_simulation(
    scenario: str,
    controller: str,
    seed: int = 1,
    params: Optional[Dict[str, Any]] = None,
    gui: bool = False,
    run_id: str = "r1",
    live: bool = False,
    emit_interval: int = 1,
    max_steps: Optional[int] = None,
    metrics_csv_path: Optional[Path] = None,
    nav: str = "none",
    penetration: float = 1.0,
    nav_fraction: float = 0.3,
    loss: float = 0.0,
    range_m: float = 300.0,
    relay: str = "off",
    aco_params: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a single simulation run and return performance metrics."""
    global _STOP_REQUESTED
    _STOP_REQUESTED = False

    # Check for features not implemented yet
    if nav != "none":
        raise NotImplementedError(f"Navigation mode '{nav}' is not implemented yet (scheduled for Module 9).")
    if relay == "on":
        raise NotImplementedError("VANET V2V relaying is not implemented yet (scheduled for Module 8).")

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, AttributeError):
        pass

    project_root = Path(__file__).resolve().parent.parent

    # 1. Load config
    config_file = project_root / "sim" / "config.yaml"
    base_config: Dict[str, Any] = {}
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            base_config = yaml.safe_load(f) or {}

    ctrl_params = base_config.get("controllers", {}).get(controller, {}).copy()
    if params:
        ctrl_params.update(params)

    # 2. Resolve scenario config and generate routes
    scenario_path = project_root / "sim" / "scenarios" / f"{scenario}.yaml"
    if not scenario_path.exists():
        scenario_path = Path(scenario)
    if not scenario_path.exists():
        raise FileNotFoundError(f"Scenario file not found: {scenario}")

    scenario_cfg = load_scenario_config(scenario_path)
    sim_duration = float(
        max_steps
        if max_steps is not None
        else scenario_cfg.get("simulation", {}).get("duration", 1000.0)
    )

    routes_path = project_root / "sim" / "sumo" / f"routes_{run_id}.rou.xml"
    generate_routes(scenario_cfg, routes_path, override_seed=seed)

    # 3. Setup outputs
    results_dir = project_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    tripinfo_path = results_dir / f"tripinfo_{run_id}.xml"
    if metrics_csv_path is None:
        metrics_csv_path = results_dir / f"metrics_{scenario}_{controller}_s{seed}.csv"

    logger_step = StepMetricsLogger(metrics_csv_path)

    # 4. Initialize SUMO backend
    sumo_api, is_libsumo = get_sumo_backend(gui=gui)
    import sumolib

    sumo_binary = sumolib.checkBinary("sumo-gui" if gui else "sumo")
    net_path = project_root / "sim" / "sumo" / "net.xml"

    sumo_cmd = [
        sumo_binary,
        "-n",
        str(net_path),
        "-r",
        str(routes_path),
        "--tripinfo-output",
        str(tripinfo_path),
        "--step-length",
        "1.0",
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
        "--time-to-teleport",
        "-1",
    ]

    controllers_map: Dict[str, Any] = {}
    t = 0.0
    arrived_count = 0
    was_interrupted = False

    if live:
        emit_json_line({
            "type": "status",
            "run_id": run_id,
            "status": "started",
            "message": f"Simulation started for scenario '{scenario}' with controller '{controller}'",
        })

    try:
        sumo_api.start(sumo_cmd)

        # Initialize StateProvider (OracleStateProvider for current tier)
        provider = OracleStateProvider(sumo_api)

        tls_ids = list(sumo_api.trafficlight.getIDList())
        for tid in tls_ids:
            controllers_map[tid] = create_controller(
                controller_name=controller,
                tls_id=tid,
                params=ctrl_params,
                provider=provider,
                sumo_api=sumo_api,
            )

        while t < sim_duration:
            if _STOP_REQUESTED:
                was_interrupted = True
                break

            t += 1.0
            provider.update(t)

            # Step controllers
            for ctrl in controllers_map.values():
                ctrl.step(t)

            active_vehs = list(sumo_api.vehicle.getIDList())
            n_vehicles = len(active_vehs)

            # Retrieve EV info through provider
            ev_list = provider.get_ev_info("J0")
            ev_present = len(ev_list) > 0

            # Check newly arrived vehicles
            try:
                arrived_count += sumo_api.simulation.getArrivedNumber()
            except Exception:
                pass

            # Step metrics logging and live state payload
            tls_state_dict: Dict[str, Any] = {}
            for tid, ctrl in controllers_map.items():
                queues = ctrl.get_queue_lengths()
                current_phase = ctrl.get_current_phase()
                phase_name = getattr(ctrl, "get_phase_name", lambda: f"PHASE_{current_phase}")()
                approach_signals = getattr(ctrl, "get_approach_signals", lambda: {"N": "r", "S": "r", "E": "r", "W": "r"})()

                logger_step.log_step(
                    sim_time=t,
                    tls_id=tid,
                    phase=current_phase,
                    queue_n=queues.get("N", 0),
                    queue_s=queues.get("S", 0),
                    queue_e=queues.get("E", 0),
                    queue_w=queues.get("W", 0),
                    n_vehicles=n_vehicles,
                    ev_present=ev_present,
                )

                tls_state_dict[tid] = {
                    "phase": phase_name,
                    "queues": queues,
                    "queues_est": queues,  # Equal to true queue in oracle mode
                    "signal": approach_signals,
                }

            # Emit live state if requested (protocol v2 schema)
            if live and (int(t) % emit_interval == 0):
                active_waits = [sumo_api.vehicle.getWaitingTime(v) for v in active_vehs]
                avg_wait = round(float(sum(active_waits) / len(active_waits)), 1) if active_waits else 0.0
                hours = max(t / 3600.0, 1e-4)
                throughput_rate = round(arrived_count / hours, 1)

                emit_json_line({
                    "type": "state",
                    "run_id": run_id,
                    "t": int(t),
                    "tls": tls_state_dict,
                    "ev": ev_list,
                    "metrics": {
                        "avg_wait": avg_wait,
                        "n_vehicles": n_vehicles,
                        "throughput": throughput_rate,
                    },
                    "comm": {
                        "pdr": 1.0,
                        "latency_ms": 0.0,
                        "penetration": penetration,
                        "sent": 0,
                        "lost": 0,
                    },
                    "nav": {
                        "mode": nav,
                        "navigating": 0,
                        "avg_tt_nav": 0.0,
                        "avg_tt_non": 0.0,
                        "max_edge_load": 0,
                        "edge_load_cv": 0.0,
                    },
                })

            sumo_api.simulationStep()

            if t > 60.0 and sumo_api.simulation.getMinExpectedNumber() <= 0:
                break

    finally:
        logger_step.close()
        try:
            sumo_api.close()
        except Exception:
            pass

        if routes_path.exists():
            try:
                routes_path.unlink()
            except Exception:
                pass

    metrics = parse_tripinfo(
        xml_path=tripinfo_path,
        sim_duration=t,
        max_queue=logger_step.max_queue,
    )

    if tripinfo_path.exists():
        try:
            tripinfo_path.unlink()
        except Exception:
            pass

    # Extended result dictionary matching PROJECT_CONTEXT v2
    result_payload = {
        "avg_waiting_time": metrics["avg_waiting_time"],
        "avg_time_loss": metrics["avg_time_loss"],
        "avg_travel_time": metrics["avg_travel_time"],
        "ev_travel_time": metrics["ev_travel_time"],
        "max_queue": metrics["max_queue"],
        "throughput_veh_per_hr": metrics["throughput_veh_per_hr"],
        "seed": seed,
        "controller": controller,
        "scenario": scenario,
        # VANET metric fields (placeholders for Module 4b)
        "pdr": None,
        "avg_latency_ms": None,
        "avg_hops": None,
        "missed_preemptions": None,
        "ev_alert_delivery_time": None,
        "queue_estimate_mae": None,
        # Navigation metric fields (placeholders for Module 4b)
        "nav_mode": nav,
        "nav_avg_travel_time": None,
        "nonnav_avg_travel_time": None,
        "edge_load_cv": None,
        "route_search_expansions": None,
        "route_search_ms": None,
        "reroute_count": None,
    }

    if live:
        if was_interrupted:
            emit_json_line({
                "type": "status",
                "run_id": run_id,
                "status": "stopped",
                "message": "Simulation interrupted by signal",
            })
        else:
            emit_json_line({
                "type": "status",
                "run_id": run_id,
                "status": "finished",
                "message": "Simulation completed successfully",
            })
        emit_json_line({
            "type": "result",
            "run_id": run_id,
            "metrics": result_payload,
            "seed": seed,
            "controller": controller,
            "scenario": scenario,
        })

    return result_payload
