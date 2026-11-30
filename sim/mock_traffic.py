"""Moving cars for the mock simulation (no SUMO): network geometry, vehicles rows and SPaT.

Cars drive fixed routes over the same node layout as the real nets, stop at the stop line when
their approach is not green, and eco cars get GLOSA-style advice. Emissions are rough
(CO2 ~ 2.6 g/s moving, 0.9 g/s idling; fuel ml = CO2 g / 2.31). Standard library only.

One-driver mode (MockTraffic.handle_driver): fake movement of the driven car with the same
"driver" messages as sim/driver.py, so the gateway and dashboard can be tested without SUMO.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Tuple

try:
    from sim.car_following import follow_advice
except ImportError:  # run as a script from sim/ (mock_sim.py)
    from car_following import follow_advice  # type: ignore

VEHICLE_FIELDS = ["id", "x", "y", "angle", "speed", "flags", "tls", "approach", "dist", "co2_g", "fuel_ml",
                  "advice", "adv_speed", "green_in",
                  # car following (sim/car_following.py)
                  "acc", "leader", "gap", "follow", "follow_speed", "ttc"]
FLAG_ECO, FLAG_EMERGENCY, FLAG_NAV, FLAG_ADVISED, FLAG_EQUIPPED, FLAG_DRIVER = 1, 2, 4, 8, 16, 32
LANE_OFFSET = 3.2
STOP_LINE = 11.0  # metres before the junction centre
V_FREE = 12.5
CAR_LEN = 5.0  # gap = distance between the cars' fronts minus this

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
        self.driver: Optional[Dict[str, Any]] = None  # the driven car (one-driver mode)
        self.n_drv = 0
        self._t = 0
        self.notices: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []

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
        self._t = t
        self._spawn(t)
        alive = []
        # Leaders first on each edge, so followers can keep a 7 m gap (simple car-following)
        self.cars.sort(key=lambda c: -c["s"])
        leader_s: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
        for c in self.cars:
            a, b = c["route"][c["seg"]], c["route"][c["seg"] + 1]
            length = math.hypot(self.nodes[b][0] - self.nodes[a][0], self.nodes[b][1] - self.nodes[a][1])
            approach = self._approach(a, b)
            dist = length - STOP_LINE - c["s"] if b.startswith("J") else None
            sig = spat.get(approach, {}) if dist is not None else {}
            ev = bool(c["flags"] & FLAG_EMERGENCY)
            target = V_FREE * (1.25 if ev else 1.0)
            if c.get("drv"):
                if c["mode"] == "stop":
                    target = 0.0
                elif c["mode"] == "ease" and c["target"] is not None:
                    target = min(V_FREE, c["target"])
                elif c["mode"] == "follow" and (a, b) in leader_s:
                    ls, lc = leader_s[(a, b)]
                    target = follow_advice(c["speed"], V_FREE, lc["speed"], lc.get("acc"), ls - c["s"] - CAR_LEN,
                                           cooperative=bool(lc["flags"] & FLAG_EQUIPPED))["speed"]
            c["advice"] = c["adv_speed"] = c["green_in"] = None
            c["flags"] &= ~FLAG_ADVISED
            if dist is not None and sig and (c["flags"] & FLAG_ECO or c.get("drv")) and dist <= 300:
                green_in = sig.get("next_green_min_s")
                c["green_in"] = green_in
                if sig.get("signal") == "G" and (sig.get("min_s") or 0) > dist / V_FREE + 1:
                    c["advice"] = "go"
                elif green_in is not None and dist / V_FREE < green_in + 1:
                    v = dist / (green_in + 1)
                    if v >= 5.0:
                        c["advice"], c["adv_speed"] = "slow", round(v, 1)
                        if not c.get("drv"):  # the driver decides for themselves
                            target = v
                            c["flags"] |= FLAG_ADVISED
                    else:
                        c["advice"] = "stop"
                else:
                    c["advice"] = "go"
            move = min(target, c["speed"] + 2.5)
            if dist is not None and sig.get("signal") != "G" and not ev and 0 <= dist < move + 1:
                move = max(0.0, dist)  # stop at the line
            c["leader"] = c["gap"] = None
            if (a, b) in leader_s:
                ls, lc = leader_s[(a, b)]
                move = max(0.0, min(move, ls - 7.0 - c["s"]))
                c["leader"], c["gap"] = lc["id"], round(max(0.0, ls - c["s"] - CAR_LEN), 1)
            c["acc"] = round(move - c["speed"], 2)  # 1 s steps
            c["speed"] = move
            c["s"] += move
            c["odo"] = c.get("odo", 0.0) + move
            leader_s[(a, b)] = (c["s"], c)
            c["co2"] += 2.6 if move > 0.5 else 0.9
            if c.get("drv"):
                self._driver_tick(c, move)
            if c["s"] >= length:
                c["s"] -= length
                c["seg"] += 1
                if c["seg"] >= len(c["route"]) - 1:
                    if c.get("drv"):
                        self._driver_done(t, "finished")
                    continue  # arrived
            alive.append(c)
        self.cars = alive

    def _approach(self, a: str, b: str) -> str:
        (x1, y1), (x2, y2) = self.nodes[a], self.nodes[b]
        if abs(y2 - y1) >= abs(x2 - x1):
            return "S" if y2 > y1 else "N"
        return "W" if x2 > x1 else "E"

    def _follow_fields(self, c: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> List[Any]:
        lead = by_id.get(c.get("leader") or "")
        if lead is None or c.get("gap") is None:
            return [None, None, None]
        adv = follow_advice(c["speed"], V_FREE, lead["speed"], lead.get("acc"), c["gap"],
                            cooperative=bool(c["flags"] & lead["flags"] & FLAG_EQUIPPED))
        return [adv["state"], adv["speed"], adv["ttc"]]

    def vehicles_message(self, run_id: str, t: int) -> Dict[str, Any]:
        rows = []
        by_id = {c["id"]: c for c in self.cars}
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
                         round(c["co2"] / 2.31, 1), c["advice"], c["adv_speed"], c["green_in"],
                         c.get("acc", 0.0), c.get("leader"), c.get("gap")] + self._follow_fields(c, by_id))
        return {"type": "vehicles", "run_id": run_id, "t": t, "fields": VEHICLE_FIELDS, "rows": rows}

    # ------------------------------------------------------------------ one-driver mode
    def _edges(self) -> Dict[str, Tuple[str, str]]:
        out: Dict[str, Tuple[str, str]] = {}
        for r in self.routes:
            for a, b in zip(r, r[1:]):
                for u, v in ((a, b), (b, a)):
                    out[edge_id(u, v, self.single)] = (u, v)
        return out

    def _node_path(self, start: str, goal: str, avoid: str) -> Optional[List[str]]:
        """Breadth-first node path start -> goal over the mock's roads (never back to `avoid`)."""
        nxt: Dict[str, List[str]] = {}
        for u, v in self._edges().values():
            nxt.setdefault(u, []).append(v)
        prev: Dict[str, Optional[str]] = {start: None}
        todo = [start]
        while todo:
            n = todo.pop(0)
            if n == goal:
                path = [n]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                return path[::-1]
            for m in sorted(nxt.get(n, [])):
                if m not in prev and m != avoid:
                    prev[m] = n
                    todo.append(m)
        return None

    def handle_driver(self, cmd: Dict[str, Any], t: int) -> None:
        kind = cmd.get("cmd")
        drv = self.driver
        if kind == "drive_start":
            if drv is not None:
                self._notice(t, "error", "A car is already being driven")
                return
            edges = self._edges()
            src, dst = edges.get(str(cmd.get("from"))), edges.get(str(cmd.get("to")))
            if src is None or dst is None:
                self._notice(t, "error", "Unknown road", vid="")
                return
            mid = [src[1]] if src[1] == dst[0] else self._node_path(src[1], dst[0], src[0])
            if not mid or not src[1].startswith("J"):
                self._notice(t, "error", f"No route from {cmd.get('from')} to {cmd.get('to')}", vid="")
                return
            route = [src[0]] + mid + [dst[1]]
            self.n_drv += 1
            vid = f"drv{self.n_drv}"
            car = {"id": vid, "route": route, "seg": 0, "s": 0.0, "speed": V_FREE,
                   "flags": FLAG_DRIVER | FLAG_EQUIPPED, "co2": 0.0, "advice": None, "adv_speed": None,
                   "green_in": None, "drv": True, "mode": "go", "target": None}
            self.cars.append(car)
            self.driver = {"car": car, "from": cmd.get("from"), "to": cmd.get("to"), "t0": t, "stops": 0,
                           "moving": True, "adv_s": 0, "adv_ok": 0}
            self.events.append({"kind": "driver_start", "t": t,
                                "detail": f"A viewer is driving {vid} from {cmd.get('from')} to {cmd.get('to')}"})
            self._notice(t, "waiting", "Your car is entering the road")
        elif kind == "drive" and drv is not None and cmd.get("mode") in ("go", "ease", "stop", "follow"):
            drv["car"]["mode"] = cmd["mode"]
            try:
                drv["car"]["target"] = max(2.0, float(cmd.get("speed"))) if cmd["mode"] == "ease" else None
            except (TypeError, ValueError):
                drv["car"]["target"] = 8.3
        elif kind == "drive_end" and drv is not None:
            self.cars = [c for c in self.cars if c is not drv["car"]]
            self._driver_done(t, "released")

    def _driver_tick(self, c: Dict[str, Any], move: float) -> None:
        d = self.driver
        if d is None:
            return
        if move >= 1.0:
            d["moving"] = True
        elif move < 0.1 and d["moving"]:
            d["moving"] = False
            d["stops"] += 1
        if c["advice"] == "slow" and c["adv_speed"] is not None:
            d["adv_s"] += 1
            d["adv_ok"] += int(move <= c["adv_speed"] + 1.4)

    def _notice(self, t: int, status: str, message: str, report: Optional[Dict[str, Any]] = None,
                vid: Optional[str] = None) -> None:
        drv_vid = self.driver["car"]["id"] if self.driver else ""
        msg = {"type": "driver", "t": t, "vid": drv_vid if vid is None else vid, "status": status, "message": message}
        if report is not None:
            msg["report"] = report
        self.notices.append(msg)

    def _driver_done(self, t: int, status: str) -> None:
        d = self.driver
        if d is None:
            return
        c = d["car"]
        travel = max(1, t - d["t0"])
        odo = c.get("odo", 0.0)
        km = odo / 1000.0
        report = {"finished": status == "finished", "from": d["from"], "to": d["to"], "travel_s": float(travel),
                  "distance_m": round(odo), "avg_speed_kmh": round(odo / travel * 3.6, 1),
                  "stops": d["stops"], "fuel_ml": round(c["co2"] / 2.31, 1), "co2_g": round(c["co2"], 1),
                  "co2_g_per_km": round(c["co2"] / km, 1) if km > 0.05 else None,
                  "fuel_l_per_100km": round(c["co2"] / 2.31 / 10.0 / km, 1) if km > 0.05 else None,
                  "stops_per_km": round(d["stops"] / km, 2) if km > 0.05 else None,
                  "advice_seconds": d["adv_s"],
                  "advice_followed_pct": round(100 * d["adv_ok"] / d["adv_s"]) if d["adv_s"] else None,
                  "amb_warnings": 0,
                  # Fixed figures in the mock (no trips are tracked)
                  "compare": {"group": "all", "n": 12, "travel_s": 60.0, "avg_speed_kmh": 30.0,
                              "co2_g_per_km": 250.0, "fuel_l_per_100km": 10.8, "stops_per_km": 1.5}}
        self.events.append({"kind": "driver_end", "t": t, "detail": f"{c['id']}: drive ended ({status})"})
        self._notice(t, status, "You reached your destination" if status == "finished" else "You handed the car back",
                     report=report)
        self.driver = None

    def driver_message(self, run_id: str, t: int) -> Optional[Dict[str, Any]]:
        d = self.driver
        if d is None:
            return None
        c = d["car"]
        a, b = c["route"][c["seg"]], c["route"][c["seg"] + 1]
        seg_len = lambda u, v: math.hypot(self.nodes[v][0] - self.nodes[u][0], self.nodes[v][1] - self.nodes[u][1])  # noqa: E731
        left = seg_len(a, b) - c["s"] + sum(seg_len(u, v) for u, v in zip(c["route"][c["seg"] + 1:], c["route"][c["seg"] + 2:]))
        tls = b if b.startswith("J") else None
        amb = None
        for o in self.cars:
            if tls and o["flags"] & FLAG_EMERGENCY and o["route"][o["seg"] + 1] == b:
                amb = {"id": o["id"], "dist_m": round(seg_len(o["route"][o["seg"]], b) - o["s"]), "where": "crossing",
                       "junction": b}
                amb["text"] = (f"Ambulance {amb['dist_m']} m from {b}, crossing your route. "
                               "Its light turns green for it: wait for it to pass.")
        by_id = {o["id"]: o for o in self.cars}

        def side(o: Optional[Dict[str, Any]], gap: Optional[float]) -> Optional[Dict[str, Any]]:
            if o is None or gap is None:
                return None
            return {"id": o["id"], "gap_m": gap, "speed_kmh": round(o["speed"] * 3.6, 1), "acc": o.get("acc", 0.0),
                    "kind": "ambulance" if o["flags"] & FLAG_EMERGENCY else "car", "v2v": bool(o["flags"] & FLAG_EQUIPPED)}

        ahead = side(by_id.get(c.get("leader") or ""), c.get("gap"))
        back = next((o for o in self.cars if o.get("leader") == c["id"]), None)
        behind = side(back, back.get("gap") if back else None)
        if behind is not None and back is not None:
            adv = follow_advice(back["speed"], V_FREE, c["speed"], c.get("acc"), back["gap"], cooperative=behind["v2v"])
            behind["follow"], behind["follow_speed_kmh"] = adv["state"], round(adv["speed"] * 3.6, 1)
        fol = (follow_advice(c["speed"], V_FREE, ahead["speed_kmh"] / 3.6, ahead["acc"], ahead["gap_m"], cooperative=ahead["v2v"])
               if ahead else follow_advice(c["speed"], V_FREE))
        fol["speed_kmh"] = round(fol["speed"] * 3.6, 1)
        return {"type": "driver", "run_id": run_id, "t": t, "vid": c["id"], "status": "driving",
                "acc": c.get("acc", 0.0), "ahead": ahead, "behind": behind, "follow": fol,
                "from": d["from"], "to": d["to"], "mode": c["mode"],
                "target_kmh": round(c["target"] * 3.6, 1) if c["target"] is not None else None,
                "speed_kmh": round(c["speed"] * 3.6, 1), "limit_kmh": round(V_FREE * 3.6, 1), "holding": False,
                "road": edge_id(a, b, self.single), "dist_left_m": round(left), "elapsed_s": t - d["t0"],
                "tls": tls, "dist": round(max(0.0, seg_len(a, b) - STOP_LINE - c["s"]), 1) if tls else None,
                "advice": {"advice": c["advice"] or "none",
                           "speed_kmh": round(c["adv_speed"] * 3.6, 1) if c["adv_speed"] else None,
                           "green_in": c["green_in"], "green_ends_in": None, "signal": None, "queue_ahead": False},
                "advice_followed_pct": round(100 * d["adv_ok"] / d["adv_s"]) if d["adv_s"] else None,
                "stops": d["stops"], "fuel_ml": round(c["co2"] / 2.31, 1), "co2_g": round(c["co2"], 1), "amb": amb}

    def pop_driver_output(self, run_id: str) -> List[Dict[str, Any]]:
        """Driver events (as protocol events) and notices produced since the last call."""
        out: List[Dict[str, Any]] = [
            {"type": "event", "run_id": run_id, "t": e.get("t", self._t), "kind": e["kind"], "tls": "",
             "detail": e["detail"]} for e in self.events]
        out += [dict(n, run_id=run_id) for n in self.notices]
        self.events.clear()
        self.notices.clear()
        return out
