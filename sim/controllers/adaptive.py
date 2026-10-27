"""Adaptive traffic signal controller.

Adjusts green phase allocations dynamically based on real-time approach queues
and starvation limits, while strictly enforcing minimum and maximum green constraints
and safe yellow/all-red transitions. Reads traffic state strictly through a StateProvider.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sim.controllers.base import BaseController
from sim.sensing import StateProvider

logger = logging.getLogger(__name__)


class AdaptiveController(BaseController):
    """Dynamic queue-weighted and starvation-aware traffic signal controller.

    Control Policy:
    1. Safety & Constraints:
       - Minimum Green (min_green): Ensures current phase cannot switch early,
         giving vehicles sufficient time to accelerate and clear the intersection.
       - Maximum Green (max_green): Limits maximum phase duration to prevent one
         heavy approach from starving other movements.
       - Safe Transitions: Every phase switch routes through BaseController.set_phase_safe(),
         enforcing yellow clearance and all-red clearance intervals.

    2. Demand Assessment (Every decision_interval seconds via StateProvider):
       - Measures queue lengths: Q_NS = Q_N + Q_S, Q_EW = Q_E + Q_W.
       - Monitors red-phase waiting duration per approach to detect starvation.

    3. Switching Decision:
       - If starvation limit is reached on a waiting approach, prioritize that approach immediately.
       - If the competing direction has a significantly higher queue (exceeding queue_threshold),
         or if the currently served direction is fully discharged, initiate a safe switch.
    """

    def __init__(
        self,
        tls_id: str,
        params: Optional[Dict[str, Any]] = None,
        provider: Optional[StateProvider] = None,
        sumo_api: Any = None,
    ) -> None:
        """Initialize adaptive controller with policy parameters and state provider."""
        super().__init__(tls_id, params=params, provider=provider, sumo_api=sumo_api)

        # Policy hyperparameters from config
        self.decision_interval: float = float(self.params.get("decision_interval", 5.0))
        self.min_green: float = float(self.params.get("min_green", 10.0))
        self.max_green: float = float(self.params.get("max_green", 50.0))
        self.queue_threshold: int = int(self.params.get("queue_threshold", 5))
        self.starvation_limit: float = float(self.params.get("starvation_limit", 60.0))

        # Runtime timers
        self.time_in_current_green: float = 0.0
        self.decision_timer: float = 0.0

        # Starvation tracking: seconds spent waiting on red per approach
        self.red_wait_times: Dict[str, float] = {"N": 0.0, "S": 0.0, "E": 0.0, "W": 0.0}

        # Start at NS Green
        self.current_phase = self.PHASE_NS_GREEN
        if self.sumo_api:
            try:
                self.sumo_api.trafficlight.setPhase(self.tls_id, self.PHASE_NS_GREEN)
            except Exception:
                pass

    def _update_starvation_trackers(self, dt: float, queues: Dict[str, int]) -> None:
        """Track how long approaches with waiting vehicles are kept on red."""
        is_ns_green = self.current_phase == self.PHASE_NS_GREEN and not self.is_in_transition()
        is_ew_green = self.current_phase == self.PHASE_EW_GREEN and not self.is_in_transition()

        # Update North-South
        if is_ns_green:
            self.red_wait_times["N"] = 0.0
            self.red_wait_times["S"] = 0.0
        else:
            if queues.get("N", 0) > 0:
                self.red_wait_times["N"] += dt
            else:
                self.red_wait_times["N"] = 0.0

            if queues.get("S", 0) > 0:
                self.red_wait_times["S"] += dt
            else:
                self.red_wait_times["S"] = 0.0

        # Update East-West
        if is_ew_green:
            self.red_wait_times["E"] = 0.0
            self.red_wait_times["W"] = 0.0
        else:
            if queues.get("E", 0) > 0:
                self.red_wait_times["E"] += dt
            else:
                self.red_wait_times["E"] = 0.0

            if queues.get("W", 0) > 0:
                self.red_wait_times["W"] += dt
            else:
                self.red_wait_times["W"] = 0.0

    def step(self, sim_time: float) -> None:
        """Execute one simulation second of adaptive signal control."""
        self.update_transitions(1.0)

        if self.is_in_transition():
            self.time_in_current_green = 0.0
            self.decision_timer = 0.0
            return

        queues = self.get_queue_lengths()
        self._update_starvation_trackers(1.0, queues)

        self.time_in_current_green += 1.0
        self.decision_timer += 1.0

        if self.decision_timer < self.decision_interval:
            return

        self.decision_timer = 0.0

        # Constraint 1: Enforce Minimum Green
        if self.time_in_current_green < self.min_green:
            return

        # Constraint 2: Enforce Maximum Green
        if self.time_in_current_green >= self.max_green:
            target_phase = (
                self.PHASE_EW_GREEN
                if self.current_phase == self.PHASE_NS_GREEN
                else self.PHASE_NS_GREEN
            )
            self.set_phase_safe(target_phase)
            self.time_in_current_green = 0.0
            return

        # Demand assessment
        q_ns = queues.get("N", 0) + queues.get("S", 0)
        q_ew = queues.get("E", 0) + queues.get("W", 0)

        ns_starving = (
            self.red_wait_times["N"] >= self.starvation_limit
            or self.red_wait_times["S"] >= self.starvation_limit
        )
        ew_starving = (
            self.red_wait_times["E"] >= self.starvation_limit
            or self.red_wait_times["W"] >= self.starvation_limit
        )

        if self.current_phase == self.PHASE_NS_GREEN:
            should_switch = False
            if ew_starving:
                should_switch = True
            elif q_ns <= 1 and q_ew > 0:
                should_switch = True
            elif q_ew > q_ns and (q_ew - q_ns >= self.queue_threshold):
                should_switch = True

            if should_switch:
                self.set_phase_safe(self.PHASE_EW_GREEN)
                self.time_in_current_green = 0.0

        elif self.current_phase == self.PHASE_EW_GREEN:
            should_switch = False
            if ns_starving:
                should_switch = True
            elif q_ew <= 1 and q_ns > 0:
                should_switch = True
            elif q_ns > q_ew and (q_ns - q_ew >= self.queue_threshold):
                should_switch = True

            if should_switch:
                self.set_phase_safe(self.PHASE_NS_GREEN)
                self.time_in_current_green = 0.0
