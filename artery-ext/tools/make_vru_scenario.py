#!/usr/bin/env python3
"""Writes the SUMO input files of the occluded-VRU scenario (task C5, scenario E1 of the plan).

The scenario is a four-leg signalized intersection with two lanes per direction and a sidewalk on each
side, a building at each corner, and a zebra crossing on the south leg, 45 m before the intersection.
An alley for pedestrians comes out of the buildings on the east side, right at the zebra crossing.
The event: a pedestrian comes out of the alley (hidden by the buildings until then) and crosses to the
west; a truck stopped on the right lane just before the crossing hides the pedestrian from the ego car,
which comes from the south on the left lane; one to three helper cars see the pedestrian (a car coming
the other way, a car ahead of the ego, a second car coming the other way) and send it in their CPMs.

Everything is placed in metres around the centre of the intersection (x east, y north), then written in
longitude and latitude so that netconvert projects the road with --proj.utm, as the single-link scenario
of C1 does (Artery needs a projection to compute the GeoNetworking positions). The buildings are written
with geo="true" for polyconvert, which uses the projection and the offset of the road file.

    python3 artery-ext/tools/make_vru_scenario.py            writes the files into artery-ext/vru
    python3 artery-ext/tools/make_vru_scenario.py --check    writes nothing, fails if a file is out of date

Needs only the Python standard library. The route file (vru.rou.xml) and the OMNeT++ configuration are
written by hand; this script writes the road, the crossing, the buildings and the SUMO configuration.
"""
import argparse
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "artery-ext" / "vru"

# the centre of the intersection, a point in the countryside west of Belfort (the same area as C1)
LON0, LAT0 = 6.9000, 47.6300
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(LAT0))

LEG = 300.0           # length of each leg in m
CROSSING_Y = -45.0    # the zebra crossing on the south leg, 45 m before the intersection
SPEED = 13.89         # 50 km/h
LANES = 2
SIDEWALK = 2.0        # width of the sidewalks in m
LANE_WIDTH = 3.2      # the default lane width of SUMO
SETBACK = 11.0        # the corner buildings start 11 m from the centre line: 2 lanes (6.4 m), a sidewalk (2 m) and a margin
BUILDING = 30.0       # side of the square buildings in m
ALLEY_LEN = 50.0      # the alley runs east from the crossing, between two buildings
ALLEY_WIDTH = 3.0
WALL = 8.6            # the two buildings along the alley start right behind the sidewalk (8.4 m from the centre line)


def lonlat(x, y):
    return LON0 + x / M_PER_DEG_LON, LAT0 + y / M_PER_DEG_LAT


def fmt(v):
    return "%.6f" % v


NODES = {
    # id: (x, y, type)
    "C": (0.0, 0.0, "traffic_light"),
    "N0": (0.0, LEG, "priority"),
    "S0": (0.0, -LEG, "priority"),
    "E0": (LEG, 0.0, "priority"),
    "W0": (-LEG, 0.0, "priority"),
    "S1": (0.0, CROSSING_Y, "priority"),
    "A": (ALLEY_LEN, CROSSING_Y, "priority"),
}

EDGES = [
    # id, from, to
    ("N0C", "N0", "C"), ("CN0", "C", "N0"),
    ("E0C", "E0", "C"), ("CE0", "C", "E0"),
    ("W0C", "W0", "C"), ("CW0", "C", "W0"),
    ("S0S1", "S0", "S1"), ("S1S0", "S1", "S0"),
    ("S1C", "S1", "C"), ("CS1", "C", "S1"),
]
ALLEY = ("alley", "A", "S1")   # pedestrians only


def nodes_xml():
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             "<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->",
             "<!-- The centre C is the intersection; S1 is the zebra crossing on the south leg, %d m before it;" % -CROSSING_Y,
             "     A is the far end of the alley that comes out at S1 from the east. -->",
             "<nodes>"]
    for nid, (x, y, kind) in NODES.items():
        lon, lat = lonlat(x, y)
        lines.append('    <node id="%s" x="%s" y="%s" type="%s"/>' % (nid, fmt(lon), fmt(lat), kind))
    lines.append("</nodes>")
    return "\n".join(lines) + "\n"


def edges_xml():
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             "<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->",
             "<!-- Two lanes per direction at 50 km/h and a sidewalk on the right of each edge (lane 0);",
             "     the alley is for pedestrians only. -->",
             "<edges>"]
    for eid, a, b in EDGES:
        lines.append('    <edge id="%s" from="%s" to="%s" priority="2" numLanes="%d" speed="%.2f" sidewalkWidth="%.1f"/>'
                     % (eid, a, b, LANES, SPEED, SIDEWALK))
    lines.append('    <edge id="%s" from="%s" to="%s" priority="1" numLanes="1" width="%.1f" speed="1.5" allow="pedestrian" spreadType="center"/>'
                 % (ALLEY + (ALLEY_WIDTH,)))
    lines.append("</edges>")
    return "\n".join(lines) + "\n"


def connections_xml():
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->",
        "<!-- The zebra crossing at S1: pedestrians have priority, cars must yield. The crossings at C are",
        "     guessed by netconvert (option crossings.guess) and controlled by the traffic light. -->",
        "<connections>",
        '    <crossing node="S1" edges="S0S1 S1S0" priority="true" width="4.0"/>',
        "</connections>",
    ]) + "\n"


def polygons_xml():
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             "<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->",
             "<!-- A square building at each corner of the intersection (%d m aside, %d m from the centre line), and two" % (BUILDING, SETBACK),
             "     buildings along the alley on the east side of the south leg, right behind the sidewalk, which hide the",
             "     alley from the road. Longitude and latitude (geo=\"true\"): polyconvert projects them with the road file. -->",
             "<additional>"]
    boxes = []
    for name, sx, sy in (("NE", 1, 1), ("NW", -1, 1), ("SW", -1, -1), ("SE", 1, -1)):
        boxes.append((name, sx * SETBACK, sy * SETBACK, sx * (SETBACK + BUILDING), sy * (SETBACK + BUILDING)))
    half = ALLEY_WIDTH / 2 + 0.5
    boxes.append(("alley_N", WALL, CROSSING_Y + half, WALL + ALLEY_LEN - 8.0, -SETBACK - 1.0))
    boxes.append(("alley_S", WALL, CROSSING_Y - half, WALL + ALLEY_LEN - 8.0, CROSSING_Y - half - BUILDING))
    for name, x0, y0, x1, y1 in boxes:
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        shape = " ".join("%s,%s" % tuple(fmt(c) for c in lonlat(x, y)) for x, y in corners)
        lines.append('    <poly id="building_%s" type="building" color="0.7,0.7,0.7" fill="true" layer="-1" geo="true" shape="%s"/>'
                     % (name, shape))
    lines.append("</additional>")
    return "\n".join(lines) + "\n"


def sumocfg_xml():
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->",
        "<configuration>",
        "    <input>",
        '        <net-file value="vru.net.xml"/>',
        '        <route-files value="vru.rou.xml"/>',
        '        <additional-files value="vru.poly.xml"/>',
        "    </input>",
        "    <time>",
        '        <begin value="0"/>',
        '        <step-length value="0.1"/>',
        "    </time>",
        "    <processing>",
        '        <collision.action value="warn"/>',
        '        <time-to-teleport value="-1"/>',
        '        <pedestrian.model value="striping"/>',
        "    </processing>",
        "    <report>",
        '        <verbose value="false"/>',
        '        <no-step-log value="true"/>',
        "    </report>",
        "</configuration>",
    ]) + "\n"


FILES = {
    "vru.nod.xml": nodes_xml,
    "vru.edg.xml": edges_xml,
    "vru.con.xml": connections_xml,
    "vru.poly.geo.xml": polygons_xml,
    "vru.sumo.cfg": sumocfg_xml,
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write the SUMO input files of the occluded-VRU scenario.")
    ap.add_argument("--check", action="store_true", help="write nothing; exit with 1 if a file is out of date")
    ap.add_argument("--out", default=str(OUT), help="output folder (default: artery-ext/vru)")
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out)
    stale = []
    for name, make in FILES.items():
        text = make()
        path = out / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            out.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print("written:", path)
    if stale:
        print("out of date:", ", ".join(stale), "- run: python3 artery-ext/tools/make_vru_scenario.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
