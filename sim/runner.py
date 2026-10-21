"""Simulation runner module.

Executes SUMO simulations with the selected controller, scenario, and random seed.
Provides a unified wrapper for traci / libsumo.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from sim.analytics.metrics import StepMetricsLogger, parse_tripinfo
from sim.controllers.fixed import FixedTimeController
from sim.scenarios.generate_routes import generate_routes, load_scenario_config

logger = logging.getLogger(__name__)


def get_sumo_backend(gui: bool = False) -> Tuple[Any, bool]:
    """Retrieve TraCI or libsumo backend behind a unified wrapper.

    Returns:
        (backend_module, is_libsumo_boolean)
    """
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
    sumo_api: Any,
) -> Any:
    """Factory to instantiate the appropriate controller."""
    ctrl_lower = controller_name.lower()
    if ctrl_lower == "fixed":
        return FixedTimeController(tls_id, params=params, sumo_api=sumo_api)
    elif ctrl_lower in ("adaptive", "adaptive_ev", "adaptive_ev_tuned"):
        # For modules where adaptive/preemption are implemented
        try:
            from sim.controllers.adaptive import AdaptiveController
            if ctrl_lower == "adaptive":
                return AdaptiveController(tls_id, params=params, sumo_api=sumo_api)
        except ImportError:
            pass

        try:
            from sim.controllers.preemption import PreemptionController
            if ctrl_lower in ("adaptive_ev", "adaptive_ev_tuned"):
                return PreemptionController(tls_id, params=params, sumo_api=sumo_api)
        except ImportError:
            pass

    raise ValueError(f"Controller '{controller_name}' is not supported or not implemented yet.")


def run_simulation(
    scenario: str,
    controller: str,
    seed: int = 1,
    params: Optional[Dict[str, Any]] = None,
    gui: bool = False,
    run_id: str = "run_default",
    max_steps: Optional[int] = None,
    metrics_csv_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run a single simulation run and return performance metrics.

    Args:
        scenario: Scenario name ('normal', 'congested', 'emergency', 'incident') or path.
        controller: Controller name ('fixed', 'adaptive', 'adaptive_ev', etc.).
        seed: Random seed for traffic generation.
        params: Optional controller/simulation parameter overrides.
        gui: If True, launch sumo-gui via TraCI; else headless.
        run_id: Identifier for the simulation run.
        max_steps: Maximum simulation seconds.
        metrics_csv_path: Custom path for per-step CSV log.

    Returns:
        Dictionary matching PROJECT_CONTEXT.md results shape.
    """
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

    try:
        if is_libsumo:
            sumo_api.start(sumo_cmd)
        else:
            sumo_api.start(sumo_cmd)

        tls_ids = list(sumo_api.trafficlight.getIDList())
        for tid in tls_ids:
            controllers_map[tid] = create_controller(
                controller_name=controller,
                tls_id=tid,
                params=ctrl_params,
                sumo_api=sumo_api,
            )

        # Simulation loop
        while t < sim_duration:
            t += 1.0

            # Step controllers
            for ctrl in controllers_map.values():
                ctrl.step(t)

            # Log metrics for each TLS
            active_vehs = sumo_api.vehicle.getIDList()
            n_vehicles = len(active_vehs)
            ev_present = any(
                sumo_api.vehicle.getTypeID(v) == "emergency" or "amb" in v.lower()
                for v in active_vehs
            )

            for tid, ctrl in controllers_map.items():
                queues = ctrl.get_queue_lengths()
                current_phase = ctrl.get_current_phase()
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

            sumo_api.simulationStep()

            # Early stop if all vehicles departed and arrived
            if t > 60.0 and sumo_api.simulation.getMinExpectedNumber() <= 0:
                break

    finally:
        logger_step.close()
        try:
            sumo_api.close()
        except Exception:
            pass

        # Clean up temporary routes file
        if routes_path.exists():
            try:
                routes_path.unlink()
            except Exception:
                pass

    # 5. Parse tripinfo metrics
    metrics = parse_tripinfo(
        xml_path=tripinfo_path,
        sim_duration=t,
        max_queue=logger_step.max_queue,
    )

    # Clean up tripinfo file
    if tripinfo_path.exists():
        try:
            tripinfo_path.unlink()
        except Exception:
            pass

    return {
        "avg_waiting_time": metrics["avg_waiting_time"],
        "avg_time_loss": metrics["avg_time_loss"],
        "avg_travel_time": metrics["avg_travel_time"],
        "ev_travel_time": metrics["ev_travel_time"],
        "max_queue": metrics["max_queue"],
        "throughput_veh_per_hr": metrics["throughput_veh_per_hr"],
        "seed": seed,
        "controller": controller,
        "scenario": scenario,
    }
