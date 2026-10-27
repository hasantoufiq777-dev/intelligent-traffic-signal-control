"""Ant Colony Optimization (ACO) edge-based vehicular navigation engine.

Implements multi-agent probabilistic path search on edge-connectivity graphs.
Pheromone matrices persist per destination edge, updating gradually across
navigation intervals. Returns top-K distinct alternative routes with selection
probabilities to distribute vehicular traffic and mitigate the herding phenomenon.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from sim.navigation.astar import astar_search
from sim.navigation.graph import EdgeGraph

logger = logging.getLogger(__name__)


class AcoRouter:
    """Ant Colony Optimization routing engine operating on directed EdgeGraphs."""

    def __init__(
        self,
        graph: EdgeGraph,
        config: Optional[Dict[str, Any]] = None,
        seed: int = 1,
    ) -> None:
        """Initialize ACO parameters and persistent pheromone matrix."""
        self.graph = graph
        self.config = dict(config or {})
        self.seed = int(seed)
        self.rng = random.Random(self.seed)

        # ACO Hyperparameters
        self.alpha: float = float(self.config.get("alpha", 1.0))
        self.beta: float = float(self.config.get("beta", 2.0))
        self.rho: float = float(self.config.get("rho", 0.1))
        self.Q: float = float(self.config.get("Q", 100.0))
        self.n_ants: int = int(self.config.get("n_ants", 10))
        self.n_iterations: int = int(self.config.get("n_iterations", 10))
        self.elite_k: int = int(self.config.get("elite_k", 3))

        # Pheromone bounds
        self.tau_min: float = float(self.config.get("tau_min", 0.1))
        self.tau_max: float = float(self.config.get("tau_max", 10.0))
        self.tau_initial: float = float(self.config.get("tau_initial", 1.0))

        # Persistent pheromone storage: dest_edge -> (u, v) -> tau
        self.pheromones: Dict[str, Dict[Tuple[str, str], float]] = {}

    def _get_pheromone(self, dest_edge: str, u: str, v: str) -> float:
        """Retrieve the pheromone level on transition (u, v) heading to dest_edge."""
        dest_map = self.pheromones.setdefault(dest_edge, {})
        return dest_map.get((u, v), self.tau_initial)

    def _deposit_pheromone(self, dest_edge: str, u: str, v: str, delta: float) -> None:
        """Deposit pheromone on transition (u, v) subject to tau_max."""
        dest_map = self.pheromones.setdefault(dest_edge, {})
        current = dest_map.get((u, v), self.tau_initial)
        dest_map[(u, v)] = min(self.tau_max, current + delta)

    def _evaporate(self, dest_edge: str) -> None:
        """Evaporate pheromone trails for the specified destination."""
        dest_map = self.pheromones.get(dest_edge, {})
        for edge_pair in list(dest_map.keys()):
            new_val = dest_map[edge_pair] * (1.0 - self.rho)
            dest_map[edge_pair] = max(self.tau_min, new_val)

    def find_routes(
        self,
        start_edge: str,
        goal_edge: str,
        speed_map: Optional[Dict[str, float]] = None,
        closed_edges: Optional[Set[str]] = None,
        k: int = 3,
        v_min: float = 1.0,
    ) -> List[Tuple[List[str], float, float]]:
        """Find the top K distinct paths from start_edge to goal_edge using ACO.

        Returns:
            List of (path, path_cost, selection_probability) tuples.
        """
        if start_edge not in self.graph.edges or goal_edge not in self.graph.edges:
            return []

        if closed_edges and (start_edge in closed_edges or goal_edge in closed_edges):
            return []

        if start_edge == goal_edge:
            return [([start_edge], 0.0, 1.0)]

        completed_paths: Dict[Tuple[str, ...], float] = {}

        # Run ACO iterations
        for _ in range(self.n_iterations):
            iteration_paths: List[Tuple[List[str], float]] = []

            for _ in range(self.n_ants):
                path = [start_edge]
                visited: Set[str] = {start_edge}
                total_cost = 0.0
                curr = start_edge

                for _ in range(30):  # Maximum path length safeguard
                    if curr == goal_edge:
                        break

                    successors = self.graph.get_successors(curr)
                    # Candidate edges must avoid loops and road closures
                    candidates = [
                        v
                        for v in successors
                        if v not in visited and (not closed_edges or v not in closed_edges)
                    ]
                    if not candidates:
                        break

                    weights: List[float] = []
                    costs: List[float] = []
                    for v in candidates:
                        c = self.graph.get_edge_cost(
                            v, speed_map=speed_map, closed_edges=closed_edges, v_min=v_min
                        )
                        costs.append(c)
                        if math.isinf(c):
                            weights.append(0.0)
                            continue

                        tau = self._get_pheromone(goal_edge, curr, v)
                        eta = 1.0 / max(c, 0.01)
                        w = (tau ** self.alpha) * (eta ** self.beta)
                        weights.append(w)

                    total_weight = sum(weights)
                    if total_weight <= 0.0:
                        break

                    # Probabilistic selection
                    r = self.rng.uniform(0.0, total_weight)
                    cum = 0.0
                    selected = candidates[0]
                    chosen_idx = 0
                    for idx, w in enumerate(weights):
                        cum += w
                        if cum >= r:
                            selected = candidates[idx]
                            chosen_idx = idx
                            break

                    path.append(selected)
                    visited.add(selected)
                    total_cost += costs[chosen_idx]
                    curr = selected

                if curr == goal_edge and len(path) >= 2:
                    iteration_paths.append((path, total_cost))
                    path_key = tuple(path)
                    if path_key not in completed_paths or total_cost < completed_paths[path_key]:
                        completed_paths[path_key] = total_cost

            # Evaporate pheromone
            self._evaporate(goal_edge)

            # Elitist deposit on top paths of this iteration
            if iteration_paths:
                iteration_paths.sort(key=lambda x: x[1])
                for elite_path, elite_cost in iteration_paths[: self.elite_k]:
                    delta = self.Q / max(elite_cost, 1.0)
                    for i in range(len(elite_path) - 1):
                        self._deposit_pheromone(goal_edge, elite_path[i], elite_path[i + 1], delta)

        # If no paths were discovered by ants, fallback to deterministic A*
        if not completed_paths:
            fallback_path, fallback_cost, _ = astar_search(
                graph=self.graph,
                start_edge=start_edge,
                goal_edge=goal_edge,
                speed_map=speed_map,
                closed_edges=closed_edges,
                heuristic="euclid",
                v_min=v_min,
            )
            if fallback_path:
                return [(fallback_path, fallback_cost, 1.0)]
            return []

        # Sort distinct paths by cost ascending
        sorted_candidates = sorted(completed_paths.items(), key=lambda x: x[1])
        top_k = sorted_candidates[:k]

        # Compute selection probabilities inversely proportional to travel time cost
        inv_costs = [1.0 / max(cost, 1.0) for _, cost in top_k]
        sum_inv = sum(inv_costs)
        probabilities = [c / sum_inv for c in inv_costs] if sum_inv > 0 else [1.0 / len(top_k)] * len(top_k)

        result: List[Tuple[List[str], float, float]] = []
        for (path_tuple, cost), prob in zip(top_k, probabilities):
            result.append((list(path_tuple), round(cost, 2), round(prob, 3)))

        return result
