"""Build grid2x2_zone.net.xml: the 2x2 grid with an accident work zone on the J2-J3 road.

The road between J2 (x=250) and J3 (x=500) is split at ZW (x=340) and ZE (x=420); the 80 m between
them is the zone. Edge ids (each direction keeps its original id for the part that starts at its
junction, so routes only need the split road expanded, see sim/scenarios/accident_zone.py):

    eastbound  J2J3   J2 -> ZW     J2J3.z  ZW -> ZE (zone)    J2J3.e  ZE -> J3
    westbound  J3J2   J3 -> ZE     J3J2.z  ZE -> ZW (zone)    J3J2.w  ZW -> J2
    contraflow J3J2.cf ZE -> ZW, one lane drawn on the eastbound outer lane (J2J3.z_0)

J3J2.cf is only used while an accident blocks the westbound carriageway: westbound cars then share
the eastbound outer lane, one direction at a time. ZW and ZE are traffic lights driven by the zone
controller (sim/controllers/zone.py), not by the junction controllers. Outside an accident they show
green for normal traffic and red for the contraflow lane.

Usage: python sim/sumo/generate_zone_net.py   (needs SUMO's netconvert)
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
BASE_NET = HERE / "grid2x2.net.xml"
OUT_NET = HERE / "grid2x2_zone.net.xml"

ZW_X, ZE_X, ROAD_Y = 340.0, 420.0, 500.0
SHARED_LANE_Y = 495.2  # centre line of J2J3_0, the eastbound outer lane


def _run(cmd: list) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{res.stderr}")


def build(out_path: Path = OUT_NET) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        prefix = str(Path(tmp) / "plain")
        _run(["netconvert", "--sumo-net-file", str(BASE_NET), "--plain-output-prefix", prefix])

        nodes = ET.parse(prefix + ".nod.xml")
        nroot = nodes.getroot()
        for nid, x in (("ZW", ZW_X), ("ZE", ZE_X)):
            ET.SubElement(nroot, "node", id=nid, x=f"{x:.2f}", y=f"{ROAD_Y:.2f}", type="traffic_light",
                          tlType="static")
        nodes.write(prefix + ".nod.xml")

        edges = ET.parse(prefix + ".edg.xml")
        eroot = edges.getroot()
        template = None
        for e in list(eroot):
            if e.get("id") in ("J2J3", "J3J2"):
                template = dict(e.attrib)
                eroot.remove(e)
        assert template is not None, "J2J3/J3J2 not found in the base network"

        def edge(eid: str, frm: str, to: str, lanes: int = 2, **extra: str) -> None:
            attrs = {k: v for k, v in template.items() if k not in ("id", "from", "to", "numLanes", "shape")}
            attrs.update(id=eid, numLanes=str(lanes), **extra)
            attrs["from"], attrs["to"] = frm, to
            ET.SubElement(eroot, "edge", attrs)

        edge("J2J3", "J2", "ZW")
        edge("J2J3.z", "ZW", "ZE")
        edge("J2J3.e", "ZE", "J3")
        edge("J3J2", "J3", "ZE")
        edge("J3J2.z", "ZE", "ZW")
        edge("J3J2.w", "ZW", "J2")
        edge("J3J2.cf", "ZE", "ZW", lanes=1, spreadType="center",
             shape=f"{ZE_X:.2f},{SHARED_LANE_Y:.2f} {ZW_X:.2f},{SHARED_LANE_Y:.2f}")
        edges.write(prefix + ".edg.xml")

        # Connections at the zone nodes only (netconvert computes all other junctions as before)
        con = ET.Element("connections")
        links = [
            # ZE: eastbound out of the zone, westbound into the zone or into the contraflow lane
            ("J2J3.z", 0, "J2J3.e", 0), ("J2J3.z", 1, "J2J3.e", 1),
            ("J3J2", 0, "J3J2.z", 0), ("J3J2", 1, "J3J2.z", 1),
            # Only the inner westbound lane feeds the contraflow lane: cars line up in one lane before
            # the entry line (two lanes merging inside the junction caused emergency stops)
            ("J3J2", 1, "J3J2.cf", 0),
            # ZW: eastbound into the zone, westbound out of the zone or out of the contraflow lane
            ("J2J3", 0, "J2J3.z", 0), ("J2J3", 1, "J2J3.z", 1),
            ("J3J2.z", 0, "J3J2.w", 0), ("J3J2.z", 1, "J3J2.w", 1),
            ("J3J2.cf", 0, "J3J2.w", 0),
        ]
        for frm, fl, to, tl in links:
            ET.SubElement(con, "connection", {"from": frm, "to": to, "fromLane": str(fl), "toLane": str(tl)})
        # No U-turns at the zone points (the grid junctions keep theirs, as in grid2x2.net.xml)
        for frm, to in (("J3J2", "J2J3.e"), ("J2J3.z", "J3J2.z"), ("J2J3.z", "J3J2.cf"),
                        ("J2J3", "J3J2.w"), ("J3J2.z", "J2J3.z"), ("J3J2.cf", "J2J3.z")):
            ET.SubElement(con, "delete", {"from": frm, "to": to})
        ET.ElementTree(con).write(prefix + ".con.xml")

        _run([
            "netconvert",
            "--node-files", prefix + ".nod.xml",
            "--edge-files", prefix + ".edg.xml",
            "--connection-files", prefix + ".con.xml",
            "--offset.disable-normalization", "true",
            "-o", str(out_path),
        ])
    print(f"Generated {out_path}", file=sys.stderr)


if __name__ == "__main__":
    build()
