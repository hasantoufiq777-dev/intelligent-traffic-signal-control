"""Build grid2x2_city.net.xml: the 2x2 signalised grid inside a small city with route alternatives.

The four signalised junctions J0-J3 and the roads between them keep exactly the connections and
signal programs of grid2x2 (the junction controllers assume four legs). Around them:

- **Arterial ring** (60 km/h, 2 wide lanes, all vehicles) joining the eight outer nodes of the grid
  (bottom0, bottom1, right0, ...) and bending round four corners SW, SE, NE, NW. City trips start
  and end on the ring roads at the corners (the corners are plain bends: nothing crosses there).
- **Bus lanes** on the south and north ring sides: the right lane is for buses and emergency
  vehicles only.
- **Residential short-cuts** (30 km/h, 1 narrow lane, no trucks or buses) cutting each corner, for
  example left0 <-> bottom0. They are shorter than the way round the corner but slower.
- Turning speeds follow curvature (at most 3 m/s^2 sideways) and corners have realistic radii, so
  10-12 m trucks and buses do not cut into neighbouring paths.

So between two corners there are several routes: through the signalised grid (short, signals, often
congested), round the ring (longer, faster, no signals), or through a residential short-cut
(shorter, slower, closed to large vehicles).

Usage: python sim/sumo/generate_city_net.py   (needs SUMO's netconvert)
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
BASE_NET = HERE / "grid2x2.net.xml"
OUT_NET = HERE / "grid2x2_city.net.xml"

GRID_JUNCTIONS = ("J0", "J1", "J2", "J3")
OUTER = ("bottom0", "bottom1", "right0", "right1", "top1", "top0", "left1", "left0")  # grid's outer nodes
CORNERS = {"SW": (0.0, 0.0), "SE": (750.0, 0.0), "NE": (750.0, 750.0), "NW": (0.0, 750.0)}

# Where city trips enter (ring road leaving a corner) and leave (ring road arriving at a corner)
CITY_ENTRY = {"SW": "SWbottom0", "SE": "SEright0", "NE": "NEtop1", "NW": "NWleft1"}
CITY_EXIT = {"SW": "left0SW", "SE": "bottom1SE", "NE": "right1NE", "NW": "top0NW"}

# Road types: speed (m/s), priority, lanes as (width m, allow or None = all classes)
ARTERIAL = dict(speed=16.67, priority=3, lanes=[(3.75, None), (3.5, None)])
BUS_ARTERIAL = dict(speed=16.67, priority=3, lanes=[(3.75, "bus emergency"), (3.5, None)])
RESIDENTIAL = dict(speed=8.33, priority=1, lanes=[(3.0, "passenger motorcycle emergency")])
# Inside the grid: the wider outer lane takes every vehicle; the inner lane (left turns) only cars,
# motorcycles and ambulances. A bus or truck waiting inside a junction to turn left needs about 7 s to
# clear it from standstill, longer than yellow + all-red (5 s), so the cross traffic would get green
# while it is still there: no left turns for heavy vehicles at the signals.
GRID_LANES = [(3.5, None), (3.25, "passenger motorcycle emergency")]
RADIUS = {"corner": 30.0, "outer": 12.0}  # turning radius (m); J0-J3 keep grid2x2's geometry

# Ring, counter-clockwise; each pair gets both directions
RING = [("SW", "bottom0"), ("bottom0", "bottom1"), ("bottom1", "SE"), ("SE", "right0"), ("right0", "right1"),
        ("right1", "NE"), ("NE", "top1"), ("top1", "top0"), ("top0", "NW"), ("NW", "left1"),
        ("left1", "left0"), ("left0", "SW")]
BUS_SIDES = {("bottom0", "bottom1"), ("top1", "top0")}
SHORTCUTS = [("left0", "bottom0"), ("bottom1", "right0"), ("right1", "top1"), ("top0", "left1")]

# Road type of each edge, for the map (written as the edge's "type" attribute)
ROAD_TYPES = ("grid", "arterial", "bus_arterial", "residential")
CLASSES = ("passenger", "motorcycle", "bus", "truck", "emergency")


def _run(cmd: list) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{res.stderr}")


def _add_edge(root: ET.Element, eid: str, frm: str, to: str, road: dict, rtype: str) -> None:
    e = ET.SubElement(root, "edge", id=eid, attrib={"from": frm, "to": to}, priority=str(road["priority"]),
                      numLanes=str(len(road["lanes"])), speed=f"{road['speed']:.2f}", type=rtype)
    for i, (width, allow) in enumerate(road["lanes"]):
        lane = ET.SubElement(e, "lane", index=str(i), width=f"{width:.2f}")
        if allow:
            lane.set("allow", allow)


def build(out_path: Path = OUT_NET) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        prefix = str(Path(tmp) / "plain")
        _run(["netconvert", "--sumo-net-file", str(BASE_NET), "--plain-output-prefix", prefix])

        nodes = ET.parse(prefix + ".nod.xml")
        nroot = nodes.getroot()
        for nid, (x, y) in CORNERS.items():
            ET.SubElement(nroot, "node", id=nid, x=f"{x:.2f}", y=f"{y:.2f}", type="priority",
                          radius=str(RADIUS["corner"]))
        # Turning radii: buses and trucks (10-12 m) cut tight corners with their rear and touch vehicles
        # on the neighbouring path, so corners get realistic radii (SUMO checks junction collisions)
        for n in nroot.findall("node"):
            nid = n.get("id")
            if nid in OUTER:
                n.set("type", "priority")  # were dead ends; now ring junctions
                n.set("radius", str(RADIUS["outer"]))
            # J0-J3 keep grid2x2's size: a larger junction is longer to cross, and the signals' 2 s
            # all-red would no longer clear it
        nodes.write(prefix + ".nod.xml")

        edges = ET.parse(prefix + ".edg.xml")
        eroot = edges.getroot()
        for e in eroot.findall("edge"):
            e.set("priority", "2")
            e.set("type", "grid")
            e.attrib.pop("width", None)
            for old in e.findall("lane"):
                e.remove(old)
            for i, (width, allow) in enumerate(GRID_LANES):
                lane = ET.SubElement(e, "lane", index=str(i), width=f"{width:.2f}")
                if allow:
                    lane.set("allow", allow)
        for a, b in RING:
            road, rtype = (BUS_ARTERIAL, "bus_arterial") if (a, b) in BUS_SIDES else (ARTERIAL, "arterial")
            _add_edge(eroot, f"{a}{b}", a, b, road, rtype)
            _add_edge(eroot, f"{b}{a}", b, a, road, rtype)
        for a, b in SHORTCUTS:
            _add_edge(eroot, f"{a}{b}", a, b, RESIDENTIAL, "residential")
            _add_edge(eroot, f"{b}{a}", b, a, RESIDENTIAL, "residential")
        edges.write(prefix + ".edg.xml")

        # Keep the grid junctions' connections; drop the old dead-end turnarounds at the outer nodes,
        # so netconvert connects the grid's outer roads to the ring
        con = ET.parse(prefix + ".con.xml")
        croot = con.getroot()
        to_outer = {e.get("id") for e in eroot.findall("edge") if e.get("to") in OUTER}
        for c in list(croot.findall("connection")):
            if c.get("from") in to_outer:
                croot.remove(c)
        con.write(prefix + ".con.xml")

        _run([
            "netconvert",
            "--node-files", prefix + ".nod.xml",
            "--edge-files", prefix + ".edg.xml",
            "--connection-files", prefix + ".con.xml",
            "--tllogic-files", prefix + ".tll.xml",
            "--no-turnarounds", "true",
            "--junctions.corner-detail", "5",
            # turning speed from curvature: at most 3 m/s^2 sideways (tight corners are taken slowly)
            "--junctions.limit-turn-speed", "3.0",
            "--output-file", str(out_path),
        ])
    print(f"Wrote {out_path}")


def check(net_path: Path = OUT_NET) -> list:
    """Problems found in the built network (empty = fine): every corner reaches every other corner
    for each vehicle class, every road a class may use leads on to a corner exit (no dead ends), and
    the signalised junctions keep grid2x2's link order."""
    import sumolib

    net = sumolib.net.readNet(str(net_path))
    problems = []
    entries = {c: net.getEdge(e) for c, e in CITY_ENTRY.items()}
    exits = {c: net.getEdge(e) for c, e in CITY_EXIT.items()}
    for vclass in CLASSES:
        for ca, a in entries.items():
            for cb, b in exits.items():
                if ca != cb and not net.getShortestPath(a, b, vClass=vclass)[0]:
                    problems.append(f"{vclass}: no route {a.getID()} -> {b.getID()}")
        for e in net.getEdges():
            if e.getID().startswith(":") or not e.allows(vclass) or e in exits.values():
                continue
            if not any(net.getShortestPath(e, x, vClass=vclass)[0] for x in exits.values()):
                problems.append(f"{vclass}: dead end at {e.getID()}")
    base = sumolib.net.readNet(str(BASE_NET))

    def links(n, j):  # (in lane, out lane, link index), in signal order
        return sorted(((c[0].getID(), c[1].getID(), c[2]) for c in n.getTLS(j).getConnections()), key=lambda x: x[2])

    for j in GRID_JUNCTIONS:
        if links(base, j) != links(net, j):
            problems.append(f"{j}: signal link order differs from grid2x2")
    return problems


if __name__ == "__main__":
    build()
    found = check()
    print("\n".join(found) if found else "Check passed: every corner reachable for every class, no dead ends, "
          "signal links as in grid2x2")
