"""VANET sensing and communication sensitivity experiment suite.

Executes comprehensive evaluation of adaptive signal control under varying
OBU penetration rates and wireless channel loss rates on the 2x2 grid network.
Compares:
- Fixed-time baseline
- Oracle adaptive controller (ground truth sensing)
- VANET-sensed adaptive controller (adaptive_vanet) across:
  penetration in {0.2, 0.5, 0.8, 1.0} x loss in {0.0, 0.1, 0.3} over 5 seeds.

Outputs:
- results/vanet_experiments.csv
- results/plot_waiting_time_vs_penetration.png
- results/plot_queue_mae_vs_penetration.png
- results/plot_pdr_vs_distance.png
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from sim.runner import run_simulation
from sim.vanet.channel import WirelessChannel

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] vanet_experiments: %(message)s",
)
logger = logging.getLogger("vanet_experiments")


def run_vanet_experiments(
    scenario: str = "congested",
    seeds: Optional[List[int]] = None,
    penetrations: Optional[List[float]] = None,
    losses: Optional[List[float]] = None,
    max_steps: Optional[int] = None,
    output_csv: Optional[Path] = None,
    plots_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Execute the multi-seed VANET sensing experiment suite."""
    if seeds is None:
        seeds = [1, 2, 3, 4, 5]
    if penetrations is None:
        penetrations = [0.2, 0.5, 0.8, 1.0]
    if losses is None:
        losses = [0.0, 0.1, 0.3]

    if output_csv is None:
        output_csv = PROJECT_ROOT / "results" / "vanet_experiments.csv"
    if plots_dir is None:
        plots_dir = PROJECT_ROOT / "results"

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, Any]] = []

    # 1. Run Fixed Baseline across seeds
    for s in seeds:
        logger.info("Running baseline 'fixed' seed=%d...", s)
        res = run_simulation(
            scenario=scenario,
            controller="fixed",
            seed=s,
            max_steps=max_steps,
            gui=False,
            live=False,
        )
        records.append({
            "scenario": scenario,
            "controller": "fixed",
            "seed": s,
            "penetration": 1.0,
            "loss": 0.0,
            "avg_waiting_time": res["avg_waiting_time"],
            "avg_time_loss": res["avg_time_loss"],
            "avg_travel_time": res["avg_travel_time"],
            "max_queue": res["max_queue"],
            "throughput_veh_per_hr": res["throughput_veh_per_hr"],
            "pdr": None,
            "avg_latency_ms": None,
            "queue_estimate_mae": None,
        })

    # 2. Run Oracle Adaptive Baseline across seeds
    for s in seeds:
        logger.info("Running baseline 'adaptive' (oracle) seed=%d...", s)
        res = run_simulation(
            scenario=scenario,
            controller="adaptive",
            seed=s,
            max_steps=max_steps,
            gui=False,
            live=False,
        )
        records.append({
            "scenario": scenario,
            "controller": "adaptive",
            "seed": s,
            "penetration": 1.0,
            "loss": 0.0,
            "avg_waiting_time": res["avg_waiting_time"],
            "avg_time_loss": res["avg_time_loss"],
            "avg_travel_time": res["avg_travel_time"],
            "max_queue": res["max_queue"],
            "throughput_veh_per_hr": res["throughput_veh_per_hr"],
            "pdr": None,
            "avg_latency_ms": None,
            "queue_estimate_mae": 0.0,
        })

    # 3. Run VANET Adaptive across penetrations and loss levels
    for p in penetrations:
        for loss in losses:
            for s in seeds:
                logger.info(
                    "Running 'adaptive_vanet' p=%.2f, loss=%.2f, seed=%d...",
                    p,
                    loss,
                    s,
                )
                res = run_simulation(
                    scenario=scenario,
                    controller="adaptive_vanet",
                    seed=s,
                    penetration=p,
                    loss=loss,
                    max_steps=max_steps,
                    gui=False,
                    live=False,
                )
                records.append({
                    "scenario": scenario,
                    "controller": "adaptive_vanet",
                    "seed": s,
                    "penetration": p,
                    "loss": loss,
                    "avg_waiting_time": res["avg_waiting_time"],
                    "avg_time_loss": res["avg_time_loss"],
                    "avg_travel_time": res["avg_travel_time"],
                    "max_queue": res["max_queue"],
                    "throughput_veh_per_hr": res["throughput_veh_per_hr"],
                    "pdr": res["pdr"],
                    "avg_latency_ms": res["avg_latency_ms"],
                    "queue_estimate_mae": res["queue_estimate_mae"],
                })

    # Save CSV
    fieldnames = [
        "scenario",
        "controller",
        "seed",
        "penetration",
        "loss",
        "avg_waiting_time",
        "avg_time_loss",
        "avg_travel_time",
        "max_queue",
        "throughput_veh_per_hr",
        "pdr",
        "avg_latency_ms",
        "queue_estimate_mae",
    ]
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    logger.info("Saved experiment results to %s", output_csv)

    # Generate Plots
    generate_plots(records, plots_dir)

    return records


def generate_plots(records: List[Dict[str, Any]], plots_dir: Path) -> None:
    """Generate analytical visualization plots for the VANET evaluation."""
    plots_dir.mkdir(parents=True, exist_ok=True)

    # Baseline means
    fixed_waits = [r["avg_waiting_time"] for r in records if r["controller"] == "fixed"]
    fixed_wait_mean = float(np.mean(fixed_waits)) if fixed_waits else 0.0

    oracle_waits = [r["avg_waiting_time"] for r in records if r["controller"] == "adaptive"]
    oracle_wait_mean = float(np.mean(oracle_waits)) if oracle_waits else 0.0

    # Aggregate adaptive_vanet by penetration and loss
    vanet_records = [r for r in records if r["controller"] == "adaptive_vanet"]
    unique_losses = sorted(list(set(r["loss"] for r in vanet_records)))
    unique_pens = sorted(list(set(r["penetration"] for r in vanet_records)))

    # --- Plot 1: Average Waiting Time vs Penetration ---
    plt.figure(figsize=(8, 5))
    plt.axhline(
        fixed_wait_mean,
        color="crimson",
        linestyle="--",
        label=f"Fixed-Time ({fixed_wait_mean:.1f}s)",
    )
    plt.axhline(
        oracle_wait_mean,
        color="forestgreen",
        linestyle=":",
        label=f"Oracle Adaptive ({oracle_wait_mean:.1f}s)",
    )

    markers = ["o", "s", "^", "D"]
    for idx, loss_val in enumerate(unique_losses):
        means = []
        for p in unique_pens:
            vals = [
                r["avg_waiting_time"]
                for r in vanet_records
                if r["loss"] == loss_val and r["penetration"] == p
            ]
            means.append(float(np.mean(vals)) if vals else 0.0)

        plt.plot(
            unique_pens,
            means,
            marker=markers[idx % len(markers)],
            linewidth=2,
            label=f"adaptive_vanet (loss={loss_val})",
        )

    plt.xlabel("OBU Penetration Rate", fontsize=11)
    plt.ylabel("Average Waiting Time (s)", fontsize=11)
    plt.title("Signal Performance vs. OBU Penetration (Congested 2x2 Grid)", fontsize=12)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend()
    plt.tight_layout()
    p1_path = plots_dir / "plot_waiting_time_vs_penetration.png"
    plt.savefig(p1_path, dpi=300)
    plt.close()

    # --- Plot 2: Queue Estimate MAE vs Penetration ---
    plt.figure(figsize=(8, 5))
    for idx, loss_val in enumerate(unique_losses):
        mae_means = []
        for p in unique_pens:
            vals = [
                r["queue_estimate_mae"]
                for r in vanet_records
                if r["loss"] == loss_val and r["penetration"] == p and r["queue_estimate_mae"] is not None
            ]
            mae_means.append(float(np.mean(vals)) if vals else 0.0)

        plt.plot(
            unique_pens,
            mae_means,
            marker=markers[idx % len(markers)],
            linewidth=2,
            label=f"Loss = {loss_val}",
        )

    plt.xlabel("OBU Penetration Rate", fontsize=11)
    plt.ylabel("Queue Estimate MAE (Vehicles)", fontsize=11)
    plt.title("Approach Queue Estimation Error vs. Penetration", fontsize=12)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend()
    plt.tight_layout()
    p2_path = plots_dir / "plot_queue_mae_vs_penetration.png"
    plt.savefig(p2_path, dpi=300)
    plt.close()

    # --- Plot 3: Packet Delivery Ratio (PDR) vs Distance ---
    distances = np.linspace(0, 350, 100)
    plt.figure(figsize=(8, 5))
    for loss_val in [0.0, 0.1, 0.3]:
        ch = WirelessChannel(range_m=300.0, base_loss=loss_val, max_loss=min(1.0, loss_val + 0.2))
        pdr_curve = [1.0 - ch.calculate_loss_probability(float(d)) for d in distances]
        plt.plot(distances, pdr_curve, linewidth=2, label=f"Base Loss = {loss_val}")

    plt.axvline(300.0, color="gray", linestyle="--", label="RF Range Boundary (300m)")
    plt.xlabel("Distance to RSU (m)", fontsize=11)
    plt.ylabel("Packet Delivery Ratio (PDR)", fontsize=11)
    plt.title("Quadratic Channel Path Loss Model", fontsize=12)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend()
    plt.tight_layout()
    p3_path = plots_dir / "plot_pdr_vs_distance.png"
    plt.savefig(p3_path, dpi=300)
    plt.close()

    logger.info("Saved plots: %s, %s, %s", p1_path, p2_path, p3_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="VANET sensing sensitivity experiments")
    parser.add_argument("--scenario", type=str, default="congested", help="Scenario name")
    parser.add_argument("--quick", action="store_true", help="Run quick 1-seed test")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum simulation steps")
    args = parser.parse_args()

    if args.quick:
        seeds = [1]
        steps = args.max_steps or 100
        penetrations = [0.5, 1.0]
        losses = [0.0, 0.2]
    else:
        seeds = [1, 2, 3, 4, 5]
        steps = args.max_steps
        penetrations = [0.2, 0.5, 0.8, 1.0]
        losses = [0.0, 0.1, 0.3]

    run_vanet_experiments(
        scenario=args.scenario,
        seeds=seeds,
        penetrations=penetrations,
        losses=losses,
        max_steps=steps,
    )


if __name__ == "__main__":
    main()
