"""How much each object is trusted: the discount (task B5).

The evidence of task B4 says "this object exists" with the score of the source. It does not look at the age of
the object. This module lowers the existence evidence of each object by a reliability alpha between 0 and 1:

    m({E}) = alpha * s        m({E, notE}) = 1 - alpha * s

An object that is fully trusted (alpha = 1) keeps its evidence. An object that is not trusted at all (alpha = 0)
says nothing: "I do not know". This is the classical discounting of Shafer. For this evidence it is also the
contextual discounting of Mercier, Quost and Denoeux, with alpha as the reliability in the context "the object
is not there" (see ``docs/discount.md``).

This is step 4 of the pipeline of the plan. Three laws give alpha. They are the three ways to handle trust that
the plan compares:

  aoc         the proposed law (B3 of the plan). alpha is the reliability curve of the theory note, read at the
              age of the object: the probability that the object is still within the tolerance of the place
              where its report puts it now. For a received object it is multiplied by the confidence that the
              sender declared. For an on-board sensor it is multiplied by the reliability of the sensor (1 today).
  full_trust  no discount (baseline B0): alpha = 1.
  cutoff      today's practice (baseline B1): a received object older than 1 s is dropped (alpha = 0),
              every other object is fully trusted (alpha = 1). An object of exactly 1 s is kept.

The class evidence is not lowered: an old object may have moved, but it has not changed its class
(decision of 2026-10-13).

    from aoc.fusion.discounting import discount_evidence, TrustModel
    trusted = discount_evidence(evidence, aligned.objects)                         # the proposed law
    trusted.existence          # one row per object, columns: empty, {E}, {notE}, {E, notE}
    trusted.reliability        # the alpha used for each object
    baseline = discount_evidence(evidence, aligned.objects, TrustModel(law="cutoff"))

Run this module to check a run:  python -m aoc.fusion.discounting RUN_FOLDER      (command ``aoc-discount``)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Mapping

import numpy as np
import pandas as pd

from ..analysis.calibration import CalibrationBins, brier_score, expected_calibration_error, fit_scalar
from ..analysis.motion import true_motion
from ..theory import reliability as theory
from . import belief
from .alignment import Q_BY_CLASS, Q_DEFAULT, align, process_noise
from .bba import Evidence, EvidenceModel, build_evidence
from .belief import CLASS, EXISTENCE
from .noise import NoiseModel
from .replay import Run, load_run

LAWS = ("aoc", "full_trust", "cutoff")
#: Name of each law in the plan.
LAW_NAMES = {"full_trust": "full trust (B0)", "cutoff": "cutoff (B1)", "aoc": "discount by age (B3)"}


@dataclass(frozen=True)
class TrustModel:
    """How the reliability alpha of an object is found.

    law                   "aoc", "full_trust" or "cutoff"
    epsilon               tolerance on the place of the object in m (1.5 m: half a lane)
    q_by_class            motion value q of the reliability curve, by class of the observations table.
                          Default: the assumed values of the theory note, the ones that the alignment uses.
                          It is a value of its own: it can be fitted without any change of the alignment.
    q_default             value for a class that is not in the table
    use_sender_confidence multiply alpha of a received object by the confidence declared by its sender
                          (column ``sender_conf``; an empty value counts as 1)
    sensor_reliability    reliability of each on-board sensor, by name of the sensor (column ``sensor``)
    sensor_default        reliability of an on-board sensor that is not in the table (1: fully trusted)
    cutoff_age            age limit of the law "cutoff" in s
    """

    law: str = "aoc"
    epsilon: float = theory.EPS
    q_by_class: Mapping[str, float] = field(default_factory=lambda: dict(Q_BY_CLASS))
    q_default: float = Q_DEFAULT
    use_sender_confidence: bool = True
    sensor_reliability: Mapping[str, float] = field(default_factory=dict)
    sensor_default: float = 1.0
    cutoff_age: float = 1.0

    def __post_init__(self):
        if self.law not in LAWS:
            raise ValueError("the law must be one of: %s" % ", ".join(LAWS))
        try:
            epsilon, cutoff = float(self.epsilon), float(self.cutoff_age)
            motion = [float(q) for q in [*self.q_by_class.values(), self.q_default]]
            sensors = [float(r) for r in [*self.sensor_reliability.values(), self.sensor_default]]
        except (TypeError, ValueError):
            raise ValueError("the tolerance, the age limit, the motion values and the reliabilities must be numbers") from None
        if not (np.isfinite(epsilon) and epsilon > 0.0) or not cutoff >= 0.0:                 # also refuses NaN
            raise ValueError("the tolerance must be a positive number and the age limit must not be negative")
        if not all(np.isfinite(q) and q >= 0.0 for q in motion):
            raise ValueError("a motion value q must be a number that is not negative")
        if not all(0.0 <= r <= 1.0 for r in sensors):
            raise ValueError("the reliability of a sensor must be a number between 0 and 1")


@dataclass
class Discounted:
    """The evidence of the objects of a table after the discount, one row per object."""

    existence: np.ndarray       # shape (n, 4): masses on the sets of the existence frame, after the discount
    classes: np.ndarray         # shape (n, 16): masses on the sets of the class frame, as they were
    reliability: np.ndarray     # shape (n,): the alpha used for each object

    def __len__(self) -> int:
        return len(self.reliability)


# ----------------------------------------------------------------------------
# The reliability curve, read for each object
# ----------------------------------------------------------------------------
def _numbers(objects: pd.DataFrame, names: list[str]) -> np.ndarray:
    """The named columns as numbers, one column per name. A column that is not there is an error with a clear text."""
    missing = [name for name in names if name not in objects.columns]
    if missing:
        raise ValueError("the table of objects has no column %s" % ", ".join(missing))
    if not len(objects):
        return np.zeros((0, len(names)))
    return np.column_stack([objects[name].to_numpy(dtype=float, na_value=np.nan) for name in names])


def _ages(objects: pd.DataFrame) -> np.ndarray:
    age = _numbers(objects, ["age_now"])[:, 0]
    if not np.isfinite(age).all() or (age < -1e-6).any():
        raise ValueError("an age is missing or negative")
    return np.clip(age, 0.0, None)


def position_spread(objects: pd.DataFrame) -> np.ndarray:
    """The variance of the place where each report puts its object now, without the unknown acceleration, in m^2.

    The table needs P11, P12, P22, Pv11, Pv22 and ``age_now``. With the unknown acceleration of intensity q:

        variance of the place now = this value + q * age^3 / 3

    When the uncertainty is not the same in every direction, the value is the largest variance over all directions
    (the largest eigenvalue of the covariance of the place). The reliability is then a safe lower bound
    (task A2, bounds with the eigenvalues). When it is the same in every direction, it is the exact value.
    """
    age = _ages(objects)
    values = _numbers(objects, ["P11", "P12", "P22", "Pv11", "Pv22"])
    if not np.isfinite(values).all():
        raise ValueError("a covariance is missing. Load the run with a noise model: it fills the covariance columns.")
    p11, p12, p22, pv11, pv22 = values.T
    if (values[:, [0, 2, 3, 4]] < 0.0).any() or (p12**2 > p11 * p22 + 1e-12).any():
        raise ValueError("a variance is negative, or P12 is too large for P11 and P22")
    along, across = p11 + age**2 * pv11, p22 + age**2 * pv22
    return (along + across) / 2.0 + np.sqrt(((along - across) / 2.0) ** 2 + p12**2)


def alpha_of_age(spread, age, q, epsilon: float = theory.EPS) -> np.ndarray:
    """The reliability curve of the theory note: probability that the true place is within ``epsilon`` of the predicted place.

    ``spread`` is the value of ``position_spread`` in m^2, ``age`` the age in s, ``q`` the motion value.
    """
    spread, age, q = (np.asarray(v, dtype=float) for v in (spread, age, q))
    for values, what in ((spread, "spread"), (age, "age"), (q, "motion value")):
        if not np.isfinite(values).all() or (values < -1e-6).any():
            raise ValueError("the %s must be a number that is not negative" % what)
    variance = np.clip(spread, 0.0, None) + q * np.clip(age, 0.0, None) ** 3 / 3.0
    with np.errstate(divide="ignore"):
        return -np.expm1(-float(epsilon) ** 2 / (2.0 * variance))            # a variance of 0 gives a reliability of 1


def age_reliability(objects: pd.DataFrame, model: TrustModel | None = None) -> np.ndarray:
    """The reliability alpha(age) of each object of a table, from its age, its covariance and its class."""
    model = model or TrustModel()
    if "class" not in objects.columns:
        raise ValueError("the table of objects has no column class")
    q = process_noise(objects["class"], model.q_by_class, model.q_default)
    return alpha_of_age(position_spread(objects), _ages(objects), q, model.epsilon)


def is_received(objects: pd.DataFrame) -> np.ndarray:
    """True for each object that came in a message (source "v2x"), False for the objects of the on-board sensors."""
    if "src" not in objects.columns:
        raise ValueError("the table of objects has no column src")
    return (objects["src"] == "v2x").to_numpy(dtype=bool, na_value=False)


def is_old(objects: pd.DataFrame, limit: float) -> np.ndarray:
    """True for each received object that is older than ``limit`` seconds. An object of exactly this age is not old."""
    return is_received(objects) & (_ages(objects) > limit + 1e-9)


def declared_reliability(objects: pd.DataFrame, model: TrustModel | None = None) -> np.ndarray:
    """The part of the reliability that does not come from the age. 1 when nothing is declared.

    For a received object: the confidence declared by its sender. For an object of an on-board sensor:
    the reliability of the sensor.
    """
    model = model or TrustModel()
    received = is_received(objects)
    out = np.ones(len(objects))
    if model.use_sender_confidence and "sender_conf" in objects.columns:
        declared = objects["sender_conf"].to_numpy(dtype=float, na_value=np.nan)
        bad = received & ~np.isnan(declared) & ~((declared >= 0.0) & (declared <= 1.0))
        if bad.any():
            raise ValueError("%d received objects have a sender confidence outside 0 to 1" % int(bad.sum()))
        out = out * np.where(received & ~np.isnan(declared), declared, 1.0)
    if (model.sensor_reliability or model.sensor_default != 1.0) and (~received).any():
        if "sensor" not in objects.columns:
            raise ValueError("the reliability of the sensors needs the column 'sensor'")
        names = objects["sensor"].to_numpy(dtype=object)
        of_sensor = np.array([model.sensor_reliability.get(name, model.sensor_default) if isinstance(name, str) else model.sensor_default
                              for name in names], dtype=float)
        out = out * np.where(received, 1.0, of_sensor)
    return out


def reliability(objects: pd.DataFrame, model: TrustModel | None = None) -> np.ndarray:
    """The reliability alpha of each object of a table, with the law of the model.

    The table needs the columns of ``aligned.objects`` (or of ``tick.local`` and ``tick.v2x``).
    """
    model = model or TrustModel()
    if model.law == "full_trust":
        return np.ones(len(objects))
    if model.law == "cutoff":
        return np.where(is_old(objects, model.cutoff_age), 0.0, 1.0)
    return age_reliability(objects, model) * declared_reliability(objects, model)


def discount_evidence(evidence: Evidence, objects: pd.DataFrame, model: TrustModel | None = None) -> Discounted:
    """Lowers the existence evidence of each object by its reliability. The class evidence is kept as it is.

    ``evidence`` comes from ``build_evidence(objects)``: same objects, same order.
    """
    if len(evidence) != len(objects):
        raise ValueError("the evidence holds %d objects, the table %d" % (len(evidence), len(objects)))
    alpha = reliability(objects, model)
    return Discounted(existence=belief.discount(EXISTENCE, evidence.existence, alpha), classes=evidence.classes.copy(), reliability=alpha)


# ----------------------------------------------------------------------------
# Check of a run from the command line
# ----------------------------------------------------------------------------
def really_there(objects: pd.DataFrame, truth: pd.DataFrame, epsilon: float) -> np.ndarray:
    """True for each object whose true object is, at the time of the tick, within ``epsilon`` of the place where its report puts it.

    ``objects`` is ``aligned.objects``, ``truth`` is ``tick.truth``. An object whose true object is not in the truth
    of the tick (it left the simulation, or the report has no true object) is not there.
    This reads the ground truth: it is for the checks only.
    """
    place = {name: (x, y) for name, x, y in zip(truth["obj_id"].to_numpy(dtype=object), truth["x"].to_numpy(dtype=float),
                                                truth["y"].to_numpy(dtype=float))}
    names = objects["gt_match_id"].to_numpy(dtype=object)
    true_xy = np.array([place.get(name, (np.nan, np.nan)) for name in names], dtype=float).reshape(len(names), 2)
    reported = _numbers(objects, ["x_t", "y_t"])
    with np.errstate(invalid="ignore"):
        return np.hypot(reported[:, 0] - true_xy[:, 0], reported[:, 1] - true_xy[:, 1]) <= epsilon       # no true place gives NaN, so False


#: What the check looks for after each discount, and the text of the problem.
_PROBLEMS = dict(invalid="%d mass functions are not valid", raised="%d beliefs were raised by the discount",
                 against="%d objects got evidence against them", classes_changed="the class evidence of %d objects changed",
                 changed="the evidence of %d objects changed", cutoff_wrong="%d objects are not handled as the cutoff says")


def check_run(run: Run, model: TrustModel | None = None, evidence_model: EvidenceModel | None = None,
              period: float = 0.1, hold: float | None = None, fit: bool = False) -> dict:
    """Discounts the evidence of every object of every tick of a run with the three laws, checks the result and measures it.

    The measures are the calibration error and the Brier score of the belief in "the object exists", for each of the
    three laws. Every object counts, one at a time (before any combination). A dropped object counts with belief 0.

    hold   a received object is forgotten when it is older than this value in s. None: it is kept until the end
           of the run, so the same old report is counted again at every tick.
    fit    also search, for each class, the motion value that fits the run best: the smallest Brier score over
           the received objects of the class. The measures with these values are given.
    """
    model = model or TrustModel()
    if model.law != "aoc":
        raise ValueError("the check compares the three laws: give a model with the law 'aoc'")
    models = {"full_trust": replace(model, law="full_trust"), "cutoff": replace(model, law="cutoff"), "aoc": model}
    bins = {law: CalibrationBins() for law in LAWS}
    found = {(law, kind): 0 for law in LAWS for kind in _PROBLEMS}
    ticks = n_objects = 0
    kept: dict[str, list] = dict(spread=[], age=[], factor=[], hit=[], label=[], received=[], alpha=[], gone=[])
    start = time.perf_counter()
    for ego in run.egos:
        for tick in run.replay(ego, period=period, hold=hold):
            objects = align(tick).objects
            ticks += 1
            if not len(objects):
                continue
            n_objects += len(objects)
            evidence = build_evidence(objects, evidence_model)
            before = belief.belief(EXISTENCE, evidence.existence, "E")
            hit = really_there(objects, tick.truth, model.epsilon)
            old = is_old(objects, model.cutoff_age)
            for law in LAWS:
                trusted = discount_evidence(evidence, objects, models[law])
                after = belief.belief(EXISTENCE, trusted.existence, "E")
                found[law, "invalid"] += int((~belief.is_valid(EXISTENCE, trusted.existence)).sum() + (~belief.is_valid(CLASS, trusted.classes)).sum())
                found[law, "raised"] += int((after > before + 1e-12).sum())
                found[law, "against"] += int((trusted.existence[:, EXISTENCE.index("notE")] != 0.0).sum())
                found[law, "classes_changed"] += int((trusted.classes != evidence.classes).any(axis=1).sum())
                if law == "full_trust":
                    found[law, "changed"] += int((trusted.existence != evidence.existence).any(axis=1).sum())
                if law == "cutoff":
                    found[law, "cutoff_wrong"] += int((after[old] != 0.0).sum() + (after[~old] != before[~old]).sum())
                if law == "aoc":
                    kept["spread"].append(position_spread(objects))
                    kept["age"].append(objects["age_now"].to_numpy(dtype=float))
                    kept["factor"].append(before * declared_reliability(objects, model))          # all of the belief but the reliability curve
                    kept["hit"].append(hit)
                    kept["label"].append(objects["class"].to_numpy(dtype=object))
                    kept["received"].append(is_received(objects))
                    kept["alpha"].append(trusted.reliability)
                    kept["gone"].append(~objects["gt_match_id"].isin(tick.truth["obj_id"]).to_numpy(dtype=bool))
                bins[law].add(after, hit)
    seconds = time.perf_counter() - start

    problems = ["the run holds no object"] if n_objects == 0 else []
    problems += ["%s: %s" % (LAW_NAMES[law], _PROBLEMS[kind] % count) for (law, kind), count in found.items() if count]
    measure = lambda value: round(value, 4) if n_objects else None
    out = dict(ticks=ticks, objects=n_objects, hold=hold, seconds=round(seconds, 1), problems=problems, result="PASS" if not problems else "FAIL",
               calibration_error={law: measure(bins[law].error()) for law in LAWS}, brier={law: measure(bins[law].brier()) for law in LAWS},
               calibration_table={law: bins[law].table() for law in LAWS})
    if not n_objects:
        return out
    spread, age, factor, hit, label, received, alpha, gone = (np.concatenate(kept[name]) for name in kept)
    young, old = received & (age <= model.cutoff_age + 1e-9), received & (age > model.cutoff_age + 1e-9)
    share = lambda mask, values: round(float(values[mask].mean()), 4) if mask.any() else None
    out.update(received=int(received.sum()), received_old=int(old.sum()), gone=int(gone.sum()),
               there_young=share(young, hit), there_old=share(old, hit), alpha_young=share(young, alpha), alpha_old=share(old, alpha))
    if fit:
        q_now = process_noise(pd.Series(label, dtype=object), model.q_by_class, model.q_default)
        confidence = factor * alpha_of_age(spread, age, q_now, model.epsilon)
        low, high = 1e-3, 100.0
        fitted, rows_used, at_limit = {}, {}, []
        for name in sorted({c for c in label[received] if isinstance(c, str)}):
            rows = received & (label == name)
            gap = lambda q: float(np.mean((factor[rows] * alpha_of_age(spread[rows], age[rows], q, model.epsilon) - hit[rows]) ** 2))
            value = fit_scalar(gap, low, high)
            fitted[name], rows_used[name] = round(value, 3), (int(rows.sum()), round(float(old[rows].mean()), 4))
            if value < low * 1.05 or value > high / 1.05:
                at_limit.append(name)
            confidence = np.where(label == name, factor * alpha_of_age(spread, age, fitted[name], model.epsilon), confidence)
        out.update(fitted_q=fitted, fitted_rows=rows_used, fitted_at_limit=at_limit,
                   fitted_error=round(expected_calibration_error(confidence, hit), 4), fitted_brier=round(brier_score(confidence, hit), 4))
    return out


def _q_options(pairs: list[str]) -> dict:
    values = dict(Q_BY_CLASS)
    for pair in pairs:
        name, _, value = pair.partition("=")
        name = name.strip()
        try:
            number = float(value)
        except ValueError:
            raise ValueError("--q needs CLASS=VALUE, for example --q passenger_car=1.0 (got %r)" % pair) from None
        if name not in Q_BY_CLASS:
            raise ValueError("--q: %r is not a class of the observations. The classes are: %s" % (name, ", ".join(sorted(Q_BY_CLASS))))
        values[name] = number
    return values


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Discount the existence evidence of every object of an exported run by its age, check it, "
                                             "and compare how right the belief is with full trust, a hard cutoff and the discount.")
    ap.add_argument("run_dir", help="folder with observations.parquet and truth.parquet (written by aoc-export)")
    ap.add_argument("--epsilon", type=float, default=theory.EPS, help="tolerance on the place of an object in m (default %.1f)" % theory.EPS)
    ap.add_argument("--q", action="append", default=[], metavar="CLASS=VALUE",
                    help="motion value of the reliability curve for one class, for example --q passenger_car=1.0 (can be given several times)")
    ap.add_argument("--cutoff", type=float, default=1.0, help="age limit of the hard cutoff in s (default 1.0)")
    ap.add_argument("--hold", type=float, default=None,
                    help="forget a received object when it is older than this value in s (default: keep it until the end of the run)")
    ap.add_argument("--no-sender-confidence", action="store_true", help="do not multiply by the confidence declared by the sender")
    ap.add_argument("--fit", action="store_true", help="also search the motion value that fits this run best (smallest Brier score)")
    ap.add_argument("--motion", action="store_true", help="also measure how the objects really move, on the ground truth of the run")
    ap.add_argument("--drop-unmatched", action="store_true", help="leave out the rows without a true object or without a time")
    args = ap.parse_args(argv)
    try:
        model = TrustModel(epsilon=args.epsilon, q_by_class=_q_options(args.q), cutoff_age=args.cutoff,
                           use_sender_confidence=not args.no_sender_confidence)
        if args.hold is not None and not args.hold > 0.0:
            raise ValueError("--hold must be a positive number of seconds")
        run = load_run(args.run_dir, noise=NoiseModel(), missing="drop" if args.drop_unmatched else "raise")
        motion = true_motion(run.truth, epsilon=model.epsilon) if args.motion else None          # first: it is fast and can refuse the run
        c = check_run(run, model, hold=args.hold, fit=args.fit)
    except (FileNotFoundError, ValueError) as err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 2

    percent = lambda value: "none" if value is None else "%.1f%%" % (100.0 * value)
    number = lambda value: "none" if value is None else "%.3f" % value
    changed = {name: value for name, value in model.q_by_class.items() if Q_BY_CLASS.get(name) != value}
    limit = "%g s" % model.cutoff_age
    print("B5 DISCOUNT CHECK")
    print("  run:                 %s" % run.info.get("run_id", os.path.abspath(args.run_dir)))
    print("  model:               tolerance %.1f m, motion values %s, sender confidence %s"
          % (model.epsilon, "of the theory note" if not changed else "changed: " + ", ".join("%s %g" % item for item in sorted(changed.items())),
             "used" if model.use_sender_confidence else "not used"))
    print("  memory:              a received object is %s"
          % ("kept until the end of the run, however old" if args.hold is None else "forgotten when it is older than %g s" % args.hold))
    print("  objects:             %d in %d ticks of %d vehicles, in %.1f s" % (c["objects"], c["ticks"], len(run.egos), c["seconds"]))
    if c["objects"]:
        share_old = c["received_old"] / c["received"] if c["received"] else None
        print("  received objects:    %d, of which %s are older than %s" % (c["received"], percent(share_old), limit))
        print("  really there:        %s of the received objects up to %s, %s of the older ones (true object within %.1f m of the predicted place)"
              % (percent(c["there_young"]), limit, percent(c["there_old"]), model.epsilon))
        if c["gone"]:
            print("                       %d objects have no true object at the time of the tick: they count as not there" % c["gone"])
        print("  reliability alpha:   mean %s up to %s, %s for the older ones" % (number(c["alpha_young"]), limit, number(c["alpha_old"])))
        print("  how right is the belief that the object exists (every object, one source at a time; 0 is perfect):")
        print("      %-24s %17s   %11s" % ("", "calibration error", "Brier score"))
        for law in ("full_trust", "cutoff", "aoc"):
            print("      %-24s %17s   %11s" % (LAW_NAMES[law], number(c["calibration_error"][law]), number(c["brier"][law])))
        if args.fit:
            print("  best fit of the motion value on this run (smallest Brier score over the received objects of the class):")
            for name, value in c["fitted_q"].items():
                rows, share_old = c["fitted_rows"][name]
                note = ". This is a limit of the search: the run cannot give the value" if name in c["fitted_at_limit"] else ""
                print("      %s %g, from %d received objects (%s older than %s)%s" % (name, value, rows, percent(share_old), limit, note))
            if c["fitted_q"]:
                print("      %-24s %17s   %11s" % ("with it, " + LAW_NAMES["aoc"].split(" (")[0], number(c["fitted_error"]), number(c["fitted_brier"])))
    if motion is not None:
        print("  true motion (a perfect constant-velocity prediction against the ground truth, no sensor noise):")
        for line in motion.itertuples():
            if line.n:
                print("      %.1f s ahead: error per axis %.2f m (motion value %.1f), median error %.2f m, within %.1f m: %s (a Gaussian error of this size: %s)"
                      % (line.horizon, line.spread, line.q_effective, line.median_error, model.epsilon, percent(line.within), percent(line.within_if_gaussian)))
    print("  result:              %s%s" % (c["result"], "" if not c["problems"] else " (" + "; ".join(c["problems"]) + ")"))
    return 0 if not c["problems"] else 3


if __name__ == "__main__":
    sys.exit(main())
