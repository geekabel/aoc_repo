"""Association (second half of task B3).

At a tick, a vehicle knows objects from several sources: each of its sensors, and each vehicle that
sends CPMs. Several of these objects are the same real object. This module decides which ones.
The result is a group number for each object: the objects of one group are one real object.

The method is the standard one for track-to-track association, written from scratch:

  1. All objects are first moved to the time of the tick (``aoc.fusion.alignment``).
  2. The sources are taken one after the other: the sensors of the vehicle first, then the senders.
     The objects of the first source open one group each.
  3. Each object of the next source is compared with each group. Two tests decide if a match is allowed:
       - the gate: the distance between the two states, counted in units of their joint uncertainty
         (the Mahalanobis distance), must be below a limit;
       - the coincidence test: the match must be more likely than finding another object at this
         place by chance. A vague object fails this test: it could match anything, so a match with
         it proves nothing.
  4. Among the allowed matches, the best global choice is taken: each object goes to at most one
     group, each group takes at most one object of this source, and the total cost is the lowest
     (the Hungarian method). An object without a match opens a new group.

Rules that follow from the design:
  - Two objects of one source are never in one group. A source reports a real object once.
  - An old object that fails the coincidence test stays alone in its group. It is not thrown away:
    the fusion gives it the reliability of its age.
  - The identifiers given by the sources are not used to match. In particular the track number of
    the local environment model of Artery, which is the same for all sensors of a vehicle, is not
    used: it comes from the simulator and a real vehicle does not have it.

    from aoc.fusion.alignment import align
    from aoc.fusion.association import associate
    aligned = align(tick)
    groups = associate(aligned)                 # one group number per row of aligned.objects

Run this module to check a run:  python -m aoc.fusion.association RUN_FOLDER      (command ``aoc-associate``)
"""
from __future__ import annotations

import argparse
import functools
import os
import sys
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from scipy.stats import chi2

from .alignment import Aligned, align
from .noise import NoiseModel
from .replay import Run, load_run

_BIG = 1.0e9          # cost of a match that is not allowed
_FLOOR = 1.0e-6       # smallest variance of a pair (1 mm, 1 mm/s): keeps an exact estimate usable


@functools.lru_cache(maxsize=None)
def _chi2_limit(probability: float, dimension: int) -> float:
    return float(chi2.ppf(probability, dimension))


@dataclass(frozen=True)
class AssociationConfig:
    """Settings of the association.

    gate_probability   a true match passes the gate with this probability (default 0.99)
    use_velocity       False (default): compare the positions. True: compare positions and speeds.
                       On the test runs the positions alone give fewer wrong and fewer missed matches,
                       because the speed of an old object is the weakest part of its state.
    density_position   chance objects per square metre around a group, for the coincidence test
                       (default 0.01: one object per 100 m2)
    density_velocity   the same for the speed, per (m/s)2 (default 0.03); used only with use_velocity
    """

    gate_probability: float = 0.99
    use_velocity: bool = False
    density_position: float = 0.01
    density_velocity: float = 0.03

    def __post_init__(self):
        if not 0.0 < self.gate_probability < 1.0:
            raise ValueError("gate_probability must be between 0 and 1")
        if not (self.density_position > 0.0 and self.density_velocity > 0.0):
            raise ValueError("the densities must be positive")

    @property
    def dimension(self) -> int:
        return 4 if self.use_velocity else 2

    @property
    def gate(self) -> float:
        """Limit of the squared Mahalanobis distance."""
        return _chi2_limit(self.gate_probability, self.dimension)

    @property
    def log_density(self) -> float:
        density = self.density_position * (self.density_velocity if self.use_velocity else 1.0)
        return float(np.log(density))


def _with_floor(cov: np.ndarray) -> np.ndarray:
    """Raises the variances that are below the floor (in place) and returns the array."""
    diagonal = np.arange(cov.shape[-1])
    cov[..., diagonal, diagonal] = np.maximum(cov[..., diagonal, diagonal], _FLOOR)
    return cov


def pair_costs(state_a: np.ndarray, cov_a: np.ndarray, state_b: np.ndarray, cov_b: np.ndarray,
               config: AssociationConfig) -> tuple[np.ndarray, np.ndarray]:
    """Compares every object of list a with every object of list b.

    Returns two tables of shape (len(a), len(b)):
      distance   the squared Mahalanobis distance d2, with the sum of the two covariances
      cost       d2 + ln det(2 pi S) + 2 ln(density): negative when the match is more likely than a coincidence
    The two estimates are taken as independent. This is the usual simplification of track-to-track association.
    A variance of S below a very small value (1 mm, 1 mm/s) is raised to it, so that two exact estimates can still be compared.

    With use_velocity, the full comparison is made only for the pairs whose positions alone pass the gate.
    The distance on position and speed is never smaller than the distance on the position alone, so no
    allowed match is lost. The other pairs get an infinite distance and an infinite cost.
    """
    dx = state_a[:, None, 0] - state_b[None, :, 0]
    dy = state_a[:, None, 1] - state_b[None, :, 1]
    sxx = np.maximum(cov_a[:, None, 0, 0] + cov_b[None, :, 0, 0], _FLOOR)
    syy = np.maximum(cov_a[:, None, 1, 1] + cov_b[None, :, 1, 1], _FLOOR)
    sxy = cov_a[:, None, 0, 1] + cov_b[None, :, 0, 1]
    det = sxx * syy - sxy**2
    distance = (syy * dx**2 - 2.0 * sxy * dx * dy + sxx * dy**2) / det
    if not config.use_velocity:
        return distance, distance + 2.0 * np.log(2.0 * np.pi) + np.log(det) + 2.0 * config.log_density

    near = distance <= config.gate
    distance = np.full(near.shape, np.inf)
    cost = np.full(near.shape, np.inf)
    ia, ib = np.nonzero(near)
    if len(ia):
        diff = state_a[ia] - state_b[ib]
        joint = _with_floor(cov_a[ia] + cov_b[ib])
        solved = np.linalg.solve(joint, diff[..., None])[..., 0]
        d2 = np.einsum("ni,ni->n", diff, solved)
        _, log_det = np.linalg.slogdet(joint)
        distance[ia, ib] = d2
        cost[ia, ib] = d2 + 4.0 * np.log(2.0 * np.pi) + log_det + 2.0 * config.log_density
    return distance, cost


def can_match(cov: np.ndarray, config: AssociationConfig) -> np.ndarray:
    """False for an object that is too vague to pass the coincidence test with any partner.

    The joint covariance of a pair is never smaller than the covariance of one of its two objects.
    So if the object alone already fails the test at distance zero, every pair with it fails.
    """
    d = config.dimension
    _, log_det = np.linalg.slogdet(_with_floor(cov[:, :d, :d].copy()))
    return d * np.log(2.0 * np.pi) + log_det + 2.0 * config.log_density < 0.0


def best_matches(distance: np.ndarray, cost: np.ndarray, gate: float) -> list[tuple[int, int]]:
    """The best global choice of matches between rows (groups) and columns (objects).

    A match is allowed when its distance is inside the gate and its cost is negative.
    Each row and each column is used at most once. Leaving a column without a match costs 0.
    """
    n_rows, n_cols = distance.shape
    if n_rows == 0 or n_cols == 0:
        return []
    allowed = (distance <= gate) & (cost < 0.0)
    if not allowed.any():
        return []
    table = np.full((n_rows + n_cols, n_cols), _BIG)
    table[:n_rows][allowed] = cost[allowed]
    table[n_rows + np.arange(n_cols), np.arange(n_cols)] = 0.0          # "no match" for each column
    rows, cols = linear_sum_assignment(table)
    return [(int(r), int(c)) for r, c in zip(rows, cols) if r < n_rows and allowed[r, c]]


def source_order(objects: pd.DataFrame) -> list[str]:
    """The sources in the order in which they are processed: sensors of the vehicle first, then the senders, by name."""
    names = pd.unique(objects["source"])
    return sorted(names, key=lambda name: (str(name).startswith("v2x:"), str(name)))


def associate(aligned: Aligned, config: AssociationConfig | None = None) -> np.ndarray:
    """Gives a group number to each object of an aligned tick. Objects with the same number are one real object.

    An object of a new source is compared with every member of a group that can still match
    (see ``can_match``). The pair with the lowest cost stands for the group. The groups are numbered from 0 in the order in which they are opened.
    """
    config = config or AssociationConfig()
    n = len(aligned)
    groups = np.full(n, -1, dtype=np.int64)
    if n == 0:
        return groups
    state, cov = aligned.state, aligned.cov
    source = aligned.objects["source"].to_numpy(dtype=object)
    if not all(isinstance(name, str) for name in source):
        raise ValueError("every object needs a source name")
    usable = can_match(cov, config)
    gate = config.gate
    n_groups = 0
    placed = np.zeros(0, dtype=np.int64)                       # usable objects that are already in a group
    for name in source_order(aligned.objects):
        members = np.flatnonzero(source == name)
        open_members = members[usable[members]]
        matched: dict[int, int] = {}
        if len(placed) and len(open_members):
            distance, cost = pair_costs(state[placed], cov[placed], state[open_members], cov[open_members], config)
            allowed = (distance <= gate) & (cost < 0.0)
            if allowed.any():
                # one line per group: the lowest cost over its members
                group_of = groups[placed]
                ids, first = np.unique(group_of, return_index=True)        # placed is kept sorted by group
                cost = np.where(allowed, cost, np.inf)
                per_group = np.minimum.reduceat(cost, first, axis=0)
                finite = np.isfinite(per_group)
                table_d = np.where(finite, 0.0, np.inf)                     # the gate was applied above
                table_c = np.where(finite, per_group, np.inf)
                for row, col in best_matches(table_d, table_c, gate):
                    matched[int(open_members[col])] = int(ids[row])
        for obj in members:
            if obj in matched:
                groups[obj] = matched[obj]
            else:
                groups[obj] = n_groups
                n_groups += 1
        if len(open_members):
            placed = np.concatenate([placed, open_members])
            placed = placed[np.argsort(groups[placed], kind="stable")]
    return groups


def oracle_groups(aligned: Aligned) -> np.ndarray:
    """The perfect association, from the ground truth: one group per true object.

    For evaluation and for ablations only. Objects without a true object get a group each.
    """
    truth = aligned.objects["gt_match_id"].to_numpy(dtype=object)
    groups = np.full(len(truth), -1, dtype=np.int64)
    seen: dict = {}
    for i, name in enumerate(truth):
        if name is None or (isinstance(name, float) and np.isnan(name)):
            groups[i] = len(seen)
            seen[("alone", i)] = groups[i]
        else:
            groups[i] = seen.setdefault(name, len(seen))
    return groups


# ----------------------------------------------------------------------------
# Quality of an association, measured with the ground truth
# ----------------------------------------------------------------------------
_COUNTS = ("same_group", "same_group_true", "same_truth", "same_truth_found", "fresh_same_truth", "fresh_same_truth_found",
           "recent_same_truth", "recent_same_truth_found")


def pair_counts(aligned: Aligned, groups: np.ndarray, fresh_age: float = 0.5, recent_age: float = 1.0) -> dict:
    """Counts the pairs of objects of one tick, to compare an association with the ground truth.

    A pair is two objects from two different sources.
      same_group        pairs put in one group
      same_group_true   of these, pairs that are one real object (the others are wrong merges)
      same_truth        pairs that are one real object
      same_truth_found  of these, pairs put in one group (the others are missed)
      fresh_*           the same two counts for pairs in which both objects are younger than fresh_age
      recent_*          the same two counts for pairs in which both objects are younger than recent_age
    """
    objects = aligned.objects
    n = len(objects)
    out = dict.fromkeys(_COUNTS, 0)
    if n < 2:
        return out
    truth = objects["gt_match_id"].to_numpy(dtype=object)
    source = objects["source"].to_numpy(dtype=object)
    age = objects["age_now"].to_numpy(dtype=float)
    fresh, recent = age <= fresh_age, age <= recent_age
    upper = np.triu(np.ones((n, n), dtype=bool), k=1) & (source[:, None] != source[None, :])
    has_truth = np.array([isinstance(name, str) for name in truth])
    same_truth = (truth[:, None] == truth[None, :]) & has_truth[:, None] & has_truth[None, :] & upper
    same_group = (groups[:, None] == groups[None, :]) & upper
    both_fresh = fresh[:, None] & fresh[None, :]
    out["same_group"] = int(same_group.sum())
    out["same_group_true"] = int((same_group & same_truth).sum())
    out["same_truth"] = int(same_truth.sum())
    out["same_truth_found"] = int((same_truth & same_group).sum())
    out["fresh_same_truth"] = int((same_truth & both_fresh).sum())
    out["fresh_same_truth_found"] = int((same_truth & both_fresh & same_group).sum())
    both_recent = recent[:, None] & recent[None, :]
    out["recent_same_truth"] = int((same_truth & both_recent).sum())
    out["recent_same_truth_found"] = int((same_truth & both_recent & same_group).sum())
    return out


def check_run(run: Run, config: AssociationConfig | None = None, period: float = 0.1, hold: float | None = None,
              fresh_age: float = 0.5, recent_age: float = 1.0) -> dict:
    """Aligns and associates every tick of every vehicle of a run, and measures the result with the ground truth.

    Three numbers decide the result:
      precision      share of the matches that join one real object (limit 0.99)
      recall_fresh   share found of the pairs of one real object in which both objects are younger than fresh_age (limit 0.95)
      recall_recent  the same for recent_age (limit 0.90)
    """
    config = config or AssociationConfig()
    total = dict.fromkeys(_COUNTS, 0)
    ticks = n_objects = n_groups = n_alone = same_source_twice = 0
    start = time.perf_counter()
    for ego in run.egos:
        for tick in run.replay(ego, period=period, hold=hold):
            aligned = align(tick)
            groups = associate(aligned, config)
            ticks += 1
            n_objects += len(groups)
            if len(groups):
                n_groups += len(np.unique(groups))
                n_alone += int((np.unique(groups, return_counts=True)[1] == 1).sum())
                in_group = set(zip(groups.tolist(), aligned.objects["source"].to_numpy(dtype=object).tolist()))
                same_source_twice += len(groups) - len(in_group)
            for key, value in pair_counts(aligned, groups, fresh_age, recent_age).items():
                total[key] += value

    def ratio(part: str, whole: str) -> float | None:
        return total[part] / total[whole] if total[whole] else None

    out = dict(total)
    out.update(ticks=ticks, objects=n_objects, groups=n_groups, groups_with_one_object=n_alone, seconds=round(time.perf_counter() - start, 1),
               same_source_twice_in_a_group=same_source_twice)
    precision, fresh = ratio("same_group_true", "same_group"), ratio("fresh_same_truth_found", "fresh_same_truth")
    recent, everything = ratio("recent_same_truth_found", "recent_same_truth"), ratio("same_truth_found", "same_truth")
    problems = []
    if ticks == 0:
        problems.append("the run gave no tick")
    if same_source_twice:
        problems.append("two objects of one source are in one group")
    if total["fresh_same_truth"] == 0:
        problems.append("the run holds no fresh pair of one real object: the association cannot be checked")
    if precision is not None and precision < 0.99:
        problems.append("more than 1% of the matches join two different real objects")
    if fresh is not None and fresh < 0.95:
        problems.append("more than 5%% of the pairs of one real object younger than %g s are missed" % fresh_age)
    if recent is not None and recent < 0.90:
        problems.append("more than 10%% of the pairs of one real object younger than %g s are missed" % recent_age)
    for name, value in (("precision", precision), ("recall_fresh", fresh), ("recall_recent", recent), ("recall_all_ages", everything)):
        out[name] = None if value is None else round(value, 4)
    out["problems"] = problems
    out["result"] = "PASS" if not problems else "FAIL"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Align and associate every tick of an exported run, and compare the result with the ground truth.")
    ap.add_argument("run_dir", help="folder with observations.parquet and truth.parquet (written by aoc-export)")
    ap.add_argument("--seed", type=int, default=0, help="seed of the noise (default 0)")
    ap.add_argument("--gate", type=float, default=0.99, help="probability that a true match passes the gate (default 0.99)")
    ap.add_argument("--with-speed", action="store_true", help="compare positions and speeds (default: positions only)")
    ap.add_argument("--hold", type=float, default=None, help="forget a CPM object older than this many seconds (default: never)")
    ap.add_argument("--drop-unmatched", action="store_true", help="leave out the rows without a true object or without a time")
    args = ap.parse_args(argv)
    try:
        config = AssociationConfig(gate_probability=args.gate, use_velocity=args.with_speed)
        run = load_run(args.run_dir, noise=NoiseModel(seed=args.seed), missing="drop" if args.drop_unmatched else "raise")
        c = check_run(run, config, hold=args.hold)
    except (FileNotFoundError, ValueError) as err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 2

    def share(part, whole):
        return "%d of %d (%.2f%%)" % (part, whole, 100.0 * part / whole) if whole else "none"

    print("B3 ASSOCIATION CHECK")
    print("  run:                 %s" % run.info.get("run_id", os.path.abspath(args.run_dir)))
    print("  settings:            gate %.2f, %s, seed %d, hold %s"
          % (config.gate_probability, "position and speed" if config.use_velocity else "position only", args.seed,
             "none" if args.hold is None else "%g s" % args.hold))
    print("  ticks:               %d ticks of %d vehicles in %.1f s" % (c["ticks"], len(run.egos), c["seconds"]))
    print("  per tick, average:   %.1f objects in %.1f groups, %.1f groups with one object"
          % (c["objects"] / max(c["ticks"], 1), c["groups"] / max(c["ticks"], 1), c["groups_with_one_object"] / max(c["ticks"], 1)))
    print("  matches that join one real object:                        %s" % share(c["same_group_true"], c["same_group"]))
    print("  pairs of one real object, both younger than 0.5 s, found: %s" % share(c["fresh_same_truth_found"], c["fresh_same_truth"]))
    print("  pairs of one real object, both younger than 1 s, found:   %s" % share(c["recent_same_truth_found"], c["recent_same_truth"]))
    print("  groups with two objects of one source:                    %d" % c["same_source_twice_in_a_group"])
    print("  result:              %s%s" % (c["result"], "" if not c["problems"] else " (" + "; ".join(c["problems"]) + ")"))
    return 0 if not c["problems"] else 3


if __name__ == "__main__":
    sys.exit(main())
