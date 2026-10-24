"""The metrics of a run (task B8): one row of numbers per run and per way to handle trust.

This module runs the whole pipeline on a run (align, associate, evidence, discount, fuse, kinematics) for the
five ways of the plan, and measures each of them. The rows are what tasks D3 and D4 aggregate over the runs.

The ways (the baselines B0 to B4 of the plan)
  B0  full trust: no discount, the conjunctive rule everywhere
  B1  hard cutoff: a received object older than 1 s is dropped, full trust below (today's practice)
  B2  Bayesian inflation: no evidential layer. The places are fused the Kalman way, and the confidence is the
      probability that the fused place is within the tolerance of the true place, read from the fused covariance
  B3  AoC, the proposed one: the discount by age, the cautious rule for the received objects
  B4  ablation: the discount by age, the conjunctive rule everywhere (Dempster only)
The places and the speeds of B0, B1, B3 and B4 come from covariance intersection (B7); those of B2 from the Kalman way.

The metrics (the table of the plan, Methodology 3)
  network     mean channel busy ratio; mean packet delivery ratio; median and 95th percentile of the age of the
              received objects at reception. The same for every way.
  perception  awareness ratio: among the true objects within 200 m of the vehicle, the share that it knows,
              that is a fused object within 1.5 m of the true place with a belief of at least 0.5;
              RMSE: the position error per axis of the fused objects that are really there
  confidence  calibration error (10 bins) and Brier score of the belief that the object exists;
              coverage: the share of fused objects whose bin has belief <= share really there <= plausibility;
              sharpness: the mean width of the interval, plausibility minus belief
  stale       stale-confidence rate: the mean belief given to the fused objects whose true object left the
              simulation more than 1 s ago (the definition of the plan). Our 60 s runs have no such object, so the
              ghost rate is given too: the mean belief given to the fused objects that are not really there
  safety      time to confident: from the moment a true object enters the 200 m range of the vehicle to the
              moment a fused object within 1.5 m of it has a belief above 0.9; the median, and the share of
              entries that reach it

"Really there" means that the true object named by the members of the fused object (all of them the same) is
within the tolerance of the fused place. A fused object whose members name different true objects is a wrong
association and counts as not there.

    from aoc.analysis.metrics import evaluate_run
    table = evaluate_run(run)             # one row per way, hold 5 s (decision of Oct 24, 2026)

Run this module to evaluate a run:  python -m aoc.analysis.metrics RUN_FOLDER      (command ``aoc-evaluate``)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from ..fusion.alignment import align
from ..fusion.association import AssociationConfig, associate
from ..fusion.bba import EvidenceModel, build_evidence
from ..fusion.combination import CombinationModel, fuse, group_truth
from ..fusion.discounting import TrustModel, discount_evidence
from ..fusion.kinematics import KinematicsModel, fuse_states
from ..fusion.noise import NoiseModel
from ..fusion.replay import Run, load_run
from .calibration import CalibrationBins

WAYS = ("B0", "B1", "B2", "B3", "B4")
WAY_NAMES = {"B0": "full trust", "B1": "hard cutoff at 1 s", "B2": "Bayesian inflation", "B3": "AoC (discount, cautious rule)",
             "B4": "ablation (discount, Dempster only)"}


@dataclass(frozen=True)
class EvaluationModel:
    """The settings of the evaluation.

    hold               the receiver forgets a received object older than this value in s (decision of Oct 24, 2026: 5 s)
    epsilon            tolerance on the place of an object in m (1.5 m: half a lane)
    radius             range of the awareness ratio in m (200 m, as in the plan)
    known_belief       a true object is known when a fused object within epsilon has at least this belief
    confident_belief   the belief that the time to confident waits for
    left_for           an object has left the scene when it is out of the simulation for more than this value in s
    trust              the trust model of B3 and B4 (the law must be "aoc")
    combination        the combination model of B3
    association        the association settings
    evidence           the evidence model
    kinematics         the kinematic fusion of B0, B1, B3 and B4
    period             the fusion period in s
    """

    hold: float | None = 5.0
    epsilon: float = 1.5
    radius: float = 200.0
    known_belief: float = 0.5
    confident_belief: float = 0.9
    left_for: float = 1.0
    trust: TrustModel = TrustModel()
    combination: CombinationModel = CombinationModel()
    association: AssociationConfig | None = None
    evidence: EvidenceModel | None = None
    kinematics: KinematicsModel = KinematicsModel()
    period: float = 0.1

    def __post_init__(self):
        if self.hold is not None and not self.hold > 0.0:
            raise ValueError("hold must be a positive number of seconds, or None")
        if not (self.epsilon > 0.0 and self.radius > 0.0 and self.left_for >= 0.0 and self.period > 0.0):
            raise ValueError("epsilon, radius and period must be positive, left_for not negative")
        if not (0.0 <= self.known_belief <= 1.0 and 0.0 <= self.confident_belief <= 1.0):
            raise ValueError("the belief thresholds must be between 0 and 1")
        if self.trust.law != "aoc":
            raise ValueError("the trust model must use the law 'aoc': the evaluation builds the baselines itself")


class _Collector:
    """Collects the measures of one way over the ticks of a run."""

    def __init__(self):
        self.bins = CalibrationBins()
        self.pl_sum = 0.0
        self.bel_sum = 0.0
        self.bins_pl = np.zeros(10)                     # sum of the plausibilities per bin of the belief, for the coverage
        self.n = 0
        self.known = 0
        self.in_range = 0
        self.se = 0.0
        self.n_there = 0
        self.stale_sum = 0.0
        self.n_stale = 0
        self.ghost_sum = 0.0
        self.n_ghost = 0
        self.entries = 0
        self.confident_times: list[float] = []
        self.wrong = 0

    def add(self, bel, pl, hit):
        self.bins.add(bel, hit)
        self.n += len(bel)
        self.pl_sum += float(pl.sum())
        self.bel_sum += float(np.sum(bel))
        index = np.clip((np.clip(bel, 0.0, 1.0) * 10).astype(np.int64), 0, 9)
        self.bins_pl += np.bincount(index, weights=pl, minlength=10)

    def coverage(self) -> float | None:
        """The share of fused objects whose bin of belief has belief <= share really there <= plausibility."""
        if not self.n:
            return None
        covered = 0
        for b in range(10):
            count = self.bins.count[b]
            if count:
                mean_bel, freq, mean_pl = self.bins.confidence[b] / count, self.bins.hits[b] / count, self.bins_pl[b] / count
                if mean_bel - 1e-9 <= freq <= mean_pl + 1e-9:
                    covered += count
        return covered / self.n

    def row(self) -> dict:
        n = self.n
        return dict(fused_objects=n, calibration_error=round(self.bins.error(), 4) if n else None, brier=round(self.bins.brier(), 4) if n else None,
                    coverage=round(self.coverage(), 4) if n else None, sharpness=round((self.pl_sum - self.bel_sum) / n, 4) if n else None,
                    awareness_ratio=round(self.known / self.in_range, 4) if self.in_range else None, objects_in_range=self.in_range,
                    rmse=round(float(np.sqrt(self.se / (2 * self.n_there))), 4) if self.n_there else None, really_there=self.n_there,
                    stale_rate=round(self.stale_sum / self.n_stale, 4) if self.n_stale else None, stale_objects=self.n_stale,
                    ghost_rate=round(self.ghost_sum / self.n_ghost, 4) if self.n_ghost else None, ghost_objects=self.n_ghost,
                    wrong_groups=self.wrong, entries=self.entries,
                    time_to_confident=round(float(np.median(self.confident_times)), 3) if self.confident_times else None,
                    confident_share=round(len(self.confident_times) / self.entries, 4) if self.entries else None)


def network_metrics(run: Run) -> dict:
    """The network side of a run, from the observations table: channel busy ratio, delivery ratio, age at reception."""
    obs = run.obs
    received = (obs["src"] == "v2x").to_numpy(dtype=bool, na_value=False)

    def one_per(column: str, keys: list[str], rows=None) -> np.ndarray:
        """The values of a column, one per combination of the keys (a tick with 15 objects counts once, not 15 times)."""
        if column not in obs.columns:
            return np.full(0, np.nan)
        part = obs if rows is None else obs[rows]
        keys = [key for key in keys if key in part.columns]
        part = part.dropna(subset=[column]).drop_duplicates(subset=keys) if keys else part
        return part[column].to_numpy(dtype=float, na_value=np.nan)

    cbr = one_per("cbr", ["rx_station", "t_sim"])                                  # one value per vehicle and per time
    pdr = one_per("pdr_window", ["rx_station", "tx_station", "t_rx"], received)    # one value per received message
    age = obs["age"].to_numpy(dtype=float, na_value=np.nan)[received]
    quiet = lambda values, f: round(float(f(values[np.isfinite(values)])), 4) if np.isfinite(values).any() else None
    return dict(cbr_mean=quiet(cbr, np.mean), pdr_mean=quiet(pdr, np.mean), age_median=quiet(age, np.median),
                age_p95=quiet(age, lambda v: np.percentile(v, 95)), received_rows=int(received.sum()))


def _distance_confidence(cov: np.ndarray, epsilon: float) -> np.ndarray:
    """B2: the probability that the true place is within epsilon of the fused place, from the largest position variance."""
    pos = cov[:, :2, :2]
    largest = np.linalg.eigvalsh(pos)[:, -1]
    with np.errstate(divide="ignore"):
        return -np.expm1(-epsilon**2 / (2.0 * largest))


def evaluate_run(run: Run, model: EvaluationModel | None = None) -> pd.DataFrame:
    """One row per way (B0 to B4) with the metrics of the plan, for one run."""
    model = model or EvaluationModel()
    trust = {"B0": replace(model.trust, law="full_trust"), "B1": replace(model.trust, law="cutoff"), "B3": model.trust, "B4": model.trust}
    combination = {"B0": replace(model.combination, cpm_rule="conjunctive"), "B1": replace(model.combination, cpm_rule="conjunctive"),
                   "B3": model.combination, "B4": replace(model.combination, cpm_rule="conjunctive")}
    collectors = {way: _Collector() for way in WAYS}
    last_seen = run.truth.groupby("obj_id")["t"].max()
    entered: dict[tuple, dict] = {}                    # (ego, true object) -> {"t": time of entry, "done": reached the confident belief}
    counts = dict(ticks=0, objects=0, no_ego_state=0)
    start = time.perf_counter()
    for ego in run.egos:
        previous_in_range: set = set()
        pending: dict[str, float] = {}                 # true object -> time of entry, until it is confident or leaves the range
        for tick in run.replay(ego, period=model.period, hold=model.hold):
            counts["ticks"] += 1
            if tick.ego_state is None:
                counts["no_ego_state"] += 1
                continue
            truth = tick.truth
            tx, ty = truth["x"].to_numpy(dtype=float), truth["y"].to_numpy(dtype=float)
            names_true = truth["obj_id"].to_numpy(dtype=object)
            in_range_mask = np.hypot(tx - tick.ego_state["x"], ty - tick.ego_state["y"]) <= model.radius
            in_range = {name for name, inside in zip(names_true, in_range_mask) if inside}
            for name in in_range - previous_in_range:
                pending[name] = tick.t
                for way in WAYS:
                    collectors[way].entries += 1
                    entered[(way, ego, name, tick.t)] = {"done": False}
            for name in previous_in_range - in_range:
                pending.pop(name, None)
            previous_in_range = in_range

            aligned = align(tick)
            if not len(aligned):
                for way in WAYS:
                    collectors[way].in_range += len(in_range)
                continue
            counts["objects"] += len(aligned)
            groups = associate(aligned, model.association)
            evidence = build_evidence(aligned.objects, model.evidence)
            states_ci = fuse_states(aligned, groups, model.kinematics)
            states_kalman = fuse_states(aligned, groups, KinematicsModel("independent"))
            place = dict(zip(names_true, zip(tx, ty)))
            # the true object of each group and the distance to it depend on the members and the states only, not on the way
            chosen, agree = group_truth(states_ci, aligned.objects)
            true_xy = np.array([place.get(name, (np.nan, np.nan)) if name is not None else (np.nan, np.nan) for name in chosen], dtype=float)
            true_xy = true_xy.reshape(len(chosen), 2)
            distance_of = {}
            with np.errstate(invalid="ignore"):
                for label, states in (("ci", states_ci), ("kalman", states_kalman)):
                    distance_of[label] = np.hypot(states.state[:, 0] - true_xy[:, 0], states.state[:, 1] - true_xy[:, 1])
            for way in WAYS:
                if way == "B2":
                    bel = _distance_confidence(states_kalman.cov, model.epsilon)
                    pl = np.ones(len(bel))
                    distance = distance_of["kalman"]
                else:
                    fused = fuse(aligned.objects, groups, discount_evidence(evidence, aligned.objects, trust[way]), combination[way])
                    bel = fused.objects["bel_exists"].to_numpy(dtype=float)
                    pl = fused.objects["pl_exists"].to_numpy(dtype=float)
                    distance = distance_of["ci"]
                with np.errstate(invalid="ignore"):
                    hit = agree & (distance <= model.epsilon)
                c = collectors[way]
                c.add(bel, pl, hit)
                c.wrong += int((~agree).sum())
                c.se += float(np.nansum(distance[hit] ** 2))
                c.n_there += int(hit.sum())
                # awareness: the true objects in range known by a fused object that is really there with enough belief
                known_names = {chosen[k] for k in np.flatnonzero(hit & (bel >= model.known_belief))}
                c.in_range += len(in_range)
                c.known += len(in_range & known_names)
                # stale: the true object left the simulation more than left_for ago
                for k in np.flatnonzero(agree):
                    name = chosen[k]
                    if name in place:
                        continue
                    left = tick.t - float(last_seen.get(name, np.nan))
                    if np.isfinite(left) and left > model.left_for:
                        c.stale_sum += float(bel[k])
                        c.n_stale += 1
                ghost = ~hit
                c.ghost_sum += float(bel[ghost].sum())
                c.n_ghost += int(ghost.sum())
                # time to confident
                confident_names = {chosen[k] for k in np.flatnonzero(hit & (bel > model.confident_belief))}
                for name in list(pending):
                    if name in confident_names:
                        key = (way, ego, name, pending[name])
                        if key in entered and not entered[key]["done"]:
                            entered[key]["done"] = True
                            c.confident_times.append(tick.t - pending[name])
            for name in list(pending):                 # an entry is closed for every way once all five reached it, or when it leaves
                if all(entered.get((way, ego, name, pending[name]), {"done": True})["done"] for way in WAYS):
                    pending.pop(name)
    seconds = time.perf_counter() - start
    network = network_metrics(run)
    rows = []
    for way in WAYS:
        row = dict(run_id=run.info.get("run_id", ""), way=way, name=WAY_NAMES[way], hold=model.hold, ticks=counts["ticks"], objects=counts["objects"],
                   no_ego_state=counts["no_ego_state"], seconds=round(seconds, 1), **network, **collectors[way].row())
        rows.append(row)
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate an exported run: the metrics of the plan for the five ways to handle trust (B0 to B4).")
    ap.add_argument("run_dir", help="folder with observations.parquet and truth.parquet (written by aoc-export)")
    ap.add_argument("--hold", type=float, default=5.0, help="forget a received object older than this value in s (default 5; 0 keeps everything)")
    ap.add_argument("--q", action="append", default=[], metavar="CLASS=VALUE", help="motion value of the reliability curve for one class")
    ap.add_argument("--out", default=None, help="write the rows to this file (.parquet, .csv or .json)")
    ap.add_argument("--drop-unmatched", action="store_true", help="leave out the rows without a true object or without a time")
    args = ap.parse_args(argv)
    try:
        from ..fusion.discounting import _q_options
        model = EvaluationModel(hold=None if args.hold == 0 else args.hold, trust=TrustModel(q_by_class=_q_options(args.q)))
        run = load_run(args.run_dir, noise=NoiseModel(), missing="drop" if args.drop_unmatched else "raise")
        table = evaluate_run(run, model)
        if args.out:
            if args.out.endswith(".parquet"):
                table.to_parquet(args.out, index=False)
            elif args.out.endswith(".csv"):
                table.to_csv(args.out, index=False)
            elif args.out.endswith(".json"):
                table.to_json(args.out, orient="records", indent=1)
            else:
                raise ValueError("--out needs a .parquet, .csv or .json file")
    except (FileNotFoundError, ValueError, KeyError) as err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 2

    first = table.iloc[0]
    number = lambda value, digits=3: "none" if value is None or (isinstance(value, float) and np.isnan(value)) else ("%%.%df" % digits) % value
    percent = lambda value: "none" if value is None or (isinstance(value, float) and np.isnan(value)) else "%.1f%%" % (100.0 * value)
    print("B8 EVALUATION")
    print("  run:                 %s" % (first["run_id"] or os.path.abspath(args.run_dir)))
    print("  memory:              a received object is %s" % ("kept until the end of the run" if model.hold is None else "forgotten after %g s" % model.hold))
    print("  objects:             %d in %d ticks of %d vehicles, in %.1f s" % (first["objects"], first["ticks"], len(run.egos), first["seconds"]))
    print("  network:             channel busy ratio %s, delivery ratio %s, age at reception median %s s and 95th percentile %s s"
          % (number(first["cbr_mean"]), number(first["pdr_mean"]), number(first["age_median"]), number(first["age_p95"])))
    print("  true objects in range of a vehicle, summed over the ticks: %d; wrong groups: %d" % (first["objects_in_range"], first["wrong_groups"]))
    print("  %-4s %-36s %9s %7s %8s %9s %9s %8s %9s %9s %9s" % ("way", "", "ECE", "Brier", "coverage", "sharpness", "awareness", "RMSE", "ghost", "stale", "t_conf"))
    for _, row in table.iterrows():
        print("  %-4s %-36s %9s %7s %8s %9s %9s %8s %9s %9s %9s"
              % (row["way"], row["name"], number(row["calibration_error"]), number(row["brier"]), percent(row["coverage"]), number(row["sharpness"]),
                 percent(row["awareness_ratio"]), number(row["rmse"]), number(row["ghost_rate"]), number(row["stale_rate"]), number(row["time_to_confident"], 1)))
    print("  ECE and Brier: 0 is perfect. coverage: share of objects whose bin has belief <= share really there <= plausibility.")
    print("  ghost: mean belief of the fused objects that are not really there. stale: the same for objects that left more than 1 s ago (%d objects)."
          % first["stale_objects"])
    print("  t_conf: median time in s from the entry of a true object in the 200 m range to a belief above 0.9 (%s of the entries reach it for B3)."
          % percent(table.set_index("way").loc["B3", "confident_share"]))
    if args.out:
        print("  rows written to %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
