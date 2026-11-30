"""Data-driven eco-car selection: each car's measured fuel against the SUMO fuel-curve model.

Reference data: sim/data/fuel_curves.json, measured with SUMO's HBEFA4 emission model for each vehicle
category (sim/analytics/fuel_curves.py): fuel and CO2 per metre at steady speeds, the idling rate and
the cost of pulling away from a stop.

For every car on the road the simulation measures, each second (SUMO via TraCI): fuel, CO2, distance,
time moving, time stopped and stops. The model reference for the same trip is the fuel the curve gives
for driving that distance smoothly at the car's own average moving speed:

    v_avg      = distance / time moving
    model_fuel = fuel_per_m(category, v_avg) x distance
    excess     = measured_fuel − model_fuel            (mg; stops, idling, hard acceleration)
    excess_pct = excess / model_fuel x 100

That excess is what eco-driving (GLOSA speed advice to arrive on green) can save. Selection (mode
"data"): every few seconds, connected cars with at least min_dist_m of data are ranked by excess fuel
per metre; the most wasteful ones (excess_pct >= min_excess_pct) become eco cars until the budget is
used: eco_fraction x connected cars on the road. A chosen car stays an eco car until its trip ends,
so the effect of the advice shows in its numbers. Mode "random" keeps the seeded random choice.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sim.eco_speed import co2_per_m, curves, fuel_per_m
from sim.vehicles import type_category

PETROL_MG_PER_ML = 745.0  # fuel_curves density (petrol); also used for the diesel categories' display
MODES = ("data", "random")


def model_reference(category: str, dist_m: float, moving_s: float) -> Optional[Dict[str, float]]:
    """The model's fuel and CO2 (mg) for dist_m driven smoothly at the car's average moving speed."""
    if dist_m <= 0 or moving_s <= 0:
        return None
    v_avg = dist_m / moving_s
    return {"v_avg": v_avg, "fuel_mg": fuel_per_m(category, v_avg) * dist_m, "co2_mg": co2_per_m(category, v_avg) * dist_m}


def best_speed_kmh(category: str) -> float:
    """The steady speed at which the category burns the least fuel per metre (from the curve)."""
    c = curves().get(category) or curves()["car"]
    pairs = list(zip(c["speeds_kmh"], c["fuel_mg_per_m"]))
    return float(min(pairs, key=lambda p: p[1])[0])


def compare_car(category: str, acc: Dict[str, float]) -> Optional[Dict[str, Any]]:
    """One car's measured numbers against the model (None until it has moved)."""
    dist = float(acc.get("dist_m", 0.0))
    ref = model_reference(category, dist, float(acc.get("moving_s", 0.0)))
    if ref is None:
        return None
    km = dist / 1000.0
    fuel, co2 = float(acc.get("fuel_mg", 0.0)), float(acc.get("co2_mg", 0.0))
    excess = fuel - ref["fuel_mg"]
    return {
        "dist_m": round(dist, 0),
        "avg_kmh": round(ref["v_avg"] * 3.6, 1),
        "best_kmh": best_speed_kmh(category),
        "fuel_l_100km": round(fuel / PETROL_MG_PER_ML / 10.0 / km, 2) if km > 0 else None,
        "model_fuel_l_100km": round(ref["fuel_mg"] / PETROL_MG_PER_ML / 10.0 / km, 2) if km > 0 else None,
        "co2_g_km": round(co2 / 1000.0 / km, 0) if km > 0 else None,
        "model_co2_g_km": round(ref["co2_mg"] / 1000.0 / km, 0) if km > 0 else None,
        "excess_pct": round(excess / ref["fuel_mg"] * 100.0, 1) if ref["fuel_mg"] > 0 else None,
        "excess_mg_per_m": excess / dist if dist > 0 else 0.0,
        "stops": int(acc.get("stops", 0)),
        "idle_s": int(acc.get("idle_s", 0)),
    }


class EcoSelector:
    """Picks eco cars from the comparison (mode "data")."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(config or {})
        self.interval_s = float(cfg.get("select_interval_s", 5.0))
        self.min_dist_m = float(cfg.get("select_min_dist_m", 150.0))
        self.min_excess_pct = float(cfg.get("select_min_excess_pct", 10.0))
        self._last = -1e9
        self.reasons: Dict[str, str] = {}  # vid -> why it was chosen

    def due(self, t: float) -> bool:
        if t - self._last >= self.interval_s - 1e-9:
            self._last = t
            return True
        return False

    def choose(self, t: float, candidates: Dict[str, Dict[str, Any]], current_eco: set, budget: int) -> List[str]:
        """Cars to add as eco cars now. candidates: vid -> comparison (connected, eligible cars only)."""
        free = budget - len(current_eco)
        if free <= 0:
            return []
        ranked = sorted(
            (vid for vid, c in candidates.items()
             if vid not in current_eco and c["dist_m"] >= self.min_dist_m
             and c["excess_pct"] is not None and c["excess_pct"] >= self.min_excess_pct),
            key=lambda v: candidates[v]["excess_mg_per_m"], reverse=True)
        chosen = ranked[:free]
        for vid in chosen:
            c = candidates[vid]
            self.reasons[vid] = (f"chosen at {int(t)} s: {c['fuel_l_100km']} L/100 km vs model "
                                 f"{c['model_fuel_l_100km']} (+{c['excess_pct']:.0f} %), {c['stops']} stops")
        return chosen


def category_of(vtype: str) -> str:
    return type_category(vtype)
