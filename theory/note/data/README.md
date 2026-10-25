# Data of the check section

The three tables of Section VIII of the note (the check in a network simulator), as the
analysis commands wrote them on the 16 single-link runs of C1 (Artery, 330 s each):

| File | Command | Content |
| --- | --- | --- |
| `check_grid.csv` | `aoc-link RESULT --grid --table check_grid.csv` | One row per run: period, loss and object share against T and p |
| `check_ages.csv` | `aoc-ages RESULT --table check_ages.csv` | One row per run: measured and law mean age, Kolmogorov distance, noise, d0 |
| `check_ealpha.csv` | `aoc-ealpha RESULT --table check_ealpha.csv` | One row per run and class: measured E[alpha], the exact law and the two formulas |

The two figures of that section, `figs/fig_check_ages.pdf` and `figs/fig_check_ealpha.pdf`, are the
outputs `--out` of `aoc-ages` and `aoc-ealpha` on the same runs (figures F3 and F4 of the plan).
`make figures` does not draw them again: they need the exported runs, which are not in the repository.
