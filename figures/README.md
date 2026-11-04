# Figures of the papers

Each figure of the plan (F1 to F10) gets one script here, named `f03_ages.py`, `f05_calibration.py` and so on.
`make figures` runs every script `figures/f*.py`, after the script of the theory note.

Rules for a figure script: it reads the results (the results database once it exists, task D4), it writes one PDF
(vector) next to itself, and it prints the numbers quoted in the text. When the results are not on the machine,
it says so and stops without an error.

| Script | Figure | Data |
| --- | --- | --- |
| `f02_alpha.py` | F2, the reliability curve alpha(age) per class: the curve of the note (assumed q), the fitted curve and the measured hit rate of the calibration runs, with the half-lives | `results/d3/qfit.csv` and `qfit_curves.csv` (in the repository; or the results folder given on the command line) |
| `f03_ages.py` | F3, the age at the receiver on the single-link runs against the law of the note | the C1 runs: the folder given on the command line, or `AOC_C1_RESULTS`, or `~/aoc-sim/c1/latest` |
| `f04_ealpha.py` | F4, E[alpha] against the update rate for several losses, the formula of the note against the runs | the same |
| `f05_calibration.py` | F5, the reliability diagram of the belief that an object exists, one curve per way B0 to B4 at a hold of 5 s, with the share of the objects per bin under it | `results/d4/reliability.csv` (the same) |
| `f06_money.py` | F6, the belief of the ego in the hidden pedestrian over time, with V2X and with its own sensors only | the occluded-VRU runs of C5: the folder given on the command line, or `AOC_C5_RESULTS`, or `~/aoc-sim/c5/latest` |
| `f07_awareness.py` | F7, the awareness ratio and the calibration error against the penetration, one line per way with its 95 % band over the evaluation runs of the level | `results/d4/runs.csv` (the same) |
| `f08_stale.py` | F8, the stale-confidence rate and the ghost rate against the channel busy ratio, one marker per cell and way with its interval | `results/d4/cells.csv` (the same) |

The four scripts of the grid call `aoc-grid-figures` (`aoc.analysis.gridfigures`), which draws them all at once:
`aoc-grid-figures [--results results] [--out figures] [--only f05]`. They need no simulation data, only the tables
of D3 and D4 in the repository.
