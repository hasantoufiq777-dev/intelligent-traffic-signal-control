"""Tests for Module 11: GA tuning.

Unit tests use a fake simulation runner (fast, no SUMO): operators stay in bounds, the
GA improves on a known fitness landscape, elitism keeps the best, identical chromosomes
are simulated once, and a run is reproducible. Integration tests check that the tuned
YAML files really change what the *_tuned controller and --nav aco_tuned run, and that
the CLI completes a tiny tuning run.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

from sim.optimizer import ga_tuner as ga


@pytest.fixture(scope="module")
def cfg() -> Dict[str, Any]:
    return ga.load_config()


class FakeRunner:
    """Stands in for SUMO: waiting time is a bowl with its minimum at min_green=20, max_green=60."""

    def __init__(self) -> None:
        self.calls = 0

    def map(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for t in tasks:
            self.calls += 1
            p = t["params"]
            if "aco" in p:
                a = p["aco"]
                out.append({"nav_avg_travel_time": 100 + (a["beta"] - 3) ** 2, "edge_load_cv": 0.5,
                            "route_search_ms": 10.0})
            else:
                wait = 10 + 0.1 * (p["min_green"] - 20) ** 2 + 0.01 * (p["max_green"] - 60) ** 2
                out.append({"avg_waiting_time": wait, "ev_travel_time": 50.0, "missed_preemptions": 0})
        return out


def _tuner(cfg, tmp_path: Path, target="signals", pop=8, gens=6, runner=None) -> ga.GeneticTuner:
    return ga.GeneticTuner(target, cfg, runner or FakeRunner(), tmp_path, pop, gens, [101], 100)


def test_gene_clip_respects_bounds_and_integers() -> None:
    g = ga.Gene("n_ants", 3, 30, "int")
    assert g.clip(2.2) == 3.0 and g.clip(99) == 30.0 and g.clip(7.6) == 8.0
    f = ga.Gene("rho", 0.02, 0.6, "float")
    assert f.clip(-1) == 0.02 and f.clip(0.123456) == 0.1235


def test_operators_stay_in_bounds(cfg, tmp_path: Path) -> None:
    t = _tuner(cfg, tmp_path)
    for _ in range(300):
        a, b = t.random_chromosome(), t.random_chromosome()
        for c in (*t.crossover(a, b), t.mutate(a)):
            for g in t.genes:
                assert g.low <= c[g.name] <= g.high
                if g.kind == "int":
                    assert float(c[g.name]).is_integer()


def test_defaults_come_from_config(cfg, tmp_path: Path) -> None:
    d = _tuner(cfg, tmp_path).defaults
    assert d["min_green"] == cfg["controllers"]["adaptive"]["min_green"]
    assert d["eta_threshold"] == cfg["controllers"]["preemption"]["eta_threshold"]
    assert d["beacon_interval"] == cfg["vanet"]["beacon_interval"]
    a = _tuner(cfg, tmp_path, target="aco").defaults
    assert a["n_ants"] == cfg["aco"]["n_ants"] and a["beta"] == cfg["aco"]["beta"]


def test_constraints_and_param_mapping() -> None:
    sig = {"min_green": 30, "max_green": 25, "queue_threshold": 5.0, "decision_interval": 5,
           "eta_threshold": 18, "alert_interval": 0.5, "beacon_interval": 1.0}
    assert ga.constraint_violation("signals", sig) == 6.0
    assert ga.constraint_violation("signals", dict(sig, max_green=60)) == 0.0
    assert ga.constraint_violation("aco", {"elite_k": 5, "n_ants": 3}) == 2.0
    p = ga.chromosome_to_params("signals", sig, "grid2x2")
    assert p["vanet"] == {"alert_interval": 0.5, "beacon_interval": 1.0}
    assert p["queue_threshold"] == 5 and isinstance(p["queue_threshold"], int)
    assert "alert_interval" not in p and p["network"] == "grid2x2"


def test_infeasible_chromosome_is_penalised_without_simulating(cfg, tmp_path: Path) -> None:
    runner = FakeRunner()
    t = _tuner(cfg, tmp_path, runner=runner)
    bad = dict(t.defaults, min_green=30.0, max_green=20.0)
    assert t.evaluate([bad])[0] >= cfg["ga"]["penalty"]
    assert runner.calls == 0


def test_ga_improves_and_keeps_the_best(cfg, tmp_path: Path) -> None:
    result = _tuner(cfg, tmp_path, pop=10, gens=8).run()
    best = [g["best_fitness"] for g in result["generations"]]
    assert all(b2 <= b1 for b1, b2 in zip(best, best[1:])), "elitism: best never gets worse"
    # Defaults (min_green 10, max_green 50) are off the bowl's minimum, so the GA must beat them
    assert result["best_fitness"] < result["default_fitness"]
    assert abs(result["best_chromosome"]["min_green"] - 20) < abs(result["default_chromosome"]["min_green"] - 20)


def test_cache_simulates_identical_chromosomes_once(cfg, tmp_path: Path) -> None:
    runner = FakeRunner()
    t = _tuner(cfg, tmp_path, runner=runner)
    c = dict(t.defaults, min_green=12.0)  # feasible, so it is simulated
    t.evaluate([c, dict(c)])
    per_chromosome = len(t.scenarios) * len(t.seeds)
    assert runner.calls == per_chromosome
    t.evaluate([c])
    assert runner.calls == per_chromosome
    # The cache is on disk: a new tuner with the same settings does not re-simulate
    t2 = _tuner(cfg, tmp_path, runner=runner)
    t2.evaluate([c])
    assert runner.calls == per_chromosome


def test_ga_is_reproducible(cfg, tmp_path: Path) -> None:
    r1 = _tuner(cfg, tmp_path / "a").run()
    r2 = _tuner(cfg, tmp_path / "b").run()
    assert r1["best_chromosome"] == r2["best_chromosome"]
    assert [g["best_fitness"] for g in r1["generations"]] == [g["best_fitness"] for g in r2["generations"]]


def test_ev_not_arriving_is_penalised(cfg) -> None:
    task = {"has_ev": True, "max_steps": 300}
    arrived = ga.run_cost("signals", {"avg_waiting_time": 10, "ev_travel_time": 60}, cfg["ga"], task)
    stuck = ga.run_cost("signals", {"avg_waiting_time": 10, "ev_travel_time": 0.0}, cfg["ga"], task)
    assert stuck > arrived + cfg["ga"]["penalty"] / 2


# ---------------------------------------------------------------------------- SUMO
def _short_run(tmp_path: Path, tag: str, **kw: Any) -> Dict[str, Any]:
    from sim.runner import run_simulation

    return run_simulation(seed=1, run_id=f"t_ga_{tag}", max_steps=kw.pop("max_steps", 150),
                          metrics_csv_path=tmp_path / f"{tag}.csv", output_dir=tmp_path, **kw)


def test_tuned_controller_uses_the_tuned_file(tmp_path: Path) -> None:
    genes = {"min_green": 25.0, "max_green": 35.0, "queue_threshold": 2.0, "decision_interval": 3.0,
             "eta_threshold": 30.0, "alert_interval": 1.0, "beacon_interval": 2.0}
    path = tmp_path / "tuned.yaml"
    content = ga.tuned_file_content("signals", genes)
    content["meta"] = {"note": "ignored by the runner"}
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    keys = ("avg_waiting_time", "avg_travel_time", "pdr", "queue_estimate_mae")

    via_file = _short_run(tmp_path, "file", scenario="emergency", controller="adaptive_ev_vanet_tuned",
                          params={"network": "grid2x2", "tuned_params_file": str(path)}, penetration=0.5)
    via_params = _short_run(tmp_path, "params", scenario="emergency", controller="adaptive_ev_vanet",
                            params=ga.chromosome_to_params("signals", genes, "grid2x2"), penetration=0.5)
    defaults = _short_run(tmp_path, "default", scenario="emergency", controller="adaptive_ev_vanet",
                          params={"network": "grid2x2"}, penetration=0.5)
    assert {k: via_file[k] for k in keys} == {k: via_params[k] for k in keys}
    assert {k: via_file[k] for k in keys} != {k: defaults[k] for k in keys}


def test_aco_tuned_uses_the_tuned_file(tmp_path: Path) -> None:
    aco = {"alpha": 2.5, "beta": 0.7, "rho": 0.5, "n_ants": 4, "n_iterations": 3, "Q": 50.0, "elite_k": 2}
    path = tmp_path / "aco.yaml"
    path.write_text(yaml.safe_dump(dict(aco, meta={"x": 1})), encoding="utf-8")
    keys = ("route_search_expansions", "reroute_count", "avg_travel_time")
    via_file = _short_run(tmp_path, "aco_f", scenario="navigation", controller="adaptive_vanet",
                          nav="aco_tuned", aco_params=str(path), max_steps=200)
    via_params = _short_run(tmp_path, "aco_p", scenario="navigation", controller="adaptive_vanet",
                            nav="aco", params={"aco": aco}, max_steps=200)
    assert {k: via_file[k] for k in keys} == {k: via_params[k] for k in keys}


def test_missing_tuned_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="ga_tuner.py --target signals"):
        _short_run(tmp_path, "missing", scenario="emergency", controller="adaptive_ev_vanet_tuned",
                   params={"tuned_params_file": str(tmp_path / "nope.yaml")})


def test_cli_tiny_run_writes_outputs(tmp_path: Path) -> None:
    params_file = tmp_path / "best_params_signals.yaml"
    rc = ga.main(["--target", "signals", "--population", "2", "--generations", "2", "--max-steps", "60",
                  "--workers", "1", "--skip-eval", "--out-dir", str(tmp_path), "--params-file", str(params_file)])
    assert rc == 0
    for name in ("ga_signals_history.json", "ga_signals_history.csv", "ga_signals_convergence.png",
                 "ga_signals_cache.json"):
        assert (tmp_path / name).exists(), name
    tuned = yaml.safe_load(params_file.read_text(encoding="utf-8"))
    assert set(tuned["vanet"]) == {"alert_interval", "beacon_interval"}
    assert "min_green" in tuned and "meta" in tuned
    history = json.loads((tmp_path / "ga_signals_history.json").read_text(encoding="utf-8"))
    assert len(history["generations"]) == 2
