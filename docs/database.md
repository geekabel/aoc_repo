# The results database (task D4)

`aoc-database [--results results] [--out results/d4]` turns the tables of D2 and D3 into the tables the papers read:
per cell, with confidence intervals, and with the tests of the hypotheses H1 and H3 of the plan. It needs no
simulation data: it reads `results/d3/metrics.csv`, `bins.csv`, `events.csv`, `qfit.csv` and
`results/d2/gridcheck_runs.csv`, all in the repository, and writes `results/d4/`.

## The tables

| File | One row per | Content |
| --- | --- | --- |
| `runs.csv` | run, way, hold | the metrics of B8 with the cell, the seed, the kind (evaluation or calibration) and the facts of the run from the check of D2: the penetration as drawn (`pen_share`), the cars, the CPM rate, the channel busy ratio of the received CPMs. The base table |
| `cells.csv` | cell, way (hold 5 s) | the mean of each metric over the 10 evaluation seeds with its 95 % confidence interval (`_lo`, `_hi`: Student's t over the seeds), the number of runs |
| `factors.csv` | factor level, way | the same per level of one factor (density, penetration, message configuration), the other factors pooled |
| `h1.csv`, `h1_overall.csv` | cell, way compared | H1: the relative gain of B3 on the calibration error against B0, B1, B2 and B4, paired by run (1 minus B3 over the other, per run; then the mean and the interval over the runs), the share of runs where B3 is not worse, the means of both; the verdict of the plan: PASS when the lower end of the interval is at least 30 % against B0 and B1 (`verdict`), and B3 not worse than B0 in every run (`not_worse`). The overall table pools the 360 evaluation runs |
| `h3.csv`, `h3_overall.csv` | cell, way compared | H3: the stale-confidence rate of B3 against B0, B1 and B2, as a difference paired by run (the other minus B3, with its interval), the share of runs where B3 is not above; the verdict of the plan: PASS when B3 is under B0 in every run |
| `holds.csv` | hold, way | the metrics over the runs of the hold check (the seeds 0 and 1 of every cell, the holds 2, 5 and 10 s) |
| `reliability.csv`, `reliability_pen.csv` | hold, way, bin (and penetration) | the reliability diagram summed over the evaluation runs: the objects in the bin, their mean belief, the share really there, the mean plausibility, the share of the objects. The data of figure F5 |
| `events.csv` | cell, way | the belief in the hidden pedestrian during the occlusion (the mean, the lowest and the last value with V2X, the last and the lowest with the own sensors), the CPM rows in the gap, the fused objects, the gap; means and intervals over the 30 events of the cell |
| `summary.md` | | the numbers of the papers in prose and tables: the fitted q, the five ways, H1, H3, the hold, the reliability diagram of B3, the factors, the occluded pedestrian |

The calibration runs (seeds 10 and 11) are left out of every aggregate: they served the fit of q. The confidence
intervals are over the seeds of a cell (10) or over the runs of a level or of the grid; they say how sure the mean
is, not how the runs spread. The verdicts are in the tables, not in the exit code of the command.

## The result of Nov 4, 2026

Built from the replay of D3 (hold 5 s on the 432 runs; the holds of 2 and 10 s on 72 runs). `results/d4/summary.md`
has the tables; the lines that matter:

- H1 passes in the 36 cells and overall: the calibration error of B3 is 80 % below B1 (interval 79.8 to 80.2 % over
  the 360 runs; the smallest cell gain 78 %), 93 % below B0, 75 % below B2, 10 % below B4 (B4 better in 11 runs of
  360). B3: 0.028 on average, B4 0.032, B2 0.100, B1 0.128, B0 0.417. The Brier score agrees (B3 0.091, B2 0.109,
  B1 0.129, B0 0.415).
- H3 passes against B0 (B3 0.24 against 0.99, under in every run). B1 is at 0 by construction (the cutoff forgets
  the old objects) and B2 at 0.065: B3 keeps the old objects with the belief their age warrants. The papers must say
  that a way that forgets looks clean on this metric and pays on the calibration and the coverage.
- The hold: with 2 s, B0 0.094 and B1 0.098 (a short hold does the cutoff's job), B3 0.033; with 10 s, B0 0.66, B1
  0.097, B3 0.023. B3 is the best calibrated way at every hold and the only one whose error hardly depends on it.
- The factors: the calibration error of every way grows with the penetration (B3 from 0.020 at 10 % to 0.035 at
  100 %, B1 from 0.111 to 0.170) and a little with the density; the message configuration changes nothing (the
  channel never bites at these loads: ages at reception of 0.06 to 0.08 s, delivery ratio above 0.97). The awareness
  is the same for every way (0.84; 0.68 at 10 %, 0.97 at 100 %).
- The reliability diagram of B3: the top bin right (97 % really there at a belief of 0.99), the low bins right, the
  middle bins (0.4 to 0.9) over-confident by about 0.1. B2 is under-confident in the low bins (43 % there at a belief
  of 0.15); B1 has nothing between 0.2 and 0.9.
- The occluded pedestrian: with V2X the belief of B3 stays at 0.99 through the gap in the 1296 events; with the own
  sensors only it ends at 0.43. The association splits the pedestrian into 2.8 to 3.7 fused objects during the gap.

## What D4 does not do

- It draws nothing: the figures F2, F5, F7 and F8 are D5 (`figures/`).
- It does not test the factors (an analysis of variance would be the next step if the papers need one); the
  intervals of `factors.csv` and `cells.csv` are what the text quotes.
