"""Which eco features help? Each one is added in turn on identical traffic.

Configurations: no eco cars; GLOSA; GLOSA + announced light changes; GLOSA + eco-routing; all three.
Every configuration uses the same scenario, seeds and departures. Whole-network results decide whether a
feature helps, so a gain for eco cars cannot hide a loss for other traffic. Differences use paired t-tests
over the seeds. Output (default results/eco_features/): raw.csv, summary.json and summary.md.

    python sim/analytics/compare_eco_features.py
    python sim/analytics/compare_eco_features.py --seeds 1 2 --max-steps 600   # short check
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# name -> (eco fraction, seconds a light change is announced, eco-routing)
CONFIGS = {
    "none": (0.0, 0.0, False),
    "glosa": (0.6, 0.0, False),
    "glosa+commit": (0.6, 8.0, False),
    "glosa+routing": (0.6, 0.0, True),
    "all": (0.6, 8.0, True),
}
ALL_CARS = [
    ("fuel_l_per_100km", "Fuel, all cars (L/100 km)"), ("co2_g_per_km", "CO₂, all cars (g/km)"),
    ("stops_per_vehicle", "Stops per car"), ("idle_s_per_vehicle", "Idle time per car (s)"),
    ("avg_waiting_time", "Waiting time (s)"), ("avg_travel_time", "Travel time (s)"),
    ("throughput_veh_per_hr", "Throughput (veh/h)"), ("collisions", "Collisions"),
]
GROUPS = [
    ("fuel_l_per_100km_eco", "Fuel, eco cars"), ("fuel_l_per_100km_non", "Fuel, regular cars"),
    ("stops_per_vehicle_eco", "Stops, eco cars"), ("stops_per_vehicle_non", "Stops, regular cars"),
    ("idle_s_per_vehicle_eco", "Idle s, eco cars"), ("idle_s_per_vehicle_non", "Idle s, regular cars"),
    ("eco_advised_vehicles", "Eco cars given speed advice"), ("eco_reroutes", "Eco route changes"),
]
KEYS = [k for k, _ in ALL_CARS + GROUPS]


# Run one configuration on one scenario and seed, in its own temporary folder.
def run_job(job: Dict[str, Any]) -> Dict[str, Any]:
    from sim.runner import run_simulation

    frac, commit, routing = CONFIGS[job["config"]]
    row: Dict[str, Any] = dict(job)
    with tempfile.TemporaryDirectory(prefix="ecofeat_") as tmp:
        try:
            res = run_simulation(
                scenario=job["scenario"], controller=job["controller"], seed=int(job["seed"]),
                params={"network": "grid2x2"}, run_id=f"ef_{os.getpid()}_{job['config']}_{job['seed']}",
                max_steps=job["max_steps"], metrics_csv_path=Path(tmp) / "m.csv", output_dir=Path(tmp),
                penetration=float(job["penetration"]), eco_fraction=frac, eco_select="data",
                eco_commit=commit, eco_routing=routing,
            )
            row.update({k: res.get(k) for k in KEYS})
        except Exception as e:  # keep going; the row records the failure
            row["error"] = repr(e)[:300]
    return row


# Mean over seeds, and the paired change against no eco cars and against plain GLOSA.
def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for ctrl in dict.fromkeys(r["controller"] for r in rows):
        out[ctrl] = {}
        for scen in dict.fromkeys(r["scenario"] for r in rows):
            def values(cfg: str, key: str) -> Dict[int, float]:
                return {int(r["seed"]): float(r[key]) for r in rows
                        if r["controller"] == ctrl and r["scenario"] == scen and r["config"] == cfg
                        and not r.get("error") and r.get(key) not in (None, "")}

            block: Dict[str, Any] = {}
            for cfg in CONFIGS:
                block[cfg] = {}
                for key in KEYS:
                    v = values(cfg, key)
                    if not v:
                        continue
                    m: Dict[str, Any] = {"mean": round(statistics.fmean(v.values()), 3)}
                    for base, tag in (("none", "vs_none"), ("glosa", "vs_glosa")):
                        b = values(base, key)
                        seeds = sorted(set(v) & set(b))
                        if cfg == base or len(seeds) < 2:
                            continue
                        mean_b = statistics.fmean(b[s] for s in seeds)
                        if mean_b:
                            m[f"{tag}_pct"] = round((statistics.fmean(v[s] for s in seeds) - mean_b) / mean_b * 100, 2)
                        diffs = [v[s] - b[s] for s in seeds]
                        if any(diffs):
                            m[f"{tag}_p"] = round(float(stats.ttest_rel([v[s] for s in seeds], [b[s] for s in seeds]).pvalue), 4)
                    block[cfg][key] = m
            out[ctrl][scen] = block
    return out


# Write the comparison as readable tables.
def write_markdown(summary: Dict[str, Any], meta: Dict[str, Any], path: Path) -> None:
    lines = ["# Eco features on identical traffic", "",
             f"Network grid2x2, seeds {meta['seeds']}, {meta['max_steps']} s, {meta['penetration'] * 100:.0f} % connected, "
             "60 % of connected cars are eco cars (chosen by data). Change is against no eco cars; * means p < 0.05.", ""]
    names = list(CONFIGS)
    for ctrl, by_scen in summary.items():
        for scen, block in by_scen.items():
            lines += [f"## `{ctrl}`, scenario `{scen}`", "", "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
            for key, label in ALL_CARS + GROUPS:
                cells = []
                for cfg in names:
                    m = block[cfg].get(key)
                    if not m:
                        cells.append("–")
                        continue
                    cell = f"{m['mean']:.2f}"
                    if "vs_none_pct" in m:
                        star = "*" if m.get("vs_none_p", 1) < 0.05 else ""
                        cell += f" ({m['vs_none_pct']:+.1f} %{star})"
                    cells.append(cell)
                lines.append(f"| {label} | " + " | ".join(cells) + " |")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# Run every controller, scenario, configuration and seed, then save the results.
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--controllers", nargs="+", default=["level_vanet", "dynamic_vanet"])
    ap.add_argument("--scenarios", nargs="+", default=["normal", "navigation"])
    ap.add_argument("--seeds", nargs="+", type=int, default=list(range(1, 11)))
    ap.add_argument("--penetration", type=float, default=0.5)
    ap.add_argument("--max-steps", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--out", default="results/eco_features")
    args = ap.parse_args(argv)

    out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    jobs = [{"controller": c, "scenario": s, "config": cfg, "seed": seed, "penetration": args.penetration,
             "max_steps": args.max_steps} for c in args.controllers for s in args.scenarios for cfg in CONFIGS for seed in args.seeds]
    print(f"{len(jobs)} runs on {args.workers} workers", file=sys.stderr)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(run_job, jobs))
    with open(out / "raw.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(jobs[0]) + KEYS + ["error"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    summary = summarise(rows)
    meta = {"seeds": args.seeds, "max_steps": args.max_steps, "penetration": args.penetration,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (out / "summary.json").write_text(json.dumps({"meta": meta, "results": summary}, indent=2), encoding="utf-8")
    write_markdown(summary, meta, out / "summary.md")
    errors = [r for r in rows if r.get("error")]
    print(f"Wrote {out / 'summary.md'}; {len(errors)} failed runs", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
