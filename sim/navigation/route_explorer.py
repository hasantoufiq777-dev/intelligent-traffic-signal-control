"""Route explorer: A* alternatives between two roads, with their costs, for the dashboard.

Asked from the dashboard with an origin and a destination road. Uses the same A* (``astar_search``)
and the same road costs as navigation, on the current traffic map:

- **Fastest now**: A* on travel time. g(n) = sum of road costs c(e) = length / current speed (at least
  1 m/s) + the expected wait at a traffic light the road ends at; closed roads cost infinity.
  h(n) = straight-line distance from the end of road n to the end of the destination / the highest
  speed limit in the network. h never overestimates the remaining time, so the route is optimal.
- **Shortest distance**: A* on length in metres, h = straight-line distance (also never more than
  the real remaining distance).
- **Alternatives**: each road of the fastest route is closed in turn and A* searched again; the
  cheapest distinct routes found are kept (an edge-removal alternative-route method).

Every route is reported with its distance, the time at free flow, the estimated time now, the
number of signals and its congestion, so the dashboard can show when the physically shorter route is
not the faster one. Units: seconds and metres, never mixed.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Set

from sim.navigation.astar import astar_search
from sim.navigation.graph import EdgeGraph

MAX_ROUTES = 4


def route_metrics(graph: EdgeGraph, path: List[str], speed_map: Optional[Dict[str, float]],
                  closed: Optional[Set[str]] = None, v_min: float = 1.0) -> Dict[str, Any]:
    """Distance, free-flow time, estimated time now and signal count of a whole route (start road
    included, so the numbers describe the full trip the user asked about)."""
    dist = sum(graph.edges[e].length for e in path)
    now = 0.0
    free = 0.0
    signals = 0
    delay = graph.junction_delay_s
    for e in path:
        node = graph.edges[e]
        if closed and e in closed:
            now = math.inf
        else:
            now += graph.get_edge_cost(e, speed_map=speed_map, v_min=v_min)
        free += node.length / max(node.free_flow_speed, v_min) + (delay if node.signalized_end else 0.0)
        signals += 1 if node.signalized_end else 0
    return {
        "distance_m": round(dist, 1),
        "time_free_s": round(free, 1),
        "time_now_s": round(now, 1) if math.isfinite(now) else None,
        "signals": signals,
        # how much slower than free flow (1.0 = no congestion)
        "congestion": round(now / free, 2) if free > 0 and math.isfinite(now) else None,
    }


def explore(graph: EdgeGraph, origin: str, destination: str, speed_map: Optional[Dict[str, float]] = None,
            closed_edges: Optional[Set[str]] = None, vclass: Optional[str] = None) -> Dict[str, Any]:
    """A* route alternatives from origin to destination road. Returns a dict for the dashboard."""
    if origin not in graph.edges or destination not in graph.edges:
        bad = origin if origin not in graph.edges else destination
        return {"ok": False, "error": f"Unknown road {bad}"}
    if origin == destination:
        return {"ok": False, "error": "Start and destination are the same road"}
    closed = set(closed_edges or ())

    fastest, f_cost, f_exp = astar_search(graph, origin, destination, speed_map=speed_map, closed_edges=closed,
                                          vclass=vclass)
    if not fastest:
        return {"ok": False, "error": f"No route from {origin} to {destination} for this vehicle class"}
    shortest, s_cost, s_exp = astar_search(graph, origin, destination, speed_map=speed_map, closed_edges=closed,
                                           vclass=vclass, cost="distance")
    found: List[List[str]] = [fastest]
    if shortest and shortest not in found:
        found.append(shortest)
    candidates = []
    for e in fastest[1:-1]:
        alt, alt_cost, _ = astar_search(graph, origin, destination, speed_map=speed_map,
                                        closed_edges=closed | {e}, vclass=vclass)
        if alt and alt not in found and alt not in [c[1] for c in candidates]:
            candidates.append((alt_cost, alt))
    for _cost, alt in sorted(candidates):
        if len(found) >= MAX_ROUTES:
            break
        found.append(alt)

    routes = []
    for path in found:
        m = route_metrics(graph, path, speed_map, closed)
        routes.append({"path": path, **m})
    best_i = min(range(len(routes)), key=lambda i: routes[i]["time_now_s"] if routes[i]["time_now_s"] is not None
                 else math.inf)
    short_i = min(range(len(routes)), key=lambda i: routes[i]["distance_m"])
    for i, r in enumerate(routes):
        r["fastest"] = i == best_i
        r["shortest"] = i == short_i
    # A*'s own figures for the fastest search: h at the start and the expanded nodes
    start_node, goal_node = graph.edges[origin], graph.edges[destination]
    straight = math.hypot(start_node.end_coord[0] - goal_node.end_coord[0],
                          start_node.end_coord[1] - goal_node.end_coord[1])
    v_max = max(graph.max_network_speed, 1.0)
    return {
        "ok": True,
        "origin": origin,
        "destination": destination,
        "vclass": vclass,
        "routes": routes,
        "chosen": best_i,
        "shorter_not_faster": short_i != best_i,
        "astar": {
            "h_start_s": round(straight / v_max, 1),  # straight-line distance / v_max
            "straight_m": round(straight, 1),
            "v_max": round(v_max, 2),
            "g_goal_s": round(f_cost, 1),  # A*'s cost: from the end of the start road
            "expansions": f_exp,
            "expansions_distance": s_exp,
            "signal_wait_s": graph.junction_delay_s,
        },
    }
