# The analysis of the control grid (task E3)

The control grid (`docs/control_grid.md`) runs five generation rules of the CP service under the same conditions.
Task E3 reads its runs and answers H4 (the target confidence reached with at least 20 % less channel load, or more
time above the target at equal load) and draws figure F9 (the control frontier: the time above the target against
the load, one point per condition and rule, the Pareto frontier). `aoc-control` (`aoc.analysis.control`) does the
first, `aoc-grid-figures --only f09` the second.

Words: a *condition* is a density, a penetration and a load level (12 in the grid); a *cell* of the design is a
condition and a rule (60); the *seed* is the repetition (4). The rules of one condition and seed see the same
traffic (the departures and the routes come from the seed), so a rule is compared with a reference rule seed by
seed.

## What is measured in a run

The per-run numbers come from `aoc.analysis.rulecheck` (the check of E1), after a warm-up of 60 s:

| name | what it is |
| --- | --- |
| `time_above_target` | the share of the time an object without V2X within 200 m of a receiver is known with a reliability `alpha(age)` at or above the target 0.9; the age is the one of the freshest report received, the curve the one of the report's class and state (stopped or moving); a report older than 5 s is forgotten; pooled over the object ticks of every receiver (0.1 s steps) |
| `known` | the same share, for any reliability: the object has a report younger than 5 s |
| `cbr_mean` | the channel busy ratio seen by the receivers: the mean over the received CPMs of a receiver (which leans to the busy moments), then the mean over the receivers |
| `bytes_per_sender` | the CPM bytes per sender-second: the bytes of the CPMs that carry something (the empty CPMs of `CpService` left out), over the time the equipped cars were present after the warm-up (`truth`) |
| `cpm_rate_hz` | the CPMs per sender-second, the same way |

The objects are the ones without V2X: the vehicles that sent no CPM in the run (the `bg_` cars and the trucks) and
the pedestrians. Every vehicle of Artery is a node with a station id, with or without services, so the node list
cannot tell them apart; in the control grid a car runs CA and CP together or nothing, so the CPM senders are the
equipped cars. At 100 % penetration the only objects are the trucks and the pedestrians of the events, so the metric
rests on fewer object ticks there (`pairs` in the tables). The receivers are the equipped cars that received at
least one CPM. The reliability is the one of the raw report (the sender's curve), not B3's belief after fusion: the
metric is the same for every rule, and it is the quantity the AoC rule promises to hold. The replay (B3's belief on
these runs, with the receiver's stopped state) is a later step.

## The pairs and the verdict

`pairs.csv` has one row per run and reference (`etsi`, the CP service of Artery untouched; `etsi_abs`, the rules
of ETSI on absolute positions with a CPM only when something is due): the ratios of the load (`bytes_ratio`,
`cbr_ratio`, `cpm_ratio`) and the differences of the service (`time_diff`, `known_diff`) against the reference run
of the same condition and seed. A run whose reference is not run yet gives no row (the command says so).

H4 on one pair: PASS when the rule's load is at most 80 % of the reference's and its time above the target is not
lower by more than one point, or when its time above the target is higher by more than one point at a load not
higher by more than one percent (`rulecheck.h4_verdict`, the same function as `aoc-rulecheck --against`). The load
side depends on the condition:

- no load station (`load` 0): the channel busy ratio (the load of the plan; the stations make the busy ratio);
- 12 or 36 load stations: the CPM bytes per sender-second (the load stations set most of the busy ratio there, and
  a rule that sends fewer CPMs changes it little; the bytes say what the rule asks of the channel).

Both verdicts are in the tables (`verdict_cbr`, `verdict_bytes`); `verdict` is the one of the condition's load side.

`cell_pairs.csv` has one row per condition, rule and reference: the mean pair over the seeds, the ratios as
geometric means (a ratio of 0.5 and one of 2 average to 1), each with its paired interval over the seeds (Student's
t, 3 degrees of freedom on the full grid, none with one seed). Two readings:

- `verdict`: the mean pair passes (the reading of the plan);
- `verdict_ci`: the worst end of the intervals passes too (the upper end of the load ratio, the lower end of the
  time difference); stricter, and `n/a` with one seed (pandas reads `n/a` as a missing value by default: read the
  tables with `keep_default_na=False, na_values=[""]`).

`h4.csv` has one row per rule and reference: the conditions that pass (in all, per load level, on the interval),
and a pooled reading over the seed pairs of the rule (48 on the full grid): the count that pass, and two one-sided
Wilcoxon signed-rank tests, `p_less_load` (the load ratio is under 0.8) and `p_time_held` (the time difference is
above minus one point). With 4 seeds nothing is significant per condition; the pooled tests are what supports H4
over the grid. `h4` is PASS when every one of the 12 conditions passes on its mean pair, FAIL when one does not, and
`incomplete (k/12)` while conditions are missing.

## The points of F9

`cells.csv` has one row per condition and rule: the mean over the seeds of each metric, its interval and its range.
Inside a condition only the seeds run for every rule present count (the means of the rules must rest on the same
traffic; the command lists the runs left out while the grid is half done). These are the points of F9:

- left and middle, the time above the target against the busy ratio and against the CPM bytes per sender-second,
  one marker per condition and rule; the colour is the rule (the ETSI rules hollow), the marker the load level; the
  dashed line is the Pareto frontier of each load level (the points no other point of the level beats on both
  axes: a lower or equal load with a higher or equal time, one of them strict). The frontier over the whole grid
  would say nothing about the loaded levels, since the load stations set their busy ratio;
- right, the paired effect against `etsi`: the load ratio on the condition's load side against the time
  difference, one marker per condition and rule, the region of H4 shaded (less load and the time held, or more time
  at equal load).

`frontier.csv` lists the points on the frontier over the grid, per load level and per condition (the five rules at
the same conditions); the summary counts in how many conditions each rule is on its condition's frontier. H4
expects the AoC rules there.

## Running it

```
aoc-control ROOT [--out results/e3] [--warmup 60] [--range 200] [--hold 5] [--jobs 4] [--reference etsi,etsi_abs] [--refresh]
aoc-grid-figures --only f09 [--results results] [--out figures]
```

`ROOT` is the results folder of the control grid (`~/aoc-sim/d1/results/control`). `runs.csv` in the output folder
is the cache of the per-run metrics: a run already there, with the same export (the time of its
`export_summary.json`) and the same `--warmup`, `--range` and `--hold`, is not read again; `--refresh` reads every
run again. The cache is written every ten runs, so a crash loses little. Reading a run takes a few seconds (more for
the heavy cells); `--jobs` reads several at once. The command prints `summary.txt`: the counts, the mean per rule
over the conditions, the H4 table, the conditions that do not pass, and the frontier.

## A first look (sandbox of this session, seed 0 of the low-density conditions)

The tables of the 30 runs are in `results/e3/sandbox/` (one seed per cell, so no intervals; the figure drawn from
them is `f09_control_sandbox.pdf` there). The pairs against `etsi`, the load ratio on the condition's load side
and the time above the target of the rule against the one of `etsi`:

| condition | etsi: time, bytes/sender-s | aoc | aoc2 | aocdev | etsi_abs |
| --- | --- | --- | --- | --- | --- |
| low/25/0 | 0.880, 666 | 0.60 of the busy ratio, -0.3 pt, PASS | 0.76, +0.0 pt, PASS | 0.63, -0.4 pt, PASS | 0.82, +0.0 pt, FAIL |
| low/25/12 | 0.864, 666 | 0.44 of the bytes, +1.2 pt, PASS | 0.75, +1.4 pt, PASS | 0.48, +1.1 pt, PASS | 0.90, +1.2 pt, PASS |
| low/25/36 | 0.735, 310 | 0.66, -5.0 pt, FAIL | 0.92, -0.2 pt, FAIL | 0.71, +0.1 pt, PASS | 0.99, +0.3 pt, FAIL |
| low/100/0 | 0.944, 632 | 0.61 of the busy ratio, -0.2 pt, PASS | 0.78, +0.1 pt, PASS | 0.64, -0.2 pt, PASS | 0.86, -0.0 pt, FAIL |
| low/100/12 | 0.938, 632 | 0.46 of the bytes, +0.2 pt, PASS | 0.78, +0.1 pt, PASS | 0.50, -0.2 pt, PASS | 0.97, +0.1 pt, FAIL |
| low/100/36 | 0.901, 296 | 0.72, -6.6 pt, FAIL | 0.94, -1.5 pt, FAIL | 0.74, -3.9 pt, FAIL | 1.03, -0.0 pt, FAIL |

What one seed at low density says (the workstation grid decides):

- **Free and loaded channel (0 and 12 stations)**: the AoC rule sends 44 to 46 % of the bytes of ETSI (290 against
  632 to 666 bytes per sender-second; 4.3 against 10 CPMs per sender-second) for the same time above the target
  (within 0.3 points, once 1.2 points above). `aocdev` costs 10 % more bytes than `aoc` for the same time; `aoc2`
  sends 76 to 78 % of the bytes of ETSI. ETSI on absolute positions sends 82 to 97 % of the bytes of `CpService`,
  not 80 %, so it does not pass H4 against it. The busy ratio moves with the rule at load 0 (0.030 to 0.018 at 25 %,
  0.060 to 0.037 at 100 %) and not at load 12 (0.19 to 0.21 for every rule): the load side of E2 holds.
- **Saturated channel (36 stations, busy ratio 0.50)**: DCC holds every rule at about 2 CPMs per sender-second,
  and the AoC rule with n = 1 loses 5 to 7 points of time above the target against ETSI while it still sends 66 to
  72 % of the bytes. The rule assumes its reports arrive: with one report per age limit, a lost or delayed CPM
  leaves the receiver under the target until the next one, about a second later, while ETSI repeats a moving object
  at every DCC opportunity (every 0.5 s). `aoc2` (n = 2) holds the time (within 0.2 points at 25 %, 1.5 under at
  100 %) at 92 to 94 % of the bytes: the second report buys back the time but not the saving. `aocdev` passes at
  25 % and loses 3.9 points at 100 %. The paired effect panel of F9 shows it: the AoC points sit in the region of
  H4 at load 0 and 12, and fall out of it at load 36.
- The time above the target is high at 100 % penetration (0.94 against 0.88 at 25 %) because the only objects
  there are the trucks and the pedestrians, which the many equipped cars see from all sides; the metric rests on
  75 000 object ticks there against 148 000 at 25 %.

The question for E4 is therefore the saturated channel: a rule that knows the DCC state (n = 2 when the channel is
saturated, or the period shortened by the expected loss) could keep the saving where the channel is free and the
time where it is not.
