"""The figures of the core grid (task D5): F2, F5, F7 and F8, drawn from the results database (``results/``).

  F2  the reliability curve alpha(age) per class: the curve of the note (the assumed q), the fitted curve and the
      measured hit rate of the calibration runs (``results/d3/qfit_curves.csv``), with the half-life markers
  F5  the reliability diagram of the belief that an object exists, one curve per way (``results/d4/reliability.csv``)
  F7  the awareness ratio and the calibration error against the penetration, one line per way with its confidence band
      over the evaluation runs of the level (``results/d4/runs.csv``)
  F8  the stale-confidence rate against the channel busy ratio, one marker per cell and way (``results/d4/cells.csv``)
  F9  the control frontier (task E3): the time above the target against the load, one marker per condition and rule
      of the control grid with the Pareto frontier of each load level, and the paired effect against ETSI with the
      region of H4 (``results/e3/cells.csv`` and ``cell_pairs.csv``, written by ``aoc-control``)

    aoc-grid-figures [--results results] [--out figures] [--only f05]

Each figure is one PDF (vector) and prints the numbers quoted in the text. The style is the one of F3, F4 and F6. A
table without the rows a figure needs raises ``NoData`` (the command says so and goes on).
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

from aoc.analysis import grid
from aoc.analysis.database import mean_ci
from aoc.analysis.metrics import WAYS

COLORS = {"B0": "#7f7f7f", "B1": "#ff7f0e", "B2": "#2ca02c", "B3": "#1f77b4", "B4": "#9467bd"}
MARKERS = {"B0": "s", "B1": "^", "B2": "D", "B3": "o", "B4": "v"}
SHORT = {"B0": "B0 full trust", "B1": "B1 cutoff 1 s", "B2": "B2 Bayesian", "B3": "B3 AoC", "B4": "B4 Dempster rule"}
CLASS_LABELS = {"passenger_car": "car", "heavy_truck": "truck", "pedestrian": "pedestrian", "cyclist": "cyclist"}
PEN_FILL = {10: "white", 25: "#d9d9d9", 50: "#8c8c8c", 100: "black"}                   # the face of a marker in F8: the penetration of the cell
RULE_COLORS = {"etsi": "#7f7f7f", "etsi_abs": "#17becf", "aoc": "#1f77b4", "aoc2": "#2ca02c", "aocdev": "#ff7f0e"}     # F9
RULE_LABELS = {"etsi": "ETSI (Artery)", "etsi_abs": "ETSI, absolute positions", "aoc": "AoC, n = 1", "aoc2": "AoC, n = 2", "aocdev": "AoC + deviation"}
LOAD_MARKERS = {0: "o", 12: "s", 36: "^"}                                                 # the marker in F9: the load stations of the cell


class NoData(ValueError):
    """The tables are there but hold no row the figure can draw."""


def _style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"], "mathtext.fontset": "stix",
                         "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7,
                         "axes.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
    return plt


def f02_alpha(results: str, path: str) -> dict:
    """F2: alpha(age) per class, the assumed curve, the fitted curve and the measured points, with the half-lives."""
    curves = pd.read_csv(os.path.join(results, "d3", "qfit_curves.csv"))
    table = pd.read_csv(os.path.join(results, "d3", "qfit.csv")).set_index("cls")
    plt = _style()
    classes = [c for c in ("passenger_car", "heavy_truck", "pedestrian") if c in set(curves["cls"])] or sorted(curves["cls"].unique())
    if not classes:
        raise NoData("qfit_curves.csv has no class")
    fig, axes = plt.subplots(1, len(classes), figsize=(6.9, 2.2), sharey=True)
    axes = np.atleast_1d(axes)
    numbers = {}
    for ax, cls in zip(axes, classes):
        c = curves[curves["cls"] == cls].sort_values("age")
        ax.plot(c["age"], c["alpha_assumed"], "--", color="#7f7f7f", lw=1.0, label="note (assumed q)")
        ax.plot(c["age"], c["alpha_fitted"], "-", color="#1f77b4", lw=1.2, label="fitted q")
        ax.plot(c["age"], c["hit_rate"], "o", color="#1f77b4", ms=2.4, mfc="white", mew=0.7, label="measured")
        for hl_col, color, ls in (("half_life_assumed", "#7f7f7f", "--"), ("half_life_fitted", "#1f77b4", "-")):
            if cls in table.index and np.isfinite(table.loc[cls, hl_col]):
                hl = float(table.loc[cls, hl_col])
                ax.plot([hl, hl], [0.0, 0.5], color=color, lw=0.6, ls=ls)
        ax.axhline(0.5, color="#bbbbbb", lw=0.5, ls=":")
        ax.set_xlim(0, 5)
        ax.set_ylim(0, 1.02)
        ax.set_xlabel("age of the report (s)")
        label = CLASS_LABELS.get(cls, cls)
        if cls in table.index and np.isfinite(table.loc[cls, ["q_assumed", "q_fitted", "half_life_assumed", "half_life_fitted"]].astype(float)).all():
            ax.set_title("%s: q note %.2g, fit %.2g; half-life %.1f s, %.1f s" % (label, table.loc[cls, "q_assumed"], table.loc[cls, "q_fitted"],
                                                                                table.loc[cls, "half_life_assumed"], table.loc[cls, "half_life_fitted"]),
                         fontsize=7.5)
            numbers[cls] = dict(q_assumed=float(table.loc[cls, "q_assumed"]), q_fitted=float(table.loc[cls, "q_fitted"]),
                                half_life_assumed=float(table.loc[cls, "half_life_assumed"]), half_life_fitted=float(table.loc[cls, "half_life_fitted"]),
                                hit_rate_5s=float(c["hit_rate"].iloc[-1]), fitted_5s=float(c["alpha_fitted"].iloc[-1]),
                                assumed_5s=float(c["alpha_assumed"].iloc[-1]))
        else:
            ax.set_title(label, fontsize=7.5)
        ax.grid(alpha=0.25, lw=0.4)
    axes[0].set_ylabel("reliability alpha: P(within 1.5 m)")
    axes[0].legend(loc="upper right", frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return numbers


def f05_reliability(results: str, path: str, hold: float = 5.0) -> dict:
    """F5: the reliability diagram of the belief, one curve per way, with the share of the objects per bin below."""
    rel = pd.read_csv(os.path.join(results, "d4", "reliability.csv"))
    rel = rel[rel["hold"] == hold]
    if not len(rel) or not (rel["count"] > 0).any():
        raise NoData("reliability.csv has no object at the hold of %g s" % hold)
    plt = _style()
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(3.45, 4.1), gridspec_kw={"height_ratios": [3.0, 1.2]}, sharex=True)
    ax.plot([0, 1], [0, 1], color="#bbbbbb", lw=0.8, ls="--", label="perfect calibration")
    numbers = {}
    width = 0.1 / (len(WAYS) + 1)
    for k, way in enumerate(WAYS):
        full = rel[rel["way"] == way].sort_values("bin")
        if not (full["count"] > 0).any():
            continue
        r = full.where(full["count"] > 0)                                       # an empty bin breaks the line: no segment across it
        ax.plot(r["belief_mean"], r["share_there"], "-", color=COLORS[way], lw=1.0, marker=MARKERS[way], ms=3.2, mfc="white", mew=0.8, label=SHORT[way],
                clip_on=False)
        ax2.bar(full["low"] + width * (k + 0.5), full["share_of_objects"], width=width, color=COLORS[way], lw=0)
        ece = float((full["count"] / full["count"].sum() * (full["belief_mean"].fillna(0) - full["share_there"].fillna(0)).abs()).sum())
        numbers[way] = dict(pooled_calibration_error=round(ece, 4), top_bin_share_there=round(float(full.loc[full["bin"] == 9, "share_there"].iloc[0]), 4),
                            top_bin_share_of_objects=round(float(full.loc[full["bin"] == 9, "share_of_objects"].iloc[0]), 4))
    b0 = rel[(rel["way"] == "B0") & (rel["count"] > 0)]
    if len(b0) == 1:                                                           # B0 has one bin: name the lone point
        ax.annotate("B0", (float(b0["belief_mean"].iloc[0]), float(b0["share_there"].iloc[0])), xytext=(-12, -3), textcoords="offset points",
                    fontsize=7, color=COLORS["B0"])
    ax.set_ylabel("share of the fused objects really there")
    ax.set_ylim(0, 1.02)
    ax.legend(loc="lower left", bbox_to_anchor=(-0.02, 1.0), frameon=False, ncol=2, columnspacing=1.5, handlelength=1.6, borderaxespad=0.0)
    ax.grid(alpha=0.25, lw=0.4)
    ax2.set_xlabel("belief that the object exists (bins of 0.1)")
    ax2.set_ylabel("share of\nthe objects")
    ax2.set_yscale("log")
    ax2.set_ylim(1e-3, 1.0)
    ax2.set_xlim(0, 1)
    ax2.grid(alpha=0.25, lw=0.4, axis="y")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return numbers


def f07_awareness(results: str, path: str) -> dict:
    """F7: the awareness ratio and the calibration error against the penetration, one line per way with the 95 % band over the runs
    of the level."""
    runs = pd.read_csv(os.path.join(results, "d4", "runs.csv"))
    runs = runs[(runs["hold"] == 5.0) & (runs["kind"] == "evaluation")]
    if not len(runs):
        raise NoData("runs.csv has no evaluation run at the hold of 5 s")
    plt = _style()
    fig, axes = plt.subplots(1, 2, figsize=(6.9, 2.3))
    numbers = {}
    for ax, col, label in ((axes[0], "awareness_ratio", "awareness ratio within 200 m"),
                           (axes[1], "calibration_error", "calibration error of the belief")):
        for way in WAYS:
            if not (runs["way"] == way).any():
                continue
            xs, ys, lo, hi, ns = [], [], [], [], []
            for pen in grid.PENETRATIONS:
                part = runs[(runs["way"] == way) & (runs["pen"] == pen)][col]
                m, a, b, n = mean_ci(part)
                xs.append(pen)
                ys.append(m)
                lo.append(a)
                hi.append(b)
                ns.append(n)
            ax.plot(xs, ys, "-", color=COLORS[way], lw=1.0, marker=MARKERS[way], ms=3.2, mfc="white", mew=0.8, label=SHORT[way])
            ax.fill_between(xs, lo, hi, color=COLORS[way], alpha=0.15, lw=0)
            numbers.setdefault(way, {})[col] = {pen: (round(y, 4), round(a, 4), round(b, 4), n) for pen, y, a, b, n in zip(xs, ys, lo, hi, ns)}
        ax.set_xlabel("penetration of the CP service (%)")
        ax.set_ylabel(label, fontsize=7.5)
        ax.set_xticks(list(grid.PENETRATIONS))
        ax.grid(alpha=0.25, lw=0.4)
    axes[0].set_ylim(0.5, 1.0)
    axes[0].legend(loc="lower right", frameon=False)
    axes[1].set_yscale("log")
    axes[1].set_ylim(0.01, 1.0)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return numbers


def f08_stale(results: str, path: str) -> dict:
    """F8: the stale-confidence rate against the channel busy ratio, one marker per cell and way, with the ghost rate beside."""
    cells = pd.read_csv(os.path.join(results, "d4", "cells.csv"))
    if not len(cells):
        raise NoData("cells.csv is empty")
    plt = _style()
    from matplotlib.lines import Line2D
    fig, axes = plt.subplots(1, 2, figsize=(6.9, 2.3), sharex=True)
    numbers = {}
    for ax, col, label in ((axes[0], "stale_rate", "stale-confidence rate"),
                           (axes[1], "ghost_rate", "ghost rate")):
        for way in WAYS:
            c = cells[cells["way"] == way]
            if c.empty:
                continue
            for pen in grid.PENETRATIONS:                                      # the face of the marker says the penetration of the cell
                cp = c[c["pen"] == pen]
                ax.errorbar(cp["cbr_mean"], cp[col], yerr=[cp[col] - cp[col + "_lo"], cp[col + "_hi"] - cp[col]], fmt=MARKERS[way], color=COLORS[way],
                            ms=3.2, mfc=PEN_FILL.get(pen, "white"), mew=0.8, lw=0.6, capsize=0, label=SHORT[way] if pen == grid.PENETRATIONS[0] else None)
            half = float(((c[col + "_hi"] - c[col + "_lo"]) / 2).max())
            numbers.setdefault(way, {})[col] = dict(mean=round(float(c[col].mean()), 4), min=round(float(c[col].min()), 4), max=round(float(c[col].max()), 4),
                                                    largest_half_width=round(half, 4))
        ax.set_xlabel("channel busy ratio (mean over the received CPMs)")
        ax.set_ylabel(label, fontsize=7.5)
        ax.set_ylim(-0.02, 1.02)
        ax.grid(alpha=0.25, lw=0.4)
    axes[0].legend(loc="center right", frameon=False)
    pens = [Line2D([], [], marker="o", color="#444444", mfc=PEN_FILL[p], mew=0.8, ms=3.2, lw=0, label="%d %%" % p) for p in grid.PENETRATIONS]
    axes[1].legend(handles=pens, title="penetration", loc="center right", frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    numbers["cbr_range"] = (round(float(cells["cbr_mean"].min()), 4), round(float(cells["cbr_mean"].max()), 4))
    return numbers


def f09_frontier(results: str, path: str) -> dict:
    """F9: the time above the target against the load, one marker per condition and rule of the control grid (left the busy ratio, middle
    the CPM bytes per sender-second), with the Pareto frontier of each load level dashed; right, the paired effect against ETSI (the load
    ratio and the time difference of the same condition and seed, averaged over the seeds) with the region of H4 shaded. The colours say
    the rule (the ETSI rules hollow), the markers the load level."""
    from aoc.analysis import control
    cells = pd.read_csv(os.path.join(results, "e3", "cells.csv"))
    if not len(cells):
        raise NoData("cells.csv of the control grid is empty")
    pairs_path = os.path.join(results, "e3", "cell_pairs.csv")
    cell_pairs = pd.read_csv(pairs_path) if os.path.isfile(pairs_path) else pd.DataFrame()
    plt = _style()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Polygon
    fig, axes = plt.subplots(1, 3, figsize=(6.9, 2.6))
    numbers = {}
    rules = [r for r in grid.CONTROL.factors["rule"] if r in set(cells["rule"])]
    loads = sorted(cells["load"].unique())
    hollow = {"etsi", "etsi_abs"}

    def face(rule):
        return "white" if rule in hollow else RULE_COLORS.get(rule, "black")

    xlabels = ("channel busy ratio", "CPM bytes per sender-second")
    for ax, (measure, col), xlabel in zip(axes[:2], control.LOAD_MEASURES.items(), xlabels):
        for load in loads:                                                          # the frontier within the load level
            part = cells[cells["load"] == load]
            on = part[control.frontier(part, col)].sort_values(col)
            ax.plot(on[col], on["time_above_target"], "--", color="#444444", lw=0.6, zorder=0)
            numbers["frontier_%s_load_%d" % (measure, load)] = {r: int(k) for r, k in on["rule"].value_counts().items()}
        for rule in rules:
            c = cells[cells["rule"] == rule]
            for load in loads:
                cl = c[c["load"] == load]
                if cl.empty:
                    continue
                ax.plot(cl[col], cl["time_above_target"], LOAD_MARKERS.get(load, "o"), color=RULE_COLORS.get(rule, "black"), ms=3.6, mfc=face(rule),
                        mew=0.8, lw=0, alpha=0.95)
        ax.set_xlabel(xlabel)
        ax.set_xlim(left=0)
        ax.grid(alpha=0.25, lw=0.4)
    axes[0].set_ylabel("time above the target (share)")
    ymin = float(cells["time_above_target"].min())
    for ax in axes[:2]:
        ax.set_ylim(max(0.0, ymin - 0.05), 1.0)
    # the paired effect against etsi: x the load ratio on the condition's load side, y the time difference
    ax = axes[2]
    cp = cell_pairs[cell_pairs["reference"] == "etsi"] if len(cell_pairs) else cell_pairs
    if len(cp):
        ylo = min(float(cp["time_diff"].min()) - 0.01, -0.03)
        yhi = max(float(cp["time_diff"].max()) + 0.01, 0.03)
        xhi = max(float(cp["load_ratio"].max()) * 1.05, 1.1)
        t, less = control.TOLERANCE, control.LOAD_LESS                           # the region of H4: less load and the time held, or more time at equal load
        ax.add_patch(Polygon([(0, -t), (less, -t), (less, t), (1 + t, t), (1 + t, yhi), (0, yhi)], closed=True, color="#2ca02c", alpha=0.12, lw=0))
        ax.axhline(0, color="#bbbbbb", lw=0.5)
        ax.axvline(1, color="#bbbbbb", lw=0.5)
        for rule in rules:
            for load in loads:
                part = cp[(cp["rule"] == rule) & (cp["load"] == load)]
                if part.empty:
                    continue
                ax.plot(part["load_ratio"], part["time_diff"], LOAD_MARKERS.get(load, "o"), color=RULE_COLORS.get(rule, "black"), ms=3.6, mfc=face(rule),
                        mew=0.8, lw=0, alpha=0.95)
        ax.set_xlim(0, xhi)
        ax.set_ylim(ylo, yhi)
        for rule in rules:
            part = cp[cp["rule"] == rule]
            if len(part):
                numbers["pair_" + rule] = dict(load_ratio=round(float(part["load_ratio"].mean()), 3), time_diff=round(float(part["time_diff"].mean()), 4),
                                               conditions_pass=int((part["verdict"] == "PASS").sum()), conditions=int(len(part)))
    ax.set_xlabel("load against ETSI (ratio)")
    ax.set_ylabel("time above the target against ETSI (difference)", fontsize=7)
    ax.grid(alpha=0.25, lw=0.4)
    rule_handles = [Line2D([], [], marker="o", color=RULE_COLORS[r], mfc=face(r), mew=0.8, lw=0, ms=3.6, label=RULE_LABELS.get(r, r)) for r in rules]
    load_handles = [Line2D([], [], marker=LOAD_MARKERS.get(ld, "o"), color="#444444", mfc="#444444", lw=0, ms=3.6, label="%d load stations" % ld) for ld in loads]
    fig.legend(handles=rule_handles + load_handles, loc="lower center", ncol=4, frameon=False, columnspacing=1.2, handletextpad=0.4)
    fig.tight_layout(rect=(0, 0.13, 1, 1))
    fig.savefig(path)
    plt.close(fig)
    for rule in rules:
        c = cells[cells["rule"] == rule]
        numbers[rule] = dict(time_above_target=round(float(c["time_above_target"].mean()), 4), cbr=round(float(c["cbr_mean"].mean()), 4),
                             bytes_per_sender=round(float(c["bytes_per_sender"].mean()), 1), conditions=int(len(c)))
    return numbers


FIGURES = {"f02": ("f02_alpha.pdf", f02_alpha), "f05": ("f05_calibration.pdf", f05_reliability), "f07": ("f07_awareness.pdf", f07_awareness),
           "f08": ("f08_stale.pdf", f08_stale), "f09": ("f09_control.pdf", f09_frontier)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Draw the figures of the core grid (F2, F5, F7, F8) and of the control grid (F9) from the results database.")
    ap.add_argument("--results", default="results", help="the results folder with d3/, d4/ and e3/ (default: results)")
    ap.add_argument("--out", default="figures", help="the folder of the PDF files (default: figures)")
    ap.add_argument("--only", default=None, help="one figure: f02, f05, f07, f08 or f09")
    args = ap.parse_args(argv)
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("matplotlib is not installed: nothing drawn")
        return 0
    wanted = [args.only] if args.only else list(FIGURES)
    os.makedirs(args.out, exist_ok=True)
    code = 0
    for key in wanted:
        if key not in FIGURES:
            print("unknown figure %s (f02, f05, f07, f08, f09)" % key)
            return 2
        name, draw = FIGURES[key]
        path = os.path.join(args.out, name)
        try:
            numbers = draw(args.results, path)
        except FileNotFoundError as exc:
            print("%s: no results (%s), nothing drawn" % (key, exc))
            continue
        except NoData as exc:
            print("%s: %s, nothing drawn" % (key, exc))
            continue
        print("written: %s" % path)
        for k, v in numbers.items():
            print("  %s: %s" % (k, v))
    return code


if __name__ == "__main__":
    sys.exit(main())
