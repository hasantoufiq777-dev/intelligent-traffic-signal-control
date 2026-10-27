"""Traffic monitor for one signalised junction: what each incoming road looks like right now.

Used by the dynamic controller (sim/controllers/dynamic.py). For every approach (N, S, E, W) it
measures, once per simulated second:

- waiting   vehicles stopped or nearly stopped (speed < 1 m/s) on the approach lanes (the queue)
- queue_m   queue length: distance from the stop line to the last stopped vehicle (m)
- vehicles  all vehicles on the approach lanes (moving and stopped)
- density   vehicles per km per lane
- mean_wait_s / max_wait_s   waiting time of the stopped vehicles (s)
- flow_vpm  arrival flow: vehicles that entered the approach in the last 60 s, per minute
- down_occ  occupancy of the roads the approach feeds (0 = empty, 1 = full), from the vehicles on
            them and their storage space (lane length / jam spacing). A closed road counts as full.
- down_space  vehicles that still fit on those roads
- down_status "clear" | "busy" | "blocked"

Two sources, the same measures:
- exact data from SUMO (lanes and vehicles), for the ``dynamic`` controller;
- the junction's roadside unit (RSU) for ``dynamic_vanet``: only the beacons of equipped cars
  that reached it, scaled by 1/penetration as the RSU queue estimate is (sim/vanet/nodes.py).

Static road data (which lanes feed which roads, lane lengths) comes from the network, as a
digital map would; only the traffic itself is sensed.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

DIRECTIONS = ("N", "S", "E", "W")
HALT_SPEED = 1.0  # m/s: slower than this counts as waiting (the RSU queue threshold)
FLOW_WINDOW_S = 60.0
JAM_DENSITY = 1000.0 / 7.5  # vehicles per km per lane when queued bumper to bumper (7.5 m per vehicle)


# Everything measured on one incoming road in one second; the controllers decide from this record.
@dataclass
class ApproachStats:
    """Traffic on one incoming approach of a junction."""

    direction: str
    roads: List[str]
    lanes: int
    waiting: float = 0.0
    queue_m: float = 0.0
    vehicles: float = 0.0
    density: float = 0.0
    mean_wait_s: float = 0.0
    max_wait_s: float = 0.0
    flow_vpm: float = 0.0
    near_stopline: float = 0.0  # vehicles within the gap distance of the stop line (moving or not)
    approaching: float = 0.0  # moving vehicles within approach_m of the stop line
    approach_eta_s: Optional[float] = None  # seconds until the first of them reaches the stop line
    stopline_occupied: bool = False  # the stop-line detector sees a stopped vehicle
    down_roads: Optional[List[str]] = None
    down_occ: float = 0.0
    down_worst: str = ""  # the fullest downstream road
    down_space: float = 0.0
    down_status: str = "clear"
    closed: bool = False  # the approach itself is closed by an incident (no flow possible)

    # Convert the measurements to a plain dictionary.
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# The junction's sensor layer: turns exact SUMO data or VANET beacons into one ApproachStats per road.
class TrafficMonitor:
    """Per-approach traffic measures for one junction, from SUMO or from its RSU."""

    # Read the junction's static map once: incoming lanes per direction, the roads they feed,
    # how many movements go to each, and lane lengths. Also prepare the arrival-flow counters.
    def __init__(self, tls_id: str, sumo_api: Any, link_directions: List[str], provider: Any = None,
                 params: Optional[Dict[str, Any]] = None) -> None:
        p = dict(params or {})
        self.tls_id = tls_id
        self.api = sumo_api
        self.provider = provider
        self.jam_spacing_m = float(p.get("jam_spacing_m", 7.5))
        self.gap_dist_m = float(p.get("gap_dist_m", 30.0))
        self.approach_m = float(p.get("approach_m", 150.0))
        # VANET runs: a stop-line detector also reports a stopped car, so a car without a radio is not stranded
        self.stopline_detector = bool(p.get("stopline_detector", False))
        self._occupied_since: Dict[str, float] = {}
        self.busy_occ = float(p.get("down_busy_occ", 0.4))
        self.blocked_occ = float(p.get("down_blocked_occ", 0.75))

        # Static map: incoming lanes and the roads each approach feeds
        self.in_lanes: Dict[str, List[str]] = {d: [] for d in DIRECTIONS}
        self.out_lanes: Dict[str, List[str]] = {d: [] for d in DIRECTIONS}
        # Movement share: links from an approach into each downstream road (straight ahead usually most)
        self.out_weight: Dict[str, Dict[str, int]] = {d: {} for d in DIRECTIONS}
        self.lane_dir: Dict[str, str] = {}
        self.lane_len: Dict[str, float] = {}
        links: List[Any] = []
        try:
            links = list(sumo_api.trafficlight.getControlledLinks(tls_id)) if sumo_api else []
        except Exception:
            links = []
        for idx, link_list in enumerate(links):
            if not link_list or idx >= len(link_directions):
                continue
            d = link_directions[idx]
            if d not in self.in_lanes:
                continue
            for link in link_list:
                if not link or len(link) < 2:
                    continue
                inl, outl = str(link[0]), str(link[1])
                if inl and inl not in self.lane_dir:
                    self.lane_dir[inl] = d
                    self.in_lanes[d].append(inl)
                if outl and not outl.startswith(":"):
                    if outl not in self.out_lanes[d]:
                        self.out_lanes[d].append(outl)
                    e = self.edge_of(outl)
                    self.out_weight[d][e] = self.out_weight[d].get(e, 0) + 1
        for lane in list(self.lane_dir) + [l for ls in self.out_lanes.values() for l in ls]:
            if lane not in self.lane_len:
                try:
                    self.lane_len[lane] = float(sumo_api.lane.getLength(lane))
                except Exception:
                    self.lane_len[lane] = 100.0
        self.out_lane_set: Set[str] = {l for ls in self.out_lanes.values() for l in ls}

        # Arrival flow: vehicles seen on each approach, and when new ones arrived
        self._seen: Dict[str, Dict[str, float]] = {d: {} for d in DIRECTIONS}
        self._arrivals: Dict[str, Deque[float]] = {d: deque() for d in DIRECTIONS}
        self._t0: Optional[float] = None

    # Road id of a lane id: 'J0J1_1' -> 'J0J1'.
    @staticmethod
    def edge_of(lane: str) -> str:
        return lane.rsplit("_", 1)[0]

    # Incoming road ids of one direction (N, S, E or W).
    def roads(self, d: str) -> List[str]:
        return list(dict.fromkeys(self.edge_of(l) for l in self.in_lanes.get(d, [])))

    # Directions that really exist here, so T-junctions and unusual networks also work.
    def directions(self) -> List[str]:
        """Approaches that exist at this junction (have incoming lanes)."""
        return [d for d in DIRECTIONS if self.in_lanes[d]]

    # ------------------------------------------------------------------ observation sources
    # Exact sensing (`dynamic`, `level`): read every vehicle on the incoming lanes and count the
    # outgoing lanes directly from SUMO. Scale is 1 because no vehicle is missed.
    def _observe_sumo(self) -> Tuple[Dict[str, List[Tuple[str, float, float, float]]], Dict[str, Tuple[float, float]], float]:
        """Vehicles on the incoming lanes [(id, distance to stop line, speed, wait)], and per
        outgoing lane (vehicles, stopped vehicles). Exact data: scale 1."""
        api = self.api
        veh: Dict[str, List[Tuple[str, float, float, float]]] = {d: [] for d in DIRECTIONS}
        for lane, d in self.lane_dir.items():
            try:
                ids = api.lane.getLastStepVehicleIDs(lane)
            except Exception:
                continue
            length = self.lane_len.get(lane, 100.0)
            for vid in ids:
                try:
                    speed = float(api.vehicle.getSpeed(vid))
                    dist = max(0.0, length - float(api.vehicle.getLanePosition(vid)))
                    wait = float(api.vehicle.getWaitingTime(vid)) if speed < HALT_SPEED else 0.0
                except Exception:
                    continue
                veh[d].append((str(vid), dist, speed, wait))
        out: Dict[str, Tuple[float, float]] = {}
        for lane in self.out_lane_set:
            try:
                out[lane] = (float(api.lane.getLastStepVehicleNumber(lane)),
                             float(api.lane.getLastStepHaltingNumber(lane)))
            except Exception:
                out[lane] = (0.0, 0.0)
        return veh, out, 1.0

    # VANET sensing (`dynamic_vanet`, `level_vanet`): use only beacons this roadside unit received.
    # Unequipped, out-of-range and lost-message cars are invisible, so counts are later scaled by
    # 1 / penetration (at 50 % connected, one beacon counts as two cars).
    def _observe_rsu(self, rsu: Any, t: float) -> Tuple[Dict[str, List[Tuple[str, float, float, float]]], Dict[str, Tuple[float, float]], float]:
        """The same from the beacons the RSU has received (equipped cars in range only)."""
        try:
            rsu.prune_stale(t)
        except Exception:
            pass
        veh: Dict[str, List[Tuple[str, float, float, float]]] = {d: [] for d in DIRECTIONS}
        out: Dict[str, List[float]] = {}
        for vid, b in list(rsu.received_beacons.items()):
            lane = str(getattr(b, "lane_id", "") or "")
            speed = float(getattr(b, "speed", 0.0) or 0.0)
            if lane in self.lane_dir:
                pos = getattr(b, "pos", (0.0, 0.0))
                dist = math.hypot(pos[0] - rsu.pos[0], pos[1] - rsu.pos[1])
                since = rsu.vehicle_stopped_since.get(vid)
                wait = max(0.0, t - since) if (since is not None and speed < HALT_SPEED) else 0.0
                veh[self.lane_dir[lane]].append((str(vid), dist, speed, wait))
            elif lane in self.out_lane_set:
                c = out.setdefault(lane, [0.0, 0.0])
                c[0] += 1.0
                if speed < HALT_SPEED:
                    c[1] += 1.0
        scale = 1.0 / max(0.01, float(getattr(rsu, "penetration", 1.0)))
        return veh, {k: (v[0], v[1]) for k, v in out.items()}, scale

    # Return this junction's roadside unit when the run uses VANET sensing, otherwise None.
    def _rsu(self) -> Any:
        net = getattr(self.provider, "vanet_net", None) if self.provider is not None else None
        if net is None or getattr(self.provider, "name", "") != "vanet":
            return None
        return net.rsus.get(self.tls_id)

    # ------------------------------------------------------------------ measures
    # Stop-line detector (a loop or camera): tells only that a car is stopped here, so an otherwise
    # invisible car without a radio counts as one waiting vehicle and its wait is timed.
    def _apply_stopline_detector(self, s: ApproachStats, d: str, lanes: List[str], t: float) -> None:
        try:
            occupied = any(self.api.lane.getLastStepHaltingNumber(lane) > 0 for lane in lanes)
        except Exception:
            occupied = False
        if not occupied:
            self._occupied_since.pop(d, None)
            return
        since = self._occupied_since.setdefault(d, t)
        s.stopline_occupied = True
        s.waiting = max(s.waiting, 1.0)
        s.vehicles = max(s.vehicles, 1.0)
        s.near_stopline = max(s.near_stopline, 1.0)
        s.max_wait_s = max(s.max_wait_s, t - since)

    # Main sensing step, once per second: choose the source, then compute each road's queue, density,
    # waiting times, arrival flow and how full its downstream roads are.
    def observe(self, t: float, closed_approaches: Optional[Set[str]] = None,
                incidents: Optional[Dict[str, str]] = None) -> Dict[str, ApproachStats]:
        """Measure every approach now. closed_approaches: approaches whose incoming road is closed;
        incidents: edge -> 'edge_blocked' | 'lane_blocked' (closed roads downstream count as full)."""
        if self._t0 is None:
            self._t0 = float(t)
        rsu = self._rsu()
        veh, out, scale = self._observe_rsu(rsu, t) if rsu is not None else self._observe_sumo()
        closed_edges = {e for e, k in (incidents or {}).items() if k == "edge_blocked"}
        stats: Dict[str, ApproachStats] = {}
        for d in self.directions():
            lanes = self.in_lanes[d]
            n_lanes = max(1, len(lanes))
            length_km = max(0.01, sum(self.lane_len.get(l, 100.0) for l in lanes) / 1000.0)
            records = veh.get(d, [])
            halted = [r for r in records if r[2] < HALT_SPEED]
            waits = [r[3] for r in halted]
            s = ApproachStats(direction=d, roads=self.roads(d), lanes=n_lanes,
                              closed=bool(closed_approaches and d in closed_approaches))
            s.vehicles = len(records) * scale
            s.waiting = len(halted) * scale
            s.queue_m = max((r[1] for r in halted), default=0.0)
            s.density = s.vehicles / length_km  # length_km sums all lanes: vehicles per km per lane
            s.mean_wait_s = (sum(waits) / len(waits)) if waits else 0.0
            s.max_wait_s = max(waits, default=0.0)
            s.near_stopline = sum(1 for r in records if r[1] <= self.gap_dist_m) * scale
            coming = [r for r in records if r[2] >= HALT_SPEED and r[1] <= self.approach_m]
            s.approaching = len(coming) * scale
            s.approach_eta_s = min((r[1] / r[2] for r in coming), default=None)
            if rsu is not None and self.stopline_detector:
                self._apply_stopline_detector(s, d, lanes, float(t))

            # Arrival flow over the last minute
            seen = self._seen[d]
            arrivals = self._arrivals[d]
            now_ids = {r[0] for r in records}
            for vid in now_ids:
                if vid not in seen:
                    arrivals.append(float(t))
                seen[vid] = float(t)
            for vid in [v for v, ts in seen.items() if t - ts > FLOW_WINDOW_S]:
                seen.pop(vid, None)
            while arrivals and t - arrivals[0] > FLOW_WINDOW_S:
                arrivals.popleft()
            window = min(FLOW_WINDOW_S, max(10.0, float(t) - self._t0 + 1.0))
            s.flow_vpm = len(arrivals) * scale * 60.0 / window

            # Downstream roads: how full each is, weighted by the approach's movements into it
            per_edge: Dict[str, List[float]] = {}
            for lane in self.out_lanes[d]:
                e = self.edge_of(lane)
                c = self.lane_len.get(lane, 100.0) / self.jam_spacing_m
                n = c if e in closed_edges else out.get(lane, (0.0, 0.0))[0] * scale
                acc = per_edge.setdefault(e, [0.0, 0.0])
                acc[0] += c
                acc[1] += min(n, c)
            weights = self.out_weight[d]
            w_sum = sum(weights.get(e, 1) for e in per_edge) or 1
            s.down_roads = list(per_edge)
            if per_edge:
                s.down_worst = max(per_edge, key=lambda e: per_edge[e][1] / per_edge[e][0] if per_edge[e][0] else 0.0)
            s.down_occ = sum(weights.get(e, 1) * (u / c if c > 0 else 0.0) for e, (c, u) in per_edge.items()) / w_sum
            s.down_space = sum(weights.get(e, 1) * max(0.0, c - u) for e, (c, u) in per_edge.items()) / w_sum
            s.down_status = ("blocked" if s.down_occ >= self.blocked_occ
                             else "busy" if s.down_occ >= self.busy_occ else "clear")
            stats[d] = s
        return stats
