"""The age at the receiver against the law of the theory note (task C2, figure F3).

On a single link (task C1) the sender sends a CPM every T seconds and each copy is lost with probability p.
Step 3 of the note gives the law of A, the time since the last received update, seen at a random time:

    P(A <= a) = 1 - p^k + (1 - p) p^k r / T        with a = k T + r, 0 <= r < T        (periodic generation)
    E[A] = T / 2 + T p / (1 - p)

The age of the object at the receiver is Delta = d0 + A, where d0 is the age at reception (the radio delay
plus the time since the sender's last sensor step). This module measures A and d0 on the exported runs and
compares A with the law. The measure is exact in time, not sampled: over a window of length L with receptions at
r_1 < r_2 < ... the share of time with an age at most a is

    F(a) = (1 / L) * sum over the gaps g of min(g, a)

where the gaps are the times between two receptions (and from the last reception to the end of the window).
The first reception after the warm-up opens the window, so the time before any knowledge does not count.

    aoc-ages RESULT_FOLDER [--warmup 10] [--out f3_ages.pdf] [--table ages.csv]

One row per run: the mean age measured and from the law, their relative gap, the largest gap between the two
distribution functions (the Kolmogorov distance), and the age at reception. PASS when every run has a relative
gap of the mean below ``--max-rel`` (default 5%) and a Kolmogorov distance below ``--max-ks`` (default 0.05).
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

from ..theory.averages import age_cdf_periodic, mean_age_periodic
from .link import find_runs, read_link_run

GRID = np.round(np.arange(0.0, 6.0001, 0.01), 4)       # ages at which the distribution functions are compared, in s


def receptions_by_link(obs: pd.DataFrame) -> dict[tuple, pd.DataFrame]:
    """The received rows about objects the receiver never saw itself, by (receiver, sender, object), sorted by t_rx."""
    is_v2x = (obs["src"] == "v2x").to_numpy(dtype=bool, na_value=False)
    own = obs[~is_v2x]
    seen = set(zip(own["rx_station"].astype(object), own["gt_match_id"].astype(object)))
    v2x = obs[is_v2x]
    v2x = v2x[(v2x["gt_match_id"] != v2x["rx_station"]).to_numpy(dtype=bool, na_value=True)]
    keep = [(r, o) not in seen for r, o in zip(v2x["rx_station"].astype(object), v2x["gt_match_id"].astype(object))]
    v2x = v2x[np.array(keep, dtype=bool)] if len(v2x) else v2x
    out = {}
    for key, rows in v2x.groupby(["rx_station", "tx_station", "gt_match_id"], sort=True):
        out[tuple(str(k) for k in key)] = rows.sort_values("t_rx", kind="stable")
    return out


def gaps_of_window(t_rx: np.ndarray, t_end: float, t_start: float | None = None) -> np.ndarray:
    """The gaps between two receptions, and from the last one to the end, inside the window [first reception >= t_start, t_end]."""
    t_rx = np.sort(np.asarray(t_rx, dtype=float))
    if t_start is not None:
        t_rx = t_rx[t_rx >= t_start - 1e-9]
    t_rx = t_rx[t_rx <= t_end + 1e-9]
    if len(t_rx) == 0:
        return np.zeros(0)
    gaps = np.diff(np.append(t_rx, t_end))
    return gaps[gaps > 0]


def cdf_of_gaps(gaps: np.ndarray, grid: np.ndarray = GRID) -> np.ndarray:
    """The time-average distribution function of the age since the last reception: F(a) = sum of min(g, a) / sum of g."""
    gaps = np.asarray(gaps, dtype=float)
    total = float(gaps.sum())
    if len(gaps) == 0 or total <= 0:
        return np.full(len(grid), np.nan)
    return np.minimum(gaps[None, :], np.asarray(grid, dtype=float)[:, None]).sum(axis=1) / total


def mean_of_gaps(gaps: np.ndarray) -> float:
    """The time-average age since the last reception: sum of g^2 / 2 over the gaps, divided by the window."""
    gaps = np.asarray(gaps, dtype=float)
    return float((gaps**2).sum() / (2.0 * gaps.sum())) if len(gaps) and gaps.sum() > 0 else float("nan")


def empirical_age_cdf(t_rx: np.ndarray, t_end: float, grid: np.ndarray = GRID, t_start: float | None = None) -> tuple[np.ndarray, float]:
    """F(grid) of one link and the length of its window (see ``gaps_of_window`` and ``cdf_of_gaps``)."""
    gaps = gaps_of_window(t_rx, t_end, t_start)
    return cdf_of_gaps(gaps, grid), float(gaps.sum())


def mean_age_from_gaps(t_rx: np.ndarray, t_end: float, t_start: float | None = None) -> float:
    return mean_of_gaps(gaps_of_window(t_rx, t_end, t_start))


def law_noise(period: float, loss: float, window: float, grid: np.ndarray = GRID, draws: int = 1000, seed: int = 0) -> tuple[float, float]:
    """How much the mean and the distribution function of a run of this length move under the law itself.

    Draws ``draws`` runs from the law (gaps of k periods, k geometric with success 1 - p) over a window of the
    same length, and returns the standard deviation of their mean age and the 99th percentile of the largest
    distance between their distribution function and the law. This is the noise a run of this length cannot
    avoid, so a measured gap smaller than it says nothing against the law.
    """
    if not (period > 0 and 0.0 <= loss < 1.0 and window > period):
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    law = theory_cdf(grid, period, loss)
    expected = window * (1.0 - loss) / period                                   # gaps of mean T / (1 - p) fill the window
    n = int(np.ceil(expected + 10.0 * np.sqrt(max(expected, 1.0)) + 20.0))      # ten standard deviations of margin
    means, distances = np.empty(draws), np.empty(draws)
    for k in range(draws):
        gaps = period * rng.geometric(1.0 - loss, n).astype(float)
        cut = np.searchsorted(np.cumsum(gaps), window, side="right")
        gaps = gaps[:cut + 1]
        gaps[-1] = window - gaps[:-1].sum()                                     # the last gap ends at the end of the window
        means[k] = mean_of_gaps(gaps)
        distances[k] = np.nanmax(np.abs(cdf_of_gaps(gaps, grid) - law))
    return float(means.std(ddof=1)), float(np.percentile(distances, 99))


def theory_cdf(grid: np.ndarray, period: float, loss: float) -> np.ndarray:
    return np.asarray(age_cdf_periodic(grid, period, loss), dtype=float)


def compare_run(folder: str | os.PathLike, warmup: float = 10.0, grid: np.ndarray = GRID, draws: int = 1000) -> dict:
    """The age at the receiver of one exported run against the law, with the gaps of all its links pooled."""
    row = read_link_run(folder)
    out = dict(folder=row["folder"], run_id=row["run_id"], T=row["T"], p=row["p"], links=0, receptions=0, window_s=None,
               mean_measured=None, mean_law=None, rel_gap=None, rel_noise=None, ks=None, ks_noise=None, d0_median=None, d0_p95=None, cdf=None)
    obs_path = os.path.join(os.fspath(folder), "observations.parquet")
    if not os.path.isfile(obs_path) or row["T"] is None or row["p"] is None:
        return out
    obs = pd.read_parquet(obs_path)
    links = receptions_by_link(obs)
    t_end = float(obs["t_sim"].astype(float).max())                            # the end of the run: every car stays until then in the C1 scenario
    gaps, d0 = [], []
    for rows in links.values():
        t_rx = rows["t_rx"].to_numpy(dtype=float)
        g = gaps_of_window(t_rx, t_end, t_start=warmup)
        if len(g) == 0:
            continue
        gaps.append(g)
        out["links"] += 1
        out["receptions"] += int((t_rx >= warmup - 1e-9).sum())
        d0.append(rows["age"].to_numpy(dtype=float)[t_rx >= warmup - 1e-9])
    if not gaps:
        return out
    gaps = np.concatenate(gaps)
    cdf = cdf_of_gaps(gaps, grid)
    law = theory_cdf(grid, row["T"], row["p"])
    sd_mean, ks_noise = law_noise(row["T"], row["p"], float(gaps.sum()), grid, draws)
    out["window_s"] = round(float(gaps.sum()), 1)
    out["mean_measured"] = round(mean_of_gaps(gaps), 4)
    out["mean_law"] = round(float(mean_age_periodic(row["T"], row["p"])), 4)
    out["rel_gap"] = round(out["mean_measured"] / out["mean_law"] - 1.0, 4)
    out["rel_noise"] = round(sd_mean / out["mean_law"], 4) if np.isfinite(sd_mean) else None
    out["ks"] = round(float(np.nanmax(np.abs(cdf - law))), 4)
    out["ks_noise"] = round(ks_noise, 4) if np.isfinite(ks_noise) else None
    d0 = np.concatenate(d0) if d0 else np.array([])
    out["d0_median"] = round(float(np.median(d0)), 4) if len(d0) else None
    out["d0_p95"] = round(float(np.percentile(d0, 95)), 4) if len(d0) else None
    out["cdf"] = cdf
    return out


def compare_grid(root: str | os.PathLike, warmup: float = 10.0, draws: int = 1000) -> pd.DataFrame:
    """One row per exported run under ``root``, with the curves in the column ``cdf``."""
    rows = [compare_run(folder, warmup, draws=draws) for folder in find_runs(root)]
    columns = ["folder", "run_id", "T", "p", "links", "receptions", "window_s", "mean_measured", "mean_law", "rel_gap", "rel_noise", "ks",
               "ks_noise", "d0_median", "d0_p95", "cdf"]
    table = pd.DataFrame(rows, columns=columns)
    return table.sort_values(["p", "T"], ascending=[True, False], kind="stable").reset_index(drop=True)


def check_ages(table: pd.DataFrame, max_rel: float = 0.05, max_ks: float = 0.05) -> list[str]:
    """The problems of a grid of runs; an empty list is a PASS.

    A run passes when the relative gap of its mean age is within ``max_rel``, or within three times the noise of
    a run of this length under the law (``law_noise``), and when the Kolmogorov distance is within ``max_ks``, or
    within the 99th percentile of that noise. So a short run with few receptions is judged against what it can
    show, and a long run against the 5% of the plan. Over four runs or more, the gaps must also average out:
    their mean must stay within 2% or three times its own noise, which catches a small systematic error that no
    single run can show.
    """
    problems = []
    if not len(table):
        return ["no exported run found"]
    number = lambda value: float(np.nan_to_num(np.asarray(value if value is not None else 0.0, dtype=float)))
    for _, row in table.iterrows():
        name = os.path.basename(os.path.dirname(row["folder"])) or row["folder"]
        if row["mean_measured"] is None or (isinstance(row["mean_measured"], float) and np.isnan(row["mean_measured"])):
            problems.append("%s: no reception about an object the receiver does not see (or T and p unknown)" % name)
            continue
        rel_limit = max(max_rel, 3.0 * number(row["rel_noise"]))
        if abs(row["rel_gap"]) > rel_limit:
            problems.append("%s (T=%g p=%g): the mean age is %.3f s, the law says %.3f s (%+.1f%%, limit %.1f%%)"
                            % (name, row["T"], row["p"], row["mean_measured"], row["mean_law"], 100 * row["rel_gap"], 100 * rel_limit))
        ks_limit = max(max_ks, number(row["ks_noise"]))
        if row["ks"] > ks_limit:
            problems.append("%s (T=%g p=%g): the distribution functions differ by %.3f at most (limit %.3f)" % (name, row["T"], row["p"], row["ks"], ks_limit))
    # over the runs: the gaps of the mean must average out (the noise of their mean is their noise over the root of their number)
    valid = table.dropna(subset=["rel_gap"])
    if len(valid) >= 4:
        noise = np.sqrt(np.nansum(np.asarray([number(v) for v in valid["rel_noise"]]) ** 2)) / len(valid)
        limit = max(0.02, 3.0 * noise)
        if abs(float(valid["rel_gap"].mean())) > limit:
            problems.append("over the runs: the gaps of the mean age average %+.1f%% (limit %.1f%%): a systematic error, not noise"
                            % (100 * float(valid["rel_gap"].mean()), 100 * limit))
    return problems


def draw(table: pd.DataFrame, path: str, grid: np.ndarray = GRID) -> None:
    """Figure F3: the measured distribution functions (dots) and the law (lines), one panel per loss p."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"], "mathtext.fontset": "stix",
                         "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7,
                         "axes.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
    losses = sorted({float(p) for p in table["p"].dropna()})
    if not losses:
        raise ValueError("nothing to draw: no run with a known loss")
    fig, axes = plt.subplots(1, len(losses), figsize=(7.1, 2.1), sharey=True)
    axes = np.atleast_1d(axes)
    colors = plt.cm.viridis(np.linspace(0.0, 0.85, max(1, table["T"].nunique())))
    periods = sorted({float(t) for t in table["T"].dropna()}, reverse=True)
    for ax, loss in zip(axes, losses):
        part = table[(table["p"] == loss) & table["cdf"].notna()]
        x_max = 0.5
        for color, period in zip(colors, periods):
            rows = part[part["T"] == period]
            if not len(rows):
                continue
            cdf = rows["cdf"].iloc[0]                                           # several runs of one cell: the first one (aoc-link --grid warns)
            law = theory_cdf(grid, period, loss)
            reach = grid[np.searchsorted(law, 0.995)] if law[-1] >= 0.995 else grid[-1]
            x_max = max(x_max, float(reach))
            ax.plot(grid, law, color=color, lw=1.0, label="T = %g s" % period)
            step = max(1, len(grid) // 40)
            ax.plot(grid[::step], cdf[::step], "o", color=color, ms=2.2, mfc="white", mew=0.7)
        ax.set_xlim(0, min(x_max * 1.05, grid[-1]))
        ax.set_ylim(0, 1.02)
        ax.set_title("p = %g" % loss, fontsize=8)
        ax.set_xlabel("age since the last reception (s)")
        ax.grid(True, lw=0.3, alpha=0.5)
    axes[0].set_ylabel("P(A <= a)")
    axes[-1].legend(loc="lower right", frameon=False, title="lines: law, dots: runs", title_fontsize=7)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare the age at the receiver of the single-link runs with the law of the theory note (C2, figure F3).")
    ap.add_argument("root", help="folder that holds the exported runs (each with an export_summary.json)")
    ap.add_argument("--warmup", type=float, default=10.0, help="seconds left out at the start of each run (default 10)")
    ap.add_argument("--out", default=None, help="write figure F3 to this file (.pdf or .png)")
    ap.add_argument("--table", default=None, help="write the table to this file (.csv or .parquet)")
    ap.add_argument("--max-rel", type=float, default=0.05, help="largest relative gap of the mean age for a PASS (default 0.05)")
    ap.add_argument("--max-ks", type=float, default=0.05, help="largest Kolmogorov distance for a PASS (default 0.05)")
    ap.add_argument("--draws", type=int, default=1000, help="runs drawn from the law to measure the noise (default 1000)")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.root):
        print("ERROR: %s is not a folder" % args.root, file=sys.stderr)
        return 2
    table = compare_grid(args.root, args.warmup, args.draws)
    problems = check_ages(table, args.max_rel, args.max_ks)
    plain = table.drop(columns=["cdf"])
    if args.table:
        if args.table.endswith(".parquet"):
            plain.to_parquet(args.table, index=False)
        else:
            plain.to_csv(args.table, index=False)
    drawn = False
    if args.out and table["cdf"].notna().any():
        try:
            draw(table, args.out)
            drawn = True
        except ImportError:
            print("WARNING: matplotlib is not installed, no figure written", file=sys.stderr)
    number = lambda value, digits=3: "none" if value is None or (isinstance(value, float) and np.isnan(value)) else ("%%.%df" % digits) % value
    print("C2 AGE CHECK")
    print("  runs:                %d under %s, warm-up %g s" % (len(table), os.path.abspath(args.root), args.warmup))
    print("  %-5s %-5s %5s %9s %8s %9s %9s %8s %7s %7s %7s %8s %8s"
          % ("T", "p", "links", "received", "window", "mean age", "law", "gap", "noise", "KS", "noise", "d0 med", "d0 p95"))
    percent = lambda value: "none" if value is None or (isinstance(value, float) and np.isnan(value)) else "%+.1f%%" % (100 * value)
    for _, row in table.iterrows():
        print("  %-5s %-5s %5s %9s %8s %9s %9s %8s %7s %7s %7s %8s %8s"
              % (number(row["T"], 1), number(row["p"], 1), row["links"], row["receptions"], number(row["window_s"], 0),
                 number(row["mean_measured"]), number(row["mean_law"]), percent(row["rel_gap"]), percent(row["rel_noise"]).lstrip("+"),
                 number(row["ks"]), number(row["ks_noise"]), number(row["d0_median"]), number(row["d0_p95"])))
    print("  mean age: time average of the age since the last reception, measured and from the law T/2 + T p/(1 - p); gap: measured against law.")
    print("  noise: what a run of this length shows under the law itself: standard deviation of the mean age, 99th percentile of the")
    print("  distance of the distribution function. KS: largest distance between the measured and the law distribution functions.")
    print("  d0: age at reception, median and 95th percentile. The age of the object at the receiver is d0 + A.")
    valid = table["rel_gap"].dropna()
    if len(valid):
        print("  over the runs:       largest |gap| of the mean %.1f%%, mean of the gaps %+.1f%%, largest KS %.3f"
              % (100 * valid.abs().max(), 100 * valid.mean(), table["ks"].dropna().max()))
    if drawn:
        print("  figure:              %s" % os.path.abspath(args.out))
    print("  result:              %s%s" % ("PASS" if not problems else "FAIL", "" if not problems else "\n    " + "\n    ".join(problems)))
    return 0 if not problems else 3


if __name__ == "__main__":
    sys.exit(main())
