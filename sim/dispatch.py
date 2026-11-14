"""Ambulance on demand: send an ambulance into a running live simulation at any moment.

The dashboard sends ``{"cmd": "ambulance"}`` (optionally with ``from`` and ``to`` road ids). The
ambulance is added to SUMO at once, and every signalised junction gets ambulance priority
(preemption) for the rest of the run:

- Controllers that already have preemption (``adaptive_ev*``) are left as they are.
- Any other controller (fixed, adaptive, adaptive_vanet) is wrapped in a PreemptionController that
  runs it unchanged until an ambulance comes within the ETA threshold, then gives the ambulance's
  approach a safe green (yellow, all-red, green) exactly as in the emergency scenarios. The wrapper
  reads ambulances through the run's own sensing (exact, or VANET alerts for adaptive_vanet).

Nothing here runs unless a command arrives, so runs without a dispatch are unchanged.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

AMBULANCE_PREFIX = "amb_dispatch_"
AMBULANCE_VTYPE = "emergency"
MAX_ACTIVE = 3  # dispatched ambulances on the road at the same time


def add_priority(
    controllers_map: Dict[str, Any],
    params: Dict[str, Any],
    provider: Any,
    sumo_api: Any,
) -> List[str]:
    """Give every junction ambulance priority. Returns the junctions whose controller was wrapped."""
    from sim.controllers.preemption import PreemptionController

    wrapped: List[str] = []
    for tid, ctrl in list(controllers_map.items()):
        if isinstance(ctrl, PreemptionController) or hasattr(ctrl, "triggered_evs"):
            continue
        controllers_map[tid] = PreemptionController(
            tid, params=params, provider=provider, sumo_api=sumo_api, inner=ctrl
        )
        wrapped.append(tid)
    return wrapped


class AmbulanceDispatcher:
    """Adds ambulances on request and reports their trips."""

    def __init__(
        self,
        sumo_api: Any,
        tls_ids: List[str],
        controllers_map: Dict[str, Any],
        enable_priority: Callable[[], List[str]],
        seed: int = 1,
    ) -> None:
        self.api = sumo_api
        self.tls_ids = list(tls_ids)
        self.tls_set: Set[str] = set(tls_ids)
        self.controllers_map = controllers_map
        self._enable_priority = enable_priority
        self.rng = random.Random(int(seed) * 7919 + 17)  # own stream: never touches the run's randomness
        self.priority_added = False
        self.wrapped: List[str] = []
        self.trips: List[Dict[str, Any]] = []
        self._events: List[Dict[str, Any]] = []
        self._n = 0
        self._edges: Optional[List[str]] = None
        self._vtype_ready = False

    # ------------------------------------------------------------------ network helpers
    def _all_edges(self) -> List[str]:
        if self._edges is None:
            self._edges = sorted(e for e in self.api.edge.getIDList() if not str(e).startswith(":"))
        return self._edges

    def _junctions_on(self, route: List[str]) -> List[str]:
        """Signalised junctions the route drives through, in order."""
        out: List[str] = []
        for e in route[:-1]:
            try:
                j = str(self.api.edge.getToJunction(e))
            except Exception:
                continue
            if j in self.tls_set and j not in out:
                out.append(j)
        return out

    def _find_route(self, origin: str, dest: str) -> List[str]:
        try:
            return [str(e) for e in self.api.simulation.findRoute(origin, dest, vType=AMBULANCE_VTYPE).edges]
        except Exception:
            return []

    def _pick_route(self) -> Tuple[str, str, List[str]]:
        """Default trip: from an entry road to an exit road, through as many signalised junctions as
        possible (the network's longest crossing); ties are broken by the dispatcher's own random."""
        all_tls = set(self.api.trafficlight.getIDList())
        entries, exits = [], []
        for e in self._all_edges():
            try:
                if str(self.api.edge.getFromJunction(e)) not in all_tls:
                    entries.append(e)
                if str(self.api.edge.getToJunction(e)) not in all_tls:
                    exits.append(e)
            except Exception:
                continue
        best: List[Tuple[str, str, List[str]]] = []
        best_n = 0
        for a in entries:
            start = str(self.api.edge.getFromJunction(a))
            for b in exits:
                if str(self.api.edge.getToJunction(b)) == start:
                    continue  # back to where it came from
                route = self._find_route(a, b)
                if not route:
                    continue
                n = len(self._junctions_on(route))
                if n > best_n:
                    best, best_n = [(a, b, route)], n
                elif n == best_n and n > 0:
                    best.append((a, b, route))
        if not best:
            return "", "", []
        return self.rng.choice(best)

    def _ensure_vtype(self) -> None:
        if self._vtype_ready:
            return
        try:
            if AMBULANCE_VTYPE not in self.api.vehicletype.getIDList():
                self.api.vehicletype.copy("DEFAULT_VEHTYPE", AMBULANCE_VTYPE)
                self.api.vehicletype.setVehicleClass(AMBULANCE_VTYPE, "emergency")
                self.api.vehicletype.setColor(AMBULANCE_VTYPE, (255, 0, 0, 255))
        except Exception as e:  # the type comes from the route file in every scenario
            logger.debug("Could not check the ambulance vehicle type: %s", e)
        self._vtype_ready = True

    # ------------------------------------------------------------------ commands
    def _reject(self, t: float, reason: str) -> None:
        self._events.append({"kind": "ambulance_rejected", "tls": "", "detail": f"Ambulance not sent: {reason}"})
        logger.info("Ambulance dispatch at %.0fs refused: %s", t, reason)

    def handle(self, cmd: Dict[str, Any], t: float) -> Optional[str]:
        """Dispatch one ambulance. Returns its vehicle id, or None when the request is refused."""
        active = [r for r in self.trips if r["arrived_t"] is None and not r.get("lost")]
        if len(active) >= MAX_ACTIVE:
            self._reject(t, f"{MAX_ACTIVE} dispatched ambulances are already on the road")
            return None
        origin, dest = str(cmd.get("from") or ""), str(cmd.get("to") or "")
        if origin or dest:
            edges = set(self._all_edges())
            for road in (origin, dest):
                if road not in edges:
                    self._reject(t, f"unknown road {road or '(none)'}")
                    return None
            if origin == dest:
                self._reject(t, "start and destination are the same road")
                return None
            route = self._find_route(origin, dest)
            if not route:
                self._reject(t, f"no route from {origin} to {dest}")
                return None
        else:
            origin, dest, route = self._pick_route()
            if not route:
                self._reject(t, "no road through a signalised junction")
                return None

        if not self.priority_added:
            self.wrapped = self._enable_priority()
            self.priority_added = True

        self._ensure_vtype()
        self._n += 1
        vid = f"{AMBULANCE_PREFIX}{self._n}"
        rid = f"{vid}_route"
        try:
            self.api.route.add(rid, route)
            self.api.vehicle.add(vid, rid, typeID=AMBULANCE_VTYPE, depart="now", departLane="best",
                                 departSpeed="max")
        except Exception as e:
            self._reject(t, f"SUMO did not accept the vehicle ({e})")
            return None

        junctions = self._junctions_on(route)
        self.trips.append({
            "id": vid, "from": origin, "to": dest, "dispatched_t": int(t), "depart_t": None,
            "arrived_t": None, "travel_time": None, "junctions": junctions, "priority_at": [],
        })
        how = ("ambulance priority switched on at " + ", ".join(self.wrapped)) if self.wrapped else \
            "the junctions' own ambulance priority"
        self._events.append({
            "kind": "ambulance_dispatched", "tls": junctions[0] if junctions else "",
            "detail": f"Ambulance {vid} sent from {origin} to {dest} through "
                      f"{', '.join(junctions) or 'no signalised junction'} ({how})",
        })
        logger.info("Ambulance %s dispatched at %.0fs: %s -> %s via %s", vid, t, origin, dest, junctions)
        return vid

    # ------------------------------------------------------------------ per second
    def step(self, t: float, active_vehs: List[str]) -> None:
        """Follow the dispatched ambulances: departure, arrival, and where they got priority."""
        if not self.trips:
            return
        active = set(active_vehs)
        for rec in self.trips:
            if rec["arrived_t"] is not None or rec.get("lost"):
                continue
            vid = rec["id"]
            if vid in active:
                if rec["depart_t"] is None:
                    rec["depart_t"] = int(t)
                continue
            if rec["depart_t"] is None:
                continue  # still waiting to enter the road
            rec["arrived_t"] = int(t)
            rec["travel_time"] = int(t) - rec["depart_t"]
            rec["priority_at"] = [j for j in rec["junctions"]
                                  if vid in getattr(self.controllers_map.get(j), "triggered_evs", set())]
            n_j, n_p = len(rec["junctions"]), len(rec["priority_at"])
            self._events.append({
                "kind": "ambulance_arrived", "tls": rec["junctions"][-1] if rec["junctions"] else "",
                "detail": f"Ambulance {vid} reached {rec['to']} after {rec['travel_time']} s; "
                          f"green given at {n_p} of {n_j} junctions",
            })

    def pop_events(self) -> List[Dict[str, Any]]:
        out, self._events = self._events, []
        return out

    def get_metrics(self) -> Dict[str, Any]:
        """Result fields. Only added to a run's result when an ambulance was sent."""
        done = [r for r in self.trips if r["travel_time"] is not None]
        return {
            "dispatched_ambulances": len(self.trips),
            "dispatched_arrived": len(done),
            "dispatched_avg_travel_time": round(sum(r["travel_time"] for r in done) / len(done), 1) if done else None,
            "dispatched_priority_added": self.wrapped,
            "dispatched_trips": [
                {k: r[k] for k in ("id", "from", "to", "dispatched_t", "depart_t", "arrived_t", "travel_time",
                                   "junctions", "priority_at")}
                for r in self.trips
            ],
        }
