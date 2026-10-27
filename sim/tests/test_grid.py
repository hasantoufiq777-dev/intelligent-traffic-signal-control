"""Unit tests for 2x2 signalized grid network and multi-junction control."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from sim.runner import run_simulation

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_grid2x2_multi_junction_execution(tmp_path: Path) -> None:
    """Verify that 2x2 grid runs with all 4 junctions (J0..J3) logged in metrics CSV."""
    csv_path = tmp_path / "grid_test_metrics.csv"
    res = run_simulation(
        scenario="navigation",
        controller="fixed",
        seed=1,
        max_steps=50,
        gui=False,
        live=False,
        metrics_csv_path=csv_path,
    )

    assert res["scenario"] == "navigation"
    assert res["controller"] == "fixed"
    assert res["throughput_veh_per_hr"] >= 0.0

    # Verify per-junction rows in CSV
    assert csv_path.exists()
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 10

    tls_seen = set()
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) > 1:
            tls_seen.add(parts[1])

    assert tls_seen == {"J0", "J1", "J2", "J3"}


def test_grid2x2_adaptive_controller_execution() -> None:
    """Verify adaptive controller executes on 2x2 grid without errors."""
    res = run_simulation(
        scenario="navigation",
        controller="adaptive",
        seed=1,
        max_steps=60,
        gui=False,
        live=False,
    )

    assert res["scenario"] == "navigation"
    assert res["controller"] == "adaptive"
    assert res["avg_waiting_time"] >= 0.0


def test_route_alternatives_file_exists() -> None:
    """Verify route_alternatives.md report exists and has required sections."""
    report_file = PROJECT_ROOT / "results" / "route_alternatives.md"
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "2x2 Grid Route Alternatives Analysis" in content
    assert "bottom0J0" in content
    assert "top1J3" in content
    assert "Near-Equal (<20%)" in content
