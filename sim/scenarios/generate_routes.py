"""Route generator for SUMO simulation scenarios.

Generates routes.rou.xml from scenario YAML configurations with deterministic random seeds.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List
import xml.dom.minidom
import xml.etree.ElementTree as ET
import numpy as np
import yaml


ROUTES_DEF = {
    "N": {
        "straight": ("N2J0", "J02S"),
        "left": ("N2J0", "J02E"),
        "right": ("N2J0", "J02W"),
    },
    "S": {
        "straight": ("S2J0", "J02N"),
        "left": ("S2J0", "J02W"),
        "right": ("S2J0", "J02E"),
    },
    "E": {
        "straight": ("E2J0", "J02W"),
        "left": ("E2J0", "J02S"),
        "right": ("E2J0", "J02N"),
    },
    "W": {
        "straight": ("W2J0", "J02E"),
        "left": ("W2J0", "J02N"),
        "right": ("W2J0", "J02S"),
    },
}


def load_scenario_config(scenario_path: Path) -> Dict[str, Any]:
    """Load scenario configuration from a YAML file."""
    with open(scenario_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def generate_routes(
    config: Dict[str, Any],
    output_path: Path,
    override_seed: int | None = None,
) -> None:
    """Generate SUMO route XML based on scenario parameters."""
    traffic_cfg = config.get("traffic", {})
    sim_cfg = config.get("simulation", {})
    duration = float(sim_cfg.get("duration", 1000.0))
    seed = override_seed if override_seed is not None else int(traffic_cfg.get("seed", 1))

    rng = np.random.default_rng(seed)

    rates = traffic_cfg.get("rates", {"N": 360, "S": 360, "E": 300, "W": 300})
    turn_ratios = traffic_cfg.get("turn_ratios", {"straight": 0.70, "left": 0.15, "right": 0.15})
    turn_options = ["straight", "left", "right"]
    turn_probs = [
        turn_ratios.get("straight", 0.70),
        turn_ratios.get("left", 0.15),
        turn_ratios.get("right", 0.15),
    ]
    turn_probs = [p / sum(turn_probs) for p in turn_probs]

    vehicles: List[Dict[str, Any]] = []

    # Generate regular vehicles per approach direction
    for direction, rate_per_hour in rates.items():
        if rate_per_hour <= 0:
            continue
        rate_per_sec = rate_per_hour / 3600.0
        # Poisson arrival process (exponential inter-arrival times)
        curr_time = float(rng.exponential(1.0 / rate_per_sec))
        veh_idx = 0
        while curr_time < duration:
            turn = rng.choice(turn_options, p=turn_probs)
            edges = ROUTES_DEF[direction][str(turn)]
            route_str = f"{edges[0]} {edges[1]}"
            vehicles.append({
                "id": f"{direction}_{veh_idx}",
                "type": "car",
                "depart": round(curr_time, 2),
                "route": route_str,
            })
            veh_idx += 1
            curr_time += float(rng.exponential(1.0 / rate_per_sec))

    # Add emergency vehicles if configured
    emergency_cfg = config.get("emergency", {})
    if emergency_cfg.get("enabled", False):
        ev_list = emergency_cfg.get("vehicles", [])
        for ev in ev_list:
            ev_id = ev.get("id", "amb1")
            depart_time = float(ev.get("depart", 120.0))
            from_edge = ev.get("from_edge", "S2J0")
            to_edge = ev.get("to_edge", "J02N")
            vehicles.append({
                "id": ev_id,
                "type": "emergency",
                "depart": round(depart_time, 2),
                "route": f"{from_edge} {to_edge}",
            })

    # Sort strictly by departure time for SUMO compatibility
    vehicles.sort(key=lambda v: (v["depart"], v["id"]))

    # Build XML
    root = ET.Element("routes")
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set("xsi:noNamespaceSchemaLocation", "http://sumo.dlr.de/xsd/routes_file.xsd")

    # Vehicle types
    ET.SubElement(
        root,
        "vType",
        id="car",
        accel="2.6",
        decel="4.5",
        sigma="0.5",
        length="5.0",
        minGap="2.5",
        maxSpeed="15.0",
        guiShape="passenger",
    )
    ET.SubElement(
        root,
        "vType",
        id="emergency",
        vClass="emergency",
        accel="3.5",
        decel="6.0",
        sigma="0.2",
        length="6.5",
        minGap="2.0",
        maxSpeed="25.0",
        speedFactor="1.3",
        color="255,0,0",
        guiShape="emergency",
    )

    # Individual vehicle definitions
    for veh in vehicles:
        v_elem = ET.SubElement(
            root,
            "vehicle",
            id=veh["id"],
            type=veh["type"],
            depart=f"{veh['depart']:.2f}",
        )
        ET.SubElement(v_elem, "route", edges=veh["route"])

    xml_str = ET.tostring(root, encoding="utf-8")
    parsed = xml.dom.minidom.parseString(xml_str)
    pretty_xml = parsed.toprettyxml(indent="    ")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(pretty_xml)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SUMO routes from scenario YAML")
    parser.add_argument(
        "--scenario",
        type=str,
        default="normal",
        help="Scenario name (normal, congested, emergency, incident) or YAML path",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed override")
    parser.add_argument(
        "--output",
        type=str,
        default="sim/sumo/routes.rou.xml",
        help="Target output routes file",
    )
    args = parser.parse_args()

    scenario_input = Path(args.scenario)
    if scenario_input.is_file():
        scenario_path = scenario_input
    else:
        scenario_path = Path("sim/scenarios") / f"{args.scenario}.yaml"

    if not scenario_path.exists():
        raise FileNotFoundError(f"Scenario file not found: {scenario_path}")

    config = load_scenario_config(scenario_path)
    output_path = Path(args.output)
    generate_routes(config, output_path, override_seed=args.seed)
    print(f"Generated routes saved to {output_path}")


if __name__ == "__main__":
    main()
