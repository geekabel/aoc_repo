"""The fusion of the places and the speeds of one real object (task B7).

After the association (B3), the objects of a group are one real object. Each of them has a state at the time of
the tick (x, y, vx, vy) and a covariance (the alignment, B3). This module gives one state and one covariance per
group. It is step 6 of the pipeline of the plan: the kinematic layer. It knows nothing about the existence and
the class (the evidential layer, B4 to B6). The two layers stay separate, as the plan asks.

Why covariance intersection
  Two reports of one object may share a part of their error: two neighbours that saw it from the same place, or
  two reports moved forward with the same unknown motion. The Kalman way of fusing (add the inverse covariances)
  assumes independent errors. With shared errors it gives a covariance that is too small: the fused object looks
  more sure than it is. Covariance intersection (Julier and Uhlmann, 1997) takes a convex combination of the
  inverse covariances instead. Its covariance is safe whatever the correlation: it never says less than the truth.
  The price is a covariance that is larger than needed when the errors are independent.

Methods
  ci           covariance intersection, two members at a time: the weight of each step is the one that gives
               the smallest determinant (default) or the smallest trace of the fused covariance (Julier and Uhlmann)
  fast_ci      covariance intersection with fixed weights: the weight of a member is the inverse of the trace of its
               covariance, rescaled to a total of 1 (Niehsen, 2002). No search, one pass for any number of members
  independent  the Kalman way: the inverse covariances add up. Right when the errors are independent, too sure otherwise
  newest       the newest member alone, as task B6 does before this task. A baseline

    from aoc.fusion.kinematics import fuse_states
    states = fuse_states(aligned, groups)          # groups from associate(aligned)
    states.state                                   # one line per group: x, y, vx, vy at the time of the tick
    states.cov                                     # one 4 x 4 covariance per group

Run this module to check a run:  python -m aoc.fusion.kinematics RUN_FOLDER      (command ``aoc-kinematics``)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .alignment import Aligned, align
from .association import AssociationConfig, associate
from .noise import NoiseModel
from .replay import Run, load_run

METHODS = ("ci", "fast_ci", "independent", "newest")
CRITERIA = ("det", "trace")
GOLDEN = (np.sqrt(5.0) - 1.0) / 2.0


@dataclass(frozen=True)
class KinematicsModel:
    """How the states of a group are fused.

    method      "ci" (default), "fast_ci", "independent" or "newest"
    criterion   what the weight of covariance intersection makes smallest: "det" (default) or "trace"
    steps       number of steps of the search of the weight (default 40: the weight is found within 1e-8)
    """

    method: str = "ci"
    criterion: str = "det"
    steps: int = 40

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError("the method must be one of: %s" % ", ".join(METHODS))
        if self.criterion not in CRITERIA:
            raise ValueError("the criterion must be one of: %s" % ", ".join(CRITERIA))
        if int(self.steps) != self.steps or self.steps < 1:
            raise ValueError("steps must be a whole number, at least 1")


@dataclass
class FusedStates:
    """The state of each real object of one tick."""

    state: np.ndarray       # shape (g, 4): x, y, vx, vy at the time of the tick
    cov: np.ndarray         # shape (g, 4, 4): the covariance of the state
    members: np.ndarray     # shape (n,): the line of ``state`` of each object of the tick
    n_members: np.ndarray   # shape (g,): how many objects were fused into each line

    def __len__(self) -> int:
        return len(self.state)


# ----------------------------------------------------------------------------
# Two estimates at a time
# ----------------------------------------------------------------------------
def _check_pair(xa, pa, xb, pb) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, bool]:
    """The two estimates as tables, and whether a single estimate was given (one state, one covariance)."""
    xa, xb = np.asarray(xa, dtype=float), np.asarray(xb, dtype=float)
    pa, pb = np.asarray(pa, dtype=float), np.asarray(pb, dtype=float)
    single = xa.ndim == 1
    if single:
        xa, xb, pa, pb = xa[None], xb[None], pa[None], pb[None]
    n, d = xa.shape if xa.ndim == 2 else (0, 0)
    if xa.ndim != 2 or xb.shape != (n, d) or pa.shape != (n, d, d) or pb.shape != (n, d, d):
        raise ValueError("two tables of states (n, d) and two tables of covariances (n, d, d) are needed")
    if not (np.isfinite(xa).all() and np.isfinite(xb).all() and np.isfinite(pa).all() and np.isfinite(pb).all()):
        raise ValueError("a state or a covariance is missing")
    _check_covariances(pa)
    _check_covariances(pb)
    return xa, pa, xb, pb, single


def _check_covariances(p: np.ndarray) -> None:
    """A covariance is symmetric and positive definite."""
    if len(p) and not np.allclose(p, np.swapaxes(p, -1, -2), rtol=1e-6, atol=1e-9):
        raise ValueError("a covariance is not symmetric")
    if len(p) and (np.linalg.eigvalsh(p) <= 0.0).any():
        raise ValueError("a covariance is not positive definite")


def _information(p: np.ndarray) -> np.ndarray:
    """The inverse of each covariance. A covariance that cannot be inverted is an error."""
    try:
        return np.linalg.inv(p)
    except np.linalg.LinAlgError:
        raise ValueError("a covariance cannot be inverted") from None


def _size(p: np.ndarray, criterion: str) -> np.ndarray:
    """The size of each covariance: its determinant or its trace."""
    return np.linalg.det(p) if criterion == "det" else np.trace(p, axis1=1, axis2=2)


def ci_weight(pa, pb, criterion: str = "det", steps: int = 40) -> np.ndarray:
    """The weight omega of covariance intersection for each pair of covariances.

    It is the weight that makes the fused covariance smallest by the criterion ("det" or "trace"). It is found by
    a golden-section search on [0, 1]: the size of the fused covariance has a single minimum in omega
    (the logarithm of the determinant of a convex combination of matrices is concave, and the trace of its inverse
    is convex). With 40 steps the weight is found within 1e-8.
    """
    if criterion not in CRITERIA:
        raise ValueError("the criterion must be one of: %s" % ", ".join(CRITERIA))
    pa, pb = np.asarray(pa, dtype=float), np.asarray(pb, dtype=float)
    if pa.ndim == 2:
        pa, pb = pa[None], pb[None]
    if pa.ndim != 3 or pa.shape != pb.shape or pa.shape[1] != pa.shape[2] or not (np.isfinite(pa).all() and np.isfinite(pb).all()):
        raise ValueError("two tables of covariances (n, d, d) of the same shape are needed")
    _check_covariances(pa)
    _check_covariances(pb)
    ia, ib = _information(pa), _information(pb)

    def size(w):
        return _size(np.linalg.inv(w[:, None, None] * ia + (1.0 - w[:, None, None]) * ib), criterion)

    low, high = np.zeros(len(pa)), np.ones(len(pa))
    c, d = high - GOLDEN * (high - low), low + GOLDEN * (high - low)
    fc, fd = size(c), size(d)
    for _ in range(int(steps)):
        same = np.isclose(fc, fd, rtol=1e-12, atol=0.0)          # a flat stretch (two equal covariances): the minimum is between c and d
        left = fc < fd                                            # else it is in [low, d] when True, in [c, high] otherwise
        high = np.where(same, d, np.where(left, d, high))
        low = np.where(same, c, np.where(left, low, c))
        c_new, d_new = high - GOLDEN * (high - low), low + GOLDEN * (high - low)
        f_new = size(np.where(left, c_new, d_new))                # one new point per step; the other one is already known
        c, d = np.where(left, c_new, d), np.where(left, c, d_new)
        fc, fd = np.where(left, f_new, fd), np.where(left, fc, f_new)
        if same.any():
            c, d = np.where(same, c_new, c), np.where(same, d_new, d)
            fc, fd = np.where(same, size(c_new), fc), np.where(same, size(d_new), fd)
    found = (low + high) / 2.0
    # a minimum at an edge, when the size there is clearly smaller, is taken exactly: the fused covariance is then
    # one of the two members. When the size is the same everywhere (two equal covariances), the found weight (one half) is kept.
    best, size_best = found, size(found)
    for edge in (np.zeros_like(found), np.ones_like(found)):
        size_edge = size(edge)
        take = size_edge < size_best * (1.0 - 1e-9)
        best, size_best = np.where(take, edge, best), np.where(take, size_edge, size_best)
    return best


def covariance_intersection(xa, pa, xb, pb, omega=None, criterion: str = "det", steps: int = 40) -> tuple[np.ndarray, np.ndarray]:
    """Covariance intersection of two estimates (Julier and Uhlmann, 1997), row by row.

        P^-1 = omega Pa^-1 + (1 - omega) Pb^-1
        x = P (omega Pa^-1 xa + (1 - omega) Pb^-1 xb)

    ``omega`` is one weight per row, or None: the weight that makes P smallest by the criterion.
    Returns (x, P). A single estimate (one state, one covariance) can be given without the table dimension.
    """
    xa, pa, xb, pb, single = _check_pair(xa, pa, xb, pb)
    if omega is None:
        w = ci_weight(pa, pb, criterion, steps)
    else:
        w = np.broadcast_to(np.asarray(omega, dtype=float), (len(xa),)).copy()
        if not np.isfinite(w).all() or (w < 0.0).any() or (w > 1.0).any():
            raise ValueError("omega must be between 0 and 1")
    ia, ib = _information(pa), _information(pb)
    info = w[:, None, None] * ia + (1.0 - w[:, None, None]) * ib
    cov = np.linalg.inv(info)
    vector = w[:, None] * np.einsum("nij,nj->ni", ia, xa) + (1.0 - w)[:, None] * np.einsum("nij,nj->ni", ib, xb)
    state = np.einsum("nij,nj->ni", cov, vector)
    cov = (cov + np.swapaxes(cov, 1, 2)) / 2.0
    return (state[0], cov[0]) if single else (state, cov)


def independent_fusion(xa, pa, xb, pb) -> tuple[np.ndarray, np.ndarray]:
    """The Kalman way, row by row: the inverse covariances add up. Right only when the two errors are independent."""
    xa, pa, xb, pb, single = _check_pair(xa, pa, xb, pb)
    ia, ib = _information(pa), _information(pb)
    cov = np.linalg.inv(ia + ib)
    state = np.einsum("nij,nj->ni", cov, np.einsum("nij,nj->ni", ia, xa) + np.einsum("nij,nj->ni", ib, xb))
    cov = (cov + np.swapaxes(cov, 1, 2)) / 2.0
    return (state[0], cov[0]) if single else (state, cov)


# ----------------------------------------------------------------------------
# A whole tick
# ----------------------------------------------------------------------------
def _by_group(groups: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, int]:
    groups = np.asarray(groups)
    if groups.shape != (n,):
        raise ValueError("one group number is needed per object (%d objects)" % n)
    if n and (not np.issubdtype(groups.dtype, np.integer) or (groups < 0).any()):
        raise ValueError("the group numbers must be whole numbers that are not negative")
    numbers, members = np.unique(groups, return_inverse=True)
    return numbers, np.asarray(members).ravel(), len(numbers)


def fuse_states(aligned: Aligned, groups, model: KinematicsModel | None = None) -> FusedStates:
    """One state and one covariance per group of an aligned tick.

    aligned   the tick after ``align`` (states and covariances at the time of the tick)
    groups    the group number of each object, from ``associate``
    """
    model = model or KinematicsModel()
    n = len(aligned)
    _, members, g = _by_group(groups, n)
    state_in, cov_in = np.asarray(aligned.state, dtype=float), np.asarray(aligned.cov, dtype=float)
    if state_in.shape != (n, 4) or cov_in.shape != (n, 4, 4):
        raise ValueError("the aligned tick needs a state (n, 4) and a covariance (n, 4, 4)")
    if n and not (np.isfinite(state_in).all() and np.isfinite(cov_in).all()):
        raise ValueError("a state or a covariance is missing")
    n_members = np.bincount(members, minlength=g)
    state, cov = np.zeros((g, 4)), np.zeros((g, 4, 4))
    if not n:
        return FusedStates(state=state, cov=cov, members=members, n_members=n_members)

    if model.method == "newest":
        t_meas = aligned.objects["t_meas"].to_numpy(dtype=float, na_value=np.nan)
        value = np.where(np.isfinite(t_meas), t_meas, -np.inf)
        order = np.lexsort((np.arange(n), -value, members))
        newest = order[np.r_[True, members[order][1:] != members[order][:-1]]]
        return FusedStates(state=state_in[newest], cov=cov_in[newest], members=members, n_members=n_members)

    if model.method == "fast_ci":
        weight = 1.0 / np.trace(cov_in, axis1=1, axis2=2)
        total = np.bincount(members, weights=weight, minlength=g)
        weight = weight / total[members]
        info = _information(cov_in) * weight[:, None, None]
        info_sum = np.zeros((g, 4, 4))
        np.add.at(info_sum, members, info)
        vector = np.zeros((g, 4))
        np.add.at(vector, members, np.einsum("nij,nj->ni", info, state_in))
        cov = np.linalg.inv(info_sum)
        return FusedStates(state=np.einsum("gij,gj->gi", cov, vector), cov=(cov + np.swapaxes(cov, 1, 2)) / 2.0, members=members, n_members=n_members)

    # "ci" and "independent": the members of all groups are taken two at a time, in the order of the rows:
    # the first member of every group, then the second with the result, and so on
    order = np.argsort(members, kind="stable")
    sorted_groups = members[order]
    first = np.r_[True, sorted_groups[1:] != sorted_groups[:-1]]
    position = np.arange(n) - np.maximum.accumulate(np.where(first, np.arange(n), 0))
    for step in range(int(position.max()) + 1):
        rows = order[position == step]
        which = members[rows]
        if step == 0:
            state[which], cov[which] = state_in[rows], cov_in[rows]
        elif model.method == "ci":
            state[which], cov[which] = covariance_intersection(state[which], cov[which], state_in[rows], cov_in[rows],
                                                               criterion=model.criterion, steps=model.steps)
        else:
            state[which], cov[which] = independent_fusion(state[which], cov[which], state_in[rows], cov_in[rows])
    return FusedStates(state=state, cov=cov, members=members, n_members=n_members)


def with_states(objects: pd.DataFrame, states: FusedStates) -> pd.DataFrame:
    """Adds the fused state to a table with one row per group (``fused.objects`` of task B6, same order of groups).

    New columns: x, y, vx, vy, sigma_xy (the root of the mean of the two position variances, in m), n_states.
    """
    if len(objects) != len(states):
        raise ValueError("the table holds %d rows, the states %d" % (len(objects), len(states)))
    out = objects.copy()
    out["x"], out["y"], out["vx"], out["vy"] = states.state.T
    out["sigma_xy"] = np.sqrt((states.cov[:, 0, 0] + states.cov[:, 1, 1]) / 2.0)
    out["n_states"] = states.n_members
    return out


# ----------------------------------------------------------------------------
# Check of a run from the command line
# ----------------------------------------------------------------------------
def true_states(aligned: Aligned, members: np.ndarray, g: int, truth: pd.DataFrame) -> np.ndarray:
    """The true state (x, y, vx, vy) of each group at the time of the tick, NaN when the members do not name one true object.

    This reads the ground truth: it is for the checks only.
    """
    names = aligned.objects["gt_match_id"].to_numpy(dtype=object)
    table = truth.drop_duplicates("obj_id").set_index("obj_id")[["x", "y", "vx", "vy"]]
    out = np.full((g, 4), np.nan)
    for k in range(g):
        found = {name for name in names[members == k]}
        if len(found) == 1:
            name = next(iter(found))
            if isinstance(name, str) and name in table.index:
                out[k] = table.loc[name].to_numpy(dtype=float)
    return out


def check_run(run: Run, association: AssociationConfig | None = None, period: float = 0.1, hold: float | None = None,
              epsilon: float = 1.5) -> dict:
    """Fuses the states of every group of every tick with five methods and measures them against the truth.

    For each method, three measures. The position error per axis (root mean square, in m). The share of fused
    objects within ``epsilon`` of the true place. The normalised error of the position (NEES): the squared error
    in units of the covariance. It is 2 on average when the covariance is right, less when the covariance is
    larger than needed, more when it is too sure. Only the groups with two members or more are measured: the
    others are the same for every method.

    For the groups of three members or more, the order dependence of covariance intersection is also measured:
    the members are fused again in the reverse order, and the shift of the fused place is given in units of
    the fused covariance.
    """
    methods = {"ci": KinematicsModel("ci", "det"), "ci_trace": KinematicsModel("ci", "trace"), "fast_ci": KinematicsModel("fast_ci"),
               "independent": KinematicsModel("independent"), "newest": KinematicsModel("newest")}
    sums = {name: dict(n=0, se=0.0, within=0, nees=0.0, larger_than_members=0, nees_speed=0.0) for name in methods}
    # what each method promises about the size of the fused covariance, against the covariances of the members
    promise = {"ci": ("det", "min"), "ci_trace": ("trace", "min"), "independent": ("det", "min"), "fast_ci": ("det", "max")}
    counts = dict(ticks=0, objects=0, groups=0, groups_of_several=0, without_truth=0, largest_group=0, groups_of_three=0)
    order_shift = 0.0
    start = time.perf_counter()
    for ego in run.egos:
        for tick in run.replay(ego, period=period, hold=hold):
            aligned = align(tick)
            counts["ticks"] += 1
            if not len(aligned):
                continue
            groups = associate(aligned, association)
            _, members, g = _by_group(groups, len(aligned))
            counts["objects"] += len(aligned)
            counts["groups"] += g
            n_members = np.bincount(members, minlength=g)
            counts["largest_group"] = max(counts["largest_group"], int(n_members.max()))
            several = n_members >= 2
            counts["groups_of_several"] += int(several.sum())
            if not several.any():
                continue
            truth = true_states(aligned, members, g, tick.truth)
            known = several & np.isfinite(truth).all(axis=1)
            counts["without_truth"] += int((several & ~known).sum())
            if not known.any():
                continue
            bounds = {}
            for criterion in CRITERIA:
                sizes = _size(aligned.cov, criterion)
                smallest, largest = np.full(g, np.inf), np.full(g, -np.inf)
                np.minimum.at(smallest, members, sizes)
                np.maximum.at(largest, members, sizes)
                bounds[criterion] = {"min": smallest, "max": largest}
            for name, model in methods.items():
                fused = fuse_states(aligned, groups, model)
                error = fused.state[known] - truth[known]
                pos_cov = fused.cov[known][:, :2, :2]
                nees = np.einsum("ni,nij,nj->n", error[:, :2], np.linalg.inv(pos_cov), error[:, :2])
                speed_cov = fused.cov[known][:, 2:, 2:]
                nees_speed = np.einsum("ni,nij,nj->n", error[:, 2:], np.linalg.inv(speed_cov), error[:, 2:])
                s = sums[name]
                s["n"] += int(known.sum())
                s["se"] += float((error[:, :2] ** 2).sum())
                s["within"] += int((np.hypot(error[:, 0], error[:, 1]) <= epsilon).sum())
                s["nees"] += float(nees.sum())
                s["nees_speed"] += float(nees_speed.sum())
                if name in promise:
                    criterion, which = promise[name]
                    size = _size(fused.cov[known], criterion)
                    s["larger_than_members"] += int((size > bounds[criterion][which][known] * (1.0 + 1e-6)).sum())
                if name == "ci":
                    three = known & (n_members >= 3)
                    if three.any():
                        reverse = Aligned(t=aligned.t, ego=aligned.ego, objects=aligned.objects.iloc[::-1], state=aligned.state[::-1], cov=aligned.cov[::-1])
                        backwards = fuse_states(reverse, np.asarray(groups)[::-1], model)
                        shift = fused.state[three, :2] - backwards.state[three, :2]
                        order_shift += float(np.sqrt(np.einsum("ni,nij,nj->n", shift, np.linalg.inv(fused.cov[three][:, :2, :2]), shift)).sum())
                        counts["groups_of_three"] += int(three.sum())
    seconds = time.perf_counter() - start
    measures = {}
    for name, s in sums.items():
        measures[name] = dict(n=s["n"], rmse=round(float(np.sqrt(s["se"] / (2 * s["n"]))), 4) if s["n"] else None,
                              within=round(s["within"] / s["n"], 4) if s["n"] else None, nees=round(s["nees"] / s["n"], 3) if s["n"] else None,
                              nees_speed=round(s["nees_speed"] / s["n"], 3) if s["n"] else None, larger_than_members=s["larger_than_members"])
    problems = ["the run holds no object"] if counts["objects"] == 0 else []
    texts = {"ci": "%d fused covariances have a larger determinant than the smallest member covariance",
             "ci_trace": "%d fused covariances have a larger trace than the smallest member covariance",
             "independent": "%d fused covariances have a larger determinant than the smallest member covariance",
             "fast_ci": "%d fused covariances have a larger determinant than the largest member covariance"}
    for name, text in texts.items():
        if measures[name]["larger_than_members"]:
            problems.append("%s: %s" % (name, text % measures[name]["larger_than_members"]))
    if measures["ci"]["n"] and measures["ci"]["nees"] > measures["newest"]["nees"] + 0.1:
        problems.append("covariance intersection is more sure than its members: NEES %.2f against %.2f for the newest member alone"
                        % (measures["ci"]["nees"], measures["newest"]["nees"]))
    order = round(order_shift / counts["groups_of_three"], 4) if counts["groups_of_three"] else None
    return dict(**counts, hold=hold, seconds=round(seconds, 1), measures=measures, order_shift=order, problems=problems,
                result="PASS" if not problems else "FAIL")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fuse the places and the speeds of the objects of each group of an exported run, with covariance "
                                             "intersection and three other methods, and measure them against the truth.")
    ap.add_argument("run_dir", help="folder with observations.parquet and truth.parquet (written by aoc-export)")
    ap.add_argument("--hold", type=float, default=None,
                    help="forget a received object when it is older than this value in s (default: keep it until the end of the run)")
    ap.add_argument("--drop-unmatched", action="store_true", help="leave out the rows without a true object or without a time")
    args = ap.parse_args(argv)
    try:
        if args.hold is not None and not args.hold > 0.0:
            raise ValueError("--hold must be a positive number of seconds")
        run = load_run(args.run_dir, noise=NoiseModel(), missing="drop" if args.drop_unmatched else "raise")
        c = check_run(run, hold=args.hold)
    except (FileNotFoundError, ValueError, KeyError, np.linalg.LinAlgError) as err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 2

    number = lambda value, digits=3: "none" if value is None else ("%%.%df" % digits) % value
    percent = lambda value: "none" if value is None else "%.1f%%" % (100.0 * value)
    labels = {"ci": "covariance intersection, determinant", "ci_trace": "covariance intersection, trace", "fast_ci": "fast covariance intersection",
              "independent": "independent (Kalman)", "newest": "newest member alone"}
    print("B7 KINEMATICS CHECK")
    print("  run:                 %s" % run.info.get("run_id", os.path.abspath(args.run_dir)))
    print("  memory:              a received object is %s"
          % ("kept until the end of the run, however old" if args.hold is None else "forgotten when it is older than %g s" % args.hold))
    print("  objects:             %d in %d ticks of %d vehicles, in %d groups, in %.1f s" % (c["objects"], c["ticks"], len(run.egos), c["groups"], c["seconds"]))
    print("  groups of several:   %d (largest %d members); without one true object: %d" % (c["groups_of_several"], c["largest_group"], c["without_truth"]))
    print("  on the groups of two members or more (true place and speed at the time of the tick; RMSE per axis):")
    print("      %-40s %10s %12s %10s %12s" % ("", "RMSE (m)", "within 1.5 m", "NEES xy", "NEES speed"))
    for name in ("newest", "independent", "fast_ci", "ci_trace", "ci"):
        m = c["measures"][name]
        print("      %-40s %10s %12s %10s %12s" % (labels[name], number(m["rmse"]), percent(m["within"]), number(m["nees"], 2), number(m["nees_speed"], 2)))
    print("  NEES: 2 when the covariance is right, below 2 when it is larger than needed (safe), above 2 when it is too sure")
    print("  order of the members:  %d groups of three or more; fused the other way round, the place moves by %s of its uncertainty on average"
          % (c["groups_of_three"], number(c["order_shift"], 2)))
    print("  result:              %s%s" % (c["result"], "" if not c["problems"] else " (" + "; ".join(c["problems"]) + ")"))
    return 0 if not c["problems"] else 3


if __name__ == "__main__":
    sys.exit(main())
