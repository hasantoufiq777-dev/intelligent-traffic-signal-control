"""Unit tests validating live JSON streaming protocol v2 for both main.py and mock_sim.py."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def run_cli_subprocess(args: List[str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable] + args
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)


def validate_schema_v2(line_data: Dict[str, Any], expected_run_id: str) -> None:
    assert "type" in line_data
    assert "run_id" in line_data
    assert line_data["run_id"] == expected_run_id

    msg_type = line_data["type"]
    assert msg_type in ("status", "state", "event", "result")

    if msg_type == "status":
        assert line_data["status"] in ("started", "finished", "stopped", "error")
        assert "message" in line_data

    elif msg_type == "state":
        assert "t" in line_data
        assert "tls" in line_data
        assert "ev" in line_data
        assert "metrics" in line_data
        assert "comm" in line_data
        assert "nav" in line_data

        # TLS validation
        for tls_id, tls_info in line_data["tls"].items():
            assert "phase" in tls_info
            assert "queues" in tls_info
            assert "queues_est" in tls_info
            assert "signal" in tls_info
            for d in ("N", "S", "E", "W"):
                assert d in tls_info["queues"]
                assert d in tls_info["queues_est"]
                assert d in tls_info["signal"]

        # EV validation
        for ev_entry in line_data["ev"]:
            assert "id" in ev_entry
            assert "tls" in ev_entry
            assert "eta" in ev_entry
            assert "dist" in ev_entry
            assert "speed" in ev_entry
            assert "alert_age" in ev_entry

        # Comm validation
        comm = line_data["comm"]
        for key in ("pdr", "latency_ms", "penetration", "sent", "lost"):
            assert key in comm

        # Nav validation
        nav = line_data["nav"]
        for key in ("mode", "navigating", "avg_tt_nav", "avg_tt_non", "max_edge_load", "edge_load_cv"):
            assert key in nav

    elif msg_type == "event":
        assert "t" in line_data
        assert "kind" in line_data
        assert line_data["kind"] in (
            "preempt_start",
            "preempt_end",
            "incident_start",
            "incident_end",
            "alert_sent",
            "alert_delivered",
            "alert_lost",
            "reroute",
        )
        assert "tls" in line_data
        assert "detail" in line_data

    elif msg_type == "result":
        assert "metrics" in line_data
        assert "seed" in line_data
        assert "controller" in line_data
        assert "scenario" in line_data


def test_mock_sim_live_protocol_v2() -> None:
    run_id = "test_mock_v2"
    proc = run_cli_subprocess([
        "sim/mock_sim.py",
        "--run-id", run_id,
        "--scenario", "emergency",
        "--controller", "adaptive_ev",
        "--seed", "1",
        "--live",
        "--max-steps", "25",
        "--step-delay", "0",
    ])
    assert proc.returncode == 0
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    for line in lines:
        data = json.loads(line)
        validate_schema_v2(data, run_id)


def test_real_sim_live_protocol_v2() -> None:
    run_id = "test_real_v2"
    proc = run_cli_subprocess([
        "sim/main.py",
        "--run-id", run_id,
        "--scenario", "normal",
        "--controller", "fixed",
        "--seed", "1",
        "--live",
        "--max-steps", "15",
    ])
    assert proc.returncode == 0, f"Sim failed: {proc.stderr}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    for line in lines:
        data = json.loads(line)
        validate_schema_v2(data, run_id)


def test_cli_flags_not_implemented() -> None:
    """Verify that unbuilt features like --nav astar raise clean NotImplementedError."""
    proc = run_cli_subprocess([
        "sim/main.py",
        "--run-id", "test_flags",
        "--scenario", "normal",
        "--controller", "fixed",
        "--nav", "astar",
    ])
    assert proc.returncode != 0
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert any("NotImplementedError" in l for l in lines)
