"""Live view data for the dashboard's traffic map and "my car" panel.

- network message (once per run): junction positions and edge shapes from the SUMO net.
- vehicles message (every emitted second): one compact row per vehicle with position, heading,
  speed, flags, the next signal on its route (junction, approach, distance along the route),
  the fuel and CO2 it has used so far, and its current GLOSA advice (eco cars).

Rows are lists with a "fields" header to keep the stream small; the gateway sends only
(id, x, y, angle, flags) to every viewer and the full row to viewers following that car.
Live mode only: this costs several TraCI calls per vehicle per step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from sim.vanet.network import resolve_lane_direction
from sim.vanet.nodes import build_edge_info, distance_along_route, is_vehicle_equipped

VEHICLE_FIELDS = ["id", "x", "y", "angle", "speed", "flags", "tls", "approach", "dist", "co2_g", "fuel_ml",
                  "advice", "adv_speed", "green_in"]
FLAG_ECO, FLAG_EMERGENCY, FLAG_NAV, FLAG_ADVISED, FLAG_EQUIPPED = 1, 2, 4, 8, 16
PETROL_MG_PER_ML = 745.0


def build_network_message(net_path: Union[str, Path], tls_ids: List[str], run_id: str) -> Dict[str, Any]:
    """Road geometry for the map (normal edges only)."""
    import sumolib

    net = sumolib.net.readNet(str(net_path))
    tls = set(tls_ids)
    junctions = [
        {"id": n.getID(), "x": round(n.getCoord()[0], 1), "y": round(n.getCoord()[1], 1), "tls": n.getID() in tls}
        for n in net.getNodes()
    ]
    edges = []
    for e in net.getEdges():
        if e.getID().startswith(":"):
            continue
        edges.append({
            "id": e.getID(),
            "from": e.getFromNode().getID(),
            "to": e.getToNode().getID(),
            "lanes": e.getLaneNumber(),
            "shape": [[round(p[0], 1), round(p[1], 1)] for p in e.getShape()],
        })
    (xmin, ymin), (xmax, ymax) = net.getBBoxXY()
    return {"type": "network", "run_id": run_id, "junctions": junctions, "edges": edges,
            "bounds": [round(xmin, 1), round(ymin, 1), round(xmax, 1), round(ymax, 1)]}


class LiveVehicleTracker:
    """Accumulates per-vehicle fuel/CO2 each step and builds the vehicles message."""

    def __init__(self, sumo_api: Any, tls_ids: List[str], penetration: float, seed: int,
                 eco_mgr: Optional[Any] = None, navigator: Optional[Any] = None) -> None:
        self.api = sumo_api
        self.tls_ids = list(tls_ids)
        self.tls_set: Set[str] = set(tls_ids)
        self.penetration = float(penetration)
        self.seed = int(seed)
        self.eco_mgr = eco_mgr
        self.navigator = navigator
        self.edge_info = build_edge_info(sumo_api)
        self.vtype: Dict[str, str] = {}
        self.co2_mg: Dict[str, float] = {}
        self.fuel_mg: Dict[str, float] = {}
        self._dir_cache: Dict[Tuple[str, str], str] = {}

    def _type(self, vid: str) -> str:
        if vid not in self.vtype:
            try:
                self.vtype[vid] = str(self.api.vehicle.getTypeID(vid))
            except Exception:
                self.vtype[vid] = "car"
        return self.vtype[vid]

    def _direction(self, edge: str, tls_id: str) -> str:
        key = (edge, tls_id)
        if key not in self._dir_cache:
            self._dir_cache[key] = resolve_lane_direction(f"{edge}_0", tls_id, self.api)
        return self._dir_cache[key]

    def step(self, vehicle_ids: List[str]) -> None:
        """Integrate emissions over the last 1 s step; forget departed vehicles."""
        alive = set(vehicle_ids)
        for vid in list(self.co2_mg):
            if vid not in alive:
                self.co2_mg.pop(vid, None)
                self.fuel_mg.pop(vid, None)
        for vid in vehicle_ids:
            if vid.startswith("incblk_"):
                continue
            try:
                self.co2_mg[vid] = self.co2_mg.get(vid, 0.0) + float(self.api.vehicle.getCO2Emission(vid))
                self.fuel_mg[vid] = self.fuel_mg.get(vid, 0.0) + float(self.api.vehicle.getFuelConsumption(vid))
            except Exception:
                continue

    def build(self, vehicle_ids: List[str], run_id: str, t: float) -> Dict[str, Any]:
        rows: List[List[Any]] = []
        api = self.api
        for vid in vehicle_ids:
            if vid.startswith("incblk_"):
                continue
            try:
                x, y = api.vehicle.getPosition(vid)
                angle = float(api.vehicle.getAngle(vid))
                speed = float(api.vehicle.getSpeed(vid))
                route = list(api.vehicle.getRoute(vid))
                idx = int(api.vehicle.getRouteIndex(vid))
                road = str(api.vehicle.getRoadID(vid))
                lane_pos = float(api.vehicle.getLanePosition(vid))
            except Exception:
                continue
            vtype = self._type(vid)
            flags = 0
            if vtype == "emergency":
                flags |= FLAG_EMERGENCY
            if is_vehicle_equipped(vid, vtype=vtype, penetration=self.penetration, seed=self.seed):
                flags |= FLAG_EQUIPPED
            if self.navigator is not None and self.navigator.is_nav_vehicle.get(vid):
                flags |= FLAG_NAV
            advice: Dict[str, Any] = {}
            if self.eco_mgr is not None and self.eco_mgr.is_eco(vid):
                flags |= FLAG_ECO
                advice = self.eco_mgr.advice.get(vid, {}) or {}
                if vid in self.eco_mgr.controlled:
                    flags |= FLAG_ADVISED

            tls = approach = None
            dist = None
            for tid in self.tls_ids:
                along = distance_along_route(tid, route, idx, road, lane_pos, self.edge_info, self.tls_set)
                if along is not None and along[2] == 0:
                    tls, dist, approach = tid, round(along[0], 1), self._direction(along[1], tid)
                    break

            rows.append([
                vid, round(x, 1), round(y, 1), int(round(angle)), round(speed, 1), flags, tls, approach, dist,
                round(self.co2_mg.get(vid, 0.0) / 1000.0, 1),
                round(self.fuel_mg.get(vid, 0.0) / PETROL_MG_PER_ML, 1),
                advice.get("advice"), advice.get("speed"), advice.get("green_in"),
            ])
        return {"type": "vehicles", "run_id": run_id, "t": int(t), "fields": VEHICLE_FIELDS, "rows": rows}
