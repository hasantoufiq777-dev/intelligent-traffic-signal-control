"""Route generator for SUMO simulation scenarios.

Generates routes.rou.xml from scenario YAML configurations for both
'single' junction and 'grid2x2' networks with deterministic random seeds.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Tuple
import xml.dom.minidom
import xml.etree.ElementTree as ET
import numpy as np
import yaml

ROUTES_DEF_SINGLE = {
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

GRID_DEFAULT_OD_ROUTES: Dict[Tuple[str, str], List[str]] = {
    ("bottom0J0", "top1J3"): ["bottom0J0", "J0J1", "J1J3", "J3top1"],
    ("bottom1J1", "top0J2"): ["bottom1J1", "J1J3", "J3J2", "J2top0"],
    ("left0J0", "right1J3"): ["left0J0", "J0J1", "J1J3", "J3right1"],
    ("left1J2", "right0J1"): ["left1J2", "J2J0", "J0J1", "J1right0"],
    ("top0J2", "bottom1J1"): ["top0J2", "J2J0", "J0J1", "J1bottom1"],
    ("top1J3", "bottom0J0"): ["top1J3", "J3J2", "J2J0", "J0bottom0"],
    ("right0J1", "left1J2"): ["right0J1", "J1J3", "J3J2", "J2left1"],
    ("right1J3", "left0J0"): ["right1J3", "J3J2", "J2J0", "J0left0"],
    ("bottom0J0", "top0J2"): ["bottom0J0", "J0J2", "J2top0"],
    ("bottom1J1", "top1J3"): ["bottom1J1", "J1J3", "J3top1"],
    ("left0J0", "right0J1"): ["left0J0", "J0J1", "J1right0"],
    ("left1J2", "right1J3"): ["left1J2", "J2J3", "J3right1"],
}


def load_scenario_config(scenario_path: Path) -> Dict[str, Any]:
    """Load scenario configuration from a YAML file."""
    with open(scenario_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def generate_single_routes(
    config: Dict[str, Any],
    rng: np.random.Generator,
    duration: float,
) -> List[Dict[str, Any]]:
    traffic_cfg = config.get("traffic", {})
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

    for direction, rate_per_hour in rates.items():
        if rate_per_hour <= 0:
            continue
        rate_per_sec = rate_per_hour / 3600.0
        curr_time = float(rng.exponential(1.0 / rate_per_sec))
        veh_idx = 0
        while curr_time < duration:
            turn = rng.choice(turn_options, p=turn_probs)
            edges = ROUTES_DEF_SINGLE[direction][str(turn)]
            route_str = f"{edges[0]} {edges[1]}"
            vehicles.append({
                "id": f"{direction}_{veh_idx}",
                "type": "car",
                "depart": round(curr_time, 2),
                "route": route_str,
            })
            veh_idx += 1
            curr_time += float(rng.exponential(1.0 / rate_per_sec))

    return vehicles


def generate_grid_routes(
    config: Dict[str, Any],
    rng: np.random.Generator,
    duration: float,
) -> List[Dict[str, Any]]:
    traffic_cfg = config.get("traffic", {})
    od_flows = traffic_cfg.get("od_flows", [])

    # If explicit od_flows are not defined, use default pairs with scaled rates
    if not od_flows:
        base_rate = float(traffic_cfg.get("rate_per_od", 120.0))
        od_flows = [
            {"from": o, "to": d, "rate": base_rate}
            for (o, d) in GRID_DEFAULT_OD_ROUTES.keys()
        ]

    vehicles: List[Dict[str, Any]] = []

    for idx, flow in enumerate(od_flows):
        o_edge = flow["from"]
        d_edge = flow["to"]
        rate_per_hour = float(flow.get("rate", 120.0))
        if rate_per_hour <= 0:
            continue

        route_edges = GRID_DEFAULT_OD_ROUTES.get((o_edge, d_edge), [o_edge, d_edge])
        route_str = " ".join(route_edges)

        rate_per_sec = rate_per_hour / 3600.0
        curr_time = float(rng.exponential(1.0 / rate_per_sec))
        veh_idx = 0
        while curr_time < duration:
            vehicles.append({
                "id": f"g{idx}_{veh_idx}",
                "type": "car",
                "depart": round(curr_time, 2),
                "route": route_str,
            })
            veh_idx += 1
            curr_time += float(rng.exponential(1.0 / rate_per_sec))

    return vehicles


def generate_routes(
    config: Dict[str, Any],
    output_path: Path,
    override_seed: int | None = None,
    network_override: str | None = None,
) -> None:
    """Generate SUMO route XML based on scenario parameters."""
    traffic_cfg = config.get("traffic", {})
    sim_cfg = config.get("simulation", {})
    duration = float(sim_cfg.get("duration", 1000.0))
    seed = override_seed if override_seed is not None else int(traffic_cfg.get("seed", 1))
    network = network_override or config.get("network", "single")

    rng = np.random.default_rng(seed)

    if network == "grid2x2":
        vehicles = generate_grid_routes(config, rng, duration)
    else:
        vehicles = generate_single_routes(config, rng, duration)

    # Add emergency vehicles if configured
    emergency_cfg = config.get("emergency", {})
    if emergency_cfg.get("enabled", False):
        ev_list = emergency_cfg.get("vehicles", [])
        for ev in ev_list:
            ev_id = ev.get("id", "amb1")
            depart_time = float(ev.get("depart", 120.0))
            if network == "grid2x2":
                from_edge = ev.get("from_edge", "bottom0J0")
                to_edge = ev.get("to_edge", "top1J3")
                if from_edge in ("S2J0", "N2J0", "E2J0", "W2J0"):
                    from_edge = "bottom0J0"
                if to_edge in ("J02S", "J02N", "J02E", "J02W"):
                    to_edge = "top1J3"
                route_edges = GRID_DEFAULT_OD_ROUTES.get((from_edge, to_edge), [from_edge, to_edge])
                route_str = " ".join(route_edges)
            else:
                from_edge = ev.get("from_edge", "S2J0")
                to_edge = ev.get("to_edge", "J02N")
                route_str = f"{from_edge} {to_edge}"

            vehicles.append({
                "id": ev_id,
                "type": "emergency",
                "depart": round(depart_time, 2),
                "route": route_str,
            })

    # Sort strictly by departure time
    vehicles.sort(key=lambda v: (v["depart"], v["id"]))

    # Build XML
    root = ET.Element("routes")
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set("xsi:noNamespaceSchemaLocation", "http://sumo.dlr.de/xsd/routes_file.xsd")

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
        help="Scenario name or YAML path",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed override")
    parser.add_argument("--network", type=str, default=None, help="Network override (single | grid2x2)")
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
    generate_routes(config, output_path, override_seed=args.seed, network_override=args.network)
    print(f"Generated routes saved to {output_path}")


if __name__ == "__main__":
    main()
