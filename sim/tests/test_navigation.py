"""Unit and integration tests for Module 9: Navigation with A* and ACO.

Tests:
1. A* with heuristic 'zero' equals Dijkstra; A* with 'euclid' achieves identical cost with <= expansions.
2. ACO produces valid contiguous routes, and top path cost is within small factor of A* optimum.
3. Closed edges (from incidents or closures) are strictly bypassed by both A* and ACO.
4. ACO is strictly deterministic and reproducible for the same seed.
5. Simulation integration: navigating vehicles perform valid dynamic reroutes and report nav metrics.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from sim.navigation.aco import AcoRouter
from sim.navigation.astar import astar_search
from sim.navigation.graph import EdgeGraph
from sim.runner import run_simulation

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GRID_NET_PATH = PROJECT_ROOT / "sim" / "sumo" / "grid2x2.net.xml"


@pytest.fixture(scope="module")
def grid_graph() -> EdgeGraph:
    """Fixture to load grid2x2 network graph once for tests."""
    assert GRID_NET_PATH.exists(), f"Missing grid2x2 network at {GRID_NET_PATH}"
    return EdgeGraph.from_net_file(GRID_NET_PATH)


def test_astar_zero_equals_dijkstra_and_euclid_expansions_le_zero(grid_graph: EdgeGraph) -> None:
    """Verify A* zero heuristic equals Dijkstra, and Euclidean heuristic expands <= nodes."""
    od_pairs = [
        ("bottom0J0", "top1J3"),
        ("left0J0", "right1J3"),
        ("bottom1J1", "top0J2"),
        ("left1J2", "right0J1"),
    ]

    for start_edge, goal_edge in od_pairs:
        # 1. Dijkstra (zero heuristic)
        path_dijkstra, cost_dijkstra, exp_dijkstra = astar_search(
            graph=grid_graph,
            start_edge=start_edge,
            goal_edge=goal_edge,
            heuristic="zero",
        )

        # 2. A* (Euclidean heuristic)
        path_euclid, cost_euclid, exp_euclid = astar_search(
            graph=grid_graph,
            start_edge=start_edge,
            goal_edge=goal_edge,
            heuristic="euclid",
        )

        assert path_dijkstra, f"Dijkstra failed to find path from {start_edge} to {goal_edge}"
        assert path_euclid, f"A* Euclid failed to find path from {start_edge} to {goal_edge}"

        # Costs must be identical (both guaranteed optimal)
        assert cost_euclid == pytest.approx(cost_dijkstra, abs=1e-2), (
            f"Cost mismatch: A* ({cost_euclid}) vs Dijkstra ({cost_dijkstra})"
        )

        # A* must expand fewer or equal nodes than uninformed Dijkstra
        assert exp_euclid <= exp_dijkstra, (
            f"A* expansions ({exp_euclid}) exceeded Dijkstra expansions ({exp_dijkstra})"
        )

        # Verify path connectivity
        for i in range(len(path_euclid) - 1):
            u, v = path_euclid[i], path_euclid[i + 1]
            assert v in grid_graph.get_successors(u), f"Invalid transition: {u} -> {v}"


def test_aco_returns_valid_connected_routes_near_optimum(grid_graph: EdgeGraph) -> None:
    """Verify ACO generates valid connected paths whose best cost is near A* optimum."""
    start_edge = "bottom0J0"
    goal_edge = "top1J3"

    # Reference A* optimum
    _, opt_cost, _ = astar_search(grid_graph, start_edge, goal_edge, heuristic="euclid")

    router = AcoRouter(
        graph=grid_graph,
        config={
            "alpha": 1.0,
            "beta": 2.0,
            "rho": 0.1,
            "Q": 100.0,
            "n_ants": 15,
            "n_iterations": 15,
            "elite_k": 3,
        },
        seed=42,
    )

    candidates = router.find_routes(start_edge, goal_edge, k=3)
    assert len(candidates) >= 1, "ACO returned no candidates"

    # Check validity of every candidate path
    for path, cost, prob in candidates:
        assert path[0] == start_edge
        assert path[-1] == goal_edge
        assert prob > 0.0
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            assert v in grid_graph.get_successors(u), f"ACO returned disconnected transition: {u} -> {v}"

    best_aco_cost = candidates[0][1]
    # ACO best cost must be within 15% of optimal
    assert best_aco_cost <= (opt_cost * 1.15), (
        f"ACO best cost ({best_aco_cost}) diverged significantly from A* optimum ({opt_cost})"
    )


def test_closed_edge_is_never_used(grid_graph: EdgeGraph) -> None:
    """Verify that closed edges (due to incidents) are strictly avoided by both A* and ACO."""
    start_edge = "bottom0J0"
    goal_edge = "top1J3"
    # In default grid, bottom0J0 -> J0J1 -> J1J3 -> J3top1 is a shortest path.
    # We close edge J0J1.
    closed = {"J0J1"}

    # 1. A*
    path_astar, cost_astar, _ = astar_search(
        graph=grid_graph,
        start_edge=start_edge,
        goal_edge=goal_edge,
        closed_edges=closed,
    )
    assert path_astar, "A* failed to find detour around closed edge"
    assert "J0J1" not in path_astar, "A* path traversed a closed edge!"
    assert "J0J2" in path_astar, "Expected A* to detour via J0J2"

    # 2. ACO
    router = AcoRouter(graph=grid_graph, seed=99)
    candidates = router.find_routes(start_edge, goal_edge, closed_edges=closed, k=3)
    assert len(candidates) >= 1, "ACO failed to find detour"
    for path, _, _ in candidates:
        assert "J0J1" not in path, f"ACO path {path} traversed closed edge J0J1!"


def test_aco_reproducibility_same_seed(grid_graph: EdgeGraph) -> None:
    """Verify ACO returns identical candidate paths and probabilities given the same seed."""
    start_edge = "left0J0"
    goal_edge = "right1J3"

    router1 = AcoRouter(graph=grid_graph, seed=12345)
    candidates1 = router1.find_routes(start_edge, goal_edge, k=3)

    router2 = AcoRouter(graph=grid_graph, seed=12345)
    candidates2 = router2.find_routes(start_edge, goal_edge, k=3)

    assert len(candidates1) == len(candidates2)
    for (p1, c1, pr1), (p2, c2, pr2) in zip(candidates1, candidates2):
        assert p1 == p2
        assert c1 == pytest.approx(c2, abs=1e-3)
        assert pr1 == pytest.approx(pr2, abs=1e-3)


def test_navigator_simulation_execution() -> None:
    """Verify running the simulation in A* navigation mode populates telemetry metrics."""
    res = run_simulation(
        scenario="navigation",
        controller="adaptive_vanet",
        seed=1,
        nav="astar",
        nav_fraction=0.5,
        max_steps=120,
        gui=False,
        live=False,
    )

    assert res["nav_mode"] == "astar"
    assert res["route_search_expansions"] > 0
    assert res["route_search_ms"] >= 0.0
    assert res["reroute_count"] >= 1
    assert res["edge_load_cv"] >= 0.0
    assert res["max_edge_load"] >= 1


# --------------------------------------------------------------------------- cost-model fixes
class _FakeVehicleApi:
    def __init__(self, road: str, route: list) -> None:
        self.road, self.route, self.set_routes = road, list(route), []

    def getIDList(self):
        return ["car1"]

    def getRoadID(self, vid):
        return self.road

    def getRoute(self, vid):
        return list(self.route)

    def getRouteIndex(self, vid):
        return self.route.index(self.road)

    def setRoute(self, vid, path):
        self.set_routes.append(list(path))
        self.route = list(path)


class _FakeSumo:
    def __init__(self, road: str, route: list) -> None:
        self.vehicle = _FakeVehicleApi(road, route)


def test_junction_delay_is_added_only_for_signalised_edges(grid_graph: EdgeGraph) -> None:
    """Edges into a traffic light carry the expected signal wait; exit edges do not."""
    assert grid_graph.edges["bottom0J0"].signalized_end
    assert not grid_graph.edges["J3top1"].signalized_end
    base_in = grid_graph.get_edge_cost("bottom0J0")
    base_out = grid_graph.get_edge_cost("J3top1")
    grid_graph.junction_delay_s = 8.0
    try:
        assert grid_graph.get_edge_cost("bottom0J0") == pytest.approx(base_in + 8.0)
        assert grid_graph.get_edge_cost("J3top1") == pytest.approx(base_out)
    finally:
        grid_graph.junction_delay_s = 0.0


def test_slow_current_edge_does_not_force_a_reroute(grid_graph: EdgeGraph) -> None:
    """A car queued on its current edge keeps an equally good route.

    Both routes J0->J1->J3 and J0->J2->J3 cost the same. The current edge is slow (red-light queue),
    but it is the same for every alternative, so it must not make the other route look cheaper.
    """
    from sim.navigation.navigator import VehicleNavigator

    route = ["bottom0J0", "J0J1", "J1J3", "J3top1"]
    sumo = _FakeSumo("bottom0J0", route)
    nav = VehicleNavigator(sumo_api=sumo, graph=grid_graph, mode="astar", nav_fraction=1.0, hysteresis=0.05)
    nav.step(100.0, speed_map={"bottom0J0": 0.5})
    assert sumo.vehicle.set_routes == []


def test_reroute_still_happens_when_the_route_ahead_is_jammed(grid_graph: EdgeGraph) -> None:
    """A slow edge further along the route still triggers a reroute to the free alternative."""
    from sim.navigation.navigator import VehicleNavigator

    route = ["bottom0J0", "J0J1", "J1J3", "J3top1"]
    sumo = _FakeSumo("bottom0J0", route)
    nav = VehicleNavigator(sumo_api=sumo, graph=grid_graph, mode="astar", nav_fraction=1.0, hysteresis=0.05)
    nav.step(100.0, speed_map={"J1J3": 1.0})
    assert sumo.vehicle.set_routes == [["bottom0J0", "J0J2", "J2J3", "J3top1"]]


def test_speed_smoothing_ignores_short_red_light_queues(grid_graph: EdgeGraph) -> None:
    """With smoothing, one slow report barely moves the edge speed; incident edges stay raw."""
    from sim.navigation.navigator import VehicleNavigator

    nav = VehicleNavigator(sumo_api=None, graph=grid_graph, mode="astar", speed_smoothing_s=60.0)
    free = grid_graph.edges["J0J1"].free_flow_speed
    nav.smoothed_speeds(0.0, {})
    out = nav.smoothed_speeds(1.0, {"J0J1": 0.5, "J0J2": 0.5}, keep_raw={"J0J2"})
    assert out["J0J1"] > 0.95 * free
    assert out["J0J2"] == 0.5
    # A jam that lasts several minutes does show up
    t = 1.0
    for _ in range(300):
        t += 1.0
        out = nav.smoothed_speeds(t, {"J0J1": 0.5})
    assert out["J0J1"] < 1.0
