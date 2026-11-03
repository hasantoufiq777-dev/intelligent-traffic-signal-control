"""Edge connectivity graph and dynamic cost model for SUMO road networks.

Builds a directed edge graph using sumolib where nodes represent road edges and
directed links represent legal turning movements between edges (edge.getOutgoing()).
Calculates dynamic edge travel times based on TMC speed maps, incidents, and
free-flow speeds for A* and ACO navigation algorithms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)


@dataclass
class EdgeNode:
    """Represents a road edge in the navigation search graph."""

    edge_id: str
    length: float
    free_flow_speed: float
    start_coord: Tuple[float, float]
    end_coord: Tuple[float, float]
    successors: List[str] = field(default_factory=list)
    signalized_end: bool = False  # the edge ends at a traffic-light junction


class EdgeGraph:
    """Directed graph where nodes are road edges and edges are valid turning transitions."""

    def __init__(self) -> None:
        """Initialize empty edge graph."""
        self.edges: Dict[str, EdgeNode] = {}
        self.max_network_speed: float = 13.89  # Default ~50 km/h
        # Expected wait at a traffic light, added to every edge that ends at one (navigation.junction_delay_s).
        # Without it a detour through extra signals looks as cheap as its driving time alone.
        self.junction_delay_s: float = 0.0

    @classmethod
    def from_net_file(cls, net_path: Union[str, Path]) -> EdgeGraph:
        """Parse a SUMO .net.xml file with sumolib and build the edge graph."""
        import sumolib

        graph = cls()
        net = sumolib.net.readNet(str(net_path))

        max_speed = 0.1
        for edge in net.getEdges():
            eid = edge.getID()
            # Ignore internal junction lanes/edges (starting with ':')
            if eid.startswith(":"):
                continue

            length = float(edge.getLength())
            speed = float(edge.getSpeed())
            if speed > max_speed:
                max_speed = speed

            shape = edge.getShape()
            start_coord = (shape[0][0], shape[0][1]) if shape else (0.0, 0.0)
            end_coord = (shape[-1][0], shape[-1][1]) if shape else (0.0, 0.0)

            # Get legal outgoing edges from sumolib
            outgoing_edges: List[str] = []
            try:
                for out_edge in edge.getOutgoing():
                    out_id = out_edge.getID()
                    if not out_id.startswith(":") and out_id not in outgoing_edges:
                        outgoing_edges.append(out_id)
            except Exception:
                pass

            graph.edges[eid] = EdgeNode(
                edge_id=eid,
                length=length,
                free_flow_speed=speed,
                start_coord=start_coord,
                end_coord=end_coord,
                successors=outgoing_edges,
                signalized_end=(edge.getToNode().getType() == "traffic_light"),
            )

        graph.max_network_speed = max_speed
        logger.info(
            "Built EdgeGraph from %s: %d edges, max_speed=%.2f m/s",
            net_path,
            len(graph.edges),
            graph.max_network_speed,
        )
        return graph

    def get_successors(self, edge_id: str) -> List[str]:
        """Return list of legally reachable successor edge IDs from edge_id."""
        node = self.edges.get(edge_id)
        if not node:
            return []
        return node.successors

    def get_edge_cost(
        self,
        edge_id: str,
        speed_map: Optional[Dict[str, float]] = None,
        closed_edges: Optional[Set[str]] = None,
        v_min: float = 1.0,
    ) -> float:
        """Compute the dynamic travel time cost of traversing edge_id in seconds.

        Cost = length / max(speed, v_min), plus junction_delay_s if the edge ends at a traffic light.
        Closed edges (due to incidents or road closures) incur infinite cost.
        """
        if closed_edges and edge_id in closed_edges:
            return float("inf")

        node = self.edges.get(edge_id)
        if not node:
            return float("inf")

        # Fallback to free-flow speed if no fresh VANET report exists
        speed = node.free_flow_speed
        if speed_map and edge_id in speed_map:
            obs_speed = speed_map[edge_id]
            if obs_speed > 0.0:
                speed = obs_speed

        effective_speed = max(speed, float(v_min))
        cost = node.length / effective_speed
        if node.signalized_end:
            cost += self.junction_delay_s
        return cost
