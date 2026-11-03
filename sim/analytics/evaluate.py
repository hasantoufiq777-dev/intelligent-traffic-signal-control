"""Full evaluation with three suites and statistics (Module 12).

Suites (all on the 2x2 grid, seeds 1-10 by default):
- signals: scenarios normal and congested. fixed, adaptive (oracle), adaptive_vanet at
  penetration 0.2/0.5/0.8/1.0. Baseline: fixed.
- ev: scenario emergency. fixed, adaptive, adaptive_ev (oracle), adaptive_ev_vanet at
  loss 0/0.1/0.3/0.5 with relay on and off (range 300), adaptive_ev_vanet_tuned at loss 0.1.
  Baseline: adaptive.
- nav: scenarios navigation and incident, controller adaptive_vanet, penetration 1.0 and 0.5.
  nav none, astar, aco, aco_tuned at nav-fraction 0.1/0.3/0.5. Baseline: nav none at the
  same penetration.

Every run is one row in results/raw.csv. A rerun skips rows that already exist (resume).
Statistics per scenario and configuration: mean, std, 95% CI (t-distribution), percent
improvement versus the baseline, and a paired test by seed (Wilcoxon signed-rank with at
least 6 paired seeds, otherwise a paired t-test).

Outputs: results/summary.json (read by the dashboard), results/summary.md, PNG plots.

Usage:
    python sim/analytics/evaluate.py --suite all [--quick] [--workers 8]
    python sim/analytics/evaluate.py --suite ev --summary-only     # stats/plots from raw.csv
Logs go to stderr.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import yaml

logger = logging.getLogger("evaluate")

SUITES = ("signals", "ev", "nav", "eco")

# Metrics summarised per suite (raw.csv keeps every field of the results dict)
SUITE_METRICS: Dict[str, List[str]] = {
    "signals": ["avg_waiting_time", "avg_time_loss", "avg_travel_time", "throughput_veh_per_hr",
                "max_queue", "queue_estimate_mae", "pdr"],
    "ev": ["ev_travel_time", "avg_waiting_time", "avg_travel_time", "missed_preemptions",
           "ev_alert_delivery_time", "avg_hops", "pdr"],
    "nav": ["avg_travel_time", "avg_waiting_time", "nav_avg_travel_time", "nonnav_avg_travel_time",
            "edge_load_cv", "max_edge_load", "route_search_ms", "route_search_expansions", "reroute_count",
            "incident_detection_delay", "incident_rerouted_vehicles"],
    "eco": ["co2_g_per_km", "fuel_l_per_100km", "stops_per_vehicle", "avg_travel_time", "avg_waiting_time",
            "co2_g_per_km_eco", "co2_g_per_km_non", "stops_per_vehicle_eco", "stops_per_vehicle_non",
            "eco_advice_share_slow", "spat_pdr"],
}
HIGHER_IS_BETTER = {"throughput_veh_per_hr", "pdr"}
# Metrics where "improvement" has no meaning (descriptive only)
NO_IMPROVEMENT = {"route_search_expansions", "reroute_count", "avg_hops", "incident_rerouted_vehicles",
                  "eco_advice_share_slow", "spat_pdr", "co2_g_per_km_eco", "co2_g_per_km_non",
                  "stops_per_vehicle_eco", "stops_per_vehicle_non"}

RAW_ID_FIELDS = ["run_key", "suite", "scenario", "label", "baseline", "seed", "controller", "nav",
                 "nav_fraction", "penetration", "loss", "range", "relay", "eco_fraction", "max_steps", "wall_s",
                 "error"]
RESULT_FIELDS = [
    "avg_waiting_time", "avg_time_loss", "avg_travel_time", "ev_travel_time", "max_queue",
    "throughput_veh_per_hr", "pdr", "avg_latency_ms", "avg_hops", "missed_preemptions",
    "ev_alert_delivery_time", "queue_estimate_mae", "nav_mode", "nav_avg_travel_time",
    "nonnav_avg_travel_time", "edge_load_cv", "max_edge_load", "route_search_expansions",
    "route_search_ms", "reroute_count", "incident_detected", "incident_detection_delay",
    "incident_false_flags", "incident_rerouted_vehicles",
    "co2_total_kg", "fuel_total_l", "co2_g_per_km", "fuel_l_per_100km", "stops_per_vehicle",
    "co2_g_per_km_eco", "co2_g_per_km_non", "fuel_l_per_100km_eco", "fuel_l_per_100km_non",
    "stops_per_vehicle_eco", "stops_per_vehicle_non", "travel_time_eco", "travel_time_non",
    "eco_advised_vehicles", "eco_advice_share_slow", "eco_advice_share_stop", "spat_pdr",
]
RAW_FIELDS = RAW_ID_FIELDS + RESULT_FIELDS


# --------------------------------------------------------------------------- run plan
@dataclass
class RunSpec:
    """One configuration of one suite (run once per seed)."""

    suite: str
    scenario: str
    label: str
    baseline: str
    controller: str
    nav: str = "none"
    nav_fraction: float = 0.3
    penetration: float = 1.0
    loss: float = 0.0
    range: float = 300.0
    relay: str = "off"
    eco_fraction: float = 0.0
    requires: Optional[str] = None  # tuned file this config needs
    description: str = ""

    def key(self, seed: int) -> str:
        return f"{self.suite}|{self.scenario}|{self.label}|{seed}"


@dataclass
class Plan:
    seeds: List[int]
    max_steps: Optional[int]
    network: str
    specs: List[RunSpec] = field(default_factory=list)


def load_config() -> Dict[str, Any]:
    with open(PROJECT_ROOT / "sim" / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _fmt(x: float) -> str:
    return f"{x:g}"


def build_plan(cfg: Dict[str, Any], suites: Iterable[str], quick: bool) -> Plan:
    ev_cfg = cfg["evaluation"]
    q = ev_cfg.get("quick", {})
    seeds = list(q["seeds"]) if quick else list(ev_cfg["seeds"])
    max_steps = q.get("max_steps") if quick else ev_cfg.get("max_steps")
    plan = Plan(seeds=seeds, max_steps=max_steps, network=str(ev_cfg.get("network", "grid2x2")))
    ga = cfg.get("ga", {})
    signals_file = ga.get("signals_params_file", "results/ga/best_params_signals.yaml")
    aco_file = ga.get("aco_params_file", "results/ga/best_params_aco.yaml")

    if "signals" in suites:
        s = ev_cfg["signals"]
        pens = q["signals_penetrations"] if quick else s["penetrations"]
        for scen in s["scenarios"]:
            plan.specs += [
                RunSpec("signals", scen, "fixed", "fixed", "fixed", description="Fixed-time baseline"),
                RunSpec("signals", scen, "adaptive", "fixed", "adaptive", description="Adaptive, oracle sensing"),
            ]
            for p in pens:
                plan.specs.append(RunSpec("signals", scen, f"adaptive_vanet@pen{_fmt(p)}", "fixed", "adaptive_vanet",
                                          penetration=float(p), description=f"Adaptive, VANET sensing, penetration {p}"))

    if "ev" in suites:
        e = ev_cfg["ev"]
        scen, pen, rng = e["scenario"], float(e.get("penetration", 1.0)), float(e.get("range", 300.0))
        losses = q["ev_losses"] if quick else e["losses"]
        plan.specs += [
            RunSpec("ev", scen, "fixed", "adaptive", "fixed", description="Fixed-time, no preemption"),
            RunSpec("ev", scen, "adaptive", "adaptive", "adaptive", description="Adaptive, no preemption (baseline)"),
            RunSpec("ev", scen, "adaptive_ev", "adaptive", "adaptive_ev", description="Adaptive + oracle preemption"),
        ]
        for loss in losses:
            for relay in e["relays"]:
                plan.specs.append(RunSpec(
                    "ev", scen, f"adaptive_ev_vanet@loss{_fmt(loss)}_relay_{relay}", "adaptive", "adaptive_ev_vanet",
                    penetration=pen, loss=float(loss), range=rng, relay=str(relay),
                    description=f"VANET preemption, loss {loss}, relay {relay}"))
        tl = float(e.get("tuned_loss", 0.1))
        plan.specs.append(RunSpec(
            "ev", scen, f"adaptive_ev_vanet_tuned@loss{_fmt(tl)}", "adaptive", "adaptive_ev_vanet_tuned",
            penetration=pen, loss=tl, range=rng, relay="off", requires=signals_file,
            description=f"VANET preemption with GA-tuned parameters, loss {tl}, relay off"))

    if "nav" in suites:
        n = ev_cfg["nav"]
        pens = q["nav_penetrations"] if quick else n["penetrations"]
        fracs = q["nav_fractions"] if quick else n["fractions"]
        for scen in n["scenarios"]:
            for p in pens:
                base = f"none@pen{_fmt(p)}"
                plan.specs.append(RunSpec("nav", scen, base, base, n["controller"], nav="none", penetration=float(p),
                                          description=f"No navigation, penetration {p}"))
                for mode in n["modes"]:
                    for f in fracs:
                        plan.specs.append(RunSpec(
                            "nav", scen, f"{mode}@frac{_fmt(f)}_pen{_fmt(p)}", base, n["controller"], nav=mode,
                            nav_fraction=float(f), penetration=float(p),
                            requires=aco_file if mode == "aco_tuned" else None,
                            description=f"{mode} navigation, fraction {f}, penetration {p}"))

    if "eco" in suites:
        e = ev_cfg["eco"]
        fracs = q.get("eco_fractions", [0.0, 1.0]) if quick else e["fractions"]
        scens = q.get("eco_scenarios", e["scenarios"]) if quick else e["scenarios"]
        for scen in scens:
            for ctrl in e["controllers"]:
                base = f"{ctrl}@eco0"
                for f in fracs:
                    plan.specs.append(RunSpec(
                        "eco", scen, f"{ctrl}@eco{_fmt(f)}", base, ctrl, penetration=float(e.get("penetration", 1.0)),
                        eco_fraction=float(f),
                        description=f"{ctrl}, {int(round(float(f) * 100))}% of cars follow GLOSA advice"))
    return plan


# --------------------------------------------------------------------------- running
def run_one(job: Dict[str, Any]) -> Dict[str, Any]:
    """Run one simulation; top level so a process pool can pickle it."""
    from sim.runner import run_simulation

    t0 = time.time()
    row: Dict[str, Any] = {k: job.get(k) for k in RAW_ID_FIELDS}
    with tempfile.TemporaryDirectory(prefix="eval_") as tmp:
        try:
            res = run_simulation(
                scenario=job["scenario"], controller=job["controller"], seed=int(job["seed"]),
                params={"network": job["network"]},
                run_id=f"ev_{hashlib.sha1(job['run_key'].encode()).hexdigest()[:10]}_{os.getpid()}",
                max_steps=job["max_steps"], metrics_csv_path=Path(tmp) / "m.csv", output_dir=Path(tmp),
                nav=job["nav"], nav_fraction=float(job["nav_fraction"]), penetration=float(job["penetration"]),
                loss=float(job["loss"]), range_m=float(job["range"]), relay=job["relay"],
                eco_fraction=float(job.get("eco_fraction") or 0.0),
            )
            row.update({k: res.get(k) for k in RESULT_FIELDS})
        except Exception as e:  # keep going; the row records the failure
            row["error"] = repr(e)[:300]
    row["wall_s"] = round(time.time() - t0, 1)
    return row


def read_raw(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(v: Any) -> Optional[float]:
    if v is None or v == "" or v == "None":
        return None
    if isinstance(v, bool):
        return float(v)
    if v in ("True", "False"):
        return 1.0 if v == "True" else 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def execute(plan: Plan, raw_path: Path, workers: int, default_s: float) -> None:
    """Run every (spec, seed) not yet in raw.csv, appending rows as they finish."""
    done = {r["run_key"] for r in read_raw(raw_path) if not r.get("error")}
    jobs: List[Dict[str, Any]] = []
    skipped_specs: List[str] = []
    for spec in plan.specs:
        if spec.requires and not (PROJECT_ROOT / spec.requires).exists():
            skipped_specs.append(f"{spec.suite}/{spec.scenario}/{spec.label} (missing {spec.requires})")
            continue
        for seed in plan.seeds:
            key = spec.key(seed)
            if key in done:
                continue
            jobs.append({
                "run_key": key, "suite": spec.suite, "scenario": spec.scenario, "label": spec.label,
                "baseline": spec.baseline, "seed": seed, "controller": spec.controller, "nav": spec.nav,
                "nav_fraction": spec.nav_fraction, "penetration": spec.penetration, "loss": spec.loss,
                "range": spec.range, "relay": spec.relay, "eco_fraction": spec.eco_fraction,
                "max_steps": plan.max_steps, "network": plan.network,
            })
    for s in skipped_specs:
        logger.warning("Skipping %s. Run the GA tuner first (Module 11).", s)

    timed = [_num(r.get("wall_s")) for r in read_raw(raw_path)]
    timed = [t for t in timed if t]
    per_run = float(np.mean(timed)) if timed else default_s
    if plan.max_steps and not timed:
        per_run *= plan.max_steps / 1000.0
    total = len(plan.specs) * len(plan.seeds)
    n_skipped = len(skipped_specs) * len(plan.seeds)
    est = len(jobs) * per_run / max(1, workers)
    logger.info("Runs: %d planned, %d skipped (no tuned file), %d already in %s, %d to run.",
                total, n_skipped, total - n_skipped - len(jobs), raw_path.name, len(jobs))
    logger.info("Runtime estimate: %d runs x %.0f s / %d worker(s) = %.1f min",
                len(jobs), per_run, workers, est / 60.0)
    if not jobs:
        return

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not raw_path.exists()
    if not new_file:
        _rewrite_without_errors(raw_path)
    t0 = time.time()
    with open(raw_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RAW_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()

        def _store(i: int, row: Dict[str, Any]) -> None:
            writer.writerow(row)
            f.flush()
            if row.get("error"):
                logger.warning("Run %s failed: %s", row["run_key"], row["error"])
            if i % 10 == 0 or i == len(jobs):
                el = time.time() - t0
                logger.info("%d/%d runs done (%.1f min elapsed, ~%.1f min left)", i, len(jobs), el / 60,
                            el / i * (len(jobs) - i) / 60)

        if workers > 1:
            import multiprocessing as mp

            with mp.get_context("spawn").Pool(workers) as pool:
                for i, row in enumerate(pool.imap_unordered(run_one, jobs), 1):
                    _store(i, row)
        else:
            for i, job in enumerate(jobs, 1):
                _store(i, run_one(job))


def _rewrite_without_errors(path: Path) -> None:
    """Drop failed rows so they are retried (and the header matches the current fields)."""
    rows = [r for r in read_raw(path) if not r.get("error")]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RAW_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------- statistics
def describe(values: List[float]) -> Tuple[float, float, List[float]]:
    from scipy import stats

    n = len(values)
    mean = float(np.mean(values))
    if n < 2:
        return mean, 0.0, [mean, mean]
    std = float(np.std(values, ddof=1))
    half = float(stats.t.ppf(0.975, n - 1)) * std / math.sqrt(n)
    return mean, std, [mean - half, mean + half]


def paired_p(a: List[float], b: List[float]) -> Tuple[Optional[float], Optional[str]]:
    """Two-sided paired test: Wilcoxon signed-rank with >= 6 pairs, else paired t-test."""
    from scipy import stats

    if len(a) < 2:
        return None, None
    diffs = np.array(a) - np.array(b)
    if np.allclose(diffs, 0.0):
        return 1.0, "identical"
    if len(a) >= 6:
        try:
            return float(stats.wilcoxon(a, b).pvalue), "wilcoxon"
        except ValueError:
            pass
    p = stats.ttest_rel(a, b).pvalue
    return (float(p) if np.isfinite(p) else None), "paired_t"


def summarise(rows: List[Dict[str, Any]], plan_specs: List[RunSpec]) -> Dict[str, Any]:
    """Build the summary.json structure from raw rows."""
    ok = [r for r in rows if not r.get("error")]
    by_cfg: Dict[Tuple[str, str, str], Dict[int, Dict[str, Any]]] = {}
    baseline_of: Dict[Tuple[str, str, str], str] = {}
    for r in ok:
        k = (r["suite"], r["scenario"], r["label"])
        by_cfg.setdefault(k, {})[int(r["seed"])] = r
        baseline_of[k] = r["baseline"]

    order = {(s.suite, s.scenario, s.label): i for i, s in enumerate(plan_specs)}
    suites: Dict[str, Any] = {}
    configs_meta: Dict[str, Any] = {}
    for k in sorted(by_cfg, key=lambda x: (SUITES.index(x[0]) if x[0] in SUITES else 9, order.get(x, 10**6), x)):
        suite, scen, label = k
        seed_rows = by_cfg[k]
        base_rows = by_cfg.get((suite, scen, baseline_of[k]), {})
        out: Dict[str, Any] = {}
        for metric in SUITE_METRICS.get(suite, []):
            vals = {s: _num(r.get(metric)) for s, r in seed_rows.items()}
            vals = {s: v for s, v in vals.items() if v is not None}
            if not vals:
                continue
            mean, std, ci = describe(list(vals.values()))
            entry: Dict[str, Any] = {"mean": round(mean, 4), "std": round(std, 4),
                                     "ci95": [round(ci[0], 4), round(ci[1], 4)], "n": len(vals),
                                     "improvement_pct": None, "p_value": None, "test": None}
            bvals = {s: _num(r.get(metric)) for s, r in base_rows.items()}
            paired = sorted(s for s in vals if bvals.get(s) is not None)
            if label != baseline_of[k] and paired and metric not in NO_IMPROVEMENT:
                b_mean = float(np.mean([bvals[s] for s in paired]))
                c_mean = float(np.mean([vals[s] for s in paired]))
                if abs(b_mean) > 1e-9:
                    sign = 1.0 if metric in HIGHER_IS_BETTER else -1.0
                    entry["improvement_pct"] = round(100.0 * sign * (c_mean - b_mean) / abs(b_mean), 2)
                p, test = paired_p([vals[s] for s in paired], [bvals[s] for s in paired])
                entry["p_value"] = round(p, 5) if p is not None else None
                entry["test"] = test
            out[metric] = entry
        suites.setdefault(suite, {}).setdefault(scen, {})[label] = out
        spec = next((s for s in plan_specs if (s.suite, s.scenario, s.label) == k), None)
        configs_meta.setdefault(suite, {})[label] = {
            "baseline": baseline_of[k], "description": spec.description if spec else "",
            "controller": spec.controller if spec else None,
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "suites": suites,
        "meta": {
            "configs": configs_meta,
            "seeds": sorted({int(r["seed"]) for r in ok}),
            "higher_is_better": sorted(HIGHER_IS_BETTER),
            "improvement_note": "Percent improvement versus the suite baseline (positive = better), paired by seed.",
        },
    }


def write_markdown(summary: Dict[str, Any], path: Path) -> None:
    lines = [
        "# Evaluation summary",
        "",
        f"Generated {summary['generated_at']}. Seeds: {summary['meta']['seeds']}.",
        "Cells: mean ± std (improvement vs baseline, paired p-value). Positive improvement = better.",
        "p-values: Wilcoxon signed-rank with >= 6 paired seeds, otherwise paired t-test.",
        "",
    ]
    titles = {"signals": "Signal control (baseline: fixed)", "ev": "Emergency vehicles (baseline: adaptive)",
              "nav": "Navigation (baseline: no navigation, same penetration)",
              "eco": "Eco-driving / GLOSA (baseline: same controller without eco cars)"}
    for suite in SUITES:
        if suite not in summary["suites"]:
            continue
        lines += [f"## {titles[suite]}", ""]
        for scen, cfgs in summary["suites"][suite].items():
            metrics = [m for m in SUITE_METRICS[suite] if any(m in c for c in cfgs.values())]
            lines += [f"### Scenario: {scen}", "",
                      "| Config | " + " | ".join(metrics) + " |",
                      "|---|" + "---|" * len(metrics)]
            for label, c in cfgs.items():
                cells = []
                for m in metrics:
                    e = c.get(m)
                    if not e:
                        cells.append("")
                        continue
                    cell = f"{e['mean']:.2f} ± {e['std']:.2f}"
                    extra = []
                    if e["improvement_pct"] is not None:
                        extra.append(f"{e['improvement_pct']:+.1f}%")
                    if e["p_value"] is not None:
                        extra.append(f"p={e['p_value']:.3f}")
                    if extra:
                        cell += f" ({', '.join(extra)})"
                    cells.append(cell)
                lines.append(f"| {label} | " + " | ".join(cells) + " |")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s [%(levelname)s] evaluate: %(message)s")
    ap = argparse.ArgumentParser(description="Module 12: evaluation suites and statistics")
    ap.add_argument("--suite", choices=list(SUITES) + ["all"], default="all")
    ap.add_argument("--quick", action="store_true", help="2 seeds, reduced factors, shorter runs")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--raw", type=str, default=None, help="Raw results CSV (default results/raw.csv, quick: results/raw_quick.csv)")
    ap.add_argument("--out-dir", type=str, default=None,
                    help="summary.json, summary.md and plots (default results/, quick: results/quick/)")
    ap.add_argument("--summary-only", action="store_true", help="Do not run simulations; summarise raw.csv")
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    suites = list(SUITES) if args.suite == "all" else [args.suite]
    plan = build_plan(cfg, suites, args.quick)
    workers = args.workers if args.workers is not None else int(cfg["evaluation"].get("workers", 1))

    def _abs(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else PROJECT_ROOT / q

    raw_path = _abs(args.raw or ("results/raw_quick.csv" if args.quick else "results/raw.csv"))
    out_dir = _abs(args.out_dir or ("results/quick" if args.quick else "results"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.summary_only:
        execute(plan, raw_path, workers, float(cfg["evaluation"].get("default_seconds_per_run", 30.0)))

    # Summarise all suites present in raw.csv (so running one suite keeps the others)
    all_specs = build_plan(cfg, SUITES, args.quick).specs
    rows = read_raw(raw_path)
    summary = summarise(rows, all_specs)
    summary["meta"]["quick"] = bool(args.quick)
    summary["meta"]["max_steps"] = plan.max_steps
    summary["meta"]["raw_file"] = str(raw_path.relative_to(PROJECT_ROOT)) if raw_path.is_relative_to(PROJECT_ROOT) else str(raw_path)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_markdown(summary, out_dir / "summary.md")
    logger.info("Wrote %s and %s", out_dir / "summary.json", out_dir / "summary.md")

    if not args.no_plots:
        from sim.analytics.plots import make_evaluation_plots

        for p in make_evaluation_plots(summary, out_dir, max_steps=plan.max_steps):
            logger.info("Plot: %s", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
