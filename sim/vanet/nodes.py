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

from sim.vanet.messages import BeaconMessage, EvAlertMessage


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


class TMC:
    """Traffic Management Center connected to RSUs via wired lossless backbone."""

    def __init__(self, backbone_delay_ms: float = 5.0) -> None:
        self.backbone_delay_ms = float(backbone_delay_ms)
        self.edge_speeds: Dict[str, float] = {}
        self.edge_density: Dict[str, float] = {}

    def receive_rsu_report(self, rsu_edge_speeds: Dict[str, float]) -> None:
        """Update global network traffic map from an RSU report."""
        for eid, spd in rsu_edge_speeds.items():
            self.edge_speeds[eid] = spd

    def get_edge_speeds(self) -> Dict[str, float]:
        """Return the current global speed map."""
        return self.edge_speeds.copy()
