"""Measure fuel and CO2 curves per vehicle category with SUMO's HBEFA4 model.

For each category of sim/vehicles.py, one vehicle drives alone on a long straight test road:

- at each constant speed (steady state): fuel and CO2 per metre;
- standing still with the engine running: fuel and CO2 per second (idling);
- speeding up from a stop to each speed with the category's acceleration: the fuel and CO2 used, the
  time and the distance (the cost of a stop, compared with driving that distance at speed).

These are SUMO's own emission values (the same model as every run's fuel and CO2 results), sampled,
not a hand-made formula. The speed advisor (sim/eco_speed.py) reads the result file.

Usage: python sim/analytics/fuel_curves.py   ->  sim/data/fuel_curves.json
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Dict, List
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "sim" / "data" / "fuel_curves.json"
SPEEDS_KMH = list(range(10, 95, 5))  # 10 ... 90 km/h
FUEL_DENSITY_MG_PER_ML = {"petrol": 745.0, "diesel": 835.0}


def _fuel_kind(emission_class: str) -> str:
    return "diesel" if any(k in emission_class for k in ("UBus", "RT_", "TT_", "LCV_diesel")) else "petrol"


def _test_net(tmp: Path) -> Path:
    nod = tmp / "t.nod.xml"
    edg = tmp / "t.edg.xml"
    nod.write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="20000" y="0"/></nodes>', encoding="utf-8")
    edg.write_text('<edges><edge id="road" from="a" to="b" numLanes="1" speed="40"/></edges>', encoding="utf-8")
    net = tmp / "t.net.xml"
    subprocess.run(["netconvert", "-n", str(nod), "-e", str(edg), "-o", str(net)], check=True, capture_output=True)
    return net


def measure() -> Dict[str, Any]:
    import sumolib
    import traci

    from sim.vehicles import CATALOG, VehicleTypeFactory

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        net = _test_net(tmp)
        rou = tmp / "t.rou.xml"
        root = ET.Element("routes")
        VehicleTypeFactory().vtype_elements(root, CATALOG.keys())
        for t in root.findall("vType"):
            t.set("speedFactor", "1")  # exact speeds for the measurement
            t.set("sigma", "0")
            t.set("maxSpeed", "40")
        ET.SubElement(root, "route", id="r", edges="road")
        rou.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
        from sim.sumo_connect import start_sumo

        start_sumo(traci, [sumolib.checkBinary("sumo"), "-n", str(net), "-r", str(rou), "--step-length", "1",
                           "--no-step-log", "true", "--device.emissions.probability", "1"])
        out: Dict[str, Any] = {}
        n = 0
        try:
            for kind, vt in CATALOG.items():
                if kind == "emergency":
                    continue
                n += 1

                def run(vid: str, depart_speed: float, target: float, steps: int) -> List[tuple]:
                    traci.vehicle.add(vid, "r", typeID=kind, departSpeed=str(depart_speed))
                    traci.vehicle.setSpeedMode(vid, 0)  # exact speed (no other traffic on the test road)
                    traci.vehicle.setSpeed(vid, target)
                    rows = []
                    for _ in range(steps):
                        traci.simulationStep()
                        rows.append((traci.vehicle.getSpeed(vid), traci.vehicle.getFuelConsumption(vid),
                                     traci.vehicle.getCO2Emission(vid), traci.vehicle.getDistance(vid)))
                    traci.vehicle.remove(vid)
                    traci.simulationStep()
                    return rows

                fuel_m, co2_m = [], []
                speeds = [s for s in SPEEDS_KMH if s / 3.6 <= vt.max_speed + 0.1]
                for s in speeds:
                    v = s / 3.6
                    rows = run(f"{kind}_c{s}", v, v, 12)[4:]  # steady state
                    fuel_m.append(round(sum(r[1] for r in rows) / sum(r[0] for r in rows), 4))
                    co2_m.append(round(sum(r[2] for r in rows) / sum(r[0] for r in rows), 3))
                idle = run(f"{kind}_idle", 0.0, 0.0, 10)[3:]
                accel = {}
                for s in speeds:
                    v = s / 3.6
                    rows = run(f"{kind}_a{s}", 0.0, v, 90)
                    k = next((i for i, r in enumerate(rows) if r[0] >= v - 0.05), len(rows) - 1)
                    accel[str(s)] = {"time_s": k + 1, "dist_m": round(rows[k][3], 1),
                                     "fuel_mg": round(sum(r[1] for r in rows[: k + 1]), 1),
                                     "co2_mg": round(sum(r[2] for r in rows[: k + 1]), 1)}
                fuel = _fuel_kind(vt.emission_class)
                out[kind] = {
                    "label": vt.label, "emission_class": vt.emission_class, "fuel": fuel,
                    "density_mg_per_ml": FUEL_DENSITY_MG_PER_ML[fuel],
                    "speeds_kmh": speeds, "fuel_mg_per_m": fuel_m, "co2_mg_per_m": co2_m,
                    "idle_fuel_mg_s": round(sum(r[1] for r in idle) / len(idle), 2),
                    "idle_co2_mg_s": round(sum(r[2] for r in idle) / len(idle), 2),
                    "accel_from_stop": accel,
                }
        finally:
            traci.close()
    return {"source": "SUMO HBEFA4 (sim/analytics/fuel_curves.py)", "speeds_unit": "km/h", "types": out}


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT))
    data = measure()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for k, v in data["types"].items():
        best = min(range(len(v["speeds_kmh"])), key=lambda i: v["fuel_mg_per_m"][i])
        l100 = v["fuel_mg_per_m"][best] * 100000 / v["density_mg_per_ml"] / 1000
        print(f"{k:11s} lowest fuel per km at {v['speeds_kmh'][best]} km/h: {l100:.2f} L/100 km; "
              f"idle {v['idle_fuel_mg_s']} mg/s")
    print(f"Wrote {OUT}")
