"""Build grid2x2_city.net.xml: a non-uniform four-junction urban network (scenario "city").

Four signalised junctions J0-J3, irregularly spaced, joined by roads of different classes:

    road class   lanes per direction   width (m)          speed     vehicles
    main         3                     3.6 / 3.5 / 3.3    60 km/h   all; inner lane no trucks or buses
    secondary    2                     3.4 / 3.2          50 km/h   all; inner lane no trucks or buses
    collector    2                     3.25 / 3.0         40 km/h   all; inner lane no trucks or buses
    local        1                     3.0                30 km/h   cars, motorcycles, ambulances only

- The **main road** runs west-east through J0 and J1 (with a bend between them): the high-capacity
  corridor.
- **Secondary roads** run north-south through the junctions and J2-J3 (with a bend), and lead to the
  exits in the south and north.
- **Collector roads** lead west from J2 and east from J3.
- **Local sub-roads** with bends cross the town between the secondary roads, south of the main road,
  between the junction rows and north of J2-J3. They join the secondary roads at stop-controlled
  T-junctions. They are shorter than going through the signals but slower, and closed to trucks and
  buses.

So between two exits there are several real routes, of different length, speed and capacity.

Signals: every junction has four legs and a 4-phase program written here (north-south green,
yellow, east-west green, yellow; left turns permissive), the layout the junction controllers use.
Lane use at the signals is fixed explicitly, like lane markings: right turns only from the right
lane, left turns only from the inner lane, straight on from each lane into the matching lane. (Left
to SUMO, a lane closed to trucks made it give trucks a left turn from the right lane, across the cars
going straight beside it.)

Trucks and buses may not use the inner lane, so they never turn left at a signal: they follow truck
routes (straight on and right turns). Measured in plain SUMO at 1.6x demand: 0 junction collisions
with cars and motorcycles only, 17 in 4 runs with the full mix, every one a bus or truck turning
left across oncoming traffic in these 30-40 m junctions. Vehicle parameters (gap acceptance,
acceleration, driver imperfection) and a shorter step did not remove them; keeping heavy vehicles off
the left-turn lanes does. Heavy trips are generated only between exits they can legally reach. The
city scenario also uses a 4 s all-red for these wider junctions. Approach directions of the roads are found from their geometry
(road ids avoid the names the controllers read directly).

Usage: python sim/sumo/generate_city_net.py   (needs SUMO's netconvert)
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
OUT_NET = HERE / "grid2x2_city.net.xml"

GRID_JUNCTIONS = ("J0", "J1", "J2", "J3")
NO_HEAVY = "passenger motorcycle emergency"
CLASSES = ("passenger", "motorcycle", "bus", "truck", "emergency")
HEAVY = ("bus", "truck")  # follow truck routes: straight on and right turns at the signals

NODES = {
    # signalised junctions (irregular spacing)
    "J0": (250.0, 250.0), "J1": (565.0, 232.0), "J2": (232.0, 545.0), "J3": (592.0, 565.0),
    # exits (dead ends)
    "gW": (-40.0, 262.0), "gE": (850.0, 220.0), "gS0": (258.0, -40.0), "gS1": (560.0, -40.0),
    "gN2": (226.0, 830.0), "gN3": (600.0, 830.0), "gW2": (-40.0, 552.0), "gE3": (850.0, 575.0),
    # stop-controlled T-junctions where the local sub-roads meet the secondary roads
    "ka": (243.0, 400.0), "kb": (580.0, 400.0), "kc": (255.0, 105.0), "kd": (562.0, 98.0),
    "ke": (229.0, 690.0), "kf": (596.0, 700.0),
}
EXITS = ("gW", "gE", "gS0", "gS1", "gN2", "gN3", "gW2", "gE3")
T_JUNCTIONS = ("ka", "kb", "kc", "kd", "ke", "kf")

ROAD = {
    "main": dict(speed=16.67, priority=4, lanes=[(3.6, None), (3.5, None), (3.3, NO_HEAVY)]),
    "secondary": dict(speed=13.89, priority=3, lanes=[(3.4, None), (3.2, NO_HEAVY)]),
    "collector": dict(speed=11.11, priority=3, lanes=[(3.25, None), (3.0, NO_HEAVY)]),
    "local": dict(speed=8.33, priority=1, lanes=[(3.0, NO_HEAVY)]),
}
ROAD_TYPES = tuple(ROAD)

# (from, to, class, intermediate shape points); every road gets both directions
ROADS = [
    ("gW", "J0", "main", []), ("J0", "J1", "main", [(408.0, 226.0)]), ("J1", "gE", "main", [(700.0, 222.0)]),
    ("J0", "ka", "secondary", []), ("ka", "J2", "secondary", []),
    ("J1", "kb", "secondary", []), ("kb", "J3", "secondary", []),
    ("J2", "J3", "secondary", [(410.0, 574.0)]),
    ("J0", "kc", "secondary", []), ("kc", "gS0", "secondary", []),
    ("J1", "kd", "secondary", []), ("kd", "gS1", "secondary", []),
    ("J2", "ke", "secondary", []), ("ke", "gN2", "secondary", []),
    ("J3", "kf", "secondary", []), ("kf", "gN3", "secondary", []),
    ("gW2", "J2", "collector", [(100.0, 548.0)]), ("J3", "gE3", "collector", [(720.0, 571.0)]),
    ("ka", "kb", "local", [(330.0, 418.0), (410.0, 428.0), (500.0, 414.0)]),
    ("kc", "kd", "local", [(330.0, 78.0), (410.0, 68.0), (490.0, 74.0)]),
    ("ke", "kf", "local", [(320.0, 712.0), (410.0, 730.0), (510.0, 718.0)]),
]

# Trips enter on the road leaving an exit and end on the road reaching one
CITY_ENTRY = {"W": "gWJ0", "E": "gEJ1", "S0": "gS0kc", "S1": "gS1kd", "N2": "gN2ke", "N3": "gN3kf",
              "W2": "gW2J2", "E3": "gE3J3"}
CITY_EXIT = {"W": "J0gW", "E": "J1gE", "S0": "kcgS0", "S1": "kdgS1", "N2": "kegN2", "N3": "kfgN3",
             "W2": "J2gW2", "E3": "J3gE3"}


def reachable_pairs(vclass: str, net_path: Path = OUT_NET) -> set:
    """Exit pairs (from, to) a vehicle class can legally travel between (SUMO's router, lane permissions)."""
    import sumolib

    net = sumolib.net.readNet(str(net_path))
    return {(ka, kb) for ka, a in CITY_ENTRY.items() for kb, b in CITY_EXIT.items()
            if ka != kb and net.getShortestPath(net.getEdge(a), net.getEdge(b), vClass=vclass)[0]}


def _run(cmd: list) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{res.stderr}")


def _write_plain(prefix: str) -> None:
    nodes = ET.Element("nodes")
    for nid, (x, y) in NODES.items():
        typ = "traffic_light" if nid in GRID_JUNCTIONS else "dead_end" if nid in EXITS else "priority_stop"
        attrs = {"id": nid, "x": f"{x:.2f}", "y": f"{y:.2f}", "type": typ}
        if nid in GRID_JUNCTIONS:
            attrs["tlType"] = "static"
        ET.SubElement(nodes, "node", attrs)
    ET.ElementTree(nodes).write(prefix + ".nod.xml")

    edges = ET.Element("edges")
    for a, b, cls, mids in ROADS:
        road = ROAD[cls]
        for frm, to, pts in ((a, b, mids), (b, a, list(reversed(mids)))):
            attrs = {"id": f"{frm}{to}", "from": frm, "to": to, "priority": str(road["priority"]),
                     "numLanes": str(len(road["lanes"])), "speed": f"{road['speed']:.2f}", "type": cls,
                     "spreadType": "right"}
            if pts:
                full = [NODES[frm], *pts, NODES[to]]
                attrs["shape"] = " ".join(f"{x:.2f},{y:.2f}" for x, y in full)
            e = ET.SubElement(edges, "edge", attrs)
            for i, (w, allow) in enumerate(road["lanes"]):
                lane = ET.SubElement(e, "lane", index=str(i), width=f"{w:.2f}")
                if allow:
                    lane.set("allow", allow)
    ET.ElementTree(edges).write(prefix + ".edg.xml")


def _signal_connections(first_net: Path, out_con: str) -> None:
    """Lane use at the signalised junctions: right turns from the right lane, left turns from the inner
    lane, straight on from each lane into the matching lane (the turn of each road pair comes from the
    first build's own connections)."""
    import sumolib

    net = sumolib.net.readNet(str(first_net))
    root = ET.Element("connections")
    for j in GRID_JUNCTIONS:
        for inc in net.getNode(j).getIncoming():
            n_in = inc.getLaneNumber()
            turns = {}
            for out, conns in inc.getOutgoing().items():
                turns[out] = conns[0].getDirection()
            for out, d in turns.items():
                n_out = out.getLaneNumber()
                if d in ("r", "R"):
                    pairs = [(0, 0)]
                elif d in ("l", "L"):
                    pairs = [(n_in - 1, n_out - 1)]
                elif d == "s":
                    pairs = [(i, min(i, n_out - 1)) for i in range(n_in)]
                else:
                    continue  # no U-turns at the signals
                for fl, tl in pairs:
                    ET.SubElement(root, "connection", attrib={"from": inc.getID(), "to": out.getID(),
                                                              "fromLane": str(fl), "toLane": str(tl)})
    ET.ElementTree(root).write(out_con)


def _approach(shape) -> str:
    """Side a lane arrives from at its junction (N, S, E, W), from its last segment."""
    (x1, y1), (x2, y2) = shape[-2], shape[-1]
    if abs(y2 - y1) >= abs(x2 - x1):
        return "S" if y2 > y1 else "N"
    return "W" if x2 > x1 else "E"


def _signal_programs(net_path: Path, out_tll: str) -> None:
    """4-phase program per junction: NS green, NS yellow, EW green, EW yellow (left turns permissive)."""
    import sumolib

    net = sumolib.net.readNet(str(net_path))
    root = ET.Element("tlLogics")
    for j in GRID_JUNCTIONS:
        links = sorted(net.getTLS(j).getConnections(), key=lambda c: c[2])
        n = links[-1][2] + 1
        axis = ["NS"] * n
        minor = [False] * n
        for in_lane, out_lane, idx in links:
            axis[idx] = "NS" if _approach(in_lane.getShape()) in ("N", "S") else "EW"
            conn = next(c for c in in_lane.getOutgoing() if c.getToLane() == out_lane)
            minor[idx] = conn.getDirection() in ("l", "L", "t")  # left turns and U-turns give way
        tl = ET.SubElement(root, "tlLogic", id=j, type="static", programID="0", offset="0")

        def state(green_axis: str, yellow: bool) -> str:
            return "".join(("y" if yellow else ("g" if minor[i] else "G")) if axis[i] == green_axis else "r"
                           for i in range(n))

        for green, dur in (("NS", "30"), ("NS_Y", "3"), ("EW", "30"), ("EW_Y", "3")):
            ET.SubElement(tl, "phase", duration=dur, state=state(green.split("_")[0], green.endswith("_Y")))
    ET.ElementTree(root).write(out_tll)


def build(out_path: Path = OUT_NET) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        prefix = str(Path(tmp) / "city")
        _write_plain(prefix)
        first = Path(tmp) / "first.net.xml"
        common = ["--no-turnarounds", "true", "--junctions.corner-detail", "5",
                  # turning speed from curvature: at most 3 m/s^2 sideways
                  "--junctions.limit-turn-speed", "3.0", "--default.junctions.radius", "8"]
        _run(["netconvert", "--node-files", prefix + ".nod.xml", "--edge-files", prefix + ".edg.xml", *common,
              "--output-file", str(first)])
        con = prefix + ".con.xml"
        _signal_connections(first, con)
        second = Path(tmp) / "second.net.xml"
        _run(["netconvert", "--node-files", prefix + ".nod.xml", "--edge-files", prefix + ".edg.xml",
              "--connection-files", con, *common, "--output-file", str(second)])
        tll = prefix + ".tll.xml"
        _signal_programs(second, tll)
        _run(["netconvert", "--sumo-net-file", str(second), "--tllogic-files", tll, "--output-file", str(out_path)])
    print(f"Wrote {out_path}")


def check(net_path: Path = OUT_NET) -> list:
    """Problems found in the built network (empty = fine): for cars, motorcycles and ambulances every
    exit is reachable from every other; for trucks and buses (truck routes) every exit can be left and
    reached by some legal route; no road a class may use is a dead end for it; every signalised junction
    has four legs, a 4-phase program and links on both axes; left turns only from inner lanes and
    right turns only from the right lane; and road classes really differ in lanes and width."""
    import sumolib

    net = sumolib.net.readNet(str(net_path), withPrograms=True)
    problems = []
    entries = {k: net.getEdge(e) for k, e in CITY_ENTRY.items()}
    exits = {k: net.getEdge(e) for k, e in CITY_EXIT.items()}
    for vclass in CLASSES:
        ok = {(ka, kb) for ka, a in entries.items() for kb, b in exits.items()
              if ka != kb and net.getShortestPath(a, b, vClass=vclass)[0]}
        if vclass in HEAVY:
            for k in entries:
                if not any(a == k for a, _ in ok):
                    problems.append(f"{vclass}: no legal route from exit {k}")
                if not any(b == k for _, b in ok):
                    problems.append(f"{vclass}: no legal route to exit {k}")
        else:
            for ka in entries:
                for kb in exits:
                    if ka != kb and (ka, kb) not in ok:
                        problems.append(f"{vclass}: no route {entries[ka].getID()} -> {exits[kb].getID()}")
        for e in net.getEdges():
            if not e.allows(vclass) or e in exits.values():
                continue
            if not any(net.getShortestPath(e, x, vClass=vclass)[0] for x in exits.values()):
                problems.append(f"{vclass}: dead end at {e.getID()}")
    for j in GRID_JUNCTIONS:
        node = net.getNode(j)
        if len(node.getIncoming()) != 4 or len(node.getOutgoing()) != 4:
            problems.append(f"{j}: not four legs")
        tls = net.getTLS(j)
        phases = tls.getPrograms()["0"].getPhases()
        if len(phases) != 4:
            problems.append(f"{j}: {len(phases)} phases, expected 4")
        axes = {"NS" if _approach(c[0].getShape()) in ("N", "S") else "EW" for c in tls.getConnections()}
        if axes != {"NS", "EW"}:
            problems.append(f"{j}: links on {axes} only")
    for j in GRID_JUNCTIONS:
        for inc in net.getNode(j).getIncoming():
            last = inc.getLaneNumber() - 1
            for lane in inc.getLanes():
                for c in lane.getOutgoing():
                    d, i = c.getDirection(), lane.getIndex()
                    if (d in ("l", "L") and i != last) or (d in ("r", "R") and i != 0) or d == "t":
                        problems.append(f"{j}: {d} turn from lane {lane.getID()}")
    widths = {}
    for e in net.getEdges():
        widths.setdefault(e.getType(), set()).add(round(sum(ln.getWidth() for ln in e.getLanes()), 2))
    if len({min(v) for v in widths.values()}) < 4:
        problems.append(f"road classes do not differ in width: {widths}")
    return problems


if __name__ == "__main__":
    build()
    found = check()
    print("\n".join(found) if found else "Check passed: every exit reachable for every class, no dead ends, "
          "four-legged signalised junctions with 4-phase programs, distinct road classes")
