"""Sensing abstraction layer for traffic signal control.

Provides abstract StateProvider interface and OracleStateProvider (direct TraCI/SUMO reader).
Future modules plug in VanetStateProvider without modifying controllers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import math
from typing import Any, Dict, List, Optional, Tuple


class StateProvider(ABC):
    """Abstract interface for reading traffic state."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the state provider ('oracle' or 'vanet')."""
        pass

    @abstractmethod
    def get_queues(self, tls_id: str) -> Dict[str, int]:
        """Return number of queued vehicles per approach direction {'N': int, 'S': int, 'E': int, 'W': int}."""
        pass

    @abstractmethod
    def get_waiting_times(self, tls_id: str) -> Dict[str, float]:
        """Return max/mean vehicle waiting time (seconds) per approach direction."""
        pass

    @abstractmethod
    def get_ev_info(self, tls_id: str) -> List[Dict[str, Any]]:
        """Return list of emergency vehicle records relevant to this TLS."""
        pass

    @abstractmethod
    def get_edge_speeds(self) -> Dict[str, float]:
        """Return mean vehicle speed per edge across the network."""
        pass

    def update(self, sim_time: float) -> None:
        """Periodic update hook called every simulation step."""
        pass


class OracleStateProvider(StateProvider):
    """Ground-truth state provider reading directly from SUMO via TraCI/libsumo."""

    def __init__(self, sumo_api: Any) -> None:
        self.sumo_api = sumo_api
        self._controlled_lanes_cache: Dict[str, List[str]] = {}
        self._lane_direction_cache: Dict[str, str] = {}

    @property
    def name(self) -> str:
        return "oracle"

    def _get_controlled_lanes(self, tls_id: str) -> List[str]:
        if tls_id not in self._controlled_lanes_cache:
            try:
                lanes = list(dict.fromkeys(self.sumo_api.trafficlight.getControlledLanes(tls_id)))
                self._controlled_lanes_cache[tls_id] = lanes
            except Exception:
                self._controlled_lanes_cache[tls_id] = []
        return self._controlled_lanes_cache[tls_id]

    def _resolve_lane_direction(self, lane_id: str, tls_id: str) -> str:
        """Determine approach direction (N, S, E, W) dynamically using geometry."""
        cache_key = f"{tls_id}_{lane_id}"
        if cache_key in self._lane_direction_cache:
            return self._lane_direction_cache[cache_key]

        # Fast heuristic based on edge name prefixes
        edge_id = lane_id.split("_")[0]
        if edge_id.startswith("N2") or edge_id.startswith("top") or "top" in edge_id or edge_id in ("J2J0", "J3J1"):
            direction = "N"
        elif edge_id.startswith("S2") or edge_id.startswith("bottom") or "bottom" in edge_id or edge_id in ("J0J2", "J1J3"):
            direction = "S"
        elif edge_id.startswith("E2") or edge_id.startswith("right") or "right" in edge_id or edge_id in ("J1J0", "J3J2"):
            direction = "E"
        elif edge_id.startswith("W2") or edge_id.startswith("left") or "left" in edge_id or edge_id in ("J0J1", "J2J3"):
            direction = "W"
        else:
            # Fallback to geometric shape if available
            try:
                target_lane = lane_id if "_" in lane_id else f"{lane_id}_0"
                if not target_lane.startswith(":"):
                    shape = self.sumo_api.lane.getShape(target_lane)
                    if len(shape) >= 2:
                        p1 = shape[0]
                        p2 = shape[-1]
                        dx = p1[0] - p2[0]
                        dy = p1[1] - p2[1]
                        if abs(dy) >= abs(dx):
                            direction = "N" if dy > 0 else "S"
                        else:
                            direction = "E" if dx > 0 else "W"
                    else:
                        direction = "N"
                else:
                    direction = "N"
            except Exception:
                direction = "N"

        self._lane_direction_cache[cache_key] = direction
        return direction

    def get_queues(self, tls_id: str) -> Dict[str, int]:
        queues = {"N": 0, "S": 0, "E": 0, "W": 0}
        if not self.sumo_api:
            return queues

        lanes = self._get_controlled_lanes(tls_id)
        for lane_id in lanes:
            try:
                halting = self.sumo_api.lane.getLastStepHaltingNumber(lane_id)
            except Exception:
                halting = 0

            d = self._resolve_lane_direction(lane_id, tls_id)
            queues[d] += halting

        return queues

    def get_waiting_times(self, tls_id: str) -> Dict[str, float]:
        wait_times = {"N": 0.0, "S": 0.0, "E": 0.0, "W": 0.0}
        if not self.sumo_api:
            return wait_times

        lanes = self._get_controlled_lanes(tls_id)
        for lane_id in lanes:
            try:
                vehs = self.sumo_api.lane.getLastStepVehicleIDs(lane_id)
            except Exception:
                vehs = []

            max_w = 0.0
            for v in vehs:
                try:
                    w = self.sumo_api.vehicle.getWaitingTime(v)
                    if w > max_w:
                        max_w = w
                except Exception:
                    pass

            d = self._resolve_lane_direction(lane_id, tls_id)
            wait_times[d] = max(wait_times[d], max_w)

        return wait_times

    def get_ev_info(self, tls_id: str, lookahead: int = 2) -> List[Dict[str, Any]]:
        ev_list: List[Dict[str, Any]] = []
        if not self.sumo_api:
            return ev_list

        try:
            active_vehs = self.sumo_api.vehicle.getIDList()
        except Exception:
            return ev_list

        for vid in active_vehs:
            try:
                vtype = self.sumo_api.vehicle.getTypeID(vid)
                if vtype == "emergency" or "amb" in vid.lower() or "ev" in vid.lower():
                    speed = float(self.sumo_api.vehicle.getSpeed(vid))
                    dist = 0.0
                    target_tls = tls_id
                    approach = "N"

                    tls_ahead = self.sumo_api.vehicle.getNextTLS(vid)
                    matched_entry = None
                    if tls_ahead:
                        for entry in tls_ahead[:lookahead]:
                            if entry[0] == tls_id:
                                matched_entry = entry
                                break

                    lane_id = str(self.sumo_api.vehicle.getLaneID(vid))
                    if matched_entry:
                        target_tls = matched_entry[0]
                        dist = float(matched_entry[2])
                        if not lane_id.startswith(":"):
                            approach = self._resolve_lane_direction(lane_id, tls_id)
                        else:
                            dist = 0.0
                            route = list(self.sumo_api.vehicle.getRoute(vid))
                            for r_edge in route:
                                if tls_id in r_edge:
                                    approach = self._resolve_lane_direction(r_edge, tls_id)
                                    break
                    elif tls_ahead:
                        target_tls = tls_ahead[0][0]
                        dist = float(tls_ahead[0][2])
                        if not lane_id.startswith(":"):
                            approach = self._resolve_lane_direction(lane_id, target_tls)
                        else:
                            dist = 0.0
                    else:
                        pos = self.sumo_api.vehicle.getPosition(vid)
                        try:
                            tls_pos = self.sumo_api.junction.getPosition(tls_id)
                            dist = math.hypot(pos[0] - tls_pos[0], pos[1] - tls_pos[1])
                        except Exception:
                            dist = math.hypot(pos[0], pos[1])
                        if not lane_id.startswith(":"):
                            approach = self._resolve_lane_direction(lane_id, tls_id)

                    # For queued/halting EV, use cruising speed to avoid infinite ETA in stopped queue
                    calc_speed = 15.0 if speed < 1.0 and dist <= 150.0 else max(speed, 0.1)
                    eta = round(dist / calc_speed, 1)

                    ev_list.append({
                        "id": vid,
                        "tls": target_tls,
                        "eta": eta,
                        "dist": round(dist, 1),
                        "speed": round(speed, 1),
                        "approach": approach,
                        "alert_age": 0.0,
                    })
            except Exception:
                continue

        return ev_list

    def get_edge_speeds(self) -> Dict[str, float]:
        speeds: Dict[str, float] = {}
        if not self.sumo_api:
            return speeds

        try:
            edge_ids = self.sumo_api.edge.getIDList()
            for eid in edge_ids:
                if not eid.startswith(":"):
                    speeds[eid] = float(self.sumo_api.edge.getLastStepMeanSpeed(eid))
        except Exception:
            pass

        return speeds


# Import VanetStateProvider for unified sensing package interface
from sim.vanet.sensing_vanet import VanetStateProvider  # noqa: E402

__all__ = ["StateProvider", "OracleStateProvider", "VanetStateProvider"]
