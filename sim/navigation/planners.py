"""Route planners (Strategy pattern) and their factory.

Navigation, the ambulance router and the route explorer all need "a route from road A to road B for
this vehicle class on the current traffic map", but by different methods. Each method is a planner
with the same ``plan()`` interface, so the caller does not branch on the method:

- ``AStarPlanner`` (cost "time"): fastest route, A* with h = straight-line distance / v_max.
- ``AStarPlanner`` (cost "distance"): shortest route in metres, h = straight-line distance.
- ``AcoPlanner``: ant colony optimisation; up to k routes with selection probabilities, one of them
  chosen at random by those probabilities (cars spread over alternatives).

``make_planner(mode, ...)`` is the factory: navigation modes "astar", "aco", "aco_tuned" and "distance"
map to a planner; "none" gives no planner. Adding a method means adding one class and one registry
entry, without touching the navigator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Callable, Dict, List, Optional, Protocol, Set, Tuple

from sim.navigation.aco import AcoRouter
from sim.eco_speed import eco_edge_cost, min_fuel_per_m
from sim.navigation.astar import astar_search
from sim.navigation.graph import EdgeGraph

Route = Tuple[List[str], float, float]  # (edges, cost, selection probability)


@dataclass
class PlanResult:
    """A planner's answer: the route to take and every alternative it considered."""

    path: List[str] = field(default_factory=list)  # empty = no route found
    cost: float = math.inf
    expansions: int = 0  # search effort (A*: roads expanded; ACO: ants x iterations)
    alternatives: List[Route] = field(default_factory=list)
    chosen: int = 0  # index of ``path`` in ``alternatives``


class RoutePlanner(Protocol):
    name: str

    def plan(self, start: str, goal: str, speed_map: Optional[Dict[str, float]],
             closed_edges: Optional[Set[str]], vclass: Optional[str]) -> PlanResult: ...


class AStarPlanner:
    """A* on travel time (default) or distance."""

    def __init__(self, graph: EdgeGraph, heuristic: str = "euclid", cost: str = "time") -> None:
        self.graph = graph
        self.heuristic = heuristic
        self.cost = cost
        self.name = "astar" if cost == "time" else "distance"

    def plan(self, start: str, goal: str, speed_map: Optional[Dict[str, float]] = None,
             closed_edges: Optional[Set[str]] = None, vclass: Optional[str] = None) -> PlanResult:
        path, cost, expansions = astar_search(graph=self.graph, start_edge=start, goal_edge=goal,
                                              speed_map=speed_map, closed_edges=closed_edges,
                                              heuristic=self.heuristic, vclass=vclass, cost=self.cost)
        return PlanResult(path=path, cost=cost, expansions=expansions,
                          alternatives=[(path, cost, 1.0)] if path else [], chosen=0)


class EcoPlanner:
    """Eco-routing: A* on expected fuel (HBEFA4) for this vehicle's category instead of travel time."""

    name = "eco"

    def __init__(self, graph: EdgeGraph, stop_probability: float = 0.7, v_min: float = 1.0) -> None:
        self.graph = graph
        self.stop_probability = float(stop_probability)
        self.v_min = float(v_min)

    def edge_cost(self, edge: str, kind: str, speed_map: Optional[Dict[str, float]] = None,
                  closed_edges: Optional[Set[str]] = None) -> float:
        """Expected fuel (mg) on one road at the speed reported by the traffic centre."""
        self.graph.cost_evals += 1
        node = self.graph.edges.get(edge)
        if node is None or (closed_edges and edge in closed_edges):
            return math.inf
        observed = (speed_map or {}).get(edge, 0.0)
        speed = max(observed if observed > 0.0 else node.free_flow_speed, self.v_min)
        delay = self.graph.junction_delay_s if node.signalized_end else 0.0
        return eco_edge_cost(kind, node.length, speed, delay, self.stop_probability)

    def route_cost(self, edges: List[str], kind: str, speed_map: Optional[Dict[str, float]] = None,
                   closed_edges: Optional[Set[str]] = None) -> float:
        return sum(self.edge_cost(e, kind, speed_map, closed_edges) for e in edges)

    def plan(self, start: str, goal: str, speed_map: Optional[Dict[str, float]] = None,
             closed_edges: Optional[Set[str]] = None, vclass: Optional[str] = None, kind: str = "car") -> PlanResult:
        path, cost, expansions = astar_search(
            graph=self.graph, start_edge=start, goal_edge=goal, closed_edges=closed_edges, vclass=vclass,
            cost_fn=lambda e: self.edge_cost(e, kind, speed_map, closed_edges), h_per_m=min_fuel_per_m(kind))
        return PlanResult(path=path, cost=cost, expansions=expansions)


class AcoPlanner:
    """Ant colony optimisation: the k best routes found, one picked by its probability."""

    name = "aco"

    def __init__(self, router: AcoRouter, rng: random.Random) -> None:
        self.router = router
        self.rng = rng  # the caller's generator, so runs stay reproducible

    def plan(self, start: str, goal: str, speed_map: Optional[Dict[str, float]] = None,
             closed_edges: Optional[Set[str]] = None, vclass: Optional[str] = None) -> PlanResult:
        candidates = self.router.find_routes(start_edge=start, goal_edge=goal, speed_map=speed_map,
                                             closed_edges=closed_edges, k=self.router.elite_k, vclass=vclass)
        if not candidates:
            return PlanResult(alternatives=[])
        r = self.rng.random()  # probabilistic selection among the top k
        cum = 0.0
        chosen = 0
        selected = candidates[0]
        for i, cand in enumerate(candidates):
            cum += cand[2]
            if cum >= r:
                selected, chosen = cand, i
                break
        return PlanResult(path=selected[0], cost=selected[1],
                          expansions=self.router.n_ants * self.router.n_iterations,
                          alternatives=list(candidates), chosen=chosen)


# Factory registry: navigation mode -> builder
_REGISTRY: Dict[str, Callable[..., RoutePlanner]] = {
    "astar": lambda graph, aco_router, rng, heuristic: AStarPlanner(graph, heuristic=heuristic),
    "distance": lambda graph, aco_router, rng, heuristic: AStarPlanner(graph, heuristic=heuristic, cost="distance"),
    "aco": lambda graph, aco_router, rng, heuristic: AcoPlanner(aco_router, rng),
    "aco_tuned": lambda graph, aco_router, rng, heuristic: AcoPlanner(aco_router, rng),
}


def make_planner(mode: str, graph: EdgeGraph, aco_router: Optional[AcoRouter] = None,
                 rng: Optional[random.Random] = None, heuristic: str = "euclid") -> Optional[RoutePlanner]:
    """The planner for a navigation mode; None for "none" (no navigation)."""
    mode = (mode or "none").lower()
    if mode == "none":
        return None
    if mode not in _REGISTRY:
        raise ValueError(f"Unknown navigation mode {mode!r} (known: none, {', '.join(_REGISTRY)})")
    if mode.startswith("aco") and aco_router is None:
        aco_router = AcoRouter(graph=graph)
    return _REGISTRY[mode](graph, aco_router, rng or random.Random(0), heuristic)
