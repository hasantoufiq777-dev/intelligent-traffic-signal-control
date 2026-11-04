"""Route choice for emergency vehicles (ambulances) on the TMC traffic map.

An ambulance is always equipped, so it receives the TMC map (edge speeds and incidents) over V2I.
With routing on, it plans its route with A*:
- when it departs,
- each time it enters a new edge (the next turn can still be changed there),
- at once when the TMC reports a new closure.

It switches route when a closed edge lies ahead, or when the new route is clearly faster: at least
`hysteresis` cheaper and `min_gain_s` seconds faster than the rest of the current route. Both costs
leave out the edge the ambulance is on (it is the same for every alternative).

Signal preemption turns the lights ahead of the ambulance green, so its route cost counts a smaller
wait per traffic light (`junction_delay_s`) than ordinary navigation does. The new route flows into
the ambulance's EV_ALERTs (they carry its route), so preemption follows it.

With routing off the ambulance keeps the route from the scenario file. It then avoids a closure only
if its driver sees the blockage on arrival (the incident scenario's local-detour rule).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from sim.navigation.astar import astar_search
from sim.navigation.graph import EdgeGraph, SpeedSmoother

logger = logging.getLogger(__name__)


class EmergencyRouter:
    """Plans and updates the routes of emergency vehicles."""

    def __init__(
        self,
        sumo_api: Any,
        graph: EdgeGraph,
        speed_smoothing_s: float = 60.0,
        junction_delay_s: float = 0.0,
        hysteresis: float = 0.05,
        min_gain_s: float = 3.0,
        gui: bool = False,
    ) -> None:
        self.sumo_api = sumo_api
        self.graph = graph
        self.junction_delay_s = float(junction_delay_s)
        self.hysteresis = float(hysteresis)
        self.min_gain_s = float(min_gain_s)
        self.gui = bool(gui)
        self._smoother = SpeedSmoother(graph, speed_smoothing_s)

        self._last_edge: Dict[str, str] = {}      # EV -> edge it was on at the last check
        self._known_closed: Set[str] = set()      # closures already planned around
        self.events_queue: List[Dict[str, Any]] = []
        self.reroutes: List[Dict[str, Any]] = []  # one entry per applied route change
        self.checks: int = 0

    # ------------------------------------------------------------------ helpers
    def _route_cost(self, edges: List[str], speed_map: Optional[Dict[str, float]],
                    closed: Optional[Set[str]]) -> float:
        return sum(self.graph.get_edge_cost(e, speed_map=speed_map, closed_edges=closed,
                                            junction_delay_s=self.junction_delay_s) for e in edges)

    def _emergency_ids(self, vehicle_ids: List[str]) -> List[str]:
        out = []
        for vid in vehicle_ids:
            try:
                if str(self.sumo_api.vehicle.getTypeID(vid)) == "emergency":
                    out.append(vid)
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------ main step
    def step(
        self,
        sim_time: float,
        vehicle_ids: List[str],
        speed_map: Optional[Dict[str, float]] = None,
        closed_edges: Optional[Set[str]] = None,
        incident_edges: Optional[Set[str]] = None,
    ) -> None:
        """Check every emergency vehicle that departed, entered a new edge, or faces a new closure."""
        smoothed = self._smoother.update(sim_time, speed_map or {}, keep_raw=incident_edges)
        closed = set(closed_edges or ())
        new_closure = bool(closed - self._known_closed)
        self._known_closed = closed

        evs = self._emergency_ids(vehicle_ids)
        for gone in [v for v in self._last_edge if v not in evs]:
            self._last_edge.pop(gone, None)

        for vid in evs:
            try:
                road = str(self.sumo_api.vehicle.getRoadID(vid))
            except Exception:
                continue
            if road.startswith(":") or road not in self.graph.edges:
                continue  # inside a junction: the route can only change on a normal edge
            entered = self._last_edge.get(vid) != road
            self._last_edge[vid] = road
            if not (entered or new_closure):
                continue
            self._check(vid, road, sim_time, smoothed, closed)

    def _check(self, vid: str, road: str, sim_time: float,
               speed_map: Optional[Dict[str, float]], closed: Set[str]) -> None:
        try:
            route = list(self.sumo_api.vehicle.getRoute(vid))
            idx = int(self.sumo_api.vehicle.getRouteIndex(vid))
        except Exception:
            return
        if not route or idx < 0 or route[idx] != road or road == route[-1]:
            return
        self.checks += 1
        remaining = route[idx:]
        cur_cost = self._route_cost(remaining[1:], speed_map, closed)
        path, new_cost, _exp = astar_search(
            graph=self.graph, start_edge=road, goal_edge=route[-1], speed_map=speed_map,
            closed_edges=closed or None, junction_delay_s=self.junction_delay_s,
        )
        if not path or new_cost == float("inf") or path == remaining:
            return

        blocked = cur_cost == float("inf")
        faster = (new_cost < cur_cost * (1.0 - self.hysteresis)) and (cur_cost - new_cost >= self.min_gain_s)
        if not (blocked or faster):
            return
        try:
            self.sumo_api.vehicle.setRoute(vid, path)
        except Exception as e:
            logger.debug("EV route change failed for %s: %s", vid, e)
            return

        reason = "closure" if blocked else "faster"
        gain = None if blocked else round(cur_cost - new_cost, 1)
        self.reroutes.append({"time": sim_time, "vehicle_id": vid, "reason": reason, "gain_s": gain,
                              "old_route": remaining, "new_route": path})
        via = " → ".join(path[1:-1]) or path[-1]
        detail = (f"{vid} rerouted around a closed road, now via {via}" if blocked
                  else f"{vid} takes a faster route via {via} (about {gain:.0f} s quicker on the TMC map)")
        self.events_queue.append({"type": "event", "event": "ev_reroute", "vehicle_id": vid,
                                  "time": sim_time, "detail": detail})
        logger.info("[EV routing] t=%.0f %s", sim_time, detail)
        if self.gui:
            try:  # yellow ring around the ambulance in SUMO-GUI for 5 s
                self.sumo_api.vehicle.highlight(vid, (255, 210, 0, 255), -1, -1, 5.0)
            except Exception:
                pass

    # ------------------------------------------------------------------ output
    def pop_events(self) -> List[Dict[str, Any]]:
        events, self.events_queue = self.events_queue, []
        return events

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "ev_reroutes": len(self.reroutes),
            "ev_reroutes_closure": sum(1 for r in self.reroutes if r["reason"] == "closure"),
            "ev_route_checks": self.checks,
        }
