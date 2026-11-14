"""Accident work-zone controller: alternating one-way traffic on a shared lane.

Used on the grid2x2_zone network (layout in sim/scenarios/accident_zone.py). It drives the two zone
signals, ZW (eastbound entry) and ZE (westbound entry), and has three modes:

normal       No accident: normal traffic on both carriageways, contraflow lane closed.
alternating  The crash blocks the westbound carriageway and one eastbound lane, so both directions
             share the eastbound outer lane. One direction at a time gets green:
               green(d) -> yellow(d) 3 s -> clearance (all red until the shared lane is empty)
               -> green(next direction)
             Safety: the next direction never gets green before the lane is empty. Occupancy comes
             from counting vehicles in and out at both ends of the zone (loop detectors), so it does
             not depend on radio messages.
             Timing: each green gets min_green + green_per_vehicle_s per queued vehicle (at most
             max_green). It ends early when its own approach is empty and the other side waits, and
             runs on while the other side has no demand. A side that waited starvation_s ends the
             other side's green at min_green. Queues come from the beacons the zone's roadside unit
             receives (range and loss of the emulated channel, scaled by 1/penetration).
             Ambulances: an EV_ALERT (V2V/V2I, range and loss) from an ambulance whose route crosses
             the zone within ev_eta_s gives its direction the next green: the other direction's
             green ends at once (no new entries), the lane clears, then the ambulance's side turns
             green and stays green until it has entered. Its own side's green is held for it.
             Routing: westbound routes through the blocked carriageway are switched to the
             contraflow lane (the zone's detour signs); navigation sees the expected wait at the
             zone as extra edge cost and may choose other roads.
closure      The westbound road is closed at J3 (the alternative without alternating control):
             vehicles about to enter it are sent over other roads; those already past J3 wait.

The mode switches on when the TMC incident map marks the blocked carriageway (VANET detection) and
back to normal when the TMC clears it.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from sim.scenarios.accident_zone import (
    AFTER,
    APPROACH,
    BLOCKED_EDGE,
    CONTRAFLOW_EDGE,
    DIRECTION_LABEL,
    ENTRY_TLS,
    SHARED,
    ZONE_TLS,
)
from sim.scenarios.incident import is_blocker
from sim.vanet.nodes import is_vehicle_equipped

logger = logging.getLogger(__name__)

OTHER = {"E": "W", "W": "E"}
QUEUE_SPEED = 1.0  # m/s: below this a vehicle counts as queued
BEACON_STALE_S = 3.0
EV_ALERT_HOLD_S = 2.0
FREE_SPEED = 13.89

DEFAULT_PARAMS: Dict[str, float] = {
    "min_green": 8.0,
    "max_green": 40.0,
    "green_per_vehicle_s": 2.5,
    "yellow": 3.0,
    "min_all_red": 2.0,
    "max_clearance_s": 90.0,  # safety net: only logged, the lane must really be empty to switch
    "starvation_s": 90.0,
    "gap_out_m": 40.0,
    "ev_eta_s": 25.0,
    "headway_s": 2.0,
}


class ZoneController:
    """Signals, routing and sensing for the accident work zone (see the module docstring)."""

    def __init__(
        self,
        sumo_api: Any,
        control: str = "alternating",
        params: Optional[Dict[str, Any]] = None,
        vanet_net: Optional[Any] = None,
        penetration: float = 1.0,
        range_m: float = 300.0,
        seed: int = 1,
    ) -> None:
        self.api = sumo_api
        self.control = control
        self.p = dict(DEFAULT_PARAMS)
        self.p.update({k: float(v) for k, v in (params or {}).items() if k in DEFAULT_PARAMS})
        self.vanet_net = vanet_net
        self.penetration = max(0.01, float(penetration))
        self.range_m = float(range_m)
        self.seed = int(seed)
        self.rng = random.Random(self.seed + 7331)

        self.mode = "normal"  # normal | alternating | closure
        self.phase = "normal"  # normal | green | yellow | clear | closure
        self.dir: Optional[str] = None  # direction of the current green / yellow
        self.last_served: Optional[str] = None
        self.phase_start = 0.0
        self.green_allotted = 0.0
        self.deactivate_requested = False
        self.ev_request: Optional[Tuple[str, str]] = None  # (ev id, direction)

        # Static geometry: link roles per zone signal, lane lengths, zone RSU position
        self.links: Dict[str, List[Tuple[str, str]]] = {}
        for tls in ZONE_TLS:
            roles: List[Tuple[str, str]] = []
            for link_group in self.api.trafficlight.getControlledLinks(tls):
                frm = link_group[0][0].rsplit("_", 1)[0] if link_group else ""
                to = link_group[0][1].rsplit("_", 1)[0] if link_group else ""
                roles.append((frm, to))
            self.links[tls] = roles
        self.length = {e: float(self.api.lane.getLength(f"{e}_0"))
                       for e in set(APPROACH.values()) | set(SHARED.values()) | {BLOCKED_EDGE}}
        xw = self.api.junction.getPosition("ZW")
        xe = self.api.junction.getPosition("ZE")
        self.rsu_pos = ((xw[0] + xe[0]) / 2.0, (xw[1] + xe[1]) / 2.0)

        # Sensing state
        self.beacons: Dict[str, Tuple[float, str, float, float]] = {}  # vid -> (t, edge, lane_pos, speed)
        self.ev_rx: Dict[str, float] = {}
        self.occupancy = {"E": 0, "W": 0}
        self.queue = {"E": 0, "W": 0}
        self.approaching = {"E": 0, "W": 0}
        self.red_since: Dict[str, Optional[float]] = {"E": None, "W": None}

        # Metrics and events
        self.events: List[Dict[str, Any]] = []
        self.activated_at: Optional[float] = None
        self.deactivated_at: Optional[float] = None
        self.switches = 0
        self.conflicts = 0
        self.max_clearance = 0.0
        self.passed = {"E": set(), "W": set()}
        self.true_max_queue = {"E": 0, "W": 0}
        self.ev_priority_ids: Set[str] = set()
        self.ev_wait: Dict[str, float] = {}  # ambulance id -> seconds stopped at the entry line
        self.rerouted: Set[str] = set()
        self._closure_redirected: Set[str] = set()
        self.guided: Set[str] = set()  # cars caught behind the crash, guided into the shared lane
        self._pending_route: Dict[str, List[str]] = {}

        self._set_travel_times()
        self._apply_signals()

    # ------------------------------------------------------------------ signals
    def _role_char(self, frm: str, to: str) -> str:
        """Signal for one link in the current mode and phase."""
        e_entry = frm == APPROACH["E"] and to == SHARED["E"]
        w_entry = frm == APPROACH["W"] and to == SHARED["W"]
        e_exit = frm == SHARED["E"]
        w_exit = frm == SHARED["W"]
        wb_in = frm == APPROACH["W"] and to == BLOCKED_EDGE
        wb_out = frm == BLOCKED_EDGE
        if e_exit or wb_out:
            return "G"
        if w_exit:
            return "g"  # merges with the westbound carriageway's exit: yield
        if self.mode in ("normal", "closure"):
            if w_entry:
                return "r"
            return "G"  # eastbound entry and the westbound carriageway
        # alternating
        if wb_in:
            return "r"
        d = "E" if e_entry else ("W" if w_entry else None)
        if d is None:
            return "r"
        if self.phase == "green" and self.dir == d:
            return "G"
        if self.phase == "yellow" and self.dir == d:
            return "y"
        return "r"

    def _apply_signals(self) -> None:
        for tls, roles in self.links.items():
            state = "".join(self._role_char(f, t) for f, t in roles)
            try:
                self.api.trafficlight.setRedYellowGreenState(tls, state)
            except Exception as e:
                logger.warning("Could not set zone signal %s: %s", tls, e)

    def entry_signal(self, d: str) -> str:
        """Signal shown at direction d's entry line: G, y or r."""
        for f, t in self.links[ENTRY_TLS[d]]:
            if f == APPROACH[d] and t == SHARED[d]:
                return self._role_char(f, t)
        return "r"

    # ------------------------------------------------------------- routing
    def _set_travel_times(self) -> None:
        """Travel times SUMO's own router uses (the driver mode's route search, rerouting)."""
        big = 1e6
        free = {e: self.length[e] / FREE_SPEED for e in self.length}
        times = {CONTRAFLOW_EDGE: big, BLOCKED_EDGE: free[BLOCKED_EDGE], APPROACH["W"]: free[APPROACH["W"]]}
        if self.mode == "alternating":
            times[CONTRAFLOW_EDGE] = free[CONTRAFLOW_EDGE] + self.expected_wait("W")
            times[BLOCKED_EDGE] = big
        elif self.mode == "closure":
            times[BLOCKED_EDGE] = big
            times[APPROACH["W"]] = big
        for e, tt in times.items():
            try:
                self.api.edge.adaptTraveltime(e, tt)
            except Exception:
                pass

    def closed_edges(self) -> Set[str]:
        """Edges navigation and ambulance routing must not use now."""
        if self.mode == "alternating":
            return {BLOCKED_EDGE}
        if self.mode == "closure":
            return {CONTRAFLOW_EDGE, BLOCKED_EDGE, APPROACH["W"]}
        return {CONTRAFLOW_EDGE}

    def expected_wait(self, d: str) -> float:
        """Expected wait at direction d's entry line: about half a cycle plus the queue ahead."""
        if self.mode != "alternating":
            return 0.0
        clear = self.length[SHARED[d]] / 8.0 + self.p["min_all_red"]
        other_green = min(self.p["max_green"],
                          self.p["min_green"] + self.p["green_per_vehicle_s"] * self.queue[OTHER[d]])
        return round(0.5 * (other_green + self.p["yellow"] + clear) + self.p["headway_s"] * self.queue[d], 1)

    def speed_overrides(self) -> Dict[str, float]:
        """Effective speeds (m/s) for the shared-lane edges that include the expected wait."""
        if self.mode != "alternating":
            return {}
        out = {}
        for d in ("E", "W"):
            e = SHARED[d]
            L = self.length[e]
            out[e] = L / (L / FREE_SPEED + self.expected_wait(d))
        return out

    def _replace_in_routes(self, active: List[str], old: str, new: str) -> None:
        """Switch remaining routes from one zone edge to another (vehicles not yet in the zone)."""
        for vid in active:
            if is_blocker(vid):
                continue
            try:
                road = str(self.api.vehicle.getRoadID(vid))
                if road.startswith(":") or road == old:
                    continue
                route = list(self.api.vehicle.getRoute(vid))
                idx = int(self.api.vehicle.getRouteIndex(vid))
                rest = route[idx:]
                if old not in rest[1:] or not rest or rest[0] != road:
                    continue
                self.api.vehicle.setRoute(vid, [new if e == old else e for e in rest])
                self.rerouted.add(vid)
            except Exception as e:
                logger.debug("Zone route change failed for %s: %s", vid, e)

    def _closure_detours(self, active: List[str]) -> None:
        """Closure mode: a vehicle about to turn into the closed road at J3 takes another route."""
        closed = APPROACH["W"]
        for vid in active:
            if is_blocker(vid) or vid in self._closure_redirected:
                continue
            try:
                road = str(self.api.vehicle.getRoadID(vid))
                route = list(self.api.vehicle.getRoute(vid))
                idx = int(self.api.vehicle.getRouteIndex(vid))
                if road.startswith(":") or idx + 1 >= len(route) or route[idx + 1] != closed or route[idx] != road:
                    continue
                self.api.vehicle.rerouteTraveltime(vid, False)
                self._closure_redirected.add(vid)
                if closed not in list(self.api.vehicle.getRoute(vid))[idx + 1:]:
                    self.rerouted.add(vid)
            except Exception as e:
                logger.debug("Closure detour failed for %s: %s", vid, e)

    def _trapped(self, active: List[str]) -> List[Tuple[float, str]]:
        """Cars caught behind the crash: on the blocked carriageway (or entering it inside ZE) when
        the zone switched on. (lane position, id), front-most first."""
        out = []
        for vid in active:
            if is_blocker(vid) or vid in self.guided:
                continue
            try:
                road = str(self.api.vehicle.getRoadID(vid))
                if road == BLOCKED_EDGE:
                    out.append((float(self.api.vehicle.getLanePosition(vid)), vid))
                elif road.startswith(":ZE_"):
                    route = self.api.vehicle.getRoute(vid)
                    idx = int(self.api.vehicle.getRouteIndex(vid))
                    if BLOCKED_EDGE in route[idx:idx + 2]:
                        out.append((-1.0, vid))
            except Exception:
                continue
        return sorted(out, reverse=True)

    def _guide_trapped(self, t: float, active: List[str]) -> int:
        """Before a westbound turn (shared lane empty): cars caught behind the crash cross into the
        shared lane, as traffic police would wave them past, and continue west on their route."""
        trapped = self._trapped(active)
        if not trapped:
            return 0
        shape = self.api.lane.getShape(f"{CONTRAFLOW_EDGE}_0")
        (x0, y0), (x1, y1) = shape[0], shape[-1]
        length = self.length[CONTRAFLOW_EDGE]
        gap = 7.5  # car length + minGap
        n = min(len(trapped), int((length - 10.0) // gap))
        front = max(trapped[0][0], 5.0 + gap * (n - 1))
        for k, (_lp, vid) in enumerate(trapped[:n]):
            pos = front - gap * k
            f = pos / length
            try:
                route = list(self.api.vehicle.getRoute(vid))
                rest = route[route.index(BLOCKED_EDGE) + 1:] if BLOCKED_EDGE in route else []
                self.api.vehicle.moveToXY(vid, CONTRAFLOW_EDGE, 0, x0 + (x1 - x0) * f, y0 + (y1 - y0) * f,
                                          angle=270.0, keepRoute=0)
                self._pending_route[vid] = [CONTRAFLOW_EDGE] + rest
                self.guided.add(vid)
            except Exception as e:
                logger.warning("Could not guide %s past the crash: %s", vid, e)
        self._event("zone_guided", f"{n} car(s) caught behind the crash guided into the shared lane", "ZE")
        return n

    def _fix_guided_routes(self) -> None:
        """A guided car keeps its destination: once it is on the shared lane, restore the rest of its route."""
        for vid, route in list(self._pending_route.items()):
            try:
                if str(self.api.vehicle.getRoadID(vid)) == CONTRAFLOW_EDGE:
                    self.api.vehicle.setRoute(vid, route)
                    self._pending_route.pop(vid, None)
            except Exception:
                self._pending_route.pop(vid, None)  # left the simulation

    # ------------------------------------------------------------- sensing
    def _direction_of(self, vid: str) -> Optional[str]:
        try:
            route = self.api.vehicle.getRoute(vid)
        except Exception:
            return None
        if SHARED["E"] in route:
            return "E"
        if SHARED["W"] in route:
            return "W"
        return None

    def _count_occupancy(self, active: List[str]) -> None:
        """Vehicles inside the shared lane per direction (entry/exit loop detectors at both ends)."""
        occ = {"E": 0, "W": 0}
        for vid in active:
            if is_blocker(vid):
                continue
            try:
                road = str(self.api.vehicle.getRoadID(vid))
            except Exception:
                continue
            # The shared lane and both end junctions (a car still crossing the exit junction blocks
            # the other direction's entry path there)
            if road in (SHARED["E"], SHARED["W"]) or road.startswith(":ZW_") or road.startswith(":ZE_"):
                d = self._direction_of(vid)
                if d is None:
                    continue  # leaving the closed westbound carriageway, not in the shared lane
                occ[d] += 1
                if road == SHARED[d] and self.mode == "alternating":
                    self.passed[d].add(vid)
        self.occupancy = occ

    def _receive(self, d_m: float) -> bool:
        """One radio message over distance d_m: in range and not lost on the emulated channel."""
        if d_m > self.range_m:
            return False
        p_loss = self.vanet_net.channel.calculate_loss_probability(d_m) if self.vanet_net is not None else 0.0
        return self.rng.random() >= p_loss

    def _sense_queues(self, t: float, active: List[str]) -> None:
        """Queues and approaching vehicles per direction from received beacons (or exact counts
        without a VANET)."""
        true_q = self._true_queues()

        if self.vanet_net is None:
            for d in ("E", "W"):
                self.queue[d] = true_q[d]
                n = 0
                for vid in self._on_edge(APPROACH[d]):
                    try:
                        if self.length[APPROACH[d]] - float(self.api.vehicle.getLanePosition(vid)) <= self.p["gap_out_m"]:
                            n += 1
                    except Exception:
                        pass
                self.approaching[d] = n
            return

        for vid in active:
            if is_blocker(vid):
                continue
            try:
                vtype = str(self.api.vehicle.getTypeID(vid))
                if not is_vehicle_equipped(vid, vtype=vtype, penetration=self.penetration, seed=self.seed):
                    continue
                road = str(self.api.vehicle.getRoadID(vid))
                if road not in (APPROACH["E"], APPROACH["W"]):
                    continue
                pos = self.api.vehicle.getPosition(vid)
                if not self._receive(math.hypot(pos[0] - self.rsu_pos[0], pos[1] - self.rsu_pos[1])):
                    continue
                self.beacons[vid] = (t, road, float(self.api.vehicle.getLanePosition(vid)),
                                     float(self.api.vehicle.getSpeed(vid)))
            except Exception:
                continue
        for vid in [v for v, b in self.beacons.items() if t - b[0] > BEACON_STALE_S]:
            self.beacons.pop(vid, None)
        scale = 1.0 / self.penetration
        for d in ("E", "W"):
            edge = APPROACH[d]
            recs = [b for b in self.beacons.values() if b[1] == edge]
            self.queue[d] = int(round(sum(1 for b in recs if b[3] < QUEUE_SPEED) * scale))
            self.approaching[d] = int(round(sum(1 for b in recs if self.length[edge] - b[2] <= self.p["gap_out_m"])
                                            * scale))

    def _true_queues(self) -> Dict[str, int]:
        """Halting vehicles on each approach (for the metrics, in both control modes)."""
        q = {"E": 0, "W": 0}
        for d in ("E", "W"):
            try:
                q[d] = int(self.api.edge.getLastStepHaltingNumber(APPROACH[d]))
            except Exception:
                pass
            self.true_max_queue[d] = max(self.true_max_queue[d], q[d])
        return q

    def _on_edge(self, edge: str) -> List[str]:
        try:
            return [v for v in self.api.edge.getLastStepVehicleIDs(edge) if not is_blocker(v)]
        except Exception:
            return []

    def _ambulance_request(self, t: float, active: List[str]) -> Optional[Tuple[str, str, float]]:
        """(ev id, direction, ETA) of the nearest ambulance heading for the zone whose alert was
        received, or None."""
        best: Optional[Tuple[str, str, float]] = None
        for vid in active:
            try:
                if str(self.api.vehicle.getTypeID(vid)) != "emergency":
                    continue
                pos = self.api.vehicle.getPosition(vid)
                if self._receive(math.hypot(pos[0] - self.rsu_pos[0], pos[1] - self.rsu_pos[1])):
                    self.ev_rx[vid] = t
                if t - self.ev_rx.get(vid, -1e9) > EV_ALERT_HOLD_S:
                    continue
                route = list(self.api.vehicle.getRoute(vid))
                idx = int(self.api.vehicle.getRouteIndex(vid))
                road = str(self.api.vehicle.getRoadID(vid))
                d = "E" if SHARED["E"] in route[idx:] else ("W" if SHARED["W"] in route[idx:] or BLOCKED_EDGE in route[idx:] else None)
                if d is None or road in (SHARED[d], AFTER[d]):
                    continue
                # Distance to the entry line along the route
                if road == APPROACH[d]:
                    dist = self.length[APPROACH[d]] - float(self.api.vehicle.getLanePosition(vid))
                elif road.startswith(":"):
                    dist = self.length[APPROACH[d]] + 10.0
                else:
                    dist = float(self.api.lane.getLength(f"{road}_0")) - float(self.api.vehicle.getLanePosition(vid))
                    k = route.index(road) + 1 if road in route else idx + 1
                    while k < len(route) and route[k] != APPROACH[d]:
                        dist += float(self.api.lane.getLength(f"{route[k]}_0")) + 10.0
                        k += 1
                    dist += self.length[APPROACH[d]] + 10.0
                eta = dist / max(float(self.api.vehicle.getSpeed(vid)), 5.0)
                if eta <= self.p["ev_eta_s"] and (best is None or eta < best[2]):
                    best = (vid, d, eta)
                if road == APPROACH[d] and float(self.api.vehicle.getSpeed(vid)) < QUEUE_SPEED:
                    self.ev_wait[vid] = self.ev_wait.get(vid, 0.0) + 1.0
            except Exception:
                continue
        for vid in [v for v in self.ev_rx if v not in active]:
            self.ev_rx.pop(vid, None)
        return best

    # ------------------------------------------------------------- mode changes
    def _event(self, kind: str, detail: str, tls: str = "ZE") -> None:
        self.events.append({"kind": kind, "tls": tls, "detail": detail})

    def activate(self, t: float) -> None:
        if self.mode != "normal":
            return
        self.mode = self.control
        self.activated_at = t
        self.deactivate_requested = False
        if self.control == "alternating":
            # Eastbound traffic was flowing: give it a yellow, then clear the lane before the first turn
            self._start_phase("yellow", "E", t)
            self._event("zone_activated", "Accident on J3J2: alternating one-way traffic on the eastbound "
                                          "outer lane (signals at both ends of the zone)")
        else:
            self.phase = "closure"
            self._event("zone_activated", "Accident on J3J2: westbound road closed at J3, traffic detours")
        self._set_travel_times()
        self._apply_signals()

    def _to_normal(self, t: float) -> None:
        self.mode = "normal"
        self.phase = "normal"
        self.dir = None
        self.deactivated_at = t
        self.deactivate_requested = False
        self.ev_request = None
        self._set_travel_times()
        self._apply_signals()
        self._event("zone_deactivated", "Accident cleared: both carriageways open, zone signals off")

    def _start_phase(self, phase: str, d: Optional[str], t: float) -> None:
        self.phase = phase
        self.dir = d
        self.phase_start = t
        if phase == "green" and d is not None:
            self.green_allotted = min(self.p["max_green"],
                                      self.p["min_green"] + self.p["green_per_vehicle_s"] * self.queue[d])
            self.red_since[d] = None
            if self.red_since[OTHER[d]] is None:
                self.red_since[OTHER[d]] = t
            self.last_served = d
            self.switches += 1
            self._event("zone_switch", f"{DIRECTION_LABEL[d].capitalize()} turn on the shared lane "
                                       f"({self.queue[d]} waiting, green up to {self.green_allotted:.0f} s)",
                        ENTRY_TLS[d])
        elif phase == "clear":
            for dd in ("E", "W"):
                if self.red_since[dd] is None:
                    self.red_since[dd] = t

    def _choose_next(self) -> str:
        if self.ev_request is not None:
            return self.ev_request[1]
        last = self.last_served
        if last is None:
            return "W" if self.queue["W"] + self.approaching["W"] > 0 else "E"
        other = OTHER[last]
        demand = {d: self.queue[d] + self.approaching[d] for d in ("E", "W")}
        if demand[other] == 0 and demand[last] > 0:
            return last
        return other

    # ------------------------------------------------------------------ step
    def step(self, t: float, active: List[str], tmc_incidents: Dict[str, str]) -> None:
        """One simulated second. tmc_incidents: the TMC incident map (edge -> kind)."""
        # On when the TMC reports the carriageway fully blocked (a partial flag can be a jam), off
        # when the TMC has cleared it
        if self.mode == "normal" and tmc_incidents.get(BLOCKED_EDGE) == "edge_blocked":
            self.activate(t)
        elif self.mode != "normal" and BLOCKED_EDGE not in tmc_incidents:
            self.deactivate_requested = True

        self._count_occupancy(active)
        if self.occupancy["E"] > 0 and self.occupancy["W"] > 0:
            self.conflicts += 1
            logger.error("Zone safety: both directions inside the shared lane at %.0fs (%s)", t, self.occupancy)
        if self.mode == "normal":
            if self.deactivated_at is not None and t - self.deactivated_at <= 120.0:
                # Routes still through the contraflow lane go back to the westbound carriageway
                self._replace_in_routes(active, CONTRAFLOW_EDGE, BLOCKED_EDGE)
            return

        if self.mode == "closure":
            self._true_queues()
            if self.deactivate_requested:
                self._to_normal(t)
            else:
                self._closure_detours(active)
            return

        self._fix_guided_routes()
        self._sense_queues(t, active)
        self._replace_in_routes(active, BLOCKED_EDGE, CONTRAFLOW_EDGE)

        req = self._ambulance_request(t, active)
        if req is not None and req[0] not in self.ev_priority_ids:
            self.ev_priority_ids.add(req[0])
            self._event("zone_ev_priority", f"Ambulance {req[0]} {DIRECTION_LABEL[req[1]]}, about {req[2]:.0f} s "
                                            f"from the zone: its direction goes next", ENTRY_TLS[req[1]])
        self.ev_request = (req[0], req[1]) if req is not None else None

        el = t - self.phase_start
        if self.phase == "green":
            d = self.dir
            assert d is not None
            other = OTHER[d]
            demand_other = self.queue[other] + self.approaching[other] > 0
            end = False
            if self.deactivate_requested:
                end = True
            elif self.ev_request is not None:
                end = self.ev_request[1] != d  # hold for an ambulance on this side
            elif el >= self.p["min_green"] and demand_other:
                waited = t - self.red_since[other] if self.red_since[other] is not None else 0.0
                if self.approaching[d] == 0 or el >= self.green_allotted or waited >= self.p["starvation_s"]:
                    end = True
            if end:
                if self.deactivate_requested and d == "E":
                    self._to_normal(t)  # the shared lane only has eastbound cars: nothing to clear
                else:
                    self._start_phase("yellow", d, t)
        elif self.phase == "yellow":
            if el >= self.p["yellow"]:
                self._start_phase("clear", None, t)
        elif self.phase == "clear":
            empty = self.occupancy["E"] == 0 and self.occupancy["W"] == 0
            if el > self.p["max_clearance_s"] and el - 1.0 <= self.p["max_clearance_s"]:
                logger.warning("Zone clearance has taken %.0fs (vehicles still in the shared lane)", el)
            if empty and el >= self.p["min_all_red"]:
                self.max_clearance = max(self.max_clearance, el)
                if self.deactivate_requested:
                    self._to_normal(t)
                else:
                    nxt = self._choose_next()
                    if self._guide_trapped(t, active):
                        nxt = "W"  # cars caught behind the crash go first (they also block the westbound entry)
                    self._start_phase("green", nxt, t)
        self._set_travel_times()
        self._apply_signals()

    # ---------------------------------------------------------------- output
    def pop_events(self) -> List[Dict[str, Any]]:
        out, self.events = self.events, []
        return out

    def live_block(self, t: float) -> Dict[str, Any]:
        """Zone state for the dashboard (state message, key "zone")."""
        waits = {d: (round(t - self.red_since[d]) if self.red_since[d] is not None and self.mode == "alternating"
                     else 0) for d in ("E", "W")}
        return {
            "mode": self.mode,
            "control": self.control,
            "phase": self.phase,
            "dir": self.dir,
            "signal": {d: self.entry_signal(d) for d in ("E", "W")},
            "queue": dict(self.queue),
            "in_zone": dict(self.occupancy),
            "red_for_s": waits,
            "expected_wait": {d: self.expected_wait(d) for d in ("E", "W")},
            "ev": self.ev_request[0] if self.ev_request else None,
            "switches": self.switches,
            "conflicts": self.conflicts,
        }

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "zone_control": self.control,
            "zone_activated_at": self.activated_at,
            "zone_deactivated_at": self.deactivated_at,
            "zone_switches": self.switches,
            "zone_conflicts": self.conflicts,
            "zone_max_clearance_s": round(self.max_clearance, 1),
            "zone_passed_eastbound": len(self.passed["E"]),
            "zone_passed_westbound": len(self.passed["W"]),
            "zone_max_queue_eastbound": self.true_max_queue["E"],
            "zone_max_queue_westbound": self.true_max_queue["W"],
            "zone_rerouted": len(self.rerouted),
            "zone_guided": len(self.guided),
            "zone_ev_priority": len(self.ev_priority_ids),
            "zone_ev_wait_s": max(self.ev_wait.values()) if self.ev_wait else 0.0,
        }
