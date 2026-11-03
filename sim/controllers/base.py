"""Base controller for traffic light signals.

Provides safe phase switching (yellow -> all-red -> green) and queue inspection helpers
via an injected StateProvider.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from sim.sensing import OracleStateProvider, StateProvider

logger = logging.getLogger(__name__)


class BaseController:
    """Base class for all traffic signal controllers."""

    # Standard phase definitions for J0 (4-phase program)
    PHASE_NS_GREEN = 0
    PHASE_NS_YELLOW = 1
    PHASE_EW_GREEN = 2
    PHASE_EW_YELLOW = 3

    # Remaining duration given to every phase the controller sets (effectively "until changed")
    HOLD_PHASE_S = 1.0e6

    NS_LINKS = {0, 1, 2, 3, 8, 9, 10, 11}
    EW_LINKS = {4, 5, 6, 7, 12, 13, 14, 15}

    def __init__(
        self,
        tls_id: str,
        params: Optional[Dict[str, Any]] = None,
        provider: Optional[StateProvider] = None,
        sumo_api: Any = None,
    ) -> None:
        """Initialize base controller with dependency-injected StateProvider.

        Args:
            tls_id: ID of the traffic light in SUMO.
            params: Controller and safety parameters.
            provider: StateProvider implementation (OracleStateProvider or VanetStateProvider).
            sumo_api: Optional direct TraCI/libsumo reference (wrapped into OracleStateProvider if provider is omitted).
        """
        self.tls_id = tls_id
        self.params = params or {}

        if provider is not None:
            self.provider: Optional[StateProvider] = provider
            self.sumo_api = sumo_api or getattr(provider, "sumo_api", None)
        elif sumo_api is not None:
            self.provider = OracleStateProvider(sumo_api)
            self.sumo_api = sumo_api
        else:
            self.provider = None
            self.sumo_api = None

        self.yellow_time: float = float(self.params.get("yellow_time", 3.0))
        self.all_red_time: float = float(self.params.get("all_red_time", 2.0))

        # Transition state machine: STABLE | YELLOW | ALL_RED
        self.transition_state: str = "STABLE"
        self.transition_timer: float = 0.0

        self.current_phase: int = self.PHASE_NS_GREEN
        self.target_phase: int = self.PHASE_NS_GREEN
        self.pending_target_phase: Optional[int] = None

        if self.sumo_api:
            try:
                self.current_phase = self.sumo_api.trafficlight.getPhase(self.tls_id)
                self._cache_link_directions()
            except Exception as e:
                logger.warning("Could not read initial TLS info for %s: %s", self.tls_id, e)

    def has_conflicting_greens(self_or_state: Any, state: str = "") -> bool:
        """Check if a signal state string contains simultaneous conflicting greens."""
        if isinstance(self_or_state, str):
            state_str = self_or_state
            self_obj = None
        else:
            self_obj = self_or_state
            state_str = state

        green_chars = {"G", "g"}
        ns_indices = getattr(self_obj, "_ns_link_indices", set()) if self_obj else set()
        ew_indices = getattr(self_obj, "_ew_link_indices", set()) if self_obj else set()

        if not ns_indices or not ew_indices:
            ns_indices = BaseController.NS_LINKS
            ew_indices = BaseController.EW_LINKS

        ns_has_green = any(i < len(state_str) and state_str[i] in green_chars for i in ns_indices)
        ew_has_green = any(i < len(state_str) and state_str[i] in green_chars for i in ew_indices)
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

    def _cache_link_directions(self) -> None:
        """Cache approach direction for each traffic light link index."""
        if not self.sumo_api:
            return
        try:
            controlled_links = self.sumo_api.trafficlight.getControlledLinks(self.tls_id)
            self._link_directions: List[str] = []
            self._ns_link_indices: set[int] = set()
            self._ew_link_indices: set[int] = set()

            for idx, link_list in enumerate(controlled_links):
                direction = "N"
                if link_list and len(link_list[0]) > 0:
                    from_lane = link_list[0][0]
                    if self.provider and hasattr(self.provider, "_resolve_lane_direction"):
                        direction = self.provider._resolve_lane_direction(from_lane, self.tls_id)
                    else:
                        if "N2" in from_lane or "top" in from_lane or from_lane in ("J2J0_0", "J2J0_1", "J3J1_0", "J3J1_1"):
                            direction = "N"
                        elif "S2" in from_lane or "bottom" in from_lane or from_lane in ("J0J2_0", "J0J2_1", "J1J3_0", "J1J3_1"):
                            direction = "S"
                        elif "E2" in from_lane or "right" in from_lane or from_lane in ("J1J0_0", "J1J0_1", "J3J2_0", "J3J2_1"):
                            direction = "E"
                        elif "W2" in from_lane or "left" in from_lane or from_lane in ("J0J1_0", "J0J1_1", "J2J3_0", "J2J3_1"):
                            direction = "W"

                self._link_directions.append(direction)
                if direction in ("N", "S"):
                    self._ns_link_indices.add(idx)
                else:
                    self._ew_link_indices.add(idx)
        except Exception:
            self._link_directions = []
            self._ns_link_indices = set(self.NS_LINKS)
            self._ew_link_indices = set(self.EW_LINKS)

    def get_approach_signals(self) -> Dict[str, str]:
        """Return dominant signal state ('G', 'y', 'r') per approach direction."""
        signals = {"N": "r", "S": "r", "E": "r", "W": "r"}
        state = self.get_phase_state()
        if not state:
            return signals

        if not hasattr(self, "_link_directions") or not self._link_directions:
            self._cache_link_directions()

        if hasattr(self, "_link_directions") and self._link_directions:
            for idx, d in enumerate(self._link_directions):
                if idx < len(state):
                    char = state[idx]
                    if char in ("G", "g"):
                        signals[d] = "G"
                    elif char == "y" and signals[d] != "G":
                        signals[d] = "y"
            return signals

        # Fallback to single junction link index ranges
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

    # ------------------------------------------------------------------ SPaT (eco-driving)
    def _green_remaining_bounds(self) -> Tuple[Optional[float], Optional[float]]:
        """Seconds until the current green ends: (earliest, latest). None = unknown."""
        return None, None

    def _green_length_bounds(self, group: str) -> Tuple[Optional[float], Optional[float]]:
        """Length of a future green for group 'NS' or 'EW': (shortest, longest). None = unknown."""
        return None, None

    def get_spat(self) -> Dict[str, Any]:
        """Signal Phase and Timing per approach, as an RSU would broadcast it.

        {"N": {"signal": "G|y|r", "min_s", "max_s", "next_green_min_s", "next_green_max_s"}, ...}
        min_s/max_s: earliest/latest seconds until this approach's signal changes.
        next_green_*: earliest/latest seconds until the next green that starts in the future (for an
        approach that is green now: the green after the coming red).
        Values are None when the controller cannot predict them.
        """
        signals = self.get_approach_signals()
        groups = {"NS": ("N", "S"), "EW": ("E", "W")}
        t_y, t_ar = float(self.yellow_time), float(self.all_red_time)

        def add(*xs: Optional[float]) -> Optional[float]:
            return None if any(x is None for x in xs) else float(sum(xs))  # type: ignore[arg-type]

        timing: Dict[str, Tuple[Optional[float], ...]] = {}
        if self.transition_state == "STABLE":
            serving = {self.PHASE_NS_GREEN: "NS", self.PHASE_EW_GREEN: "EW"}.get(self.current_phase)
            if serving is not None:
                other = "EW" if serving == "NS" else "NS"
                rmin, rmax = self._green_remaining_bounds()
                omin, omax = self._green_length_bounds(other)
                to_other_min, to_other_max = add(rmin, t_y, t_ar), add(rmax, t_y, t_ar)
                timing[serving] = (rmin, rmax, add(to_other_min, omin, t_y, t_ar), add(to_other_max, omax, t_y, t_ar))
                timing[other] = (to_other_min, to_other_max, to_other_min, to_other_max)
        else:
            target = self.pending_target_phase if self.pending_target_phase is not None else self.target_phase
            tgt = "NS" if target == self.PHASE_NS_GREEN else "EW"
            other = "EW" if tgt == "NS" else "NS"
            rem = max(0.0, float(self.transition_timer))
            to_tgt_green = rem + t_ar if self.transition_state == "YELLOW" else rem
            gmin, gmax = self._green_length_bounds(tgt)
            timing[tgt] = (to_tgt_green, to_tgt_green, to_tgt_green, to_tgt_green)
            other_change = rem if self.transition_state == "YELLOW" else add(to_tgt_green, gmin, t_y, t_ar)
            other_change_max = rem if self.transition_state == "YELLOW" else add(to_tgt_green, gmax, t_y, t_ar)
            timing[other] = (other_change, other_change_max,
                             add(to_tgt_green, gmin, t_y, t_ar), add(to_tgt_green, gmax, t_y, t_ar))

        spat: Dict[str, Any] = {}
        for group, dirs in groups.items():
            tmin, tmax, ngmin, ngmax = timing.get(group, (None, None, None, None))
            for d in dirs:
                sig = signals.get(d, "r")
                spat[d] = {
                    "signal": sig,
                    "min_s": None if tmin is None else round(tmin, 1),
                    "max_s": None if tmax is None else round(tmax, 1),
                    "next_green_min_s": None if ngmin is None else round(ngmin, 1),
                    "next_green_max_s": None if ngmax is None else round(ngmax, 1),
                }
        return spat

    def get_queue_lengths(self) -> Dict[str, int]:
        """Return number of halting vehicles per approach direction (N, S, E, W) via StateProvider."""
        if self.provider:
            return self.provider.get_queues(self.tls_id)
        return {"N": 0, "S": 0, "E": 0, "W": 0}

    def get_waiting_times(self) -> Dict[str, float]:
        """Return max/mean vehicle waiting times per approach direction via StateProvider."""
        if self.provider:
            return self.provider.get_waiting_times(self.tls_id)
        return {"N": 0.0, "S": 0.0, "E": 0.0, "W": 0.0}

    def is_in_transition(self) -> bool:
        """Check if controller is currently in yellow or all-red transition."""
        return self.transition_state != "STABLE"

    def set_phase_safe(self, target_phase: int) -> bool:
        """Request a transition to target_phase, enforcing yellow -> all-red -> green.

        Returns True if target_phase is immediately active, False if transitioning.
        """
        if target_phase == self.current_phase and self.transition_state == "STABLE":
            return True

        if self.transition_state != "STABLE":
            self.pending_target_phase = target_phase
            return False

        self.target_phase = target_phase
        self.pending_target_phase = None

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
                self.sumo_api.trafficlight.setProgram(self.tls_id, "0")
            except Exception:
                pass
            self.sumo_api.trafficlight.setPhase(self.tls_id, phase_index)
            # Hold the phase until the controller changes it. Without this, SUMO's static program
            # advances by itself when the phase's net-file duration (30 s single, 42 s grid) ends,
            # cutting long greens (adaptive max_green, EV preemption holds) behind the controller.
            self.sumo_api.trafficlight.setPhaseDuration(self.tls_id, self.HOLD_PHASE_S)
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
                self.transition_state = "ALL_RED"
                self.transition_timer = self.all_red_time
                self._apply_all_red()

            elif self.transition_state == "ALL_RED":
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
