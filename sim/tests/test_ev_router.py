"""Emergency-vehicle routing (sim/navigation/ev_router.py) and its use in the simulation."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim.navigation.ev_router import EmergencyRouter
from sim.navigation.graph import EdgeGraph
from sim.runner import run_simulation

GRID = Path(__file__).resolve().parent.parent / "sumo" / "grid2x2.net.xml"
DEFAULT_ROUTE = ["bottom0J0", "J0J1", "J1J3", "J3top1"]
OTHER_ROUTE = ["bottom0J0", "J0J2", "J2J3", "J3top1"]


@pytest.fixture(scope="module")
def graph() -> EdgeGraph:
    return EdgeGraph.from_net_file(GRID)


class _Veh:
    def __init__(self, road: str, route: list, vtype: str = "emergency") -> None:
        self.road, self.route, self.vtype, self.set_routes = road, list(route), vtype, []

    def getTypeID(self, vid):
        return self.vtype

    def getRoadID(self, vid):
        return self.road

    def getRoute(self, vid):
        return list(self.route)

    def getRouteIndex(self, vid):
        return self.route.index(self.road)

    def setRoute(self, vid, path):
        self.set_routes.append(list(path))
        self.route = list(path)


class _Sumo:
    def __init__(self, veh: _Veh) -> None:
        self.vehicle = veh


def _router(graph, veh, **kw):
    kw.setdefault("speed_smoothing_s", 0.0)
    return EmergencyRouter(sumo_api=_Sumo(veh), graph=graph, **kw)


def test_closure_ahead_triggers_a_reroute_at_departure(graph) -> None:
    veh = _Veh("bottom0J0", DEFAULT_ROUTE)
    r = _router(graph, veh)
    r.step(240.0, ["amb1"], speed_map={}, closed_edges={"J1J3"})
    assert veh.set_routes == [OTHER_ROUTE]
    assert r.get_metrics()["ev_reroutes_closure"] == 1
    ev = r.pop_events()
    assert ev and ev[0]["event"] == "ev_reroute" and "closed road" in ev[0]["detail"]


def test_congested_route_is_avoided_when_clearly_faster(graph) -> None:
    veh = _Veh("bottom0J0", DEFAULT_ROUTE)
    r = _router(graph, veh)
    r.step(300.0, ["amb1"], speed_map={"J0J1": 2.0})  # queue on the next edge: ~115 s instead of ~17 s
    assert veh.set_routes == [OTHER_ROUTE]
    assert r.reroutes[0]["reason"] == "faster" and r.reroutes[0]["gain_s"] > 50


def test_small_gain_keeps_the_route(graph) -> None:
    veh = _Veh("bottom0J0", DEFAULT_ROUTE)
    r = _router(graph, veh)
    r.step(300.0, ["amb1"], speed_map={"J0J1": 12.0})  # about 2 s slower than free flow
    assert veh.set_routes == []


def test_checks_only_on_a_new_edge_or_a_new_closure(graph) -> None:
    veh = _Veh("bottom0J0", DEFAULT_ROUTE)
    r = _router(graph, veh)
    r.step(1.0, ["amb1"], speed_map={})
    r.step(2.0, ["amb1"], speed_map={})
    assert r.checks == 1                       # same edge: no new check
    r.step(3.0, ["amb1"], speed_map={}, closed_edges={"J3top1"})
    assert r.checks == 2                       # a new closure: checked at once (no way around it)
    assert veh.set_routes == []


def test_ordinary_cars_are_ignored(graph) -> None:
    veh = _Veh("bottom0J0", DEFAULT_ROUTE, vtype="car")
    r = _router(graph, veh)
    r.step(240.0, ["car1"], speed_map={}, closed_edges={"J1J3"})
    assert veh.set_routes == [] and r.checks == 0


def test_ambulance_plans_around_a_closure_in_the_simulation() -> None:
    """ev_incident: J1J3 is closed when the ambulance departs. With routing it goes via J2."""
    common = dict(scenario="ev_incident", controller="adaptive_ev_vanet", seed=1, max_steps=420)
    on = run_simulation(ev_routing=True, run_id="t_evr_on", **common)
    off = run_simulation(ev_routing=False, run_id="t_evr_off", **common)
    assert on["ev_reroutes"] == 1 and on["ev_reroutes_closure"] == 1
    assert off["ev_reroutes"] is None
    # Both arrive (without routing it turns back at J1 and must still get green at J0 again)
    assert on["ev_travel_time"] > 0 and off["ev_travel_time"] > 0
    assert on["ev_travel_time"] < off["ev_travel_time"]
    assert on["missed_preemptions"] == 0 and off["missed_preemptions"] == 0
