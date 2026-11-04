# Results brought back from the workstation

The simulations run on the workstation (`artery-ext/scripts`); their parquet files stay there (the grid of D1 is
18 GB). This folder keeps the small tables that the analysis and the papers need, one subfolder per task.

| Folder | Content | From |
| --- | --- | --- |
| `d2/` | The quality check of the core grid (`aoc-gridcheck`, Nov 1, 2026): one row per run (`gridcheck_runs.csv`, 432), per event (`gridcheck_events.csv`, 1296), per cell (`gridcheck_cells.csv`, 36), and the printed report | D2 |
| `d3/` | The calibration of q (`aoc-fit-q`, Nov 3, 2026: `qfit.csv` per class, `qfit_curves.csv` per class and age, `qfit.pdf`; `qfit_moving.csv` for the objects moving at least 1 m/s) and the replay of the grid (`aoc-replay-grid`, Nov 3 and 4, 2026: `metrics.csv`, one row per run, way and hold, 2880 rows: the 432 runs with the hold of 5 s and the seeds 0 and 1 of every cell with the holds of 2 and 10 s; `bins.csv`, the reliability diagram per run, way and hold; `events.csv`, the belief of the egos in their pedestrian during the occlusion, per event and way) | D3 |

The tables are written by the commands of the package on the results folder of the workstation; `docs/grid.md`
says what the columns hold.
