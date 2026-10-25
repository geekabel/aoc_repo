# The single-link validation scenario

Task C1. The first step of the experiment E2 of the plan (theory validation, H2): one sender, one receiver,
CPMs at a known rate with a known loss. Tasks C2 and C3 compare the logs of these runs with the age law and
the expected reliability E[alpha] of the theory note (steps 3 and 4). This page describes the scenario, how to run
it on the workstation, and what to check in the result. Everything lives in `artery-ext/link`, `artery-ext/aoc`
and `aoc.analysis.link`.

## The scenario

Three cars on a straight road of 5 km, one lane, all at 10 m/s, so the distances never change:

```
receiver ........ 180 m ........ sender .. 40 m .. object          direction of travel: east
```

- The sender sees the object with its front radars (40 m, within 65 m and 150 m) and nothing else.
- The object sees the sender with its rear radars and nothing else.
- The receiver sees nothing: the sender is 180 m ahead (the radars reach 150 m), the object 220 m.

So the receiver knows the object only through the CPMs of the sender: one link, one object. The CPMs of the
object about the sender give a second link of the same kind, and the sender also hears the object. Every car
runs the same services (CAM, and the CP service below), so the result does not depend on which SUMO car
Artery maps to which module. The cars enter one second apart (object, sender, receiver), and the departure
positions take these seconds into account.

The road is built on the workstation by `netconvert` from two nodes given in longitude and latitude
(`link.nod.xml`, `link.edg.xml`), with a UTM projection: Artery needs one to write the reference position of
a CPM. The SUMO step is 0.1 s (`link.sumo.cfg`). The cars have no speed deviation and no driver imperfection
(`link.rou.xml`).

## The messages: a fixed period and a known loss

`AocCpService` (in `artery-ext/aoc`) extends the CP service of Artery. Each received CPM is dropped with
probability `dropProbability` before the service reads it, with the random number generator of the module, so a
seed set gives the same drops. A dropped CPM is announced with the signal `CpmDropped`, which the logger writes to
`cpm_drop.csv`. With `dropProbability = 0` the service is exactly `CpService`.

The period is fixed by the parameters of `CpService` itself: `minInterval = maxInterval = T`,
`withDccRestriction = false` (DCC cannot stretch the interval), and `objectInclusionConfig = 0` (every
perceived object is in every CPM; with the ETSI rules a car at constant speed would be sent once per second
only). The sensor information container is sent once per second.

`omnetpp.ini` has two configurations (the quick one is written as iterations of one value, so that the logger records
T and p the same way):

| Configuration | Runs | What |
| --- | --- | --- |
| `link` | 16 of 330 s | the grid of the plan: T in {1, 0.5, 0.2, 0.1} s (1, 2, 5, 10 Hz) x p in {0, 0.1, 0.3, 0.5} |
| `link_quick` | 1 of 30 s | T = 0.5 s, p = 0.3, to check the chain |

The iteration variables are written in `run.csv` by the logger and in `export_summary.json` by the exporter
(`"variables": {"T": "0.5s", "p": "0.3"}`).

## Run it on the workstation

```bash
bash c1_link.sh --quick          # one run of 30 s: the chain works
bash c1_link.sh                  # the grid: 16 runs, several at a time (15 to 40 minutes)
bash c1_link.sh --check DIR      # export again and check the runs of one result folder
```

The script installs the AoC sources in `artery/scenarios/aoc` (the same block in `scenarios/CMakeLists.txt` as
A8), builds them, writes the scenario in `artery/scenarios/aoc-link`, builds the road, runs the grid with
`C1_JOBS` runs at a time, exports every run with the exporter of A8 and checks the grid. The results go to
`~/aoc-sim/c1/results/<stamp>-link/aoc-link-N/parquet`, with `~/aoc-sim/c1/latest` pointing to the last one,
and the table of the grid in `c1_grid.csv`.

## The check

```bash
aoc-link RESULT_FOLDER --grid --single-link
```

One row per run, and PASS when every run is what its settings say:

| Check | Condition |
| --- | --- |
| the period | the median time between two CPMs of a sender is T, within 1% |
| the loss | the share of the dropped copies, among the copies that arrived or were dropped, is p within three standard errors (plus 0.005) |
| the object | the receiver got rows about an object its own sensors never saw, and the share of the sender's CPMs that reached it is at least 0.9 (1 - p) - 0.05 |
| the single link | with `--single-link`: the receivers' own sensors see nothing |
| the grid | with `--grid`: the 16 cells are there, once each |

On a synthetic grid of 16 runs (`aoc.io.synthetic`, the same three cars, delay 0.02 s), every check passes and
the table reads as expected: period exactly T, loss within 0.03 of p, delay 0.020 s, age at reception 0.020 s.
The tests of `tests/test_link.py` build this grid and also feed the check with lying settings (a wrong T, a
wrong p, a missing cell, a receiver that sees something) to make sure each one is caught.

## What to expect from the real runs

- The age at reception of the object: the delay of the radio plus the time since the sender's last radar step
  (0 to 0.1 s). Volk et al. measured 119 to 133 ms end to end; the Artery example gave a median of 0.08 s.
- The delivery share read on the delivered rows (`pdr_window`) is above 1 - p: it is read on delivered messages
  only, so its window always holds one success.
- With p = 0.5 and T = 1 s the receiver gets about 165 CPMs in 330 s; the age at a random time then reaches
  several seconds. This is the regime where the age law matters most (C2).

## Limits

The loss is drawn at the receiver, independently per copy: the i.i.d. loss of the theory note, not a radio
model. The radio itself loses nothing at 180 m on a free road. One seed per cell; C2 may ask for more.
The C++ part is compiled on the workstation only: this repository holds the sources and the script, and the
quick run is the test.
