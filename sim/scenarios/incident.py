"""Incident simulation (ground truth) for Module 10.

Two incident types, configured in the scenario YAML under `incident:`:

- "stop": a vehicle breaks down on one lane of an edge for `duration` seconds.
  The other lanes stay open; traffic changes lanes around it.
- "closure": the edge is closed at `start_time` and reopened at start_time + duration.
  Modelled physically as a stopped vehicle on every lane (a crash blocking the road).

Blocking vehicles are inserted with traci.vehicle.add and held with traci.vehicle.setStop,
then removed (towed away) when the incident ends. They use the vType "incident_blocker"
and the id prefix BLOCKER_PREFIX, and are excluded from trip metrics.

Lane permissions are NOT changed: SUMO aborts when a running vehicle's route becomes
invalid, so a closure is a physical blockage instead.

This module is the simulation side (truth). It is never used for control: signals and
navigation learn about incidents only through VANET beacons -> RSU INCIDENT_FLAG -> TMC.
The one exception is the optional `local_detour` baseline: a driver who reaches the edge
just before a closure sees the blockage and SUMO reroutes them (like SUMO's own rerouting),
which applies to every vehicle, navigating or not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

BLOCKER_PREFIX = "incblk_"
BLOCKER_VTYPE = "incident_blocker"
# Vehicles already committed to the edge (inside the junction) when the TMC learns of the
# incident cannot react; entries within this many seconds after detection are not counted.
ENTRY_GRACE_S = 5.0

_TYPE_ALIASES = {
    "stop": "stop",
    "stopped_vehicle": "stop",
    "breakdown": "stop",
    "lane_closure": "stop",
    "closure": "closure",
    "edge_closure": "closure",
    "road_closure": "closure",
}


def is_blocker(vehicle_id: str) -> bool:
    """True for the synthetic vehicles that create an incident."""
    return str(vehicle_id).startswith(BLOCKER_PREFIX)


@dataclass
class IncidentSpec:
    """One configured incident."""

    index: int
    type: str
    edge: str
    lane: int
    position: Optional[float]
    position_frac: float
    start_time: float
    duration: float

    @property
    def end_time(self) -> float:
        return self.start_time + self.duration

    @property
    def kind(self) -> str:
        """Expected TMC kind for a perfect detector."""
        return "edge_blocked" if self.type == "closure" else "lane_blocked"


@dataclass
class IncidentState:
    """Runtime state of one incident."""

    spec: IncidentSpec
    active: bool = False
    finished: bool = False
    blockers: List[str] = field(default_factory=list)
    blocker_lanes: Dict[str, int] = field(default_factory=dict)
    stopped: Set[str] = field(default_factory=set)
    tls: str = ""
    detected_at: Optional[float] = None
    detected_kind: Optional[str] = None
    cleared_at: Optional[float] = None


def parse_incident_config(cfg: Optional[Dict[str, Any]]) -> List[IncidentSpec]:
    """Parse the scenario `incident:` block (single incident or a list under `events`)."""
    if not cfg or not cfg.get("enabled", False):
        return []
    entries = cfg.get("events") or [cfg]
    specs: List[IncidentSpec] = []
    for i, e in enumerate(entries):
        raw_type = str(e.get("type", cfg.get("type", "closure"))).lower()
        if raw_type not in _TYPE_ALIASES:
            raise ValueError(f"Unknown incident type '{raw_type}'. Use 'stop' or 'closure'.")
        edge = str(e.get("edge", cfg.get("edge", "")))
        if not edge:
            raise ValueError("Incident config needs an 'edge'.")
        pos = e.get("position", cfg.get("position"))
        specs.append(IncidentSpec(
            index=i,
            type=_TYPE_ALIASES[raw_type],
            edge=edge,
            lane=int(e.get("lane", cfg.get("lane", 0))),
            position=float(pos) if pos is not None else None,
            position_frac=float(e.get("position_frac", cfg.get("position_frac", 0.5))),
            start_time=float(e.get("start_time", cfg.get("start_time", 150.0))),
            duration=float(e.get("duration", cfg.get("duration", 180.0))),
        ))
    return specs


class IncidentManager:
    """Creates, holds and removes incidents in SUMO and scores VANET detection."""

    def __init__(self, sumo_api: Any, incident_cfg: Optional[Dict[str, Any]]) -> None:
        self.sumo_api = sumo_api
        cfg = dict(incident_cfg or {})
        self.local_detour = bool(cfg.get("local_detour", True))
        self.states: List[IncidentState] = [IncidentState(spec=s) for s in parse_incident_config(cfg)]
        self._vtype_ready = False
        self._active_ids: Set[str] = set()
        self._last_road: Dict[str, str] = {}
        # (time, vehicle id, edge) each time a vehicle enters an edge with an active incident
        self.edge_entries: List[Tuple[float, str, str]] = []

        self.events_queue: List[Dict[str, Any]] = []
        self.local_detour_vids: Set[str] = set()
        self._adapted: Dict[str, Set[str]] = {}  # vid -> edges with a per-vehicle adapted travel time
        self.false_flag_edges: Set[str] = set()
        self.informed_ev_reroutes: int = 0

        for st in self.states:
            st.tls = self._downstream_tls(st.spec.edge)

    @property
    def enabled(self) -> bool:
        return bool(self.states)

    # ------------------------------------------------------------------ truth
    def get_true_incidents(self) -> Dict[str, str]:
        """Edge -> kind for incidents that are currently in place."""
        result: Dict[str, str] = {}
        for st in self.states:
            if st.active:
                if result.get(st.spec.edge) != "edge_blocked":
                    result[st.spec.edge] = st.spec.kind
        return result

    def is_active_window(self, sim_time: float) -> bool:
        return any(st.spec.start_time <= sim_time < st.spec.end_time for st in self.states)

    def _downstream_tls(self, edge: str) -> str:
        try:
            return str(self.sumo_api.edge.getToJunction(edge))
        except Exception:
            return ""

    def _ensure_vtype(self) -> None:
        if self._vtype_ready:
            return
        try:
            self.sumo_api.vehicletype.copy("car", BLOCKER_VTYPE)
        except Exception:
            pass
        self._vtype_ready = True

    def _start(self, st: IncidentState, sim_time: float) -> None:
        spec = st.spec
        self._ensure_vtype()
        try:
            n_lanes = int(self.sumo_api.edge.getLaneNumber(spec.edge))
            length = float(self.sumo_api.lane.getLength(f"{spec.edge}_0"))
        except Exception as e:
            raise ValueError(f"Incident edge '{spec.edge}' is not in the network: {e}") from e
        pos = spec.position if spec.position is not None else spec.position_frac * length
        pos = max(5.0, min(length - 5.0, pos))

        lanes = list(range(n_lanes)) if spec.type == "closure" else [min(spec.lane, n_lanes - 1)]
        route_id = f"{BLOCKER_PREFIX}route_{spec.index}"
        try:
            self.sumo_api.route.add(route_id, [spec.edge])
        except Exception:
            pass
        for li in lanes:
            vid = f"{BLOCKER_PREFIX}{spec.index}_{li}"
            self.sumo_api.vehicle.add(
                vid, route_id, typeID=BLOCKER_VTYPE, depart="now",
                departLane=str(li), departPos=f"{pos:.2f}", departSpeed="0",
            )
            st.blockers.append(vid)
            st.blocker_lanes[vid] = li
        st.active = True
        what = "closed (all lanes blocked)" if spec.type == "closure" else f"lane {lanes[0]} blocked by a stopped vehicle"
        self.events_queue.append({
            "kind": "incident_start",
            "tls": st.tls,
            "detail": f"Incident on {spec.edge}: {what} at {pos:.0f} m for {spec.duration:.0f}s",
        })
        logger.info("Incident %d started on %s (%s) at t=%.0f", spec.index, spec.edge, spec.type, sim_time)

    def _hold_blockers(self, st: IncidentState) -> None:
        """Put a stop on each blocker once SUMO has inserted it."""
        for vid in st.blockers:
            if vid in st.stopped:
                continue
            try:
                if vid not in self._active_ids:
                    continue
                lane_pos = float(self.sumo_api.vehicle.getLanePosition(vid))
                self.sumo_api.vehicle.setStop(
                    vid, st.spec.edge, pos=lane_pos + 0.5,
                    laneIndex=st.blocker_lanes[vid], duration=1e6,
                )
                st.stopped.add(vid)
            except Exception as e:
                logger.debug("Could not stop blocker %s yet: %s", vid, e)

    def _end(self, st: IncidentState, sim_time: float) -> None:
        for vid in st.blockers:
            try:
                self.sumo_api.vehicle.remove(vid)
            except Exception:
                pass
        st.active = False
        st.finished = True
        # Drop per-vehicle closure knowledge so later SUMO reroutes may use the edge again
        for vid, edges in list(self._adapted.items()):
            if vid not in self._active_ids:
                self._adapted.pop(vid, None)
                continue
            if st.spec.edge in edges:
                try:
                    self.sumo_api.vehicle.setAdaptedTraveltime(vid, st.spec.edge)
                except Exception:
                    pass
                edges.discard(st.spec.edge)
        self.events_queue.append({
            "kind": "incident_end",
            "tls": st.tls,
            "detail": f"Incident on {st.spec.edge} cleared; edge reopened",
        })
        logger.info("Incident %d ended on %s at t=%.0f", st.spec.index, st.spec.edge, sim_time)

    def _apply_local_detours(self, closed: Set[str]) -> None:
        """Vehicles about to enter a closed edge see the blockage; SUMO reroutes them."""
        for vid in self._active_ids:
            if is_blocker(vid):
                continue
            try:
                route = self.sumo_api.vehicle.getRoute(vid)
                idx = int(self.sumo_api.vehicle.getRouteIndex(vid))
                if idx < 0 or idx + 1 >= len(route):
                    continue
                if route[idx + 1] not in closed or self.sumo_api.vehicle.getRoadID(vid) != route[idx]:
                    continue
                nxt = route[idx + 1]
                self.sumo_api.vehicle.setAdaptedTraveltime(vid, nxt, 1e6)
                self._adapted.setdefault(vid, set()).add(nxt)
                self.sumo_api.vehicle.rerouteTraveltime(vid, False)
                new_route = list(self.sumo_api.vehicle.getRoute(vid))
                new_idx = int(self.sumo_api.vehicle.getRouteIndex(vid))
                if nxt not in new_route[new_idx + 1:]:
                    self.local_detour_vids.add(vid)
            except Exception as e:
                logger.debug("Local detour failed for %s: %s", vid, e)

    def step(self, sim_time: float) -> None:
        """Advance incidents for this step. Call before simulationStep()."""
        if not self.states:
            return
        try:
            self._active_ids = set(self.sumo_api.vehicle.getIDList())
        except Exception:
            self._active_ids = set()

        for st in self.states:
            if not st.active and not st.finished and sim_time >= st.spec.start_time:
                self._start(st, sim_time)
            if st.active and sim_time >= st.spec.end_time:
                self._end(st, sim_time)
            if st.active:
                self._hold_blockers(st)

        # Record vehicles entering an active incident edge (for the avoidance metrics)
        active_edges = {st.spec.edge for st in self.states if st.active}
        if active_edges:
            for vid in self._active_ids:
                if is_blocker(vid):
                    continue
                try:
                    road = str(self.sumo_api.vehicle.getRoadID(vid))
                except Exception:
                    continue
                if road in active_edges and self._last_road.get(vid) != road:
                    self.edge_entries.append((sim_time, vid, road))
                self._last_road[vid] = road
        elif self._last_road:
            self._last_road.clear()

        if self.local_detour:
            closed = {st.spec.edge for st in self.states if st.active and st.spec.type == "closure"}
            if closed:
                self._apply_local_detours(closed)

    # -------------------------------------------------------------- detection
    def observe_tmc_changes(self, changes: List[Dict[str, Any]], sim_time: float) -> None:
        """Score TMC incident-map changes (from VANET) against the true incidents."""
        for ch in changes:
            edge, before, after = ch["edge"], ch.get("before"), ch.get("after")
            # True incidents on this edge that have already started (active or finished)
            matches = [st for st in self.states if st.spec.edge == edge and st.spec.start_time <= sim_time]
            if before is None and after is not None:
                st = next((m for m in matches if m.detected_at is None and m.active), None)
                if st is not None:
                    st.detected_at = sim_time
                    st.detected_kind = after
                    delay = sim_time - st.spec.start_time
                    self.events_queue.append({
                        "kind": "incident_detected",
                        "tls": ch.get("rsu", st.tls),
                        "detail": (
                            f"TMC marked {edge} as {after} via RSU {ch.get('rsu')} "
                            f"(detection delay {delay:.1f}s)"
                        ),
                    })
                elif not matches:
                    # Flag on an edge with no true incident (e.g. spillback or a long red)
                    self.false_flag_edges.add(edge)
                    self.events_queue.append({
                        "kind": "incident_detected",
                        "tls": ch.get("rsu", ""),
                        "detail": f"TMC marked {edge} as {after} via RSU {ch.get('rsu')} (no true incident on this edge)",
                    })
            elif after is not None and before is not None:
                for st in matches:
                    if st.active:
                        st.detected_kind = after
            elif after is None:
                for st in matches:
                    if st.detected_at is not None and st.cleared_at is None:
                        st.cleared_at = sim_time

    def pop_events(self) -> List[Dict[str, Any]]:
        events = self.events_queue[:]
        self.events_queue.clear()
        return events

    def entries_after_detection(
        self, is_nav_fn: Callable[[str], bool], grace_s: float = ENTRY_GRACE_S
    ) -> Tuple[Optional[int], Optional[int]]:
        """Count (navigating, non-navigating) vehicles that entered an incident edge more than
        grace_s seconds after the TMC detected it, while it was still active.
        Returns (None, None) if the incident was never detected."""
        windows: Dict[str, List[Tuple[float, float]]] = {}
        for st in self.states:
            if st.detected_at is not None:
                windows.setdefault(st.spec.edge, []).append((st.detected_at + grace_s, st.spec.end_time))
        if not windows:
            return None, None
        nav = non_nav = 0
        for t, vid, edge in self.edge_entries:
            if any(a <= t < b for a, b in windows.get(edge, [])):
                if is_nav_fn(vid):
                    nav += 1
                else:
                    non_nav += 1
        return nav, non_nav

    def get_metrics(self) -> Dict[str, Any]:
        """Detection and impact metrics for the results dict."""
        if not self.states:
            return {}
        delays = [st.detected_at - st.spec.start_time for st in self.states if st.detected_at is not None]
        first = self.states[0]
        return {
            "incident_type": first.spec.type,
            "incident_edge": first.spec.edge,
            "incident_detected": all(st.detected_at is not None for st in self.states),
            "incident_detection_delay": round(float(sum(delays) / len(delays)), 1) if delays else None,
            "incident_detected_kind": first.detected_kind,
            "incident_false_flags": len(self.false_flag_edges),
            "incident_local_detours": len(self.local_detour_vids),
        }
