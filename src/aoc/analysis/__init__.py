"""Metrics and aggregation of the results.

Written:
  calibration  the calibration error and the Brier score of a confidence, and a search for the value that fits best (B5, B8)
  motion       how the objects of a run really move, measured on the ground truth (support for B5)
  metrics      the metrics of a run for the five ways of the plan: network, awareness, RMSE, calibration, coverage,
               stale and ghost rates, time to confident (B8, command ``aoc-evaluate``)
  link         the single-link runs: read the grid of exported runs and check each one against its period and its loss
               (C1, command ``aoc-link``)
  ages         the age at the receiver of the single-link runs against the law of the note, figure F3 (C2, command ``aoc-ages``)
  ealpha       E[alpha] measured on the single-link runs against the three forms of the note, figure F4 (C3, command ``aoc-ealpha``)
  vru          the occluded-VRU runs: the timeline of the event in the logs of each run, and its check (C5, command ``aoc-vru``)
  trace        the belief of one vehicle in one true object over time, with V2X and with its own sensors only (C7)
  pilot        the pilot through the whole pipeline: metrics per run, the traces of the ego about the pedestrian, figure F6
               (C7, command ``aoc-pilot``)
  grid         the core grid of the plan: the cells and the run numbers, the index of the exported runs (D1, command ``aoc-grid``)
  gridcheck    the quality check of the grid: every run against its cell, the three events of each run, a table per cell
               (D2, command ``aoc-gridcheck``)
  qfit         the motion value q of the reliability curve fitted per class on the calibration runs (D3, command ``aoc-fit-q``)
  gridreplay   the replay of the five ways on every run of the grid, one receiver at a time (D3, command ``aoc-replay-grid``)
  database     the results database: the tables of D3 per cell with confidence intervals, the tests of H1 and H3 (D4, command ``aoc-database``)

Planned: the figures of the grid (task D5) read the database.
"""
