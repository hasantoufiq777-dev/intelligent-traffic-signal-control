"""Vehicle type catalogue and factory (city network).

One place defines every vehicle category: its SUMO vehicle class (which decides the lanes it may use),
size, speeds, acceleration and HBEFA4 emission class (fuel and CO2). ``VehicleTypeFactory`` turns the
catalogue into SUMO ``vType`` elements for route files and into the summary the dashboard draws from,
so the map, the driver view and SUMO always agree on what a vehicle is.

The research networks (single, grid2x2, grid2x2_zone) keep their original two types, written by
sim/scenarios/generate_routes.py, so stored results are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
import xml.etree.ElementTree as ET

ECO_COLOR = "22,163,74"  # the single eco-vehicle colour (also --eco-color in the dashboard: #16a34a)


@dataclass(frozen=True)
class VehicleType:
    """One vehicle category. Speeds in m/s, sizes in m, accelerations in m/s^2."""

    id: str
    label: str
    vclass: str  # SUMO vehicle class: lane permissions are given per class
    length: float
    width: float
    height: float
    accel: float
    decel: float
    max_speed: float
    min_gap: float
    sigma: float  # driver imperfection (Krauss)
    emission_class: str  # HBEFA4 class: fuel and CO2 per second from speed and acceleration
    gui_shape: str
    color: Optional[str] = None
    speed_factor: str = "normc(1.0,0.05,0.85,1.0)"  # never above the speed limit
    # Junction model: smallest time gap (s) accepted when crossing or turning in front of traffic that
    # has priority (permissive left turns). Heavy, slow-accelerating vehicles need a larger gap.
    gap_accept_s: float = 1.5
    always_equipped: bool = False  # has a VANET radio whatever the penetration
    eco: bool = False  # follows GLOSA speed advice
    extra: Dict[str, str] = field(default_factory=dict)


CATALOG: Dict[str, VehicleType] = {
    "car": VehicleType("car", "Car", "passenger", 4.5, 1.8, 1.5, 2.6, 4.5, 50.0, 2.5, 0.5,
                       "HBEFA4/PC_petrol_Euro-4", "passenger"),
    "eco_car": VehicleType("eco_car", "Eco car", "passenger", 4.3, 1.8, 1.5, 2.0, 4.0, 50.0, 2.5, 0.3,
                           "HBEFA4/PC_petrol_Euro-6d", "passenger/hatchback", color=ECO_COLOR,
                           always_equipped=True, eco=True),
    "motorcycle": VehicleType("motorcycle", "Motorcycle", "motorcycle", 2.2, 0.9, 1.4, 3.5, 6.5, 33.0, 1.5, 0.5,
                              "HBEFA4/MC_4S_le250cc_Euro-5", "motorcycle"),
    "bus": VehicleType("bus", "Bus", "bus", 12.0, 2.55, 3.2, 1.2, 4.0, 22.0, 3.0, 0.3,
                       "HBEFA4/UBus_Std_gt15-18t_Euro-VI_D-E", "bus", color="250,180,20", gap_accept_s=2.5),
    "truck": VehicleType("truck", "Truck", "truck", 10.0, 2.5, 3.6, 1.0, 4.0, 25.0, 3.0, 0.4,
                         "HBEFA4/RT_gt14-20t_Euro-VI_D-E", "truck", gap_accept_s=2.5),
    "emergency": VehicleType("emergency", "Ambulance", "emergency", 6.5, 2.1, 2.6, 3.5, 6.0, 25.0, 2.0, 0.2,
                             "HBEFA4/PC_petrol_Euro-4", "emergency", color="255,0,0", speed_factor="1.3",
                             always_equipped=True),
}

DEFAULT_MIX = {"car": 0.55, "eco_car": 0.15, "motorcycle": 0.10, "bus": 0.05, "truck": 0.15}


class VehicleTypeFactory:
    """Creates SUMO vType definitions and dashboard summaries from the catalogue."""

    def __init__(self, catalog: Optional[Dict[str, VehicleType]] = None) -> None:
        self.catalog = dict(catalog or CATALOG)

    def get(self, type_id: str) -> VehicleType:
        return self.catalog[type_id]

    def vtype_element(self, parent: ET.Element, type_id: str) -> ET.Element:
        """Append the SUMO <vType> of one category to a routes element."""
        t = self.catalog[type_id]
        attrs = {
            "id": t.id, "vClass": t.vclass, "length": f"{t.length}", "width": f"{t.width}",
            "height": f"{t.height}", "accel": f"{t.accel}", "decel": f"{t.decel}",
            "emergencyDecel": f"{max(t.decel * 1.8, 7.5):.1f}", "maxSpeed": f"{t.max_speed}",
            "minGap": f"{t.min_gap}", "sigma": f"{t.sigma}", "speedFactor": t.speed_factor,
            "emissionClass": t.emission_class, "guiShape": t.gui_shape,
            "jmTimegapMinor": f"{t.gap_accept_s}", **t.extra,
        }
        if t.color:
            attrs["color"] = t.color
        return ET.SubElement(parent, "vType", attrs)

    def vtype_elements(self, parent: ET.Element, type_ids: Iterable[str]) -> None:
        for tid in type_ids:
            self.vtype_element(parent, tid)

    def summary(self, type_ids: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        """What the dashboard needs to draw and describe each category."""
        out = []
        for tid in type_ids or self.catalog:
            t = self.catalog[tid]
            out.append({"id": t.id, "label": t.label, "vclass": t.vclass, "length": t.length, "width": t.width,
                        "height": t.height, "max_speed": t.max_speed, "accel": t.accel, "decel": t.decel,
                        "emission_class": t.emission_class, "eco": t.eco, "always_equipped": t.always_equipped})
        return out


DRIVER_PREFIX_TYPE = "driven"  # "driven" = a car; "driven_<category>" = another category


def is_driven_type(vtype_id: str) -> bool:
    """The vehicle a dashboard viewer drives (sim/driver.py), of any category."""
    v = str(vtype_id or "")
    return v == DRIVER_PREFIX_TYPE or v.startswith(DRIVER_PREFIX_TYPE + "_")


def driven_type_id(category: str) -> str:
    return DRIVER_PREFIX_TYPE if category == "car" else f"{DRIVER_PREFIX_TYPE}_{category}"


def type_category(vtype_id: str) -> str:
    """Category shown on the map for a SUMO type id (unknown types count as cars). A driven vehicle
    has the category it was given (the map marks it as driven from its flags)."""
    if vtype_id in CATALOG:
        return vtype_id
    if is_driven_type(vtype_id):
        rest = str(vtype_id)[len(DRIVER_PREFIX_TYPE) + 1:]
        return rest if rest in CATALOG else "car"
    return "car"


def is_always_equipped(vtype_id: str) -> bool:
    t = CATALOG.get(vtype_id)
    return bool(t and t.always_equipped)


def is_eco_type(vtype_id: str) -> bool:
    t = CATALOG.get(vtype_id)
    return bool(t and t.eco)
