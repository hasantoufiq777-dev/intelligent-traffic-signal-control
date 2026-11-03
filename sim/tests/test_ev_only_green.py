"""EV-only green: during preemption only the ambulance's approach is green (all its movements),
reached and left through yellow and all-red, with no conflicting greens at any step."""

from __future__ import annotations

from typing import Any, Dict, List

from sim.controllers.preemption import PreemptionController
from sim.sensing import StateProvider

# 16 links, 4 per approach, in the single-junction order: N 0-3, E 4-7, S 8-11, W 12-15
LANES = ["N2J0_0"] * 4 + ["E2J0_0"] * 4 + ["S2J0_0"] * 4 + ["W2J0_0"] * 4
PHASES = {0: "GGGgrrrrGGGgrrrr", 1: "yyyyrrrryyyyrrrr", 2: "rrrrGGGgrrrrGGGg", 3: "rrrryyyyrrrryyyy"}


class FakeTL:
    def __init__(self) -> None:
        self.phase = 0  # SUMO starts on the north-south green; the EV comes from the east
        self.state = PHASES[0]
        self.history: List[str] = [self.state]

    def getPhase(self, tls: str) -> int:
        return self.phase

    def setPhase(self, tls: str, phase: int) -> None:
        self.phase = phase
        self.state = PHASES[phase]
        self.history.append(self.state)

    def setProgram(self, tls: str, prog: str) -> None:
        pass

    def setPhaseDuration(self, tls: str, d: float) -> None:
        pass

    def getRedYellowGreenState(self, tls: str) -> str:
        return self.state

    def setRedYellowGreenState(self, tls: str, state: str) -> None:
        self.state = state
        self.history.append(state)

    def getControlledLinks(self, tls: str) -> List[List[tuple]]:
        return [[(lane, "x", ":J0_0")] for lane in LANES]

    def getControlledLanes(self, tls: str) -> List[str]:
        return LANES


class Api:
    def __init__(self) -> None:
        self.trafficlight = FakeTL()


class EvProvider(StateProvider):
    """EV from the east: approaching for 12 s, then inside the junction, then gone."""

    def __init__(self) -> None:
        self.t = 0.0

    @property
    def name(self) -> str:
        return "fake"

    def get_queues(self, tls_id: str) -> Dict[str, int]:
        return {"N": 2, "S": 2, "E": 2, "W": 2}

    def get_waiting_times(self, tls_id: str) -> Dict[str, float]:
        return {d: 0.0 for d in "NSEW"}

    def get_edge_speeds(self) -> Dict[str, float]:
        return {}

    def get_ev_info(self, tls_id: str) -> List[Dict[str, Any]]:
        if self.t < 12:
            return [{"id": "amb1", "tls": "J0", "eta": 12 - self.t, "dist": (12 - self.t) * 12, "approach": "E"}]
        if self.t < 14:
            return [{"id": "amb1", "tls": "J0", "eta": 0, "dist": 0.0, "approach": "E"}]
        return []

    def update(self, sim_time: float) -> None:
        self.t = sim_time


def _run(steps: int = 60):
    api = Api()
    provider = EvProvider()
    ctrl = PreemptionController("J0", params={"yellow_time": 3, "all_red_time": 2, "clearance_time": 3,
                                              "recovery_time": 5, "eta_threshold": 18}, provider=provider,
                                sumo_api=api)
    names = []
    for t in range(1, steps + 1):
        provider.update(float(t))
        ctrl.step(float(t))
        names.append((t, ctrl.preempt_state, api.trafficlight.state))
    return api.trafficlight.history, names, ctrl


def test_only_the_ev_approach_is_green_during_preemption() -> None:
    _, names, _ = _run()
    held = [s for _, st, s in names if st == "EV_GREEN"]
    assert held, "preemption never reached EV_GREEN"
    for s in held:
        assert s[4:8] == "GGGG", s  # east approach: every movement green (left turn protected)
        assert set(s[:4] + s[8:]) == {"r"}, s  # every other approach red


def test_transitions_are_safe() -> None:
    history, _, ctrl = _run()
    green = set("Gg")
    for prev, cur in zip(history, history[1:]):
        assert not ctrl.has_conflicting_greens(cur), cur
        for i, (a, b) in enumerate(zip(prev, cur)):
            # a light never jumps from green to red without yellow
            assert not (a in green and b == "r"), (i, prev, cur)
            # a light never turns green straight out of yellow
            assert not (a == "y" and b in green), (i, prev, cur)


def test_back_to_normal_control_after_the_ev() -> None:
    history, names, ctrl = _run()
    assert ctrl.preempt_state == "NORMAL"
    assert ctrl.preemption_records and ctrl.preemption_records[0]["vehicle_id"] == "amb1"
    # recovery gives the cross (north-south) traffic its green back
    assert any(st == "RECOVERY" and s == PHASES[0] for _, st, s in names)
    assert ctrl.adaptive_controller.transition_state == "STABLE"
