"""Unit tests for adaptive traffic signal controller and safety guarantees."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import pytest

from sim.controllers.adaptive import AdaptiveController
from sim.controllers.base import BaseController
from sim.tests.test_safety import MockSUMOAPI

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_no_conflicting_greens_during_adaptive_simulation() -> None:
    """End-to-end test asserting no conflicting greens occur during an adaptive run."""
    cmd = [
        sys.executable,
        "sim/main.py",
        "--run-id", "test_adapt_safety",
        "--scenario", "congested",
        "--controller", "adaptive",
        "--seed", "1",
        "--live",
        "--max-steps", "100",
    ]
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"Simulation failed: {proc.stderr}"

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        if data.get("type") == "state":
            tls_data = data["tls"]
            for tls_id, info in tls_data.items():
                sig = info["signal"]
                ns_green = sig["N"] in ("G", "y") or sig["S"] in ("G", "y")
                ew_green = sig["E"] in ("G", "y") or sig["W"] in ("G", "y")
                # Conflicting green assertion
                assert not (ns_green and ew_green), f"Safety violation at t={data['t']} on {tls_id}: {sig}"


def test_adaptive_min_green_constraint() -> None:
    """Verify that AdaptiveController strictly obeys min_green before switching."""
    mock_api = MockSUMOAPI()
    ctrl = AdaptiveController(
        tls_id="J0",
        params={
            "min_green": 10.0,
            "max_green": 50.0,
            "decision_interval": 1.0,
            "queue_threshold": 2,
        },
        sumo_api=mock_api,
    )

    # Initial state: NS Green
    assert ctrl.current_phase == BaseController.PHASE_NS_GREEN

    # Simulate heavy EW demand and 0 NS demand
    def mock_queues() -> dict[str, int]:
        return {"N": 0, "S": 0, "E": 10, "W": 10}

    ctrl.get_queue_lengths = mock_queues  # type: ignore

    # Step for 5 seconds (< min_green of 10s)
    for _ in range(5):
        ctrl.step(1.0)
        assert ctrl.current_phase == BaseController.PHASE_NS_GREEN
        assert not ctrl.is_in_transition()

    # Step past min_green (10s) -> should now trigger transition to EW green
    for _ in range(6):
        ctrl.step(1.0)

    assert ctrl.is_in_transition()
    assert ctrl.target_phase == BaseController.PHASE_EW_GREEN


def test_adaptive_max_green_constraint() -> None:
    """Verify that AdaptiveController forces a switch when max_green is reached."""
    mock_api = MockSUMOAPI()
    ctrl = AdaptiveController(
        tls_id="J0",
        params={
            "min_green": 10.0,
            "max_green": 20.0,
            "decision_interval": 1.0,
            "queue_threshold": 100,  # High threshold so queues alone wouldn't switch
        },
        sumo_api=mock_api,
    )

    # Both approaches have traffic
    ctrl.get_queue_lengths = lambda: {"N": 5, "S": 5, "E": 5, "W": 5}  # type: ignore

    # Step 19 seconds
    for _ in range(19):
        ctrl.step(1.0)
        assert ctrl.current_phase == BaseController.PHASE_NS_GREEN

    # At second 20 (max_green reached), controller must initiate switch
    ctrl.step(1.0)
    assert ctrl.is_in_transition()
    assert ctrl.target_phase == BaseController.PHASE_EW_GREEN


def test_adaptive_starvation_avoidance() -> None:
    """Verify that waiting vehicles trigger starvation priority when limit is reached."""
    mock_api = MockSUMOAPI()
    ctrl = AdaptiveController(
        tls_id="J0",
        params={
            "min_green": 5.0,
            "max_green": 80.0,
            "decision_interval": 1.0,
            "queue_threshold": 50,  # Huge threshold to prevent normal switching
            "starvation_limit": 15.0,
        },
        sumo_api=mock_api,
    )

    # NS has huge queue (30), EW has 1 waiting car
    ctrl.get_queue_lengths = lambda: {"N": 15, "S": 15, "E": 1, "W": 0}  # type: ignore

    # Step 14 seconds (less than starvation limit of 15s)
    for _ in range(14):
        ctrl.step(1.0)
        assert ctrl.current_phase == BaseController.PHASE_NS_GREEN

    # At 16 seconds, East has waited >= 15s on red -> starvation triggered!
    for _ in range(2):
        ctrl.step(1.0)

    assert ctrl.is_in_transition()
    assert ctrl.target_phase == BaseController.PHASE_EW_GREEN
