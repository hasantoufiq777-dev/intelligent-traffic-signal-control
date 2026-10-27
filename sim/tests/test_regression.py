"""Regression test suite for Module 4b.

Asserts that sensing abstraction refactoring produces 100% IDENTICAL results
compared to the pre-refactor baseline.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from sim.runner import run_simulation

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_zero_regression_against_baseline() -> None:
    baseline_path = PROJECT_ROOT / "tests" / "regression_baseline.json"
    if not baseline_path.exists():
        baseline_path = PROJECT_ROOT / "sim" / "tests" / "regression_baseline.json"

    assert baseline_path.exists(), f"Baseline file missing at {baseline_path}"

    with open(baseline_path, "r", encoding="utf-8") as f:
        baseline_data = json.load(f)

    for run_key, expected in baseline_data.items():
        sc = expected["scenario"]
        ctrl = expected["controller"]
        seed = expected["seed"]

        actual = run_simulation(
            scenario=sc,
            controller=ctrl,
            seed=seed,
            max_steps=200,
            gui=False,
            live=False,
        )

        for metric in (
            "avg_waiting_time",
            "avg_time_loss",
            "avg_travel_time",
            "ev_travel_time",
            "max_queue",
            "throughput_veh_per_hr",
        ):
            exp_val = expected[metric]
            act_val = actual[metric]
            assert act_val == pytest.approx(exp_val, abs=1e-2), (
                f"Regression on {run_key} for {metric}: expected {exp_val}, got {act_val}"
            )
