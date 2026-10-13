"""Is a confidence right? The calibration error (first piece of task B8, needed by the check of task B5).

A confidence is well calibrated when, among the objects that got a confidence near 0.8, about 80% are really there.

    from aoc.analysis.calibration import expected_calibration_error
    error = expected_calibration_error(confidence, hit)       # 0 is perfect

``confidence`` holds one number between 0 and 1 per object. ``hit`` says for each object if it is really there
(True or False). The objects are put in bins of equal width by their confidence (10 by default). In each bin
the gap between the mean confidence and the share of objects that are really there is measured. The error is
the mean of these gaps, weighted by the number of objects of each bin.

One limit of this error: it looks at groups of objects, not at each object. If every object gets the same
confidence, say 0.99, and 99% of them are there, the error is 0, even if the confidence cannot tell which
objects are the missing ones. The Brier score does not have this limit: it is the mean of
(confidence - hit)^2 over the objects. It is small only when each object gets a confidence close to its truth.
Both are given.
"""
from __future__ import annotations

import numpy as np
from scipy import optimize


class CalibrationBins:
    """Collects confidences and hits piece by piece, to give the calibration error at the end."""

    def __init__(self, bins: int = 10):
        if int(bins) != bins or bins < 1:
            raise ValueError("the number of bins must be a whole number, at least 1")
        self.bins = int(bins)
        self.count = np.zeros(self.bins, dtype=np.int64)
        self.confidence = np.zeros(self.bins)
        self.hits = np.zeros(self.bins)
        self.squared = 0.0                                    # sum of (confidence - hit)^2, for the Brier score

    def add(self, confidence, hit) -> None:
        confidence = np.asarray(confidence, dtype=float).ravel()
        hit = np.asarray(hit, dtype=float).ravel()
        if confidence.shape != hit.shape:
            raise ValueError("one hit is needed per confidence")
        if not len(confidence):
            return
        if not np.isfinite(confidence).all() or (confidence < -1e-9).any() or (confidence > 1.0 + 1e-9).any():
            raise ValueError("the confidences must be numbers between 0 and 1")
        if not np.isin(hit, (0.0, 1.0)).all():
            raise ValueError("the hits must be True or False (1 or 0)")
        self.squared += float(((confidence - hit) ** 2).sum())
        index = np.clip((np.clip(confidence, 0.0, 1.0) * self.bins).astype(np.int64), 0, self.bins - 1)      # a confidence of 1 is in the last bin
        self.count += np.bincount(index, minlength=self.bins)
        self.confidence += np.bincount(index, weights=confidence, minlength=self.bins)
        self.hits += np.bincount(index, weights=hit, minlength=self.bins)

    @property
    def total(self) -> int:
        return int(self.count.sum())

    def error(self) -> float:
        """The expected calibration error. NaN when nothing was added."""
        if not self.total:
            return float("nan")
        used = self.count > 0
        gaps = np.abs(self.confidence[used] - self.hits[used]) / self.count[used]
        return float((gaps * self.count[used]).sum() / self.total)

    def brier(self) -> float:
        """The Brier score: the mean of (confidence - hit)^2. 0 is perfect. NaN when nothing was added."""
        return self.squared / self.total if self.total else float("nan")

    def table(self) -> list[dict]:
        """One line per bin that holds objects: its limits, the number of objects, the mean confidence, the share really there."""
        return [dict(low=b / self.bins, high=(b + 1) / self.bins, count=int(self.count[b]),
                     confidence=float(self.confidence[b] / self.count[b]), frequency=float(self.hits[b] / self.count[b]))
                for b in range(self.bins) if self.count[b]]


def expected_calibration_error(confidence, hit, bins: int = 10) -> float:
    """The expected calibration error of confidences against what really happened. 0 is perfect."""
    collected = CalibrationBins(bins)
    collected.add(confidence, hit)
    return collected.error()


def brier_score(confidence, hit) -> float:
    """The Brier score of confidences against what really happened: the mean of (confidence - hit)^2. 0 is perfect."""
    collected = CalibrationBins(1)
    collected.add(confidence, hit)
    return collected.brier()


def fit_scalar(error, low: float, high: float) -> float:
    """The value between ``low`` and ``high`` that gives the smallest error. ``error`` takes one number and returns one number."""
    if not 0.0 < low < high:
        raise ValueError("the limits must be positive, the low one below the high one")
    best = optimize.minimize_scalar(lambda u: error(float(np.exp(u))), bounds=(np.log(low), np.log(high)), method="bounded",
                                    options=dict(xatol=1e-4))
    return float(np.exp(best.x))
