"""Unit tests for metrics parser and CSV logging."""

from __future__ import annotations

from pathlib import Path
import pytest
from sim.analytics.metrics import StepMetricsLogger, parse_tripinfo


def test_parse_tripinfo_calculations(tmp_path: Path) -> None:
    """Verify tripinfo XML parsing and metric calculations."""
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<tripinfos>
    <tripinfo id="car1" depart="10.0" duration="40.0" waitingTime="10.0" timeLoss="15.0" vType="car"/>
    <tripinfo id="car2" depart="20.0" duration="60.0" waitingTime="20.0" timeLoss="25.0" vType="car"/>
    <tripinfo id="amb1" depart="30.0" duration="25.0" waitingTime="5.0" timeLoss="5.0" vType="emergency"/>
</tripinfos>
"""
    xml_file = tmp_path / "test_tripinfo.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    metrics = parse_tripinfo(xml_file, sim_duration=360.0, max_queue=8)

    # Total vehicles = 3, duration = 360s = 0.1 hour -> throughput = 30 veh/hr
    assert metrics["throughput_veh_per_hr"] == 30.0

    # avg_waiting_time: (10 + 20 + 5) / 3 = 11.67
    assert metrics["avg_waiting_time"] == pytest.approx(11.67, 0.01)

    # avg_time_loss: (15 + 25 + 5) / 3 = 15.0
    assert metrics["avg_time_loss"] == pytest.approx(15.0, 0.01)

    # avg_travel_time: (40 + 60 + 25) / 3 = 41.67
    assert metrics["avg_travel_time"] == pytest.approx(41.67, 0.01)

    # ev_travel_time: only amb1 (duration 25.0)
    assert metrics["ev_travel_time"] == 25.0

    # max_queue carried through
    assert metrics["max_queue"] == 8.0


def test_parse_tripinfo_missing_file(tmp_path: Path) -> None:
    """Verify safe fallback when tripinfo XML is missing."""
    missing_file = tmp_path / "does_not_exist.xml"
    metrics = parse_tripinfo(missing_file, sim_duration=360.0, max_queue=0)

    assert metrics["avg_waiting_time"] == 0.0
    assert metrics["avg_time_loss"] == 0.0
    assert metrics["avg_travel_time"] == 0.0
    assert metrics["ev_travel_time"] == 0.0
    assert metrics["throughput_veh_per_hr"] == 0.0


def test_step_metrics_logger(tmp_path: Path) -> None:
    """Verify that StepMetricsLogger creates correct CSV and computes peak queue."""
    csv_file = tmp_path / "metrics_test.csv"
    logger = StepMetricsLogger(csv_file)

    logger.log_step(
        sim_time=1.0,
        tls_id="J0",
        phase=0,
        queue_n=2,
        queue_s=5,
        queue_e=1,
        queue_w=0,
        n_vehicles=8,
        ev_present=False,
    )
    logger.log_step(
        sim_time=2.0,
        tls_id="J0",
        phase=0,
        queue_n=4,
        queue_s=7,
        queue_e=3,
        queue_w=2,
        n_vehicles=16,
        ev_present=True,
    )
    logger.close()

    assert logger.max_queue == 7

    content = csv_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(content) == 3  # Header + 2 data lines
    assert content[0] == "time,tls_id,phase,queue_N,queue_S,queue_E,queue_W,n_vehicles,ev_present"
    assert "2.0,J0,0,4,7,3,2,16,1" in content[2]
