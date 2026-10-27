"""Eco features: announced light changes, fuel-based routing and idle time per group."""

from __future__ import annotations

import math

import pytest

from sim.analytics.metrics import parse_tripinfo_emissions
from sim.controllers.dynamic import DynamicController
from sim.controllers.leveling import LevelingController
from sim.eco_speed import eco_edge_cost, fuel_per_m, min_fuel_per_m
from sim.navigation.graph import EdgeGraph, EdgeNode
from sim.navigation.planners import EcoPlanner
from sim.tests.test_leveling import Feed, PARAMS, road

COMMIT = dict(PARAMS, commit_green_s=8)


def level(feed, serving="N", params=COMMIT):
    c = LevelingController("J0", params=dict(params))
    c.monitor = feed
    c.current_phase = c._code(serving)
    return c


def test_commitment_is_off_by_default():
    c = level(Feed(N=4, E=4), params=PARAMS)
    for i in range(1, 5):
        c.step(float(i))
    assert c._commit is None and c.get_live_info()["locked_s"] is None


def test_locked_green_broadcasts_the_exact_change_and_next_green():
    c = level(Feed(N=4, E=2))
    c.step(1.0)
    assert c._commit is not None
    end, target = c._commit
    assert target == "E"
    spat = c.get_spat()
    assert spat["N"]["min_s"] == spat["N"]["max_s"] == end - c.elapsed
    assert spat["E"]["next_green_min_s"] == spat["E"]["next_green_max_s"] == end - c.elapsed + 3 + 2


def test_the_change_happens_exactly_when_announced():
    c = level(Feed(N=4, E=2))
    c.step(1.0)
    end = c._commit[0]
    t = 1
    while not c.is_in_transition() and t < 30:
        t += 1
        c.step(float(t))
    assert t == end and c._target_group() == "E"
    assert c._commit is None


def test_a_locked_green_ignores_new_queues_until_it_ends():
    feed = Feed(N=4, E=2)
    c = level(feed)
    c.step(1.0)
    target = c._commit[1]
    feed.stats["W"] = road("W", 30)
    c.step(2.0)
    assert c._commit[1] == target and c.serving() == "N"


def test_no_lock_when_nobody_else_is_there():
    c = level(Feed(N=4))
    for i in range(1, 12):
        c.step(float(i))
    assert c._commit is None and c.serving() == "N"


def test_closed_target_is_replaced_at_the_change():
    feed = Feed(N=4, E=2, S=1)
    c = level(feed)
    c.step(1.0)
    feed.stats["E"] = road("E", 2, closed=True)
    feed.stats["W"] = road("W", 3)
    t = 1
    while not c.is_in_transition() and t < 30:
        t += 1
        c.step(float(t))
    assert c._target_group() == "W"


def test_dynamic_controller_announces_its_change_too():
    c = DynamicController("J0", params=dict(COMMIT))
    c.monitor = Feed(N=4, E=3)
    c.step(1.0)
    assert c._commit is not None and c._commit[1] == "EW"
    low, high = c._green_remaining_bounds()
    assert low == high


def test_eco_cost_adds_idle_and_restart_only_at_a_signal():
    free = eco_edge_cost("car", 200.0, 13.89)
    assert free == pytest.approx(fuel_per_m("car", 13.89) * 200.0)
    assert eco_edge_cost("car", 200.0, 13.89, signal_delay_s=8.0) > free + 8 * 400
    assert eco_edge_cost("car", 200.0, 3.0) > free  # a congested road costs more fuel


def line_graph() -> EdgeGraph:
    """s -> a -> g is short but slow; s -> b -> c -> g is longer but free-flowing."""
    g = EdgeGraph()
    coords = {"s": 0, "a": 300, "b": 240, "c": 480, "g": 400}  # road ends: straight lines never exceed the road distance
    links = {"s": ["a", "b"], "a": ["g"], "b": ["c"], "c": ["g"], "g": []}
    lengths = {"s": 100, "a": 300, "b": 240, "c": 240, "g": 100}
    for e, succ in links.items():
        g.edges[e] = EdgeNode(e, float(lengths[e]), 13.89, (coords[e] - 100.0, 0.0), (float(coords[e]), 0.0), successors=succ)
    return g


def test_eco_route_avoids_the_jam_even_if_longer():
    g = line_graph()
    p = EcoPlanner(g)
    res = p.plan("s", "g", {"a": 1.5})
    assert res.path == ["s", "b", "c", "g"]
    assert res.cost == pytest.approx(p.route_cost(res.path[1:], "car", {"a": 1.5}))


def test_eco_route_takes_the_short_road_when_it_is_free():
    res = EcoPlanner(line_graph()).plan("s", "g", {})
    assert res.path == ["s", "a", "g"]


def test_eco_route_never_uses_a_closed_road():
    res = EcoPlanner(line_graph()).plan("s", "g", {}, closed_edges={"a"})
    assert "a" not in res.path
    assert EcoPlanner(line_graph()).edge_cost("a", "car", closed_edges={"a"}) == math.inf


def test_heuristic_is_a_lower_bound():
    assert min_fuel_per_m("car") <= fuel_per_m("car", 13.89)


def test_idle_time_is_reported_per_group(tmp_path):
    xml = tmp_path / "t.xml"
    xml.write_text("""<tripinfos>
      <tripinfo id="e" vType="car" routeLength="1000" duration="100" waitingTime="4" waitingCount="1"><emissions CO2_abs="1" fuel_abs="1"/></tripinfo>
      <tripinfo id="n" vType="car" routeLength="1000" duration="120" waitingTime="20" waitingCount="2"><emissions CO2_abs="1" fuel_abs="1"/></tripinfo>
    </tripinfos>""", encoding="utf-8")
    out = parse_tripinfo_emissions(xml, group_fn=lambda vid, _: "eco" if vid == "e" else "non")
    assert (out["idle_s_per_vehicle_eco"], out["idle_s_per_vehicle_non"], out["idle_s_per_vehicle"]) == (4.0, 20.0, 12.0)
