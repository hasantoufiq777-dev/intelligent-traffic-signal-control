"""Accident work zone (grid2x2_zone): alternating one-way traffic on a shared lane, the closure
alternative, ambulance priority, and that the rest of the grid is the same as grid2x2."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from sim.runner import run_simulation
from sim.scenarios.accident_zone import expand_route

SUMO_DIR = Path(__file__).resolve().parent.parent / "sumo"


def _run(control: str = "alternating", seed: int = 1, live: bool = False, **kw: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    params = {"accident_zone": {"control": control}}
    params["accident_zone"].update(kw.pop("zone", {}))
    args = dict(scenario="accident_zone", controller="adaptive_ev_vanet", seed=seed, params=params,
                run_id=f"t_zone_{control}_{seed}")
    args.update(kw)
    if not live:
        return run_simulation(**args), []
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        res = run_simulation(live=True, **args)
    return res, [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]


@pytest.fixture(scope="module")
def alternating_live():
    return _run("alternating", seed=1, live=True)


def test_zone_network_keeps_the_grid_and_adds_one_contraflow_lane() -> None:
    import sumolib

    base = sumolib.net.readNet(str(SUMO_DIR / "grid2x2.net.xml"))
    zone = sumolib.net.readNet(str(SUMO_DIR / "grid2x2_zone.net.xml"))
    for j in ("J0", "J1", "J2", "J3"):
        a = sorted((c.getFrom().getID().split(".")[0], c.getTo().getID().split(".")[0], c.getDirection())
                   for c in base.getNode(j).getConnections())
        b = sorted((c.getFrom().getID().split(".")[0], c.getTo().getID().split(".")[0], c.getDirection())
                   for c in zone.getNode(j).getConnections())
        assert a == b, f"junction {j} differs from grid2x2"
    cf = zone.getEdge("J3J2.cf")
    assert cf.getLaneNumber() == 1 and cf.getFromNode().getID() == "ZE" and cf.getToNode().getID() == "ZW"
    # Only the inner westbound lane feeds the contraflow lane (no merge inside the junction)
    feeds = [c for c in zone.getEdge("J3J2").getOutgoing().get(cf, [])]
    assert [c.getFromLane().getIndex() for c in feeds] == [1]
    assert {t.getID() for t in zone.getTrafficLights()} == {"J0", "J1", "J2", "J3", "ZW", "ZE"}


def test_routes_are_expanded_over_the_split_road() -> None:
    assert expand_route(["right1J3", "J3J2", "J2J0", "J0left0"]) == [
        "right1J3", "J3J2", "J3J2.z", "J3J2.w", "J2J0", "J0left0"]
    assert expand_route(["left1J2", "J2J3", "J3right1"]) == ["left1J2", "J2J3", "J2J3.z", "J2J3.e", "J3right1"]
    assert expand_route(["bottom0J0", "J0J1"]) == ["bottom0J0", "J0J1"]


def test_alternating_turns_are_safe_and_serve_both_directions(alternating_live) -> None:
    res, msgs = alternating_live
    # Never both directions in the shared lane, no collisions anywhere
    assert res["zone_conflicts"] == 0 and res["collisions"] == 0
    # On after the VANET detected the crash (start 150 s + detection), off after it was cleared (510 s)
    assert res["incident_detected"] is True
    assert 150 < res["zone_activated_at"] < 220 and 510 <= res["zone_deactivated_at"] < 560
    assert res["zone_passed_eastbound"] > 10 and res["zone_passed_westbound"] > 10
    assert res["zone_switches"] >= 6 and res["zone_max_clearance_s"] < 30
    assert res["zone_rerouted"] > 0  # westbound routes moved onto the contraflow lane

    states = [m for m in msgs if m["type"] == "state" and "zone" in m]
    assert states, "the state message carries the zone block"
    for m in states:
        sig = m["zone"]["signal"]
        assert not (sig["E"] == "G" and sig["W"] == "G"), f"both entries green at {m['t']}s"
        if m["zone"]["mode"] == "alternating" and m["zone"]["phase"] == "green":
            other = "W" if m["zone"]["dir"] == "E" else "E"
            assert sig[other] == "r"
    modes = {m["zone"]["mode"] for m in states}
    assert modes == {"normal", "alternating"}
    net = next(m for m in msgs if m["type"] == "network")
    assert net["zone"]["contraflow_edge"] == "J3J2.cf" and set(net["zone"]["entry"]) == {"E", "W"}
    kinds = {m["kind"] for m in msgs if m["type"] == "event"}
    assert {"zone_activated", "zone_switch", "zone_deactivated", "zone_ev_priority"} <= kinds


def test_after_the_accident_the_contraflow_lane_is_unused(alternating_live) -> None:
    _res, msgs = alternating_live
    rows_after = [r for m in msgs if m["type"] == "vehicles" and m["t"] > 600 for r in m["rows"]]
    assert rows_after
    # Contraflow lane: y = 495.2 heading west (angle ~270) between the zone ends
    on_cf = [r for r in rows_after if 344 <= r[1] <= 416 and abs(r[2] - 495.2) < 0.5 and 225 <= r[3] <= 315]
    assert not on_cf


def test_closure_detours_and_never_uses_the_shared_lane() -> None:
    res, _ = _run("closure", seed=1)
    assert res["zone_control"] == "closure"
    assert res["zone_passed_westbound"] == 0 and res["zone_switches"] == 0
    assert res["zone_rerouted"] > 10  # sent around the closed road at J3
    assert res["zone_conflicts"] == 0 and res["collisions"] == 0


def test_ambulance_gets_the_next_turn() -> None:
    # Seed 2: without priority the ambulance waits at the zone line; with priority it does not
    on, _ = _run("alternating", seed=2)
    off, _ = _run("alternating", seed=2, zone={"ev_eta_s": 0.0})
    assert on["zone_ev_priority"] == 1 and off["zone_ev_priority"] == 0
    assert on["zone_ev_wait_s"] == 0.0 < off["zone_ev_wait_s"]
    assert on["ev_travel_time"] < off["ev_travel_time"]
    assert on["zone_conflicts"] == 0 and on["collisions"] == 0
