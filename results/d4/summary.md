# The results database of the core grid (task D4)

360 evaluation runs (36 cells x 10 seeds) replayed with the hold of 5 s; 72 runs with the holds of 2 and 10 s. The calibration runs (seeds 10 and 11) served the fit of q and are left out. Every mean below weighs the runs equally (each run one value); the reliability diagram weighs the objects.

## The motion value q (fitted on the calibration runs)

| class | q assumed | q fitted | half-life assumed (s) | half-life fitted (s) | calibration error assumed | fitted |
| --- | --- | --- | --- | --- | --- | --- |
| heavy_truck | 1.44 | 0.118 | 1.32 | 2.14 | 0.182 | 0.018 |
| passenger_car | 4 | 0.269 | 0.99 | 1.91 | 0.211 | 0.039 |
| pedestrian | 0.5 | 0.0252 | 1.70 | 2.38 | 0.144 | 0.007 |

## The five ways over the evaluation runs (hold 5 s, mean and 95 % interval over the runs)

| way | calibration error | Brier | coverage | awareness | RMSE (m) | stale rate | ghost rate | time to confident (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B0 | 0.433 [0.421, 0.446] | 0.431 [0.419, 0.444] | 0.000 [0.000, 0.000] | 0.843 [0.831, 0.856] | 0.438 [0.433, 0.443] | 0.990 [0.990, 0.990] | 0.990 [0.990, 0.990] | 0.180 [0.120, 0.240] |
| B1 | 0.140 [0.137, 0.143] | 0.142 [0.139, 0.144] | 0.575 [0.560, 0.591] | 0.827 [0.815, 0.840] | 0.438 [0.433, 0.443] | 0.000 [0.000, 0.000] | 0.019 [0.019, 0.019] | 0.193 [0.132, 0.254] |
| B2 | 0.111 [0.108, 0.113] | 0.122 [0.119, 0.124] | 0.581 [0.567, 0.596] | 0.827 [0.814, 0.839] | 0.416 [0.411, 0.421] | 0.065 [0.063, 0.066] | 0.058 [0.058, 0.059] | 0.203 [0.141, 0.264] |
| B3 | 0.028 [0.028, 0.029] | 0.101 [0.098, 0.103] | 0.461 [0.444, 0.479] | 0.834 [0.822, 0.847] | 0.438 [0.433, 0.443] | 0.240 [0.238, 0.242] | 0.220 [0.219, 0.221] | 0.193 [0.132, 0.254] |
| B4 | 0.032 [0.031, 0.032] | 0.102 [0.099, 0.104] | 0.420 [0.407, 0.433] | 0.834 [0.822, 0.847] | 0.438 [0.433, 0.443] | 0.246 [0.244, 0.248] | 0.222 [0.221, 0.223] | 0.193 [0.132, 0.254] |

## H1: the calibration error of B3 against the other ways (paired by run)

| against | runs | gain of B3 (mean over the runs) | 95 % interval | over the cells: mean, 95 % interval | lowest run | runs where B3 is not worse | verdict (at least 30 % against B0 and B1) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B0 | 360 | 93.4 % | [93.3 %, 93.5 %] | 93.4 % [93.2 %, 93.5 %] | 89.8 % | 100 % | PASS |
| B1 | 360 | 80.0 % | [79.8 %, 80.2 %] | 80.0 % [79.6 %, 80.4 %] | 73.6 % | 100 % | PASS |
| B2 | 360 | 74.6 % | [74.3 %, 74.9 %] | 74.6 % [74.0 %, 75.2 %] | 65.4 % | 100 % | n/a |
| B4 | 360 | 10.0 % | [9.6 %, 10.4 %] | 10.0 % [9.1 %, 10.9 %] | -14.4 % | 97 % | n/a |

The interval over the runs treats the 360 runs as one sample; the interval over the cells (the 36 cell means) is the one that respects the design. The verdict reads the lower end of the two-sided 95 % interval of the cell (one-sided at 97.5 %), with no correction for the 36 cells. The rule of the plan applies where objects get old (B0's calibration error at least 0.05: regime aged); where every object is fresh, B3 must not be worse than B0 in every run.

Per cell against B0: 36 of 36 cells pass (36 aged, 0 fresh); the smallest gain is 92.3 % (low/10/etsi), its lower end 91.4 %.

Per cell against B1: 36 of 36 cells pass (36 aged, 0 fresh); the smallest gain is 78.2 % (low/100/10hz), its lower end 76.8 %.

## H3: the stale-confidence rate of B3 against the other ways

| against | B3 | the other | difference (the other minus B3), 95 % interval | ratio B3 / the other | runs where B3 is not above | awareness, B3 minus the other: mean [95 % interval], lowest run | verdict (under B0 in every run, ratio at most 0.5, awareness within 2 points) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B0 | 0.240 | 0.990 | 0.751 [0.748, 0.753] | 0.242 | 100 % | -0.0090 [-0.0094, -0.0086], -0.0269 | PASS |
| B1 | 0.240 | 0.000 | -0.240 [-0.242, -0.238] | 23975.000 | 0 % | +0.0071 [+0.0069, +0.0074], +0.0024 | n/a |
| B2 | 0.240 | 0.065 | -0.175 [-0.176, -0.174] | 3.712 | 0 % | +0.0073 [+0.0070, +0.0077], +0.0012 | n/a |

## The hold (the 72 runs of the hold check, the seeds 0 and 1 of every cell, at every hold)

| hold (s) | B0: calibration error, stale rate | B1: calibration error, stale rate | B2: calibration error, stale rate | B3: calibration error, stale rate | B4: calibration error, stale rate |
| --- | --- | --- | --- | --- | --- |
| 2 | 0.094, 0.992 | 0.098, 0.000 | 0.063, 0.247 | 0.033, 0.630 | 0.040, 0.664 |
| 5 | 0.445, 0.990 | 0.141, 0.000 | 0.111, 0.062 | 0.029, 0.237 | 0.032, 0.243 |
| 10 | 0.656, 0.990 | 0.097, 0.000 | 0.079, 0.023 | 0.023, 0.097 | 0.025, 0.099 |

## The reliability diagram of B3 (hold 5 s, every evaluation run)

| bin | objects | mean belief | share really there |
| --- | --- | --- | --- |
| 0.0 to 0.1 | 61667278 | 0.077 | 0.106 |
| 0.1 to 0.2 | 83195696 | 0.142 | 0.169 |
| 0.2 to 0.3 | 39269237 | 0.243 | 0.254 |
| 0.3 to 0.4 | 29182324 | 0.344 | 0.318 |
| 0.4 to 0.5 | 12606251 | 0.455 | 0.303 |
| 0.5 to 0.6 | 5926136 | 0.560 | 0.437 |
| 0.6 to 0.7 | 4794050 | 0.658 | 0.545 |
| 0.7 to 0.8 | 4658694 | 0.761 | 0.631 |
| 0.8 to 0.9 | 4593095 | 0.857 | 0.715 |
| 0.9 to 1.0 | 120336676 | 0.990 | 0.970 |

Calibration error pooled over the objects (the diagram above, every way): B0 0.524, B1 0.160, B2 0.127, B3 0.033, B4 0.037.

## The factors (hold 5 s, B3 and B1)

| density | way | calibration error | awareness | stale rate |
| --- | --- | --- | --- | --- |
| low | B1 | 0.123 [0.118, 0.128] | 0.817 | 0.000 |
| low | B3 | 0.025 [0.023, 0.026] | 0.824 | 0.252 |
| medium | B1 | 0.142 [0.137, 0.146] | 0.830 | 0.000 |
| medium | B3 | 0.028 [0.027, 0.029] | 0.836 | 0.238 |
| high | B1 | 0.156 [0.152, 0.160] | 0.834 | 0.000 |
| high | B3 | 0.032 [0.031, 0.033] | 0.843 | 0.229 |

| pen | way | calibration error | awareness | stale rate |
| --- | --- | --- | --- | --- |
| 10 | B1 | 0.111 [0.107, 0.115] | 0.665 | 0.000 |
| 10 | B3 | 0.020 [0.019, 0.021] | 0.675 | 0.259 |
| 25 | B1 | 0.131 [0.127, 0.135] | 0.797 | 0.000 |
| 25 | B3 | 0.026 [0.025, 0.027] | 0.804 | 0.245 |
| 50 | B1 | 0.150 [0.146, 0.154] | 0.880 | 0.000 |
| 50 | B3 | 0.031 [0.030, 0.032] | 0.886 | 0.235 |
| 100 | B1 | 0.170 [0.167, 0.173] | 0.965 | 0.000 |
| 100 | B3 | 0.035 [0.035, 0.036] | 0.973 | 0.221 |

| msg | way | calibration error | awareness | stale rate |
| --- | --- | --- | --- | --- |
| etsi | B1 | 0.141 [0.136, 0.146] | 0.824 | 0.000 |
| etsi | B3 | 0.028 [0.027, 0.029] | 0.832 | 0.231 |
| 2hz | B1 | 0.140 [0.134, 0.145] | 0.826 | 0.000 |
| 2hz | B3 | 0.028 [0.027, 0.030] | 0.833 | 0.236 |
| 10hz | B1 | 0.140 [0.135, 0.145] | 0.831 | 0.000 |
| 10hz | B3 | 0.029 [0.028, 0.030] | 0.838 | 0.253 |

## The occluded pedestrian (1080 events of the evaluation runs, B3)

Belief in the hidden pedestrian during the gap with V2X: mean 0.990, lowest 0.990; with the own sensors only at the end of the gap: 0.432. CPM rows about the pedestrian received in the gap: 52 on average (34 at 10 % penetration, 74 at 100 %).
