"""Which cars should be eco cars? Data-driven choice vs random choice vs no eco cars, same traffic.

Each configuration runs the same scenario and seeds (same vehicles and departures). The fair measure
is the whole network: every car's fuel, CO2, stops and travel time, so the comparison does not depend
on which cars were picked. Output (default results/eco_select/): raw.csv, summary.json, summary.md.

    python sim/analytics/compare_eco_select.py                 # normal traffic, fixed-time signals, 5 seeds
    python sim/analytics/compare_eco_select.py --fractions 0.3 --seeds 1 2

Eco advice (GLOSA) needs predictable signal timing, so the default signal controller is fixed-time.
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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

METRICS = [
    ("co2_g_per_km", "CO₂, all cars (g/km)"),
    ("fuel_l_per_100km", "Fuel, all cars (L/100 km)"),
    ("stops_per_vehicle", "Stops per vehicle, all cars"),
    ("avg_waiting_time", "Average waiting time (s)"),
    ("avg_travel_time", "Average travel time (s)"),
    ("eco_advised_vehicles", "Eco cars that got speed advice"),
    ("collisions", "Collisions"),
]


def run_job(job: Dict[str, Any]) -> Dict[str, Any]:
    from sim.runner import run_simulation

    row: Dict[str, Any] = dict(job)
    with tempfile.TemporaryDirectory(prefix="ecosel_") as tmp:
        try:
            res = run_simulation(
                scenario=job["scenario"], controller=job["controller"], seed=int(job["seed"]),
                params={"network": job["network"]}, run_id=f"es_{os.getpid()}_{job['config']}_{job['seed']}",
                max_steps=job["max_steps"], metrics_csv_path=Path(tmp) / "m.csv", output_dir=Path(tmp),
                penetration=float(job["penetration"]), eco_fraction=float(job["eco_fraction"]),
                eco_select=job["eco_select"],
            )
            row.update({k: res.get(k) for k, _ in METRICS})
        except Exception as e:
            row["error"] = repr(e)[:300]
    return row


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", default="normal")
    ap.add_argument("--controller", default="fixed")
    ap.add_argument("--network", default="grid2x2")
    ap.add_argument("--fractions", nargs="+", type=float, default=[0.3, 0.6])
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3, 4, 5])
    ap.add_argument("--penetration", type=float, default=1.0)
    ap.add_argument("--max-steps", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--out", default="results/eco_select")
    args = ap.parse_args(argv)

    out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    configs = [("none", 0.0, "random")]
    for f in args.fractions:
        configs += [(f"random@{f:g}", f, "random"), (f"data@{f:g}", f, "data")]
    jobs = [{"config": name, "eco_fraction": f, "eco_select": sel, "seed": s, "scenario": args.scenario,
             "controller": args.controller, "network": args.network, "penetration": args.penetration,
             "max_steps": args.max_steps}
            for (name, f, sel) in configs for s in args.seeds]
    print(f"{len(jobs)} runs on {args.workers} workers", file=sys.stderr)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(run_job, jobs))

    with open(out / "raw.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(jobs[0]) + [k for k, _ in METRICS] + ["error"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    summary: Dict[str, Any] = {}
    for name, _, _ in configs:
        rs = [r for r in rows if r["config"] == name and not r.get("error")]
        summary[name] = {}
        for k, _ in METRICS:
            vals = [float(r[k]) for r in rs if r.get(k) not in (None, "")]
            if vals:
                summary[name][k] = {"mean": round(statistics.fmean(vals), 3),
                                    "sd": round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0}
    base = summary.get("none", {})
    for name, stats in summary.items():
        for k, s in stats.items():
            b = base.get(k, {}).get("mean")
            if name != "none" and b:
                s["vs_none_pct"] = round((s["mean"] - b) / b * 100.0, 2)
    meta = {"scenario": args.scenario, "controller": args.controller, "network": args.network, "seeds": args.seeds,
            "penetration": args.penetration, "max_steps": args.max_steps,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (out / "summary.json").write_text(json.dumps({"meta": meta, "configs": summary}, indent=2), encoding="utf-8")

    names = [n for n, _, _ in configs]
    lines = ["# Eco cars chosen by data vs at random", "",
             f"Scenario {args.scenario}, {args.controller} signals, {args.network}, seeds {args.seeds}, "
             f"{args.max_steps} s, penetration {args.penetration}. Whole-network means; change against no eco cars.", "",
             "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for k, label in METRICS:
        cells = []
        for n in names:
            s = summary[n].get(k)
            cells.append("–" if not s else f"{s['mean']:.2f}" + (f" ({s['vs_none_pct']:+.1f} %)" if "vs_none_pct" in s else ""))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out / 'summary.md'}", file=sys.stderr)
    return 1 if any(r.get("error") for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
