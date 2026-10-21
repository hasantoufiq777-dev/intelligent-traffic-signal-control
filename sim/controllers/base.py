"""Base controller for traffic light signals.

Provides safe phase switching (yellow -> all-red -> green) and queue inspection helpers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BaseController:
    """Base class for all traffic signal controllers."""

    # Standard phase definitions for J0 (4-phase program)
    PHASE_NS_GREEN = 0
    PHASE_NS_YELLOW = 1
    PHASE_EW_GREEN = 2
    PHASE_EW_YELLOW = 3

    NS_LINKS = {0, 1, 2, 3, 8, 9, 10, 11}
    EW_LINKS = {4, 5, 6, 7, 12, 13, 14, 15}

    def __init__(
        self,
        tls_id: str,
        params: Optional[Dict[str, Any]] = None,
        sumo_api: Any = None,
    ) -> None:
        """Initialize base controller.

        Args:
            tls_id: ID of the traffic light in SUMO.
            params: Controller and safety parameters.
            sumo_api: TraCI or libsumo module reference.
        """
        self.tls_id = tls_id
        self.params = params or {}
        self.sumo_api = sumo_api

        self.yellow_time: float = float(self.params.get("yellow_time", 3.0))
        self.all_red_time: float = float(self.params.get("all_red_time", 2.0))

        # Transition state machine: STABLE | YELLOW | ALL_RED
        self.transition_state: str = "STABLE"
        self.transition_timer: float = 0.0

        self.current_phase: int = self.PHASE_NS_GREEN
        self.target_phase: int = self.PHASE_NS_GREEN
        self.pending_target_phase: Optional[int] = None

        # Cache controlled lanes and approaches
        self.controlled_lanes: List[str] = []
        if self.sumo_api:
            try:
                self.controlled_lanes = list(
                    dict.fromkeys(self.sumo_api.trafficlight.getControlledLanes(self.tls_id))
                )
                self.current_phase = self.sumo_api.trafficlight.getPhase(self.tls_id)
            except Exception as e:
                logger.warning("Could not read initial TLS info for %s: %s", self.tls_id, e)

    @staticmethod
    def has_conflicting_greens(state: str) -> bool:
        """Check if a signal state string contains simultaneous conflicting greens."""
        green_chars = {"G", "g"}
        ns_has_green = any(i < len(state) and state[i] in green_chars for i in BaseController.NS_LINKS)
        ew_has_green = any(i < len(state) and state[i] in green_chars for i in BaseController.EW_LINKS)
        return ns_has_green and ew_has_green

    def get_current_phase(self) -> int:
        """Return the current active phase index."""
        if self.sumo_api:
            return self.sumo_api.trafficlight.getPhase(self.tls_id)
        return self.current_phase

    def get_phase_state(self) -> str:
        """Return the current red-yellow-green state string."""
        if self.sumo_api:
            return self.sumo_api.trafficlight.getRedYellowGreenState(self.tls_id)
        return ""

    def get_phase_name(self) -> str:
        """Return human-readable phase name for live reporting."""
        if self.transition_state == "ALL_RED":
            return "ALL_RED"
        if self.transition_state == "YELLOW":
            return "NS_YELLOW" if self.current_phase == self.PHASE_NS_YELLOW else "EW_YELLOW"

        names = {
            self.PHASE_NS_GREEN: "NS_GREEN",
            self.PHASE_NS_YELLOW: "NS_YELLOW",
            self.PHASE_EW_GREEN: "EW_GREEN",
            self.PHASE_EW_YELLOW: "EW_YELLOW",
        }
        return names.get(self.current_phase, f"PHASE_{self.current_phase}")

    def get_approach_signals(self) -> Dict[str, str]:
        """Return dominant signal state ('G', 'y', 'r') per approach direction."""
        signals = {"N": "r", "S": "r", "E": "r", "W": "r"}
        state = self.get_phase_state()
        if not state:
            return signals

        # Link index ranges: N: 0..3, E: 4..7, S: 8..11, W: 12..15
        directions = {
            "N": range(0, min(4, len(state))),
            "E": range(4, min(8, len(state))),
            "S": range(8, min(12, len(state))),
            "W": range(12, min(16, len(state))),
        }

        for d, indices in directions.items():
            chars = [state[i] for i in indices if i < len(state)]
            if any(c in ("G", "g") for c in chars):
                signals[d] = "G"
            elif any(c == "y" for c in chars):
                signals[d] = "y"
            else:
                signals[d] = "r"

        return signals

    def get_queue_lengths(self) -> Dict[str, int]:
        """Return number of halting vehicles per approach direction (N, S, E, W)."""
        queues = {"N": 0, "S": 0, "E": 0, "W": 0}
        if not self.sumo_api:
            return queues

        for lane_id in self.controlled_lanes:
            try:
                halting = self.sumo_api.lane.getLastStepHaltingNumber(lane_id)
            except Exception:
                halting = 0

            if "N2" in lane_id:
                queues["N"] += halting
            elif "S2" in lane_id:
                queues["S"] += halting
            elif "E2" in lane_id:
                queues["E"] += halting
            elif "W2" in lane_id:
                queues["W"] += halting

        return queues

    def is_in_transition(self) -> bool:
        """Check if controller is currently in yellow or all-red transition."""
        return self.transition_state != "STABLE"

    def set_phase_safe(self, target_phase: int) -> bool:
        """Request a transition to target_phase, enforcing yellow -> all-red -> green.

        Returns True if target_phase is immediately active, False if transitioning.
        """
        # If already at the target phase and stable, do nothing
        if target_phase == self.current_phase and self.transition_state == "STABLE":
            return True

        # If currently transitioning, update pending target
        if self.transition_state != "STABLE":
            self.pending_target_phase = target_phase
            return False

        # Start transition from current green phase
        self.target_phase = target_phase
        self.pending_target_phase = None

        # Determine yellow phase corresponding to current green
        yellow_phase = self.PHASE_NS_YELLOW if self.current_phase == self.PHASE_NS_GREEN else self.PHASE_EW_YELLOW

        self.transition_state = "YELLOW"
        self.transition_timer = self.yellow_time
        self._apply_phase(yellow_phase)
        return False

    def _apply_phase(self, phase_index: int) -> None:
        """Apply a phase to SUMO traffic light with safety check."""
        self.current_phase = phase_index
        if self.sumo_api:
            try:
                # Reset to default program '0' in case an online state (like all-red) was active
                self.sumo_api.trafficlight.setProgram(self.tls_id, "0")
            except Exception:
                pass
            self.sumo_api.trafficlight.setPhase(self.tls_id, phase_index)
            state = self.sumo_api.trafficlight.getRedYellowGreenState(self.tls_id)
            if self.has_conflicting_greens(state):
                msg = f"SAFETY VIOLATION on {self.tls_id}: Conflicting greens in state {state}"
                logger.error(msg)
                raise RuntimeError(msg)

    def _apply_all_red(self) -> None:
        """Set all controlled links to red."""
        if self.sumo_api:
            num_links = len(self.sumo_api.trafficlight.getRedYellowGreenState(self.tls_id))
            all_red_state = "r" * num_links
            self.sumo_api.trafficlight.setRedYellowGreenState(self.tls_id, all_red_state)
            if self.has_conflicting_greens(all_red_state):
                raise RuntimeError(f"Conflicting greens in all-red state: {all_red_state}")

    def update_transitions(self, dt: float = 1.0) -> None:
        """Advance the transition state machine timer by dt seconds."""
        if self.transition_state == "STABLE":
            return

        self.transition_timer -= dt

        if self.transition_timer <= 0:
            if self.transition_state == "YELLOW":
                # Advance from YELLOW to ALL_RED
                self.transition_state = "ALL_RED"
                self.transition_timer = self.all_red_time
                self._apply_all_red()

            elif self.transition_state == "ALL_RED":
                # Advance from ALL_RED to TARGET GREEN
                target = (
                    self.pending_target_phase
                    if self.pending_target_phase is not None
                    else self.target_phase
                )
                self.pending_target_phase = None
                self.transition_state = "STABLE"
                self.transition_timer = 0.0
                self._apply_phase(target)

    def step(self, sim_time: float) -> None:
        """Step method to be overridden by subclasses."""
        self.update_transitions(1.0)
