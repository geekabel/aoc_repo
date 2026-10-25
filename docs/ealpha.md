# E[alpha] on the single-link runs against the formulas of the note

Task C3, figure F4, hypothesis H2. The module is `aoc.analysis.ealpha`, the command `aoc-ealpha`, the figure script
`figures/f04_ealpha.py`. It takes the exported single-link runs of C1 and compares the expected reliability
E[alpha] measured on them with the three forms of step 3 of the theory note (task A3).

## What is compared

On a single link the object at the receiver has the age Delta = d0 + A: d0 the age at reception, A the time since
the last received update. Its reliability is alpha(Delta), the curve of step 2 (`aoc.theory.reliability.alpha`,
one curve per class of the note: sigma_x 0.3 m, sigma_v 0.5 m/s and the motion value q of the class). Step 3 gives
the average over time:

| Form | What | Where |
| --- | --- | --- |
| exact | alpha(d0 + a) integrated over the age law of A (numerical) | `averages.e_exact_periodic` |
| plateau | a plain formula, with the plateau surrogate of alpha (alpha = 1 up to D0, then exponential), the main result of A3 | `averages.e_plateau_periodic` |
| expo | a plain formula, with the exponential surrogate, the first idea of the plan | `averages.e_expo_periodic` |

The three forms are taken at the median d0 of the run.

## How E[alpha] is measured

Exactly in time, like the age in C2. For every received update i with age at reception d0_i (the column `age` of
the row) and a gap g_i until the next reception (or the end of the run), the time integral of alpha from d0_i to
d0_i + g_i; the sum over the updates, divided by the window. The integral of alpha is tabulated once per class on a
grid of 1 ms and read by interpolation (`AlphaIntegral`, checked against quadrature to 2e-5). The receptions are
the rows about objects the receiver never saw itself; the two links of the scenario are pooled. The age process
is the same for every class, so the measure is made for the five classes of the note on the same gaps.

## The noise and the PASS rule

As in C2, 1000 runs of the same window are drawn from the law (geometric gaps, the measured d0 values drawn again)
and give the standard deviation of the measured E[alpha]. A run passes when its gap to the exact law is within 5%
or within three times that standard deviation. Per class, over four runs or more, the gaps must average out
(within 2% or three times their own noise). The gap to the plateau formula is what the plan's H2 asks to be
below 5%: it is reported with the number of runs within 5%, and it holds the model error of the surrogate on top
of the noise.

## Run it

```bash
aoc-ealpha ~/aoc-sim/c1/latest --out f4_ealpha.pdf --table ealpha.csv
python figures/f04_ealpha.py [RESULT_FOLDER]      # the same, written next to the script as f04_ealpha.pdf
```

One block per class, one row per run: T, p, receptions, d0, the measured value, the three forms, the gap to each
and the noise; then a line over the runs. Options: `--warmup` (10 s), `--max-rel` (0.05), `--draws` (1000),
`--out`, `--table`.

## On a synthetic grid

The 16 cells of the plan as synthetic streams (the same three cars, delay 0.02 s, 330 s, seeds 0 to 15 in the
order of the grid): PASS for the five classes. The gap to the exact law is within three times the noise on every
run; the largest is 5.2% (the car at T = 1 s, p = 0.3, noise 2.1%) and 4.3% for the truck on the same cell. The
gap to the plateau formula is within 5% on every run and every class (largest 4.7%, the same cell), with a
mean of -0.2 to -1.2% per class: the surrogate is slightly above the exact law, by 1.1% for the parked vehicle
whose alpha at age 0 is 0.989 and not 1. With one seed per cell this is one seed away from a run above 5%; the
noise columns say so. The exponential surrogate is 1 to 23% too low for the moving classes, as A3 found: it is
not the one to use.

## Figure F4

One panel per class (pedestrian, car), E[alpha] against the update rate (log axis), one line per loss p (the
plateau formula at the median d0 of the runs of that loss), the runs as dots, and the largest gap of a run
to the formula at its own d0 printed in the panel. Accept when the printed gap is below 5%. On the real runs
d0 differs between cells, so a dot can sit a little off its line even when its printed gap is small.

## Limits

The curve alpha is the one of the note, with the assumed motion values (B5 found the assumed q too careful on the
Artery runs; this does not matter here, where the same curve is used on both sides). The forms take d0 as one
constant, the median; on the real runs d0 varies by up to 0.1 s inside a run, which the measure follows and the
forms do not. One seed per cell.
