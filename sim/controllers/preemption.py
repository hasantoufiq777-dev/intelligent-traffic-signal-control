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
        self.max_preempt_s: float = float(self.params.get("max_preempt_s", 90.0))

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

        # EV-only green: custom signal states [(state, seconds), ...] shown in order
        self._custom_steps: List[Any] = []
        self._custom_timer: float = 0.0
        self._ev_only_active: bool = False
        self._exit_phase: int = self.PHASE_NS_GREEN

    def get_phase_name(self) -> str:
        """Return semantic name of the active signal phase."""
        if self.preempt_state == "EV_GREEN":
            return "EV_GREEN"
        elif self.preempt_state in ("TRANSITION", "EXIT"):
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

    def get_spat(self) -> Dict[str, Any]:
        """Normal operation: the adaptive controller's timing. During a preemption the timing
        cannot be predicted, so only the current signals are published (marked preempted)."""
        if self.preempt_state == "NORMAL":
            return self.adaptive_controller.get_spat()
        signals = self.get_approach_signals()
        return {
            d: {"signal": signals.get(d, "r"), "min_s": None, "max_s": None,
                "next_green_min_s": None, "next_green_max_s": None, "preempted": True}
            for d in ("N", "S", "E", "W")
        }

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

    # ------------------------------------------------------------------ EV-only green
    # During preemption only the EV's approach gets green, for all its movements (straight, left,
    # right). With a whole-axis green, left-turners ahead of the EV must wait for gaps in opposing
    # traffic and block it; with an approach-only green the queue in front of the EV clears at once.
    # Every change is built from the current signal state: links losing green show yellow first,
    # then an all-red interval before any link gains green, so no conflicting greens can appear.

    def _ev_target_state(self, approach: str) -> Optional[str]:
        if not self.sumo_api:
            return None
        if not getattr(self, "_link_directions", None):
            self._cache_link_directions()
        dirs = getattr(self, "_link_directions", None)
        n = len(self.get_phase_state())
        if not dirs or len(dirs) != n:
            return None
        return "".join("G" if d == approach else "r" for d in dirs)

    def _set_state(self, state: str) -> None:
        self.sumo_api.trafficlight.setRedYellowGreenState(self.tls_id, state)
        if self.has_conflicting_greens(state):
            msg = f"SAFETY VIOLATION on {self.tls_id}: conflicting greens in EV state {state}"
            logger.error(msg)
            raise RuntimeError(msg)

    def _start_ev_only_green(self, approach: str) -> bool:
        """Plan current state -> (yellow) -> (all-red) -> EV-only green. False if not possible."""
        target = self._ev_target_state(approach)
        if target is None:
            return False
        cur = self.get_phase_state()
        green = set("Gg")
        steps = []
        yellow = "".join("y" if c in green and t == "r" else c for c, t in zip(cur, target))
        if yellow != cur or "y" in cur:
            # A yellow already showing (normal control was mid-change) also gets a full yellow time
            steps.append((yellow, self.yellow_time))
        if any(t == "G" and c not in green for c, t in zip(cur, target)) or "y" in cur:
            all_red = "".join("G" if c in green and t == "G" else "r" for c, t in zip(cur, target))
            steps.append((all_red, self.all_red_time))
        steps.append((target, 0.0))
        self._custom_steps = steps
        self._custom_timer = 0.0
        self._ev_only_active = True
        # The base phase machine is not used while custom states are shown
        self.transition_state = "STABLE"
        self.pending_target_phase = None
        self._set_state(steps[0][0])
        self._custom_timer = steps[0][1]
        return True

    def _start_exit_to_phase(self, phase: int) -> None:
        cur = self.get_phase_state()
        yellow = "".join("y" if c in "Gg" else "r" for c in cur)
        self._custom_steps = [(yellow, self.yellow_time), ("r" * len(cur), self.all_red_time)]
        self._exit_phase = phase
        self._set_state(yellow)
        self._custom_timer = self.yellow_time

    def _advance_custom(self) -> bool:
        """Count down the current custom state; True when the last step has been reached/finished."""
        self._custom_timer -= 1.0
        if self._custom_timer > 0:
            return False
        self._custom_steps.pop(0)
        if not self._custom_steps:
            return True
        state, duration = self._custom_steps[0]
        self._set_state(state)
        self._custom_timer = duration
        if duration <= 0 and len(self._custom_steps) == 1:
            self._custom_steps = [(state, 0.0)]
            return True
        return False

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

                self.clearance_timer = 0.0
                if self._start_ev_only_green(self.active_ev_approach):
                    self.preempt_state = "TRANSITION"
                else:
                    # No link information (no SUMO connection): fall back to the program phase
                    self.preempt_state = "TRANSITION"
                    self.set_phase_safe(target_phase)
            else:
                # No emergency vehicles in range, run adaptive controller
                self.adaptive_controller.step(sim_time)
                self.current_phase = self.adaptive_controller.current_phase

        # -------------------------------------------------------------
        # STATE 2: TRANSITION (yellow -> all-red -> EV-only green)
        # -------------------------------------------------------------
        elif self.preempt_state == "TRANSITION":
            if self._custom_steps:
                if self._advance_custom():
                    self.preempt_state = "EV_GREEN"
            else:
                target_phase = self._get_target_phase_for_approach(self.active_ev_approach or "N")
                if self.current_phase == target_phase and self.transition_state == "STABLE":
                    self.preempt_state = "EV_GREEN"

        # -------------------------------------------------------------
        # STATE 3: EV_GREEN (hold the EV approach green until the EV has cleared)
        # -------------------------------------------------------------
        elif self.preempt_state == "EV_GREEN":
            target_phase = self._get_target_phase_for_approach(self.active_ev_approach or "N")

            # Check if EV has completed clearance
            active_rec = next((e for e in ev_info_list if e.get("id") == self.active_ev_id), None)
            ev_cleared = False
            if not active_rec:
                ev_cleared = True
            elif active_rec.get("tls") != self.tls_id:
                ev_cleared = True
            elif active_rec.get("dist", 100.0) <= 0.0:
                ev_cleared = True
            elif str(active_rec.get("approach", self.active_ev_approach)) != self.active_ev_approach:
                # The EV now arrives from another side: it changed route (or turned back) after this
                # junction gave it green. Holding the old approach green would keep its new approach
                # red for good. Close by: move the green to the new approach. Further away: end this
                # preemption; it triggers again when the EV is within eta_threshold.
                new_app = str(active_rec.get("approach"))
                if float(active_rec.get("eta", 999.0)) <= self.eta_threshold and self._start_ev_only_green(new_app):
                    self.events_queue.append({
                        "kind": "preempt_start", "tls": self.tls_id,
                        "detail": f"{self.active_ev_id} now arrives on approach {new_app}: green moved there",
                    })
                    logger.info("[%s] EV %s changed approach %s -> %s: green moved",
                                self.tls_id, self.active_ev_id, self.active_ev_approach, new_app)
                    self.active_ev_approach = new_app
                    self.clearance_timer = 0.0
                    self.preempt_state = "TRANSITION"
                    return
                ev_cleared = True
            if not ev_cleared and sim_time - self.preempt_start_time > self.max_preempt_s:
                # Safety limit, as in real preemption systems: never hold the other approaches red forever
                logger.warning("[%s] Preemption for %s ended after the %.0f s limit",
                               self.tls_id, self.active_ev_id, self.max_preempt_s)
                ev_cleared = True
                self.clearance_timer = self.clearance_time

            if ev_cleared:
                self.clearance_timer += 1.0
                if self.clearance_timer >= self.clearance_time:
                    # Clearance complete: give green to the starved opposing movement, then RECOVERY
                    opposing_phase = (
                        self.PHASE_EW_GREEN
                        if target_phase == self.PHASE_NS_GREEN
                        else self.PHASE_NS_GREEN
                    )
                    self.recovery_timer = self.recovery_time
                    if self._ev_only_active:
                        self._start_exit_to_phase(opposing_phase)
                        self.preempt_state = "EXIT"
                    else:
                        self.preempt_state = "RECOVERY"
                        self.set_phase_safe(opposing_phase)

        # -------------------------------------------------------------
        # STATE 3b: EXIT (EV-only green -> yellow -> all-red -> opposing program phase)
        # -------------------------------------------------------------
        elif self.preempt_state == "EXIT":
            if self._advance_custom():
                self._apply_phase(self._exit_phase)  # back to the normal signal program
                self.transition_state = "STABLE"
                self.pending_target_phase = None
                self._ev_only_active = False
                self.preempt_state = "RECOVERY"

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

                # Reset adaptive controller and restore NORMAL operation. It was not stepped during the
                # preemption, so any transition it had in progress is void: hand it a stable green.
                self.adaptive_controller.current_phase = self.current_phase
                self.adaptive_controller.transition_state = "STABLE"
                self.adaptive_controller.transition_timer = 0.0
                self.adaptive_controller.pending_target_phase = None
                self.adaptive_controller.time_in_current_green = 0.0
                self.adaptive_controller.decision_timer = 0.0

                self.preempt_state = "NORMAL"
                self.active_ev_id = None
                self.active_ev_approach = None
