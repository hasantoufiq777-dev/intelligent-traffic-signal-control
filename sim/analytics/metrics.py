"""Metrics logging and tripinfo XML parser.

Provides per-step CSV logging and tripinfo XML extraction for performance evaluation.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Union
import xml.etree.ElementTree as ET
import numpy as np


class StepMetricsLogger:
    """Logs per-step simulation metrics to a CSV file."""

    CSV_HEADER = [
        "time",
        "tls_id",
        "phase",
        "queue_N",
        "queue_S",
        "queue_E",
        "queue_W",
        "n_vehicles",
        "ev_present",
    ]

    def __init__(self, filepath: Union[str, Path]) -> None:
        """Initialize logger and write header."""
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.filepath, "w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        self.writer.writerow(self.CSV_HEADER)
        self.max_queue: int = 0

    def log_step(
        self,
        sim_time: float,
        tls_id: str,
        phase: int,
        queue_n: int,
        queue_s: int,
        queue_e: int,
        queue_w: int,
        n_vehicles: int,
        ev_present: bool,
    ) -> None:
        """Write one row of metrics."""
        step_max_q = max(queue_n, queue_s, queue_e, queue_w)
        if step_max_q > self.max_queue:
            self.max_queue = step_max_q

        self.writer.writerow([
            f"{sim_time:.1f}",
            tls_id,
            phase,
            queue_n,
            queue_s,
            queue_e,
            queue_w,
            n_vehicles,
            1 if ev_present else 0,
        ])

    def flush(self) -> None:
        """Flush file buffer to disk."""
        self.file.flush()

    def close(self) -> None:
        """Close CSV file handle."""
        if not self.file.closed:
            self.file.close()


def parse_tripinfo(
    xml_path: Union[str, Path],
    sim_duration: float = 1000.0,
    max_queue: int = 0,
) -> Dict[str, float]:
    """Parse SUMO tripinfo.out.xml and return aggregated performance metrics.

    Args:
        xml_path: Path to tripinfo.out.xml.
        sim_duration: Simulation duration in seconds to compute hourly throughput.
        max_queue: Peak queue recorded during simulation.

    Returns:
        Dictionary with avg_waiting_time, avg_time_loss, avg_travel_time,
        ev_travel_time, max_queue, and throughput_veh_per_hr.
    """
    path = Path(xml_path)
    if not path.exists():
        return {
            "avg_waiting_time": 0.0,
            "avg_time_loss": 0.0,
            "avg_travel_time": 0.0,
            "ev_travel_time": 0.0,
            "max_queue": float(max_queue),
            "throughput_veh_per_hr": 0.0,
        }

    tree = ET.parse(path)
    root = tree.getroot()

    waiting_times: List[float] = []
    time_losses: List[float] = []
    durations: List[float] = []
    ev_durations: List[float] = []

    for trip in root.findall("tripinfo"):
        w = float(trip.attrib.get("waitingTime", 0.0))
        tl = float(trip.attrib.get("timeLoss", 0.0))
        d = float(trip.attrib.get("duration", 0.0))
        vtype = trip.attrib.get("vType", "")
        vid = trip.attrib.get("id", "")

        waiting_times.append(w)
        time_losses.append(tl)
        durations.append(d)

        if vtype == "emergency" or "amb" in vid.lower():
            ev_durations.append(d)

    total_vehicles = len(durations)
    hours = max(sim_duration / 3600.0, 1e-6)
    throughput = round(total_vehicles / hours, 2)

    return {
        "avg_waiting_time": round(float(np.mean(waiting_times)) if waiting_times else 0.0, 2),
        "avg_time_loss": round(float(np.mean(time_losses)) if time_losses else 0.0, 2),
        "avg_travel_time": round(float(np.mean(durations)) if durations else 0.0, 2),
        "ev_travel_time": round(float(np.mean(ev_durations)) if ev_durations else 0.0, 2),
        "max_queue": float(max_queue),
        "throughput_veh_per_hr": throughput,
    }
