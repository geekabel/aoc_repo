"""The belief of one vehicle in one true object over time (task C7, figure F6 of the plan).

The pipeline of B2 to B7 gives, at every tick, one fused object per real object the vehicle knows, with a belief
and a plausibility that the object exists. This module follows one true object (the pedestrian of the
occluded-VRU scenario) through the ticks of one vehicle (the ego) and writes down, tick by tick:

    bel, pl        the belief and the plausibility of the fused object that stands for the true object
                   (the one with the highest belief when the association made two), NaN when the vehicle knows
                   nothing about it
    n_local        sensor rows of the vehicle about the object at this tick
    n_new          CPM rows about the object received since the last tick
    n_known        CPM objects about it in the memory of the vehicle
    groups         fused objects that stand for the true object (more than one: the association split it)

Two views of the same run are compared:

    with V2X       the run as it is: the vehicle fuses its sensors and the CPMs it received. The pipeline keeps
                   a memory of the CPMs (a received object is forgotten after ``hold`` seconds) and none of the
                   own sensors: when they miss the object and no CPM is there, the belief is NaN (nothing known).
    own sensors    the CPM rows are taken out before the replay. When the vehicle's own sensors miss the object,
                   the trace remembers the last own report and ages it: the rows of that tick are fused as the
                   pipeline fused them then, and the result is discounted once by the age with the law of the
                   way (the proposed law lets the belief fall along the curve of the note; full trust and the
                   cutoff keep it, the cutoff being a rule for received objects). The memory is forgotten after
                   ``hold`` seconds, like the one of the CPMs. This memory is a device of the trace, not a step of the pipeline, which keeps no
                   track of its own detections: it answers "what would the vehicle believe of its last own report,
                   read on the curve of the note". This is the "local-only" curve of F6.

The plausibility is 1 as long as no source speaks against the object; the band between belief and plausibility
is then 1 - bel, and "the band tightens" means the belief rises.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from ..fusion.alignment import align
from ..fusion.association import associate
from ..fusion.bba import build_evidence
from ..fusion.combination import fuse, group_truth
from ..fusion.discounting import discount_evidence, reliability
from ..fusion.kinematics import fuse_states
from ..fusion.replay import Run
from .metrics import WAYS, EvaluationModel

COLUMNS = ["t", "way", "bel", "pl", "n_local", "n_new", "n_known", "groups", "age_last_local"]


def without_v2x(run: Run) -> Run:
    """The same run without the CPM rows: what the vehicles know from their own sensors."""
    is_v2x = (run.obs["src"] == "v2x").to_numpy(dtype=bool, na_value=False)
    return replace(run, obs=run.obs[~is_v2x])


def _trust_of(model: EvaluationModel, run: Run) -> dict:
    base = model.trust.for_run(run)
    return {"B0": replace(base, law="full_trust"), "B1": replace(base, law="cutoff"), "B3": base, "B4": base}


def _combination_of(model: EvaluationModel) -> dict:
    return {"B0": replace(model.combination, cpm_rule="conjunctive"), "B1": replace(model.combination, cpm_rule="conjunctive"),
            "B3": model.combination, "B4": replace(model.combination, cpm_rule="conjunctive")}


def belief_trace(run: Run, ego: str, target: str, model: EvaluationModel | None = None, ways: tuple = ("B0", "B1", "B3"),
                 local_only: bool = False, t_start: float | None = None, t_end: float | None = None) -> pd.DataFrame:
    """The belief of ``ego`` in the true object ``target`` at every tick, for the given ways (B2 is not a belief).

    local_only: the CPM rows are taken out, and when the ego's sensors miss the object, the last own report is
    remembered and aged (see the module docstring). The replay starts at the first tick of the ego whatever
    ``t_start``, so that the memory is the same whatever the window; only the ticks of the window are returned.
    The result has the columns ``COLUMNS``.
    """
    model = model or EvaluationModel()
    unknown = [w for w in ways if w not in WAYS or w == "B2"]
    if unknown:
        raise ValueError("the ways must be among B0, B1, B3, B4 (not %s)" % ", ".join(unknown))
    source = without_v2x(run) if local_only else run
    trust = _trust_of(model, source)
    combination = _combination_of(model)
    rows = []
    last_local: pd.DataFrame | None = None            # the last own rows about the target, for the local-only memory
    for tick in source.replay(ego, period=model.period, hold=model.hold, t_end=t_end):
        if tick.ego_state is None:
            continue
        local_about = tick.local[(tick.local["gt_match_id"] == target).to_numpy(dtype=bool, na_value=False)]
        n_local = int(len(local_about))
        if n_local:
            last_local = local_about.copy()
        if t_start is not None and tick.t < t_start - 1e-9:
            continue
        n_new = int((tick.v2x_new["gt_match_id"] == target).sum())
        n_known = int((tick.v2x["gt_match_id"] == target).sum())
        age_last = np.nan if last_local is None else float(tick.t - float(last_local["t_meas"].max()))
        found: dict = {}
        aligned = align(tick) if len(tick.local) or len(tick.v2x) else None
        if aligned is not None and len(aligned):
            groups = associate(aligned, model.association)
            evidence = build_evidence(aligned.objects, model.evidence)
            states = fuse_states(aligned, groups, model.kinematics)
            chosen, agree = group_truth(states, aligned.objects)
            mine = [k for k, name in enumerate(chosen) if name == target and agree[k]]
            if mine:
                for way in ways:
                    fused = fuse(aligned.objects, groups, discount_evidence(evidence, aligned.objects, trust[way]), combination[way])
                    bel = fused.objects["bel_exists"].to_numpy(dtype=float)[mine]
                    pl = fused.objects["pl_exists"].to_numpy(dtype=float)[mine]
                    best = int(np.argmax(bel))
                    found[way] = (float(bel[best]), float(pl[best]), len(mine))
        remembered = local_only and last_local is not None and (model.hold is None or age_last <= model.hold + 1e-9)
        for way in ways:
            if way in found:
                bel, pl, n_groups = found[way]
            elif remembered:
                bel, pl = remembered_belief(last_local, tick.t, trust[way], combination[way], model)
                n_groups = 0
            else:
                bel, pl, n_groups = np.nan, np.nan, 0
            rows.append(dict(t=tick.t, way=way, bel=bel, pl=pl, n_local=n_local, n_new=n_new, n_known=n_known, groups=n_groups, age_last_local=age_last))
    return pd.DataFrame(rows, columns=COLUMNS)


def remembered_belief(last_local: pd.DataFrame, t: float, trust, combination, model: EvaluationModel) -> tuple[float, float]:
    """The belief and the plausibility of the last own rows about an object, aged to time ``t``.

    The rows are fused as the pipeline fused them at the detection (their evidence, discounted at the age they had
    then, combined with the rule for the own sensors), and the result is discounted once by the reliability of the
    age they have now: the sensors saw one object at one time, and it is that one object that moved since, so the
    age counts once and not once per sensor (the three sensors of a car are not three independent witnesses of
    where the object is now)."""
    evidence = build_evidence(last_local, model.evidence)
    trusted = discount_evidence(evidence, last_local, trust)
    fused = fuse(last_local, np.zeros(len(last_local), dtype=int), trusted, combination)
    bel0, pl0 = float(fused.objects["bel_exists"].iloc[0]), float(fused.objects["pl_exists"].iloc[0])
    aged = last_local.assign(age_now=t - last_local["t_meas"].to_numpy(dtype=float))
    then = reliability(last_local, trust)
    now = reliability(aged, trust)
    with np.errstate(divide="ignore", invalid="ignore"):
        factor = float(np.nanmax(np.where(then > 0, now / then, 0.0)))
    return bel0 * min(1.0, factor), pl0


def both_views(run: Run, ego: str, target: str, model: EvaluationModel | None = None, ways: tuple = ("B0", "B1", "B3"),
               t_start: float | None = None, t_end: float | None = None) -> pd.DataFrame:
    """The trace with V2X and the trace with the own sensors only, in one table with a column ``view``."""
    with_v2x = belief_trace(run, ego, target, model, ways, local_only=False, t_start=t_start, t_end=t_end).assign(view="with V2X")
    own = belief_trace(run, ego, target, model, ways, local_only=True, t_start=t_start, t_end=t_end).assign(view="own sensors")
    return pd.concat([with_v2x, own], ignore_index=True)
