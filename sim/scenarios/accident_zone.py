"""Accident work zone on the J2-J3 road (network grid2x2_zone, built by sim/sumo/generate_zone_net.py).

Layout (x grows east; J2 at x=250, J3 at x=500; the zone runs from ZW at x=340 to ZE at x=420):

    eastbound  J2J3 -> [ZW] -> J2J3.z -> [ZE] -> J2J3.e
    westbound  J3J2 -> [ZE] -> J3J2.z -> [ZW] -> J3J2.w      (normal carriageway)
                       [ZE] -> J3J2.cf -> [ZW]                (contraflow on the eastbound outer lane)

The accident (configured under `accident_zone:` in a scenario YAML) blocks both westbound lanes at
the east end of the zone, and its debris the inner eastbound lane. One lane (the eastbound outer
lane) is left for both directions. Two ways of handling it:

- alternating: the zone controller (sim/controllers/zone.py) runs the shared lane one way at a time,
  with signals at ZW (eastbound) and ZE (westbound);
- closure: the westbound road is closed at J3 and westbound traffic detours over other roads, which
  is what happens without alternating control. Vehicles already past J3 wait until the road is cleared.

The crash itself is created by the incident module (sim/scenarios/incident.py) and detected over the
VANET like any incident (stopped vehicles -> RSU INCIDENT_FLAG -> TMC).
"""

from __future__ import annotations

from typing import Any, Dict, List

ZONE_NETWORK = "grid2x2_zone"
ZONE_TLS = ("ZW", "ZE")

# Directions through the shared lane: E = eastbound, W = westbound (contraflow)
APPROACH = {"E": "J2J3", "W": "J3J2"}  # road that ends at the direction's entry line
SHARED = {"E": "J2J3.z", "W": "J3J2.cf"}  # edge each direction drives on in the shared lane
AFTER = {"E": "J2J3.e", "W": "J3J2.w"}
ENTRY_TLS = {"E": "ZW", "W": "ZE"}
EXIT_TLS = {"E": "ZE", "W": "ZW"}
BLOCKED_EDGE = "J3J2.z"  # westbound carriageway inside the zone (blocked by the crash)
CONTRAFLOW_EDGE = "J3J2.cf"
ZONE_EDGES = ("J2J3.z", "J3J2.z", "J3J2.cf")
DIRECTION_LABEL = {"E": "eastbound", "W": "westbound"}

# The split road: each original edge id is followed by its zone part and the part after the zone
_EXPAND = {"J2J3": ["J2J3", "J2J3.z", "J2J3.e"], "J3J2": ["J3J2", "J3J2.z", "J3J2.w"]}

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "control": "alternating",  # alternating | closure
    "start_time": 150.0,
    "duration": 300.0,
    # m into J3J2.z for the westbound crash: far enough from ZE that a car caught behind it is out
    # of the junction (a tail left in the junction would block the contraflow entry for good)
    "crash_pos": 16.0,
    "debris_pos": 50.0,  # m into J2J3.z for the debris on the inner eastbound lane
}


def expand_route(edges: List[str]) -> List[str]:
    """Replace J2J3 / J3J2 of the plain 2x2 grid by their three parts in the zone network."""
    out: List[str] = []
    for e in edges:
        out.extend(_EXPAND.get(e, [e]))
    return out


def zone_config(scenario_cfg: Dict[str, Any], params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """The scenario's accident_zone block with defaults; params["accident_zone"] overrides fields."""
    cfg = dict(DEFAULTS)
    cfg.update(scenario_cfg.get("accident_zone") or {})
    if params and isinstance(params.get("accident_zone"), dict):
        cfg.update(params["accident_zone"])
    cfg["control"] = str(cfg.get("control", "alternating")).lower()
    if cfg["control"] not in ("alternating", "closure"):
        raise ValueError("accident_zone.control must be 'alternating' or 'closure'")
    return cfg


def incident_events(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Incident config (for sim/scenarios/incident.py) that creates the crash and its debris.

    The same physical accident in both control modes, so their results compare like for like.
    Local detours are off: routing around the crash is the zone's job (contraflow or closure at J3).
    """
    start, duration = float(cfg["start_time"]), float(cfg["duration"])
    return {
        "enabled": True,
        "local_detour": False,
        "events": [
            {"type": "closure", "edge": BLOCKED_EDGE, "position": float(cfg["crash_pos"]),
             "start_time": start, "duration": duration},
            {"type": "stop", "edge": "J2J3.z", "lane": 1, "position": float(cfg["debris_pos"]),
             "start_time": start, "duration": duration, "detect": False},
        ],
    }


def zone_geometry(net_path: Any, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Static zone drawing data for the dashboard map (network message, key "zone"); SUMO coordinates."""
    import sumolib
    from sumolib.geomhelper import positionAtShapeOffset

    net = sumolib.net.readNet(str(net_path))

    def lane_shape(lane_id: str) -> List[List[float]]:
        return [[round(x, 1), round(y, 1)] for x, y in net.getLane(lane_id).getShape()]

    def at(lane_id: str, offset: float) -> List[float]:
        x, y = positionAtShapeOffset(net.getLane(lane_id).getShape(), offset)
        return [round(x, 1), round(y, 1)]

    entry = {}
    for d, edge in APPROACH.items():
        lanes = [lane_shape(f"{edge}_{i}")[-1] for i in range(net.getEdge(edge).getLaneNumber())]
        entry[d] = {"head": lanes[0], "line": [lanes[0], lanes[-1]], "label": DIRECTION_LABEL[d]}
    crash_pos = float(cfg.get("crash_pos", DEFAULTS["crash_pos"]))
    debris_pos = float(cfg.get("debris_pos", DEFAULTS["debris_pos"]))
    return {
        "entry": entry,
        "shared": lane_shape(f"{SHARED['E']}_0"),
        "blocked": [lane_shape(f"{BLOCKED_EDGE}_{i}") for i in range(net.getEdge(BLOCKED_EDGE).getLaneNumber())],
        "crash": [at(f"{BLOCKED_EDGE}_0", crash_pos + 2.0), at(f"{BLOCKED_EDGE}_1", crash_pos + 2.0)],
        "debris": at(f"{SHARED['E']}_1", debris_pos),
        # Where the westbound road starts at J3 (closed there in closure mode)
        "closure": [lane_shape(f"{APPROACH['W']}_{i}")[0] for i in range(net.getEdge(APPROACH["W"]).getLaneNumber())],
        "contraflow_edge": CONTRAFLOW_EDGE,
        "lane_w": round(net.getLane(f"{SHARED['E']}_0").getWidth(), 2),
    }
