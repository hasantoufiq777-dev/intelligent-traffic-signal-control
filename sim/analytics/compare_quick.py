"""Quick comparison script benchmarking Fixed vs Adaptive controllers.

Runs 5 seeds across 'normal' and 'congested' scenarios, reporting mean ± std
for average waiting time and throughput.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Dict, List
import numpy as np

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sim.runner import run_simulation


def run_benchmark(
    scenarios: List[str],
    controllers: List[str],
    seeds: List[int],
    max_steps: int = 400,
) -> Dict[str, Dict[str, Dict[str, List[float]]]]:
    """Execute simulation matrix and collect metrics."""
    data: Dict[str, Dict[str, Dict[str, List[float]]]] = {
        sc: {
            ctrl: {"waiting_time": [], "throughput": []}
            for ctrl in controllers
        }
        for sc in scenarios
    }

    total_runs = len(scenarios) * len(controllers) * len(seeds)
    run_idx = 0

    print(f"Starting benchmark: {total_runs} total simulation runs (max_steps={max_steps})...\n")

    for sc in scenarios:
        for ctrl in controllers:
            for s in seeds:
                run_idx += 1
                sys.stderr.write(f"[{run_idx}/{total_runs}] Running scenario={sc}, controller={ctrl}, seed={s}...\n")
                sys.stderr.flush()

                res = run_simulation(
                    scenario=sc,
                    controller=ctrl,
                    seed=s,
                    max_steps=max_steps,
                    gui=False,
                    live=False,
                )

                data[sc][ctrl]["waiting_time"].append(res["avg_waiting_time"])
                data[sc][ctrl]["throughput"].append(res["throughput_veh_per_hr"])

    return data


def print_comparison_table(
    data: Dict[str, Dict[str, Dict[str, List[float]]]],
    scenarios: List[str],
) -> None:
    """Print markdown formatted summary table."""
    print("\n" + "=" * 80)
    print("QUICK CONTROLLER COMPARISON BENCHMARK RESULTS (5 SEEDS)")
    print("=" * 80)

    for sc in scenarios:
        fixed_waits = data[sc]["fixed"]["waiting_time"]
        adapt_waits = data[sc]["adaptive"]["waiting_time"]

        fixed_tps = data[sc]["fixed"]["throughput"]
        adapt_tps = data[sc]["adaptive"]["throughput"]

        mean_fixed_wait = np.mean(fixed_waits)
        std_fixed_wait = np.std(fixed_waits)

        mean_adapt_wait = np.mean(adapt_waits)
        std_adapt_wait = np.std(adapt_waits)

        mean_fixed_tp = np.mean(fixed_tps)
        std_fixed_tp = np.std(fixed_tps)

        mean_adapt_tp = np.mean(adapt_tps)
        std_adapt_tp = np.std(adapt_tps)

        wait_impr = (
            ((mean_fixed_wait - mean_adapt_wait) / mean_fixed_wait) * 100.0
            if mean_fixed_wait > 0
            else 0.0
        )
        tp_impr = (
            ((mean_adapt_tp - mean_fixed_tp) / mean_fixed_tp) * 100.0
            if mean_fixed_tp > 0
            else 0.0
        )

        print(f"\n### Scenario: {sc.upper()}")
        print("| Controller | Avg Waiting Time (s) | Throughput (veh/hr) |")
        print("|------------|----------------------|---------------------|")
        print(f"| Fixed-Time | {mean_fixed_wait:6.2f} ± {std_fixed_wait:4.2f}    | {mean_fixed_tp:7.1f} ± {std_fixed_tp:5.1f}   |")
        print(f"| Adaptive   | {mean_adapt_wait:6.2f} ± {std_adapt_wait:4.2f}    | {mean_adapt_tp:7.1f} ± {std_adapt_tp:5.1f}   |")
        print(f"| Difference | -{wait_impr:5.2f}% delay       | +{tp_impr:5.2f}% throughput  |")

    print("\n" + "=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="Quick Controller Benchmark Comparison")
    parser.add_argument("--steps", type=int, default=400, help="Simulation duration in steps per run")
    parser.add_argument("--seeds", type=int, default=5, help="Number of random seeds (1..N)")
    args = parser.parse_args()

    scenarios = ["normal", "congested"]
    controllers = ["fixed", "adaptive"]
    seeds = list(range(1, args.seeds + 1))

    results = run_benchmark(scenarios, controllers, seeds, max_steps=args.steps)
    print_comparison_table(results, scenarios)


if __name__ == "__main__":
    main()
