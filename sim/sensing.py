"""Sensing abstraction layer for traffic signal control.

Provides abstract StateProvider interface and OracleStateProvider (direct TraCI/SUMO reader).
Future modules plug in VanetStateProvider without modifying controllers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import math
from typing import Any, Dict, List, Optional


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
        """Return list of emergency vehicle records relevant to this TLS.

        Each record contains: {'id': str, 'tls': str, 'eta': float, 'dist': float, 'speed': float, 'alert_age': float}
        """
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

            # Directional mapping from lane/edge name
            if "N2" in lane_id or ":J0" in lane_id and "N" in lane_id:
                queues["N"] += halting
            elif "S2" in lane_id or ":J0" in lane_id and "S" in lane_id:
                queues["S"] += halting
            elif "E2" in lane_id or ":J0" in lane_id and "E" in lane_id:
                queues["E"] += halting
            elif "W2" in lane_id or ":J0" in lane_id and "W" in lane_id:
                queues["W"] += halting

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

            if "N2" in lane_id:
                wait_times["N"] = max(wait_times["N"], max_w)
            elif "S2" in lane_id:
                wait_times["S"] = max(wait_times["S"], max_w)
            elif "E2" in lane_id:
                wait_times["E"] = max(wait_times["E"], max_w)
            elif "W2" in lane_id:
                wait_times["W"] = max(wait_times["W"], max_w)

        return wait_times

    def get_ev_info(self, tls_id: str) -> List[Dict[str, Any]]:
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
                if vtype == "emergency" or "amb" in vid.lower():
                    speed = float(self.sumo_api.vehicle.getSpeed(vid))
                    dist = 0.0
                    target_tls = tls_id

                    tls_ahead = self.sumo_api.vehicle.getNextTLS(vid)
                    if tls_ahead:
                        target_tls = tls_ahead[0][0]
                        dist = float(tls_ahead[0][2])
                    else:
                        pos = self.sumo_api.vehicle.getPosition(vid)
                        dist = math.hypot(pos[0], pos[1])

                    eta = round(dist / max(speed, 0.1), 1)
                    ev_list.append({
                        "id": vid,
                        "tls": target_tls,
                        "eta": eta,
                        "dist": round(dist, 1),
                        "speed": round(speed, 1),
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
                if not eid.startswith(":"):  # skip internal junction edges
                    speeds[eid] = float(self.sumo_api.edge.getLastStepMeanSpeed(eid))
        except Exception:
            pass

        return speeds
