"""Fixed-time vs dynamic congestion-aware signal control, on the same traffic.

Every controller runs the same scenario, network and seeds (same vehicles, same departure times),
so the differences come from the signal control alone. Output (default results/dynamic/):
raw.csv (one row per run), summary.json and summary.md (mean ± standard deviation over the seeds,
and the change against fixed-time).

    python sim/analytics/compare_dynamic.py                      # normal + congested, 5 seeds
    python sim/analytics/compare_dynamic.py --seeds 1 2 --quick  # short check

The research evaluation (sim/analytics/evaluate.py, results/summary.json) is not changed by this.
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

# (name in the result, label, lower is better)
METRICS = [
    ("avg_waiting_time", "Average waiting time per vehicle (s)", True),
    ("avg_queue", "Average queue per junction (vehicles)", True),
    ("trips_completed", "Vehicles that completed their trip", False),
    ("avg_travel_time", "Average travel time (s)", True),
    ("queue_veh_h", "Total congestion: vehicle-hours queued at signals", True),
    ("stops_per_vehicle", "Stops per vehicle", True),
    ("throughput_veh_per_hr", "Throughput (vehicles per hour)", False),
    ("avg_time_loss", "Average time loss (s)", True),
    ("max_queue", "Longest queue on one approach (vehicles)", True),
    ("collisions", "Collisions", True),
]
LABELS = {
    "fixed": "Fixed-time (30 s greens)",
    "adaptive": "Adaptive (queue threshold, existing)",
    "dynamic": "Dynamic congestion-aware",
    "dynamic_vanet": "Dynamic congestion-aware, VANET sensing",
}


def run_job(job: Dict[str, Any]) -> Dict[str, Any]:
    from sim.runner import run_simulation

    t0 = time.time()
    row: Dict[str, Any] = {k: job[k] for k in ("scenario", "controller", "seed", "penetration", "network")}
    with tempfile.TemporaryDirectory(prefix="dyn_") as tmp:
        try:
            res = run_simulation(
                scenario=job["scenario"], controller=job["controller"], seed=int(job["seed"]),
                params={"network": job["network"]}, run_id=f"cmp_{os.getpid()}_{job['controller']}_{job['seed']}",
                max_steps=job["max_steps"], metrics_csv_path=Path(tmp) / "m.csv", output_dir=Path(tmp),
                penetration=float(job["penetration"]),
            )
            row.update({k: res.get(k) for k, _, _ in METRICS})
        except Exception as e:  # keep going; the row records the failure
            row["error"] = repr(e)[:300]
    row["wall_s"] = round(time.time() - t0, 1)
    return row


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for scen in dict.fromkeys(r["scenario"] for r in rows):
        by_ctrl: Dict[str, Dict[str, Any]] = {}
        for ctrl in dict.fromkeys(r["controller"] for r in rows if r["scenario"] == scen):
            rs = [r for r in rows if r["scenario"] == scen and r["controller"] == ctrl and not r.get("error")]
            stats: Dict[str, Any] = {"n": len(rs)}
            for k, _, _ in METRICS:
                vals = [float(r[k]) for r in rs if r.get(k) not in (None, "")]
                if vals:
                    stats[k] = {"mean": round(statistics.fmean(vals), 3),
                                "sd": round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0}
            by_ctrl[ctrl] = stats
        base = by_ctrl.get("fixed", {})
        for ctrl, stats in by_ctrl.items():
            for k, _, _ in METRICS:
                b, v = base.get(k, {}).get("mean"), stats.get(k, {}).get("mean")
                if ctrl != "fixed" and b not in (None, 0) and v is not None:
                    stats[k]["vs_fixed_pct"] = round((v - b) / b * 100.0, 1)
        out[scen] = by_ctrl
    return out


def write_markdown(summary: Dict[str, Any], meta: Dict[str, Any], path: Path) -> None:
    lines = ["# Fixed-time vs dynamic signal control", "",
             f"Network {meta['network']}, seeds {meta['seeds']}, {meta['max_steps'] or 'full'} s per run, "
             f"penetration {meta['penetration']} (VANET sensing). Mean ± standard deviation over the seeds; "
             "the change is against fixed-time on the same traffic.", ""]
    for scen, by_ctrl in summary.items():
        ctrls = list(by_ctrl)
        lines += [f"## Scenario `{scen}`", "",
                  "| Metric | " + " | ".join(LABELS.get(c, c) for c in ctrls) + " |",
                  "|---|" + "---|" * len(ctrls)]
        for k, label, _ in METRICS:
            cells = []
            for c in ctrls:
                s = by_ctrl[c].get(k)
                if not s:
                    cells.append("–")
                    continue
                cell = f"{s['mean']:.2f} ± {s['sd']:.2f}"
                if "vs_fixed_pct" in s:
                    cell += f" ({s['vs_fixed_pct']:+.1f} %)"
                cells.append(cell)
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenarios", nargs="+", default=["normal", "congested"])
    ap.add_argument("--controllers", nargs="+", default=["fixed", "adaptive", "dynamic", "dynamic_vanet"])
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3, 4, 5])
    ap.add_argument("--network", default="grid2x2")
    ap.add_argument("--penetration", type=float, default=0.5, help="share of equipped cars (VANET sensing)")
    ap.add_argument("--max-steps", type=int, default=1000)
    ap.add_argument("--quick", action="store_true", help="600 s runs")
    ap.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--out", default="results/dynamic")
    args = ap.parse_args(argv)

    max_steps = 600 if args.quick else args.max_steps
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    jobs = [{"scenario": s, "controller": c, "seed": seed, "network": args.network, "max_steps": max_steps,
             "penetration": args.penetration}
            for s in args.scenarios for c in args.controllers for seed in args.seeds]
    print(f"{len(jobs)} runs on {args.workers} workers", file=sys.stderr)
    rows: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for row in ex.map(run_job, jobs):
            rows.append(row)
            print(f"  {row['scenario']} {row['controller']} seed {row['seed']}: wait {row.get('avg_waiting_time')} s"
                  f"{' ERROR ' + row['error'] if row.get('error') else ''}", file=sys.stderr)

    fields = ["scenario", "controller", "seed", "penetration", "network"] + [k for k, _, _ in METRICS] + ["error", "wall_s"]
    with open(out / "raw.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    summary = summarise(rows)
    meta = {"network": args.network, "seeds": args.seeds, "max_steps": max_steps, "penetration": args.penetration,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (out / "summary.json").write_text(json.dumps({"meta": meta, "labels": LABELS, "scenarios": summary}, indent=2),
                                      encoding="utf-8")
    write_markdown(summary, meta, out / "summary.md")
    print(f"Wrote {out / 'summary.md'}", file=sys.stderr)
    return 1 if any(r.get("error") for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
