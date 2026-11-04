# Results brought back from the workstation

The simulations run on the workstation (`artery-ext/scripts`); their parquet files stay there (the grid of D1 is
18 GB). This folder keeps the small tables that the analysis and the papers need, one subfolder per task.

| Folder | Content | From |
| --- | --- | --- |
| `d2/` | The quality check of the core grid (`aoc-gridcheck`, Nov 1, 2026): one row per run (`gridcheck_runs.csv`, 432), per event (`gridcheck_events.csv`, 1296), per cell (`gridcheck_cells.csv`, 36), and the printed report | D2 |
| `d3/` | The calibration of q (`aoc-fit-q`, Nov 3, 2026: `qfit.csv` per class, `qfit_curves.csv` per class and age, `qfit.pdf`; `qfit_moving.csv` for the objects moving at least 1 m/s) and the replay of the grid (`aoc-replay-grid`, Nov 3 and 4, 2026: `metrics.csv`, one row per run, way and hold, 2880 rows: the 432 runs with the hold of 5 s and the seeds 0 and 1 of every cell with the holds of 2 and 10 s; `bins.csv`, the reliability diagram per run, way and hold; `events.csv`, the belief of the egos in their pedestrian during the occlusion, per event and way) | D3 |
| `e1/` | The check of the AoC generation rule on the quick runs of E1 (Nov 4, 2026, in the sandbox of the session: 60 s, low density, 100 %, the rules `etsi`, `etsi_abs` and `aoc`): the tables of `aoc-rulecheck` for `aoc` against each ETSI run and the printed reports. See `docs/generation_rule.md` | E1 |
| `d4/` | The results database built from `d2/` and `d3/` by `aoc-database` (Nov 4, 2026): per run (`runs.csv`), per cell (`cells.csv`), per factor level (`factors.csv`), the tests of H1 and H3 (`h1*.csv`, `h3*.csv`), the hold check (`holds.csv`), the reliability diagrams (`reliability*.csv`), the events (`events.csv`) and `summary.md`. Rebuilt from the tables with the command; see `docs/database.md` | D4 |

The tables are written by the commands of the package on the results folder of the workstation; `docs/grid.md`
says what the columns hold.
