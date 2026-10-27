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


def run_vanet_preemption_experiments(
    scenario: str = "emergency",
    seeds: Optional[List[int]] = None,
    max_steps: Optional[int] = 200,
    output_csv: Optional[Path] = None,
    plots_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Execute sensitivity experiments for Module 8 (VANET-based emergency preemption)."""
    if seeds is None:
        seeds = [1, 2, 3, 4, 5]

    if output_csv is None:
        output_csv = PROJECT_ROOT / "results" / "vanet_preemption_experiments.csv"
    if plots_dir is None:
        plots_dir = PROJECT_ROOT / "results"

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, Any]] = []

    # 1. Reference Baselines across seeds
    for s in seeds:
        logger.info("Running baseline 'adaptive' (no preemption) seed=%d...", s)
        res_adapt = run_simulation(
            scenario=scenario,
            controller="adaptive",
            seed=s,
            max_steps=max_steps,
            params={"network": "grid2x2"},
            gui=False,
            live=False,
        )
        records.append({
            "experiment": "baseline",
            "seed": s,
            "controller": "adaptive",
            "loss": 0.0,
            "range": 300.0,
            "relay": "off",
            "alert_interval": 0.5,
            "ev_travel_time": res_adapt["ev_travel_time"],
            "avg_waiting_time": res_adapt["avg_waiting_time"],
            "pdr": None,
            "avg_latency_ms": None,
            "ev_alert_delivery_time": None,
            "avg_hops": None,
        })

        logger.info("Running baseline 'adaptive_ev' (oracle preemption) seed=%d...", s)
        res_oracle = run_simulation(
            scenario=scenario,
            controller="adaptive_ev",
            seed=s,
            max_steps=max_steps,
            params={"network": "grid2x2"},
            gui=False,
            live=False,
        )
        records.append({
            "experiment": "oracle",
            "seed": s,
            "controller": "adaptive_ev",
            "loss": 0.0,
            "range": 300.0,
            "relay": "off",
            "alert_interval": 0.5,
            "ev_travel_time": res_oracle["ev_travel_time"],
            "avg_waiting_time": res_oracle["avg_waiting_time"],
            "pdr": None,
            "avg_latency_ms": None,
            "ev_alert_delivery_time": None,
            "avg_hops": None,
        })

    # 2. Sweep 1: EV Travel Time vs Loss in {0.0, 0.1, 0.3, 0.5}
    loss_levels = [0.0, 0.1, 0.3, 0.5]
    for loss_val in loss_levels:
        for s in seeds:
            logger.info("Running Sweep 1 (loss=%s) seed=%d...", loss_val, s)
            res = run_simulation(
                scenario=scenario,
                controller="adaptive_ev_vanet",
                seed=s,
                loss=loss_val,
                range_m=300.0,
                relay="off",
                max_steps=max_steps,
                params={"network": "grid2x2"},
                gui=False,
                live=False,
            )
            records.append({
                "experiment": "sweep_loss",
                "seed": s,
                "controller": "adaptive_ev_vanet",
                "loss": loss_val,
                "range": 300.0,
                "relay": "off",
                "alert_interval": 0.5,
                "ev_travel_time": res["ev_travel_time"],
                "avg_waiting_time": res["avg_waiting_time"],
                "pdr": res.get("pdr"),
                "avg_latency_ms": res.get("avg_latency_ms"),
                "ev_alert_delivery_time": res.get("ev_alert_delivery_time"),
                "avg_hops": res.get("avg_hops"),
            })

    # 3. Sweep 2: EV Travel Time vs Range in {100, 200, 300, 500} x Relay {off, on}
    range_levels = [100.0, 200.0, 300.0, 500.0]
    for rel_mode in ["off", "on"]:
        for rng_val in range_levels:
            for s in seeds:
                logger.info("Running Sweep 2 (range=%s, relay=%s) seed=%d...", rng_val, rel_mode, s)
                res = run_simulation(
                    scenario=scenario,
                    controller="adaptive_ev_vanet",
                    seed=s,
                    loss=0.1,
                    range_m=rng_val,
                    relay=rel_mode,
                    max_steps=max_steps,
                    params={"network": "grid2x2"},
                    gui=False,
                    live=False,
                )
                records.append({
                    "experiment": "sweep_range_relay",
                    "seed": s,
                    "controller": "adaptive_ev_vanet",
                    "loss": 0.1,
                    "range": rng_val,
                    "relay": rel_mode,
                    "alert_interval": 0.5,
                    "ev_travel_time": res["ev_travel_time"],
                    "avg_waiting_time": res["avg_waiting_time"],
                    "pdr": res.get("pdr"),
                    "avg_latency_ms": res.get("avg_latency_ms"),
                    "ev_alert_delivery_time": res.get("ev_alert_delivery_time"),
                    "avg_hops": res.get("avg_hops"),
                })

    # 4. Sweep 3: EV Travel Time vs Alert Interval in {0.25, 0.5, 1.0, 2.0}
    interval_levels = [0.25, 0.5, 1.0, 2.0]
    for interval_val in interval_levels:
        for s in seeds:
            logger.info("Running Sweep 3 (interval=%s) seed=%d...", interval_val, s)
            res = run_simulation(
                scenario=scenario,
                controller="adaptive_ev_vanet",
                seed=s,
                loss=0.1,
                range_m=300.0,
                relay="off",
                max_steps=max_steps,
                params={"network": "grid2x2", "vanet": {"alert_interval": interval_val}},
                gui=False,
                live=False,
            )
            records.append({
                "experiment": "sweep_interval",
                "seed": s,
                "controller": "adaptive_ev_vanet",
                "loss": 0.1,
                "range": 300.0,
                "relay": "off",
                "alert_interval": interval_val,
                "ev_travel_time": res["ev_travel_time"],
                "avg_waiting_time": res["avg_waiting_time"],
                "pdr": res.get("pdr"),
                "avg_latency_ms": res.get("avg_latency_ms"),
                "ev_alert_delivery_time": res.get("ev_alert_delivery_time"),
                "avg_hops": res.get("avg_hops"),
            })

    # Write records to CSV
    csv_fields = [
        "experiment", "seed", "controller", "loss", "range", "relay",
        "alert_interval", "ev_travel_time", "avg_waiting_time", "pdr",
        "avg_latency_ms", "ev_alert_delivery_time", "avg_hops"
    ]
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(records)

    logger.info("Wrote %d preemption experiment records to %s", len(records), output_csv)

    # 5. Generate Plots
    _plot_preemption_results(records, plots_dir)

    return records


def _plot_preemption_results(records: List[Dict[str, Any]], plots_dir: Path) -> None:
    """Generate the 3 required sensitivity plots for Module 8."""
    # Compute baseline reference lines
    adapt_tt = [r["ev_travel_time"] for r in records if r["controller"] == "adaptive"]
    oracle_tt = [r["ev_travel_time"] for r in records if r["controller"] == "adaptive_ev"]
    mean_adapt = float(np.mean(adapt_tt)) if adapt_tt else 60.0
    mean_oracle = float(np.mean(oracle_tt)) if oracle_tt else 41.0

    # --- Plot 1: EV Travel Time vs Loss ---
    loss_levels = [0.0, 0.1, 0.3, 0.5]
    loss_means = []
    loss_stds = []
    for l_val in loss_levels:
        vals = [
            r["ev_travel_time"]
            for r in records
            if r["experiment"] == "sweep_loss" and r["loss"] == l_val
        ]
        loss_means.append(float(np.mean(vals)) if vals else 0.0)
        loss_stds.append(float(np.std(vals)) if vals else 0.0)

    plt.figure(figsize=(8, 5))
    plt.axhline(mean_adapt, color="red", linestyle="--", label=f"Adaptive (No Preemption: {mean_adapt:.1f}s)")
    plt.axhline(mean_oracle, color="green", linestyle="--", label=f"Oracle Preemption ({mean_oracle:.1f}s)")
    plt.errorbar(loss_levels, loss_means, yerr=loss_stds, marker="o", color="blue", linewidth=2, capsize=4, label="VANET Preemption (adaptive_ev_vanet)")
    plt.xlabel("Channel Base Loss Rate", fontsize=11)
    plt.ylabel("Emergency Vehicle Travel Time (s)", fontsize=11)
    plt.title("EV Travel Time vs. Wireless Channel Loss", fontsize=12)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend()
    plt.tight_layout()
    p1 = plots_dir / "plot_ev_travel_time_vs_loss.png"
    plt.savefig(p1, dpi=300)
    plt.close()

    # --- Plot 2: EV Travel Time vs Range & Relay ---
    ranges = [100.0, 200.0, 300.0, 500.0]
    plt.figure(figsize=(8, 5))
    plt.axhline(mean_adapt, color="red", linestyle="--", label=f"Adaptive (No Preemption: {mean_adapt:.1f}s)")
    plt.axhline(mean_oracle, color="green", linestyle="--", label=f"Oracle Preemption ({mean_oracle:.1f}s)")

    for rel_mode, color, marker in [("off", "crimson", "s"), ("on", "royalblue", "^")]:
        means = []
        for rng in ranges:
            vals = [
                r["ev_travel_time"]
                for r in records
                if r["experiment"] == "sweep_range_relay" and r["range"] == rng and r["relay"] == rel_mode
            ]
            means.append(float(np.mean(vals)) if vals else 0.0)
        plt.plot(ranges, means, marker=marker, color=color, linewidth=2, label=f"VANET Relay {rel_mode.upper()}")

    plt.xlabel("Communication Range (m)", fontsize=11)
    plt.ylabel("Emergency Vehicle Travel Time (s)", fontsize=11)
    plt.title("EV Travel Time vs. Communication Range and V2V Relaying", fontsize=12)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend()
    plt.tight_layout()
    p2 = plots_dir / "plot_ev_travel_time_vs_range_relay.png"
    plt.savefig(p2, dpi=300)
    plt.close()

    # --- Plot 3: EV Travel Time vs Alert Interval ---
    intervals = [0.25, 0.5, 1.0, 2.0]
    int_means = []
    for int_val in intervals:
        vals = [
            r["ev_travel_time"]
            for r in records
            if r["experiment"] == "sweep_interval" and r["alert_interval"] == int_val
        ]
        int_means.append(float(np.mean(vals)) if vals else 0.0)

    plt.figure(figsize=(8, 5))
    plt.axhline(mean_adapt, color="red", linestyle="--", label=f"Adaptive (No Preemption: {mean_adapt:.1f}s)")
    plt.axhline(mean_oracle, color="green", linestyle="--", label=f"Oracle Preemption ({mean_oracle:.1f}s)")
    plt.plot(intervals, int_means, marker="D", color="purple", linewidth=2, label="VANET Preemption")
    plt.xlabel("EV Alert Broadcast Interval (s)", fontsize=11)
    plt.ylabel("Emergency Vehicle Travel Time (s)", fontsize=11)
    plt.title("EV Travel Time vs. Alert Broadcast Interval", fontsize=12)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend()
    plt.tight_layout()
    p3 = plots_dir / "plot_ev_travel_time_vs_alert_interval.png"
    plt.savefig(p3, dpi=300)
    plt.close()

    logger.info("Saved preemption plots: %s, %s, %s", p1, p2, p3)


def main() -> None:
    parser = argparse.ArgumentParser(description="VANET sensing sensitivity experiments")
    parser.add_argument("--scenario", type=str, default="congested", help="Scenario name")
    parser.add_argument("--quick", action="store_true", help="Run quick 1-seed test")
    parser.add_argument("--preemption", action="store_true", help="Run Module 8 emergency preemption experiments")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum simulation steps")
    args = parser.parse_args()

    if args.preemption:
        seeds = [1] if args.quick else [1, 2, 3, 4, 5]
        steps = args.max_steps or 200
        run_vanet_preemption_experiments(
            scenario="emergency",
            seeds=seeds,
            max_steps=steps,
        )
        return

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
