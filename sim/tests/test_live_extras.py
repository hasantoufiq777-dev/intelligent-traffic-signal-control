"""Live dashboard additions (Phase 10): traffic demand factor, measured VANET jitter, and the
navigator's latest route decision (A* route or ACO alternatives with their probabilities).
Display-only additions must not change results; the demand factor is a new run setting that is
the identity at 1."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any, Dict, List

from sim.runner import run_simulation
from sim.scenarios.generate_routes import load_scenario_config, scale_demand
from sim.vanet.channel import WirelessChannel

ROOT = Path(__file__).resolve().parents[2]


def _live(**kw: Any):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = run_simulation(live=True, step_delay=0.0, params={"network": "grid2x2"}, **kw)
    msgs = [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]
    return result, [m for m in msgs if m["type"] == "state"]


def test_scale_demand_multiplies_every_flow_and_is_the_identity_at_one() -> None:
    cfg = load_scenario_config(ROOT / "sim" / "scenarios" / "normal.yaml")
    assert scale_demand(cfg, 1.0) is cfg
    half = scale_demand(cfg, 0.5)
    for a, b in zip(cfg["traffic"]["od_flows"], half["traffic"]["od_flows"]):
        assert b["rate"] == a["rate"] * 0.5
    assert half["traffic"]["rate_per_od"] == cfg["traffic"]["rate_per_od"] * 0.5
    assert cfg["traffic"]["od_flows"][0]["rate"] == 100  # original untouched


def test_demand_factor_changes_the_number_of_cars() -> None:
    _, low = _live(scenario="normal", controller="fixed", seed=1, max_steps=200, run_id="t_d1", demand=0.5)
    _, high = _live(scenario="normal", controller="fixed", seed=1, max_steps=200, run_id="t_d2", demand=2.0)
    cars = lambda states: max(s["metrics"]["n_vehicles"] for s in states)  # noqa: E731
    assert cars(high) > 2 * cars(low)


def test_jitter_is_the_standard_deviation_of_delivered_delays() -> None:
    ch = WirelessChannel.__new__(WirelessChannel)
    ch.latencies_ms = [10.0, 12.0, 14.0]
    assert abs(ch.get_jitter_ms() - 2.0) < 1e-9
    ch.latencies_ms.append(20.0)  # incremental: only the new delivery is added
    # 10, 12, 14, 20: mean 14, squared deviations 16 + 4 + 0 + 36 = 56, sample variance 56 / 3
    assert abs(ch.get_jitter_ms() - (56.0 / 3) ** 0.5) < 1e-9


def test_live_state_carries_jitter_and_the_latest_aco_decision_without_changing_results() -> None:
    kw = dict(scenario="navigation", controller="adaptive_vanet", seed=3, max_steps=240, nav="aco")
    result, states = _live(run_id="t_x1", **kw)
    assert states[-1]["comm"]["jitter_ms"] > 0
    decisions = [s["nav"]["last_decision"] for s in states if s["nav"].get("last_decision")]
    assert decisions, "no route decision streamed"
    d = decisions[-1]
    assert d["mode"] == "aco" and 1 <= len(d["routes"]) <= 3
    assert abs(sum(r["prob"] for r in d["routes"]) - 1.0) < 0.01
    assert 0 <= d["chosen"] < len(d["routes"]) and isinstance(d["applied"], bool)
    assert all(r["path"][0] == d["from"] and r["path"][-1] == d["to"] for r in d["routes"])
    # the same run without live output gives the same results
    plain = run_simulation(params={"network": "grid2x2"}, run_id="t_x2", **kw)
    keys = ("avg_waiting_time", "avg_travel_time", "throughput_veh_per_hr", "max_queue")
    assert {k: result[k] for k in keys} == {k: plain[k] for k in keys}
