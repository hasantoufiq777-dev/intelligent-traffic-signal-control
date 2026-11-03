"""Fixed-time baseline traffic light controller.

Operates on fixed green durations without using any traffic information.
Transitions through yellow and all-red via BaseController.set_phase_safe().
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from sim.controllers.base import BaseController
from sim.sensing import StateProvider


class FixedTimeController(BaseController):
    """Fixed-time baseline controller."""

    def __init__(
        self,
        tls_id: str,
        params: Optional[Dict[str, Any]] = None,
        provider: Optional[StateProvider] = None,
        sumo_api: Any = None,
    ) -> None:
        """Initialize fixed-time controller with cycle parameters."""
        super().__init__(tls_id, params=params, provider=provider, sumo_api=sumo_api)

        # Durations in seconds
        self.green_ns: float = float(self.params.get("green_ns", 30.0))
        self.green_ew: float = float(self.params.get("green_ew", 30.0))

        self.time_in_current_green: float = 0.0

        # Ensure starting in NS green
        self.current_phase = self.PHASE_NS_GREEN
        if self.sumo_api:
            try:
                self._apply_phase(self.PHASE_NS_GREEN)  # also holds the phase (no SUMO auto-advance)
            except Exception:
                pass

    def _green_remaining_bounds(self) -> Tuple[Optional[float], Optional[float]]:
        """Fixed timing: the current green ends exactly when its fixed length is used up."""
        g = self.green_ns if self.current_phase == self.PHASE_NS_GREEN else self.green_ew
        r = max(0.0, g - self.time_in_current_green)
        return r, r

    def _green_length_bounds(self, group: str) -> Tuple[Optional[float], Optional[float]]:
        g = self.green_ns if group == "NS" else self.green_ew
        return g, g

    def step(self, sim_time: float) -> None:
        """Advance controller by one simulation step (1s)."""
        self.update_transitions(1.0)

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
