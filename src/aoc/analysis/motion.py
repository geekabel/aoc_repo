"""How do the objects of a run really move? (support for the choice of the motion value of the reliability curve)

The reliability curve of the theory note assumes that the error of a constant-velocity prediction is Gaussian,
with a variance that grows as q * age^3 / 3. This module measures the real error on the ground truth of a run:
the true state of an object is taken at one time, moved forward at constant velocity, and compared with the
true state at the later time. No sensor and no noise is involved.

    from aoc.analysis.motion import true_motion
    table = true_motion(run.truth)          # one line per horizon
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_motion(truth: pd.DataFrame, horizons=(0.5, 1.0, 2.0), epsilon: float = 1.5, classes=None) -> pd.DataFrame:
    """The error of a perfect constant-velocity prediction, for several horizons.

    ``truth`` is the truth table of a run (columns t, obj_id, x, y, vx, vy, class). One line per horizon:

      horizon        how far ahead the prediction goes, in s
      n              number of predictions
      q_effective    the motion value that gives the measured spread at this horizon: 3 * spread^2 / horizon^3
      spread         size of the error per axis, in m: the root of the mean squared error, the two axes together
      median_error   median distance between the predicted and the true place, in m
      within         share of the predictions that are within ``epsilon`` of the true place
      within_if_gaussian   the share that a Gaussian error with the same spread would give (what the curve assumes)

    When ``within`` is well above ``within_if_gaussian``, most objects move straight and a few move a lot.
    """
    if not np.isfinite(truth[["t", "x", "y", "vx", "vy"]].to_numpy(dtype=float, na_value=np.nan)).all():
        raise ValueError("the truth table has an empty time, place or speed")
    times = np.sort(truth["t"].unique())
    if len(times) < 2:
        raise ValueError("the truth table needs at least two times")
    step = float(np.median(np.diff(times)))
    frame = truth if classes is None else truth[truth["class"].isin(list(classes))]
    lines = []
    for horizon in horizons:
        k = int(round(horizon / step))
        if k < 1 or abs(k * step - horizon) > 1e-6:
            raise ValueError("the horizon %g s is not a multiple of the step of the truth (%g s)" % (horizon, step))
        errors = []
        for _, track in frame.sort_values("t").groupby("obj_id", sort=False):
            t, x, y, vx, vy = (track[name].to_numpy(dtype=float) for name in ("t", "x", "y", "vx", "vy"))
            if len(t) <= k:
                continue
            whole = np.abs((t[k:] - t[:-k]) - horizon) < 1e-6                  # leaves out the pairs over a hole in the truth
            errors.append(np.column_stack([x[k:] - x[:-k] - horizon * vx[:-k], y[k:] - y[:-k] - horizon * vy[:-k]])[whole])
        errors = np.concatenate(errors) if errors else np.zeros((0, 2))
        if not len(errors):
            lines.append(dict(horizon=horizon, n=0, q_effective=np.nan, spread=np.nan, median_error=np.nan, within=np.nan, within_if_gaussian=np.nan))
            continue
        variance = float((errors**2).mean())
        distance = np.hypot(errors[:, 0], errors[:, 1])
        gaussian = float(-np.expm1(-epsilon**2 / (2.0 * variance))) if variance > 0.0 else 1.0
        lines.append(dict(horizon=horizon, n=len(errors), q_effective=3.0 * variance / horizon**3, spread=np.sqrt(variance),
                          median_error=float(np.median(distance)), within=float((distance <= epsilon).mean()), within_if_gaussian=gaussian))
    return pd.DataFrame(lines)
