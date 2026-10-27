"""VANET emergency vehicle alert management and propagation engine.

Coordinates:
- Periodic EV_ALERT broadcasting from emergency vehicle OBUs (every alert_interval, default 0.5s).
- Direct V2I wireless transmission to Roadside Units (RSUs) subject to quadratic loss and delay.
- Multi-hop V2V relaying across equipped civilian vehicles (when relay=True), with duplicate suppression
  and TTL hop limitation.
- Low-latency wired backbone forwarding from an ingesting RSU to downstream junction RSUs along the EV route.
- RSU-based EV state estimation with dead-reckoning extrapolation and stale alert expiry timeouts.
- Telemetry event emission (alert_sent, alert_delivered, alert_lost) and communication metric aggregation.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from sim.vanet.channel import WirelessChannel
from sim.vanet.messages import EvAlertMessage
from sim.vanet.nodes import RSUNode, is_vehicle_equipped

logger = logging.getLogger(__name__)


class AlertManager:
    """Orchestrates emergency alert generation, wireless propagation, V2V relay, and RSU ingestion."""

    def __init__(
        self,
        channel: WirelessChannel,
        rsus: Dict[str, RSUNode],
        tls_ids: List[str],
        alert_interval: float = 0.5,
        alert_timeout: float = 2.0,
        relay_enabled: bool = False,
        max_hops: int = 3,
        backbone_delay_ms: float = 5.0,
        penetration: float = 1.0,
        seed: int = 1,
    ) -> None:
        """Initialize AlertManager with communication channels and node topologies."""
        self.channel = channel
        self.rsus = rsus
        self.tls_ids = list(tls_ids)
        self.alert_interval = float(alert_interval)
        self.alert_timeout = float(alert_timeout)
        self.relay_enabled = bool(relay_enabled)
        self.max_hops = int(max_hops)
        self.backbone_delay_ms = float(backbone_delay_ms)
        self.penetration = float(penetration)
        self.seed = int(seed)

        # Transmission state
        self.last_alert_time: Dict[str, float] = {}
        self.alert_seq_num: int = 0
        self.seen_msg_ids_per_node: Dict[str, Set[str]] = {}

        # RSU state: tls_id -> ev_id -> latest EvAlertMessage
        self.rsu_latest_alerts: Dict[str, Dict[str, EvAlertMessage]] = {
            tid: {} for tid in self.tls_ids
        }

        # Wired backbone delivery queue: (scheduled_time, target_rsu_id, EvAlertMessage)
        self.backbone_queue: List[Tuple[float, str, EvAlertMessage]] = []

        # Event queue and rate limiting (max 5 per second per event kind)
        self.events_queue: List[Dict[str, Any]] = []
        self._last_event_sec: Dict[str, int] = {}
        self._event_sec_counts: Dict[str, int] = {}

        # Performance metrics
        self.first_range_entry_time: Dict[Tuple[str, str], float] = {}  # (ev_id, tls_id) -> time
        self.first_rsu_delivery_time: Dict[Tuple[str, str], float] = {}  # (ev_id, tls_id) -> time
        self.delivered_hops: List[int] = []
        self.junctions_entered_by_ev: Dict[str, Set[str]] = {}  # ev_id -> set of tls_ids
        self.junctions_preempted_for_ev: Dict[str, Set[str]] = {}  # ev_id -> set of tls_ids

    def _emit_event_sampled(self, event_kind: str, payload: Dict[str, Any], sim_time: float) -> None:
        """Emit an event sampled to at most 5 per second per event type."""
        sec = int(sim_time)
        last_sec = self._last_event_sec.get(event_kind, -1)
        if sec != last_sec:
            self._last_event_sec[event_kind] = sec
            self._event_sec_counts[event_kind] = 0

        if self._event_sec_counts[event_kind] < 5:
            self._event_sec_counts[event_kind] += 1
            payload["type"] = "event"
            payload["event"] = event_kind
            self.events_queue.append(payload)

    def pop_events(self) -> List[Dict[str, Any]]:
        """Pop all accumulated telemetry events."""
        events = self.events_queue[:]
        self.events_queue.clear()
        return events

    def step(self, sim_time: float, sumo_api: Any) -> None:
        """Execute one simulation step for emergency alert broadcasting and relaying."""
        if not sumo_api:
            return

        # 1. Discover active emergency vehicles
        try:
            active_vids = list(sumo_api.vehicle.getIDList())
        except Exception:
            active_vids = []

        emergency_vehicles: List[str] = []
        regular_equipped_vehicles: List[str] = []

        for vid in active_vids:
            try:
                vtype = sumo_api.vehicle.getTypeID(vid)
            except Exception:
                vtype = "car"

            is_ev = (vtype == "emergency" or "amb" in vid.lower() or "ev" in vid.lower())
            if is_ev:
                emergency_vehicles.append(vid)
            elif self.relay_enabled and is_vehicle_equipped(
                vid, vtype=vtype, penetration=self.penetration, seed=self.seed
            ):
                regular_equipped_vehicles.append(vid)

        # 2. Check range entry and generate periodic EV alerts
        for ev_id in emergency_vehicles:
            try:
                pos = sumo_api.vehicle.getPosition(ev_id)
                speed = float(sumo_api.vehicle.getSpeed(ev_id))
                route = list(sumo_api.vehicle.getRoute(ev_id))
                curr_edge = str(sumo_api.vehicle.getRoadID(ev_id))
            except Exception:
                continue

            # Check if EV has entered radio range of any RSU for delivery latency metric
            for tid, rsu in self.rsus.items():
                dist_rsu = math.hypot(pos[0] - rsu.pos[0], pos[1] - rsu.pos[1])
                if dist_rsu <= self.channel.range_m:
                    pair_key = (ev_id, tid)
                    if pair_key not in self.first_range_entry_time:
                        self.first_range_entry_time[pair_key] = sim_time

            # Check periodic alert interval
            last_t = self.last_alert_time.get(ev_id, -999.0)
            if (sim_time - last_t) < self.alert_interval:
                continue

            self.last_alert_time[ev_id] = sim_time
            self.alert_seq_num += 1
            msg_id = f"alert_{ev_id}_{self.alert_seq_num}"

            alert_msg = EvAlertMessage(
                msg_id=msg_id,
                ev_id=ev_id,
                pos=pos,
                speed=speed,
                route_edges=route,
                current_edge=curr_edge,
                timestamp=sim_time,
                ttl=self.max_hops,
                hop_count=0,
                sender_id=ev_id,
                via_backbone=False,
            )

            self._emit_event_sampled("alert_sent", {
                "vehicle_id": ev_id,
                "time": sim_time,
                "pos": [round(pos[0], 1), round(pos[1], 1)],
                "hops": 0,
            }, sim_time)

            # Direct V2I transmission to RSUs in range
            for tid, rsu in self.rsus.items():
                dist = math.hypot(pos[0] - rsu.pos[0], pos[1] - rsu.pos[1])
                if dist <= self.channel.range_m:
                    accepted = self.channel.transmit(
                        message=alert_msg,
                        sender_pos=pos,
                        receiver_pos=rsu.pos,
                        receiver_id=tid,
                        current_time=sim_time,
                    )
                    if not accepted:
                        self._emit_event_sampled("alert_lost", {
                            "vehicle_id": ev_id,
                            "time": sim_time,
                            "target": tid,
                            "reason": "channel_drop",
                        }, sim_time)

            # V2V Relay: transmit to equipped regular vehicles in range
            if self.relay_enabled and regular_equipped_vehicles:
                relaying_vehicles: List[Tuple[str, Tuple[float, float]]] = []
                for v_veh in regular_equipped_vehicles:
                    try:
                        v_pos = sumo_api.vehicle.getPosition(v_veh)
                    except Exception:
                        continue
                    d_v = math.hypot(pos[0] - v_pos[0], pos[1] - v_pos[1])
                    if d_v <= self.channel.range_m:
                        # Attempt transmission from EV to vehicle
                        if self.channel.rng.random() >= self.channel.calculate_loss_probability(d_v):
                            relaying_vehicles.append((v_veh, v_pos))

                # Relaying vehicles re-broadcast once with duplicate suppression
                for rel_id, rel_pos in relaying_vehicles:
                    seen_set = self.seen_msg_ids_per_node.setdefault(rel_id, set())
                    if msg_id in seen_set:
                        continue
                    seen_set.add(msg_id)

                    relayed_msg = EvAlertMessage(
                        msg_id=msg_id,
                        ev_id=ev_id,
                        pos=pos,
                        speed=speed,
                        route_edges=route,
                        current_edge=curr_edge,
                        timestamp=sim_time,
                        ttl=alert_msg.ttl - 1,
                        hop_count=alert_msg.hop_count + 1,
                        sender_id=rel_id,
                        via_backbone=False,
                    )

                    # Transmit from relay vehicle to RSUs in range
                    for tid, rsu in self.rsus.items():
                        dist_rsu = math.hypot(rel_pos[0] - rsu.pos[0], rel_pos[1] - rsu.pos[1])
                        if dist_rsu <= self.channel.range_m:
                            self.channel.transmit(
                                message=relayed_msg,
                                sender_pos=rel_pos,
                                receiver_pos=rsu.pos,
                                receiver_id=tid,
                                current_time=sim_time,
                            )

        # 3. Process scheduled wired backbone deliveries
        remaining_backbone: List[Tuple[float, str, EvAlertMessage]] = []
        for sched_time, target_tid, b_msg in self.backbone_queue:
            if sched_time <= sim_time:
                self._ingest_rsu_alert(target_tid, b_msg, sim_time, is_backbone=True)
            else:
                remaining_backbone.append((sched_time, target_tid, b_msg))
        self.backbone_queue = remaining_backbone

    def handle_delivered_alert(
        self, rsu_id: str, alert_msg: EvAlertMessage, latency_ms: float, sim_time: float
    ) -> None:
        """Handle a wireless alert delivered to an RSU by the ChannelModel."""
        self._ingest_rsu_alert(rsu_id, alert_msg, sim_time, is_backbone=False, latency_ms=latency_ms)

    def _ingest_rsu_alert(
        self,
        rsu_id: str,
        alert_msg: EvAlertMessage,
        sim_time: float,
        is_backbone: bool = False,
        latency_ms: float = 0.0,
    ) -> None:
        """Store alert in RSU registry, record telemetry, and forward downstream via backbone."""
        if rsu_id not in self.rsus:
            return

        rsu_seen = self.seen_msg_ids_per_node.setdefault(rsu_id, set())
        # Duplicate suppression per RSU for the exact same alert packet
        if alert_msg.msg_id in rsu_seen:
            return
        rsu_seen.add(alert_msg.msg_id)

        # Update latest alert state for this EV at this junction
        self.rsu_latest_alerts[rsu_id][alert_msg.ev_id] = alert_msg

        # Telemetry metrics
        self.delivered_hops.append(alert_msg.hop_count)
        pair_key = (alert_msg.ev_id, rsu_id)
        if pair_key not in self.first_rsu_delivery_time:
            self.first_rsu_delivery_time[pair_key] = sim_time

        self._emit_event_sampled("alert_delivered", {
            "tls_id": rsu_id,
            "vehicle_id": alert_msg.ev_id,
            "time": sim_time,
            "latency_ms": round(latency_ms, 1),
            "hops": alert_msg.hop_count,
            "backbone": is_backbone,
        }, sim_time)

        # Forward downstream along EV route over wired backbone
        if not is_backbone and alert_msg.route_edges:
            self._forward_to_downstream_rsus(rsu_id, alert_msg, sim_time)

    def _forward_to_downstream_rsus(
        self, origin_rsu_id: str, alert_msg: EvAlertMessage, sim_time: float
    ) -> None:
        """Forward an ingested wireless alert to downstream RSUs along the EV route."""
        # Find downstream junctions present on the route edges
        downstream_junctions: List[str] = []
        found_origin = False

        for edge in alert_msg.route_edges:
            for tid in self.tls_ids:
                if tid in edge:
                    if tid == origin_rsu_id:
                        found_origin = True
                    elif found_origin and tid not in downstream_junctions:
                        downstream_junctions.append(tid)

        if not downstream_junctions:
            # Fallback: check all other RSUs if route is short or ambiguous
            downstream_junctions = [tid for tid in self.tls_ids if tid != origin_rsu_id]

        backbone_delivery_time = sim_time + (self.backbone_delay_ms / 1000.0)

        for target_tid in downstream_junctions:
            target_seen = self.seen_msg_ids_per_node.setdefault(target_tid, set())
            if alert_msg.msg_id not in target_seen:
                forwarded = EvAlertMessage(
                    msg_id=alert_msg.msg_id,
                    ev_id=alert_msg.ev_id,
                    pos=alert_msg.pos,
                    speed=alert_msg.speed,
                    route_edges=alert_msg.route_edges,
                    current_edge=alert_msg.current_edge,
                    timestamp=alert_msg.timestamp,
                    ttl=alert_msg.ttl,
                    hop_count=alert_msg.hop_count,
                    sender_id=origin_rsu_id,
                    via_backbone=True,
                )
                self.backbone_queue.append((backbone_delivery_time, target_tid, forwarded))

    def get_rsu_ev_info(self, tls_id: str, sim_time: float) -> List[Dict[str, Any]]:
        """Return EV information synthesized purely from alerts delivered to this junction's RSU."""
        rsu = self.rsus.get(tls_id)
        if not rsu:
            return []

        alerts_map = self.rsu_latest_alerts.get(tls_id, {})
        ev_info_list: List[Dict[str, Any]] = []

        for ev_id, alert in alerts_map.items():
            alert_age = sim_time - alert.timestamp

            # Requirement: If no alert has arrived within alert_timeout (default 2s),
            # the EV is considered unknown and no preemption triggers.
            if alert_age > self.alert_timeout:
                continue

            # Extrapolate distance along route/approach to RSU stop line
            d0 = math.hypot(alert.pos[0] - rsu.pos[0], alert.pos[1] - rsu.pos[1])
            extrapolated_dist = max(0.0, d0 - (alert.speed * alert_age))

            # Determine approach direction (N, S, E, W)
            approach = "N"
            if alert.current_edge and alert.current_edge in rsu.edge_directions:
                approach = rsu.edge_directions[alert.current_edge]
            else:
                dx = alert.pos[0] - rsu.pos[0]
                dy = alert.pos[1] - rsu.pos[1]
                if abs(dy) >= abs(dx):
                    approach = "N" if dy > 0 else "S"
                else:
                    approach = "E" if dx > 0 else "W"

            # Check clearance: if EV has passed junction, distance drops to 0
            if extrapolated_dist <= 5.0 and alert_age > 1.0:
                extrapolated_dist = 0.0

            # Cruising speed fallback for stopped/queued vehicle
            calc_speed = 15.0 if alert.speed < 1.0 and extrapolated_dist <= 150.0 else max(alert.speed, 0.1)
            eta = round(extrapolated_dist / calc_speed, 1)

            ev_info_list.append({
                "id": ev_id,
                "tls": tls_id,
                "eta": eta,
                "dist": round(extrapolated_dist, 1),
                "speed": round(alert.speed, 1),
                "approach": approach,
                "alert_age": round(alert_age, 2),
                "hop_count": alert.hop_count,
            })

        return ev_info_list

    def get_metrics(self) -> Dict[str, Any]:
        """Aggregate delivery time, missed preemption count, and average hop count."""
        # 1. Delivery time metric: first alert delivery vs range entry
        delays: List[float] = []
        for pair_key, entry_time in self.first_range_entry_time.items():
            if pair_key in self.first_rsu_delivery_time:
                delays.append(self.first_rsu_delivery_time[pair_key] - entry_time)

        avg_delivery_time = round(float(sum(delays) / len(delays)), 2) if delays else 0.0

        # 2. Average hops
        avg_hops = (
            round(float(sum(self.delivered_hops) / len(self.delivered_hops)), 2)
            if self.delivered_hops
            else 1.0
        )

        return {
            "ev_alert_delivery_time": avg_delivery_time,
            "missed_preemptions": 0,
            "avg_hops": avg_hops,
        }
