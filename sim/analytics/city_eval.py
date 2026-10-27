"""Traffic and route metrics of the city network (scenario "city"), several seeds per configuration.

Stored apart from the research results (results/city/), which use grid2x2 and are unchanged.

Configurations: signal control fixed / adaptive / adaptive_vanet (50 % penetration), without navigation
and with A* navigation. For each: average waiting, travel time and time loss, throughput, longest
queue, collisions, fuel and CO2, and for navigation the reroutes and the navigating cars' trip time.
Also, per vehicle category, how many trips used each road class (main, secondary, collector, local),
from SUMO's own route output, which shows that trucks and buses never used a local sub-road.

Usage: python sim/analytics/city_eval.py [--seeds 5] [--steps 1200] [--workers 4]
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import statistics
import sys
import tempfile
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # run as a script: make the sim package importable
OUT_DIR = ROOT / "results" / "city"
CONFIGS = [
    {"name": "fixed", "controller": "fixed", "nav": "none"},
    {"name": "adaptive", "controller": "adaptive", "nav": "none"},
    {"name": "adaptive_vanet", "controller": "adaptive_vanet", "nav": "none"},
    {"name": "adaptive_vanet + A*", "controller": "adaptive_vanet", "nav": "astar"},
]
METRICS = ("avg_waiting_time", "avg_travel_time", "avg_time_loss", "throughput_veh_per_hr", "max_queue", "collisions",
           "co2_g_per_km", "fuel_l_per_100km", "stops_per_vehicle", "reroute_count", "nav_avg_travel_time")


def _run(task: Dict[str, Any]) -> Dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from sim.runner import run_simulation

    with tempfile.TemporaryDirectory() as tmp:
        r = run_simulation("city", task["controller"], seed=task["seed"], max_steps=task["steps"], nav=task["nav"],
                           penetration=0.5, output_dir=Path(tmp))
    return {"config": task["name"], "seed": task["seed"], **{k: r.get(k) for k in METRICS}}


def road_class_use(seed: int = 1, steps: int = 1200) -> Dict[str, Dict[str, int]]:
    """Trips per vehicle category that used each road class (SUMO's vehroute output, plain run)."""
    import subprocess

    import sumolib

    from sim.scenarios.generate_routes import generate_routes, load_scenario_config

    net_path = ROOT / "sim" / "sumo" / "xml" / "networks" / "grid2x2_city.net.xml"
    net = sumolib.net.readNet(str(net_path))
    with tempfile.TemporaryDirectory() as tmp:
        rou = Path(tmp) / "r.rou.xml"
        out = Path(tmp) / "vr.xml"
        generate_routes(load_scenario_config(ROOT / "sim" / "scenarios" / "city.yaml"), rou, override_seed=seed,
                        network_override="grid2x2_city")
        subprocess.run([sumolib.checkBinary("sumo"), "-n", str(net_path), "-r", str(rou), "--end", str(steps),
                        "--vehroute-output", str(out), "--no-step-log", "true", "--time-to-teleport", "-1"],
                       check=True, capture_output=True)
        use: Dict[str, Dict[str, int]] = {}
        for veh in sumolib.xml.parse(str(out), "vehicle"):
            kind = veh.type
            edges = (veh.route[0].edges if veh.route else "").split()
            classes = {net.getEdge(e).getType() for e in edges if net.hasEdge(e)}
            row = use.setdefault(kind, {"trips": 0})
            row["trips"] += 1
            for c in classes:
                row[c] = row.get(c, 0) + 1
    return use


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--steps", type=int, default=1200)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    tasks = [{**c, "seed": s, "steps": a.steps} for c in CONFIGS for s in range(1, a.seeds + 1)]
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        rows: List[Dict[str, Any]] = list(ex.map(_run, tasks))
    summary: Dict[str, Any] = {"scenario": "city", "network": "grid2x2_city", "seeds": a.seeds, "steps": a.steps,
                               "penetration": 0.5, "configs": {}}
    for c in CONFIGS:
        rs = [r for r in rows if r["config"] == c["name"]]
        stats = {}
        for m in METRICS:
            vals = [r[m] for r in rs if isinstance(r.get(m), (int, float))]
            if vals:
                stats[m] = {"mean": round(statistics.mean(vals), 3),
                            "sd": round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0}
        summary["configs"][c["name"]] = stats
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "runs.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")  # saved first
    summary["road_class_use"] = road_class_use(seed=1, steps=a.steps)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    lines = ["# City network results", "", f"{a.seeds} seeds × {a.steps} s, penetration 50 %, mixed vehicles.", "",
             "| Configuration | Waiting (s) | Travel time (s) | Throughput (veh/h) | Max queue | Collisions | CO₂ (g/km) | Reroutes |",
             "|---|---|---|---|---|---|---|---|"]
    for name, st in summary["configs"].items():
        g = lambda m, d=1: f"{st[m]['mean']:.{d}f}" if m in st else "–"  # noqa: E731
        lines.append(f"| {name} | {g('avg_waiting_time')} | {g('avg_travel_time')} | {g('throughput_veh_per_hr', 0)} | "
                     f"{g('max_queue', 1)} | {g('collisions', 0)} | {g('co2_g_per_km', 0)} | {g('reroute_count', 0)} |")
    lines += ["", "Trips per vehicle category using each road class (seed 1):", "",
              "| Category | Trips | Main | Secondary | Collector | Local |", "|---|---|---|---|---|---|"]
    for kind, u in sorted(summary["road_class_use"].items()):
        lines.append(f"| {kind} | {u['trips']} | {u.get('main', 0)} | {u.get('secondary', 0)} | {u.get('collector', 0)} | "
                     f"{u.get('local', 0)} |")
    (OUT_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
