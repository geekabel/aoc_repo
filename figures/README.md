# Figures of the papers

Each figure of the plan (F1 to F10) gets one script here, named `f03_ages.py`, `f05_calibration.py` and so on.
`make figures` runs every script `figures/f*.py`, after the script of the theory note.

Rules for a figure script: it reads the results (the results database once it exists, task D4), it writes one PDF
(vector) next to itself, and it prints the numbers quoted in the text. When the results are not on the machine,
it says so and stops without an error.

| Script | Figure | Data |
| --- | --- | --- |
| `f03_ages.py` | F3, the age at the receiver on the single-link runs against the law of the note | the C1 runs: the folder given on the command line, or `AOC_C1_RESULTS`, or `~/aoc-sim/c1/latest` |
| `f04_ealpha.py` | F4, E[alpha] against the update rate for several losses, the formula of the note against the runs | the same |
| `f06_money.py` | F6, the belief of the ego in the hidden pedestrian over time, with V2X and with its own sensors only | the occluded-VRU runs of C5: the folder given on the command line, or `AOC_C5_RESULTS`, or `~/aoc-sim/c5/latest` |
