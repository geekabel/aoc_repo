"""The evidence that each object brings (second part of task B4).

Each object that a source reports is turned into two mass functions:

  existence   on the frame {E, notE}. The source says "this object exists" with a score s between 0 and 1.
              The mass function is m({E}) = s and m({E, notE}) = 1 - s.
              It never puts mass on "does not exist": a report is support for the object, never against it.
  class       on the frame {car, truck, bicycle, pedestrian}. The source names a class with a confidence c.
              The mass function is m({class}) = c and m(whole frame) = 1 - c.
              A class that the frame does not know (or no class at all) gives no information: all mass on the whole frame.

This is step 3 of the pipeline of the plan. The masses are not yet lowered for the age of the object or for
the reliability of the source: that is the discounting, the next step (task B5).

Where the score s comes from
  1. If the source gives a score (column ``exist_score`` of the observations), it is used.
  2. Else the score comes from a table, by the origin of the measurement (column ``meas_src``).
     Artery gives no score, so today every score comes from this table.
  The values of the table are assumed values, like the motion values of the theory note.

    from aoc.fusion.bba import build_evidence
    evidence = build_evidence(aligned.objects)
    evidence.existence        # one row per object, columns: empty, {E}, {notE}, {E, notE}
    evidence.classes          # one row per object, 16 columns (the sets of the four classes)

Run this module to check a run:  python -m aoc.fusion.bba RUN_FOLDER      (command ``aoc-evidence``)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

from . import belief
from .alignment import align
from .belief import CLASS, EXISTENCE
from .noise import NoiseModel
from .replay import Run, load_run

#: Class of the frame for each class name of the observations table. A name that is not here gives no class evidence.
CLASS_OF_LABEL: dict[str, str] = {
    "passenger_car": "car",
    "light_truck": "truck",
    "heavy_truck": "truck",
    "bus": "truck",
    "trailer": "truck",
    "tram": "truck",
    "special_vehicle": "truck",
    "cyclist": "bicycle",
    "pedestrian": "pedestrian",
}

#: Existence score of an object, by the origin of its measurement. Assumed values.
EXISTENCE_SCORE: dict[str, float] = {"radar": 0.9, "fov": 0.9, "see_through": 0.9, "cam_msg": 0.9}


@dataclass(frozen=True)
class EvidenceModel:
    """How a reported object becomes evidence.

    existence_score     score s by origin of the measurement (column ``meas_src``)
    existence_default   score for an origin that is not in the table (default 0.9)
    class_confidence    confidence c in the class named by the source (default 0.9)
    class_of_label      class of the frame for each class name of the observations
    """

    existence_score: Mapping[str, float] = field(default_factory=lambda: dict(EXISTENCE_SCORE))
    existence_default: float = 0.9
    class_confidence: float = 0.9
    class_of_label: Mapping[str, str] = field(default_factory=lambda: dict(CLASS_OF_LABEL))

    def __post_init__(self):
        scores = [*self.existence_score.values(), self.existence_default, self.class_confidence]
        if not all(0.0 <= s <= 1.0 for s in scores):                 # also refuses NaN
            raise ValueError("the scores and the confidence must be numbers between 0 and 1")
        unknown = sorted(set(self.class_of_label.values()) - set(CLASS.elements))
        if unknown:
            raise ValueError("class_of_label names classes that are not in the frame: %s" % ", ".join(unknown))


@dataclass
class Evidence:
    """The mass functions of the objects of a table, one row per object."""

    existence: np.ndarray       # shape (n, 4): masses on the sets of the existence frame
    classes: np.ndarray         # shape (n, 16): masses on the sets of the class frame
    exist_score: np.ndarray     # shape (n,): the score s used for each object
    class_element: np.ndarray   # shape (n,): the class of the frame named for each object, or None

    def __len__(self) -> int:
        return len(self.exist_score)


def existence_scores(objects: pd.DataFrame, model: EvidenceModel | None = None) -> np.ndarray:
    """The existence score of each object: the one given by the source if there is one, else the one of the table."""
    model = model or EvidenceModel()
    origin = objects["meas_src"].to_numpy(dtype=object)
    score = np.array([model.existence_score.get(o, model.existence_default) if isinstance(o, str) else model.existence_default
                      for o in origin], dtype=float)
    if "exist_score" in objects.columns:
        given = objects["exist_score"].to_numpy(dtype=float, na_value=np.nan)
        bad = np.isfinite(given) & ((given < 0.0) | (given > 1.0))
        if bad.any():
            raise ValueError("%d objects have an existence score outside 0 to 1" % int(bad.sum()))
        score = np.where(np.isfinite(given), given, score)
    return score


def build_evidence(objects: pd.DataFrame, model: EvidenceModel | None = None) -> Evidence:
    """The existence and class mass functions of each object of a table.

    The table needs the columns ``meas_src`` and ``class`` (the tables of a tick and ``aligned.objects`` have them).
    """
    model = model or EvidenceModel()
    n = len(objects)
    score = existence_scores(objects, model)
    existence = belief.simple_support(EXISTENCE, "E", score) if n else np.zeros((0, EXISTENCE.n_sets))

    labels = objects["class"].to_numpy(dtype=object)
    element = np.array([model.class_of_label.get(label) if isinstance(label, str) else None for label in labels], dtype=object)
    classes = belief.vacuous(CLASS, n)
    for name in CLASS.elements:
        rows = np.flatnonzero(element == name)
        if len(rows):
            classes[rows] = belief.simple_support(CLASS, name, np.full(len(rows), model.class_confidence))
    return Evidence(existence=existence, classes=classes, exist_score=score, class_element=element)


# ----------------------------------------------------------------------------
# Check of a run from the command line
# ----------------------------------------------------------------------------
def check_run(run: Run, model: EvidenceModel | None = None, period: float = 0.1) -> dict:
    """Builds the evidence of every object of every tick of a run and checks that every mass function is valid."""
    model = model or EvidenceModel()
    ticks = n_objects = n_bad = n_without_class = 0
    score_sum = 0.0
    by_origin: dict[str, int] = {}
    by_class: dict[str, int] = {}
    start = time.perf_counter()
    for ego in run.egos:
        for tick in run.replay(ego, period=period):
            objects = align(tick).objects
            evidence = build_evidence(objects, model)
            ticks += 1
            n_objects += len(evidence)
            if not len(evidence):
                continue
            n_bad += int((~belief.is_valid(EXISTENCE, evidence.existence)).sum() + (~belief.is_valid(CLASS, evidence.classes)).sum())
            n_bad += int((evidence.existence[:, EXISTENCE.index("notE")] != 0.0).sum())          # a report never speaks against the object
            score_sum += float(evidence.exist_score.sum())
            n_without_class += int(sum(e is None for e in evidence.class_element))
            for name, count in objects["meas_src"].value_counts(dropna=False).items():
                name = name if isinstance(name, str) else "unknown"
                by_origin[name] = by_origin.get(name, 0) + int(count)
            for name in evidence.class_element:
                if name is not None:
                    by_class[name] = by_class.get(name, 0) + 1
    problems = []
    if n_objects == 0:
        problems.append("the run holds no object")
    if n_bad:
        problems.append("%d mass functions are not valid" % n_bad)
    return dict(ticks=ticks, objects=n_objects, invalid=n_bad, without_class=n_without_class,
                mean_score=round(score_sum / n_objects, 4) if n_objects else None,
                by_origin=dict(sorted(by_origin.items())), by_class=dict(sorted(by_class.items())),
                seconds=round(time.perf_counter() - start, 1), problems=problems, result="PASS" if not problems else "FAIL")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the existence and class evidence of every object of an exported run, and check it.")
    ap.add_argument("run_dir", help="folder with observations.parquet and truth.parquet (written by aoc-export)")
    ap.add_argument("--score", type=float, default=0.9, help="existence score of a reported object (default 0.9)")
    ap.add_argument("--class-confidence", type=float, default=0.9, help="confidence in the class named by the source (default 0.9)")
    ap.add_argument("--drop-unmatched", action="store_true", help="leave out the rows without a true object or without a time")
    args = ap.parse_args(argv)
    try:
        model = EvidenceModel(existence_score={}, existence_default=args.score, class_confidence=args.class_confidence)
        run = load_run(args.run_dir, noise=NoiseModel(), missing="drop" if args.drop_unmatched else "raise")
        c = check_run(run, model)
    except (FileNotFoundError, ValueError) as err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 2

    def counts(table: dict) -> str:
        return ", ".join("%s %d" % item for item in table.items()) or "none"

    print("B4 EVIDENCE CHECK")
    print("  run:                 %s" % run.info.get("run_id", os.path.abspath(args.run_dir)))
    print("  model:               existence score %.2f, class confidence %.2f" % (model.existence_default, model.class_confidence))
    print("  objects:             %d in %d ticks of %d vehicles, in %.1f s" % (c["objects"], c["ticks"], len(run.egos), c["seconds"]))
    print("  by origin:           %s" % counts(c["by_origin"]))
    print("  by class:            %s; without a class of the frame: %d" % (counts(c["by_class"]), c["without_class"]))
    print("  mean existence score: %s" % c["mean_score"])
    print("  mass functions that are not valid: %d" % c["invalid"])
    print("  result:              %s%s" % (c["result"], "" if not c["problems"] else " (" + "; ".join(c["problems"]) + ")"))
    return 0 if not c["problems"] else 3


if __name__ == "__main__":
    sys.exit(main())
