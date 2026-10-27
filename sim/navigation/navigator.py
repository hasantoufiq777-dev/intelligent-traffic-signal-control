"""Vehicular dynamic route navigation engine.

Orchestrates real-time routing for navigating vehicles using A* or Ant Colony
Optimization (ACO). Reroutes vehicles dynamically based on VANET TMC speed maps,
enforcing minimum reroute intervals, route hysteresis, and SUMO topology validity.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from sim.navigation.aco import AcoRouter
from sim.navigation.astar import astar_search
from sim.navigation.graph import EdgeGraph

logger = logging.getLogger(__name__)


def is_vehicle_navigating(vid: str, nav_fraction: float, seed: int = 1) -> bool:
    """Determine deterministically whether an equipped vehicle has active navigation."""
    if nav_fraction <= 0.0:
        return False
    if nav_fraction >= 1.0:
        return True
    digest = hashlib.sha256(f"{seed}:nav:{vid}".encode("utf-8")).hexdigest()[:8]
    val = int(digest, 16) / 0xFFFFFFFF
    return val < nav_fraction


class VehicleNavigator:
    """Manages periodic dynamic rerouting of vehicles via A* or ACO."""

    def __init__(
        self,
        sumo_api: Any,
        graph: EdgeGraph,
        mode: str = "none",
        nav_fraction: float = 0.3,
        nav_interval: float = 30.0,
        min_reroute_gap: float = 15.0,
        hysteresis: float = 0.05,
        aco_config: Optional[Dict[str, Any]] = None,
        heuristic: str = "euclid",
        seed: int = 1,
    ) -> None:
        """Initialize navigation engine."""
        self.sumo_api = sumo_api
        self.graph = graph
        self.mode = mode.lower()
        self.nav_fraction = float(nav_fraction)
        self.nav_interval = float(nav_interval)
        self.min_reroute_gap = float(min_reroute_gap)
        self.hysteresis = float(hysteresis)
        self.heuristic = heuristic
        self.seed = int(seed)
        self.rng = random.Random(self.seed + 777)

        # ACO router instance
        self.aco_router = AcoRouter(
            graph=self.graph,
            config=aco_config,
            seed=self.seed,
        )

        # Vehicle tracking state
        self.last_reroute_time: Dict[str, float] = {}
        self.vehicle_prev_edge: Dict[str, str] = {}
        self.depart_times: Dict[str, float] = {}
        self.is_nav_vehicle: Dict[str, bool] = {}

        # Travel time aggregation
        self.nav_travel_times: List[float] = []
        self.nonnav_travel_times: List[float] = []

        # Telemetry metrics
        self.reroute_count: int = 0
        self.total_search_expansions: int = 0
        self.total_search_ms: float = 0.0
        self.step_edge_loads: List[List[int]] = []

        # Event queue and rate limiting (max 5 per second)
        self.events_queue: List[Dict[str, Any]] = []
        self._last_event_sec: int = -1
        self._event_sec_count: int = 0

    def pop_events(self) -> List[Dict[str, Any]]:
        """Pop all pending navigation events."""
        events = self.events_queue[:]
        self.events_queue.clear()
        return events

    def _emit_reroute_event(
        self, vid: str, old_route: List[str], new_route: List[str], sim_time: float
    ) -> None:
        """Emit a sampled reroute event."""
        sec = int(sim_time)
        if sec != self._last_event_sec:
            self._last_event_sec = sec
            self._event_sec_count = 0

        if self._event_sec_count < 5:
            self._event_sec_count += 1
            self.events_queue.append({
                "type": "event",
                "event": "reroute",
                "vehicle_id": vid,
                "time": sim_time,
                "mode": self.mode,
                "from_edge": new_route[0] if new_route else "",
                "to_edge": new_route[-1] if new_route else "",
                "edges_count": len(new_route),
            })

    def step(
        self,
        sim_time: float,
        speed_map: Optional[Dict[str, float]] = None,
        closed_edges: Optional[Set[str]] = None,
        equipped_check_fn: Optional[Any] = None,
    ) -> None:
        """Execute one simulation step of dynamic vehicle navigation."""
        if not self.sumo_api:
            return

        try:
            active_vids = set(self.sumo_api.vehicle.getIDList())
        except Exception:
            return

        # 1. Track departed vehicles and edge load distributions
        edge_counts: Dict[str, int] = {}
        for vid in active_vids:
            if vid not in self.depart_times:
                self.depart_times[vid] = sim_time
                is_equipped = True
                if equipped_check_fn is not None:
                    is_equipped = equipped_check_fn(vid)
                self.is_nav_vehicle[vid] = is_vehicle_navigating(
                    vid=vid, nav_fraction=self.nav_fraction, seed=self.seed
                ) if is_equipped else False

            try:
                road_id = self.sumo_api.vehicle.getRoadID(vid)
                if road_id and not road_id.startswith(":"):
                    edge_counts[road_id] = edge_counts.get(road_id, 0) + 1
            except Exception:
                pass

        if edge_counts:
            self.step_edge_loads.append(list(edge_counts.values()))

        # 2. Check for completed trips to collect travel time metrics
        for tracked_vid in list(self.depart_times.keys()):
            if tracked_vid not in active_vids:
                dep_time = self.depart_times.pop(tracked_vid, sim_time)
                duration = sim_time - dep_time
                is_nav = self.is_nav_vehicle.pop(tracked_vid, False)
                if is_nav:
                    self.nav_travel_times.append(duration)
                else:
                    self.nonnav_travel_times.append(duration)
                self.last_reroute_time.pop(tracked_vid, None)
                self.vehicle_prev_edge.pop(tracked_vid, None)

        if self.mode == "none":
            return

        # 3. Dynamic Rerouting Evaluation
        for vid in active_vids:
            if not self.is_nav_vehicle.get(vid, False):
                continue

            try:
                curr_road = str(self.sumo_api.vehicle.getRoadID(vid))
            except Exception:
                continue

            # Cannot reroute while inside an internal junction link (e.g., ':J0_0')
            if curr_road.startswith(":") or curr_road not in self.graph.edges:
                continue

            prev_road = self.vehicle_prev_edge.get(vid, curr_road)
            self.vehicle_prev_edge[vid] = curr_road

            # Condition: passed junction or nav_interval elapsed
            passed_junction = (curr_road != prev_road)
            last_reroute = self.last_reroute_time.get(vid, -999.0)
            time_since_reroute = sim_time - last_reroute

            interval_trigger = (time_since_reroute >= self.nav_interval)
            gap_ok = (time_since_reroute >= self.min_reroute_gap)

            if not (gap_ok and (passed_junction or interval_trigger)):
                continue

            try:
                current_route = list(self.sumo_api.vehicle.getRoute(vid))
                route_index = int(self.sumo_api.vehicle.getRouteIndex(vid))
            except Exception:
                continue

            if not current_route:
                continue

            goal_edge = current_route[-1]
            if curr_road == goal_edge:
                continue

            # Evaluate remaining cost of current route
            remaining_route = current_route[route_index:] if route_index >= 0 else [curr_road, goal_edge]
            curr_route_cost = 0.0
            for r_edge in remaining_route:
                c = self.graph.get_edge_cost(r_edge, speed_map=speed_map, closed_edges=closed_edges)
                curr_route_cost += c

            # Search alternative route
            t_start = time.perf_counter()
            new_path: List[str] = []
            new_cost: float = float("inf")
            expansions: int = 0

            if self.mode == "astar":
                new_path, new_cost, expansions = astar_search(
                    graph=self.graph,
                    start_edge=curr_road,
                    goal_edge=goal_edge,
                    speed_map=speed_map,
                    closed_edges=closed_edges,
                    heuristic=self.heuristic,
                )
            elif self.mode in ("aco", "aco_tuned"):
                candidates = self.aco_router.find_routes(
                    start_edge=curr_road,
                    goal_edge=goal_edge,
                    speed_map=speed_map,
                    closed_edges=closed_edges,
                    k=self.aco_router.elite_k,
                )
                if candidates:
                    # Probabilistic selection among top K alternatives
                    r = self.rng.random()
                    cum = 0.0
                    selected_cand = candidates[0]
                    for cand_path, cand_cost, cand_prob in candidates:
                        cum += cand_prob
                        if cum >= r:
                            selected_cand = (cand_path, cand_cost, cand_prob)
                            break
                    new_path = selected_cand[0]
                    new_cost = selected_cand[1]
                    expansions = self.aco_router.n_ants * self.aco_router.n_iterations

            t_elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            self.total_search_ms += t_elapsed_ms
            self.total_search_expansions += expansions

            # Apply route if strictly advantageous under hysteresis threshold
            if new_path and new_path != remaining_route:
                if new_cost < (curr_route_cost * (1.0 - self.hysteresis)):
                    try:
                        self.sumo_api.vehicle.setRoute(vid, new_path)
                        self.reroute_count += 1
                        self.last_reroute_time[vid] = sim_time
                        self._emit_reroute_event(vid, remaining_route, new_path, sim_time)
                    except Exception as e:
                        logger.debug("Failed to setRoute for vehicle %s: %s", vid, e)

    def get_nav_metrics(self) -> Dict[str, Any]:
        """Aggregate navigation performance metrics."""
        nav_avg = round(float(np.mean(self.nav_travel_times)), 2) if self.nav_travel_times else 0.0
        nonnav_avg = round(float(np.mean(self.nonnav_travel_times)), 2) if self.nonnav_travel_times else 0.0

        all_loads: List[int] = []
        for step_loads in self.step_edge_loads:
            all_loads.extend(step_loads)

        if all_loads:
            mean_l = float(np.mean(all_loads))
            std_l = float(np.std(all_loads))
            edge_load_cv = round(std_l / max(mean_l, 1e-3), 3)
            max_load = int(np.max(all_loads))
        else:
            edge_load_cv = 0.0
            max_load = 0

        return {
            "nav_mode": self.mode,
            "nav_avg_travel_time": nav_avg,
            "nonnav_avg_travel_time": nonnav_avg,
            "edge_load_cv": edge_load_cv,
            "max_edge_load": max_load,
            "route_search_expansions": self.total_search_expansions,
            "route_search_ms": round(self.total_search_ms, 1),
            "reroute_count": self.reroute_count,
        }

    def get_live_nav_state(self) -> Dict[str, Any]:
        """Return the navigation telemetry block for live streaming."""
        nav_active_count = sum(1 for is_n in self.is_nav_vehicle.values() if is_n)
        metrics = self.get_nav_metrics()
        return {
            "mode": self.mode,
            "navigating": nav_active_count,
            "avg_tt_nav": metrics["nav_avg_travel_time"],
            "avg_tt_non": metrics["nonnav_avg_travel_time"],
            "max_edge_load": metrics["max_edge_load"],
            "edge_load_cv": metrics["edge_load_cv"],
        }
