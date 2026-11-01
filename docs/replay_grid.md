# The replay of the grid (task D3, experiment E3)

D1 ran the 432 runs of the core grid and D2 checked them. D3 replays the five ways of the plan on every run and writes
the metrics the papers need. Two commands, in this order:

1. `aoc-fit-q`: the motion value q of the reliability curve, fitted per class on the calibration runs (seeds 10 and 11);
2. `aoc-replay-grid`: the replay of B0 to B4 on every run with the fitted q, one JSON file per run, then the tables.

Both run on the workstation, where the parquet files are. The tables they write are small and come back to the
repository under `results/d3/`.

## The calibration of q

The reliability curve of the note is alpha(age) = 1 - exp(-eps^2 / (2 (spread + q age^3 / 3))): the probability that
the place a report predicts for its object after `age` seconds (the report moved forward at its speed) is within
eps = 1.5 m of the true place. The spread is the variance of the predicted place from the covariance of the report
(P + age^2 Pv), and q is the intensity of the unknown acceleration. The note assumes q per class (B5: the assumed
values are too careful on straight roads, so the plan fits q on calibration runs kept apart from the evaluation).

`aoc-fit-q ROOT [--out qfit.csv] [--curves curves.csv] [--figure qfit.pdf] [--seeds 10,11] [--receivers 5] [--rows 20000] [--jobs N]`

fits q without the pipeline, on what the curve speaks about:

- the received rows (the CPM reports, with the noise of the replay) after the warm-up of 60 s, for the egos of the
  events and the first other receivers of each calibration run (5 receivers), up to 20 000 rows per class and run;
- each report used at the ages 0.1, 0.2, ..., 5.0 s (the hold), as the replay would use it until a newer report
  comes: its predicted place against the true place of the object at that time (interpolated in the truth), a hit
  when within eps, when the object is still there;
- per class, the q that maximises the Bernoulli likelihood of the hits under alpha(age), found on a logarithmic
  grid from 1e-4 to 1e3 then refined by a golden-section search; next to it the assumed q of the note, the
  log-likelihood and the calibration error (10 bins of alpha) of both curves against the hits, the half-life of
  both curves with the median spread of a fresh report, and the hit rate per age with both curves (`--curves`,
  `--figure`: one panel per class, the measured points, the assumed curve dashed, the fitted one solid).

The printed line `--q class=value ...` is what the replay takes; `--q-from qfit.csv` reads it from the table. A class
with fewer than 2000 (report, age) points is not fitted and keeps the assumed value. On the pilot run of C5 (60 s, cars
mostly straight, the truck stopped) the fitted q were 8 times smaller than the assumed ones for the cars and 10 times
for the pedestrian, and the measured curves had a heavier tail than the Gaussian law (the cars that turn), as B5 had
found; the calibration runs of the grid, with their turning traffic, are the values that count.

## The replay

`aoc-replay-grid ROOT --out DIR [--holds 5] [--q class=value ... | --q-from qfit.csv] [--runs 0-11,36] [--receivers all|egos|N] [--jobs N] [--warmup 60] [--no-events] [--aggregate]`

For every exported run (or those of `--runs`):

- the receivers are those of the observations table: the 15 actors of the events and the 8 cars the exporter drew
  (`--receivers egos` keeps the three egos only; `--receivers N` the egos and the first N drawn cars). A run is loaded
  one receiver at a time (`load_run(..., receivers=[r])` reads its received rows and the sensor rows of every vehicle:
  the received rows of a dense run are tens of millions), and the measures of the receivers add up into one row per
  way and hold (`aoc.analysis.metrics.EvaluationState`, `evaluate_into`); the result is the same as a whole replay;
- nothing is counted before the warm-up of 60 s; the replay of a receiver starts one hold earlier, so its memory is
  the one of a full replay. A receiver present before its first row counts from 60 s on (it knows nothing then, which
  counts against its awareness: a full replay that started at its first row would have flattered it);
- the holds: 5 s by default, the same for every way (decision of Oct 24, 2026); `--holds 2,10` writes the two others
  in files of their own, the check of the plan on a part of the grid (the seeds 0 and 1 of every cell, say:
  `--runs 0-1,12-13,24-25,...`, the list `aoc.analysis.grid.seed_runs([0, 1])` gives);
- the metrics per way and hold are those of B8 (`docs/metrics.md`): calibration error and Brier score of the belief,
  coverage, sharpness, awareness ratio, RMSE, stale-confidence rate, ghost rate, time to confident, with the
  network side (channel busy ratio, delivery ratio, age at reception) over the same receivers and time;
- the reliability diagram of every way (10 bins of the belief: count, mean belief, share really there, mean
  plausibility) is written too: the data of figure F5, to be summed over the runs of a cell;
- the events: for each ego of the events (`ego_k`), the belief in its pedestrian (`ped_k`) around the occlusion (the
  first gap of at least 1.5 s in its own detections, the rule of C5), with V2X and with its own sensors only, for B0,
  B1, B3 and B4 (B2 is not a belief): the mean, the lowest and the last value in the gap with V2X, the last and the
  lowest with the own sensors, the CPM rows received in the gap. The data behind figure F6 at the scale of the grid
  (1296 events). `--no-events` leaves them out.

One JSON file per run and set of holds (`run-013-hold-5.json`) holds the metrics, the bins, the events, the
receivers, the q used and the time taken; a run whose file exists is not replayed again, so the replay runs in
several sittings and a failed run is rerun alone (`--runs`). Runs are replayed in parallel (`--jobs`, default half
the CPUs; one by one when the system has no shared memory for a pool). After the runs, or with `--aggregate` alone,
the JSON files are read into `metrics.csv` (one row per run, way and hold, with the cell, the seed and the kind,
evaluation or calibration), `bins.csv` and `events.csv`, and a table per message configuration and penetration is
printed for the first hold: the mean calibration error and Brier score of each way over the evaluation runs. The exit
code is 0 when every run asked for has its file.

Time: the pipeline costs about 0.12 s per tick for a receiver that knows 350 objects (the heaviest cell, high/100/10hz);
a receiver present the whole run is 2700 ticks after the warm-up. The heaviest runs take about an hour each; the whole
grid for the hold of 5 s is of the order of 80 to 100 CPU hours, 5 to 6 hours with 16 jobs. The covariance
intersection is the main cost (the golden-section search of its weight); its search now works on the determinant of
the information, without an inversion per step, and does not check the covariances twice.

## What D3 does not do

- It does not aggregate over the runs beyond the printed table: the results database (one table per cell with
  means and confidence intervals, the tests of H1 and H3) is D4; the figures F5, F7 and F8 are D5.
- It does not change the pipeline: the same B2 to B7 as the pilot of C7, with the sensor tables of C6 (reliability 1,
  the detection probabilities of clear weather) and the noise model of B2.
