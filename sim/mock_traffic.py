"""Moving cars for the mock simulation (no SUMO): network geometry, vehicles rows and SPaT.

Cars drive fixed routes over the same node layout as the real nets, stop at the stop line when
their approach is not green, and eco cars get GLOSA-style advice. Emissions are rough
(CO2 ~ 2.6 g/s moving, 0.9 g/s idling; fuel ml = CO2 g / 2.31). Standard library only.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Tuple

VEHICLE_FIELDS = ["id", "x", "y", "angle", "speed", "flags", "tls", "approach", "dist", "co2_g", "fuel_ml",
                  "advice", "adv_speed", "green_in"]
FLAG_ECO, FLAG_EMERGENCY, FLAG_NAV, FLAG_ADVISED, FLAG_EQUIPPED = 1, 2, 4, 8, 16
LANE_OFFSET = 3.2
STOP_LINE = 11.0  # metres before the junction centre
V_FREE = 12.5

GRID_NODES = {"J0": (250, 250), "J1": (500, 250), "J2": (250, 500), "J3": (500, 500),
              "bottom0": (250, 0), "bottom1": (500, 0), "top0": (250, 750), "top1": (500, 750),
              "left0": (0, 250), "left1": (0, 500), "right0": (750, 250), "right1": (750, 500)}
GRID_ROUTES = [["bottom0", "J0", "J1", "J3", "top1"], ["left0", "J0", "J2", "J3", "right1"],
               ["top0", "J2", "J0", "bottom0"], ["right0", "J1", "J0", "left0"],
               ["bottom1", "J1", "J3", "top1"], ["left1", "J2", "J3", "right1"],
               ["top1", "J3", "J2", "left1"], ["right1", "J3", "J1", "bottom1"]]
SINGLE_NODES = {"J0": (300, 300), "N": (300, 600), "S": (300, 0), "E": (600, 300), "W": (0, 300)}
SINGLE_ROUTES = [["S", "J0", "N"], ["N", "J0", "S"], ["E", "J0", "W"], ["W", "J0", "E"],
                 ["S", "J0", "E"], ["W", "J0", "N"]]


def edge_id(a: str, b: str, single: bool) -> str:
    if single:
        return f"{a}2J0" if b == "J0" else f"J02{b}"
    return f"{a}{b}"


def mock_spat(t: int, preempted: bool = False) -> Dict[str, Dict[str, Any]]:
    """SPaT of the mock's 60 s cycle: NS green 0-25, yellow 25-28, all-red 28-30, EW green 30-55, ..."""
    out: Dict[str, Dict[str, Any]] = {}
    for dirs, shift in (("NS", 0), ("EW", 30)):
        c = (t - shift) % 60
        if c < 25:
            sig, change, nxt = "G", 25 - c, 60 - c
        elif c < 28:
            sig, change, nxt = "y", 28 - c, 60 - c
        else:
            sig, change, nxt = "r", 60 - c, 60 - c
        for d in dirs:
            out[d] = {"signal": sig, "min_s": float(change), "max_s": float(change),
                      "next_green_min_s": float(nxt), "next_green_max_s": float(nxt)}
            if preempted:
                out[d].update(min_s=None, max_s=None, next_green_min_s=None, next_green_max_s=None, preempted=True)
    return out


class MockTraffic:
    def __init__(self, network: str, rng: random.Random, penetration: float, eco_fraction: float,
                 nav: str, with_ev: bool) -> None:
        self.single = network != "grid2x2"
        self.nodes = SINGLE_NODES if self.single else GRID_NODES
        self.routes = SINGLE_ROUTES if self.single else GRID_ROUTES
        self.rng = rng
        self.penetration = penetration
        self.eco_fraction = eco_fraction
        self.nav = nav
        self.with_ev = with_ev
        self.cars: List[Dict[str, Any]] = []
        self.n = 0

    def network_message(self, run_id: str) -> Dict[str, Any]:
        edges: Dict[str, Dict[str, Any]] = {}
        for r in self.routes:
            for a, b in zip(r, r[1:]):
                for u, v in ((a, b), (b, a)):
                    eid = edge_id(u, v, self.single)
                    if eid not in edges:
                        edges[eid] = {"id": eid, "from": u, "to": v, "lanes": 2,
                                      "shape": [list(self._offset(u, v, 0.0)), list(self._offset(u, v, 1.0))]}
        xs = [p[0] for p in self.nodes.values()]
        ys = [p[1] for p in self.nodes.values()]
        return {"type": "network", "run_id": run_id,
                "junctions": [{"id": k, "x": float(x), "y": float(y), "tls": k.startswith("J")}
                              for k, (x, y) in self.nodes.items()],
                "edges": list(edges.values()), "bounds": [min(xs), min(ys), max(xs), max(ys)]}

    def _offset(self, a: str, b: str, f: float) -> Tuple[float, float]:
        (x1, y1), (x2, y2) = self.nodes[a], self.nodes[b]
        length = math.hypot(x2 - x1, y2 - y1) or 1.0
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        # right-hand traffic: drive LANE_OFFSET to the right of the centreline
        return (round(x1 + ux * length * f + uy * LANE_OFFSET, 1), round(y1 + uy * length * f - ux * LANE_OFFSET, 1))

    def _spawn(self, t: int) -> None:
        if self.with_ev and t == 10:
            route, vid, flags = self.routes[0], "amb1", FLAG_EMERGENCY | FLAG_EQUIPPED
        elif self.rng.random() < 0.45:
            route = self.routes[self.rng.randrange(len(self.routes))]
            vid = f"m{self.n}"
            self.n += 1
            flags = 0
            if self.rng.random() < self.penetration:
                flags |= FLAG_EQUIPPED
                if self.rng.random() < self.eco_fraction:
                    flags |= FLAG_ECO
                if self.nav != "none" and self.rng.random() < 0.3:
                    flags |= FLAG_NAV
        else:
            return
        self.cars.append({"id": vid, "route": route, "seg": 0, "s": 0.0, "speed": V_FREE, "flags": flags,
                          "co2": 0.0, "advice": None, "adv_speed": None, "green_in": None})

    def step(self, t: int, spat: Dict[str, Dict[str, Any]]) -> None:
        self._spawn(t)
        alive = []
        # Leaders first on each edge, so followers can keep a 7 m gap (simple car-following)
        self.cars.sort(key=lambda c: -c["s"])
        leader_s: Dict[Tuple[str, str], float] = {}
        for c in self.cars:
            a, b = c["route"][c["seg"]], c["route"][c["seg"] + 1]
            length = math.hypot(self.nodes[b][0] - self.nodes[a][0], self.nodes[b][1] - self.nodes[a][1])
            approach = self._approach(a, b)
            dist = length - STOP_LINE - c["s"] if b.startswith("J") else None
            sig = spat.get(approach, {}) if dist is not None else {}
            ev = bool(c["flags"] & FLAG_EMERGENCY)
            target = V_FREE * (1.25 if ev else 1.0)
            c["advice"] = c["adv_speed"] = c["green_in"] = None
            c["flags"] &= ~FLAG_ADVISED
            if dist is not None and sig and c["flags"] & FLAG_ECO and dist <= 300:
                green_in = sig.get("next_green_min_s")
                c["green_in"] = green_in
                if sig.get("signal") == "G" and (sig.get("min_s") or 0) > dist / V_FREE + 1:
                    c["advice"] = "go"
                elif green_in is not None and dist / V_FREE < green_in + 1:
                    v = dist / (green_in + 1)
                    if v >= 5.0:
                        c["advice"], c["adv_speed"], target = "slow", round(v, 1), v
                        c["flags"] |= FLAG_ADVISED
                    else:
                        c["advice"] = "stop"
                else:
                    c["advice"] = "go"
            move = min(target, c["speed"] + 2.5)
            if dist is not None and sig.get("signal") != "G" and not ev and 0 <= dist < move + 1:
                move = max(0.0, dist)  # stop at the line
            if (a, b) in leader_s:
                move = max(0.0, min(move, leader_s[(a, b)] - 7.0 - c["s"]))
            c["speed"] = move
            c["s"] += move
            leader_s[(a, b)] = c["s"]
            c["co2"] += 2.6 if move > 0.5 else 0.9
            if c["s"] >= length:
                c["s"] -= length
                c["seg"] += 1
                if c["seg"] >= len(c["route"]) - 1:
                    continue  # arrived
            alive.append(c)
        self.cars = alive

    def _approach(self, a: str, b: str) -> str:
        (x1, y1), (x2, y2) = self.nodes[a], self.nodes[b]
        if abs(y2 - y1) >= abs(x2 - x1):
            return "S" if y2 > y1 else "N"
        return "W" if x2 > x1 else "E"

    def vehicles_message(self, run_id: str, t: int) -> Dict[str, Any]:
        rows = []
        for c in self.cars:
            a, b = c["route"][c["seg"]], c["route"][c["seg"] + 1]
            length = math.hypot(self.nodes[b][0] - self.nodes[a][0], self.nodes[b][1] - self.nodes[a][1]) or 1.0
            x, y = self._offset(a, b, min(1.0, c["s"] / length))
            (x1, y1), (x2, y2) = self.nodes[a], self.nodes[b]
            angle = int(round(math.degrees(math.atan2(x2 - x1, y2 - y1)) % 360))  # SUMO: 0 = north, clockwise
            tls = b if b.startswith("J") else None
            dist = round(max(0.0, length - STOP_LINE - c["s"]), 1) if tls else None
            rows.append([c["id"], x, y, angle, round(c["speed"], 1), c["flags"], tls,
                         self._approach(a, b) if tls else None, dist, round(c["co2"], 1),
                         round(c["co2"] / 2.31, 1), c["advice"], c["adv_speed"], c["green_in"]])
        return {"type": "vehicles", "run_id": run_id, "t": t, "fields": VEHICLE_FIELDS, "rows": rows}
