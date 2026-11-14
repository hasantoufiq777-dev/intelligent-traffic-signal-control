"""Ambulance on demand (sim/dispatch.py): an ambulance sent from the dashboard at any moment of a live
run gets signal priority at every junction it crosses, whatever the run's controller, safely and
with its trip reported. Runs without a dispatch are unchanged."""

from __future__ import annotations

import contextlib
import io
import json
from typing import Any, Dict, List

import pytest

from sim.controllers.fixed import FixedTimeController
from sim.controllers.preemption import PreemptionController
from sim.dispatch import AmbulanceDispatcher, add_priority
from sim.runner import run_simulation
from sim.tests.test_playback import Script


def _run(controller: str, plan: Dict[int, List[Dict[str, Any]]], network: str = "grid2x2",
         scenario: str = "normal", max_steps: int = 100):
    out = io.StringIO()
    params = {} if scenario == "accident_zone" else {"network": network}
    with contextlib.redirect_stdout(out):
        result = run_simulation(scenario, controller, seed=1, live=True, driver=True, driver_commands=Script(plan),
                                max_steps=max_steps, params=params, run_id="t_amb", step_delay=0.0)
    msgs = [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]
    events = [m for m in msgs if m["type"] == "event"]
    return result, events, msgs


def _kinds(events: List[Dict[str, Any]], prefix: str = "") -> List[str]:
    return [e["kind"] for e in events if e["kind"].startswith(prefix)]


# ---------------------------------------------------------------------------- units
def test_wrapping_keeps_the_running_controller() -> None:
    inner = FixedTimeController("J0")
    inner.current_phase = 2
    wrapped = PreemptionController("J0", params={}, inner=inner)
    assert wrapped.adaptive_controller is inner and wrapped.current_phase == 2
    assert wrapped.preempt_state == "NORMAL"


def test_add_priority_wraps_only_controllers_without_preemption() -> None:
    own = PreemptionController("J1", params={})
    ctrls: Dict[str, Any] = {"J0": FixedTimeController("J0"), "J1": own}
    fixed = ctrls["J0"]
    assert add_priority(ctrls, {"eta_threshold": 18.0}, provider=None, sumo_api=None) == ["J0"]
    assert isinstance(ctrls["J0"], PreemptionController) and ctrls["J0"].adaptive_controller is fixed
    assert ctrls["J1"] is own
    assert add_priority(ctrls, {}, provider=None, sumo_api=None) == []  # idempotent


class _Api:
    """Just enough of TraCI for the dispatcher's checks before it touches SUMO."""

    class edge:  # noqa: N801
        @staticmethod
        def getIDList():
            return ["A", "B", ":J0_0"]


def test_requests_are_checked_before_anything_is_added() -> None:
    calls: List[str] = []
    d = AmbulanceDispatcher(_Api(), ["J0"], {}, enable_priority=lambda: calls.append("priority") or [], seed=1)
    for cmd, reason in (({"from": "A", "to": "X"}, "unknown road X"), ({"from": "A", "to": "A"}, "same road"),
                        ({"from": ":J0_0", "to": "B"}, "unknown road :J0_0")):
        assert d.handle({"cmd": "ambulance", **cmd}, t=5) is None
        ev = d.pop_events()
        assert [e["kind"] for e in ev] == ["ambulance_rejected"] and reason in ev[0]["detail"]
    assert calls == [] and d.trips == [] and not d.priority_added
    assert d.get_metrics()["dispatched_ambulances"] == 0


# ---------------------------------------------------------------------------- real SUMO
@pytest.mark.parametrize("controller", ["fixed", "adaptive_vanet"])
def test_ambulance_sent_mid_run_gets_green_at_every_junction(controller: str) -> None:
    result, events, msgs = _run(controller, {20: [{"cmd": "ambulance"}]})
    sent = [e for e in events if e["kind"] == "ambulance_dispatched"]
    assert len(sent) == 1 and sent[0]["t"] == 20
    assert "ambulance priority switched on at J0, J1, J2, J3" in sent[0]["detail"]
    trip = result["dispatched_trips"][0]
    assert len(trip["junctions"]) == 3  # the longest crossing of the 2x2 grid
    assert trip["priority_at"] == trip["junctions"]  # green given at each junction it crossed
    assert result["dispatched_arrived"] == 1 and result["dispatched_avg_travel_time"] == trip["travel_time"]
    assert result["missed_preemptions"] == 0 and result["collisions"] == 0
    # events in order: dispatched, then preemption at the first junction, then arrival
    starts = [e for e in events if e["kind"] == "preempt_start" and "amb_dispatch_1" in e["detail"]]
    assert [e["tls"] for e in starts] == trip["junctions"]
    arrived = next(e for e in events if e["kind"] == "ambulance_arrived")
    assert sent[0]["t"] < starts[0]["t"] < arrived["t"]
    assert "green given at 3 of 3 junctions" in arrived["detail"]
    # it is on the live map and in the ambulance list while it drives
    assert any("amb_dispatch_1" in m.get("ev_routes", {}) for m in msgs if m["type"] == "state")
    assert any(r["id"] == "amb_dispatch_1" for m in msgs if m["type"] == "state" for r in m["ev"])
    # no conflicting greens: the preemption controller raises on one, which would have failed the run
    assert any(m["tls"].get("J2", {}).get("phase") == "EV_GREEN" for m in msgs if m["type"] == "state")


def test_priority_makes_the_ambulance_faster(monkeypatch: pytest.MonkeyPatch) -> None:
    with_priority, _, _ = _run("fixed", {20: [{"cmd": "ambulance"}]})
    monkeypatch.setattr("sim.dispatch.add_priority", lambda *a, **k: [])  # the same ambulance, no priority
    without, events, _ = _run("fixed", {20: [{"cmd": "ambulance"}]}, max_steps=160)
    assert without["dispatched_trips"][0]["priority_at"] == [] and "preempt_start" not in _kinds(events)
    assert with_priority["dispatched_trips"][0]["junctions"] == without["dispatched_trips"][0]["junctions"]
    assert with_priority["dispatched_avg_travel_time"] < without["dispatched_avg_travel_time"]


def test_own_priority_is_used_and_a_chosen_route_is_followed() -> None:
    result, events, _ = _run("adaptive_ev", {15: [{"cmd": "ambulance", "from": "E2J0", "to": "J02W"}],
                                             16: [{"cmd": "ambulance", "from": "E2J0", "to": "nowhere"}]},
                             network="single", max_steps=80)
    assert result["dispatched_priority_added"] == []  # adaptive_ev has its own ambulance priority
    trip = result["dispatched_trips"][0]
    assert (trip["from"], trip["to"], trip["priority_at"]) == ("E2J0", "J02W", ["J0"])
    assert "the junctions' own ambulance priority" in next(e for e in events if e["kind"] == "ambulance_dispatched")["detail"]
    rej = next(e for e in events if e["kind"] == "ambulance_rejected")
    assert rej["t"] == 16 and "unknown road nowhere" in rej["detail"]


def test_at_most_three_dispatched_ambulances_on_the_road() -> None:
    result, events, _ = _run("adaptive", {10: [{"cmd": "ambulance"}] * 4}, max_steps=30)
    assert _kinds(events, "ambulance_") == ["ambulance_dispatched"] * 3 + ["ambulance_rejected"]
    assert result["dispatched_ambulances"] == 3


def test_work_zone_run_gives_priority_and_keeps_the_zone_safe() -> None:
    result, _, _ = _run("adaptive", {20: [{"cmd": "ambulance"}]}, scenario="accident_zone", max_steps=110)
    trip = result["dispatched_trips"][0]
    assert trip["arrived_t"] is not None and trip["priority_at"] == trip["junctions"]
    assert result["collisions"] == 0 and result.get("zone_conflicts", 0) == 0


def test_a_run_without_a_dispatch_is_unchanged() -> None:
    plain, _, plain_msgs = _run("fixed", {})
    assert not any(k.startswith("dispatched") for k in plain)
    # the dispatcher is idle: the same states second by second as a run without live commands
    again, _, msgs = _run("fixed", {50: [{"cmd": "drive_step_unknown"}]})
    states = lambda ms: [(m["t"], m["metrics"], m["tls"]) for m in ms if m["type"] == "state"]  # noqa: E731
    assert states(msgs) == states(plain_msgs)
    keys = ("avg_waiting_time", "avg_travel_time", "max_queue", "throughput_veh_per_hr")
    assert {k: again[k] for k in keys} == {k: plain[k] for k in keys}
