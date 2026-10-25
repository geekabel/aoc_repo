"""The expected reliability E[alpha] on the single-link runs against the formulas of the note (task C3, figure F4).

On a single link (task C1) the object at the receiver has the age Delta = d0 + A: d0 the age at reception, A the
time since the last received update. The reliability of the object is alpha(Delta), the curve of step 2 of the
note (``aoc.theory.reliability.alpha``). Step 3 gives its average over time, E[alpha], in three forms
(``aoc.theory.averages``): the exact law (numerical integral of alpha over the age law), the plateau surrogate
(closed form, the main result of A3) and the exponential surrogate (closed form, the first idea of the plan).

This module measures E[alpha] on each exported run, exactly in time: for every received update i with age at
reception d0_i and a gap g_i until the next one, the time integral of alpha from d0_i to d0_i + g_i, summed and
divided by the window. It is done for every class of the note (the age process is the same, the curve is not).
Then it compares with the three forms, each taken at the median d0 of the run.

    aoc-ealpha RESULT_FOLDER [--out f4.pdf] [--table ealpha.csv]

The plan asks for a relative error below 5% between the closed form and the simulation (H2). A run of 330 s
cannot always show that: as in task C2, the noise of a run of this length under the law is drawn and a run
passes when its gap to the exact law is within 5% or three times that noise. Over the runs the gaps must
average out. The gap to the plateau closed form is reported for the plan's criterion, with its model error.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

from ..theory import averages as a3
from ..theory import reliability as a2
from .ages import gaps_of_window, receptions_by_link
from .link import find_runs, read_link_run

CLASSES = {name: values[:4] for name, values in a2.CLASSES.items()}          # sx, sv, sxv, q
PANELS = ("pedestrian", "car (moving)")                                        # the two classes drawn in figure F4
STEP = 0.001                                                                   # s, the grid of the integral of alpha


class AlphaIntegral:
    """The integral of alpha from 0 to x, for one class, on a fine grid; ``between(lo, hi)`` by interpolation."""

    def __init__(self, par: tuple, horizon: float, step: float = STEP, eps: float = a2.EPS):
        self.grid = np.arange(0.0, horizon + 2.0 * step, step)
        values = np.asarray(a2.alpha(self.grid, *par, eps), dtype=float)
        self.cumulative = np.concatenate([[0.0], np.cumsum(0.5 * (values[1:] + values[:-1]) * np.diff(self.grid))])

    def between(self, lo, hi) -> np.ndarray:
        lo, hi = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)
        return np.interp(hi, self.grid, self.cumulative) - np.interp(lo, self.grid, self.cumulative)


def measured_ealpha(d0: np.ndarray, gaps: np.ndarray, integral: AlphaIntegral) -> float:
    """The time average of alpha(d0_i + u) over each gap, pooled: sum of the integrals over the window."""
    d0, gaps = np.asarray(d0, dtype=float), np.asarray(gaps, dtype=float)
    total = float(gaps.sum())
    if len(gaps) == 0 or total <= 0:
        return float("nan")
    return float(integral.between(d0, d0 + gaps).sum() / total)


def law_ealpha(period: float, loss: float, par: tuple, d0: float) -> dict:
    """E[alpha] from the three forms of the note at one age at reception."""
    dd0, tau2 = a2.plateau_surrogate(*par)
    d_half = a2.half_life(*par)
    out = dict(exact=a3.e_exact_periodic(period, loss, par, d0), plateau=a3.e_plateau_periodic(period, loss, dd0, tau2, d0))
    out["expo"] = a3.e_expo_periodic(period, loss, a2.tau_from_half_life(d_half), d0) if np.isfinite(d_half) else float("nan")
    return out


def law_noise(period: float, loss: float, window: float, d0_values: np.ndarray, integral: AlphaIntegral, draws: int = 1000,
              seed: int = 0) -> float:
    """The standard deviation of the measured E[alpha] over runs of this length drawn from the law (gaps and d0 drawn again)."""
    if not (period > 0 and 0.0 <= loss < 1.0 and window > period) or len(d0_values) == 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    expected = window * (1.0 - loss) / period
    n = int(np.ceil(expected + 10.0 * np.sqrt(max(expected, 1.0)) + 20.0))
    values = np.empty(draws)
    for k in range(draws):
        gaps = period * rng.geometric(1.0 - loss, n).astype(float)
        cut = np.searchsorted(np.cumsum(gaps), window, side="right")
        gaps = gaps[:cut + 1]
        gaps[-1] = window - gaps[:-1].sum()
        d0 = d0_values[rng.integers(0, len(d0_values), len(gaps))]
        values[k] = measured_ealpha(d0, gaps, integral)
    return float(values.std(ddof=1))


def run_gaps(folder: str | os.PathLike, warmup: float = 10.0) -> tuple[dict, np.ndarray, np.ndarray, float]:
    """The settings of a run, the ages at reception, the gaps that follow them (pooled over the links), and the window."""
    row = read_link_run(folder)
    obs_path = os.path.join(os.fspath(folder), "observations.parquet")
    if not os.path.isfile(obs_path) or row["T"] is None or row["p"] is None:
        return row, np.zeros(0), np.zeros(0), 0.0
    obs = pd.read_parquet(obs_path)
    t_end = float(obs["t_sim"].astype(float).max())
    d0_all, gaps_all = [], []
    for rows in receptions_by_link(obs).values():
        t_rx = rows["t_rx"].to_numpy(dtype=float)
        order = np.argsort(t_rx, kind="stable")
        t_rx, d0 = t_rx[order], rows["age"].to_numpy(dtype=float)[order]
        keep = (t_rx >= warmup - 1e-9) & (t_rx <= t_end + 1e-9)
        t_rx, d0 = t_rx[keep], d0[keep]
        gaps = gaps_of_window(t_rx, t_end)
        if len(gaps) == 0:
            continue
        first = np.diff(np.append(t_rx, t_end)) > 0                              # the gaps of zero length were dropped
        d0_all.append(d0[first])
        gaps_all.append(gaps)
    if not gaps_all:
        return row, np.zeros(0), np.zeros(0), 0.0
    gaps = np.concatenate(gaps_all)
    return row, np.concatenate(d0_all), gaps, float(gaps.sum())


def compare_run(folder: str | os.PathLike, warmup: float = 10.0, draws: int = 1000, classes: dict | None = None) -> list[dict]:
    """One row per class of the note for one run: measured E[alpha], the three forms, the gaps and the noise."""
    classes = classes or CLASSES
    row, d0, gaps, window = run_gaps(folder, warmup)
    base = dict(folder=row["folder"], run_id=row["run_id"], T=row["T"], p=row["p"], receptions=int(len(gaps)), window_s=round(window, 1),
                d0_median=round(float(np.median(d0)), 4) if len(d0) else None)
    out = []
    horizon = float((d0 + gaps).max()) + 1.0 if len(gaps) else 1.0
    if len(gaps) and row["T"] and row["p"]:
        horizon = max(horizon, float(np.max(d0)) + row["T"] * np.ceil(np.log(1e-9) / np.log(row["p"])) + 1.0)   # the longest gap a draw can make
    for name, par in classes.items():
        entry = dict(base, cls=name, measured=None, exact=None, plateau=None, expo=None, gap_exact=None, gap_plateau=None, gap_expo=None, noise=None)
        if len(gaps) and row["T"] is not None and row["p"] is not None:
            integral = AlphaIntegral(par, horizon)
            measured = measured_ealpha(d0, gaps, integral)
            law = law_ealpha(row["T"], row["p"], par, float(np.median(d0)))
            sd = law_noise(row["T"], row["p"], window, d0, integral, draws)
            entry.update(measured=round(measured, 4), exact=round(law["exact"], 4), plateau=round(law["plateau"], 4),
                         expo=round(law["expo"], 4) if np.isfinite(law["expo"]) else None,
                         gap_exact=round(measured / law["exact"] - 1.0, 4), gap_plateau=round(measured / law["plateau"] - 1.0, 4),
                         gap_expo=round(measured / law["expo"] - 1.0, 4) if np.isfinite(law["expo"]) and law["expo"] > 0 else None,
                         noise=round(sd / law["exact"], 4) if np.isfinite(sd) else None)
        out.append(entry)
    return out


def compare_grid(root: str | os.PathLike, warmup: float = 10.0, draws: int = 1000, classes: dict | None = None) -> pd.DataFrame:
    rows = [entry for folder in find_runs(root) for entry in compare_run(folder, warmup, draws, classes)]
    columns = ["folder", "run_id", "T", "p", "cls", "receptions", "window_s", "d0_median", "measured", "exact", "plateau", "expo",
               "gap_exact", "gap_plateau", "gap_expo", "noise"]
    table = pd.DataFrame(rows, columns=columns)
    return table.sort_values(["cls", "p", "T"], ascending=[True, True, False], kind="stable").reset_index(drop=True)


def check_ealpha(table: pd.DataFrame, max_rel: float = 0.05) -> list[str]:
    """The problems; an empty list is a PASS. Per run and class: the gap to the exact law within max(max_rel, 3 noise).
    Per class over four runs or more: the gaps to the exact law must average out (within 2% or three times their noise)."""
    problems = []
    if not len(table):
        return ["no exported run found"]
    number = lambda value: float(np.nan_to_num(np.asarray(value if value is not None else 0.0, dtype=float)))
    for _, row in table.iterrows():
        name = os.path.basename(os.path.dirname(row["folder"])) or row["folder"]
        if row["measured"] is None or (isinstance(row["measured"], float) and np.isnan(row["measured"])):
            message = "%s: no reception about an object the receiver does not see (or T and p unknown)" % name
            if message not in problems:
                problems.append(message)
            continue
        limit = max(max_rel, 3.0 * number(row["noise"]))
        if abs(row["gap_exact"]) > limit:
            problems.append("%s (T=%g p=%g, %s): E[alpha] measured %.4f, exact law %.4f (%+.1f%%, limit %.1f%%)"
                            % (name, row["T"], row["p"], row["cls"], row["measured"], row["exact"], 100 * row["gap_exact"], 100 * limit))
    for cls, part in table.dropna(subset=["gap_exact"]).groupby("cls"):
        if len(part) >= 4:
            noise = np.sqrt(np.nansum(np.asarray([number(v) for v in part["noise"]]) ** 2)) / len(part)
            limit = max(0.02, 3.0 * noise)
            if abs(float(part["gap_exact"].mean())) > limit:
                problems.append("%s, over the runs: the gaps to the exact law average %+.1f%% (limit %.1f%%): a systematic error"
                                % (cls, 100 * float(part["gap_exact"].mean()), 100 * limit))
    return problems


def draw(table: pd.DataFrame, path: str, panels: tuple = PANELS) -> None:
    """Figure F4: E[alpha] against the rate, one line per loss (plateau closed form), markers for the runs, one panel per class."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"], "mathtext.fontset": "stix",
                         "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7,
                         "axes.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
    panels = [name for name in panels if (table["cls"] == name).any()]
    if not panels:
        raise ValueError("nothing to draw: no run for the classes of the figure")
    fig, axes = plt.subplots(1, len(panels), figsize=(3.45 * len(panels), 2.3))
    axes = np.atleast_1d(axes)
    losses = sorted({float(p) for p in table["p"].dropna()})
    colors = plt.cm.viridis(np.linspace(0.0, 0.85, max(1, len(losses))))
    rates = np.logspace(np.log10(0.5), np.log10(20.0), 120)
    for ax, name in zip(axes, panels):
        part = table[(table["cls"] == name) & table["measured"].notna()]
        par = CLASSES[name]
        dd0, tau2 = a2.plateau_surrogate(*par)
        worst = 0.0
        for color, loss in zip(colors, losses):
            rows = part[part["p"] == loss]
            if not len(rows):
                continue
            d0 = float(rows["d0_median"].median())
            line = [a3.e_plateau_periodic(1.0 / rate, loss, dd0, tau2, d0) for rate in rates]
            ax.plot(rates, line, color=color, lw=1.0, label="p = %g" % loss)
            ax.plot(1.0 / rows["T"].to_numpy(dtype=float), rows["measured"].to_numpy(dtype=float), "o", color=color, ms=3.0, mfc="white", mew=0.8)
            worst = max(worst, float(rows["gap_plateau"].abs().max()))
        ax.set_xscale("log")
        ax.set_xticks([1, 2, 5, 10])
        ax.set_xticklabels(["1", "2", "5", "10"])
        ax.set_xlim(0.7, 15.0)
        ax.set_ylim(0.0, 1.02)
        ax.set_xlabel("update rate (Hz)")
        ax.set_title("%s, d0 = %.0f ms" % (name, 1000 * float(part["d0_median"].median())), fontsize=8)
        ax.grid(True, lw=0.3, alpha=0.5)
        ax.text(0.03, 0.05, "largest gap of a run to the closed form at its own d0: %.1f%%" % (100 * worst), transform=ax.transAxes, fontsize=7)
    axes[0].set_ylabel("E[alpha]")
    axes[-1].legend(loc="lower right", frameon=False, title="lines: closed form, dots: runs", title_fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare E[alpha] measured on the single-link runs with the formulas of the note (C3, figure F4).")
    ap.add_argument("root", help="folder that holds the exported runs (each with an export_summary.json)")
    ap.add_argument("--warmup", type=float, default=10.0, help="seconds left out at the start of each run (default 10)")
    ap.add_argument("--out", default=None, help="write figure F4 to this file (.pdf or .png)")
    ap.add_argument("--table", default=None, help="write the table to this file (.csv or .parquet)")
    ap.add_argument("--max-rel", type=float, default=0.05, help="largest relative gap to the exact law for a PASS (default 0.05)")
    ap.add_argument("--draws", type=int, default=1000, help="runs drawn from the law to measure the noise (default 1000)")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.root):
        print("ERROR: %s is not a folder" % args.root, file=sys.stderr)
        return 2
    table = compare_grid(args.root, args.warmup, args.draws)
    problems = check_ealpha(table, args.max_rel)
    if args.table:
        if args.table.endswith(".parquet"):
            table.to_parquet(args.table, index=False)
        else:
            table.to_csv(args.table, index=False)
    drawn = False
    if args.out and table["measured"].notna().any():
        try:
            draw(table, args.out)
            drawn = True
        except ImportError:
            print("WARNING: matplotlib is not installed, no figure written", file=sys.stderr)
    number = lambda value, digits=4: "none" if value is None or (isinstance(value, float) and np.isnan(value)) else ("%%.%df" % digits) % value
    percent = lambda value: "none" if value is None or (isinstance(value, float) and np.isnan(value)) else "%+.1f%%" % (100 * value)
    print("C3 E[ALPHA] CHECK")
    print("  runs:                %d under %s, warm-up %g s, %d classes" % (table["folder"].nunique(), os.path.abspath(args.root), args.warmup,
                                                                             table["cls"].nunique()))
    for cls, part in table.groupby("cls", sort=False):
        print("  class %s" % cls)
        print("  %-5s %-5s %9s %7s %9s %9s %9s %9s %8s %8s %8s %7s"
              % ("T", "p", "received", "d0", "measured", "exact", "plateau", "expo", "gap ex", "gap pl", "gap expo", "noise"))
        for _, row in part.iterrows():
            print("  %-5s %-5s %9s %7s %9s %9s %9s %9s %8s %8s %8s %7s"
                  % (number(row["T"], 1), number(row["p"], 1), row["receptions"], number(row["d0_median"], 3), number(row["measured"]),
                     number(row["exact"]), number(row["plateau"]), number(row["expo"]), percent(row["gap_exact"]), percent(row["gap_plateau"]),
                     percent(row["gap_expo"]), percent(row["noise"]).lstrip("+")))
        valid = part.dropna(subset=["gap_exact"])
        if len(valid):
            print("  over the runs: gap to the exact law largest %.1f%%, mean %+.1f%%; to the plateau form largest %.1f%%, mean %+.1f%%; "
                  "runs within 5%% of the plateau form: %d of %d"
                  % (100 * valid["gap_exact"].abs().max(), 100 * valid["gap_exact"].mean(), 100 * valid["gap_plateau"].abs().max(),
                     100 * valid["gap_plateau"].mean(), int((valid["gap_plateau"].abs() <= 0.05).sum()), len(valid)))
    print("  measured: time average of alpha(d0 + A) over the run. exact: the law of A integrated numerically; plateau and expo: the closed")
    print("  forms of the note, all three at the median d0 of the run. gap: measured against each form. noise: standard deviation of the")
    print("  measured value over runs of this length drawn from the law.")
    if drawn:
        print("  figure:              %s" % os.path.abspath(args.out))
    valid = table.dropna(subset=["gap_plateau"])
    if len(valid):
        within = int((valid["gap_plateau"].abs() <= 0.05).sum())
        print("  H2:                  %d of %d run and class pairs within 5%% of the plateau closed form (largest gap %.1f%%)"
              % (within, len(valid), 100 * valid["gap_plateau"].abs().max()))
    print("  result:              %s%s" % ("PASS" if not problems else "FAIL", "" if not problems else "\n    " + "\n    ".join(problems)))
    return 0 if not problems else 3


if __name__ == "__main__":
    sys.exit(main())
