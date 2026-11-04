# Artery side

Everything that runs with the network simulator: the logger, the config files and the scripts
for the workstation.

Versions used: OMNeT++ 5.7.1, SUMO 1.21.0, Artery commit e0ddd8c (2026-07-30), on Ubuntu 22.04.
The installation lives in `~/aoc-sim`. Before a manual run, load its settings with `source ~/aoc-sim/env.sh`.

## Folders

| Folder | Content |
| --- | --- |
| `aoc/` | The logger (`AocLogger`), the network `AocWorld` that contains it, `AocCpService`: the CP service of Artery with the AoC generation rule (task E1) and a controlled loss of received CPMs (task C1), and `cpservice-protected.patch`, the change of `CpService.h` the rule needs |
| `config/` | `services-cp.xml` (CAM and CPM services), `services-link.xml` (CAM and the lossy CP service), `services-vru1/2/3.xml` (CAM and CPM, the CP service off on some helpers), `services-grid-10/25/50/100.xml` (CAM and CPM on a share of the cars, the grid of D1), `services-control-10/25/50/100.xml` (the same with `AocCpService`, the control grid of E1 and E2), `sensors-aoc.xml` (the radars of the first AoC runs) and `sensors-vru.xml` (camera, radar and lidar of the plan) |
| `link/` | The single-link validation scenario of task C1: the road (SUMO), the three cars, the OMNeT++ configuration with the grid T x p |
| `vru/` | The occluded-VRU scenario of task C5 (E1 of the plan): the intersection, the buildings, the alley, the seven actors, the OMNeT++ configuration. The road files are written by `tools/make_vru_scenario.py` |
| `grid/` | The core grid of task D1 (E3 of the plan): the road of C5 with background traffic at three densities and the event three times per run, the OMNeT++ configuration with the factors density x penetration x message config x 12 seeds. The route files are written by `tools/make_grid_scenario.py` |
| `scripts/` | The scripts to run on the workstation |
| `tools/` | The templates of the scripts, `build_scripts.py`, which builds them, `make_vru_scenario.py`, which writes the road of the VRU scenario, and `make_grid_scenario.py`, which writes the traffic of the grid |

## The scripts

Each script is one file. Copy it to the workstation and run it with `bash`. Each one prints a report with PASS or FAIL.

| Script | What it does | Time |
| --- | --- | --- |
| `scripts/a6_install.sh` | Installs OMNeT++, SUMO and Artery for one user, then runs three test simulations | about 1 hour |
| `scripts/a7_cp_demo.sh` | Runs the Artery example with the real CP service and exports the CPM logs to CSV | 1 minute |
| `scripts/a8_logger.sh` | Adds the logger to Artery, runs the example with it, exports the logs to parquet | a few minutes the first time |
| `scripts/c1_link.sh` | Runs the single-link scenario on the grid of the plan (16 runs), exports them, checks the period and the loss of each one | 15 to 40 minutes; `--quick` for one run of 30 s |
| `scripts/c5_vru.sh` | Runs the occluded-VRU pilot (1, 2, 3 helpers x 2 seeds, 6 runs of 60 s), exports the runs, checks that the occlusion event is in the logs of each one | a few minutes; `--quick` for one run of 45 s |
| `scripts/d1_grid.sh` | Runs the core grid (432 runs of 330 s) in one fixed results folder, exports each run as it ends, packs its logs, indexes what is done; stop and start again at will, `--cell` and `--runs` for a part; `--control-quick` runs the three generation rules of `AocCpService` once (60 s each) and checks the AoC rule against the ETSI runs (task E1) | hours to a day; `--quick` for one run of 60 s |

Options are environment variables written in front of the command. `bash SCRIPT --help` lists them. The most useful ones:

```bash
A8_SIM_TIME=120s bash a8_logger.sh          # simulate 120 s (default 60 s)
A8_SENSORS=example bash a8_logger.sh        # use the sensors of the Artery example
bash a8_logger.sh --export FOLDER           # export one run again
```

`a7_cp_demo.sh`, `a8_logger.sh`, `c1_link.sh`, `c5_vru.sh` and `d1_grid.sh` hold copies of files of this repository. Do not edit them by hand.
Change the source file, then run `make scripts`.

## The logger

`AocLogger` only listens. It subscribes to four signals at the top of the network and writes CSV files.
It does not change `CpService` or any other module.

| Signal | File written | One row is |
| --- | --- | --- |
| `CpmSent` | `cpm_tx.csv`, `cpm_obj.csv` | one CPM sent, one object of this CPM |
| `CpmReceived` | `cpm_rx.csv` | one CPM received |
| `EnvironmentModel.refresh` | `truth.csv`, `local.csv` | the true state of one object, one detection by one sensor |
| `ChannelLoad` | `cbr.csv` | one channel load value of one vehicle |
| `CpmDropped` | `cpm_drop.csv` | one CPM dropped on purpose by `AocCpService` (task C1) |

The files go to `<result folder>/aoc-<config name>-<run number>/`. Two more files describe the run: `nodes.csv` and `run.csv`.

To use the logger in a scenario that is based on the environment model, set `network = AocWorld`
in its `omnetpp.ini`, or pass `--network=AocWorld` on the command line.

## The CP service of the extension

`AocCpService` extends `CpService` with two things.

**The AoC generation rule** (task E1, `generationRule = "aoc"`). The rule of Step 4 of the theory note: each
perceived object is sent with the period `T = (age limit - delay) / n`, where the age limit is the age at which the
reliability `alpha(age) = 1 - exp(-eps^2 / (2 (sigmaX^2 + sigmaV^2 age^2 + q age^3 / 3)))` of the object's class
falls to the target. The rule runs at every generation step of the service (every `minInterval`, or the DCC
interval with `withDccRestriction`): an object goes into the CPM of this step when its last report would be older
than `T` at the next step; a newly seen object goes in at once; an object slower than `stoppedSpeed` takes the curve
of a parked vehicle; the period never exceeds `maxPeriod` (the receivers forget an object after their hold). A CPM
is sent only when an object or the sensor information is due, and at least every `maxInterval`. The parameters
(`targetAlpha`, `tolerance`, `delayEstimate`, `repeats`, `sigmaX`, `sigmaV`, `qByClass`, `stoppedSpeed`, `qStopped`,
`sigmaXStopped`, `sigmaVStopped`, `maxPeriod`, `deviationFraction`, `emptyCpm`) are in `aoc/AocCpService.ned`, with the
fitted q of D3 by default. With `generationRule = "etsi"` the service is `CpService`; with `"etsi_abs"` it applies
the rules of ETSI with the position change of an object taken on its absolute position (`CpService` takes it on the
position relative to the sender, so a moving sender sends every stopped object it sees at every step). See
`docs/generation_rule.md`.

The rule overrides three methods of `CpService` that are private in Artery. `aoc/cpservice-protected.patch` makes
them protected and virtual in `src/artery/envmod/service/CpService.h` (nine lines, no change of behaviour); the
scripts apply it once and build `libartery_envmod.so` again.

**The controlled loss** (task C1). Each received CPM is dropped with probability `dropProbability` before the
service reads it, with the random number generator of the module (a seed set gives the same drops), and the signal
`CpmDropped` is emitted. With `dropProbability = 0` nothing is dropped. The single-link scenario (`link/omnetpp.ini`)
uses it with a fixed period (`minInterval = maxInterval = T`, `withDccRestriction = false`) and every perceived
object in every CPM (`objectInclusionConfig = 0`). See `docs/link.md`.

How the scripts (`a8_logger.sh`, `c1_link.sh`, `c5_vru.sh`, `d1_grid.sh`) install the sources:

1. They copy `aoc/` to `artery/scenarios/aoc`.
2. They add one block at the end of `artery/scenarios/CMakeLists.txt`. The original file is kept as `CMakeLists.txt.a8-backup`.
3. They apply `cpservice-protected.patch` to `CpService.h` (the original is kept as `CpService.h.aoc-backup`).
4. They build the library `libartery_aoc.so`, and `libartery_envmod.so` again after the patch. The rest of Artery is not built again.

## The sensors files

`config/sensors-vru.xml` holds the three sensors of the plan for the VRU runs: a camera (120 degrees, 80 m), a radar
(120 degrees, 150 m) and a lidar (360 degrees, 100 m), all front-mounted cones of Artery with a line-of-sight check;
their ranges and angles are set in `vru/omnetpp.ini`. See `docs/vru.md`.

`config/sensors-aoc.xml` keeps the four radars of the Artery example. It leaves out two sensors:

- the CAM sensor, which puts every vehicle known from its CAMs into the local model, and so into the CPMs;
- the see-through sensor, which has no line-of-sight check and detects objects behind obstacles.

See `docs/artery_notes.md` for the numbers.
