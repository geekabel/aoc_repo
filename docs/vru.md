# The occluded-VRU scenario

Task C5, scenario E1 of the plan. A pedestrian hidden from the ego car by a stopped truck, seen by other cars
that send it in their CPMs. It is the scenario of the fusion experiment E3 (hypotheses H1 and H3) and of the
"money figure" of the paper: the belief of the ego in the pedestrian over time, with its own sensors only and
with the CPMs. This page describes the scenario, how to run it on the workstation, and what the check reads in
the logs. Everything lives in `artery-ext/vru`, `artery-ext/config`, `artery-ext/tools/make_vru_scenario.py`
and `aoc.analysis.vru`.

## The place

A four-leg signalized intersection C, two lanes per direction at 50 km/h and a sidewalk on the right of
each edge (lane 0 of the edge in SUMO; the car lanes are 1 and 2), legs of 300 m, a square building of 30 m
at each corner. On the south leg, 45 m before the intersection, a zebra crossing (node S1) where pedestrians
have priority. An alley for pedestrians comes out of the buildings on the east side, right at the crossing,
between two buildings whose walls start just behind the sidewalk.

```
                      N0
                      |
          NW  bldg    |    bldg  NE
                      |
   W0 ----------------C---------------- E0                 north is up
                      |
          SW  bldg    |    bldg  SE
                      |      |  bldg (alley_N)
      zebra crossing  S1=====|==== alley ==== A             the pedestrian comes from A
                      |      |  bldg (alley_S)
                  truck| lane 1 (stopped, 5 m before the crossing)
                      |
                  ego | lane 2 (comes from the south)
                      |
                      S0
```

`make_vru_scenario.py` writes the road (`vru.nod.xml`, `vru.edg.xml`, `vru.con.xml`), the buildings
(`vru.poly.geo.xml`) and the SUMO configuration, in longitude and latitude around a point west of Belfort (the
same area as the single-link scenario). On the workstation `netconvert --proj.utm --crossings.guess` builds the
road with the crossings of the intersection (in the traffic light) and the zebra crossing, and `polyconvert`
places the buildings in the frame of the road. The committed copies of the five files must match the script
(a test checks it).

## The event

The route file `vru.rou.xml` puts seven actors in the place (times in s, read from a SUMO run of the file and
from the lines of sight computed on it; the Artery runs will tell the real sensor times):

| Who | What |
| --- | --- |
| `truck` | heavy truck, 12 m by 2.5 m, stopped on lane 1 of the south leg with its front 5 m before the crossing |
| `ped` | pedestrian at 1.25 m/s (SUMO's striping model), walks west in the alley, comes out on the sidewalk at about t = 17, crosses to the west (12.8 m, about 10 s), walks south on the west sidewalk |
| `ego` | passenger car on lane 2 of the south leg at 13.9 m/s; 74 m from the pedestrian when it comes out of the alley; a line of sight for about a second, then the truck hides the pedestrian for about 2.6 s (from 60 m down to 24 m); the pedestrian comes out from behind the truck in the middle of lane 1, 23 m ahead of the ego |
| `helper_a` | comes from the north on lane 2, sees the pedestrian from the alley exit on, stops at the crossing and lets it pass |
| `helper_b` | about 8 s ahead of the ego on the same lane; past the intersection when the pedestrian comes out, sees it behind itself with the lidar until it is 100 m away (it slows down at the crossing at t = 13 because SUMO sees the pedestrian coming in the alley) |
| `helper_c` | comes from the north 5 s behind `helper_a` |
| `east`, `west` | one car every 6 s each way on the east-west street; the corner buildings hide the crossing from them until they reach the stop line, from where the lidar sees it; they run no CP service |

The cars keep their lane (`lcKeepRight = 0`, `lcSpeedGain = 0`), have no driver imperfection and no speed
deviation, so the event is the same in every run; the seed of a run changes the radio only. SUMO makes the
cars brake for the pedestrian whatever their sensors see (the ego stops before the crossing): the perception is
in the logs, the driving is SUMO's. The truck sees the pedestrian too (from 15 m, with a free line) but runs
no CP service: it is the obstacle, not a helper. The event needs the north-south green of the traffic light
during its first 37 s, which is the program netconvert 1.21 writes for this intersection (the check of the
pilot reads the timeline, so another program would show as a FAIL).

Why the pedestrian is hidden, and when: the ego is on lane 2 and the truck on lane 1, 3.3 m to the right of
the ego's line. From far away the ego looks past the east side of the truck and sees the sidewalk; as it comes
closer, its line of sight to the crossing passes through the truck; once level with the rear of the truck, it
looks past the front-left corner and sees the pedestrian in lane 1. The alley hides the approach of the
pedestrian from everyone (nobody sees into it except a car right in front of it).

## Sensors and messages

The three sensors of the plan, in `sensors-vru.xml` and `omnetpp.ini`. All three are the cone sensor of
Artery with a line-of-sight check (the see-through sensor and the CAM sensor of Artery are not used): a range,
an opening angle, a mounting point, and a line-of-sight check against the buildings and the other objects (a
vehicle and a pedestrian block the view too). An object is seen when one of its four corners is in
the cone with a free line. There is no detection probability and no noise in the simulator. The three sensors
are of the Radar category in the logs (`src = radar` in the observations table); their names tell them apart
(column `sensor`).

| Name | Where | Opening | Range |
| --- | --- | --- | --- |
| `Camera` | front | 120 degrees | 80 m |
| `Radar` | front | 120 degrees (plus or minus 60) | 150 m |
| `Lidar` | front bumper | 360 degrees | 100 m |

The cones are drawn with many segments (12 and 36) so that they are round: with one segment the cone of
Artery is a triangle and the range at the middle of its far edge is `range * cos(angle / 2)`.

A detection probability per weather (the clear and rain multipliers of the plan) is not in the simulator. The
A8 decision for the noise applies: it belongs to the replay, in Python, where it can change without a new
simulation and is repeatable with a seed. It is not done yet.

The cars run the CP service of Artery with the rules of ETSI (`objectInclusionConfig = 1`, DCC on): a
pedestrian (a type A object) is sent when it is new and then every 0.5 s. The pedestrian is a plain node of
Artery without radio (`artery.envmod.PlainPerson`): an object for the sensors, not a station. Its class in the
CPM is `vru:1` (pedestrian) and its station type in the truth is 1. `omnetpp.ini` sets `ignorePersons = false`
and the person type, and names `building` as the obstacle type of the environment model.

Which cars run the CP service is set by the services file with a name filter of Artery (the pattern must
match the whole SUMO id): `services-vru1.xml` runs it on `ego` and `helper_a`, `services-vru2.xml` on `ego`,
`helper_a` and `helper_b`, `services-vru3.xml` on `ego` and the three helpers. Every other car (the truck, the
helpers left out, the traffic on the cross street) runs the CA service only, so the CPMs about the pedestrian
come from the configured helpers alone. The ego keeps the CP service to receive. Two configurations:

| Configuration | Runs | What |
| --- | --- | --- |
| `vru` | 6 of 60 s | helpers in {1, 2, 3} x 2 seeds: the pilot of the plan |
| `vru_quick` | 1 of 45 s | 2 helpers, to check the chain |

The iteration variable `helpers` is written in `run.csv` by the logger and in `export_summary.json` by the
exporter (`"variables": {"helpers": "2"}`).

## Run it on the workstation

```bash
bash c5_vru.sh --quick          # one run of 45 s: the chain works
bash c5_vru.sh                  # the pilot: 6 runs, several at a time (a few minutes)
bash c5_vru.sh --check DIR      # export again and check the runs of one result folder
```

The script installs the AoC sources (as `a8_logger.sh` does), writes the scenario in
`~/aoc-sim/artery/scenarios/aoc-vru`, builds the road and the buildings, runs the simulations, exports every run
to parquet with the exporter of A8 and runs the check below. The results are in
`~/aoc-sim/c5/results/<date>-<config>/` (also `~/aoc-sim/c5/latest`), one folder `aoc-vru-N` per run with the
CSV logs and a `parquet` folder, plus `c5_vru.csv`, one row per run.

## The check: the event in the logs

`aoc-vru RESULT_FOLDER [--pilot] [--out c5_vru.csv]` reads the exported runs and prints the timeline of the
event of each one, all read from the logs (no geometry of the scenario is assumed, so the same check works on
any variant of the scenario):

- the pedestrian: the one object of class pedestrian in the truth;
- it appears: the first time any car sees it with its own sensors (before, it is in the alley);
- the ego loses it: the first gap of at least 1.5 s in the ego's own detections of the pedestrian (the occlusion
  comes before the ego passes the pedestrian; a longer hole later, when another car hides it behind the ego,
  must not be taken for it), with the distance when lost and when seen again, and which sensors see it again;
- which cars see the pedestrian with their own sensors during the gap, and at how many steps (the truck too);
- how many CPM rows about the pedestrian the ego received during the gap, from whom, the age of the first one,
  and its class.

A run passes when: there is one pedestrian; the ego sees it with its own sensors, then loses it for 1.5 to 6 s
(the occlusion), within 100 m when lost and within 40 m when seen again; during the gap at least one of the
configured helpers sees it at four steps out of five; during the gap the ego receives at least one CPM row
about it, and these rows are of class pedestrian; the senders of the CPMs about it that the ego received over the run
are configured helpers only (the ego's own CPMs go to the other cars and do not count), and a helper whose
CP service is off sends no CPM at all. With `--pilot` the six runs must be there (1, 2, 3 helpers, two seeds each). The
command exits with 0 on PASS, 1 on FAIL, 2 when no run was found.

What the pilot shows is the "done when" of the plan for E1: the pilot runs end to end and the occlusion event
is visible in the logs. The belief of the ego over time (the money figure F2) is drawn from these logs by the
pipeline of B2 to B8 in a later task.

## Tests

`tests/test_vru.py`: the intervals and the gap by hand; a run made by hand that passes, with its timeline;
a run where a longer hole comes later; eight runs that lie (a blink instead of an occlusion, a gap too long,
no CPM during the gap, no helper seeing while the truck does, a helper with the service off that sends, the
truck sending, a wrong class, no pedestrian), each caught by the right check; the pilot of six runs with the
command and its table; the scenario files against the script that writes them; the names in the route file,
the sensors file and the configuration against each other, and the name filters of the services files applied
as Artery applies them (the ego and the configured helpers run the CP service, nobody else).
The simulation itself runs on the workstation only.
