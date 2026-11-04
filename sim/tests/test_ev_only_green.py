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


class TurningBackProvider(EvProvider):
    """The EV is given green from the east, then turns back upstream and later arrives from the south.

    While it holds the east green the EV's route changes so that it now reaches this junction from
    the south. The junction must not keep the east green (and the south red) for good.
    """

    def get_ev_info(self, tls_id: str) -> List[Dict[str, Any]]:
        if self.t < 8:
            return [{"id": "amb1", "tls": "J0", "eta": 14 - self.t, "dist": (14 - self.t) * 12, "approach": "E"}]
        if self.t < 40:  # rerouted: now far away, coming from the south
            return [{"id": "amb1", "tls": "J0", "eta": 60 - self.t, "dist": (60 - self.t) * 10, "approach": "S"}]
        if self.t < 45:
            return [{"id": "amb1", "tls": "J0", "eta": 45 - self.t, "dist": (45 - self.t) * 10, "approach": "S"}]
        if self.t < 47:
            return [{"id": "amb1", "tls": "J0", "eta": 0, "dist": 0.0, "approach": "S"}]
        return []


def _run_with(provider, steps: int, **params):
    api = Api()
    p = {"yellow_time": 3, "all_red_time": 2, "clearance_time": 3, "recovery_time": 5, "eta_threshold": 18}
    p.update(params)
    ctrl = PreemptionController("J0", params=p, provider=provider, sumo_api=api)
    names = []
    for t in range(1, steps + 1):
        provider.update(float(t))
        ctrl.step(float(t))
        names.append((t, ctrl.preempt_state, ctrl.active_ev_approach, api.trafficlight.state))
    return api, names, ctrl


def test_ev_that_changes_approach_gets_green_on_its_new_approach() -> None:
    api, names, ctrl = _run_with(TurningBackProvider(), 90)
    # The east preemption ends once the EV is far away on another approach ...
    assert any(st == "NORMAL" and 12 <= t <= 40 for t, st, _, _ in names)
    # ... and the junction preempts again for the south approach when the EV comes close
    south_green = [s for t, st, app, s in names if st == "EV_GREEN" and app == "S"]
    assert south_green and all(s[8:12] == "GGGG" for s in south_green)
    assert ctrl.preempt_state == "NORMAL"
    for prev, cur in zip(api.trafficlight.history, api.trafficlight.history[1:]):
        assert not ctrl.has_conflicting_greens(cur), cur


class CloseApproachSwitchProvider(EvProvider):
    """The EV's approach changes while it is already close: the green moves to the new approach."""

    def get_ev_info(self, tls_id: str) -> List[Dict[str, Any]]:
        if self.t < 8:
            return [{"id": "amb1", "tls": "J0", "eta": 14 - self.t, "dist": (14 - self.t) * 12, "approach": "E"}]
        if self.t < 20:
            return [{"id": "amb1", "tls": "J0", "eta": 20 - self.t, "dist": (20 - self.t) * 10, "approach": "S"}]
        if self.t < 22:
            return [{"id": "amb1", "tls": "J0", "eta": 0, "dist": 0.0, "approach": "S"}]
        return []


def test_green_moves_safely_when_a_close_ev_changes_approach() -> None:
    api, names, ctrl = _run_with(CloseApproachSwitchProvider(), 60)
    assert any(st == "EV_GREEN" and app == "E" for _, st, app, _ in names)
    assert any(st == "EV_GREEN" and app == "S" and s[8:12] == "GGGG" for _, st, app, s in names)
    green = set("Gg")
    hist = api.trafficlight.history
    for prev, cur in zip(hist, hist[1:]):
        assert not ctrl.has_conflicting_greens(cur), cur
        for a, b in zip(prev, cur):
            assert not (a in green and b == "r") and not (a == "y" and b in green), (prev, cur)


class StuckEvProvider(EvProvider):
    """The EV reports itself 50 m away forever (stuck): the preemption must still end."""

    def get_ev_info(self, tls_id: str) -> List[Dict[str, Any]]:
        return [{"id": "amb1", "tls": "J0", "eta": 10.0, "dist": 50.0, "approach": "E"}]


def test_preemption_has_a_maximum_duration() -> None:
    _, names, _ = _run_with(StuckEvProvider(), 60, max_preempt_s=30)
    assert any(st == "RECOVERY" for _, st, _, _ in names)
