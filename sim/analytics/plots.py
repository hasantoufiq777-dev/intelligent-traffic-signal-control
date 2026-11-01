"""Shared plotting helpers.

Module 11: GA convergence curves (best and mean fitness per generation).
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Categorical slots 1 and 2 (light surface) of the project chart palette
SERIES_1 = "#2a78d6"
SERIES_2 = "#eb6834"
TEXT_SECONDARY = "#5f5e5a"
GRID = "#e4e3df"


def plot_ga_convergence(history: Dict[str, Any], out_path: Union[str, Path]) -> Path:
    """Plot best-so-far and population-mean fitness per generation (lower is better)."""
    gens: List[Dict[str, Any]] = history["generations"]
    x = [g["generation"] for g in gens]
    best = [g["best_fitness"] for g in gens]
    mean = [g["mean_fitness"] for g in gens]

    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.plot(x, mean, color=SERIES_2, linewidth=2, marker="s", markersize=5, label="Population mean")
    ax.plot(x, best, color=SERIES_1, linewidth=2, marker="o", markersize=5, label="Best so far")
    # Direct label on the final best value only
    ax.annotate(f"best {best[-1]:.1f}", (x[-1], best[-1]), textcoords="offset points", xytext=(-8, 8),
                ha="right", color=TEXT_SECONDARY, fontsize=9)

    ax.set_xlabel("Generation")
    ax.set_ylabel("Fitness (lower is better)")
    ax.set_title(f"GA convergence - target: {history.get('target', '?')}", loc="left", fontsize=11)
    ax.set_xticks(x)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(TEXT_SECONDARY)
    ax.tick_params(colors=TEXT_SECONDARY)
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return out_path


# =============================================================================
# Module 12: evaluation plots (read results/summary.json)
# =============================================================================

# Categorical slots 1-5 (light surface), fixed order
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
REF_GRAY = "#8a8984"


def _style(ax: Any, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_title(title, loc="left", fontsize=11)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="both", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(TEXT_SECONDARY)
    ax.tick_params(colors=TEXT_SECONDARY)


def _save(fig: Any, path: Path) -> Path:
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def _stat(entry: Optional[Dict[str, Any]]) -> Tuple[Optional[float], float]:
    """(mean, 95% CI half-width) of a summary entry."""
    if not entry:
        return None, 0.0
    lo, hi = entry["ci95"]
    return entry["mean"], max(0.0, (hi - lo) / 2.0)


def _param(label: str, name: str) -> Optional[float]:
    m = re.search(rf"{name}([0-9.]+)", label)
    return float(m.group(1).rstrip(".")) if m else None


def plot_grouped_bars(suite: Dict[str, Dict[str, Any]], metric: str, title: str, xlabel: str,
                      path: Path) -> Optional[Path]:
    """Horizontal bars per configuration, one colour per scenario, 95% CI error bars."""
    scenarios = list(suite.keys())
    labels: List[str] = []
    for scen in scenarios:
        for lab, c in suite[scen].items():
            if metric in c and lab not in labels:
                labels.append(lab)
    if not labels:
        return None
    n_s = len(scenarios)
    h = 0.8 / n_s
    fig, ax = plt.subplots(figsize=(8.5, max(3.0, 0.3 * len(labels) * n_s + 1.4)))
    for si, scen in enumerate(scenarios):
        ys, xs, errs = [], [], []
        for li, lab in enumerate(labels):
            mean, err = _stat(suite[scen].get(lab, {}).get(metric))
            if mean is None:
                continue
            ys.append(li + (si - (n_s - 1) / 2) * h)
            xs.append(mean)
            errs.append(err)
        ax.barh(ys, xs, height=h * 0.9, xerr=errs, color=SERIES[si % len(SERIES)], label=scen,
                error_kw={"ecolor": TEXT_SECONDARY, "elinewidth": 1, "capsize": 2})
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    _style(ax, title, xlabel, "")
    ax.grid(False, axis="y")
    if n_s > 1:
        ax.legend(frameon=False, loc="lower right")
    return _save(fig, path)


def plot_wait_vs_penetration(signals: Dict[str, Dict[str, Any]], path: Path) -> Optional[Path]:
    """adaptive_vanet waiting time vs penetration; adaptive (dashed) and fixed (dotted) references."""
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    any_line = False
    for si, (scen, cfgs) in enumerate(signals.items()):
        color = SERIES[si % len(SERIES)]
        pts = []
        for lab, c in cfgs.items():
            if lab.startswith("adaptive_vanet@"):
                mean, err = _stat(c.get("avg_waiting_time"))
                if mean is not None:
                    pts.append((_param(lab, "pen"), mean, err))
        pts.sort()
        if pts:
            any_line = True
            ax.errorbar([p[0] for p in pts], [p[1] for p in pts], yerr=[p[2] for p in pts], color=color,
                        linewidth=2, marker="o", markersize=5, capsize=3, label=f"{scen}: adaptive_vanet")
        for ref, ls in (("adaptive", "--"), ("fixed", ":")):
            mean, _ = _stat(cfgs.get(ref, {}).get("avg_waiting_time"))
            if mean is not None:
                ax.axhline(mean, color=color, linestyle=ls, linewidth=1.5, label=f"{scen}: {ref}")
    if not any_line:
        plt.close(fig)
        return None
    _style(ax, "Average waiting time vs OBU penetration", "OBU penetration", "Average waiting time (s)")
    ax.legend(frameon=False, fontsize=8, ncol=2)
    return _save(fig, path)


def plot_ev_vs_loss(ev: Dict[str, Dict[str, Any]], path: Path) -> Optional[Path]:
    """EV travel time vs loss, relay on/off; oracle and no-preemption references; tuned point."""
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    any_line = False
    for cfgs in ev.values():
        for ri, relay in enumerate(("on", "off")):
            pts = []
            for lab, c in cfgs.items():
                if lab.startswith("adaptive_ev_vanet@") and lab.endswith(f"relay_{relay}"):
                    mean, err = _stat(c.get("ev_travel_time"))
                    if mean is not None:
                        pts.append((_param(lab, "loss"), mean, err))
            pts.sort()
            if pts:
                any_line = True
                ax.errorbar([p[0] for p in pts], [p[1] for p in pts], yerr=[p[2] for p in pts],
                            color=SERIES[ri], linewidth=2, marker="o" if relay == "on" else "s", markersize=5,
                            capsize=3, label=f"VANET preemption, relay {relay}")
        for ref, ls, name in (("adaptive_ev", "--", "oracle preemption"), ("adaptive", ":", "no preemption")):
            mean, _ = _stat(cfgs.get(ref, {}).get("ev_travel_time"))
            if mean is not None:
                ax.axhline(mean, color=REF_GRAY, linestyle=ls, linewidth=1.5, label=f"{ref} ({name})")
        for lab, c in cfgs.items():
            if lab.startswith("adaptive_ev_vanet_tuned@"):
                mean, err = _stat(c.get("ev_travel_time"))
                if mean is not None:
                    ax.errorbar([_param(lab, "loss")], [mean], yerr=[err], color=SERIES[2], marker="D",
                                markersize=7, capsize=3, linestyle="none", label="GA-tuned, relay off")
    if not any_line:
        plt.close(fig)
        return None
    _style(ax, "EV travel time vs channel loss", "Base packet loss probability", "EV travel time (s)")
    ax.legend(frameon=False, fontsize=8)
    return _save(fig, path)


def _nav_series(cfgs: Dict[str, Any], mode: str, metric: str, pen: float) -> List[Tuple[float, float, float]]:
    pts = []
    for lab, c in cfgs.items():
        if lab.startswith(f"{mode}@") and _param(lab, "_pen") == pen:
            mean, err = _stat(c.get(metric))
            if mean is not None:
                pts.append((_param(lab, "frac"), mean, err))
    return sorted(pts)


def _nav_pen(cfgs: Dict[str, Any]) -> float:
    pens = sorted({_param(l, "pen") for l in cfgs if l.startswith("none@")}, reverse=True)
    return pens[0] if pens else 1.0


def plot_nav_travel_times(nav: Dict[str, Dict[str, Any]], path: Path) -> Optional[Path]:
    """One panel per scenario: navigating (solid) vs non-navigating (dashed) travel time per mode."""
    if not nav:
        return None
    scen_list = list(nav.keys())
    fig, axes = plt.subplots(1, len(scen_list), figsize=(6.2 * len(scen_list), 4.4), squeeze=False)
    for ax, scen in zip(axes[0], scen_list):
        cfgs = nav[scen]
        pen = _nav_pen(cfgs)
        for mi, mode in enumerate(("astar", "aco", "aco_tuned")):
            for metric, ls, who in (("nav_avg_travel_time", "-", "navigating"),
                                    ("nonnav_avg_travel_time", "--", "non-navigating")):
                pts = _nav_series(cfgs, mode, metric, pen)
                if pts:
                    ax.errorbar([p[0] for p in pts], [p[1] for p in pts], yerr=[p[2] for p in pts],
                                color=SERIES[mi], linestyle=ls, linewidth=2, marker="o", markersize=5,
                                capsize=3, label=f"{mode}, {who}")
        mean, _ = _stat(cfgs.get(f"none@pen{pen:g}", {}).get("avg_travel_time"))
        if mean is not None:
            ax.axhline(mean, color=REF_GRAY, linestyle=":", linewidth=1.5, label="no navigation (all vehicles)")
        _style(ax, f"{scen}: travel time (penetration {pen:g})", "Navigating fraction", "Travel time (s)")
        ax.legend(frameon=False, fontsize=7)
    return _save(fig, path)


def plot_nav_edge_load_cv(nav: Dict[str, Dict[str, Any]], path: Path) -> Optional[Path]:
    """One panel per scenario: edge-load CV per nav mode vs fraction; no-navigation reference."""
    if not nav:
        return None
    scen_list = list(nav.keys())
    fig, axes = plt.subplots(1, len(scen_list), figsize=(6.2 * len(scen_list), 4.2), squeeze=False)
    for ax, scen in zip(axes[0], scen_list):
        cfgs = nav[scen]
        pen = _nav_pen(cfgs)
        for mi, mode in enumerate(("astar", "aco", "aco_tuned")):
            pts = _nav_series(cfgs, mode, "edge_load_cv", pen)
            if pts:
                ax.errorbar([p[0] for p in pts], [p[1] for p in pts], yerr=[p[2] for p in pts], color=SERIES[mi],
                            linewidth=2, marker="o", markersize=5, capsize=3, label=mode)
        mean, _ = _stat(cfgs.get(f"none@pen{pen:g}", {}).get("edge_load_cv"))
        if mean is not None:
            ax.axhline(mean, color=REF_GRAY, linestyle=":", linewidth=1.5, label="no navigation")
        _style(ax, f"{scen}: edge-load CV (penetration {pen:g})", "Navigating fraction",
               "Edge-load CV (lower = more even)")
        ax.legend(frameon=False, fontsize=8)
    return _save(fig, path)


def plot_preemption_timeseries(out_dir: Path, max_steps: Optional[int] = None, seed: int = 1) -> Optional[Path]:
    """Queue lengths at the first preempted junction, 60 s before to 90 s after preemption.

    Runs one emergency simulation (adaptive_ev_vanet, grid2x2) to get per-step queues and
    the preemption-events CSV.
    """
    import csv as _csv

    from sim.runner import run_simulation

    run_dir = out_dir / "preemption_example"
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_csv = run_dir / "metrics.csv"
    run_simulation("emergency", "adaptive_ev_vanet", seed=seed, params={"network": "grid2x2"},
                   run_id="eval_preempt_example", max_steps=max_steps, metrics_csv_path=metrics_csv,
                   output_dir=run_dir)
    events = sorted(run_dir.glob("preemption_events_*.csv"))
    if not events:
        return None
    with open(events[0], newline="", encoding="utf-8") as f:
        ev_rows = list(_csv.DictReader(f))
    if not ev_rows:
        return None
    first = min(ev_rows, key=lambda r: float(r["time"]))
    tls, t0, dur = first["tls_id"], float(first["time"]), float(first["duration"])
    with open(metrics_csv, newline="", encoding="utf-8") as f:
        rows = [r for r in _csv.DictReader(f)
                if r["tls_id"] == tls and t0 - 60 <= float(r["time"]) <= t0 + dur + 90]
    if not rows:
        return None
    t = [float(r["time"]) for r in rows]
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    ax.axvspan(t0, t0 + dur, color=GRID, alpha=0.9, label=f"preemption for {first['vehicle_id']}")
    for i, d in enumerate("NSEW"):
        ax.plot(t, [float(r[f"queue_{d}"]) for r in rows], color=SERIES[i], linewidth=2, label=f"queue {d}")
    _style(ax, f"Queues at {tls} around an EV preemption (adaptive_ev_vanet, seed {seed})",
           "Simulation time (s)", "Halting vehicles")
    ax.legend(frameon=False, fontsize=8, ncol=5, loc="upper left")
    return _save(fig, out_dir / "plot_preemption_queues.png")


def make_evaluation_plots(summary: Dict[str, Any], out_dir: Union[str, Path],
                          max_steps: Optional[int] = None) -> List[Path]:
    """All Module 12 plots for the suites present in the summary."""
    out_dir = Path(out_dir)
    s = summary.get("suites", {})
    made: List[Optional[Path]] = []
    if "signals" in s:
        made.append(plot_grouped_bars(s["signals"], "avg_waiting_time", "Signals suite: average waiting time (95% CI)",
                                      "Average waiting time (s)", out_dir / "plot_signals_bars.png"))
        made.append(plot_wait_vs_penetration(s["signals"], out_dir / "plot_wait_vs_penetration.png"))
    if "ev" in s:
        made.append(plot_grouped_bars(s["ev"], "ev_travel_time", "EV suite: EV travel time (95% CI)",
                                      "EV travel time (s)", out_dir / "plot_ev_bars.png"))
        made.append(plot_ev_vs_loss(s["ev"], out_dir / "plot_ev_vs_loss.png"))
        made.append(plot_preemption_timeseries(out_dir, max_steps=max_steps))
    if "nav" in s:
        made.append(plot_grouped_bars(s["nav"], "avg_travel_time", "Navigation suite: average travel time (95% CI)",
                                      "Average travel time, all vehicles (s)", out_dir / "plot_nav_bars.png"))
        made.append(plot_nav_travel_times(s["nav"], out_dir / "plot_nav_travel_times.png"))
        made.append(plot_nav_edge_load_cv(s["nav"], out_dir / "plot_nav_edge_load_cv.png"))
    return [p for p in made if p is not None]
