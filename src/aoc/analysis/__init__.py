"""Metrics and aggregation of the results.

Written:
  calibration  the calibration error and the Brier score of a confidence, and a search for the value that fits best (B5, B8)
  motion       how the objects of a run really move, measured on the ground truth (support for B5)
  metrics      the metrics of a run for the five ways of the plan: network, awareness, RMSE, calibration, coverage,
               stale and ghost rates, time to confident (B8, command ``aoc-evaluate``)
  link         the single-link runs: read the grid of exported runs and check each one against its period and its loss
               (C1, command ``aoc-link``)
  ages         the age at the receiver of the single-link runs against the law of the note, figure F3 (C2, command ``aoc-ages``)

Planned: the results database over the runs (task D4).
"""
