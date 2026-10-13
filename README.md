# Age of Confidence (AoC)

Code of the AoC work: how the age of a shared object, the message loss and the channel load
change the trust that a vehicle can put in this object, and which message rate a target trust needs.

The plan, the board and the task notes are in the Claude document "From Age of Information to Age of Confidence".
This repository holds what the tasks produce.

## What is here today

| Folder | Content | Task |
| --- | --- | --- |
| `src/aoc/theory/` | The reliability curve, its averages, the needed message rates | A2, A3, A4 |
| `src/aoc/io/` | Export of the Artery logs to parquet, schema of the observations table | A8 |
| `src/aoc/fusion/` | Replay loader and noise model. The fusion steps come next | B2 (done), B3 to B7 |
| `src/aoc/analysis/` | Metrics and aggregation. Empty for now | B8, D4 |
| `artery-ext/` | Logger for Artery, config files, scripts for the workstation | A6, A7, A8 |
| `theory/note/` | Theory note: LaTeX source, PDF, figures and the script that draws them | A5 |
| `figures/` | One script per figure of the papers. Empty for now | D5, E3 |
| `paper-globecom/`, `paper-mswim/` | The two papers. Empty for now | F1, F6 |
| `tests/` | Tests, with two short simulation logs in `tests/data` | B1 |
| `docs/` | Schema of the tables, the replay, notes about Artery, issue draft | A8, B2 |

## Quick start

Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,figures]"
make check          # code style, then the tests (a few seconds)
```

Other commands:

| Command | What it does |
| --- | --- |
| `make test` | Runs the tests |
| `make lint` | Checks the code style with ruff |
| `make figures` | Draws every figure again (today: the seven figures of the theory note) |
| `make note` | Compiles the theory note (needs pdflatex) |
| `make scripts` | Builds the workstation scripts again after a change of a source file |
| `make schema` | Writes `docs/observations_schema.md` again after a change of the columns |
| `aoc-export RUN_FOLDER` | Turns the logs of one simulation run into parquet files |
| `aoc-replay RUN_FOLDER` | Checks an exported run: noise model and replay of every vehicle |

## Typical uses

Compute a reliability and a needed rate:

```python
from aoc.theory import reliability, rates

ped = reliability.CLASSES["pedestrian"][:4]           # sigma_x, sigma_v, covariance, q
print(reliability.alpha(1.0, *ped))                   # reliability of a pedestrian seen 1 s ago: 0.89
print(reliability.half_life(*ped))                    # age at which the reliability is 0.5: 1.70 s
period = rates.t_max_periodic_exact(0.9, 0.1, ped, reliability.D0)
print(1 / period)                                     # updates per second for an average of 0.9 with 10 % loss: 0.91
```

Export one simulation run and read it:

```bash
aoc-export tests/data/run_aoc_sensors --out /tmp/run_aoc
```

```python
import pandas as pd
obs = pd.read_parquet("/tmp/run_aoc/observations.parquet")
print(obs[obs.src == "v2x"][["t_rx", "rx_station", "tx_station", "gt_match_id", "age"]].head())
```

Replay the run for one vehicle, tick by tick (details in `docs/replay.md`):

```python
from aoc.fusion.replay import load_run

run = load_run("/tmp/run_aoc")                       # adds the measurement noise (sigma_x 0.3 m, sigma_v 0.5 m/s)
for tick in run.replay("flow0.0"):                   # one tick every 0.1 s
    print(tick.t, len(tick.local), len(tick.v2x_new), len(tick.v2x))
```

Run a simulation on the workstation: see `artery-ext/README.md`.

## Names in the note and names in the code

The theory note uses simple names. The code keeps the names of the plan.

| Note | Code | Meaning |
| --- | --- | --- |
| delay d | `d0`, `D0_DELAY` (0.13 s) | Age of an object when it is received |
| plateau length L | `D0`, `dd0` | Time during which the simple curve stays at 1 |
| decay time tau | `tau2` | How fast the simple curve falls after the plateau |
| simple curve | `plateau_alpha` | 1 up to L, then an exponential decay |
| regular updates | `periodic` | One update every T seconds |
| random updates | `poisson` | Updates at random times, at a given average rate |
| average reliability | `e_plateau_*`, `e_exact_*` | Reliability averaged over time |
| age limit | `age_at_level` | Age at which the reliability falls to the target |

## Layout against the plan

The plan lists `fusion/` and `analysis/` at the top of the repository. Here they are sub-packages of one
Python package, `src/aoc/fusion` and `src/aoc/analysis`. The reason: one `pip install` gives every module,
and the tests import the installed code. The other folders keep the names of the plan.

## Rules of this repository

- One source for each file. The workstation scripts hold copies of the logger and of the exporter.
  They are built by `make scripts`, and a test fails when a copy is out of date.
- Every number printed in the theory note comes from `theory/note/code/a5_figures.py`.
  `tests/test_theory.py` checks Tables II and III of the note against the code.
- The parameter values in `aoc.theory.reliability.CLASSES` are assumed values. They are not fitted to data yet.
- The continuous integration (`.github/workflows/ci.yml`) runs the style check and the tests on Python 3.10 and 3.12.
  It does not build Artery.

## Not decided yet

- The license. The logger in `artery-ext/aoc` links against Artery, which is under GPL v2.
