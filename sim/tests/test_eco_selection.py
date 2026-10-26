"""Data-driven eco-car choice: comparison with the SUMO fuel-curve model and the selection rule."""

from __future__ import annotations

import pytest

from sim.eco_selection import EcoSelector, best_speed_kmh, compare_car, eco_rating, model_reference
from sim.eco_speed import fuel_per_m


def acc(dist=1000.0, moving=72.0, fuel_factor=1.0, stops=0, idle=0):
    """Totals of a car that drove `dist` m in `moving` s, burning fuel_factor x the model."""
    v = dist / moving
    return {"dist_m": dist, "moving_s": moving, "fuel_mg": fuel_per_m("car", v) * dist * fuel_factor,
            "co2_mg": 3000.0 * dist / 1000 * fuel_factor, "stops": stops, "idle_s": idle}


def test_model_reference_is_the_curve_at_the_average_speed():
    ref = model_reference("car", 1000.0, 72.0)  # 50 km/h
    assert ref["v_avg"] == pytest.approx(1000 / 72)
    assert ref["fuel_mg"] == pytest.approx(fuel_per_m("car", 1000 / 72) * 1000)
    assert model_reference("car", 0.0, 10.0) is None and model_reference("car", 100.0, 0.0) is None


def test_compare_car_reports_fuel_above_the_model():
    smooth = compare_car("car", acc(fuel_factor=1.0))
    wasteful = compare_car("car", acc(fuel_factor=1.5, stops=3, idle=40))
    assert smooth["excess_pct"] == pytest.approx(0.0, abs=0.1)
    assert wasteful["excess_pct"] == pytest.approx(50.0, abs=0.1)
    assert wasteful["fuel_l_100km"] > wasteful["model_fuel_l_100km"]
    assert wasteful["stops"] == 3 and wasteful["idle_s"] == 40
    assert smooth["best_kmh"] == best_speed_kmh("car") == 65.0  # cheapest steady speed on the car curve


def test_selector_picks_the_most_wasteful_within_budget():
    sel = EcoSelector({"select_min_dist_m": 150, "select_min_excess_pct": 10})
    comps = {
        "a": compare_car("car", acc(fuel_factor=1.8)),
        "b": compare_car("car", acc(fuel_factor=1.3)),
        "c": compare_car("car", acc(fuel_factor=1.05)),  # below the 10 % threshold
        "d": compare_car("car", acc(dist=100.0, moving=8.0, fuel_factor=3.0)),  # too little data
        "e": compare_car("car", acc(fuel_factor=1.6)),
    }
    assert sel.choose(10.0, comps, set(), budget=2) == ["a", "e"]
    assert "chosen at 10 s" in sel.reasons["a"] and "+80 %" in sel.reasons["a"]
    # Budget already used by current eco cars: nobody new
    assert sel.choose(15.0, comps, {"x", "y"}, budget=2) == []
    # Never c (efficient) or d (too short)
    assert set(sel.choose(20.0, comps, set(), budget=10)) == {"a", "b", "e"}


def test_selector_runs_every_interval():
    sel = EcoSelector({"select_interval_s": 5})
    assert sel.due(1.0) and not sel.due(3.0) and sel.due(6.0)


def test_eco_score_is_how_close_the_car_is_to_hbefa4():
    assert compare_car("car", acc(fuel_factor=1.0))["eco_pct"] == pytest.approx(100.0, abs=0.1)
    wasteful = compare_car("car", acc(fuel_factor=2.0))
    assert wasteful["eco_pct"] == pytest.approx(50.0, abs=0.1) and wasteful["rating"] == "wasteful"
    assert compare_car("car", acc(fuel_factor=0.8))["eco_pct"] == 100.0  # capped
    assert compare_car("car", acc(dist=80.0, moving=6.0))["eco_pct"] is None  # too short to judge
    assert [eco_rating(v) for v in (85, 70, 55, 54.9, None)] == ["eco", "good", "fair", "wasteful", None]


def test_diesel_litres_use_the_diesel_density():
    v = 1000.0 / 72.0
    fuel = fuel_per_m("bus", v) * 1000.0
    c = compare_car("bus", {"dist_m": 1000.0, "moving_s": 72.0, "fuel_mg": fuel, "co2_mg": 1.0})
    assert c["fuel_l_100km"] == pytest.approx(fuel / 835.0 / 10.0, abs=0.01)
