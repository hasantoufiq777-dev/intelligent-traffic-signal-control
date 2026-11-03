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
from sim.vanet.nodes import internal_lane_junction, is_vehicle_equipped

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
    elif ctrl_lower in ("adaptive", "adaptive_vanet", "adaptive_ev", "adaptive_ev_tuned", "adaptive_ev_vanet", "adaptive_ev_vanet_tuned"):
        try:
            from sim.controllers.adaptive import AdaptiveController
            if ctrl_lower in ("adaptive", "adaptive_vanet"):
                return AdaptiveController(tls_id, params=params, provider=provider, sumo_api=sumo_api)
        except ImportError:
            pass

        try:
            from sim.controllers.preemption import PreemptionController
            if ctrl_lower in ("adaptive_ev", "adaptive_ev_tuned", "adaptive_ev_vanet", "adaptive_ev_vanet_tuned"):
                return PreemptionController(tls_id, params=params, provider=provider, sumo_api=sumo_api)
        except ImportError:
            pass

    raise ValueError(f"Controller '{controller_name}' is not supported or not implemented yet.")


def _reroute_evs_around(
    sumo_api: Any,
    graph: Any,
    vehicle_ids: List[str],
    closed_edges: set,
    speed_map: Dict[str, float],
    rerouted: set,
) -> None:
    """Emergency vehicles (always equipped) avoid edges the TMC reports as closed.

    Uses A* on the TMC map. The EV's new route then flows into its EV_ALERTs, so
    preemption follows the new route.
    """
    from sim.navigation.astar import astar_search

    for vid in vehicle_ids:
        try:
            if sumo_api.vehicle.getTypeID(vid) != "emergency":
                continue
            curr = str(sumo_api.vehicle.getRoadID(vid))
            if curr.startswith(":") or curr in closed_edges or curr not in graph.edges:
                continue
            route = list(sumo_api.vehicle.getRoute(vid))
            idx = int(sumo_api.vehicle.getRouteIndex(vid))
            if not any(e in closed_edges for e in route[idx + 1:]):
                continue
            path, cost, _exp = astar_search(
                graph=graph, start_edge=curr, goal_edge=route[-1],
                speed_map=speed_map, closed_edges=closed_edges,
            )
            if path and cost < float("inf"):
                sumo_api.vehicle.setRoute(vid, path)
                rerouted.add(vid)
                logger.info("EV %s rerouted around closed edge(s) %s", vid, sorted(closed_edges))
        except Exception as e:
            logger.debug("EV incident reroute failed for %s: %s", vid, e)


# SUMO-GUI colours matching the dashboard legend (RGBA)
GUI_COLORS = {
    "plain": (128, 128, 128, 255),     # car without radio
    "equipped": (42, 120, 214, 255),   # connected car (OBU)
    "eco": (27, 175, 122, 255),        # eco car following GLOSA advice
    "blocker": (40, 40, 40, 255),      # incident blocker
}


def _color_vehicle_for_gui(sumo_api: Any, vid: str, vtype: str, penetration: float, seed: int,
                           eco_fraction: float) -> None:
    """Colour a new vehicle in SUMO-GUI like the dashboard map (ambulances keep their red vType colour)."""
    if vtype == "emergency":
        return
    from sim.vanet.glosa import is_eco_vehicle

    if vid.startswith("incblk_"):
        kind = "blocker"
    elif is_eco_vehicle(vid, vtype, eco_fraction, penetration, seed):
        kind = "eco"
    elif is_vehicle_equipped(vid, vtype=vtype, penetration=penetration, seed=seed):
        kind = "equipped"
    else:
        kind = "plain"
    try:
        sumo_api.vehicle.setColor(vid, GUI_COLORS[kind])
    except Exception:
        pass


def _resolve_path(project_root: Path, path: Any) -> Path:
    p = Path(str(path))
    return p if p.is_absolute() else project_root / p


def _load_tuned_file(project_root: Path, path: Any, target: str) -> Dict[str, Any]:
    """Load a GA output YAML (Module 11) or fail with a message saying how to create it."""
    p = _resolve_path(project_root, path)
    if not p.exists():
        raise FileNotFoundError(
            f"Tuned parameter file not found: {p}. "
            f"Run: python sim/optimizer/ga_tuner.py --target {target}"
        )
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.pop("meta", None)
    return data


def protocol_event(raw: Dict[str, Any], run_id: str, t: float) -> Dict[str, Any]:
    """Convert an internal event (VANET alert, reroute) to the live protocol event shape:
    {"type","run_id","t","kind","tls","detail"}. Internal events name their kind under "event"."""
    kind = raw.get("kind") or raw.get("event") or "unknown"
    tls = raw.get("tls") or raw.get("tls_id") or raw.get("target") or ""
    detail = raw.get("detail")
    if not detail:
        skip = {"type", "event", "kind", "tls", "tls_id", "time", "run_id", "t"}
        detail = ", ".join(f"{k}={v}" for k, v in raw.items() if k not in skip)
    return {"type": "event", "run_id": run_id, "t": int(t), "kind": kind, "tls": str(tls), "detail": str(detail)}


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
    output_dir: Optional[Path] = None,
    step_delay: float = 0.0,
    eco_fraction: float = 0.0,
) -> Dict[str, Any]:
    """Run a single simulation run and return performance metrics.

    output_dir: where tripinfo and the preemption-events CSV go (default results/).
    Parallel callers such as the GA pass a private directory so runs never share files.
    """
    global _STOP_REQUESTED
    _STOP_REQUESTED = False

    # Global signal handlers for graceful interrupt
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

    ctrl_section = base_config.get("controllers", {}).get(controller)
    if ctrl_section is None:
        if controller == "adaptive_vanet":
            ctrl_section = base_config.get("controllers", {}).get("adaptive", {})
        elif controller in ("adaptive_ev", "adaptive_ev_tuned", "adaptive_ev_vanet", "adaptive_ev_vanet_tuned"):
            ctrl_section = base_config.get("controllers", {}).get("adaptive", {}).copy()
            ctrl_section.update(base_config.get("controllers", {}).get("preemption", {}))
    elif controller in ("adaptive_ev", "adaptive_ev_tuned", "adaptive_ev_vanet", "adaptive_ev_vanet_tuned"):
        ctrl_section = dict(ctrl_section)
        ctrl_section.update(base_config.get("controllers", {}).get("preemption", {}))

    ctrl_params = dict(ctrl_section or {})

    # Module 11: *_tuned controllers use the GA output (defaults < tuned file < explicit params)
    tuned_vanet: Dict[str, Any] = {}
    if controller.endswith("_tuned"):
        tuned_file = (params or {}).get("tuned_params_file") or base_config.get("ga", {}).get(
            "signals_params_file", "results/ga/best_params_signals.yaml"
        )
        tuned = _load_tuned_file(project_root, tuned_file, "signals")
        tuned_vanet = dict(tuned.pop("vanet", {}) or {})
        ctrl_params.update(tuned)

    if nav == "aco_tuned" and not aco_params:
        aco_file = base_config.get("ga", {}).get("aco_params_file", "results/ga/best_params_aco.yaml")
        _load_tuned_file(project_root, aco_file, "aco")  # clear error if the GA has not run
        aco_params = str(_resolve_path(project_root, aco_file))

    if params:
        ctrl_params.update(params)

    # 2. Resolve scenario config and generate routes
    scenario_path = project_root / "sim" / "scenarios" / f"{scenario}.yaml"
    if not scenario_path.exists():
        scenario_path = Path(scenario)
    if not scenario_path.exists():
        raise FileNotFoundError(f"Scenario file not found: {scenario}")

    scenario_cfg = load_scenario_config(scenario_path)
    scenario_name = scenario_path.stem
    sim_duration = float(
        max_steps
        if max_steps is not None
        else scenario_cfg.get("simulation", {}).get("duration", 1000.0)
    )

    network = params.get("network") if params and "network" in params else scenario_cfg.get("network", "single")

    # Incident config (Module 10); params["incident"] overrides fields, e.g. {"enabled": False}
    incident_cfg: Dict[str, Any] = dict(scenario_cfg.get("incident") or {})
    if params and isinstance(params.get("incident"), dict):
        incident_cfg.update(params["incident"])
    incident_enabled = bool(incident_cfg.get("enabled", False))
    # The process id keeps parallel runs that share a run_id (for example the default "r1") from
    # overwriting each other's route and tripinfo files.
    file_tag = f"{run_id}_{os.getpid()}"
    routes_path = project_root / "sim" / "sumo" / f"routes_{file_tag}.rou.xml"
    generate_routes(scenario_cfg, routes_path, override_seed=seed, network_override=network)

    # 3. Setup outputs
    results_dir = Path(output_dir) if output_dir is not None else project_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    tripinfo_path = results_dir / f"tripinfo_{file_tag}.xml"
    if metrics_csv_path is None:
        metrics_csv_path = results_dir / f"metrics_{scenario_name}_{controller}_s{seed}.csv"

    logger_step = StepMetricsLogger(metrics_csv_path)

    # 4. Initialize SUMO backend
    sumo_api, is_libsumo = get_sumo_backend(gui=gui)
    import sumolib

    sumo_binary = sumolib.checkBinary("sumo-gui" if gui else "sumo")
    if network == "grid2x2":
        net_path = project_root / "sim" / "sumo" / "grid2x2.net.xml"
    else:
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
        # Fuel and CO2 per trip in tripinfo (HBEFA4 model); does not change vehicle behaviour
        "--device.emissions.probability",
        "1",
    ]

    decorations_file = project_root / "sim" / "sumo" / "decorations.add.xml"
    if network == "single" and decorations_file.exists():
        sumo_cmd.extend(["-a", str(decorations_file)])

    settings_name = "gui-settings-grid.cfg.xml" if network == "grid2x2" else "gui-settings.cfg.xml"
    gui_settings_file = project_root / "sim" / "sumo" / settings_name
    if gui:
        if gui_settings_file.exists():
            sumo_cmd.extend(["--gui-settings-file", str(gui_settings_file)])
        # Start without pressing Play and close the window when the run ends (so live playlists continue)
        sumo_cmd.extend(["--start", "--quit-on-end"])

    controllers_map: Dict[str, Any] = {}
    t = 0.0
    arrived_count = 0
    was_interrupted = False
    is_vanet_controller = (controller in ("adaptive_vanet", "adaptive_ev_vanet", "adaptive_ev_vanet_tuned"))
    vanet_net: Optional[Any] = None
    vtype_cache: Dict[str, str] = {}
    ev_crossings: set = set()  # (ev id, signalised junction) pairs the EV drove through
    eco_mgr: Optional[Any] = None
    queue_errors: List[float] = []

    if live:
        emit_json_line({
            "type": "status",
            "run_id": run_id,
            "status": "started",
            "message": f"Simulation started for scenario '{scenario}' with controller '{controller}'",
        })

    try:
        sumo_api.start(sumo_cmd)
        tls_ids = list(sumo_api.trafficlight.getIDList())

        vanet_cfg = base_config.get("vanet", {}).copy()
        vanet_cfg.update(tuned_vanet)
        if params and "vanet" in params:
            vanet_cfg.update(params["vanet"])
        # Incident detection runs through the VANET. "auto" = only when the scenario has an incident.
        det_mode = str(vanet_cfg.get("incident_detection", "auto")).lower()
        incident_detection_on = det_mode == "on" or (det_mode == "auto" and incident_enabled)

        eco_on = float(eco_fraction or 0.0) > 0.0
        if is_vanet_controller or incident_detection_on or eco_on:
            from sim.vanet.network import VanetNetwork
            from sim.vanet.sensing_vanet import VanetStateProvider

            relay_flag = True if relay == "on" else (False if relay == "off" else None)
            vanet_net = VanetNetwork(
                sumo_api=sumo_api,
                tls_ids=tls_ids,
                config=vanet_cfg,
                seed=seed,
                penetration_override=penetration,
                loss_override=loss,
                range_override=range_m,
                relay_override=relay_flag,
                incident_detection=incident_detection_on,
            )

        if is_vanet_controller and vanet_net is not None:
            provider: StateProvider = VanetStateProvider(vanet_net)
            oracle_provider = OracleStateProvider(sumo_api)
        else:
            provider = OracleStateProvider(sumo_api)
            oracle_provider = provider

        # Incident ground truth (Module 10). Oracle sensing may see the truth (ideal baseline);
        # VANET sensing and navigation only see the TMC incident map.
        incident_mgr: Optional[Any] = None
        if incident_enabled:
            from sim.scenarios.incident import IncidentManager

            incident_mgr = IncidentManager(sumo_api, incident_cfg)
            oracle_provider.set_incident_source(incident_mgr.get_true_incidents)
        incident_speed = float(vanet_cfg.get("incident_speed", 1.0))
        ev_informed_reroutes: set = set()

        for tid in tls_ids:
            controllers_map[tid] = create_controller(
                controller_name=controller,
                tls_id=tid,
                params=ctrl_params,
                provider=provider,
                sumo_api=sumo_api,
            )

        # Initialize Navigation Engine (Module 9)
        from sim.navigation.graph import EdgeGraph
        from sim.navigation.navigator import VehicleNavigator

        edge_graph = EdgeGraph.from_net_file(net_path)
        nav_cfg = base_config.get("navigation", {}).copy()
        if params and "navigation" in params:
            nav_cfg.update(params["navigation"])
        edge_graph.junction_delay_s = float(nav_cfg.get("junction_delay_s", 0.0))
        aco_cfg = base_config.get("aco", {}).copy()
        if aco_params:
            # Missing file = clear error; the "meta" block written by the GA is not an ACO parameter
            aco_cfg.update(_load_tuned_file(project_root, aco_params, "aco"))
        elif params and "aco" in params:
            aco_cfg.update(params["aco"])

        nav_frac = float(nav_fraction if nav_fraction is not None else nav_cfg.get("nav_fraction", 0.3))
        navigator = VehicleNavigator(
            sumo_api=sumo_api,
            graph=edge_graph,
            mode=nav,
            nav_fraction=nav_frac,
            nav_interval=float(nav_cfg.get("nav_interval", 30.0)),
            min_reroute_gap=float(nav_cfg.get("min_reroute_gap", 15.0)),
            hysteresis=float(nav_cfg.get("hysteresis", 0.05)),
            aco_config=aco_cfg,
            heuristic=str(nav_cfg.get("heuristic", "euclid")),
            seed=seed,
            speed_smoothing_s=float(nav_cfg.get("speed_smoothing_s", 0.0)),
        )

        # Eco-driving (GLOSA): RSUs broadcast SPaT, eco cars follow speed advice
        if eco_on and vanet_net is not None:
            from sim.vanet.glosa import EcoDrivingManager

            eco_cfg = dict(base_config.get("eco", {}) or {})
            if params and isinstance(params.get("eco"), dict):
                eco_cfg.update(params["eco"])
            eco_mgr = EcoDrivingManager(
                sumo_api=sumo_api,
                vanet_net=vanet_net,
                tls_ids=tls_ids,
                spat_source=lambda tid: controllers_map[tid].get_spat(),
                config=eco_cfg,
                eco_fraction=float(eco_fraction),
                penetration=penetration,
                seed=seed,
                track_live_emissions=live,
            )

        # Live map data: road geometry once, then per-vehicle rows every emitted second
        vehicle_tracker: Optional[Any] = None
        if live:
            from sim.live_vehicles import LiveVehicleTracker, build_network_message

            emit_json_line(build_network_message(net_path, tls_ids, run_id))
            vehicle_tracker = LiveVehicleTracker(sumo_api, tls_ids, penetration=penetration, seed=seed,
                                                 eco_mgr=eco_mgr, navigator=navigator)

        while t < sim_duration:
            if _STOP_REQUESTED:
                was_interrupted = True
                break

            t += 1.0
            if incident_mgr is not None:
                incident_mgr.step(t)
            provider.update(t)
            if is_vanet_controller:
                oracle_provider.update(t)
            elif vanet_net is not None:
                # VANET used only for incident detection / eco-driving (oracle-sensed controller);
                # its EV-alert events are not used by this controller, so drop them.
                vanet_net.step(t)
                vanet_net.pop_events()

            if incident_mgr is not None:
                if vanet_net is not None:
                    incident_mgr.observe_tmc_changes(vanet_net.pop_incident_changes(), t)
                inc_events = incident_mgr.pop_events()
                if live:
                    for ev in inc_events:
                        emit_json_line({
                            "type": "event",
                            "run_id": run_id,
                            "t": int(t),
                            "kind": ev["kind"],
                            "tls": ev.get("tls", ""),
                            "detail": ev["detail"],
                        })

            # Step controllers
            for tid, ctrl in controllers_map.items():
                ctrl.step(t)
                if hasattr(ctrl, "pop_events"):
                    events = ctrl.pop_events()
                    if live:
                        for ev in events:
                            emit_json_line({
                                "type": "event",
                                "run_id": run_id,
                                "t": int(t),
                                "kind": ev["kind"],
                                "tls": ev.get("tls", tid),
                                "detail": ev["detail"],
                            })

            active_vehs = list(sumo_api.vehicle.getIDList())
            n_vehicles = len(active_vehs)

            # Emergency vehicles (type looked up once per vehicle) and the junctions they cross
            for vid in active_vehs:
                if vid not in vtype_cache:
                    try:
                        vtype_cache[vid] = sumo_api.vehicle.getTypeID(vid)
                    except Exception:
                        vtype_cache[vid] = "car"
                    if gui:
                        _color_vehicle_for_gui(sumo_api, vid, vtype_cache[vid], penetration, seed,
                                               float(eco_fraction or 0.0))
            active_evs = [v for v in active_vehs if vtype_cache.get(v) == "emergency"]
            for vid in active_evs:
                try:
                    j = internal_lane_junction(str(sumo_api.vehicle.getRoadID(vid)))
                except Exception:
                    j = None
                if j in controllers_map:
                    ev_crossings.add((vid, j))

            # Live EV list through the provider: each EV's nearest upcoming junction
            ev_list: List[Dict[str, Any]] = []
            if active_evs:
                nearest: Dict[str, Dict[str, Any]] = {}
                for tid in controllers_map:
                    for rec in provider.get_ev_info(tid):
                        if rec["id"] not in nearest or rec.get("dist", 1e9) < nearest[rec["id"]].get("dist", 1e9):
                            nearest[rec["id"]] = rec
                ev_list = list(nearest.values())
            ev_present = len(active_evs) > 0

            # Check newly arrived vehicles
            try:
                arrived_count += sumo_api.simulation.getArrivedNumber()
            except Exception:
                pass

            # Step metrics logging and live state payload
            tls_state_dict: Dict[str, Any] = {}
            for tid, ctrl in controllers_map.items():
                current_phase = ctrl.get_current_phase()
                phase_name = getattr(ctrl, "get_phase_name", lambda: f"PHASE_{current_phase}")()
                approach_signals = getattr(ctrl, "get_approach_signals", lambda: {"N": "r", "S": "r", "E": "r", "W": "r"})()

                true_q = oracle_provider.get_queues(tid)
                if is_vanet_controller:
                    est_q = provider.get_queues(tid)
                    for d in ("N", "S", "E", "W"):
                        queue_errors.append(abs(true_q.get(d, 0) - est_q.get(d, 0)))
                else:
                    est_q = true_q

                logger_step.log_step(
                    sim_time=t,
                    tls_id=tid,
                    phase=current_phase,
                    queue_n=true_q.get("N", 0),
                    queue_s=true_q.get("S", 0),
                    queue_e=true_q.get("E", 0),
                    queue_w=true_q.get("W", 0),
                    n_vehicles=n_vehicles,
                    ev_present=ev_present,
                    queue_est_n=est_q.get("N", 0),
                    queue_est_s=est_q.get("S", 0),
                    queue_est_e=est_q.get("E", 0),
                    queue_est_w=est_q.get("W", 0),
                )

                tls_state_dict[tid] = {
                    "phase": phase_name,
                    "queues": true_q,
                    "queues_est": est_q,
                    "signal": approach_signals,
                }
                if live and hasattr(ctrl, "get_spat"):
                    # Signal Phase and Timing as broadcast by the RSU (countdowns in the dashboard)
                    tls_state_dict[tid]["spat"] = ctrl.get_spat()

            # Step Navigation (Module 9)
            speed_map = provider.get_edge_speeds()
            closed_edges: Optional[set] = None
            tmc_incidents = vanet_net.get_incidents() if (vanet_net is not None and incident_detection_on) else {}
            if tmc_incidents:
                # Module 10: TMC incident map -> edge costs (closure = infinite, lane block = very slow)
                speed_map = dict(speed_map)
                closed_edges = {e for e, k in tmc_incidents.items() if k == "edge_blocked"}
                for e, k in tmc_incidents.items():
                    if k != "edge_blocked":
                        speed_map[e] = incident_speed
                if closed_edges:
                    _reroute_evs_around(sumo_api, edge_graph, active_vehs, closed_edges, speed_map, ev_informed_reroutes)
            navigator.step(
                sim_time=t,
                speed_map=speed_map,
                closed_edges=closed_edges or None,
                incident_edges=set(tmc_incidents) or None,
                equipped_check_fn=lambda vid: is_vehicle_equipped(vid, penetration=penetration, seed=seed),
            )
            if live:
                for n_ev in navigator.pop_events():
                    emit_json_line(protocol_event(n_ev, run_id, t))

            # Eco-driving step: after the controllers decided, so SPaT reflects this second
            if eco_mgr is not None:
                eco_mgr.step(t)

            # Emit live state if requested (protocol v2 schema)
            if live and (int(t) % emit_interval == 0):
                active_waits = [
                    sumo_api.vehicle.getWaitingTime(v) for v in active_vehs if not v.startswith("incblk_")
                ]
                avg_wait = round(float(sum(active_waits) / len(active_waits)), 1) if active_waits else 0.0
                hours = max(t / 3600.0, 1e-4)
                throughput_rate = round(arrived_count / hours, 1)

                if is_vanet_controller and vanet_net is not None:
                    comm_dict = vanet_net.get_comm_stats()
                else:
                    comm_dict = {
                        "pdr": 1.0,
                        "latency_ms": 0.0,
                        "penetration": penetration,
                        "sent": 0,
                        "lost": 0,
                    }

                if is_vanet_controller and vanet_net is not None:
                    for a_ev in vanet_net.pop_events():
                        emit_json_line(protocol_event(a_ev, run_id, t))

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
                    "comm": comm_dict,
                    "nav": navigator.get_live_nav_state(),
                    "eco": eco_mgr.get_live_block() if eco_mgr is not None else {
                        "eco_fraction": 0.0, "eco_active": 0, "advised_now": 0,
                        "co2_g_per_km_eco": None, "co2_g_per_km_non": None,
                        "fuel_l_100km_eco": None, "fuel_l_100km_non": None,
                        "stops_eco": None, "stops_non": None, "spat_pdr": None,
                    },
                })
                if vehicle_tracker is not None:
                    emit_json_line(vehicle_tracker.build(active_vehs, run_id, t))

            if vehicle_tracker is not None:
                vehicle_tracker.step(active_vehs)

            sumo_api.simulationStep()
            if step_delay > 0.0:
                # Live demo pacing (wall-clock seconds per simulated second)
                import time as _time

                _time.sleep(step_delay)

            if t > 60.0 and sumo_api.simulation.getMinExpectedNumber() <= 0:
                break

    except Exception as e:
        err_msg = str(e).lower()
        closed = ("connection closed" in err_msg or "closed by sumo" in err_msg
                  or "fataltracierror" in type(e).__name__.lower())
        # Only a GUI window can be closed by the user. Headless, a closed connection means SUMO
        # quit on an error (for example a bad route file), so the error is raised.
        if gui and closed:
            logger.info("SUMO GUI window closed by user or connection closed.")
            was_interrupted = True
        else:
            raise
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

    # Fuel and CO2 (all runs), split eco / non-eco cars when eco-driving is on
    from sim.analytics.metrics import parse_tripinfo_emissions
    from sim.vanet.glosa import is_eco_vehicle

    def _eco_group(vid: str, vtype: str) -> Optional[str]:
        if vtype == "emergency":
            return None
        return "eco" if is_eco_vehicle(vid, vtype, float(eco_fraction or 0.0), penetration, seed) else "non"

    emission_metrics = parse_tripinfo_emissions(
        tripinfo_path, group_fn=_eco_group if float(eco_fraction or 0.0) > 0.0 else None
    )

    if tripinfo_path.exists():
        try:
            tripinfo_path.unlink()
        except Exception:
            pass

    # Extended result dictionary matching PROJECT_CONTEXT v2
    result_payload: Dict[str, Any] = {
        "avg_waiting_time": metrics["avg_waiting_time"],
        "avg_time_loss": metrics["avg_time_loss"],
        "avg_travel_time": metrics["avg_travel_time"],
        "ev_travel_time": metrics["ev_travel_time"],
        "max_queue": metrics["max_queue"],
        "throughput_veh_per_hr": metrics["throughput_veh_per_hr"],
        "seed": seed,
        "controller": controller,
        "scenario": scenario_name,
        # VANET metric fields
        "pdr": None,
        "avg_latency_ms": None,
        "avg_hops": None,
        "missed_preemptions": None,
        "ev_alert_delivery_time": None,
        "queue_estimate_mae": None,
        # Navigation metric fields (Module 9)
        "nav_mode": nav,
        "nav_avg_travel_time": None,
        "nonnav_avg_travel_time": None,
        "edge_load_cv": None,
        "max_edge_load": None,
        "route_search_expansions": None,
        "route_search_ms": None,
        "reroute_count": None,
    }

    nav_metrics = navigator.get_nav_metrics()
    result_payload.update(nav_metrics)

    # Eco-driving and emission fields (eco-specific ones stay null when eco-driving is off)
    result_payload["eco_fraction"] = float(eco_fraction or 0.0)
    for key in ("co2_g_per_km_eco", "co2_g_per_km_non", "fuel_l_per_100km_eco", "fuel_l_per_100km_non",
                "stops_per_vehicle_eco", "stops_per_vehicle_non", "travel_time_eco", "travel_time_non",
                "eco_advised_vehicles", "eco_advice_share_slow", "eco_advice_share_stop", "spat_pdr"):
        result_payload[key] = None
    result_payload.update({k: v for k, v in emission_metrics.items() if not k.startswith("n_trips_")})
    if eco_mgr is not None:
        result_payload.update(eco_mgr.get_metrics())

    # Incident fields (Module 10). incident_added_delay needs a paired no-incident run and is
    # filled by sim/analytics/vanet_experiments.py --incident.
    result_payload.update({
        "incident_type": None,
        "incident_edge": None,
        "incident_detected": None,
        "incident_detection_delay": None,
        "incident_detected_kind": None,
        "incident_false_flags": None,
        "incident_local_detours": None,
        "incident_rerouted_vehicles": None,
        "incident_nav_entries_after_detection": None,
        "incident_nonnav_entries_after_detection": None,
        "incident_added_delay": None,
    })
    if incident_mgr is not None:
        result_payload.update(incident_mgr.get_metrics())
        rerouted = set(incident_mgr.local_detour_vids) | set(ev_informed_reroutes)
        rerouted |= {vid for (rt, vid) in navigator.reroute_log if incident_mgr.is_active_window(rt)}
        result_payload["incident_rerouted_vehicles"] = len(rerouted)

        from sim.navigation.navigator import is_vehicle_navigating

        def _is_nav(vid: str) -> bool:
            if nav == "none" or not is_vehicle_equipped(vid, penetration=penetration, seed=seed):
                return False
            return is_vehicle_navigating(vid, nav_fraction=nav_frac, seed=seed)

        n_nav, n_non = incident_mgr.entries_after_detection(_is_nav)
        result_payload["incident_nav_entries_after_detection"] = n_nav
        result_payload["incident_nonnav_entries_after_detection"] = n_non

    if is_vanet_controller and vanet_net is not None:
        mae = round(float(sum(queue_errors) / max(len(queue_errors), 1)), 3)
        final_comm = vanet_net.get_comm_stats()
        result_payload["queue_estimate_mae"] = mae
        result_payload["pdr"] = final_comm["pdr"]
        result_payload["avg_latency_ms"] = final_comm["latency_ms"]
        result_payload["avg_hops"] = final_comm.get("avg_hops", 1.0)
        result_payload["ev_alert_delivery_time"] = final_comm.get("ev_alert_delivery_time", 0.0)
        result_payload["missed_preemptions"] = final_comm.get("missed_preemptions", 0)

    # Missed preemptions: signalised junctions an EV drove through without that junction's
    # preemption having triggered for it (only meaningful for preemption controllers)
    if any(hasattr(c, "triggered_evs") for c in controllers_map.values()):
        result_payload["missed_preemptions"] = sum(
            1 for (ev_id, tid) in ev_crossings
            if ev_id not in getattr(controllers_map.get(tid), "triggered_evs", set())
        )

    # Write preemption events CSV if any occurred
    import csv
    all_preemption_records = []
    for ctrl in controllers_map.values():
        if hasattr(ctrl, "preemption_records"):
            all_preemption_records.extend(ctrl.preemption_records)

    if all_preemption_records:
        preempt_csv_path = results_dir / f"preemption_events_{scenario_name}_{controller}_s{seed}.csv"
        with open(preempt_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["time", "tls_id", "vehicle_id", "eta_at_trigger", "duration"]
            )
            writer.writeheader()
            writer.writerows(all_preemption_records)

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
            "scenario": scenario_name,
        })

    return result_payload
