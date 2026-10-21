"""Unit tests validating live JSON streaming protocol for both main.py and mock_sim.py."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def run_cli_subprocess(args: List[str]) -> subprocess.CompletedProcess[str]:
    """Execute CLI command and return completed process."""
    cmd = [sys.executable] + args
    return subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )


def validate_schema(line_data: Dict[str, Any], expected_run_id: str) -> None:
    """Validate JSON-lines protocol schema rules."""
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

        # Verify tls schema
        for tls_id, tls_info in line_data["tls"].items():
            assert "phase" in tls_info
            assert "queues" in tls_info
            assert "signal" in tls_info
            for d in ("N", "S", "E", "W"):
                assert d in tls_info["queues"]
                assert d in tls_info["signal"]

        # Verify ev schema
        for ev_entry in line_data["ev"]:
            assert "id" in ev_entry
            assert "tls" in ev_entry
            assert "eta" in ev_entry
            assert "dist" in ev_entry
            assert "speed" in ev_entry

        # Verify metrics schema
        m = line_data["metrics"]
        assert "avg_wait" in m
        assert "n_vehicles" in m
        assert "throughput" in m

    elif msg_type == "event":
        assert "t" in line_data
        assert "kind" in line_data
        assert line_data["kind"] in ("preempt_start", "preempt_end", "incident_start", "incident_end")
        assert "tls" in line_data
        assert "detail" in line_data

    elif msg_type == "result":
        assert "metrics" in line_data
        assert "seed" in line_data
        assert "controller" in line_data
        assert "scenario" in line_data
        m = line_data["metrics"]
        for key in (
            "avg_waiting_time",
            "avg_time_loss",
            "avg_travel_time",
            "ev_travel_time",
            "max_queue",
            "throughput_veh_per_hr",
        ):
            assert key in m


def test_mock_sim_live_protocol() -> None:
    """Verify mock_sim emits valid JSON lines adhering strictly to the live protocol."""
    run_id = "test_mock_1"
    proc = run_cli_subprocess([
        "sim/mock_sim.py",
        "--run-id",
        run_id,
        "--scenario",
        "emergency",
        "--controller",
        "adaptive_ev",
        "--seed",
        "1",
        "--live",
        "--max-steps",
        "25",
        "--step-delay",
        "0",
    ])

    assert proc.returncode == 0, f"mock_sim failed: {proc.stderr}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert len(lines) >= 20

    parsed_lines = []
    for line in lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"Non-JSON line encountered: {line}")
        validate_schema(data, run_id)
        parsed_lines.append(data)

    # First line must be status: started
    assert parsed_lines[0]["type"] == "status"
    assert parsed_lines[0]["status"] == "started"

    # Penultimate line must be status: finished
    assert parsed_lines[-2]["type"] == "status"
    assert parsed_lines[-2]["status"] == "finished"

    # Last line must be result
    assert parsed_lines[-1]["type"] == "result"


def test_real_sim_live_protocol() -> None:
    """Verify sim/main.py emits valid JSON lines in --live mode."""
    run_id = "test_real_1"
    proc = run_cli_subprocess([
        "sim/main.py",
        "--run-id",
        run_id,
        "--scenario",
        "normal",
        "--controller",
        "fixed",
        "--seed",
        "1",
        "--live",
        "--max-steps",
        "15",
    ])

    assert proc.returncode == 0, f"main.py failed: {proc.stderr}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert len(lines) >= 15

    parsed_lines = []
    for line in lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"Non-JSON line encountered on stdout: {line}")
        validate_schema(data, run_id)
        parsed_lines.append(data)

    assert parsed_lines[0]["type"] == "status"
    assert parsed_lines[0]["status"] == "started"

    assert parsed_lines[-2]["type"] == "status"
    assert parsed_lines[-2]["status"] == "finished"

    assert parsed_lines[-1]["type"] == "result"


def test_real_sim_error_protocol() -> None:
    """Verify invalid scenario emits status: error line and non-zero exit code."""
    run_id = "test_err_1"
    proc = run_cli_subprocess([
        "sim/main.py",
        "--run-id",
        run_id,
        "--scenario",
        "non_existent_xyz",
        "--controller",
        "fixed",
        "--seed",
        "1",
        "--live",
    ])

    assert proc.returncode != 0
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert len(lines) >= 1
    err_obj = json.loads(lines[-1])
    assert err_obj["type"] == "status"
    assert err_obj["status"] == "error"
    assert err_obj["run_id"] == run_id
