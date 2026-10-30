# The pilot through the whole pipeline, and figure F6

Task C7. The gate M3 of the plan asks that the occluded-VRU pilot runs end to end. C5 ran the scenario in Artery
and found the occlusion in the logs; this task runs the pipeline of B2 to B8 on those runs and reads off what the
plan wants: the metrics of the five ways on each run, the belief of the ego in the hidden pedestrian over time,
and figure F6, the "money figure" of the paper. Code: `aoc.analysis.trace` (the belief over time),
`aoc.analysis.pilot` (the command `aoc-pilot`), `figures/f06_money.py`.

## The belief of one vehicle in one object over time

`belief_trace(run, ego, target, model, ways, local_only)` follows one true object through the ticks of one
vehicle. At every tick it runs the pipeline (align, associate, evidence, discount, fuse) and writes down the
belief and the plausibility of the fused object that stands for the true object (the one with the highest belief
when the association made two), how many of the vehicle's own sensor rows and how many new CPM rows are about
the object, how many CPM objects about it are in the vehicle's memory, and how many fused objects stand for it.

Two views of the same run (`both_views`):

| View | What the vehicle fuses | When its own sensors miss the object |
| --- | --- | --- |
| with V2X | its sensors and the CPMs it received | the CPMs carry it, else the memory of the CPMs ages (hold 5 s) |
| own sensors | its sensors only (the CPM rows are taken out) | its last own report, remembered and aged: the rows of that tick fused as the pipeline fused them, then discounted once by their age with the law of the way (the proposed law lets the belief fall along the curve of the note; full trust keeps it; the cutoff keeps it too, its age limit being for received objects); forgotten after the hold (5 s), like a CPM |

The memory of the own report is a device of the trace, not a step of the pipeline, which keeps a memory of the
CPMs (hold 5 s) and none of the own detections: with V2X, when the ego misses the object and no CPM is there,
the belief is "nothing known" (NaN). The dashed curve of F6 answers the question "what would the ego believe of
its last own report, read on the curve of the note". The age is counted once for the fused report, not once per
sensor: the three sensors of the car saw one object at one time, and it is that one object that moved since.
The memory starts at the first tick of the run, whatever the window drawn.

The plausibility is 1 as long as no source speaks against the object, so the band between belief and
plausibility is 1 minus the belief: "the band tightens" means the belief rises.

## The pilot command

`aoc-pilot ROOT [--weather rain] [--out pilot.csv] [--traces traces.csv] [--figure f6.pdf] [--figure-run NAME] [--no-metrics]`

For every exported run under `ROOT`: the timeline of C5 (`aoc.analysis.vru`: when the ego first sees the
pedestrian, loses it and sees it again, the first CPM in the gap), the two traces of the ego about the pedestrian
from 3 s before the first sight (or the loss) to 3 s after the ego sees it again, the metrics of the five ways on
the whole run (`aoc.analysis.metrics`, about 90 s per run on the workstation, 3 minutes here; `--no-metrics`
skips them), and the checks. The event times come from the raw logs; the traces from the replay with the sensor
tables of the weather (the same in the clear; in rain the tables may take rows out, and the traces are the ones
to read). Figure F6 is drawn for the first run with two
helpers (or the run named). The tables: one row per run and way for the metrics, one row per run, view, way and
tick for the traces.

A run passes (on the proposed law B3) when the occlusion of C5 is there; the ego receives CPM rows about the
pedestrian during it and its belief with V2X stays at or above 0.9 on average over the occlusion; and at the end
of the occlusion its belief with V2X is at least 0.3 above the belief its own sensors alone would leave it. The
last rule is the acceptance line of F6 in the plan, "the band tightens visibly": it is relative because the own
belief of a pedestrian falls to about 0.6 after 1.5 s (the shortest occlusion C5 accepts) and to 0.2 after 2.7 s.

## The pilot run aoc-vru-2 of C5 (helpers = 2), read here

One of the six runs of the pilot (1, 2, 3 helpers x 2 seeds) was brought here; the six are read on the
workstation with `aoc-pilot ~/aoc-sim/c5/latest` (C7 tab of the document).

- The ego sees the pedestrian at 16.9 s, loses it at 17.7 s (62 m) and sees it again at 20.4 s (24 m).
- With V2X the belief of the proposed law stays at 0.99 through the 2.7 s (11 CPM rows from helper_a and
  helper_b, the first 0.04 s after the loss). With its own sensors only the belief of the last report falls
  along the curve of the pedestrian to 0.21 at the end of the occlusion. Full trust (B0) says 0.998 with V2X.
- Before the ego sees the pedestrian, from 14.0 s to 16.8 s, the only thing it has is a CPM of helper_b
  measured at 13.8 s, when helper_b looked into the alley while passing it: a report that ages while the
  pedestrian walks on. At 16.8 s it is 3.0 s old and 3.6 m from the truth; the proposed law gives it 0.15, full
  trust 0.99. The figure says so ("an old CPM report ages"). helper_b lost the pedestrian at 14.0 s and found it
  again at 16.9 s under a new track id, so the old report stays in the ego's memory until its hold (18.9 s).
- The association splits the pedestrian into two fused objects during the occlusion (the old report of helper_b,
  5 m away, alone in its group) and into three for 0.5 s (18.5 to 18.8 s: the two fresh reports of helper_a and
  helper_b drew noise errors of 0.9 m on opposite sides, 1.65 m apart with a spread of 0.3 m, a one-in-a-hundred
  draw that the gate at 99% refuses and that lasts until the next CPM). The trace reports the highest belief,
  which is 0.99 in every case. Worth a look when the association is revisited; it does not change the figure.
- Metrics of the whole run, clear weather, reliability tables at 1: calibration error B0 0.036, B1 0.017,
  B2 0.010, B3 0.016, B4 0.015; Brier 0.041, 0.012, 0.010, 0.011, 0.011; awareness 47% for every way. With
  the guessed reliability tables of C6, B3 had 0.040 (see `sensors.md`).

## Figure F6

One panel for the belief over time: the band from belief to plausibility and the belief line with V2X (proposed
law), the belief with the own sensors only (dashed), full trust with V2X (dotted), the target 0.9, and four
event lines (the ego sees the pedestrian, loses it, the first CPM, sees it again). Under it a strip of events:
the ego's own detections and the CPM rows about the pedestrian it receives. `figures/f06_money.py` draws it from
the results folder (`AOC_C5_RESULTS`, or `~/aoc-sim/c5/latest`, or the folder given) and writes the traces next
to it; a FAIL of the checks still draws the figure. The palette and the fonts are the ones of figures F3 and F4;
the polish (task F3 of the plan) comes with the paper.

## Tests

`tests/test_pilot.py`, on a synthetic scenario: a pedestrian walking slowly, the ego behind it and a helper ahead
of it, both within sensor range, the ego's own rows about the pedestrian cut out for 3 s (the occlusion). The
view without V2X; the trace with V2X (the belief stays above 0.9 through the gap on the helper's CPMs) and with
the own sensors only (the belief falls monotonically along the curve of the pedestrian and jumps back; full trust
keeps the score; the memory is forgotten after the hold); NaN where the ego knows nothing; the pilot passing on that run and failing when the helper
sends nothing; two runs through the command with the metrics, the traces, the figure (the run with two helpers is
drawn); the figure script, with and without results.
