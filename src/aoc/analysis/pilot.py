"""The pilot of the occluded-VRU scenario through the whole pipeline (task C7), and figure F6.

Task C5 ran the scenario in Artery and checked that the occlusion event is in the logs. This module runs the
pipeline of B2 to B8 on every exported run of the pilot and reads off what the plan wants from it:

  - the metrics of the five ways (B0 to B4) of ``aoc.analysis.metrics`` on each run, one row per run and way;
  - the belief of the ego in the pedestrian over time, with V2X and with its own sensors only
    (``aoc.analysis.trace``), around the occlusion found by ``aoc.analysis.vru``;
  - figure F6 of the plan (the "money figure"): the band between belief and plausibility over time for the
    hidden pedestrian, with V2X and own sensors only, with the event markers (the pedestrian comes out, the
    ego loses it, the first CPM, the ego sees it again).

    aoc-pilot ROOT [--weather rain] [--out pilot.csv] [--traces traces.csv] [--figure f6.pdf] [--figure-run NAME]
              [--no-metrics]

The check of one run (PASS when all hold), on the proposed law B3:
  - the occlusion of C5 is there (the ego loses the pedestrian for 1.5 to 6 s);
  - during the occlusion the ego receives CPM rows about the pedestrian, and its belief with V2X stays at or
    above 0.9 on average over the occlusion;
  - at the end of the occlusion, its belief with V2X is at least 0.3 above the belief its own sensors alone
    would leave it (the last own report, aged): the band is visibly tighter with V2X than without, the
    acceptance line of F6 in the plan. (A 1.5 s occlusion of a pedestrian lowers the own belief to about 0.6,
    a 2.7 s one to 0.2; the rule is relative so that it holds for both.)

The event times (the ego loses and sees the pedestrian again, the first CPM) come from the raw logs through
``aoc.analysis.vru``; the traces come from the replay with the sensor tables of the weather. In the clear the
two agree; in rain the tables may take rows out, and the traces are the ones to read.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

from ..fusion.replay import load_run
from .metrics import EvaluationModel, evaluate_run
from .trace import both_views
from .vru import find_runs, read_vru_run

V2X_BELIEF = 0.9      # the belief with V2X must stay at or above this, on average over the occlusion
VISIBLE_GAIN = 0.3    # at the end of the occlusion, the belief with V2X must be this much above the own-sensors belief
MARGIN = 3.0          # seconds traced before the ego first sees (or loses) the pedestrian and after it sees it again


def run_name(folder: str) -> str:
    name = os.path.basename(folder.rstrip("/"))
    return os.path.basename(os.path.dirname(folder.rstrip("/"))) if name == "parquet" else name


def pilot_run(folder: str, weather: str | None = None, ego: str = "ego", model: EvaluationModel | None = None,
              metrics: bool = True) -> dict:
    """One run of the pilot: the timeline of C5, the traces of the ego about the pedestrian, the metrics, the checks."""
    model = model or EvaluationModel()
    timeline = read_vru_run(folder, ego=ego)
    run = load_run(folder, weather=weather)
    out = dict(folder=folder, name=run_name(folder), run_id=run.info.get("run_id", ""), seed=run.info.get("seed"),
               helpers=timeline.get("helpers"), weather=run.weather, timeline=timeline, checks={}, notes=[])
    out["detection"] = run.detection
    target = timeline.get("pedestrian")
    checks = out["checks"]
    checks["occlusion"] = bool(timeline["checks"].get("occlusion_gap")) and timeline.get("gap") is not None
    if target is None:
        out["notes"].append("no pedestrian in the truth")
        out["traces"] = pd.DataFrame()
        return finish(out)
    lost, seen = timeline.get("lost"), timeline.get("seen_again")
    first = timeline.get("ego_first")
    starts = [t for t in (first, lost) if t is not None]
    t0 = None if not starts else max(0.0, min(starts) - MARGIN)
    t1 = None if seen is None else seen + MARGIN
    start = time.perf_counter()
    traces = both_views(run, ego, target, model, ways=("B0", "B1", "B3"), t_start=t0, t_end=t1)
    traces.insert(0, "run", out["name"])
    out["traces"] = traces
    out["trace_seconds"] = round(time.perf_counter() - start, 1)
    if lost is not None and seen is not None:
        gap = traces[(traces["t"] > lost) & (traces["t"] < seen)]
        v2x = gap[(gap["view"] == "with V2X") & (gap["way"] == "B3")]
        own = gap[(gap["view"] == "own sensors") & (gap["way"] == "B3")]
        out["v2x_gap_mean"] = float(v2x["bel"].mean()) if len(v2x) else np.nan
        out["v2x_gap_min"] = float(v2x["bel"].min()) if len(v2x) else np.nan
        out["v2x_gap_end"] = float(v2x["bel"].iloc[-1]) if len(v2x) else np.nan
        out["own_gap_end"] = float(own["bel"].iloc[-1]) if len(own) else np.nan
        out["own_gap_min"] = float(own["bel"].min()) if len(own) else np.nan
        out["cpm_rows_in_gap"] = int(v2x["n_new"].sum())
        out["groups_max"] = int(v2x["groups"].max()) if len(v2x) else 0
        full = traces[(traces["view"] == "with V2X") & (traces["way"] == "B0")]
        out["b0_gap_mean"] = float(full[(full["t"] > lost) & (full["t"] < seen)]["bel"].mean()) if len(full) else np.nan
    else:
        out.update(v2x_gap_mean=np.nan, v2x_gap_min=np.nan, v2x_gap_end=np.nan, own_gap_end=np.nan, own_gap_min=np.nan, cpm_rows_in_gap=0,
                   groups_max=0, b0_gap_mean=np.nan)
    checks["cpm_in_gap"] = out["cpm_rows_in_gap"] > 0
    checks["v2x_belief_holds"] = bool(np.isfinite(out["v2x_gap_mean"]) and out["v2x_gap_mean"] >= V2X_BELIEF)
    own_end = out["own_gap_end"] if np.isfinite(out["own_gap_end"]) else 0.0          # nothing remembered: belief 0
    checks["band_tightens"] = bool(np.isfinite(out["v2x_gap_end"]) and out["v2x_gap_end"] - own_end >= VISIBLE_GAIN)
    if metrics:
        start = time.perf_counter()
        table = evaluate_run(run, model)
        table.insert(0, "run", out["name"])
        table["helpers"] = out["helpers"]
        table["weather"] = run.weather
        out["metrics"] = table
        out["metrics_seconds"] = round(time.perf_counter() - start, 1)
    else:
        out["metrics"] = pd.DataFrame()
    return finish(out)


def finish(out: dict) -> dict:
    out["ok"] = all(out["checks"].values())
    return out


def format_run(r: dict) -> str:
    tl = r["timeline"]
    lines = ["%s (helpers = %s, seed %s, weather %s): %s" % (r["name"], r["helpers"], r["seed"], r["weather"], "PASS" if r["ok"] else "FAIL")]
    if tl.get("gap") is not None:
        lines.append("  occlusion: the ego loses the pedestrian at %.1f s (%s m) and sees it again at %.1f s (%s m): %.1f s"
                     % (tl["lost"], fmt(tl.get("lost_distance")), tl["seen_again"], fmt(tl.get("seen_again_distance")), tl["gap"]))
        lines.append("  belief of the ego in the pedestrian during the occlusion (B3): with V2X mean %.3f, lowest %.3f, %.3f at the end, %d CPM rows"
                     " received; own sensors only: %.3f at the end (lowest %.3f); full trust (B0) with V2X: %.3f"
                     % (r["v2x_gap_mean"], r["v2x_gap_min"], r["v2x_gap_end"], r["cpm_rows_in_gap"], r["own_gap_end"], r["own_gap_min"], r["b0_gap_mean"]))
        if r.get("groups_max", 0) > 1:
            lines.append("  note: the association made up to %d fused objects for the pedestrian during the occlusion (the highest belief is reported)"
                         % r["groups_max"])
    else:
        lines.append("  no occlusion found in the ego's detections")
    if len(r["metrics"]):
        m = r["metrics"].set_index("way")
        lines.append("  metrics (whole run, %s s): ECE " % r.get("metrics_seconds", "?")
                     + ", ".join("%s %.3f" % (w, m.loc[w, "calibration_error"]) for w in m.index)
                     + "; Brier " + ", ".join("%s %.3f" % (w, m.loc[w, "brier"]) for w in m.index)
                     + "; awareness B3 %.1f%%" % (100.0 * m.loc["B3", "awareness_ratio"]))
    failed = [k for k, v in r["checks"].items() if not v]
    if failed:
        lines.append("  failed checks: " + ", ".join(failed))
    for note in r["notes"]:
        lines.append("  note: " + note)
    return "\n".join(lines)


def fmt(v) -> str:
    return "?" if v is None else "%.0f" % v


def draw(r: dict, path: str) -> None:
    """Figure F6: the belief of the ego in the hidden pedestrian over time, with V2X and with its own sensors only."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"], "mathtext.fontset": "stix",
                         "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7,
                         "axes.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
    traces, tl = r["traces"], r["timeline"]
    if not len(traces):
        raise ValueError("nothing to draw: no trace")
    fig, (ax, ev) = plt.subplots(2, 1, figsize=(3.45, 3.0), sharex=True, gridspec_kw={"height_ratios": [3.4, 1.0], "hspace": 0.08})
    v2x = traces[(traces["view"] == "with V2X") & (traces["way"] == "B3")]
    own = traces[(traces["view"] == "own sensors") & (traces["way"] == "B3")]
    full = traces[(traces["view"] == "with V2X") & (traces["way"] == "B0")]
    ax.fill_between(v2x["t"], v2x["bel"], v2x["pl"], color="#1f77b4", alpha=0.18, lw=0, label="with V2X: belief to plausibility (AoC)")
    ax.plot(v2x["t"], v2x["bel"], color="#1f77b4", lw=1.2, label="with V2X: belief (AoC)")
    ax.plot(own["t"], own["bel"], color="#d62728", lw=1.1, ls="--", label="own sensors only: belief (AoC)")
    ax.plot(full["t"], full["bel"], color="#7f7f7f", lw=0.8, ls=":", label="with V2X: full trust (B0)")
    t_min, t_max = float(traces["t"].min()), float(traces["t"].max())
    events = [("ego sees it", tl.get("ego_first"), "#2ca02c", "right"), ("ego loses it", tl.get("lost"), "#d62728", "right"),
              ("first CPM", tl.get("first_cpm_in_gap"), "#1f77b4", "left"), ("ego sees it again", tl.get("seen_again"), "#2ca02c", "left")]
    for label, t, color, side in events:
        if t is None or not (t_min <= t <= t_max):
            continue
        for a in (ax, ev):
            a.axvline(t, color=color, lw=0.6, alpha=0.7)
        ax.text(t + (0.06 if side == "left" else -0.06), 0.5, label, color=color, fontsize=6.5, rotation=90, va="center", ha=side,
                bbox=dict(facecolor="white", edgecolor="none", pad=0.6, alpha=0.8))
    ax.set_ylim(-0.02, 1.05)
    ax.set_ylabel("belief that the pedestrian exists")
    ax.axhline(0.9, color="k", lw=0.4, ls=(0, (2, 2)))
    ax.text(t_max, 0.905, "target 0.9 ", fontsize=6.5, va="bottom", ha="right")
    # a CPM report that ages before the ego sees the pedestrian itself: say what the early part of the curve is
    first = tl.get("ego_first")
    early = v2x[(v2x["t"] < (first if first is not None else np.inf)) & v2x["bel"].notna() & (v2x["n_known"] > 0)]
    if len(early) >= 5:
        mid = early.iloc[len(early) // 2]
        ax.annotate("an old CPM report ages", xy=(float(mid["t"]), float(mid["bel"])), xytext=(float(mid["t"]), min(0.98, float(mid["bel"]) + 0.3)),
                    fontsize=6.5, color="#1f77b4", ha="center", arrowprops=dict(arrowstyle="-", color="#1f77b4", lw=0.5))
    ax.legend(loc="lower center", frameon=False, ncol=2, bbox_to_anchor=(0.5, 1.0), handlelength=1.8, columnspacing=1.0)
    # the event strip: the ego's own detections, and the CPM rows about the pedestrian it receives
    own_t = v2x.loc[v2x["n_local"] > 0, "t"]
    new_t = v2x.loc[v2x["n_new"] > 0, "t"]
    ev.vlines(own_t, 0.55, 0.95, color="#d62728", lw=0.8)
    ev.vlines(new_t, 0.05, 0.45, color="#1f77b4", lw=0.8)
    ev.set_yticks([0.25, 0.75])
    ev.set_yticklabels(["CPM received", "own detection"], fontsize=6.5)
    ev.set_ylim(0, 1)
    ev.set_xlim(t_min, t_max)
    ev.set_xlabel("time (s)")
    for side in ("top", "right"):
        ev.spines[side].set_visible(False)
    fig.savefig(path, bbox_inches="tight", dpi=200)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The pilot of the occluded-VRU scenario through the whole pipeline: "
                                 "metrics, the belief of the ego in the pedestrian, figure F6.")
    ap.add_argument("root", help="folder that holds the exported runs (each with an export_summary.json)")
    ap.add_argument("--weather", default=None, help="weather of the replay (default: the one of the run, else clear)")
    ap.add_argument("--ego", default="ego", help="SUMO id of the ego car (default: ego)")
    ap.add_argument("--hold", type=float, default=5.0, help="forget a received object older than this value in s (default 5)")
    ap.add_argument("--out", default=None, help="write the metrics (one row per run and way) to this CSV file")
    ap.add_argument("--traces", default=None, help="write the belief traces (one row per run, view, way and tick) to this CSV file")
    ap.add_argument("--figure", default=None, help="draw figure F6 to this PDF (or PNG) file")
    ap.add_argument("--figure-run", default=None, help="name of the run to draw (default: the first run with 2 helpers, else the first run)")
    ap.add_argument("--no-metrics", action="store_true", help="skip the metrics of the whole run (fast: the traces and the checks only)")
    args = ap.parse_args(argv)
    folders = find_runs(args.root)
    if not folders:
        print("aoc-pilot: no exported run under %s" % args.root)
        return 2
    model = EvaluationModel(hold=None if args.hold == 0 else args.hold)
    results = []
    for folder in folders:
        r = pilot_run(folder, weather=args.weather, ego=args.ego, model=model, metrics=not args.no_metrics)
        print(format_run(r), flush=True)
        results.append(r)
    ok = all(r["ok"] for r in results)
    print("runs: %d, passed: %d" % (len(results), sum(1 for r in results if r["ok"])))
    if args.out and not args.no_metrics:
        pd.concat([r["metrics"] for r in results if len(r["metrics"])], ignore_index=True).to_csv(args.out, index=False)
        print("metrics: %s" % args.out)
    if args.traces:
        pd.concat([r["traces"] for r in results if len(r["traces"])], ignore_index=True).to_csv(args.traces, index=False)
        print("traces: %s" % args.traces)
    if args.figure:
        chosen = None
        if args.figure_run:
            chosen = next((r for r in results if r["name"] == args.figure_run), None)
            if chosen is None:
                print("aoc-pilot: no run named %s" % args.figure_run)
        else:
            chosen = next((r for r in results if r["helpers"] == 2 and len(r["traces"])), None) or next((r for r in results if len(r["traces"])), None)
        if chosen is not None:
            try:
                draw(chosen, args.figure)
                print("figure: %s (%s)" % (args.figure, chosen["name"]))
            except ImportError:
                print("figure: matplotlib is not installed, nothing drawn")
    print("result: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
