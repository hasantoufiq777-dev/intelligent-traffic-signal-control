"""Emergency vehicle signal preemption controller (oracle mode).

Wraps an underlying AdaptiveController to provide signal preemption when an
emergency vehicle approaches within an ETA threshold. EV information is obtained
STRICTLY through provider.get_ev_info(tls_id) (OracleStateProvider), enforcing
safety clearances and safe phase transitions (yellow -> all-red -> EV green).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from sim.controllers.adaptive import AdaptiveController
from sim.controllers.base import BaseController
from sim.sensing import StateProvider

logger = logging.getLogger(__name__)


class PreemptionController(BaseController):
    """Adaptive controller wrapper with emergency vehicle preemption."""

    def __init__(
        self,
        tls_id: str,
        params: Optional[Dict[str, Any]] = None,
        provider: Optional[StateProvider] = None,
        sumo_api: Any = None,
    ) -> None:
        """Initialize preemption controller and wrapped adaptive controller."""
        super().__init__(tls_id, params=params, provider=provider, sumo_api=sumo_api)

        # Policy hyperparameters from config
        self.eta_threshold: float = float(self.params.get("eta_threshold", 18.0))
        self.clearance_time: float = float(self.params.get("clearance_time", 5.0))
        self.recovery_time: float = float(self.params.get("recovery_time", 10.0))
        self.lookahead_junctions: int = int(self.params.get("lookahead_junctions", 2))

        # Instantiate underlying adaptive controller
        self.adaptive_controller = AdaptiveController(
            tls_id=tls_id,
            params=params,
            provider=provider,
            sumo_api=sumo_api,
        )

        # Preemption state machine: NORMAL | TRANSITION | EV_GREEN | RECOVERY
        self.preempt_state: str = "NORMAL"
        self.active_ev_id: Optional[str] = None
        self.active_ev_approach: Optional[str] = None
        self.preempt_start_time: float = 0.0
        self.clearance_timer: float = 0.0
        self.recovery_timer: float = 0.0
        self.eta_at_trigger: float = 0.0

        # Event and history queues
        self.events_queue: List[Dict[str, Any]] = []
        self.preemption_records: List[Dict[str, Any]] = []
        # EVs this junction has triggered preemption for (used for the missed_preemptions metric)
        self.triggered_evs: Set[str] = set()

    def get_phase_name(self) -> str:
        """Return semantic name of the active signal phase."""
        if self.preempt_state == "EV_GREEN":
            return "EV_GREEN"
        elif self.preempt_state == "TRANSITION":
            return "TRANSITION"
        elif self.preempt_state == "RECOVERY":
            return "RECOVERY"
        return self.adaptive_controller.get_phase_name()

    def get_queue_lengths(self) -> Dict[str, int]:
        """Return current approach queue lengths from provider."""
        return self.adaptive_controller.get_queue_lengths()

    def get_waiting_times(self) -> Dict[str, float]:
        """Return waiting times from provider."""
        return self.adaptive_controller.get_waiting_times()

    def pop_events(self) -> List[Dict[str, Any]]:
        """Pop all pending preemption events."""
        events = self.events_queue[:]
        self.events_queue.clear()
        return events

    def _get_target_phase_for_approach(self, approach: str) -> int:
        """Map approach direction to corresponding green phase."""
        if approach in ("N", "S"):
            return self.PHASE_NS_GREEN
        return self.PHASE_EW_GREEN

    def step(self, sim_time: float) -> None:
        """Execute one simulation second of preemption control."""
        # Advance safety transition timer (yellow -> all-red -> green)
        self.update_transitions(1.0)

        # Query EV information strictly through StateProvider
        ev_info_list: List[Dict[str, Any]] = []
        if self.provider:
            ev_info_list = self.provider.get_ev_info(self.tls_id)

        # Filter candidate emergency vehicles targeting this TLS within threshold
        candidates = [
            e
            for e in ev_info_list
            if e.get("tls") == self.tls_id and e.get("eta", 999.0) <= self.eta_threshold
        ]

        # -------------------------------------------------------------
        # STATE 1: NORMAL
        # -------------------------------------------------------------
        if self.preempt_state == "NORMAL":
            if candidates:
                # Prioritize emergency vehicle with lowest ETA
                candidates.sort(key=lambda x: x.get("eta", 999.0))
                best_ev = candidates[0]

                self.active_ev_id = str(best_ev["id"])
                self.triggered_evs.add(self.active_ev_id)
                self.active_ev_approach = str(best_ev.get("approach", "N"))
                self.eta_at_trigger = float(best_ev.get("eta", 0.0))
                self.preempt_start_time = float(sim_time)

                target_phase = self._get_target_phase_for_approach(self.active_ev_approach)

                self.events_queue.append({
                    "kind": "preempt_start",
                    "tls": self.tls_id,
                    "detail": (
                        f"Emergency preemption initiated for {self.active_ev_id} "
                        f"on approach {self.active_ev_approach} (ETA: {self.eta_at_trigger}s)"
                    ),
                })
                logger.info(
                    "[%s] Preemption triggered for EV %s on approach %s (ETA=%.1fs)",
                    self.tls_id,
                    self.active_ev_id,
                    self.active_ev_approach,
                    self.eta_at_trigger,
                )

                if self.current_phase == target_phase and self.transition_state == "STABLE":
                    self.preempt_state = "EV_GREEN"
                    self.clearance_timer = 0.0
                else:
                    self.preempt_state = "TRANSITION"
                    self.set_phase_safe(target_phase)
            else:
                # No emergency vehicles in range, run adaptive controller
                self.adaptive_controller.step(sim_time)
                self.current_phase = self.adaptive_controller.current_phase

        # -------------------------------------------------------------
        # STATE 2: TRANSITION (Yellow -> All-Red -> Green)
        # -------------------------------------------------------------
        elif self.preempt_state == "TRANSITION":
            target_phase = self._get_target_phase_for_approach(self.active_ev_approach or "N")
            if self.current_phase == target_phase and self.transition_state == "STABLE":
                self.preempt_state = "EV_GREEN"
                self.clearance_timer = 0.0

        # -------------------------------------------------------------
        # STATE 3: EV_GREEN (Hold green until EV clears stop line)
        # -------------------------------------------------------------
        elif self.preempt_state == "EV_GREEN":
            target_phase = self._get_target_phase_for_approach(self.active_ev_approach or "N")
            if self.current_phase != target_phase and self.transition_state == "STABLE":
                self._apply_phase(target_phase)

            # Check if EV has completed clearance
            active_rec = next((e for e in ev_info_list if e.get("id") == self.active_ev_id), None)
            ev_cleared = False
            if not active_rec:
                ev_cleared = True
            elif active_rec.get("tls") != self.tls_id:
                ev_cleared = True
            elif active_rec.get("dist", 100.0) <= 0.0:
                ev_cleared = True

            if ev_cleared:
                self.clearance_timer += 1.0
                if self.clearance_timer >= self.clearance_time:
                    # Clearance complete; enter RECOVERY
                    self.preempt_state = "RECOVERY"
                    self.recovery_timer = self.recovery_time
                    # Give green to the starved opposing movement
                    opposing_phase = (
                        self.PHASE_EW_GREEN
                        if target_phase == self.PHASE_NS_GREEN
                        else self.PHASE_NS_GREEN
                    )
                    self.set_phase_safe(opposing_phase)

        # -------------------------------------------------------------
        # STATE 4: RECOVERY (Discharge starved opposing movements)
        # -------------------------------------------------------------
        elif self.preempt_state == "RECOVERY":
            self.recovery_timer -= 1.0
            if self.recovery_timer <= 0.0 and self.transition_state == "STABLE":
                duration = sim_time - self.preempt_start_time
                self.preemption_records.append({
                    "time": self.preempt_start_time,
                    "tls_id": self.tls_id,
                    "vehicle_id": self.active_ev_id,
                    "eta_at_trigger": self.eta_at_trigger,
                    "duration": round(duration, 1),
                })
                self.events_queue.append({
                    "kind": "preempt_end",
                    "tls": self.tls_id,
                    "detail": (
                        f"Emergency vehicle {self.active_ev_id} cleared intersection; "
                        f"preemption ended after {duration:.1f}s"
                    ),
                })
                logger.info(
                    "[%s] Preemption ended for EV %s after %.1fs",
                    self.tls_id,
                    self.active_ev_id,
                    duration,
                )

                # Reset adaptive controller and restore NORMAL operation
                self.adaptive_controller.current_phase = self.current_phase
                self.adaptive_controller.time_in_current_green = 0.0
                self.adaptive_controller.decision_timer = 0.0

                self.preempt_state = "NORMAL"
                self.active_ev_id = None
                self.active_ev_approach = None
