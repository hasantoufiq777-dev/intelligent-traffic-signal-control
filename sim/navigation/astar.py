"""A* and Dijkstra heuristic shortest-path search on edge-connectivity graphs.

Implements hand-crafted A* heuristic search using an admissible Euclidean distance
over maximum network speed heuristic. Guarantees exact equivalence to Dijkstra
when configured with heuristic='zero'.
"""

from __future__ import annotations

import heapq
import math
from typing import Dict, List, Optional, Set, Tuple

from sim.navigation.graph import EdgeGraph


def astar_search(
    graph: EdgeGraph,
    start_edge: str,
    goal_edge: str,
    speed_map: Optional[Dict[str, float]] = None,
    closed_edges: Optional[Set[str]] = None,
    heuristic: str = "euclid",
    v_min: float = 1.0,
) -> Tuple[List[str], float, int]:
    """Execute A* heuristic search from start_edge to goal_edge.

    Args:
        graph: Edge connectivity graph.
        start_edge: ID of the originating road edge.
        goal_edge: ID of the destination road edge.
        speed_map: Dynamic edge speed observations from VANET/TMC.
        closed_edges: Set of road edges currently blocked or closed.
        heuristic: 'euclid' for admissible Euclidean heuristic, 'zero' for exact Dijkstra.
        v_min: Minimum velocity floor for edge traversal time computation.

    Returns:
        Tuple of (path, total_cost, expansions):
        - path: Ordered list of edge IDs from start to goal.
        - total_cost: Travel time in seconds along the path.
        - expansions: Number of node expansions popped from the priority queue.
    """
    if start_edge not in graph.edges or goal_edge not in graph.edges:
        return ([], float("inf"), 0)

    if closed_edges and (start_edge in closed_edges or goal_edge in closed_edges):
        return ([], float("inf"), 0)

    if start_edge == goal_edge:
        return ([start_edge], 0.0, 0)

    goal_node = graph.edges[goal_edge]
    goal_coord = goal_node.end_coord
    max_speed = max(graph.max_network_speed, 1.0)
    use_euclid = (heuristic.lower() == "euclid")

    def calc_h(edge_id: str) -> float:
        if not use_euclid or edge_id == goal_edge:
            return 0.0
        node = graph.edges[edge_id]
        dist = math.hypot(node.end_coord[0] - goal_coord[0], node.end_coord[1] - goal_coord[1])
        return dist / max_speed

    # Priority queue: (f_score, h_score, tie_breaker_counter, current_edge)
    counter = 0
    start_h = calc_h(start_edge)
    open_set: List[Tuple[float, float, int, str]] = [(start_h, start_h, counter, start_edge)]

    g_score: Dict[str, float] = {start_edge: 0.0}
    came_from: Dict[str, str] = {}
    expansions = 0

    while open_set:
        f, h, _, current = heapq.heappop(open_set)
        expansions += 1

        if current == goal_edge:
            # Reconstruct path
            path = [current]
            curr = current
            while curr in came_from:
                curr = came_from[curr]
                path.append(curr)
            path.reverse()
            return (path, round(g_score[goal_edge], 3), expansions)

        current_g = g_score[current]

        # Expand legal outgoing edges
        for neighbor in graph.get_successors(current):
            edge_cost = graph.get_edge_cost(
                neighbor,
                speed_map=speed_map,
                closed_edges=closed_edges,
                v_min=v_min,
            )
            if math.isinf(edge_cost):
                continue

            tentative_g = current_g + edge_cost
            if tentative_g < g_score.get(neighbor, float("inf")):
                g_score[neighbor] = tentative_g
                h_cost = calc_h(neighbor)
                f_cost = tentative_g + h_cost
                came_from[neighbor] = current
                counter += 1
                heapq.heappush(open_set, (f_cost, h_cost, counter, neighbor))

    # Goal unreachable
    return ([], float("inf"), expansions)
