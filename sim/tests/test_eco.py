"""Tests for eco-driving (GLOSA over VANET SPaT).

Unit: the GLOSA decision rule, SPaT published by fixed and adaptive controllers, eco-car
selection. Integration: with fixed-time signals, eco-driving lowers CO2 and stops without
making trips slower, SPaT travels through the lossy channel, and results are reproducible.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from sim.controllers.adaptive import AdaptiveController
from sim.controllers.fixed import FixedTimeController
from sim.tests.test_safety import MockTrafficLightAPI
from sim.vanet.glosa import glosa_advice, is_eco_vehicle

EXACT_RED = {"signal": "r", "min_s": 20.0, "max_s": 20.0, "next_green_min_s": 20.0, "next_green_max_s": 20.0}


def test_go_when_green_surely_lasts() -> None:
    green = {"signal": "G", "min_s": 15.0, "max_s": 15.0, "next_green_min_s": 50.0, "next_green_max_s": 50.0}
    a = glosa_advice(dist_m=100.0, approach_spat=green, age_s=0.0, v_max=13.0)
    assert a["advice"] == "go" and a["speed"] == 13.0 and a["green_ends_in"] == 15.0


def test_slow_down_to_arrive_on_green() -> None:
    a = glosa_advice(dist_m=200.0, approach_spat=EXACT_RED, age_s=0.0, v_max=13.0, margin_s=1.0)
    assert a["advice"] == "slow"
    assert a["speed"] == pytest.approx(200.0 / 21.0, abs=0.01)
    assert a["green_in"] == 20.0 and a["certain"] is True


def test_message_age_is_subtracted() -> None:
    a = glosa_advice(dist_m=200.0, approach_spat=EXACT_RED, age_s=2.0, v_max=13.0, margin_s=1.0)
    assert a["green_in"] == 18.0
    assert a["speed"] == pytest.approx(200.0 / 19.0, abs=0.01)


def test_go_when_green_starts_before_arrival() -> None:
    a = glosa_advice(dist_m=300.0, approach_spat=EXACT_RED, age_s=0.0, v_max=13.0)
    assert a["advice"] == "go"  # 300 m at 13 m/s takes 23 s > 20 s + margin


def test_stop_without_speed_change_when_red_is_long() -> None:
    long_red = dict(EXACT_RED, min_s=60.0, max_s=60.0, next_green_min_s=60.0, next_green_max_s=60.0)
    a = glosa_advice(dist_m=100.0, approach_spat=long_red, age_s=0.0, v_max=13.0, v_min=5.0)
    assert a["advice"] == "stop" and a["speed"] is None


def test_no_speed_advice_when_timing_is_uncertain_or_preempted() -> None:
    uncertain = dict(EXACT_RED, max_s=45.0, next_green_max_s=45.0)  # adaptive control
    a = glosa_advice(dist_m=200.0, approach_spat=uncertain, age_s=0.0, v_max=13.0)
    assert a["advice"] == "none" and a["green_in"] == 20.0 and a["certain"] is False
    pre = dict(EXACT_RED, preempted=True)
    assert glosa_advice(200.0, pre, 0.0, 13.0)["advice"] == "none"
    assert glosa_advice(200.0, None, 0.0, 13.0)["advice"] == "none"


def _api() -> Any:
    api = type("Api", (), {})()
    api.trafficlight = MockTrafficLightAPI()
    return api


def test_fixed_controller_publishes_exact_spat() -> None:
    ctrl = FixedTimeController("J0", params={"green_ns": 30, "green_ew": 30, "yellow_time": 3, "all_red_time": 2},
                               sumo_api=_api())
    for t in range(1, 11):
        ctrl.step(float(t))
    spat = ctrl.get_spat()
    assert spat["N"]["signal"] == "G" and spat["E"]["signal"] == "r"
    assert spat["N"]["min_s"] == spat["N"]["max_s"] == 20.0  # 30 s green, 10 s used
    assert spat["E"]["next_green_min_s"] == spat["E"]["next_green_max_s"] == 25.0  # + yellow 3 + all-red 2
    assert spat["N"]["next_green_min_s"] == 60.0  # 25 + EW green 30 + 5


def test_adaptive_controller_publishes_a_range() -> None:
    ctrl = AdaptiveController("J0", params={"min_green": 10, "max_green": 50, "decision_interval": 5,
                                            "yellow_time": 3, "all_red_time": 2}, sumo_api=_api())
    ctrl.step(1.0)
    spat = ctrl.get_spat()
    assert spat["N"]["min_s"] < spat["N"]["max_s"]
    assert spat["E"]["next_green_max_s"] - spat["E"]["next_green_min_s"] > 5.0  # too uncertain for advice


def test_eco_vehicle_selection() -> None:
    ids = [f"g0_{i}" for i in range(400)]
    eco = [v for v in ids if is_eco_vehicle(v, "car", 0.5, 1.0, seed=1)]
    assert 150 < len(eco) < 250
    assert eco == [v for v in ids if is_eco_vehicle(v, "car", 0.5, 1.0, seed=1)]  # deterministic
    assert not is_eco_vehicle("amb1", "emergency", 1.0, 1.0, seed=1)
    assert not is_eco_vehicle("incblk_0_0", "incident_blocker", 1.0, 1.0, seed=1)
    assert not any(is_eco_vehicle(v, "car", 0.0, 1.0, seed=1) for v in ids)


def _run(eco: float, tag: str) -> Dict[str, Any]:
    from sim.runner import run_simulation

    with tempfile.TemporaryDirectory() as tmp:
        return run_simulation("normal", "fixed", seed=1, params={"network": "grid2x2"}, run_id=f"t_eco_{tag}",
                              max_steps=600, eco_fraction=eco, metrics_csv_path=Path(tmp) / "m.csv",
                              output_dir=Path(tmp))


@pytest.fixture(scope="module")
def fixed_runs() -> Dict[str, Dict[str, Any]]:
    return {"off": _run(0.0, "off"), "on": _run(1.0, "on"), "on2": _run(1.0, "on2")}


def test_eco_driving_lowers_co2_and_stops_with_fixed_signals(fixed_runs) -> None:
    off, on = fixed_runs["off"], fixed_runs["on"]
    print(f"\nCO2 {off['co2_g_per_km']} -> {on['co2_g_per_km']} g/km, stops {off['stops_per_vehicle']} -> "
          f"{on['stops_per_vehicle']}, travel {off['avg_travel_time']} -> {on['avg_travel_time']} s")
    assert on["co2_g_per_km"] < off["co2_g_per_km"]
    assert on["stops_per_vehicle"] < off["stops_per_vehicle"]
    assert on["avg_travel_time"] <= off["avg_travel_time"] * 1.05
    assert on["eco_advised_vehicles"] > 0 and on["eco_advice_share_slow"] > 0
    assert 0.5 < on["spat_pdr"] <= 1.0  # SPaT goes through the lossy channel
    assert off["eco_fraction"] == 0.0 and off["co2_g_per_km_eco"] is None and off["spat_pdr"] is None


def test_eco_runs_are_reproducible(fixed_runs) -> None:
    keys = ("co2_g_per_km", "stops_per_vehicle", "avg_travel_time", "eco_advised_vehicles", "spat_pdr")
    assert {k: fixed_runs["on"][k] for k in keys} == {k: fixed_runs["on2"][k] for k in keys}
