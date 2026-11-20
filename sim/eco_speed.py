"""Estimated fuel-efficient speed for one vehicle, with the reasons, for the driver.

Inputs: the vehicle category, its speed, the speed limit here, the distance to the next traffic light
and that light's timing (SPaT received over the VANET), the vehicle ahead, and the speed of traffic
on the road ahead.

Method (an estimate, not a global optimum):

1. Hard limits on the speed: the speed limit and the vehicle's top speed; the vehicle ahead (do not
   close in on a slower vehicle within 60 m); congestion (no faster than traffic ahead plus a margin).
2. For each candidate speed v within those limits (0.5 m/s steps), the cost of covering the distance
   to the next light (or the next 300 m) is estimated with the fuel curves measured from SUMO's HBEFA4
   model for this vehicle category (sim/analytics/fuel_curves.py):
   - cruising fuel  = fuel per metre at v x distance;
   - a stop, if v makes the car reach the light at red: idling fuel until green + the fuel to speed up
     from standstill again (minus the cruising fuel those metres would have cost);
   - travel time, valued at 1.5 x the vehicle's idling fuel rate per second, so the advice never
     slows the car right down just to save a little fuel.
3. The speed with the lowest total is the recommendation; maintain / increase / reduce follows from
   the difference to the current speed (more than 1 m/s).

The result is labelled an estimated efficient speed.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

CURVES_FILE = Path(__file__).resolve().parent / "data" / "fuel_curves.json"
LOOKAHEAD_M = 300.0
LEADER_RANGE_M = 60.0
TIME_VALUE = 1.5  # travel time valued at this many seconds of idling fuel per second
UNKNOWN_RED_S = 20.0  # red time assumed when the light's next green is not known
TOLERANCE_MS = 1.0  # within this of the recommendation counts as "maintain"

_CURVES: Optional[Dict[str, Any]] = None


def curves() -> Dict[str, Any]:
    global _CURVES
    if _CURVES is None:
        _CURVES = json.loads(CURVES_FILE.read_text(encoding="utf-8"))["types"]
    return _CURVES


def _curve_for(kind: str) -> Dict[str, Any]:
    c = curves()
    return c.get(kind) or c["car"]


def _interp(xs: List[float], ys: List[float], x: float) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + f * (ys[i] - ys[i - 1])
    return ys[-1]


def fuel_per_m(kind: str, v_ms: float) -> float:
    """Fuel (mg) per metre at a steady speed (below 10 km/h: the 10 km/h value)."""
    c = _curve_for(kind)
    return _interp(c["speeds_kmh"], c["fuel_mg_per_m"], v_ms * 3.6)


def co2_per_m(kind: str, v_ms: float) -> float:
    c = _curve_for(kind)
    return _interp(c["speeds_kmh"], c["co2_mg_per_m"], v_ms * 3.6)


def _accel(kind: str, v_ms: float) -> Dict[str, float]:
    c = _curve_for(kind)
    keys = sorted(int(k) for k in c["accel_from_stop"])
    k = min(keys, key=lambda s: abs(s - v_ms * 3.6))
    return c["accel_from_stop"][str(k)]


def l_per_100km(kind: str, v_ms: float) -> float:
    c = _curve_for(kind)
    return fuel_per_m(kind, v_ms) * 100000.0 / c["density_mg_per_ml"] / 1000.0


def _arrives_on_green(t_arr: float, light: Dict[str, Any]) -> Optional[float]:
    """None if the vehicle arrives on green, otherwise the expected wait (s)."""
    sig = (light.get("signal") or "").lower()
    green_in = light.get("green_in")
    green_ends = light.get("green_ends_in")
    if sig == "g":
        if green_ends is None or t_arr <= float(green_ends):
            return None
        # red from green_ends for about UNKNOWN_RED_S: arriving later means waiting less
        wait = float(green_ends) + UNKNOWN_RED_S - t_arr
        return wait if wait > 0 else None
    if green_in is not None:
        return None if t_arr >= float(green_in) else float(green_in) - t_arr
    return UNKNOWN_RED_S if sig in ("r", "y") else None


def recommend(kind: str, speed: float, limit: float, max_speed: Optional[float] = None,
              light: Optional[Dict[str, Any]] = None, leader: Optional[Dict[str, float]] = None,
              traffic_speed: Optional[float] = None, road_left_m: Optional[float] = None) -> Dict[str, Any]:
    """Recommended speed (m/s) with reasons and the fuel/CO2 estimate for the distance ahead.

    light: {"dist": m, "signal": "G"/"r"/"y", "green_in": s, "green_ends_in": s} or None
    leader: {"gap": m, "speed": m/s} or None; traffic_speed: mean speed of the road ahead (m/s)
    """
    c = _curve_for(kind)
    reasons: List[Dict[str, str]] = []
    hi = max(0.0, min(limit, max_speed or limit))
    reasons.append({"code": "limit", "text": f"Speed limit here: {round(limit * 3.6)} km/h"})
    if leader and leader.get("gap") is not None and leader["gap"] < LEADER_RANGE_M and leader["speed"] < hi:
        cap = leader["speed"] + max(0.0, (leader["gap"] - 15.0) / 10.0)
        if cap < hi:
            hi = cap
            reasons.append({"code": "leader",
                            "text": f"Vehicle ahead at {round(leader['gap'])} m doing {round(leader['speed'] * 3.6)} km/h"})
    if traffic_speed is not None and traffic_speed < 0.6 * limit:
        cap = max(traffic_speed + 2.0, 2.0)
        if cap < hi:
            hi = cap
            reasons.append({"code": "congestion",
                            "text": f"Congestion ahead: traffic moving at {round(traffic_speed * 3.6)} km/h"})
    if hi < 0.5:
        return {"ok": True, "target_kmh": 0.0, "action": "reduce" if speed > TOLERANCE_MS else "maintain",
                "reasons": reasons, "estimated": True, "horizon_m": 0}

    horizon = LOOKAHEAD_M
    if light and light.get("dist") is not None:
        horizon = max(10.0, float(light["dist"]))
    elif road_left_m is not None:
        horizon = max(50.0, min(LOOKAHEAD_M, float(road_left_m)))
    lo = max(2.0, 0.3 * limit) if light else max(2.0, 0.6 * min(limit, hi))
    lo = min(lo, hi)
    time_value = TIME_VALUE * c["idle_fuel_mg_s"]

    def cost(v: float) -> Dict[str, Any]:
        t = horizon / v
        fuel = fuel_per_m(kind, v) * horizon
        co2 = co2_per_m(kind, v) * horizon
        wait = _arrives_on_green(t, light) if light else None
        stop = wait is not None
        if stop:
            a = _accel(kind, v)
            fuel += c["idle_fuel_mg_s"] * wait + max(0.0, a["fuel_mg"] - fuel_per_m(kind, v) * a["dist_m"])
            co2 += c["idle_co2_mg_s"] * wait + max(0.0, a["co2_mg"] - co2_per_m(kind, v) * a["dist_m"])
            t += wait + max(0.0, a["time_s"] - a["dist_m"] / v)
        return {"v": v, "fuel_mg": fuel, "co2_mg": co2, "time_s": t, "stop": stop,
                "total": fuel + time_value * t}

    steps = max(1, int(math.floor((hi - lo) / 0.5)))
    cands = [cost(lo + i * (hi - lo) / steps) for i in range(steps + 1)]
    best = min(cands, key=lambda r: r["total"])
    now = cost(max(speed, 0.5))
    target = best["v"]

    if light:
        if now["stop"] and not best["stop"]:
            reasons.append({"code": "light_slow" if target < speed else "light_fast",
                            "text": f"Light at {round(float(light['dist']))} m: at {round(target * 3.6)} km/h you "
                                    f"arrive on green instead of stopping"})
        elif best["stop"]:
            reasons.append({"code": "light_red",
                            "text": f"Light at {round(float(light['dist']))} m will be red on arrival at any "
                                    f"reasonable speed: coast and stop smoothly"})
        else:
            reasons.append({"code": "light_green", "text": f"Light at {round(float(light['dist']))} m: "
                                                          f"you arrive on green"})
    top = min(limit, max_speed or limit) * 3.6 + 0.1
    eff = min(range(len(c["speeds_kmh"])), key=lambda i: c["fuel_mg_per_m"][i] if c["speeds_kmh"][i] <= top
              else math.inf)
    reasons.append({"code": "efficient",
                    "text": f"Least fuel per km for this {c['label'].lower()} at a steady speed on this road: "
                            f"{c['speeds_kmh'][eff]} km/h"})

    diff = target - speed
    action = "maintain" if abs(diff) <= TOLERANCE_MS else ("increase" if diff > 0 else "reduce")
    dens = c["density_mg_per_ml"]

    def est(r: Dict[str, Any]) -> Dict[str, Any]:
        return {"speed_kmh": round(r["v"] * 3.6, 1), "fuel_ml": round(r["fuel_mg"] / dens, 1),
                "co2_g": round(r["co2_mg"] / 1000.0, 1), "time_s": round(r["time_s"], 1), "stop": r["stop"]}

    curve = [{"kmh": s, "l_100km": round(l_per_100km(kind, s / 3.6), 2)}
             for s in c["speeds_kmh"] if s <= max(limit * 3.6, 20) + 0.1]
    return {
        "ok": True,
        "estimated": True,  # an estimated efficient speed, not a proven optimum
        "kind": kind,
        "target_kmh": round(target * 3.6, 1),
        "action": action,
        "reasons": reasons,
        "horizon_m": round(horizon),
        "current": est(now),
        "recommended": est(best),
        "curve": curve,
    }
