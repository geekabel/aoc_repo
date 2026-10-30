"""The fusion of the evidence about one real object (task B6).

After the association (B3), the objects of a tick are in groups: the objects of one group are one real object.
After the evidence (B4) and the discount (B5), each object carries two mass functions: existence and class.
This module combines the mass functions of each group into one, with the rules of step 5 of the plan:

  on-board sensors   the radar, the camera and so on see the object on their own: their pieces of evidence are
                     distinct. They are combined with the conjunctive rule (Dempster's rule without the normalisation).
  received objects   several neighbours may report the same object, and they may have got it from the same place
                     (one of them saw it, the others repeat it). Their pieces of evidence may share their origin.
                     They are combined with the cautious rule of Denoeux (2008): a piece of evidence that reaches the
                     vehicle twice counts once, so shared origins cannot inflate the confidence.
  the two together   the on-board result and the received result are distinct: the conjunctive rule.

The conflict (the mass of the empty set) is kept in the fused mass functions, as the plan asks for it as a
diagnostic (the open-world reading of the Transferable Belief Model). ``belief.normalize`` removes it. With the
existence evidence of task B4 there is never any conflict on the existence frame: every source supports "the
object exists" or says nothing. Conflict appears on the class frame when two sources name different classes.

Outputs per fused object: the two mass functions, the belief and the plausibility that the object exists,
the pignistic probability of each class, the conflict, and how many sources of each kind took part.
The place and the speed of the fused object come from the kinematic fusion of task B7, not from here.

The plan compares this rule (B3 of the plan) with "Dempster only" (B4 of the plan, the ablation): the received
objects are then combined with the conjunctive rule too. ``CombinationModel(cpm_rule="conjunctive")`` gives it.

    from aoc.fusion.combination import fuse
    fused = fuse(aligned.objects, groups, trusted)          # groups from associate(aligned), trusted from discount_evidence
    fused.objects                                            # one row per real object
    fused.existence, fused.classes                           # its mass functions

Run this module to check a run:  python -m aoc.fusion.combination RUN_FOLDER      (command ``aoc-combine``)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from ..analysis.calibration import CalibrationBins
from . import belief
from .alignment import align
from .association import AssociationConfig, associate
from .bba import EvidenceModel, build_evidence
from .belief import CLASS, EXISTENCE
from .discounting import LAW_NAMES, Discounted, TrustModel, discount_evidence, is_received
from .noise import NoiseModel
from .replay import Run, load_run

RULES = {"conjunctive": belief.conjunctive, "cautious": belief.cautious, "dempster": belief.dempster}


@dataclass(frozen=True)
class CombinationModel:
    """Which rule combines which pieces of evidence.

    local_rule     rule for the objects of the on-board sensors of one group (default "conjunctive")
    cpm_rule       rule for the received objects of one group (default "cautious"; "conjunctive" is the ablation of the plan)
    across_rule    rule between the on-board result and the received result (default "conjunctive")
    min_ignorance  the cautious rule needs some mass on the whole frame. A mass function with less than this
                   on the whole frame gets this much, taken from its other masses in proportion (default 1e-9)
    """

    local_rule: str = "conjunctive"
    cpm_rule: str = "cautious"
    across_rule: str = "conjunctive"
    min_ignorance: float = 1e-9

    def __post_init__(self):
        for name in (self.local_rule, self.cpm_rule, self.across_rule):
            if name not in RULES:
                raise ValueError("a rule must be one of: %s" % ", ".join(RULES))
        if not 0.0 <= self.min_ignorance < 1.0:
            raise ValueError("min_ignorance must be a number between 0 and 1")


#: The names of the plan for the ways to combine.
COMBINATION_NAMES = {"cautious": "cautious rule for the received objects (B3)", "conjunctive": "Dempster only (B4, ablation)"}


@dataclass
class Fused:
    """The real objects of one tick, one row each, with their fused evidence."""

    t: float
    ego: str
    objects: pd.DataFrame        # one row per real object: group, n_sources, n_local, n_received, n_senders, newest member, class named
    existence: np.ndarray        # shape (g, 4): the fused existence mass function of each real object
    classes: np.ndarray          # shape (g, 16): the fused class mass function
    members: np.ndarray          # shape (n,): the row of ``objects`` of each object of the tick

    def __len__(self) -> int:
        return len(self.objects)


def _not_dogmatic(frame, table: np.ndarray, floor: float) -> np.ndarray:
    """Gives every row at least ``floor`` of mass on the whole frame, taken from the other masses in proportion."""
    if floor <= 0.0:
        return table
    short = table[:, frame.omega] < floor
    if not short.any():
        return table
    out = table.copy()
    rest = 1.0 - out[short, frame.omega]
    scale = np.where(rest > 0.0, (1.0 - floor) / np.where(rest > 0.0, rest, 1.0), 1.0)
    out[short] *= scale[:, None]
    out[short, frame.omega] = floor
    return out


def _combine_by_group(frame, table: np.ndarray, group_of_row: np.ndarray, n_groups: int, rule: str, floor: float) -> tuple[np.ndarray, np.ndarray]:
    """One mass function per group from the rows of a table, with a rule. Returns (table of groups, group has at least one row).

    The rows of all groups are combined at the same time: the first row of every group, then the second row of
    every group with the result, and so on. A group without a row gets "I do not know".
    """
    out = belief.vacuous(frame, n_groups)
    has = np.zeros(n_groups, dtype=bool)
    if not len(group_of_row):
        return out, has
    if rule == "cautious":
        table = _not_dogmatic(frame, table, floor)
    order = np.argsort(group_of_row, kind="stable")
    sorted_groups = group_of_row[order]
    first = np.r_[True, sorted_groups[1:] != sorted_groups[:-1]]
    position = np.arange(len(order)) - np.maximum.accumulate(np.where(first, np.arange(len(order)), 0))
    combine = RULES[rule]
    for step in range(int(position.max()) + 1):
        rows = order[position == step]
        groups = group_of_row[rows]
        if step == 0:
            out[groups] = table[rows]
            has[groups] = True
        else:
            out[groups] = combine(frame, out[groups], table[rows])
    return out, has


def fuse(objects: pd.DataFrame, groups, trusted: Discounted, model: CombinationModel | None = None, t: float = np.nan, ego: str = "") -> Fused:
    """Combines the evidence of the objects of each group into one mass function per frame.

    objects   ``aligned.objects`` (one row per object of the tick)
    groups    the group number of each object, from ``associate`` (objects with the same number are one real object)
    trusted   the discounted evidence of the same objects, in the same order (``discount_evidence``)
    """
    model = model or CombinationModel()
    groups = np.asarray(groups)
    n = len(objects)
    if groups.shape != (n,) or len(trusted) != n:
        raise ValueError("one group number and one piece of evidence are needed per object (%d objects)" % n)
    if n and (not np.issubdtype(groups.dtype, np.integer) or (groups < 0).any()):
        raise ValueError("the group numbers must be whole numbers that are not negative")
    received = is_received(objects)
    numbers, members = np.unique(groups, return_inverse=True)
    members = np.asarray(members).ravel()
    g = len(numbers)
    stored = {}
    for frame, table in ((EXISTENCE, trusted.existence), (CLASS, trusted.classes)):
        own, has_own = _combine_by_group(frame, table[~received], members[~received], g, model.local_rule, model.min_ignorance)
        theirs, has_theirs = _combine_by_group(frame, table[received], members[received], g, model.cpm_rule, model.min_ignorance)
        both = has_own & has_theirs
        fused = np.where(has_own[:, None], own, theirs)
        if both.any():
            left, right = own[both], theirs[both]
            if model.across_rule == "cautious":
                left, right = _not_dogmatic(frame, left, model.min_ignorance), _not_dogmatic(frame, right, model.min_ignorance)
            fused[both] = RULES[model.across_rule](frame, left, right)
        stored[frame.name] = fused
    existence, classes = stored["existence"], stored["class"]

    n_local = np.bincount(members[~received], minlength=g)
    n_received = np.bincount(members[received], minlength=g)
    t_meas = objects["t_meas"].to_numpy(dtype=float, na_value=np.nan) if "t_meas" in objects.columns else np.full(n, np.nan)
    value = np.where(np.isfinite(t_meas), t_meas, -np.inf)
    order = np.lexsort((np.arange(n), -value, members))                   # by group, then the newest first, then the first row
    newest = order[np.r_[True, members[order][1:] != members[order][:-1]]] if n else np.zeros(0, dtype=np.int64)
    senders = objects["tx_station"].to_numpy(dtype=object) if "tx_station" in objects.columns else np.full(n, None, dtype=object)
    labels = objects["class"].to_numpy(dtype=object) if "class" in objects.columns else np.full(n, None, dtype=object)
    n_senders = np.zeros(g, dtype=np.int64)
    named: list = [None] * g
    for k in range(g):
        rows = members == k
        n_senders[k] = len({sender for sender in senders[rows & received] if isinstance(sender, str)})
        found = [label for label in labels[rows] if isinstance(label, str)]
        if found:
            named[k] = max(sorted(set(found)), key=found.count)           # the class named most often (the first in alphabetical order on a tie)
    pignistic = belief.pignistic(CLASS, classes)
    columns = {"group": numbers.astype(np.int64), "n_sources": n_local + n_received, "n_local": n_local, "n_received": n_received,
               "n_senders": n_senders, "newest": newest, "class": pd.Series(named, dtype=object),
               "bel_exists": belief.belief(EXISTENCE, existence, "E"), "pl_exists": belief.plausibility(EXISTENCE, existence, "E"),
               "conflict_class": belief.conflict(CLASS, classes)}
    columns.update({"p_" + name: pignistic[:, position] for position, name in enumerate(CLASS.elements)})
    table = pd.DataFrame(columns)
    return Fused(t=t, ego=ego, objects=table, existence=existence, classes=classes, members=members)


# ----------------------------------------------------------------------------
# Check of a run from the command line
# ----------------------------------------------------------------------------
def group_truth(fused: Fused, objects: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """For each fused object: the name of its true object, and whether its members all name the same one.

    A fused object whose members name different true objects (or no true object) is a wrong association.
    """
    names = objects["gt_match_id"].to_numpy(dtype=object)
    agree = np.ones(len(fused), dtype=bool)
    chosen = np.full(len(fused), None, dtype=object)
    for row, k in enumerate(fused.members):
        name = names[row] if isinstance(names[row], str) else None
        if chosen[k] is None and agree[k] and name is not None and not (fused.members[:row] == k).any():
            chosen[k] = name
        elif name is None or chosen[k] != name:
            agree[k] = False
    return chosen, agree


def really_there_fused(fused: Fused, objects: pd.DataFrame, truth: pd.DataFrame, epsilon: float) -> np.ndarray:
    """True for each fused object whose true object is within ``epsilon`` of the place of its newest member.

    Before the kinematic fusion (task B7), the place of a fused object is the one of its newest member.
    An object whose members name different true objects is a wrong association: it counts as not there.
    This reads the ground truth: it is for the checks only.
    """
    chosen, agree = group_truth(fused, objects)
    place = dict(zip(truth["obj_id"].to_numpy(dtype=object), zip(truth["x"].to_numpy(dtype=float), truth["y"].to_numpy(dtype=float))))
    true_xy = np.array([place.get(name, (np.nan, np.nan)) if name is not None else (np.nan, np.nan) for name in chosen], dtype=float).reshape(len(fused), 2)
    newest = fused.objects["newest"].to_numpy()
    x_t, y_t = objects["x_t"].to_numpy(dtype=float)[newest], objects["y_t"].to_numpy(dtype=float)[newest]
    with np.errstate(invalid="ignore"):
        return agree & (np.hypot(x_t - true_xy[:, 0], y_t - true_xy[:, 1]) <= epsilon)


def check_run(run: Run, model: CombinationModel | None = None, trust: TrustModel | None = None, evidence_model: EvidenceModel | None = None,
              association: AssociationConfig | None = None, period: float = 0.1, hold: float | None = None) -> dict:
    """Runs the whole evidential chain on a run (align, associate, evidence, discount, fuse), checks the fused
    mass functions and measures the fused belief against the truth.

    Four variants of the plan are measured: B0 full trust and B1 cutoff (Dempster only), B3 the discount with the
    cautious rule for the received objects, B4 the discount with Dempster only (the ablation).
    """
    model = model or CombinationModel()
    trust = (trust or TrustModel()).for_run(run)
    if trust.law != "aoc":
        raise ValueError("give a trust model with the law 'aoc': the check builds the baselines itself")
    variants = {"B0": (replace(trust, law="full_trust"), replace(model, cpm_rule="conjunctive")),
                "B1": (replace(trust, law="cutoff"), replace(model, cpm_rule="conjunctive")),
                "B3": (trust, model), "B4": (trust, replace(model, cpm_rule="conjunctive"))}
    bins = {name: CalibrationBins() for name in variants}
    found = {name: dict(invalid=0, bel_above_pl=0, above_members=0, conflict_exists=0) for name in variants}
    counts = dict(ticks=0, objects=0, fused=0, with_local=0, with_received=0, with_both=0, several_senders=0, wrong_groups=0, gone=0)
    gain = []                                                            # bel(E) with Dempster only minus bel(E) with the cautious rule
    conflict_class = []
    start = time.perf_counter()
    for ego in run.egos:
        for tick in run.replay(ego, period=period, hold=hold):
            aligned = align(tick)
            counts["ticks"] += 1
            if not len(aligned):
                continue
            counts["objects"] += len(aligned)
            groups = associate(aligned, association)
            evidence = build_evidence(aligned.objects, evidence_model)
            results = {}
            hit = None                                                   # the truth of a fused object is the same for the four variants
            for name, (trust_model, combination) in variants.items():
                trusted = discount_evidence(evidence, aligned.objects, trust_model)
                fused = fuse(aligned.objects, groups, trusted, combination, t=tick.t, ego=ego)
                results[name] = (fused, trusted)
                bad = found[name]
                bad["invalid"] += int((~belief.is_valid(EXISTENCE, fused.existence)).sum() + (~belief.is_valid(CLASS, fused.classes)).sum())
                bel, pl = fused.objects["bel_exists"].to_numpy(), fused.objects["pl_exists"].to_numpy()
                bad["bel_above_pl"] += int((bel > pl + 1e-12).sum())
                bad["conflict_exists"] += int((fused.existence[:, 0] > 1e-12).sum())
                member_bel = belief.belief(EXISTENCE, trusted.existence, "E")
                best = np.zeros(len(fused))
                np.maximum.at(best, fused.members, member_bel)
                if name == "B3":                                         # cautious: the received part never beats its best member
                    only_received = (fused.objects["n_local"] == 0).to_numpy()
                    bad["above_members"] += int((bel[only_received] > best[only_received] + 1e-9).sum())
                if hit is None:
                    hit = really_there_fused(fused, aligned.objects, tick.truth, trust.epsilon)
                bins[name].add(bel, hit)
            fused = results["B3"][0]
            counts["fused"] += len(fused)
            counts["with_local"] += int((fused.objects["n_local"] > 0).sum())
            counts["with_received"] += int((fused.objects["n_received"] > 0).sum())
            counts["with_both"] += int(((fused.objects["n_local"] > 0) & (fused.objects["n_received"] > 0)).sum())
            several = (fused.objects["n_senders"] > 1).to_numpy()
            counts["several_senders"] += int(several.sum())
            if several.any():
                gain.append(results["B4"][0].objects["bel_exists"].to_numpy()[several] - fused.objects["bel_exists"].to_numpy()[several])
            conflict_class.append(fused.objects["conflict_class"].to_numpy())
            counts["wrong_groups"] += int((~group_truth(fused, aligned.objects)[1]).sum())
            counts["gone"] += int((~aligned.objects["gt_match_id"].isin(tick.truth["obj_id"])).sum())
    seconds = time.perf_counter() - start
    texts = dict(invalid="%d fused mass functions are not valid", bel_above_pl="%d beliefs above the plausibility",
                 above_members="the cautious rule gave %d received-only objects more belief than their best source",
                 conflict_exists="%d fused objects have conflict on the existence frame")
    problems = ["the run holds no object"] if counts["objects"] == 0 else []
    problems += ["%s: %s" % (name, texts[kind] % count) for name, bad in found.items() for kind, count in bad.items() if count]
    gain = np.concatenate(gain) if gain else np.zeros(0)
    conflict_class = np.concatenate(conflict_class) if conflict_class else np.zeros(0)
    measure = lambda value: round(value, 4) if counts["objects"] else None
    return dict(**counts, hold=hold, seconds=round(seconds, 1), problems=problems, result="PASS" if not problems else "FAIL",
                calibration_error={name: measure(bins[name].error()) for name in variants},
                brier={name: measure(bins[name].brier()) for name in variants},
                gain_mean=round(float(gain.mean()), 4) if len(gain) else None, gain_max=round(float(gain.max()), 4) if len(gain) else None,
                conflict_class_max=round(float(conflict_class.max()), 4) if len(conflict_class) else None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the evidential chain on an exported run (align, associate, evidence, discount, fuse), "
                                             "check the fused evidence and compare the four ways of the plan.")
    ap.add_argument("run_dir", help="folder with observations.parquet and truth.parquet (written by aoc-export)")
    ap.add_argument("--hold", type=float, default=None,
                    help="forget a received object when it is older than this value in s (default: keep it until the end of the run)")
    ap.add_argument("--q", action="append", default=[], metavar="CLASS=VALUE", help="motion value of the reliability curve for one class")
    ap.add_argument("--drop-unmatched", action="store_true", help="leave out the rows without a true object or without a time")
    ap.add_argument("--weather", default=None, help="weather of the replay and of the sensor tables: clear or rain (default: the one of the run, else clear)")
    ap.add_argument("--no-sensor-tables", action="store_true", help="no sensor tables: every logged sensor row is kept and every sensor is fully reliable")
    args = ap.parse_args(argv)
    try:
        from .discounting import _q_options
        trust = TrustModel(q_by_class=_q_options(args.q))
        if args.hold is not None and not args.hold > 0.0:
            raise ValueError("--hold must be a positive number of seconds")
        run = load_run(args.run_dir, noise=NoiseModel(), missing="drop" if args.drop_unmatched else "raise", weather=args.weather,
                       sensors=not args.no_sensor_tables)
        c = check_run(run, trust=trust, hold=args.hold)
    except (FileNotFoundError, ValueError) as err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 2

    number = lambda value: "none" if value is None else "%.3f" % value
    share = lambda part, whole: "none" if not whole else "%.1f%%" % (100.0 * part / whole)
    print("B6 COMBINATION CHECK")
    print("  run:                 %s" % run.info.get("run_id", os.path.abspath(args.run_dir)))
    print("  memory:              a received object is %s"
          % ("kept until the end of the run, however old" if args.hold is None else "forgotten when it is older than %g s" % args.hold))
    print("  objects:             %d in %d ticks of %d vehicles, fused into %d real objects, in %.1f s"
          % (c["objects"], c["ticks"], len(run.egos), c["fused"], c["seconds"]))
    print("  fused objects:       %s with on-board evidence, %s with received evidence, %s with both, %s with several senders"
          % (share(c["with_local"], c["fused"]), share(c["with_received"], c["fused"]), share(c["with_both"], c["fused"]),
             share(c["several_senders"], c["fused"])))
    print("  wrong groups:        %d (members that name different true objects); objects without a true object at the tick: %d"
          % (c["wrong_groups"], c["gone"]))
    print("  several senders:     Dempster only gives %s more belief than the cautious rule on average (at most %s)"
          % (number(c["gain_mean"]), number(c["gain_max"])))
    print("  class conflict:      largest mass on the empty set %s" % number(c["conflict_class_max"]))
    print("  how right is the fused belief that the object exists (0 is perfect):")
    print("      %-44s %17s   %11s" % ("", "calibration error", "Brier score"))
    labels = {"B0": LAW_NAMES["full_trust"], "B1": LAW_NAMES["cutoff"], "B3": "discount, cautious rule (B3)", "B4": "discount, Dempster only (B4)"}
    for name in ("B0", "B1", "B3", "B4"):
        print("      %-44s %17s   %11s" % (labels[name], number(c["calibration_error"][name]), number(c["brier"][name])))
    print("  result:              %s%s" % (c["result"], "" if not c["problems"] else " (" + "; ".join(c["problems"]) + ")"))
    return 0 if not c["problems"] else 3


if __name__ == "__main__":
    sys.exit(main())
