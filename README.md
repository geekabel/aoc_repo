# Age of Confidence (AoC)

Code of the AoC work: how the age of a shared object, the message loss and the channel load
change the trust that a vehicle can put in this object, and which message rate a target trust needs.

The plan, the board and the task notes are in the Claude document "From Age of Information to Age of Confidence".
This repository holds what the tasks produce.

## What is here today

| Folder | Content | Task |
| --- | --- | --- |
| `src/aoc/theory/` | The reliability curve, its averages, the needed message rates | A2, A3, A4 |
| `src/aoc/io/` | Export of the Artery logs to parquet, schema of the observations table, synthetic streams | A8, B9 |
| `src/aoc/fusion/` | Replay loader, noise model, sensor models and weather tables, time alignment, association, evidence, discount by age, combination, kinematic fusion | B2 to B7, C6 |
| `src/aoc/analysis/` | Calibration error, real motion of the objects, the metrics of a run for the five ways of the plan, the check of the single-link runs, the age at the receiver and E[alpha] against the law, the check of the occluded-VRU runs, the belief of one vehicle in one object over time and the pilot through the whole pipeline. The aggregation over runs comes later | B5, B8, C1 to C3, C5, C7, D4 |
| `artery-ext/` | Logger and lossy CP service for Artery, config files, the single-link scenario, the occluded-VRU scenario, scripts for the workstation | A6, A7, A8, C1, C5 |
| `theory/note/` | Theory note: LaTeX source, PDF, figures and the script that draws them | A5 |
| `figures/` | One script per figure of the papers: `f03_ages.py` (F3), `f04_ealpha.py` (F4) and `f06_money.py` (F6) so far | C2, C3, C7, D5, E3 |
| `paper-globecom/`, `paper-mswim/` | The two papers. Empty for now | F1, F6 |
| `tests/` | Tests, with two short simulation logs in `tests/data`; `test_synthetic.py` checks the whole pipeline on streams whose right answer is known | B1, B9 |
| `docs/` | Schema of the tables, the replay, the association, the evidence, the discount, the combination, the kinematic fusion, the metrics, the synthetic streams, the single-link scenario, the age law and E[alpha] checks, the occluded-VRU scenario, the sensor models and weather tables, the pilot and figure F6, notes about Artery, issue draft | A8, B2 to B9, C1 to C3, C5 to C7 |

## Quick start

Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,figures]"
make check          # code style, then the tests (about two minutes)
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
| `aoc-replay RUN_FOLDER [--weather rain]` | Checks an exported run: noise model, sensor tables (the rows the sensors miss in this weather) and replay of every vehicle |
| `aoc-associate RUN_FOLDER` | Checks the association of a run against the ground truth |
| `aoc-evidence RUN_FOLDER` | Builds the existence and class evidence of every object of a run and checks it |
| `aoc-discount RUN_FOLDER` | Lowers the existence evidence of every object by its age, checks it, and compares full trust, a hard cutoff and the discount |
| `aoc-combine RUN_FOLDER` | Runs the whole evidential chain and fuses the evidence of each real object; compares the four ways of the plan (about 2 minutes) |
| `aoc-kinematics RUN_FOLDER` | Fuses the places and the speeds of each real object by covariance intersection; compares it with the Kalman way (6 to 10 minutes) |
| `aoc-evaluate RUN_FOLDER` | Runs the whole pipeline for the five ways of the plan and gives the metrics of the plan, one row per way (4 to 11 minutes) |
| `aoc-synthetic OUT_FOLDER [--scenario NAME]` | Writes a small synthetic run whose right answer is known (a few cars, a known delay and loss); the check commands of B2 to B8 run on it |
| `aoc-link RESULT_FOLDER [--grid]` | Reads the exported single-link runs of C1 and checks each one against its period T and its loss p |
| `aoc-ages RESULT_FOLDER [--out f3.pdf]` | Compares the age at the receiver of the single-link runs with the law of the note, and draws figure F3 |
| `aoc-ealpha RESULT_FOLDER [--out f4.pdf]` | Measures E[alpha] on the single-link runs for every class of the note and compares it with the exact law and the two formulas; draws figure F4 |
| `aoc-sensors [--write tables.json]` | Prints the sensor models and the weather tables (detection probability and reliability by sensor, weather and range bin), or writes them to a file |
| `aoc-pilot RESULT_FOLDER [--figure f6.pdf]` | Runs the whole pipeline on the exported occluded-VRU runs: the metrics of the five ways on each run, the belief of the ego in the pedestrian over time with V2X and with its own sensors only, and figure F6 (about 90 s per run) |
| `aoc-vru RESULT_FOLDER [--pilot]` | Reads the exported occluded-VRU runs of C5 and prints the timeline of the event of each one (when the ego loses the pedestrian, who sees it, which CPMs reach the ego), with a PASS or FAIL |

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

run = load_run("/tmp/run_aoc")                       # adds the measurement noise (sigma_x 0.3 m, sigma_v 0.5 m/s) and the misses of the sensor tables
for tick in run.replay("flow0.0"):                   # one tick every 0.1 s
    print(tick.t, len(tick.local), len(tick.v2x_new), len(tick.v2x))
```

Find which objects of a tick are the same real object (details in `docs/association.md`):

```python
from aoc.fusion.alignment import align
from aoc.fusion.association import associate

aligned = align(tick)                                # every object moved to the time of the tick
groups = associate(aligned)                          # one group number per object
```

The association is the adapted greedy algorithm of Wolf et al. (FUSION 2023), with a likelihood distance
(the Mahalanobis distance plus a term for the size of the uncertainty).

Turn each object into evidence (details in `docs/evidence.md`):

```python
from aoc.fusion.bba import build_evidence

evidence = build_evidence(aligned.objects)           # existence and class mass functions, one row per object
```

Lower the evidence of each object by its age (details in `docs/discount.md`):

```python
from aoc.fusion.discounting import discount_evidence

trusted = discount_evidence(evidence, aligned.objects)   # trusted.reliability: the alpha of each object
```

Fuse the evidence of each real object (details in `docs/combination.md`):

```python
from aoc.fusion.combination import fuse

fused = fuse(aligned.objects, groups, trusted)           # one row per real object, with bel_exists and pl_exists
```

Fuse the places and the speeds of each real object (details in `docs/kinematics.md`):

```python
from aoc.fusion.kinematics import fuse_states, with_states

states = fuse_states(aligned, groups)                    # covariance intersection: one state and one covariance per real object
table = with_states(fused.objects, states)              # the two layers side by side
```

Measure a whole run for the five ways of the plan (details in `docs/metrics.md`):

```python
from aoc.analysis.metrics import evaluate_run

rows = evaluate_run(run)                                 # one row per way: calibration error, Brier score, awareness ratio, RMSE, and more
```

Make a stream whose right answer is known and measure it (details in `docs/synthetic.md`):

```python
from aoc.io.synthetic import Car, Scenario, make_run

cars = (Car("a", vx=10.0), Car("b", x=40.0, vx=10.0), Car("c", x=80.0, vx=10.0, t_out=8.0))
run = make_run(Scenario(cars, duration=15.0, delay=(0.1, 1.0), loss=0.2))     # a Run, as load_run gives one
rows = evaluate_run(run)
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
- Every number printed in the theory note comes from `theory/note/code/a5_figures.py`, except the
  check section (Section VIII), whose numbers and two figures come from `aoc-link`, `aoc-ages` and
  `aoc-ealpha` on the single-link runs (`theory/note/data/README.md`).
  `tests/test_theory.py` checks Tables II and III of the note against the code.
- The parameter values in `aoc.theory.reliability.CLASSES` are assumed values. They are not fitted to data yet.
  So is the detection table of `aoc.fusion.sensors` (by sensor, weather and range bin); its reliability table is 1 by decision.
- The continuous integration (`.github/workflows/ci.yml`) runs the style check and the tests on Python 3.10 and 3.12.
  It does not build Artery.

## Not decided yet

- The license. The logger in `artery-ext/aoc` links against Artery, which is under GPL v2.
