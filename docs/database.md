# The results database (task D4)

`aoc-database [--results results] [--out results/d4]` turns the tables of D2 and D3 into the tables the papers read:
per cell, with confidence intervals, and with the tests of the hypotheses H1 and H3 of the plan. It needs no
simulation data: it reads `results/d3/metrics.csv`, `bins.csv`, `events.csv`, `qfit.csv` and
`results/d2/gridcheck_runs.csv`, all in the repository, and writes `results/d4/`. Only `metrics.csv` is required;
without the others the tables that need them are empty.

## The tables

| File | One row per | Content |
| --- | --- | --- |
| `runs.csv` | run, way, hold | the metrics of B8 with the cell, the seed, the kind (evaluation or calibration) and the facts of the run from the check of D2: the penetration as drawn (`pen_share`), the cars, the CPM rate, the channel busy ratio of the received CPMs. The base table |
| `cells.csv` | cell, way (hold 5 s) | the mean of each metric over the 10 evaluation seeds with its 95 % confidence interval (`_lo`, `_hi`: Student's t over the seeds), the number of runs |
| `factors.csv` | factor level, way | the same per level of one factor (density, penetration, message configuration), the other factors pooled |
| `h1.csv`, `h1_overall.csv` | cell, way compared | H1: the relative gain of B3 on the calibration error against B0, B1, B2 and B4, paired by run (1 minus B3 over the other, per run; then the mean, the interval and the lowest value over the runs), the share of runs where B3 is not worse, the means of both, B0's error in the cell and the regime it gives: `aged` when B0's error is at least 0.05 (objects get old there), else `fresh`. The verdict of the plan against B0 and B1: in an aged cell, PASS when the lower end of the interval is at least 30 %; in a fresh cell, PASS when B3 is not worse than B0 in every run. `not_worse` gives that second rule on its own against B0; `n/a` where a rule does not apply or cannot be tested (one seed). The overall table pools the 360 evaluation runs |
| `h3.csv`, `h3_overall.csv` | cell, way compared | H3: the stale-confidence rate of B3 against B0, B1 and B2, as a difference paired by run (the other minus B3, with its interval and its lowest value), the ratio of the means, the share of runs where B3 is not above, and the awareness of B3 minus the other paired by run (mean, interval, lowest). The verdict of the plan against B0: PASS when B3 is under B0 in every run, the ratio is at most 0.5 and the awareness is within 2 points on average |
| `holds.csv` | hold, way | the metrics over the 72 runs of the hold check (the seeds 0 and 1 of every cell), at the holds 2, 5 and 10 s. The 5 s row is over those 72 runs too, so the three rows are comparable (B0 0.445 there against 0.433 over the 360 runs) |
| `reliability.csv`, `reliability_pen.csv` | hold, way, bin (and penetration) | the reliability diagram summed over the evaluation runs: the objects in the bin, their mean belief, the share really there, the mean plausibility, the share of the objects. The data of figure F5 |
| `events.csv` | cell, way | the belief in the hidden pedestrian during the occlusion (the mean, the lowest and the last value with V2X, the last and the lowest with the own sensors), the CPM rows in the gap, the fused objects, the gap; means and intervals over the 30 events of the cell (3 per run: `events`; correlated within a run: `runs`). The ways of `events.csv` of D3: B0, B1, B3 and B4 (no B2) |
| `summary.md` | | the numbers of the papers in prose and tables: the fitted q, the five ways, H1, H3, the hold, the reliability diagram of B3, the factors, the occluded pedestrian |

The calibration runs (seeds 10 and 11) are left out of every aggregate: they served the fit of q.

Two things the papers must say when they quote these tables:

- **Two weightings.** The metrics per run weigh the runs equally (the calibration error of H1 is the mean of 360
  per-run errors); the reliability diagram pools the objects of the runs, so the error read off the diagram is
  object-weighted (B3: 0.033 against 0.028 per run, since the heavy runs weigh more). `summary.md` prints both.
- **The runs are clustered in cells.** The interval over the 360 runs treats them as one sample; the interval over
  the 36 cell means respects the design (for the gain against B1: 79.6 to 80.4 % over the cells against 79.8 to
  80.2 % over the runs: the same here, since the gain hardly varies within a cell). The paper quotes the per-cell
  gains with their 10-seed intervals and the smallest cell, and the overall gain over the cells. The verdict reads
  the lower end of a two-sided 95 % interval (one-sided at 97.5 %) with no correction for the 36 cells. The ratio of
  the mean errors (0.798) and the mean of the per-run ratios (0.800) agree to 0.3 points; the per-run ratio would be
  unstable only in a fresh cell, where the denominator is near 0, and this grid has none.

The intervals say how sure the mean is, not how the runs spread. The verdicts are in the tables, not in the exit
code of the command.

## The result of Nov 4, 2026

Built from the replay of D3 (hold 5 s on the 432 runs; the holds of 2 and 10 s on 72 runs). `results/d4/summary.md`
has the tables; the lines that matter:

- H1 passes in the 36 cells and overall: the calibration error of B3 is 80 % below B1 (79.8 to 80.2 % over the 360
  runs; the smallest cell gain 78.2 % at low/100/10hz, lower end 76.8 %; the lowest run 73.6 %), 93 % below B0, 75 %
  below B2, 10 % below B4 (B4 better in 11 runs of 360). Over the evaluation runs at 5 s: B3 0.028, B4 0.032, B2
  0.111, B1 0.140, B0 0.433. The Brier score agrees (B3 0.101, B4 0.102, B2 0.122, B1 0.142, B0 0.431). Every cell
  is an aged cell (B0's error runs from 0.23 to 0.64): the second clause of H1, B3 not worse than B0 where every
  object is fresh, cannot be tested on this grid; the `not_worse` column passes everywhere but is a formality.
- H3 passes against B0: B3 0.240 against 0.990 (a factor 4), under in every run; the awareness of B3 is 0.9 points
  under B0 on average (0.834 against 0.843; the worst run 2.7 points). B1 is at 0 up to wrong associations (22 runs
  above 0, at most 0.0004) because the cutoff forgets the old objects, and B2 at 0.065. B3 keeps the old objects with
  the belief their age warrants. The papers must say that a way that forgets looks clean on this metric and pays on
  the calibration error and the Brier score; on the coverage B3 is under B1 and B2 (0.461 against 0.575 and 0.581;
  B4 0.420), a cost of the over-confident middle bins below.
- The hold: with 2 s, B0 0.094 and B1 0.098 (a short hold does the cutoff's job), B3 0.033; with 10 s, B0 0.66, B1
  0.097, B3 0.023. B3 and B4 are the best calibrated ways at every hold and the ones whose error hardly depends on it
  (B3 0.033, 0.029, 0.023; B4 0.040, 0.032, 0.025).
- The factors: the calibration error of every way grows with the penetration (B3 from 0.020 at 10 % to 0.035 at
  100 %, B1 from 0.111 to 0.170) and a little with the density. The message configuration does not change the
  calibration error (B3 0.028, 0.028, 0.029) but moves the stale rate (0.231 at etsi, 0.253 at 10hz) and the coverage
  (0.490 against 0.436) by 2 to 5 points. The channel does load up in the heaviest cell: at high/100/10hz the delivery
  ratio of the CPMs is 0.80 (0.72 in the worst run), the channel busy ratio 0.20 and the median age at reception
  0.18 s (p95 0.29 s), against 0.96 or more, 0.16 or less and 0.06 to 0.08 s elsewhere; the calibration error of B3
  there is 0.038 against 0.0375 at etsi in the same cell. So what ages the objects in this grid is the memory hold,
  not the channel; the control grid (E2) must push the channel further. The awareness is within 1 point for every
  way (B0 0.843, B3 0.834, B1 0.827; 0.67 at 10 %, 0.97 at 100 %).
- The reliability diagram of B3: the top bin right (97 % really there at a belief of 0.99), the low bins right, the
  middle bins (0.4 to 0.9) over-confident by 0.11 to 0.15 (a belief of 0.455 for 30 % there). B2 is under-confident
  in the low bins (43 % there at a belief of 0.14, 75 % at 0.45); B1 has nothing between 0.1 and 0.9 and its bin 0
  holds 246 million objects at a belief of 0.000 of which 22.5 % are really there: that is where its error comes from.
- The occluded pedestrian: with V2X the belief of B3 stays at 0.99 through the gap in the 1080 events of the
  evaluation runs (and in the 216 of the calibration runs); with the own sensors only it ends at 0.43. The
  association splits the pedestrian into 2.8 (at 10 %) to 3.8 (at 100 %) fused objects during the gap on average
  (2.5 to 4.5 per cell).

## What D4 does not do

- It draws nothing: the figures F2, F5, F7 and F8 are drawn from its tables by `aoc-grid-figures` (D5, `figures/`).
- It does not test the factors (an analysis of variance would be the next step if the papers need one); the
  intervals of `factors.csv` and `cells.csv` are what the text quotes.
