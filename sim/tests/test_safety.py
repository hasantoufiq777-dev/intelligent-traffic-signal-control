"""Unit tests for traffic signal safety rules and phase transition logic."""

from __future__ import annotations

import pytest
from sim.controllers.base import BaseController


class MockTrafficLightAPI:
    """Mock TraCI/libsumo traffic light API for unit testing."""

    def __init__(self, initial_phase: int = 0, initial_state: str = "GGGGrrrrGGGGrrrr") -> None:
        self.phase = initial_phase
        self.state = initial_state
        self.state_history = [initial_state]
        self.phase_history = [initial_phase]

    def setPhase(self, tls_id: str, phase_index: int) -> None:
        self.phase = phase_index
        self.phase_history.append(phase_index)
        # Update mock state based on standard 4-phase program
        state_map = {
            0: "GGGGrrrrGGGGrrrr",
            1: "yyyyrrrryyyyrrrr",
            2: "rrrrGGGGrrrrGGGG",
            3: "rrrryyyyrrrryyyy",
        }
        self.state = state_map.get(phase_index, "rrrrrrrrrrrrrrrr")
        self.state_history.append(self.state)

    def getPhase(self, tls_id: str) -> int:
        return self.phase

    def getRedYellowGreenState(self, tls_id: str) -> str:
        return self.state

    def setRedYellowGreenState(self, tls_id: str, state: str) -> None:
        self.state = state
        self.state_history.append(state)

    def setProgram(self, tls_id: str, program_id: str) -> None:
        pass

    def getControlledLanes(self, tls_id: str) -> list[str]:
        return ["N2J0_0", "N2J0_1", "S2J0_0", "S2J0_1", "E2J0_0", "E2J0_1", "W2J0_0", "W2J0_1"]


class MockSUMOAPI:
    def __init__(self) -> None:
        self.trafficlight = MockTrafficLightAPI()


def test_has_conflicting_greens_detection() -> None:
    """Assert that conflicting greens are accurately detected."""
    # Safe states
    assert not BaseController.has_conflicting_greens("GGGGrrrrGGGGrrrr")  # NS Green
    assert not BaseController.has_conflicting_greens("yyyyrrrryyyyrrrr")  # NS Yellow
    assert not BaseController.has_conflicting_greens("rrrrGGGGrrrrGGGG")  # EW Green
    assert not BaseController.has_conflicting_greens("rrrryyyyrrrryyyy")  # EW Yellow
    assert not BaseController.has_conflicting_greens("rrrrrrrrrrrrrrrr")  # All Red

    # Conflicting states
    assert BaseController.has_conflicting_greens("GGGGGGGGrrrrrrrr")  # Conflict: NS 0-3 and EW 4-7
    assert BaseController.has_conflicting_greens("GGGGGGGGGGGGGGGG")  # All Green (catastrophic)
    assert BaseController.has_conflicting_greens("Grrrgrrrrrrrrrrr")  # Conflict: NS index 0 and EW index 4


def test_safe_transition_sequence() -> None:
    """Verify that switching from NS green to EW green enforces yellow -> all-red -> green."""
    mock_api = MockSUMOAPI()
    ctrl = BaseController(
        tls_id="J0",
        params={"yellow_time": 3.0, "all_red_time": 2.0},
        sumo_api=mock_api,
    )

    # Initial state: Phase 0 (NS Green)
    assert ctrl.current_phase == BaseController.PHASE_NS_GREEN
    assert not ctrl.is_in_transition()

    # Request safe switch to Phase 2 (EW Green)
    is_immediate = ctrl.set_phase_safe(BaseController.PHASE_EW_GREEN)
    assert not is_immediate
    assert ctrl.transition_state == "YELLOW"
    assert ctrl.current_phase == BaseController.PHASE_NS_YELLOW

    # Tick 1s in yellow
    ctrl.update_transitions(1.0)
    assert ctrl.transition_state == "YELLOW"

    # Tick 2s in yellow (total 3s expired -> enters ALL_RED)
    ctrl.update_transitions(2.0)
    assert ctrl.transition_state == "ALL_RED"
    assert mock_api.trafficlight.getRedYellowGreenState("J0") == "rrrrrrrrrrrrrrrr"

    # Tick 1s in all-red
    ctrl.update_transitions(1.0)
    assert ctrl.transition_state == "ALL_RED"

    # Tick 1s in all-red (total 2s expired -> enters target EW Green)
    ctrl.update_transitions(1.0)
    assert ctrl.transition_state == "STABLE"
    assert ctrl.current_phase == BaseController.PHASE_EW_GREEN
    assert mock_api.trafficlight.getPhase("J0") == BaseController.PHASE_EW_GREEN

    # Assert that no state in history contained conflicting greens
    for state in mock_api.trafficlight.state_history:
        assert not BaseController.has_conflicting_greens(state)
