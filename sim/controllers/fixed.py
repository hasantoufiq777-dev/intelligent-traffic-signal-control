"""Fixed-time baseline traffic light controller.

Operates on fixed green durations without using any traffic information.
Transitions through yellow and all-red via BaseController.set_phase_safe().
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from sim.controllers.base import BaseController


class FixedTimeController(BaseController):
    """Fixed-time baseline controller."""

    def __init__(
        self,
        tls_id: str,
        params: Optional[Dict[str, Any]] = None,
        sumo_api: Any = None,
    ) -> None:
        """Initialize fixed-time controller with cycle parameters."""
        super().__init__(tls_id, params=params, sumo_api=sumo_api)

        # Durations in seconds
        self.green_ns: float = float(self.params.get("green_ns", 30.0))
        self.green_ew: float = float(self.params.get("green_ew", 30.0))

        self.time_in_current_green: float = 0.0

        # Ensure starting in NS green
        self.current_phase = self.PHASE_NS_GREEN
        if self.sumo_api:
            try:
                self.sumo_api.trafficlight.setPhase(self.tls_id, self.PHASE_NS_GREEN)
            except Exception:
                pass

    def step(self, sim_time: float) -> None:
        """Advance controller by one simulation step (1s)."""
        # Advance any active transition (yellow or all-red)
        self.update_transitions(1.0)

        # If transitioning, hold counter
        if self.is_in_transition():
            self.time_in_current_green = 0.0
            return

        self.time_in_current_green += 1.0

        if self.current_phase == self.PHASE_NS_GREEN:
            if self.time_in_current_green >= self.green_ns:
                self.set_phase_safe(self.PHASE_EW_GREEN)
                self.time_in_current_green = 0.0

        elif self.current_phase == self.PHASE_EW_GREEN:
            if self.time_in_current_green >= self.green_ew:
                self.set_phase_safe(self.PHASE_NS_GREEN)
                self.time_in_current_green = 0.0
