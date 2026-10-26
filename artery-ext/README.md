# Artery side

Everything that runs with the network simulator: the logger, the config files and the scripts
for the workstation.

Versions used: OMNeT++ 5.7.1, SUMO 1.21.0, Artery commit e0ddd8c (2026-07-30), on Ubuntu 22.04.
The installation lives in `~/aoc-sim`. Before a manual run, load its settings with `source ~/aoc-sim/env.sh`.

## Folders

| Folder | Content |
| --- | --- |
| `aoc/` | The logger (`AocLogger`), the network `AocWorld` that contains it, and `AocCpService`: the CP service of Artery with a controlled loss of received CPMs (task C1) |
| `config/` | `services-cp.xml` (CAM and CPM services), `services-link.xml` (CAM and the lossy CP service), `services-vru1/2/3.xml` (CAM and CPM, the CP service off on some helpers), `sensors-aoc.xml` (the radars of the first AoC runs) and `sensors-vru.xml` (camera, radar and lidar of the plan) |
| `link/` | The single-link validation scenario of task C1: the road (SUMO), the three cars, the OMNeT++ configuration with the grid T x p |
| `vru/` | The occluded-VRU scenario of task C5 (E1 of the plan): the intersection, the buildings, the alley, the seven actors, the OMNeT++ configuration. The road files are written by `tools/make_vru_scenario.py` |
| `scripts/` | The scripts to run on the workstation |
| `tools/` | The templates of the scripts, `build_scripts.py`, which builds them, and `make_vru_scenario.py`, which writes the road of the VRU scenario |

## The scripts

Each script is one file. Copy it to the workstation and run it with `bash`. Each one prints a report with PASS or FAIL.

| Script | What it does | Time |
| --- | --- | --- |
| `scripts/a6_install.sh` | Installs OMNeT++, SUMO and Artery for one user, then runs three test simulations | about 1 hour |
| `scripts/a7_cp_demo.sh` | Runs the Artery example with the real CP service and exports the CPM logs to CSV | 1 minute |
| `scripts/a8_logger.sh` | Adds the logger to Artery, runs the example with it, exports the logs to parquet | a few minutes the first time |
| `scripts/c1_link.sh` | Runs the single-link scenario on the grid of the plan (16 runs), exports them, checks the period and the loss of each one | 15 to 40 minutes; `--quick` for one run of 30 s |
| `scripts/c5_vru.sh` | Runs the occluded-VRU pilot (1, 2, 3 helpers x 2 seeds, 6 runs of 60 s), exports the runs, checks that the occlusion event is in the logs of each one | a few minutes; `--quick` for one run of 45 s |

Options are environment variables written in front of the command. `bash SCRIPT --help` lists them. The most useful ones:

```bash
A8_SIM_TIME=120s bash a8_logger.sh          # simulate 120 s (default 60 s)
A8_SENSORS=example bash a8_logger.sh        # use the sensors of the Artery example
bash a8_logger.sh --export FOLDER           # export one run again
```

`a7_cp_demo.sh`, `a8_logger.sh`, `c1_link.sh` and `c5_vru.sh` hold copies of files of this repository. Do not edit them by hand.
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

## The lossy CP service

`AocCpService` extends `CpService`. Each received CPM is dropped with probability `dropProbability` before the
service reads it, with the random number generator of the module (a seed set gives the same drops), and the
signal `CpmDropped` is emitted. With `dropProbability = 0` it behaves exactly like `CpService`. The single-link
scenario (`link/omnetpp.ini`) uses it with a fixed period (`minInterval = maxInterval = T`,
`withDccRestriction = false`) and every perceived object in every CPM (`objectInclusionConfig = 0`).
See `docs/link.md`.

How `a8_logger.sh` (and `c1_link.sh`) installs the sources:

1. It copies `aoc/` to `artery/scenarios/aoc`.
2. It adds one block at the end of `artery/scenarios/CMakeLists.txt`. The original file is kept as `CMakeLists.txt.a8-backup`.
3. It builds one new library, `libartery_aoc.so`. The rest of Artery is not built again.

## The sensors files

`config/sensors-vru.xml` holds the three sensors of the plan for the VRU runs: a camera (120 degrees, 80 m), a radar
(120 degrees, 150 m) and a lidar (360 degrees, 100 m), all front-mounted cones of Artery with a line-of-sight check;
their ranges and angles are set in `vru/omnetpp.ini`. See `docs/vru.md`.

`config/sensors-aoc.xml` keeps the four radars of the Artery example. It leaves out two sensors:

- the CAM sensor, which puts every vehicle known from its CAMs into the local model, and so into the CPMs;
- the see-through sensor, which has no line-of-sight check and detects objects behind obstacles.

See `docs/artery_notes.md` for the numbers.
