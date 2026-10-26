"""The occluded-VRU runs (task C5, scenario E1): read the exported runs and check that the event is in the logs.

The scenario (``artery-ext/vru``) is a signalized intersection with buildings. A pedestrian comes out of an
alley and crosses the south leg at a zebra crossing. A truck stopped on the right lane before the crossing
hides the pedestrian from the ego car, which comes from the south on the left lane. One to three helper cars
see the pedestrian and send it in their CPMs. The plan asks for one thing of this task: a pilot of two seeds
runs end to end and the occlusion event is visible in the logs. This module reads the exported runs and
tells, for each one, the timeline of the event and whether it holds:

    aoc-vru ROOT                     # every run folder under ROOT with an export_summary.json
    aoc-vru ROOT --pilot             # also checks that the six runs of the pilot (1, 2, 3 helpers x 2 seeds) are there

The timeline of one run, all read from the logs (no geometry of the scenario is assumed here):
  - the pedestrian: the one object of class pedestrian in the truth;
  - it appears: the first time any car sees it with its own sensors (before, it is in the alley);
  - the ego loses it: the first gap of at least 1.5 s in the ego's own detections of the pedestrian (the
    occlusion comes before the ego passes the pedestrian; when there is no such gap, the longest one is reported);
  - which cars see the pedestrian with their own sensors during that gap, and how often (the truck does too);
  - the CPMs: how many CPM rows about the pedestrian the ego received during the gap, from whom, and the
    age of the first one.

The checks of one run (PASS when all hold):
  - one pedestrian, seen by the ego's own sensors at some time, then lost for at least 1.5 s and at most 6 s
    (the occlusion), with the pedestrian within 100 m when lost and within 40 m when seen again;
  - during the gap, at least one of the configured helpers sees the pedestrian at four steps out of five;
  - during the gap, the ego receives at least one CPM row about the pedestrian; the class of these rows is
    pedestrian;
  - the senders of CPMs about the pedestrian over the run are configured helpers only (the truck and the other
    cars run no CP service), and a helper whose CP service is off sends no CPM at all.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HELPERS = ("helper_a", "helper_b", "helper_c")
PILOT_HELPERS = (1, 2, 3)
PILOT_SEEDS = 2
MIN_GAP = 1.5        # s: a shorter gap is not an occlusion, a sensor blink at most
MAX_GAP = 6.0        # s: a longer gap means the ego never had the pedestrian at the crossing
LOST_WITHIN = 100.0  # m: distance ego-pedestrian when the ego loses it (the lidar range)
SEEN_WITHIN = 40.0   # m: distance when the ego sees it again (it comes out from behind the truck)
HELPER_SHARE = 0.8   # share of the steps of the gap at which a helper must see the pedestrian


def find_runs(root: str | os.PathLike) -> list[str]:
    """The folders under ``root`` (itself included) that hold an export_summary.json, in order of name.
    (The same as in ``aoc.analysis.link``; copied so that this file runs alone on the workstation.)"""
    root = os.fspath(root)
    found = []
    for folder, _, files in os.walk(root):
        if "export_summary.json" in files:
            found.append(folder)
    return sorted(found)


def intervals(times: np.ndarray, step: float = 0.1) -> list[tuple[float, float]]:
    """The runs of consecutive times (at most ``step`` apart, with a margin) as (first, last) pairs."""
    times = np.unique(np.asarray(times, dtype=float))
    if times.size == 0:
        return []
    cuts = np.flatnonzero(np.diff(times) > 1.5 * step)
    starts = np.concatenate(([0], cuts + 1))
    ends = np.concatenate((cuts, [times.size - 1]))
    return [(float(times[a]), float(times[b])) for a, b in zip(starts, ends)]


def longest_gap(times: np.ndarray, step: float = 0.1) -> tuple[float, float] | None:
    """The longest hole between two runs of detections: (last time before, first time after), or None."""
    runs = intervals(times, step)
    if len(runs) < 2:
        return None
    gaps = [(runs[i + 1][0] - runs[i][1], runs[i][1], runs[i + 1][0]) for i in range(len(runs) - 1)]
    _, before, after = max(gaps)
    return before, after


def first_gap(times: np.ndarray, min_gap: float, step: float = 0.1) -> tuple[float, float] | None:
    """The first hole of at least ``min_gap`` between two runs of detections, or the longest hole when none is
    that long: (last time before, first time after). None without a hole."""
    runs = intervals(times, step)
    for i in range(len(runs) - 1):
        if runs[i + 1][0] - runs[i][1] >= min_gap:
            return runs[i][1], runs[i + 1][0]
    return longest_gap(times, step)


def distance_at(truth: pd.DataFrame, a: str, b: str, t: float) -> float | None:
    """Distance in m between the true objects ``a`` and ``b`` at the step ``t`` (the nearest step logged)."""
    out = []
    for who in (a, b):
        rows = truth[truth["obj_id"] == who]
        if rows.empty:
            return None
        k = int(np.argmin(np.abs(rows["t"].to_numpy(float) - t)))
        out.append((float(rows["x"].iloc[k]), float(rows["y"].iloc[k])))
    return float(np.hypot(out[0][0] - out[1][0], out[0][1] - out[1][1]))


def read_vru_run(folder: str | os.PathLike, ego: str = "ego") -> dict:
    """The timeline of the occlusion event of one exported run, and the checks. Keys: see ``format_run``."""
    folder = os.fspath(folder)
    with open(os.path.join(folder, "export_summary.json"), encoding="utf-8") as fh:
        info = json.load(fh)
    variables = info.get("variables") or {}
    helpers = variables.get("helpers")
    out = dict(folder=folder, run_id=info.get("run_id", ""), config=info.get("config", ""), seed=info.get("seed"),
               helpers=None if helpers is None else int(helpers), ego=ego, checks={}, notes=[])
    truth = pd.read_parquet(os.path.join(folder, "truth.parquet"))
    obs = pd.read_parquet(os.path.join(folder, "observations.parquet"))
    tx_path = os.path.join(folder, "cpm_tx.parquet")
    tx = pd.read_parquet(tx_path) if os.path.isfile(tx_path) else None
    checks = out["checks"]

    peds = sorted(truth.loc[truth["class"] == "pedestrian", "obj_id"].dropna().unique().tolist())
    checks["one_pedestrian"] = len(peds) == 1
    if not peds:
        out["notes"].append("no pedestrian in the truth")
        return finish(out)
    ped = peds[0]
    out["pedestrian"] = ped
    ped_truth = truth[truth["obj_id"] == ped]
    out["ped_from"], out["ped_to"] = float(ped_truth["t"].min()), float(ped_truth["t"].max())

    about = obs[obs["gt_match_id"] == ped]
    local = about[about["src"] != "v2x"]
    v2x = about[about["src"] == "v2x"]
    out["appears"] = None if local.empty else float(local["t_sim"].min())
    out["first_seen_by"] = None if local.empty else str(local.sort_values("t_sim")["rx_station"].iloc[0])

    # the ego's own view of the pedestrian
    ego_local = local[local["rx_station"] == ego]
    ego_times = ego_local["t_sim"].to_numpy(float)
    out["ego_first"] = None if ego_local.empty else float(ego_times.min())
    out["ego_first_sensors"] = sorted(ego_local.loc[ego_local["t_sim"] == out["ego_first"], "sensor"].astype(str).unique().tolist()) if not ego_local.empty else []
    out["ego_steps"] = int(np.unique(ego_times).size)
    checks["ego_sees_pedestrian"] = not ego_local.empty
    gap = first_gap(ego_times, MIN_GAP)
    out["lost"], out["seen_again"], out["gap"] = None, None, None
    if gap is not None:
        out["lost"], out["seen_again"] = gap
        out["gap"] = round(gap[1] - gap[0], 3)
        out["lost_distance"] = distance_at(truth, ego, ped, gap[0])
        out["seen_again_distance"] = distance_at(truth, ego, ped, gap[1])
        out["seen_again_sensors"] = sorted(ego_local.loc[ego_local["t_sim"] == gap[1], "sensor"].astype(str).unique().tolist())
    checks["occlusion_gap"] = gap is not None and MIN_GAP <= out["gap"] <= MAX_GAP
    checks["lost_within_range"] = gap is not None and out["lost_distance"] is not None and out["lost_distance"] <= LOST_WITHIN
    checks["seen_again_close"] = gap is not None and out["seen_again_distance"] is not None and out["seen_again_distance"] <= SEEN_WITHIN

    # the cars that see the pedestrian during the gap, and the CPMs the ego receives then
    configured = HELPERS if out["helpers"] is None else HELPERS[:out["helpers"]]
    out["configured_helpers"] = list(configured)
    out["helpers_seeing"] = {}
    out["cpm_in_gap"] = {}
    out["first_cpm_in_gap"] = None
    out["first_cpm_age"] = None
    out["cpm_classes"] = []
    if gap is not None:
        t0, t1 = gap
        steps = np.round(np.arange(t0 + 0.1, t1 - 0.05, 0.1), 1)
        in_gap = local[(local["t_sim"] > t0) & (local["t_sim"] < t1) & (local["rx_station"] != ego)]
        for who, rows in in_gap.groupby("rx_station"):
            seen = np.unique(np.round(rows["t_sim"].to_numpy(float), 1))
            out["helpers_seeing"][str(who)] = (int(np.isin(steps, seen).sum()), int(steps.size))
        rx = v2x[(v2x["rx_station"] == ego) & (v2x["t_rx"] > t0) & (v2x["t_rx"] < t1)]
        out["cpm_in_gap"] = {str(k): int(v) for k, v in rx["tx_station"].value_counts().items()}
        if not rx.empty:
            first = rx.sort_values("t_rx").iloc[0]
            out["first_cpm_in_gap"] = float(first["t_rx"])
            out["first_cpm_age"] = float(first["age"])
            out["first_cpm_from"] = str(first["tx_station"])
            out["cpm_classes"] = sorted(rx["class"].astype(str).unique().tolist())
    checks["helper_sees_in_gap"] = any(n >= HELPER_SHARE * total for who, (n, total) in out["helpers_seeing"].items()
                                       if total and who in configured)
    checks["cpm_received_in_gap"] = bool(out["cpm_in_gap"])
    checks["cpm_class_pedestrian"] = not out["cpm_in_gap"] or out["cpm_classes"] == ["pedestrian"]

    # who sent CPMs about the pedestrian over the run, and the helpers whose CP service is off
    senders = sorted(v2x["tx_station"].dropna().astype(str).unique().tolist())
    out["senders"] = senders
    out["other_senders"] = [s for s in senders if s not in configured]
    checks["senders_are_helpers"] = not out["other_senders"]
    if out["helpers"] is not None:
        off = HELPERS[out["helpers"]:]
        sent_by_off = [] if tx is None else sorted(tx.loc[tx["tx_id"].isin(off), "tx_id"].astype(str).unique().tolist())
        out["sent_by_off"] = sent_by_off
        checks["helpers_as_configured"] = not sent_by_off
    else:
        out["notes"].append("the run has no 'helpers' variable: the three helpers are taken as configured")
        checks["helpers_as_configured"] = True
    return finish(out)


def finish(out: dict) -> dict:
    out["ok"] = all(out["checks"].values())
    return out


def format_run(r: dict) -> str:
    """The timeline of one run as a few lines."""
    name = os.path.basename(r["folder"].rstrip("/"))
    if name == "parquet":
        name = os.path.basename(os.path.dirname(r["folder"].rstrip("/")))
    head = "%s (helpers = %s, seed %s): %s" % (name, r["helpers"], r["seed"], "PASS" if r["ok"] else "FAIL")
    lines = [head]
    if "pedestrian" not in r:
        lines.append("  no pedestrian in the truth")
        return "\n".join(lines)
    lines.append("  pedestrian %s: in the truth from %.1f to %.1f s; first seen by a car at %s (%s)"
                 % (r["pedestrian"], r["ped_from"], r["ped_to"],
                    "never" if r["appears"] is None else "%.1f s" % r["appears"], r["first_seen_by"] or "-"))
    if r["ego_first"] is None:
        lines.append("  %s: never sees it with its own sensors" % r["ego"])
    else:
        lines.append("  %s: sees it at %.1f s (%s), %d steps in all" % (r["ego"], r["ego_first"], ", ".join(r["ego_first_sensors"]), r["ego_steps"]))
        if r["gap"] is None:
            lines.append("  %s: no gap in its own detections" % r["ego"])
        else:
            lines.append("  %s: loses it at %.1f s (%s m), sees it again at %.1f s (%s m, %s): gap %.1f s"
                         % (r["ego"], r["lost"], fmt_m(r.get("lost_distance")), r["seen_again"], fmt_m(r.get("seen_again_distance")),
                            ", ".join(r.get("seen_again_sensors", [])), r["gap"]))
            if r["helpers_seeing"]:
                seeing = ", ".join("%s (%d of %d steps)" % (k, n, tot) for k, (n, tot) in sorted(r["helpers_seeing"].items()))
                lines.append("  cars that see it during the gap: " + seeing)
            else:
                lines.append("  no other car sees it during the gap")
            if r["cpm_in_gap"]:
                lines.append("  CPM rows about it received by %s during the gap: %s; the first at %.1f s from %s, age %.3f s; class %s"
                             % (r["ego"], ", ".join("%d from %s" % (n, k) for k, n in sorted(r["cpm_in_gap"].items())),
                                r["first_cpm_in_gap"], r["first_cpm_from"], r["first_cpm_age"], ", ".join(r["cpm_classes"]) or "-"))
            else:
                lines.append("  no CPM row about it received by %s during the gap" % r["ego"])
    lines.append("  senders of CPMs about it over the run: %s (configured helpers: %s)"
                 % (", ".join(r["senders"]) or "none", ", ".join(r.get("configured_helpers", []))))
    if r.get("other_senders"):
        lines.append("  senders that are not configured helpers: " + ", ".join(r["other_senders"]))
    if r.get("sent_by_off"):
        lines.append("  helpers with the CP service off that sent CPMs: " + ", ".join(r["sent_by_off"]))
    failed = [k for k, v in r["checks"].items() if not v]
    if failed:
        lines.append("  failed checks: " + ", ".join(failed))
    for note in r["notes"]:
        lines.append("  note: " + note)
    return "\n".join(lines)


def fmt_m(v) -> str:
    return "?" if v is None else "%.0f" % v


def check_pilot(runs: list[dict]) -> tuple[bool, list[str]]:
    """The six runs of the pilot: helpers 1, 2, 3 with two seeds each, once each."""
    problems = []
    for h in PILOT_HELPERS:
        seeds = sorted(set(r["seed"] for r in runs if r["helpers"] == h))
        if len(seeds) < PILOT_SEEDS:
            problems.append("helpers = %d: %d seed(s) instead of %d" % (h, len(seeds), PILOT_SEEDS))
        n = sum(1 for r in runs if r["helpers"] == h)
        if n != len(seeds):
            problems.append("helpers = %d: %d runs for %d seeds (a seed twice)" % (h, n, len(seeds)))
    return not problems, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the occluded-VRU runs of C5: the timeline of the event in the logs of each run.")
    ap.add_argument("root", help="folder that holds the exported runs (each with an export_summary.json)")
    ap.add_argument("--pilot", action="store_true", help="also check that the six runs of the pilot are there (1, 2, 3 helpers x 2 seeds)")
    ap.add_argument("--ego", default="ego", help="SUMO id of the ego car (default: ego)")
    ap.add_argument("--out", default=None, help="write one row per run to this CSV file")
    args = ap.parse_args(argv)
    folders = find_runs(args.root)
    if not folders:
        print("aoc-vru: no exported run under %s" % args.root)
        return 2
    runs = [read_vru_run(f, ego=args.ego) for f in folders]
    for r in runs:
        print(format_run(r))
    ok = all(r["ok"] for r in runs)
    print("runs: %d, passed: %d" % (len(runs), sum(1 for r in runs if r["ok"])))
    if args.pilot:
        pilot_ok, problems = check_pilot(runs)
        for p in problems:
            print("pilot: " + p)
        print("pilot: %s" % ("the six runs are there" if pilot_ok else "incomplete"))
        ok = ok and pilot_ok
    if args.out:
        rows = []
        for r in runs:
            rows.append({"run_id": r["run_id"], "config": r["config"], "seed": r["seed"], "helpers": r["helpers"], "ok": r["ok"],
                         "pedestrian": r.get("pedestrian"), "appears": r.get("appears"), "ego_first": r.get("ego_first"),
                         "lost": r.get("lost"), "seen_again": r.get("seen_again"), "gap": r.get("gap"),
                         "lost_distance": r.get("lost_distance"), "seen_again_distance": r.get("seen_again_distance"),
                         "helpers_seeing": " ".join(sorted(r.get("helpers_seeing", {}))),
                         "cpm_rows_in_gap": sum(r.get("cpm_in_gap", {}).values()), "first_cpm_age": r.get("first_cpm_age"),
                         "senders": " ".join(r.get("senders", [])), "failed": " ".join(k for k, v in r["checks"].items() if not v)})
        pd.DataFrame(rows).to_csv(args.out, index=False)
        print("table: %s" % args.out)
    print("result: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
