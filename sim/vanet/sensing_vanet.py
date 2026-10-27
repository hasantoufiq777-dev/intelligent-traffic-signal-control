"""VANET-based StateProvider implementation.

Provides the StateProvider interface to traffic controllers reading strictly
from emulated RSU approach estimates and TMC traffic maps, without any direct
TraCI queue or halting vehicle inquiries.
"""

from __future__ import annotations

from typing import Any, Dict, List

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

    def update(self, sim_time: float) -> None:
        """Step the underlying VANET network emulation."""
        self.vanet_net.step(sim_time)
