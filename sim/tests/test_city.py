"""City network, vehicle types, lane permissions, A* routing, route explorer, planners, controller
factory and the fuel-efficient speed advice (supervisor enhancements, November 2026)."""

from __future__ import annotations

import contextlib
import io
import json
import math
from pathlib import Path
import random
import xml.etree.ElementTree as ET

import pytest

from sim.controllers.factory import CONTROLLERS, create_controller
from sim.eco_speed import curves, recommend
from sim.navigation.astar import astar_search
from sim.navigation.graph import EdgeGraph
from sim.navigation.planners import AcoPlanner, AStarPlanner, make_planner
from sim.navigation.route_explorer import explore
from sim.scenarios.generate_routes import generate_routes, load_scenario_config
from sim.sumo.generate_city_net import CITY_ENTRY, CITY_EXIT, check, reachable_pairs
from sim.tests.test_playback import Script
from sim.vehicles import CATALOG, VehicleTypeFactory, driven_type_id, is_driven_type, type_category

ROOT = Path(__file__).resolve().parents[2]
CITY_NET = ROOT / "sim" / "sumo" / "grid2x2_city.net.xml"
GRID_NET = ROOT / "sim" / "sumo" / "grid2x2.net.xml"
# Local sub-roads: 1 narrow lane, 30 km/h, no trucks or buses
LOCAL = {"kakb", "kbka", "kckd", "kdkc", "kekf", "kfke"}


@pytest.fixture(scope="module")
def city_graph() -> EdgeGraph:
    g = EdgeGraph.from_net_file(CITY_NET)
    g.junction_delay_s = 8.0
    return g


# ---------------------------------------------------------------------------- network and vehicles
def test_city_network_is_navigable_and_its_road_classes_really_differ() -> None:
    import sumolib

    # every exit reachable per class, no dead ends, four-legged junctions with 4-phase programs
    assert check() == []
    net = sumolib.net.readNet(str(CITY_NET), withPrograms=True)
    by_type = {}
    for e in net.getEdges():
        by_type.setdefault(e.getType(), set()).add((e.getLaneNumber(), round(sum(ln.getWidth() for ln in e.getLanes()), 2),
                                                    round(e.getSpeed(), 2)))
    assert {t: {x[0] for x in v} for t, v in by_type.items()} == {"main": {3}, "secondary": {2}, "collector": {2}, "local": {1}}
    widths = {t: min(x[1] for x in v) for t, v in by_type.items()}
    assert widths["main"] > widths["secondary"] > widths["collector"] > widths["local"]
    speeds = {t: max(x[2] for x in v) for t, v in by_type.items()}
    assert speeds["main"] > speeds["secondary"] > speeds["collector"] > speeds["local"]
    # local roads and inner lanes are closed to trucks and buses; the junction areas are not uniform
    for e in net.getEdges():
        if e.getType() == "local":
            assert not e.allows("truck") and not e.allows("bus") and e.allows("passenger")
        else:  # heavy vehicles: outer lanes only (inner lanes carry the left turns)
            assert e.getLane(0).allows("truck") and not e.getLane(e.getLaneNumber() - 1).allows("truck")
    spacing = sorted(round(net.getNode(a).getCoord()[0] - net.getNode(b).getCoord()[0]) for a, b in (("J1", "J0"), ("J3", "J2")))
    assert spacing[0] != spacing[1]  # irregular spacing
    assert any(len(e.getShape()) > 2 for e in net.getEdges() if e.getType() == "local")  # bends


def test_vehicle_factory_writes_real_sumo_types() -> None:
    root = ET.Element("routes")
    VehicleTypeFactory().vtype_elements(root, CATALOG.keys())
    types = {t.get("id"): t for t in root.findall("vType")}
    assert set(types) == set(CATALOG)
    assert types["truck"].get("vClass") == "truck" and float(types["truck"].get("length")) == 10.0
    assert types["motorcycle"].get("vClass") == "motorcycle"
    assert types["bus"].get("emissionClass").startswith("HBEFA4/UBus")
    assert types["eco_car"].get("color") == "22,163,74"  # the one eco colour
    assert all(t.get("speedFactor").startswith("normc(1.0") for k, t in types.items() if k != "emergency")
    assert type_category("driven_truck") == "truck" and type_category("driven") == "car" and type_category("x") == "car"
    assert is_driven_type("driven") and is_driven_type("driven_bus") and not is_driven_type("car")
    assert driven_type_id("car") == "driven" and driven_type_id("bus") == "driven_bus"


def test_city_trips_mix_categories_and_never_force_heavy_vehicles_through_the_grid(tmp_path: Path) -> None:
    cfg = load_scenario_config(ROOT / "sim" / "scenarios" / "city.yaml")
    out = tmp_path / "r.rou.xml"
    generate_routes(cfg, out, override_seed=3, network_override="grid2x2_city")
    trips = ET.parse(out).getroot().findall("trip")
    kinds = {t.get("type") for t in trips}
    assert {"car", "eco_car", "motorcycle", "bus", "truck"} <= kinds
    assert all(t.get("via") is None for t in trips if t.get("type") in ("bus", "truck"))
    ends = {t.get("from") for t in trips} | {t.get("to") for t in trips}
    assert {"gWJ0", "J1gE", "gEJ1", "J0gW"} <= ends  # the main corridor carries traffic
    out2 = tmp_path / "r2.rou.xml"
    generate_routes(cfg, out2, override_seed=3, network_override="grid2x2_city")
    assert out.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8")  # same seed, same trips


# ---------------------------------------------------------------------------- A*
def test_lane_permissions_are_respected_by_the_router(city_graph: EdgeGraph) -> None:
    legal = reachable_pairs("truck")
    assert len(legal) >= 24  # truck routes cover most exit pairs; every exit can be left and reached
    assert {a for a, _ in legal} == set(CITY_ENTRY) and {b for _, b in legal} == set(CITY_EXIT)
    for ka, a in CITY_ENTRY.items():
        for kb, b in CITY_EXIT.items():
            if ka == kb:
                continue
            path, cost, _ = astar_search(city_graph, a, b, vclass="truck")
            assert bool(path) == ((ka, kb) in legal), (ka, kb)  # A* agrees with SUMO's router
            if path:
                assert not LOCAL & set(path), path  # trucks never use local sub-roads
                # and never turn left at a signal: every turn into a road leaving a signal is straight or right
                for x, y in zip(path, path[1:]):
                    assert y in city_graph.get_successors(x, "truck")
    # on the research grid every lane allows every class: routes identical with and without a class
    grid = EdgeGraph.from_net_file(GRID_NET)
    for a, b in (("bottom0J0", "J3top1"), ("left1J2", "J1right0"), ("top0J2", "J1bottom1")):
        assert astar_search(grid, a, b) == astar_search(grid, a, b, vclass="passenger")


def test_astar_heuristic_is_admissible_and_astar_equals_dijkstra(city_graph: EdgeGraph) -> None:
    rng = random.Random(7)
    edges = sorted(city_graph.edges)
    for _ in range(40):
        a, b = rng.sample(edges, 2)
        speeds = {e: rng.uniform(1.0, 16.0) for e in edges if rng.random() < 0.5}  # congestion somewhere
        p1, c1, _ = astar_search(city_graph, a, b, speed_map=speeds)
        p0, c0, _ = astar_search(city_graph, a, b, speed_map=speeds, heuristic="zero")  # Dijkstra
        assert math.isclose(c1, c0, rel_tol=1e-9, abs_tol=1e-9)
        if p1:
            ea, eb = city_graph.edges[a], city_graph.edges[b]
            h = math.hypot(ea.end_coord[0] - eb.end_coord[0], ea.end_coord[1] - eb.end_coord[1]) / city_graph.max_network_speed
            assert h <= c1 + 1e-9  # h never overestimates the real remaining cost


def test_distance_search_finds_the_shortest_route(city_graph: EdgeGraph) -> None:
    import sumolib

    net = sumolib.net.readNet(str(CITY_NET))
    for a, b in (("gWJ0", "J3gE3"), ("gEJ1", "kcgS0"), ("gS1kd", "kegN2")):
        path, cost, _ = astar_search(city_graph, a, b, cost="distance", vclass="passenger")
        ref, _ = net.getShortestPath(net.getEdge(a), net.getEdge(b), vClass="passenger")
        ref_len = sum(e.getLength() for e in ref[1:])
        assert math.isclose(cost, ref_len, rel_tol=1e-6), (path, [e.getID() for e in ref])


def test_route_explorer_shows_that_the_shorter_route_is_not_always_faster(city_graph: EdgeGraph) -> None:
    r = explore(city_graph, "gEJ1", "kcgS0", vclass="passenger")
    assert r["ok"] and len(r["routes"]) >= 2
    fastest = next(x for x in r["routes"] if x["fastest"])
    shortest = next(x for x in r["routes"] if x["shortest"])
    assert r["shorter_not_faster"] and fastest is not shortest
    assert shortest["distance_m"] < fastest["distance_m"] and shortest["time_now_s"] > fastest["time_now_s"]
    # congestion on the fastest route makes another one fastest
    jam = {e: 2.0 for e in fastest["path"][1:-1]}
    r2 = explore(city_graph, "gEJ1", "kcgS0", speed_map=jam, vclass="passenger")
    assert next(x for x in r2["routes"] if x["fastest"])["path"] != fastest["path"]
    # units: seconds and metres; h(start) = straight line / v_max
    st = r["astar"]
    assert math.isclose(st["h_start_s"], st["straight_m"] / st["v_max"], abs_tol=0.11)
    assert LOCAL & set(shortest["path"]) and not LOCAL & set(fastest["path"])  # the short-cut is the slow local road
    truck = explore(city_graph, "gEJ1", "kegN2", vclass="truck")
    assert truck["ok"] and all(not LOCAL & set(x["path"]) for x in truck["routes"])
    assert not explore(city_graph, "gEJ1", "kcgS0", vclass="truck")["ok"]  # no legal truck route there
    assert not explore(city_graph, "gEJ1", "nowhere")["ok"]


def test_planner_factory_and_strategies(city_graph: EdgeGraph) -> None:
    assert make_planner("none", city_graph) is None
    with pytest.raises(ValueError):
        make_planner("teleport", city_graph)
    assert isinstance(make_planner("astar", city_graph), AStarPlanner)
    assert isinstance(make_planner("aco", city_graph, rng=random.Random(1)), AcoPlanner)
    res = make_planner("astar", city_graph).plan("gWJ0", "J3gE3", None, None, "passenger")
    assert res.path == astar_search(city_graph, "gWJ0", "J3gE3", vclass="passenger")[0]
    aco1 = make_planner("aco", city_graph, rng=random.Random(5)).plan("gWJ0", "J3gE3", None, None, "passenger")
    aco2 = make_planner("aco", city_graph, rng=random.Random(5)).plan("gWJ0", "J3gE3", None, None, "passenger")
    assert aco1.path == aco2.path and math.isclose(sum(p for _, _, p in aco1.alternatives), 1.0, abs_tol=1e-6)


def test_controller_factory_maps_every_name() -> None:
    assert set(CONTROLLERS) >= {"fixed", "adaptive", "adaptive_vanet", "adaptive_ev", "adaptive_ev_vanet",
                                "adaptive_ev_vanet_tuned"}
    assert type(create_controller("fixed", "J0", {}, provider=None)).__name__ == "FixedTimeController"
    assert type(create_controller("adaptive_ev_vanet", "J0", {}, provider=None)).__name__ == "PreemptionController"
    with pytest.raises(ValueError):
        create_controller("magic", "J0", {}, provider=None)


# ---------------------------------------------------------------------------- fuel-efficient speed
def test_fuel_curves_exist_for_every_drivable_category() -> None:
    c = curves()
    for k in ("car", "eco_car", "motorcycle", "bus", "truck"):
        assert len(c[k]["speeds_kmh"]) == len(c[k]["fuel_mg_per_m"]) > 5 and c[k]["idle_fuel_mg_s"] >= 0
    # an eco car (Euro 6d) burns less per km than an ordinary car (Euro 4) at the same speed
    assert min(c["eco_car"]["fuel_mg_per_m"]) < min(c["car"]["fuel_mg_per_m"])


def test_speed_advice_avoids_a_stop_and_respects_every_limit() -> None:
    r = recommend("car", 13.9, 13.89, light={"dist": 200, "signal": "r", "green_in": 20})
    assert r["estimated"] and r["action"] == "reduce" and not r["recommended"]["stop"] and r["current"]["stop"]
    assert r["recommended"]["fuel_ml"] < r["current"]["fuel_ml"]
    assert any(x["code"] == "light_slow" for x in r["reasons"])
    lead = recommend("truck", 11.1, 16.67, leader={"gap": 20, "speed": 4.0})
    assert lead["target_kmh"] <= (4.0 + 0.5) * 3.6 + 0.1 and any(x["code"] == "leader" for x in lead["reasons"])
    jam = recommend("bus", 12.0, 16.67, traffic_speed=3.0)
    assert jam["target_kmh"] <= (3.0 + 2.0) * 3.6 + 0.1 and any(x["code"] == "congestion" for x in jam["reasons"])
    for kind in ("car", "motorcycle", "bus", "truck", "eco_car"):
        for limit in (8.33, 13.89, 16.67):
            assert recommend(kind, 5.0, limit, road_left_m=250)["target_kmh"] <= round(limit * 3.6, 1)  # shown to 0.1 km/h
    keep = recommend("car", 13.4, 13.89, light={"dist": 150, "signal": "G", "green_ends_in": 40})
    assert keep["action"] == "maintain"


# ---------------------------------------------------------------------------- a real city run
def _city_run(plan, max_steps=150):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = run_simulation("city", "adaptive_vanet", seed=2, live=True, driver=True, driver_commands=Script(plan),
                                max_steps=max_steps, run_id="t_city", step_delay=0.0, penetration=0.5, nav="astar")
    return result, [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]


from sim.runner import run_simulation  # noqa: E402  (after the helpers above, as in the other live tests)


def test_city_run_half_equipped_lanes_respected_and_the_driver_capped() -> None:
    import sumolib

    plan = {5: [{"cmd": "drive_start", "from": "gWJ0", "to": "kdgS1", "kind": "truck"}],
            20: [{"cmd": "drive", "mode": "ease", "speed": 50.0}],  # 180 km/h asked: never above the limit
            40: [{"cmd": "routes", "qid": "q", "from": "gEJ1", "to": "kegN2", "vclass": "bus"}]}
    result, msgs = _city_run(plan)
    assert result["collisions"] == 0
    net = sumolib.net.readNet(str(CITY_NET))
    vclass_of = {k: CATALOG[k].vclass for k in CATALOG}
    seen_kinds, equipped, ordinary = set(), 0, 0
    for m in (m for m in msgs if m["type"] == "vehicles"):
        f = m["fields"]
        for row in m["rows"]:
            kind, road, lane, flags = row[f.index("kind")], row[f.index("road")], row[f.index("lane")], row[f.index("flags")]
            seen_kinds.add(kind)
            if not road.startswith(":"):
                assert net.getEdge(road).getLane(lane).allows(vclass_of.get(kind, "passenger")), (row[0], kind, road, lane)
            if kind in ("car", "motorcycle", "bus", "truck") and not row[0].startswith("drv"):
                ordinary += 1
                equipped += 1 if flags & 16 else 0
            if kind in ("eco_car", "emergency"):
                assert flags & 16  # always connected
    assert {"car", "eco_car", "truck"} <= seen_kinds
    assert 0.35 < equipped / ordinary < 0.65  # 50 % penetration among ordinary vehicles
    drv = [m for m in msgs if m["type"] == "driver" and m.get("status") == "driving"]
    assert drv and all(m["kind"] == "truck" for m in drv)
    assert all(m["speed_kmh"] <= m["limit_kmh"] + 0.5 for m in drv)  # the 180 km/h request was capped
    assert any(m.get("eco_speed", {}) and m["eco_speed"]["target_kmh"] <= m["limit_kmh"] + 0.01 for m in drv)
    routes = next(m for m in msgs if m["type"] == "routes")
    assert routes["ok"] and routes["qid"] == "q" and routes["vclass"] == "bus"
    assert all(not LOCAL & set(r["path"]) for r in routes["routes"])
