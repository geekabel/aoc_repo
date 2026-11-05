# The analysis of the control grid (task E3)

The control grid (`docs/control_grid.md`) runs five generation rules of the CP service in the same cells. Task E3
reads its runs and answers H4 (the target confidence reached with at least 20 % less channel load, or more time
above the target at equal load) and draws figure F9 (the control frontier: the time above the target against the
load, one point per configuration, the Pareto frontier). `aoc-control` (`aoc.analysis.control`) does the first,
`aoc-grid-figures --only f09` the second.

## What is measured in a run

The per-run numbers come from `aoc.analysis.rulecheck` (the check of E1), after a warm-up of 60 s:

| name | what it is |
| --- | --- |
| `time_above_target` | the share of the time an object without V2X within 200 m of a receiver is known with a reliability `alpha(age)` at or above the target 0.9; the age is the one of the freshest report received, the curve the one of the report's class and state (stopped or moving); a report older than 5 s is forgotten; pooled over the object ticks of every receiver (0.1 s steps) |
| `known` | the same share, for any reliability: the object has a report younger than 5 s |
| `cbr_mean` | the channel busy ratio seen by the receivers, the mean over the received CPMs (it leans to the busy moments) |
| `bytes_per_sender` | the CPM bytes per second per sender, without the empty CPMs (the CPMs of `CpService` that carry nothing, which the standard does not require) |
| `cpm_rate_hz` | the CPMs per second per sender |

The objects are the ones without a station: the `bg_` cars, the truck and the pedestrians (the load stations, which
have no `node_id`, are not objects). The receivers are the equipped cars that received at least one CPM. The
reliability is the one of the raw report (the sender's curve), not B3's belief after fusion: the metric is the same
for every rule, and it is the quantity the AoC rule promises to hold. The replay (B3's belief on these runs, with the
receiver's stopped state) is a later step.

## The pairs and the verdict

The rules of one cell and seed see the same traffic (the departures and the routes come from the seed), so a rule is
compared with a reference rule run by run: `pairs.csv` has one row per run and reference (`etsi`, the CP service of
Artery untouched; `etsi_abs`, the rules of ETSI on absolute positions with a CPM only when something is due). The row
holds the ratios of the load (`bytes_ratio`, `cbr_ratio`, `cpm_ratio`) and the differences of the service
(`time_diff`, `known_diff`).

H4 on one pair: PASS when the rule's load is at most 80 % of the reference's and its time above the target is not
lower by more than one point, or when its time above the target is higher by more than one point at a load not
higher by more than one percent. The load side depends on the cell:

- no load station (`load` 0): the channel busy ratio (the load of the plan; the stations make the busy ratio);
- 12 or 36 load stations: the CPM bytes per second per sender (the load stations set most of the busy ratio there,
  and a rule that sends fewer CPMs changes it little; the bytes say what the rule asks of the channel).

Both verdicts are in the tables (`verdict_cbr`, `verdict_bytes`); `verdict` is the one of the cell's load side. A
cell passes when its mean pair over the seeds passes (`cell_pairs.csv`, with the count of seed pairs that pass). A
rule passes H4 against a reference when every cell passes (`h4.csv`, with the count per load level, the mean and the
worst load ratio and time difference).

## The points of F9

`cells.csv` has one row per cell and rule: the mean over the seeds of each metric, its interval (Student's t over
the seeds, empty with one seed) and its range. These are the points of F9: the time above the target against the
load, the left panel with the busy ratio, the right one with the CPM bytes per sender. The colour is the rule, the
marker the load level. The dashed line is the Pareto frontier of the panel: the points no other point beats on both
axes (a lower or equal load with a higher or equal time, one of them strict). `frontier.csv` lists them, over the
whole grid and within each load level. H4 expects the AoC points on the frontier.

## Running it

```
aoc-control ROOT [--out results/e3] [--warmup 60] [--range 200] [--hold 5] [--jobs 4] [--reference etsi,etsi_abs]
aoc-grid-figures --only f09 [--results results] [--out figures]
```

`ROOT` is the results folder of the control grid (`~/aoc-sim/d1/results/control`). `runs.csv` in the output folder
is the cache of the per-run metrics: a run already there is not read again, so the command can run on a grid that is
half done and again at the end. Reading a run takes a few seconds (longer for the heavy cells); `--jobs` reads
several at once. The command prints `summary.txt`: the counts, the mean per rule over the cells, the H4 table, the
cells that do not pass, and the frontier.

## A first look (sandbox, seed 0 of the low-density cells)

To be filled from the sandbox runs of this session, then from the workstation grid.
