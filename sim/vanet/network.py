"""VANET network orchestration and execution engine.

Coordinates vehicle beaconing, wireless transmission through the channel,
message delivery, RSU local state estimation, backbone sharing with the TMC,
and real-time communication statistics tracking.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from sim.vanet.channel import WirelessChannel
from sim.vanet.messages import BeaconMessage
from sim.vanet.nodes import RSUNode, TMC, is_vehicle_equipped

logger = logging.getLogger(__name__)


def resolve_lane_direction(lane_id: str, tls_id: str, sumo_api: Any = None) -> str:
    """Determine approach direction (N, S, E, W) dynamically using geometry and naming."""
    edge_id = lane_id.split("_")[0]
    if edge_id.startswith("N2") or edge_id.startswith("top") or "top" in edge_id or edge_id in ("J2J0", "J3J1"):
        return "N"
    elif edge_id.startswith("S2") or edge_id.startswith("bottom") or "bottom" in edge_id or edge_id in ("J0J2", "J1J3"):
        return "S"
    elif edge_id.startswith("E2") or edge_id.startswith("right") or "right" in edge_id or edge_id in ("J1J0", "J3J2"):
        return "E"
    elif edge_id.startswith("W2") or edge_id.startswith("left") or "left" in edge_id or edge_id in ("J0J1", "J2J3"):
        return "W"

    if sumo_api:
        try:
            shape = sumo_api.lane.getShape(lane_id)
            if len(shape) >= 2:
                p1, p2 = shape[0], shape[-1]
                dx, dy = p1[0] - p2[0], p1[1] - p2[1]
                if abs(dy) >= abs(dx):
                    return "N" if dy > 0 else "S"
                else:
                    return "E" if dx > 0 else "W"
        except Exception:
            pass

    return "N"


class VanetNetwork:
    """Orchestrates emulated VANET communications for vehicles, RSUs, and the TMC."""

    def __init__(
        self,
        sumo_api: Any,
        tls_ids: List[str],
        config: Dict[str, Any],
        seed: int = 1,
        penetration_override: Optional[float] = None,
        loss_override: Optional[float] = None,
        range_override: Optional[float] = None,
    ) -> None:
        """Initialize VANET environment from configuration."""
        self.sumo_api = sumo_api
        self.tls_ids = list(tls_ids)
        self.config = dict(config or {})
        self.seed = int(seed)

        # Communication parameters
        self.penetration = (
            float(penetration_override)
            if penetration_override is not None
            else float(self.config.get("penetration", 1.0))
        )
        range_m = (
            float(range_override)
            if range_override is not None
            else float(self.config.get("range", 300.0))
        )
        base_loss = (
            float(loss_override)
            if loss_override is not None
            else float(self.config.get("base_loss", 0.0))
        )
        max_loss = float(self.config.get("max_loss", 0.2))
        base_delay_ms = float(self.config.get("base_delay_ms", 10.0))
        jitter_ms = float(self.config.get("jitter_ms", 5.0))
        self.beacon_interval = float(self.config.get("beacon_interval", 1.0))
        self.stale_timeout = float(self.config.get("stale_timeout", 3.0))
        self.queue_speed_threshold = float(self.config.get("queue_speed_threshold", 1.0))
        self.queue_zone_length = float(self.config.get("queue_zone_length", 100.0))
        backbone_delay_ms = float(self.config.get("backbone_delay_ms", 5.0))
        self.gps_sigma_m = float(self.config.get("gps_sigma_m", 0.0))

        # Channel & Node instances
        self.channel = WirelessChannel(
            range_m=range_m,
            base_loss=base_loss,
            max_loss=max_loss,
            base_delay_ms=base_delay_ms,
            jitter_ms=jitter_ms,
            seed=self.seed,
        )
        self.tmc = TMC(backbone_delay_ms=backbone_delay_ms)
        self.rsus: Dict[str, RSUNode] = {}
        self.last_beacon_time: Dict[str, float] = {}
        self.current_sim_time: float = 0.0
        self.rng = random.Random(self.seed + 999)

        # Initialize RSUs
        for tid in self.tls_ids:
            pos = (0.0, 0.0)
            if self.sumo_api:
                try:
                    pos = self.sumo_api.junction.getPosition(tid)
                except Exception:
                    pass

            lanes: List[str] = []
            lane_dirs: Dict[str, str] = {}
            edge_dirs: Dict[str, str] = {}
            if self.sumo_api:
                try:
                    lanes = list(dict.fromkeys(self.sumo_api.trafficlight.getControlledLanes(tid)))
                    for lid in lanes:
                        d = resolve_lane_direction(lid, tid, self.sumo_api)
                        lane_dirs[lid] = d
                        eid = lid.split("_")[0]
                        edge_dirs[eid] = d
                except Exception as e:
                    logger.warning("Could not read controlled lanes for RSU %s: %s", tid, e)

            self.rsus[tid] = RSUNode(
                tls_id=tid,
                pos=pos,
                range_m=range_m,
                stale_timeout=self.stale_timeout,
                queue_speed_threshold=self.queue_speed_threshold,
                queue_zone_length=self.queue_zone_length,
                penetration=self.penetration,
                controlled_lanes=lanes,
                lane_directions=lane_dirs,
                edge_directions=edge_dirs,
            )

    def step(self, sim_time: float) -> None:
        """Execute one simulation second of the VANET emulation."""
        self.current_sim_time = float(sim_time)

        # 1. Transmit beacons from active equipped vehicles
        if self.sumo_api:
            try:
                active_vids = list(self.sumo_api.vehicle.getIDList())
            except Exception:
                active_vids = []

            for vid in active_vids:
                try:
                    vtype = self.sumo_api.vehicle.getTypeID(vid)
                except Exception:
                    vtype = "car"

                if not is_vehicle_equipped(vid, vtype=vtype, penetration=self.penetration, seed=self.seed):
                    continue

                last_t = self.last_beacon_time.get(vid, -999.0)
                if (sim_time - last_t) < self.beacon_interval:
                    continue

                try:
                    pos = self.sumo_api.vehicle.getPosition(vid)
                    if self.gps_sigma_m > 0.0:
                        pos = (
                            pos[0] + self.rng.gauss(0.0, self.gps_sigma_m),
                            pos[1] + self.rng.gauss(0.0, self.gps_sigma_m),
                        )
                    speed = float(self.sumo_api.vehicle.getSpeed(vid))
                    edge_id = str(self.sumo_api.vehicle.getRoadID(vid))
                    lane_id = str(self.sumo_api.vehicle.getLaneID(vid))
                except Exception:
                    continue

                beacon = BeaconMessage(
                    msg_id=f"b_{vid}_{sim_time:.1f}",
                    sender_id=vid,
                    pos=pos,
                    speed=speed,
                    edge_id=edge_id,
                    lane_id=lane_id,
                    vtype=vtype,
                    timestamp=sim_time,
                )

                # Transmit to all RSUs within RF range
                for rsu in self.rsus.values():
                    dist = math.hypot(pos[0] - rsu.pos[0], pos[1] - rsu.pos[1])
                    if dist <= self.channel.range_m:
                        self.channel.transmit(
                            message=beacon,
                            sender_pos=pos,
                            receiver_pos=rsu.pos,
                            receiver_id=rsu.tls_id,
                            current_time=sim_time,
                        )

                self.last_beacon_time[vid] = sim_time

        # 2. Deliver messages scheduled for or before sim_time
        due_transmissions = self.channel.get_due_messages(sim_time)
        for msg, rsu_id, latency_ms in due_transmissions:
            if rsu_id in self.rsus and isinstance(msg, BeaconMessage):
                self.rsus[rsu_id].receive_beacon(msg, sim_time)

        # 3. RSUs prune stale records and update TMC via backbone
        for rsu in self.rsus.values():
            speeds = rsu.estimate_edge_speeds(sim_time)
            self.tmc.receive_rsu_report(speeds)

    def get_rsu_queue_estimate(self, tls_id: str) -> Dict[str, int]:
        """Return RSU approach queue estimate for the requested junction."""
        rsu = self.rsus.get(tls_id)
        if not rsu:
            return {"N": 0, "S": 0, "E": 0, "W": 0}
        return rsu.estimate_queues(self.current_sim_time)

    def get_rsu_waiting_times(self, tls_id: str) -> Dict[str, float]:
        """Return RSU approach waiting time estimate for the requested junction."""
        rsu = self.rsus.get(tls_id)
        if not rsu:
            return {"N": 0.0, "S": 0.0, "E": 0.0, "W": 0.0}
        return rsu.estimate_waiting_times(self.current_sim_time)

    def get_rsu_ev_info(self, tls_id: str) -> List[Dict[str, Any]]:
        """Return EV alert info received by this RSU (implemented in Module 8)."""
        return []

    def get_comm_stats(self) -> Dict[str, Any]:
        """Return communication statistics dictionary matching protocol v2 schema."""
        sent = self.channel.stats["sent"]
        lost = self.channel.stats["lost"]
        pdr = round(float(self.channel.get_pdr()), 3) if sent > 0 else 1.0
        lat_ms = round(float(self.channel.get_avg_latency_ms()), 1)
        return {
            "pdr": pdr,
            "latency_ms": lat_ms,
            "penetration": self.penetration,
            "sent": sent,
            "lost": lost,
        }
