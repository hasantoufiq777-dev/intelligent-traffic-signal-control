"""VANET-based StateProvider implementation.

Provides the StateProvider interface to traffic controllers reading strictly
from emulated RSU approach estimates and TMC traffic maps, without any direct
TraCI queue or halting vehicle inquiries.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from sim.sensing import StateProvider
from sim.vanet.network import VanetNetwork


class VanetStateProvider(StateProvider):
    """Sensing provider reading purely from the emulated VANET layer."""

    def __init__(self, vanet_net: VanetNetwork) -> None:
        """Initialize with an active VanetNetwork instance."""
        self.vanet_net = vanet_net

    @property
    def name(self) -> str:
        """Name of the state provider."""
        return "vanet"

    def get_queues(self, tls_id: str) -> Dict[str, int]:
        """Return RSU-estimated approach queue counts."""
        return self.vanet_net.get_rsu_queue_estimate(tls_id)

    def get_waiting_times(self, tls_id: str) -> Dict[str, float]:
        """Return RSU-estimated approach waiting times."""
        return self.vanet_net.get_rsu_waiting_times(tls_id)

    def get_ev_info(self, tls_id: str) -> List[Dict[str, Any]]:
        """Return RSU-received emergency vehicle alerts."""
        return self.vanet_net.get_rsu_ev_info(tls_id)

    def get_edge_speeds(self) -> Dict[str, float]:
        """Return global edge speed map from the TMC."""
        return self.vanet_net.tmc.get_edge_speeds()

    def get_incidents(self) -> Dict[str, str]:
        """Return the TMC incident map built from RSU INCIDENT_FLAGs."""
        return self.vanet_net.get_incidents()

    def get_blocked_approaches(self, tls_id: str) -> Set[str]:
        """Approaches of tls_id whose incoming edge the TMC marks as fully blocked."""
        incidents = self.vanet_net.get_incidents()
        rsu = self.vanet_net.rsus.get(tls_id)
        if not incidents or rsu is None:
            return set()
        return {
            rsu.edge_directions[edge]
            for edge, kind in incidents.items()
            if kind == "edge_blocked" and edge in rsu.edge_directions
        }

    def update(self, sim_time: float) -> None:
        """Step the underlying VANET network emulation."""
        self.vanet_net.step(sim_time)
