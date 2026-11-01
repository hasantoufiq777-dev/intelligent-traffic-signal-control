"""Nodes and infrastructure components for VANET emulation.

Includes:
- OBU equipped-vehicle selection via deterministic seeded hashing.
- RSUNode: Roadside Unit per junction with local telemetry caching, stale-beacon
  expiry, approach queue estimation scaled by 1/penetration, and local edge speed estimation.
- TMC: Traffic Management Center holding the network-wide traffic map.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from sim.vanet.messages import BeaconMessage, EvAlertMessage, IncidentFlagMessage


def is_vehicle_equipped(
    vehicle_id: str,
    vtype: str = "car",
    penetration: float = 1.0,
    seed: int = 1,
) -> bool:
    """Determine if a vehicle is equipped with an On-Board Unit (OBU).

    Emergency vehicles are always equipped (100% penetration).
    Other vehicles are selected deterministically using a seeded cryptographic hash.
    """
    if vtype == "emergency" or "amb" in vehicle_id.lower() or "ev" in vehicle_id.lower():
        return True
    if penetration >= 1.0:
        return True
    if penetration <= 0.0:
        return False

    # Seeded deterministic uniform hash in [0.0, 1.0)
    token = f"{seed}:obu:{vehicle_id}".encode("utf-8")
    digest = hashlib.sha256(token).hexdigest()
    normalized_val = int(digest[:8], 16) / 0xFFFFFFFF
    return normalized_val < penetration


EdgeInfo = Dict[str, Tuple[str, float]]  # edge id -> (downstream junction id, length in m)


def build_edge_info(sumo_api: Any) -> EdgeInfo:
    """Static map data: downstream junction and length of every normal edge."""
    info: EdgeInfo = {}
    if not sumo_api:
        return info
    try:
        edge_ids = list(sumo_api.edge.getIDList())
    except Exception:
        return info
    for eid in edge_ids:
        if eid.startswith(":"):
            continue
        try:
            info[eid] = (str(sumo_api.edge.getToJunction(eid)), float(sumo_api.lane.getLength(f"{eid}_0")))
        except Exception:
            continue
    return info


def internal_lane_junction(edge_or_lane: str) -> Optional[str]:
    """Junction id of an internal edge/lane such as ':J1_10_0' -> 'J1'; None for normal edges."""
    if not edge_or_lane.startswith(":"):
        return None
    return edge_or_lane[1:].split("_")[0]  # junction ids in our networks contain no "_"


def distance_along_route(
    tls_id: str,
    route: List[str],
    route_index: int,
    current_edge: str,
    lane_pos: float,
    edge_info: EdgeInfo,
    tls_ids: Optional[Set[str]] = None,
) -> Optional[Tuple[float, str, int]]:
    """Distance (m) a vehicle still has to drive to the stop line of junction tls_id.

    Returns (distance, incoming edge, number of signalised junctions before it), or None if
    tls_id is not ahead on the remaining route (already passed, or not on the route at all).
    A vehicle inside tls_id (on one of its internal lanes) gets distance 0.
    """
    if not route or route_index < 0 or route_index >= len(route):
        return None
    passed_tls = 0
    if current_edge.startswith(":"):
        # Inside a junction. The junction comes from the internal lane id, because SUMO may
        # already have advanced route_index to the edge after the junction.
        crossing = internal_lane_junction(current_edge)
        k = next((i for i in range(max(0, route_index - 1), len(route))
                  if edge_info.get(route[i], ("", 0.0))[0] == crossing), None)
        if k is None:
            return None
        if crossing == tls_id:
            return 0.0, route[k], 0
        dist = 0.0
        start = k + 1
    else:
        idx = route_index
        if route[idx] != current_edge and current_edge in route[idx:]:
            idx = route.index(current_edge, idx)
        to_j, length = edge_info.get(route[idx], ("", 0.0))
        remaining = max(0.0, length - float(lane_pos))
        if to_j == tls_id:
            return remaining, route[idx], 0
        if tls_ids is None or to_j in tls_ids:
            passed_tls += 1
        dist = remaining
        start = idx + 1
    for edge in route[start:]:
        if edge not in edge_info:
            return None
        to_j, length = edge_info[edge]
        dist += length
        if to_j == tls_id:
            return dist, edge, passed_tls
        if tls_ids is None or to_j in tls_ids:
            passed_tls += 1
    return None


class RSUNode:
    """Roadside Unit stationed at a signalized intersection."""

    def __init__(
        self,
        tls_id: str,
        pos: Tuple[float, float],
        range_m: float = 300.0,
        stale_timeout: float = 3.0,
        queue_speed_threshold: float = 1.0,
        queue_zone_length: float = 100.0,
        penetration: float = 1.0,
        controlled_lanes: Optional[List[str]] = None,
        lane_directions: Optional[Dict[str, str]] = None,
        edge_directions: Optional[Dict[str, str]] = None,
    ) -> None:
        """Initialize RSU node with intersection geometry and estimator thresholds."""
        self.tls_id = str(tls_id)
        self.pos = (float(pos[0]), float(pos[1]))
        self.range_m = float(range_m)
        self.stale_timeout = float(stale_timeout)
        self.queue_speed_threshold = float(queue_speed_threshold)
        self.queue_zone_length = float(queue_zone_length)
        self.penetration = max(0.01, min(1.0, float(penetration)))

        self.controlled_lanes: List[str] = list(controlled_lanes or [])
        self.lane_directions: Dict[str, str] = dict(lane_directions or {})
        self.edge_directions: Dict[str, str] = dict(edge_directions or {})

        # Active telemetry state
        self.received_beacons: Dict[str, BeaconMessage] = {}
        self.beacon_arrival_times: Dict[str, float] = {}
        self.vehicle_stopped_since: Dict[str, float] = {}
        self.ev_alerts: Dict[str, EvAlertMessage] = {}

        # Incident detection (Module 10). Off until configure_incident_detection() is called.
        self.incident_detection_enabled: bool = False
        self.incident_detect_time: float = 30.0
        self.incident_clear_time: float = 10.0
        self.incident_stopline_exclusion: float = 30.0
        self.incident_min_vehicles: int = 1
        # Static digital-map data: edge id -> (downstream end coordinate, number of lanes)
        self.edge_map_info: Dict[str, Tuple[Tuple[float, float], int]] = {}
        # vid -> (edge_id, lane_id, time the vehicle was first seen stopped on that edge)
        self.incident_stop_since: Dict[str, Tuple[str, str, float]] = {}
        # edge_id -> kind ("lane_blocked" | "edge_blocked") currently flagged by this RSU
        self.flagged_edges: Dict[str, str] = {}
        # edge_id -> last time incident evidence was seen on it
        self.flag_last_evidence: Dict[str, float] = {}

    def receive_beacon(self, beacon: BeaconMessage, current_time: float) -> None:
        """Process incoming vehicle telemetry beacon."""
        vid = beacon.sender_id
        self.received_beacons[vid] = beacon
        self.beacon_arrival_times[vid] = current_time

        # Update vehicle stopped timestamp for wait-time tracking
        if beacon.speed <= self.queue_speed_threshold:
            if vid not in self.vehicle_stopped_since:
                self.vehicle_stopped_since[vid] = current_time
        else:
            self.vehicle_stopped_since.pop(vid, None)

        # Incident tracking: how long has this vehicle been stopped on its current edge?
        if self.incident_detection_enabled:
            edge = beacon.edge_id
            if beacon.speed <= self.queue_speed_threshold and edge and not edge.startswith(":"):
                prev = self.incident_stop_since.get(vid)
                if prev is None or prev[0] != edge:
                    self.incident_stop_since[vid] = (edge, beacon.lane_id, current_time)
            else:
                self.incident_stop_since.pop(vid, None)

    def receive_ev_alert(self, alert: EvAlertMessage, current_time: float) -> None:
        """Process emergency vehicle alert."""
        self.ev_alerts[alert.ev_id] = alert

    def prune_stale(self, current_time: float) -> None:
        """Remove beacons older than stale_timeout."""
        expired_vids = [
            vid
            for vid, t_arr in self.beacon_arrival_times.items()
            if (current_time - t_arr) > self.stale_timeout
        ]
        for vid in expired_vids:
            self.received_beacons.pop(vid, None)
            self.beacon_arrival_times.pop(vid, None)
            self.vehicle_stopped_since.pop(vid, None)
            self.incident_stop_since.pop(vid, None)

    def estimate_queues(self, current_time: float) -> Dict[str, int]:
        """Estimate queue length per approach (N, S, E, W) scaled by 1/penetration.

        Only counts vehicles within queue_zone_length of the RSU traveling below queue_speed_threshold
        on recognized incoming approaches.
        """
        self.prune_stale(current_time)
        raw_counts = {"N": 0, "S": 0, "E": 0, "W": 0}

        for vid, beacon in self.received_beacons.items():
            if beacon.speed > self.queue_speed_threshold:
                continue

            # Determine incoming approach direction
            direction: Optional[str] = None
            if beacon.lane_id in self.lane_directions:
                direction = self.lane_directions[beacon.lane_id]
            elif beacon.edge_id in self.edge_directions:
                direction = self.edge_directions[beacon.edge_id]

            if not direction or direction not in raw_counts:
                continue

            # Check distance to intersection stop-line / center
            dist = math.hypot(beacon.pos[0] - self.pos[0], beacon.pos[1] - self.pos[1])
            if dist <= self.queue_zone_length:
                raw_counts[direction] += 1

        # Scale by 1 / penetration
        scale = 1.0 / self.penetration
        return {d: int(round(count * scale)) for d, count in raw_counts.items()}

    def estimate_waiting_times(self, current_time: float) -> Dict[str, float]:
        """Estimate maximum waiting time (seconds) of queued vehicles per approach."""
        self.prune_stale(current_time)
        wait_times = {"N": 0.0, "S": 0.0, "E": 0.0, "W": 0.0}

        for vid, stopped_time in self.vehicle_stopped_since.items():
            beacon = self.received_beacons.get(vid)
            if not beacon:
                continue

            direction = self.lane_directions.get(beacon.lane_id) or self.edge_directions.get(beacon.edge_id)
            if direction in wait_times:
                wait_duration = max(0.0, current_time - stopped_time)
                if wait_duration > wait_times[direction]:
                    wait_times[direction] = round(wait_duration, 1)

        return wait_times

    def estimate_edge_speeds(self, current_time: float) -> Dict[str, float]:
        """Estimate mean speed per edge from all active beacons observed on that edge."""
        self.prune_stale(current_time)
        edge_speeds_sum: Dict[str, float] = {}
        edge_counts: Dict[str, int] = {}

        for beacon in self.received_beacons.values():
            eid = beacon.edge_id
            if not eid or eid.startswith(":"):
                continue
            edge_speeds_sum[eid] = edge_speeds_sum.get(eid, 0.0) + beacon.speed
            edge_counts[eid] = edge_counts.get(eid, 0) + 1

        return {eid: round(edge_speeds_sum[eid] / edge_counts[eid], 2) for eid in edge_counts}


    def configure_incident_detection(
        self,
        edge_map_info: Dict[str, Tuple[Tuple[float, float], int]],
        detect_time: float = 30.0,
        clear_time: float = 10.0,
        stopline_exclusion: float = 30.0,
        min_vehicles: int = 1,
    ) -> None:
        """Enable beacon-based incident detection on this RSU."""
        self.incident_detection_enabled = True
        self.edge_map_info = dict(edge_map_info)
        self.incident_detect_time = float(detect_time)
        self.incident_clear_time = float(clear_time)
        self.incident_stopline_exclusion = float(stopline_exclusion)
        self.incident_min_vehicles = max(1, int(min_vehicles))

    def detect_incidents(self, current_time: float) -> List[IncidentFlagMessage]:
        """Raise or clear INCIDENT_FLAGs from the beacons this RSU has received.

        Rule: an edge is flagged when at least incident_min_vehicles equipped vehicles have
        been stopped on it for incident_detect_time seconds, farther than
        incident_stopline_exclusion metres from the edge's downstream end (so ordinary
        red-light queues, whose longest waiters sit at the stop line, are not flagged).
        Stopped vehicles in every lane -> "edge_blocked", otherwise "lane_blocked".
        A flag is cleared after incident_clear_time seconds without such evidence.
        """
        if not self.incident_detection_enabled:
            return []
        self.prune_stale(current_time)

        evidence: Dict[str, Dict[str, Any]] = {}
        for vid, (edge, lane, since) in self.incident_stop_since.items():
            if (current_time - since) < self.incident_detect_time:
                continue
            beacon = self.received_beacons.get(vid)
            info = self.edge_map_info.get(edge)
            if beacon is None or info is None:
                continue
            end_xy = info[0]
            if math.hypot(beacon.pos[0] - end_xy[0], beacon.pos[1] - end_xy[1]) <= self.incident_stopline_exclusion:
                continue
            ev = evidence.setdefault(edge, {"lanes": set(), "n": 0})
            ev["lanes"].add(lane)
            ev["n"] += 1

        messages: List[IncidentFlagMessage] = []
        for edge in sorted(evidence.keys()):
            ev = evidence[edge]
            if ev["n"] < self.incident_min_vehicles:
                continue
            n_lanes = self.edge_map_info[edge][1]
            kind = "edge_blocked" if len(ev["lanes"]) >= n_lanes else "lane_blocked"
            self.flag_last_evidence[edge] = current_time
            previous = self.flagged_edges.get(edge)
            # Send on a new flag or an upgrade to a full blockage (no downgrade while active)
            if previous is None or (previous == "lane_blocked" and kind == "edge_blocked"):
                self.flagged_edges[edge] = kind
                messages.append(IncidentFlagMessage(
                    msg_id=f"inc_{self.tls_id}_{edge}_{current_time:.1f}",
                    timestamp=current_time,
                    edge_id=edge,
                    reason="stopped_vehicles",
                    kind=kind,
                    rsu_id=self.tls_id,
                    lanes_blocked=len(ev["lanes"]),
                    n_lanes=n_lanes,
                    n_vehicles=ev["n"],
                ))

        for edge in sorted(self.flagged_edges.keys()):
            if edge in evidence:
                continue
            if (current_time - self.flag_last_evidence.get(edge, current_time)) >= self.incident_clear_time:
                self.flagged_edges.pop(edge, None)
                self.flag_last_evidence.pop(edge, None)
                messages.append(IncidentFlagMessage(
                    msg_id=f"inc_clr_{self.tls_id}_{edge}_{current_time:.1f}",
                    timestamp=current_time,
                    edge_id=edge,
                    reason="no_stopped_vehicles",
                    kind="cleared",
                    rsu_id=self.tls_id,
                ))
        return messages


class TMC:
    """Traffic Management Center connected to RSUs via wired lossless backbone."""

    _SEVERITY = {"lane_blocked": 1, "edge_blocked": 2}

    def __init__(self, backbone_delay_ms: float = 5.0) -> None:
        self.backbone_delay_ms = float(backbone_delay_ms)
        self.edge_speeds: Dict[str, float] = {}
        self.edge_density: Dict[str, float] = {}
        # Incident map (Module 10): edge -> {rsu_id: kind}
        self.incident_reports: Dict[str, Dict[str, str]] = {}

    def receive_incident_flag(self, msg: IncidentFlagMessage, current_time: float) -> Optional[Dict[str, Any]]:
        """Apply an INCIDENT_FLAG from an RSU. Returns a change record if the map changed."""
        before = self.get_incidents().get(msg.edge_id)
        if msg.kind == "cleared":
            reports = self.incident_reports.get(msg.edge_id, {})
            reports.pop(msg.rsu_id, None)
            if not reports:
                self.incident_reports.pop(msg.edge_id, None)
        else:
            self.incident_reports.setdefault(msg.edge_id, {})[msg.rsu_id] = msg.kind
        after = self.get_incidents().get(msg.edge_id)
        if before == after:
            return None
        return {"edge": msg.edge_id, "before": before, "after": after, "rsu": msg.rsu_id, "t": current_time}

    def get_incidents(self) -> Dict[str, str]:
        """Return edge -> most severe incident kind currently reported by any RSU."""
        return {
            edge: max(by_rsu.values(), key=lambda k: self._SEVERITY.get(k, 0))
            for edge, by_rsu in self.incident_reports.items()
            if by_rsu
        }

    def receive_rsu_report(self, rsu_edge_speeds: Dict[str, float]) -> None:
        """Update global network traffic map from an RSU report."""
        for eid, spd in rsu_edge_speeds.items():
            self.edge_speeds[eid] = spd

    def get_edge_speeds(self) -> Dict[str, float]:
        """Return the current global speed map."""
        return self.edge_speeds.copy()
