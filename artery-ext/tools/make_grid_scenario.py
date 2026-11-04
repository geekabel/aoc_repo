#!/usr/bin/env python3
"""Writes the SUMO route files and configurations of the core grid (task D1, experiment E3 of the plan).

The road is the one of the occluded-VRU scenario (make_vru_scenario.py): the same intersection, buildings and
alley. On top of it, this script writes one route file per traffic density, with:

  - background traffic on the north, east and west approaches, random arrivals (Poisson) at the density's rate,
    turning straight (60%), right (20%) or left (20%), a speed deviation of 10%, and SUMO's lane-change model.
    The south approach, where the event takes place, carries the event vehicles only: so the event has the same
    geometry at every density and seed, and the density acts through the channel load and the number of
    witnesses (the cars that leave the junction southbound pass the pedestrian like the helpers do). With
    background cars on the south approach too, cars ahead of the ego stop at the zebra crossing for the
    pedestrian and the ego queues behind them (8 to 10 s late at 150 veh/h already, no arrival at 600);
  - the scripted event of C5, three times per run: a truck that comes and stops on the right lane of the south
    leg before the zebra crossing for 50 s, a pedestrian who comes out of the alley and crosses while the
    truck stands there, an ego car on the left lane, three helper cars. Each event keeps the timing of C5 and
    starts at the start of a light cycle (90 s, north-south green from 0 to 42 s), like C5 did: at 90, 180 and
    270 s (the first after the warm-up of 60 s), so the pedestrian comes out at 106.6, 196.6 and 286.6 s and
    the run lasts 330 s. The helpers come from the north during the green and pass the pedestrian while it
    crosses; with the events at another phase of the cycle, they stood at the red north of the junction and
    missed it. The ego reaches the crossing 5.2 s after the pedestrian is out (checked with SUMO alone over the
    three densities and the seeds 0 to 11), stops there for the pedestrian, and enters the junction during the
    green. The helpers a and c are inserted at 40 m on the north approach, inside the background flow: their
    insertion waits for a free spot, and the southbound cars stop beside them at the crossing, so their arrival
    (14 to 16 s after the pedestrian is out in free traffic, up to 3 s later at the high density) and their view
    of the pedestrian vary with the northern flow. The ego's approach is the same in every run.

The densities, in vehicles per hour per approach (three approaches): low 250, medium 450, high 700: about
15, 25 and 40 cars on the map after the warm-up. Checked with SUMO alone over seeds 0 to 5: the count is
stable over the run up to 700 (at 800 and above the junction saturates and the queues grow to the end). SUMO
draws the arrivals and the speed deviations from its own seed, which the OMNeT++ configuration sets from the
repetition, so every seed has its own traffic.

The control grid (task E2) uses the same road, events and densities, with the equipped cars drawn in the route
file: on each approach two flows, ``eq_<leg>`` at the share of the penetration and ``bg_<leg>`` at the rest (the
sum is the density; the superposition of two Poisson flows is a Poisson flow). The services file of the control
grid gives the CA and CP services to the ``eq_`` cars and the actors of the events by name, so that a car has V2X
(CAM and CPM together) or nothing: the ``bg_`` cars, the truck and the pedestrian are the objects collective
perception serves. One route file per density and penetration: ``control_<density>_p<pen>.rou.xml``.

The channel load of the control grid comes from load stations: static stations around the junction that only run
the load source of the extension (``AocLoadService``, no DCC on them: the other stations do not back off). The
files ``load_<n>.xml`` place ``n`` of them (Artery's ``StaticNodeManager``) on a grid of 80 m around the junction, the
closest first; ``load_0.xml`` places none. The equipped cars themselves are held by their DCC: at 10 Hz CAMs and the
CPMs they cannot add much (the check of Nov 4, 2026: 25 packets per second asked from each car raised the busy
ratio from 0.12 to 0.15 only).

    python3 artery-ext/tools/make_grid_scenario.py            writes the files into artery-ext/grid
    python3 artery-ext/tools/make_grid_scenario.py --check    writes nothing, fails if a file is out of date

Needs only the Python standard library.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "artery-ext" / "grid"

DENSITIES = {"low": 250.0, "medium": 450.0, "high": 700.0}     # vehicles per hour per approach, three approaches (about 15, 25 and 40 cars)
PENETRATIONS = (25, 100)                                           # the control grid: the share of the cars with V2X, drawn in the route file
CONTROL_DENSITIES = ("low", "high")                                # the control grid runs two of the three densities
LOAD_STATIONS = (0, 12, 24, 36)                                   # the control grid: the load stations around the junction
LOAD_GRID = 6                                                      # the stations stand on a 6 x 6 grid ...
LOAD_SPACING = 80.0                                                # ... with 80 m between them, centred on the junction
CENTRE = (300.42, 299.50)                                          # the junction C of the road (vru.net.xml)
CYCLE = 90.0                                                      # the light cycle of the junction (s), north-south green first
STARTS = (CYCLE, 2 * CYCLE, 3 * CYCLE)                            # the events start at the start of a cycle, like C5 at 0 s
EVENTS = tuple(t + 16.6 for t in STARTS)                          # the pedestrian comes out of the alley at these times (s)
DURATION = 330.0
TURNS = (("N", 0.6), ("R", 0.2), ("L", 0.2))                     # straight, right, left

# the legs: approach edges to the centre and exit edges from it, in the order north, east, south, west
LEGS = {
    "N": {"in": "N0C", "out": "CN0", "straight": "S", "right": "W", "left": "E"},
    "E": {"in": "E0C", "out": "CE0", "straight": "W", "right": "N", "left": "S"},
    "S": {"in": "S1C", "out": "CS1", "straight": "N", "right": "E", "left": "W"},
    "W": {"in": "W0C", "out": "CW0", "straight": "E", "right": "S", "left": "N"},
}
# the south leg is in two edges because of the zebra crossing at S1
APPROACH = {"N": "N0C", "E": "E0C", "S": "S0S1 S1C", "W": "W0C"}
EXIT = {"N": "CN0", "E": "CE0", "S": "CS1 S1S0", "W": "CW0"}


def route_edges(frm: str, to: str) -> str:
    return APPROACH[frm] + " " + EXIT[to]


def header(density: str, rate: float, pen: int | None = None) -> str:
    if pen is None:
        what = "<!-- Core grid of the plan (task D1), density %s: %.0f vehicles per hour on each of the north, east and west approaches," % (density, rate)
    else:
        what = ("<!-- Control grid of the plan (task E2), density %s, penetration %d %%: %.0f vehicles per hour on each of the north, east and west "
                "approaches, %.0f of them with V2X (the flows eq_*) and %.0f without (bg_*)," % (density, pen, rate, rate * pen / 100.0, rate * (100 - pen) / 100.0))
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- Written by artery-ext/tools/make_grid_scenario.py. Do not edit by hand. -->",
        what,
        "     random arrivals, 60% straight, 20% right, 20% left; the south approach carries the event vehicles only;",
        "     the event of C5 three times (started at %s s, the pedestrian out of the alley 16.6 s later). -->" % ", ".join("%.0f" % t for t in STARTS),
        "<routes>",
        '    <vType id="car" vClass="passenger" length="4.5" width="1.8" height="1.5" maxSpeed="13.89" speedFactor="1.0" speedDev="0.0"',
        '           sigma="0.0" accel="2.6" decel="4.5" minGap="2.5" tau="1.0" lcKeepRight="0" lcSpeedGain="0" lcStrategic="1"/>',
        '    <vType id="flow" vClass="passenger" length="4.5" width="1.8" height="1.5" maxSpeed="13.89" speedFactor="1.0" speedDev="0.1"',
        '           sigma="0.5" accel="2.6" decel="4.5" minGap="2.5" tau="1.0"/>',
        '    <vType id="truck" vClass="truck" length="12.0" width="2.5" height="4.0" maxSpeed="13.89" speedFactor="1.0" speedDev="0.0"',
        '           sigma="0.0" accel="1.0" decel="4.0" minGap="2.5" tau="1.0" guiShape="truck"/>',
        '    <vType id="ped" vClass="pedestrian" length="0.5" width="0.5" height="1.7" maxSpeed="1.4" speedFactor="1.0" speedDev="0.0"/>',
        "",
        '    <route id="northbound" edges="S0S1 S1C CN0"/>',
        '    <route id="southbound" edges="N0C CS1 S1S0"/>',
    ])


FLOW_LEGS = ("N", "E", "W")                                       # the south approach carries the event vehicles only


def distributions() -> list:
    lines = ["", "    <!-- the background traffic: one route distribution per approach with a flow -->"]
    for leg, info in LEGS.items():
        if leg not in FLOW_LEGS:
            continue
        lines.append('    <routeDistribution id="from_%s">' % leg)
        for turn, share in TURNS:
            to = info["straight"] if turn == "N" else (info["right"] if turn == "R" else info["left"])
            lines.append('        <route id="%s_%s" edges="%s" probability="%.1f"/>' % (leg, to, route_edges(leg, to), share))
        lines.append("    </routeDistribution>")
    return lines


def event_vehicles(k: int, t: float) -> list:
    """The actors of one event, from the timing of C5 (the pedestrian out of the alley at t)."""
    tag = "_%d" % k
    car = '    <vehicle id="%s%s" type="car" route="%s" depart="%.1f" departLane="2" departPos="%.1f" departSpeed="13.89"/>'
    return [
        (t - 45.0, '    <vehicle id="truck%s" type="truck" route="northbound" depart="%.1f" departLane="1" departPos="0.0" departSpeed="13.89">'
                   '<stop lane="S0S1_1" endPos="246.8" duration="50"/></vehicle>' % (tag, t - 45.0)),
        (t - 16.6, '    <person id="ped%s" type="ped" depart="%.1f" departPos="20.0"><walk from="alley" to="S1S0" arrivalPos="80.0"/></person>' % (tag, t - 16.6)),
        (t - 16.6, car % ("helper_a", tag, "southbound", t - 16.6, 40.0)),
        (t - 16.1, car % ("helper_b", tag, "northbound", t - 16.1, 90.0)),
        (t - 13.1, car % ("ego", tag, "northbound", t - 13.1, 0.0)),
        (t - 11.6, car % ("helper_c", tag, "southbound", t - 11.6, 40.0)),
    ]


def flows(rate: float, pen: int | None = None) -> list:
    """The background flows, random arrivals at the rate of the density on each of the north, east and west approaches,
    from time 0 to the end. With a penetration, two flows per approach: the cars with V2X (eq_, the share of the
    penetration) and the cars without (bg_, the rest); a flow with a rate of 0 is left out."""
    parts = [("bg", rate)] if pen is None else [("eq", rate * pen / 100.0), ("bg", rate * (100 - pen) / 100.0)]
    lines = []
    for leg in FLOW_LEGS:
        for kind, part in parts:
            if part <= 0.0:
                continue
            lines.append((0.0, '    <flow id="%s_%s" type="flow" route="from_%s" begin="0" end="%.0f" period="exp(%.5f)" departLane="best" departSpeed="max"/>'
                          % (kind, leg, leg, DURATION, part / 3600.0)))
    return lines


def routes_xml(density: str, pen: int | None = None) -> str:
    rate = DENSITIES[density]
    items = flows(rate, pen)
    for k, t in enumerate(EVENTS, start=1):
        items.extend(event_vehicles(k, t))
    items.sort(key=lambda item: item[0])                       # SUMO wants the file sorted by departure
    lines = [header(density, rate, pen)] + distributions() + ["", "    <!-- the events of C5 and the background flows, sorted by departure -->"]
    lines += [text for _, text in items]
    lines.append("</routes>")
    return "\n".join(lines) + "\n"


def sumocfg_xml(name: str) -> str:
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- Written by artery-ext/tools/make_grid_scenario.py. Do not edit by hand. -->",
        "<configuration>",
        "    <input>",
        '        <net-file value="vru.net.xml"/>',
        '        <route-files value="%s.rou.xml"/>' % name,
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


def load_stations_xml(n: int) -> str:
    """The n load stations closest to the junction on the grid (the file of Artery's StaticNodeManager)."""
    half = (LOAD_GRID - 1) / 2.0
    points = []
    for i in range(LOAD_GRID):
        for j in range(LOAD_GRID):
            x, y = CENTRE[0] + (i - half) * LOAD_SPACING, CENTRE[1] + (j - half) * LOAD_SPACING
            points.append(((x - CENTRE[0]) ** 2 + (y - CENTRE[1]) ** 2, i, j, x, y))
    points.sort()
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             "<!-- Written by artery-ext/tools/make_grid_scenario.py. Do not edit by hand. -->",
             "<!-- Control grid of the plan (task E2): %d load stations around the junction, on a grid of %.0f m, the closest first. -->" % (n, LOAD_SPACING),
             "<nodes>"]
    for _, i, j, x, y in points[:n]:
        lines.append('    <rsu id="load_%d_%d" positionX="%.2f" positionY="%.2f"/>' % (i, j, x, y))
    lines.append("</nodes>")
    return "\n".join(lines) + "\n"


def files() -> dict:
    out = {}
    for n in LOAD_STATIONS:
        out["load_%d.xml" % n] = load_stations_xml(n)
    for density in DENSITIES:
        out["grid_%s.rou.xml" % density] = routes_xml(density)
        out["grid_%s.sumo.cfg" % density] = sumocfg_xml("grid_%s" % density)
        for pen in (PENETRATIONS if density in CONTROL_DENSITIES else ()):
            name = "control_%s_p%d" % (density, pen)
            out[name + ".rou.xml"] = routes_xml(density, pen)
            out[name + ".sumo.cfg"] = sumocfg_xml(name)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write the SUMO route files and configurations of the core grid and of the control grid.")
    ap.add_argument("--check", action="store_true", help="write nothing; exit with 1 if a file is out of date")
    ap.add_argument("--out", default=str(OUT), help="output folder (default: artery-ext/grid)")
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out)
    stale = []
    for name, text in files().items():
        path = out / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            out.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print("written:", path)
    if stale:
        print("out of date:", ", ".join(stale), "- run: python3 artery-ext/tools/make_grid_scenario.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
