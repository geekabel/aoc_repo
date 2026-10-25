# The metrics of a run

Task B8. One row of numbers per run and per way to handle trust. The module is `aoc.analysis.metrics`. It runs
the whole pipeline on a run (align, associate, evidence, discount, fuse, kinematics) for the five ways of the
plan and measures each of them against the truth. The rows are what tasks D3 and D4 aggregate over the runs.

## Evaluate a run

```bash
aoc-evaluate RUN_FOLDER                       # the folder with observations.parquet and truth.parquet
aoc-evaluate RUN_FOLDER --out rows.parquet    # also writes the five rows (.parquet, .csv or .json)
```

It takes 4 to 11 minutes for a run of 60 s with 20 vehicles, depending on the machine and on the number of reports.

| Option | What it does |
| --- | --- |
| `--hold SECONDS` | the receiver forgets a received object older than this value (default 5 s, the decision of Oct 24, 2026; 0 keeps everything) |
| `--q CLASS=VALUE` | sets the motion value of the reliability curve for one class |
| `--out FILE` | writes the rows to a file |
| `--drop-unmatched` | leaves out the rows without a true object or without a time |

## Use it in Python

```python
from aoc.analysis.metrics import evaluate_run, EvaluationModel

table = evaluate_run(run)                                   # one row per way, hold 5 s
table = evaluate_run(run, EvaluationModel(hold=2.0, epsilon=2.0))
table.set_index("way").loc["B3", "calibration_error"]
```

## The five ways

| Way | Trust | Rule for the received objects | Places and speeds | Confidence |
| --- | --- | --- | --- | --- |
| B0 full trust | none | conjunctive | covariance intersection | belief that the object exists |
| B1 hard cutoff | a received object older than 1 s is dropped | conjunctive | covariance intersection | belief |
| B2 Bayesian inflation | none, no evidential layer | none | the Kalman way | the probability that the fused place is within the tolerance of the true place, read from the fused covariance |
| B3 AoC | the discount by age | cautious | covariance intersection | belief |
| B4 ablation | the discount by age | conjunctive (Dempster only) | covariance intersection | belief |

B2 is my reading of the plan's "no evidential layer; inflate measurement covariance with age only". The covariance
of every object already grows with its age in the alignment. B2 fuses the places the Kalman way and reads a
confidence from the fused covariance, the same way the reliability curve reads one from the covariance of a single
object. Its plausibility is 1. Its confidence knows nothing of the number of sources that agree, nor of the
score of the sensor: only the fused covariance.

## The metrics

A fused object is "really there" when its members all name the same true object and this true object is within
1.5 m of the fused place. A fused object whose members name different true objects is a wrong association: it
counts as not there.

| Column | Meaning |
| --- | --- |
| `cbr_mean`, `pdr_mean` | mean channel busy ratio, one value per vehicle and per time, and mean packet delivery ratio, one value per received message (network). A tick with 15 objects counts once, not 15 times |
| `age_median`, `age_p95` | age of the received objects at reception, median and 95th percentile, in s (network) |
| `awareness_ratio` | among the true objects within 200 m of the vehicle, the share that it knows: a fused object within 1.5 m of the true place with a belief of at least 0.5. Summed over the vehicles and the ticks |
| `rmse` | position error per axis of the fused objects that are really there, in m |
| `calibration_error` | expected calibration error of the belief that the object exists, 10 bins |
| `brier` | Brier score of the belief: the mean of (belief - truth)^2 |
| `coverage` | share of fused objects whose bin of belief has: mean belief <= share really there <= mean plausibility |
| `sharpness` | mean width of the interval: plausibility minus belief |
| `stale_rate` | mean belief given to the fused objects whose true object left the simulation more than 1 s ago (the stale-confidence rate of the plan) |
| `ghost_rate` | mean belief given to the fused objects that are not really there |
| `time_to_confident` | median time, in s, from the entry of a true object in the 200 m range of a vehicle to a fused object within 1.5 m of it with a belief above 0.9 |
| `confident_share` | share of the entries that reach it before the object leaves the range |
| `wrong_groups` | fused objects whose members name different true objects |

Our 60 s runs have no object that leaves the simulation, so `stale_rate` is empty there and `ghost_rate` stands in.
The cars enter the simulation one after the other (one every 3 s on the run with the AoC sensors), and a car
enters the 200 m range of a vehicle 270 times in all. The first report of a new car reaches the vehicle at the
same tick in a third of the entries, and one tick later (0.1 s) in most of the others: the report is measured at
the entry, and its reception takes 0.08 s on the median, which pushes it to the next tick; the own sensor sees
the car later. So `time_to_confident` is 0.1 s for every way, and the five ways do not differ here. Both measures
will mean something on the runs of phase C, with vehicles that come and go.

The ticks where the vehicle has no state in the truth are skipped for every metric, and counted in `no_ego_state`
(0 on our runs).

### Two things to know about the coverage and the sharpness

The plausibility that an object exists is always 1 (no source speaks against an object; B2 sets it to 1 as
well). So the coverage reduces to a one-sided test: the mean belief of a bin must not be above the share really
there. A belief slightly too high in a bin fails the whole bin. And the sharpness is 1 minus the mean belief for
every way: it says how careful a way is, not how narrow its intervals are. Both measures are kept as the plan
defines them; they will tell more once a source can speak against an object.

## Measures on the two runs of 60 s

Memory of 5 s. The motion value of the curve is the assumed one (4.0).

**Run with the AoC sensors** (20 cars, one seed). Channel busy ratio 0.050, delivery ratio 0.968, age at
reception median 0.078 s, 95th percentile 0.178 s. 52 444 true objects in range, summed over the vehicles and
the ticks. No wrong group.

| Way | ECE | Brier | Coverage | Sharpness | Awareness | RMSE (m) | Ghost rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B0 full trust | 0.568 | 0.563 | 0.0% | 0.006 | 99.2% | 0.384 | 0.990 |
| B1 hard cutoff | 0.078 | 0.078 | 62.2% | 0.623 | 99.1% | 0.384 | 0.026 |
| B2 Bayesian inflation | 0.051 | 0.070 | 55.9% | 0.602 | 99.0% | 0.350 | 0.063 |
| B3 AoC | 0.044 | 0.070 | 91.7% | 0.612 | 98.5% | 0.384 | 0.060 |
| B4 ablation | 0.050 | 0.070 | 56.2% | 0.604 | 99.0% | 0.384 | 0.062 |

What it shows, on this one run:

- **H1** asks for a calibration error of B3 at least 30% below B0 and B1. Here: 92% below B0 and 43% below B1,
  with the assumed motion value. On the Brier score: 10% below B1.
- **H3** asks for the stale confidence halved against B0 with the awareness within 2 points. Here the ghost rate
  stands in for the stale rate: 0.060 against 0.990, and the awareness is 0.8 points below B0. Both hold.
  The cutoff has a lower ghost rate than B3 (0.026): it throws every old object away, which also costs it on the
  calibration error.
- **The cautious rule** (B3 against B4): the same Brier score to three decimals (0.0705 against 0.0700), a lower
  calibration error (0.044 against 0.050), and a coverage of 92% against 56%. With the cautious rule the belief stays below the share really there in
  almost every bin. Dempster only adds up the reports of several neighbours and goes above it.
- **B2** is a serious baseline: its calibration error (0.051) is close to B3, its Brier score is the same to
  three decimals, and its places are better (the Kalman way, 0.350 m against 0.384 m). It has no belief function
  and no cautious rule.

**Run with the example sensors** (same traffic, other sensors). Channel busy ratio 0.069, delivery ratio 0.971,
age at reception median 0.120 s, 95th percentile 0.325 s. Every report is fresh. No wrong group.

| Way | ECE | Brier | Coverage | Sharpness | Awareness | RMSE (m) | Ghost rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B0 full trust | 0.001 | 0.001 | 0.0% | 0.000 | 99.6% | 0.262 | 0.991 |
| B1 hard cutoff | 0.001 | 0.001 | 0.1% | 0.001 | 99.6% | 0.262 | 0.435 |
| B2 Bayesian inflation | 0.001 | 0.001 | 0.1% | 0.001 | 99.7% | 0.112 | 0.415 |
| B3 AoC | 0.005 | 0.001 | 100.0% | 0.006 | 99.6% | 0.262 | 0.450 |
| B4 ablation | 0.001 | 0.001 | 0.1% | 0.001 | 99.6% | 0.262 | 0.451 |

Where every object is fresh, the five ways are alike: calibration errors of 0.001 to 0.005, the same awareness.
This is the case where H1 asks only that B3 is not worse than B0 (decision of Oct 24, 2026): the Brier scores are
equal, and the calibration error of B3 is 0.004 above. The ghosts (fused objects more than 1.5 m from their true
place) are few. The cutoff gives them 0.435 on average: about half of them are fresh reports with a large error,
which no way can tell apart, and the other half are old reports.

### Limits

One run, one seed, one traffic for each table. The numbers show a direction, not a result.

## What the tests check

- The collector of one way by hand: calibration error, Brier score, coverage, sharpness, awareness, RMSE, ghost
  rate, time to confident, with small arrays where the answer is known.
- The confidence of B2 by hand.
- The network numbers against pandas on the observations table.
- The confidence metrics, the coverage, the awareness, the RMSE and the ghost rate of B0, B1, B3 and B4 on the
  sample run are counted again tick by tick with the modules themselves.
- The entries are counted from the truth alone, on the sample run and on a run where the other cars come 1 to 3 s
  late.
- A true object that leaves the simulation gives stale objects: full trust believes them, the cutoff drops them
  after 1 s, the discount gives them little.
- A run without received objects, a run without objects, the command and its output file.
