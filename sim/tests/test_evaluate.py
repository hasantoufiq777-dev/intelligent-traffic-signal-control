"""Tests for Module 12: evaluation plan, statistics, summary shape and resume (no SUMO)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from sim.analytics import evaluate as ev


@pytest.fixture(scope="module")
def cfg() -> Dict[str, Any]:
    return ev.load_config()


def _count(plan: ev.Plan, suite: str) -> int:
    return sum(1 for s in plan.specs if s.suite == suite)


def test_full_plan_matches_the_suite_definitions(cfg) -> None:
    plan = ev.build_plan(cfg, ev.SUITES, quick=False)
    assert plan.seeds == list(range(1, 11))
    # signals: 2 scenarios x (fixed, adaptive, adaptive_vanet x 4 penetrations)
    assert _count(plan, "signals") == 2 * 6
    # ev: fixed, adaptive, adaptive_ev, 4 losses x 2 relays, tuned
    assert _count(plan, "ev") == 3 + 8 + 1
    # nav: 2 scenarios x 2 penetrations x (none + 3 modes x 3 fractions)
    assert _count(plan, "nav") == 2 * 2 * (1 + 9)
    labels = {s.label for s in plan.specs}
    assert {"fixed", "adaptive", "adaptive_ev", "adaptive_vanet@pen0.2", "adaptive_ev_vanet@loss0.5_relay_on",
            "adaptive_ev_vanet_tuned@loss0.1", "none@pen0.5", "aco_tuned@frac0.1_pen1"} <= labels
    baselines = {(s.suite, s.baseline) for s in plan.specs}
    assert ("signals", "fixed") in baselines and ("ev", "adaptive") in baselines
    nav_base = {s.baseline for s in plan.specs if s.suite == "nav"}
    assert nav_base == {"none@pen1", "none@pen0.5"}


def test_quick_plan_is_smaller(cfg) -> None:
    full = ev.build_plan(cfg, ev.SUITES, quick=False)
    quick = ev.build_plan(cfg, ev.SUITES, quick=True)
    assert quick.seeds == [1, 2] and len(quick.specs) < len(full.specs)
    assert quick.max_steps == cfg["evaluation"]["quick"]["max_steps"]


def _rows(values: Dict[str, List[float]], metric: str = "avg_waiting_time", suite: str = "signals") -> List[Dict[str, Any]]:
    rows = []
    for label, vals in values.items():
        for seed, v in enumerate(vals, 1):
            rows.append({"suite": suite, "scenario": "normal", "label": label, "baseline": "fixed",
                         "seed": str(seed), "error": "", metric: str(v)})
    return rows


def test_summary_shape_and_improvement_sign(cfg) -> None:
    specs = ev.build_plan(cfg, ["signals"], quick=False).specs
    rows = _rows({"fixed": [20, 22, 21, 23, 20, 22, 21], "adaptive": [10, 11, 10, 12, 11, 10, 11]})
    for r in rows:  # a higher-is-better metric in the same rows
        r["throughput_veh_per_hr"] = str(1050 if r["label"] == "adaptive" else 1000)
    s = ev.summarise(rows, specs)
    entry = s["suites"]["signals"]["normal"]["adaptive"]["avg_waiting_time"]
    assert set(entry) >= {"mean", "std", "ci95", "improvement_pct", "p_value"}
    assert entry["improvement_pct"] > 45  # lower waiting time = positive improvement
    assert entry["ci95"][0] < entry["mean"] < entry["ci95"][1]
    assert entry["p_value"] is not None and entry["p_value"] < 0.05 and entry["test"] == "wilcoxon"
    thr = s["suites"]["signals"]["normal"]["adaptive"]["throughput_veh_per_hr"]
    assert thr["improvement_pct"] == pytest.approx(5.0)  # higher throughput = positive improvement
    base = s["suites"]["signals"]["normal"]["fixed"]["avg_waiting_time"]
    assert base["improvement_pct"] is None and base["p_value"] is None
    json.dumps(s)  # serialisable


def test_paired_test_uses_t_test_for_few_seeds() -> None:
    p, test = ev.paired_p([1.0, 2.0, 3.0], [2.0, 3.5, 4.0])
    assert test == "paired_t" and 0 < p < 1
    assert ev.paired_p([1.0, 2.0], [1.0, 2.0]) == (1.0, "identical")


def test_failed_rows_are_ignored_in_stats(cfg) -> None:
    specs = ev.build_plan(cfg, ["signals"], quick=False).specs
    rows = _rows({"fixed": [20, 22], "adaptive": [10, 12]})
    rows.append({"suite": "signals", "scenario": "normal", "label": "adaptive", "baseline": "fixed",
                 "seed": "3", "error": "RuntimeError('x')", "avg_waiting_time": "999"})
    entry = ev.summarise(rows, specs)["suites"]["signals"]["normal"]["adaptive"]["avg_waiting_time"]
    assert entry["n"] == 2 and entry["mean"] == 11.0


def test_execute_resumes_and_skips_missing_tuned_files(cfg, tmp_path: Path, monkeypatch) -> None:
    calls: List[str] = []

    def fake_run(job: Dict[str, Any]) -> Dict[str, Any]:
        calls.append(job["run_key"])
        row = {k: job.get(k) for k in ev.RAW_ID_FIELDS}
        row.update({"avg_waiting_time": 10.0, "ev_travel_time": 50.0, "wall_s": 0.1, "error": ""})
        return row

    monkeypatch.setattr(ev, "run_one", fake_run)
    plan = ev.build_plan(cfg, ["ev"], quick=True)
    for s in plan.specs:  # point the tuned config at a file that does not exist
        if s.requires:
            s.requires = "results/ga/__missing__.yaml"
    raw = tmp_path / "raw.csv"
    ev.execute(plan, raw, workers=1, default_s=1.0)
    n_runnable = sum(1 for s in plan.specs if not s.requires) * len(plan.seeds)
    assert len(calls) == n_runnable
    assert not any("tuned" in k for k in calls)

    ev.execute(plan, raw, workers=1, default_s=1.0)  # everything already in raw.csv
    assert len(calls) == n_runnable
    assert len(ev.read_raw(raw)) == n_runnable


def test_failed_runs_are_retried(cfg, tmp_path: Path, monkeypatch) -> None:
    attempt = {"n": 0}

    def flaky(job: Dict[str, Any]) -> Dict[str, Any]:
        attempt["n"] += 1
        row = {k: job.get(k) for k in ev.RAW_ID_FIELDS}
        row["error"] = "boom" if attempt["n"] == 1 else ""
        return row

    monkeypatch.setattr(ev, "run_one", flaky)
    plan = ev.build_plan(cfg, ["signals"], quick=True)
    plan.specs, plan.seeds = plan.specs[:1], [1]
    raw = tmp_path / "raw.csv"
    ev.execute(plan, raw, workers=1, default_s=1.0)
    ev.execute(plan, raw, workers=1, default_s=1.0)
    rows = ev.read_raw(raw)
    assert attempt["n"] == 2 and len(rows) == 1 and rows[0]["error"] == ""
