"""Metrics logging and tripinfo XML parser.

Provides per-step CSV logging and tripinfo XML extraction for performance evaluation.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
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
        "queue_est_N",
        "queue_est_S",
        "queue_est_E",
        "queue_est_W",
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
        queue_est_n: Optional[int] = None,
        queue_est_s: Optional[int] = None,
        queue_est_e: Optional[int] = None,
        queue_est_w: Optional[int] = None,
    ) -> None:
        """Write one row of metrics."""
        step_max_q = max(queue_n, queue_s, queue_e, queue_w)
        if step_max_q > self.max_queue:
            self.max_queue = step_max_q

        q_est_n = queue_n if queue_est_n is None else queue_est_n
        q_est_s = queue_s if queue_est_s is None else queue_est_s
        q_est_e = queue_e if queue_est_e is None else queue_est_e
        q_est_w = queue_w if queue_est_w is None else queue_est_w

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
            q_est_n,
            q_est_s,
            q_est_e,
            q_est_w,
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

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception:
        return {
            "avg_waiting_time": 0.0,
            "avg_time_loss": 0.0,
            "avg_travel_time": 0.0,
            "ev_travel_time": 0.0,
            "max_queue": float(max_queue),
            "throughput_veh_per_hr": 0.0,
        }

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

        # Synthetic incident blockers (Module 10) are not traffic
        if vid.startswith("incblk_") or vtype == "incident_blocker":
            continue

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


PETROL_DENSITY_G_PER_L = 745.0


def parse_tripinfo_emissions(
    xml_path: Union[str, Path],
    group_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Fuel, CO2 and stops from tripinfo with SUMO's emission device (CO2_abs, fuel_abs in mg).

    group_fn(vehicle_id, vtype) -> "eco" | "non" | None splits finished trips into groups;
    None leaves a trip out of the groups (it still counts in the totals). Incident blockers
    are excluded everywhere. Returns totals for all trips and per-group averages.
    """
    out: Dict[str, Any] = {
        "co2_total_kg": None, "fuel_total_l": None, "co2_g_per_km": None, "fuel_l_per_100km": None,
        "stops_per_vehicle": None,
    }
    path = Path(xml_path)
    if not path.exists():
        return out
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return out

    def new_acc() -> Dict[str, float]:
        return {"co2": 0.0, "fuel": 0.0, "dist": 0.0, "stops": 0.0, "dur": 0.0, "wait": 0.0, "n": 0.0}

    total = new_acc()
    groups: Dict[str, Dict[str, float]] = {"eco": new_acc(), "non": new_acc()}
    for trip in root.findall("tripinfo"):
        vid = trip.attrib.get("id", "")
        vtype = trip.attrib.get("vType", "")
        if vid.startswith("incblk_") or vtype == "incident_blocker":
            continue
        em = trip.find("emissions")
        if em is None:
            continue
        rec = {
            "co2": float(em.attrib.get("CO2_abs", 0.0)),
            "fuel": float(em.attrib.get("fuel_abs", 0.0)),
            "dist": float(trip.attrib.get("routeLength", 0.0)),
            "stops": float(trip.attrib.get("waitingCount", 0.0)),
            "dur": float(trip.attrib.get("duration", 0.0)),
            "wait": float(trip.attrib.get("waitingTime", 0.0)),
            "n": 1.0,
        }
        targets = [total]
        g = group_fn(vid, vtype) if group_fn is not None else None
        if g in groups:
            targets.append(groups[g])
        for acc in targets:
            for k, v in rec.items():
                acc[k] += v

    def per_km(acc: Dict[str, float]) -> Dict[str, Optional[float]]:
        if acc["n"] <= 0 or acc["dist"] <= 0:
            return {"co2_g_per_km": None, "fuel_l_per_100km": None, "stops_per_vehicle": None,
                    "travel_time": None, "waiting_time": None, "n": int(acc["n"])}
        km = acc["dist"] / 1000.0
        return {
            "co2_g_per_km": round(acc["co2"] / 1000.0 / km, 2),
            "fuel_l_per_100km": round(acc["fuel"] / 1000.0 / PETROL_DENSITY_G_PER_L / km * 100.0, 3),
            "stops_per_vehicle": round(acc["stops"] / acc["n"], 3),
            "travel_time": round(acc["dur"] / acc["n"], 2),
            "waiting_time": round(acc["wait"] / acc["n"], 2),
            "n": int(acc["n"]),
        }

    t = per_km(total)
    out.update({
        "co2_total_kg": round(total["co2"] / 1e6, 3),
        "fuel_total_l": round(total["fuel"] / 1000.0 / PETROL_DENSITY_G_PER_L, 3),
        "co2_g_per_km": t["co2_g_per_km"],
        "fuel_l_per_100km": t["fuel_l_per_100km"],
        "stops_per_vehicle": t["stops_per_vehicle"],
    })
    if group_fn is not None:
        for name, acc in groups.items():
            g = per_km(acc)
            out[f"co2_g_per_km_{name}"] = g["co2_g_per_km"]
            out[f"fuel_l_per_100km_{name}"] = g["fuel_l_per_100km"]
            out[f"stops_per_vehicle_{name}"] = g["stops_per_vehicle"]
            out[f"travel_time_{name}"] = g["travel_time"]
            out[f"n_trips_{name}"] = g["n"]
    return out
