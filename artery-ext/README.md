# Artery side

Everything that runs with the network simulator: the logger, the config files and the scripts
for the workstation.

Versions used: OMNeT++ 5.7.1, SUMO 1.21.0, Artery commit e0ddd8c (2026-07-30), on Ubuntu 22.04.
The installation lives in `~/aoc-sim`. Before a manual run, load its settings with `source ~/aoc-sim/env.sh`.

## Folders

| Folder | Content |
| --- | --- |
| `aoc/` | The logger: one OMNeT++ module (`AocLogger`) and the network `AocWorld` that contains it |
| `config/` | `services-cp.xml` (CAM and CPM services) and `sensors-aoc.xml` (the sensors of AoC runs) |
| `scripts/` | The three scripts to run on the workstation |
| `tools/` | The templates of the scripts and `build_scripts.py`, which builds them |

## The scripts

Each script is one file. Copy it to the workstation and run it with `bash`. Each one prints a report with PASS or FAIL.

| Script | What it does | Time |
| --- | --- | --- |
| `scripts/a6_install.sh` | Installs OMNeT++, SUMO and Artery for one user, then runs three test simulations | about 1 hour |
| `scripts/a7_cp_demo.sh` | Runs the Artery example with the real CP service and exports the CPM logs to CSV | 1 minute |
| `scripts/a8_logger.sh` | Adds the logger to Artery, runs the example with it, exports the logs to parquet | a few minutes the first time |

Options are environment variables written in front of the command. `bash SCRIPT --help` lists them. The most useful ones:

```bash
A8_SIM_TIME=120s bash a8_logger.sh          # simulate 120 s (default 60 s)
A8_SENSORS=example bash a8_logger.sh        # use the sensors of the Artery example
bash a8_logger.sh --export FOLDER           # export one run again
```

`a7_cp_demo.sh` and `a8_logger.sh` hold copies of files of this repository. Do not edit them by hand.
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

The files go to `<result folder>/aoc-<config name>-<run number>/`. Two more files describe the run: `nodes.csv` and `run.csv`.

To use the logger in a scenario that is based on the environment model, set `network = AocWorld`
in its `omnetpp.ini`, or pass `--network=AocWorld` on the command line.

How `a8_logger.sh` installs it:

1. It copies `aoc/` to `artery/scenarios/aoc`.
2. It adds one block at the end of `artery/scenarios/CMakeLists.txt`. The original file is kept as `CMakeLists.txt.a8-backup`.
3. It builds one new library, `libartery_aoc.so`. The rest of Artery is not built again.

## The sensors file

`config/sensors-aoc.xml` keeps the four radars of the Artery example. It leaves out two sensors:

- the CAM sensor, which puts every vehicle known from its CAMs into the local model, and so into the CPMs;
- the see-through sensor, which has no line-of-sight check and detects objects behind obstacles.

See `docs/artery_notes.md` for the numbers.
