"""The calibration of the motion value q on the calibration runs of the grid (task D3, decision of Oct 24, 2026).

The reliability curve of the note, alpha(age) = 1 - exp(-eps^2 / (2 (spread + q age^3 / 3))), is the probability that
the place a received report predicts for its object (the report moved forward by its age at constant speed) is
within eps of the true place. The note assumes a motion value q per class; the plan says to fit q per class on
calibration runs kept apart from the evaluation runs (the seeds 10 and 11 of every cell). This module does the fit
without the pipeline, on the thing the curve speaks about:

  - from the calibration runs, the received rows (the CPM reports, with the noise of the replay) about the objects
    of each class, after the warm-up, for some receivers of each run;
  - each report is used at the ages 0.1, 0.2, ... 5.0 s (the hold), as the replay would use it until a newer report
    comes: the report moved forward by the age against the true place of its object at that time (interpolated in
    the truth), a hit when within eps;
  - per class, the q that maximises the Bernoulli likelihood of the hits under alpha(age), searched on a logarithmic
    grid then refined; the assumed q of the note next to it, with the log-likelihood and the calibration error of
    both curves against the hits, and the hit rate per age bin for the figure.

    aoc-fit-q ROOT [--out qfit.csv] [--curves curves.csv] [--figure qfit.pdf] [--seeds 10,11] [--receivers 5]
                   [--rows 20000] [--jobs N] [--runs 10-11,22-23]

The printed line ``--q class=value ...`` is what ``aoc-replay-grid`` takes. The exit code is 0 when every class with
enough points was fitted.
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from aoc.analysis import grid
from aoc.fusion.alignment import Q_BY_CLASS
from aoc.fusion.discounting import alpha_of_age, position_spread
from aoc.fusion.noise import NoiseModel
from aoc.fusion.replay import load_run
from aoc.theory import reliability as theory

WARM_UP = 60.0
AGES = np.round(np.arange(0.1, 5.0 + 1e-9, 0.1), 1)           # s: the ages at which a report is used, up to the hold
EPSILON = theory.EPS
MIN_POINTS = 2000                                               # a class with fewer (row, age) points is not fitted
Q_GRID = np.logspace(-4, 3, 281)                                # m^2/s^3


def receivers_of(folder: str, info: dict, count: int) -> list[str]:
    """The egos of the events first, then the first other receivers of the export, up to ``count`` in all."""
    listed = info.get("receivers") or []
    egos = sorted(r for r in listed if str(r).startswith("ego"))
    others = sorted(r for r in listed if str(r) not in egos)
    return [str(r) for r in (egos + others)[:count]]


def truth_arrays(truth: pd.DataFrame) -> dict:
    """Per true object, its times and places sorted, for the interpolation."""
    out = {}
    for name, rows in truth.groupby("obj_id", sort=False):
        rows = rows.sort_values("t")
        out[str(name)] = (rows["t"].to_numpy(dtype=float), rows["x"].to_numpy(dtype=float), rows["y"].to_numpy(dtype=float))
    return out


def hits_of(rows: pd.DataFrame, arrays: dict, ages: np.ndarray = AGES, epsilon: float = EPSILON) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each received row and each age: the hit (the predicted place within eps of the true place at t_meas + age),
    the spread of the predicted place without q, and whether the true object is there at that time.

    Returns (hit, spread, valid), each of shape (rows, ages)."""
    n, m = len(rows), len(ages)
    x, y, vx, vy = (rows[c].to_numpy(dtype=float) for c in ("x", "y", "vx", "vy"))
    t_meas = rows["t_meas"].to_numpy(dtype=float)
    names = rows["gt_match_id"].astype(str).to_numpy()
    hit = np.zeros((n, m), dtype=bool)
    valid = np.zeros((n, m), dtype=bool)
    t_use = t_meas[:, None] + ages[None, :]
    px, py = x[:, None] + ages[None, :] * vx[:, None], y[:, None] + ages[None, :] * vy[:, None]
    for name in np.unique(names):
        where = np.flatnonzero(names == name)
        if name not in arrays:
            continue
        tt, tx, ty = arrays[name]
        tu = t_use[where]
        inside = (tu >= tt[0] - 1e-9) & (tu <= tt[-1] + 1e-9)
        ix, iy = np.interp(tu, tt, tx), np.interp(tu, tt, ty)
        d = np.hypot(px[where] - ix, py[where] - iy)
        hit[where] = inside & (d <= epsilon)
        valid[where] = inside
    spread = np.column_stack([position_spread(rows.assign(age_now=float(a))) for a in ages]) if n else np.zeros((0, m))
    return hit, spread, valid


def log_likelihood(q: float, hit: np.ndarray, spread: np.ndarray, age: np.ndarray, epsilon: float = EPSILON) -> float:
    a = np.clip(alpha_of_age(spread, age, np.full(spread.shape, float(q)), epsilon), 1e-9, 1.0 - 1e-9)
    return float(np.sum(np.where(hit, np.log(a), np.log1p(-a))))


def fit_q(hit: np.ndarray, spread: np.ndarray, age: np.ndarray, epsilon: float = EPSILON) -> tuple[float, float]:
    """The q that maximises the likelihood: the best point of the logarithmic grid, refined by a golden-section search
    on log q between its neighbours. Returns (q, log-likelihood)."""
    values = np.array([log_likelihood(q, hit, spread, age, epsilon) for q in Q_GRID])
    k = int(np.argmax(values))
    lo, hi = np.log(Q_GRID[max(k - 1, 0)]), np.log(Q_GRID[min(k + 1, len(Q_GRID) - 1)])
    golden = (np.sqrt(5.0) - 1.0) / 2.0
    c, d = hi - golden * (hi - lo), lo + golden * (hi - lo)
    fc, fd = log_likelihood(np.exp(c), hit, spread, age, epsilon), log_likelihood(np.exp(d), hit, spread, age, epsilon)
    for _ in range(40):
        if fc > fd:
            hi, d, fd = d, c, fc
            c = hi - golden * (hi - lo)
            fc = log_likelihood(np.exp(c), hit, spread, age, epsilon)
        else:
            lo, c, fc = c, d, fd
            d = lo + golden * (hi - lo)
            fd = log_likelihood(np.exp(d), hit, spread, age, epsilon)
    q = float(np.exp((lo + hi) / 2.0))
    return q, log_likelihood(q, hit, spread, age, epsilon)


def calibration_error(alpha: np.ndarray, hit: np.ndarray, bins: int = 10) -> float:
    """The calibration error of a reliability curve against the hits: the mean over 10 bins of |mean alpha - hit rate|, weighted."""
    index = np.clip((np.clip(alpha, 0.0, 1.0) * bins).astype(int), 0, bins - 1)
    count = np.bincount(index, minlength=bins)
    mean_a = np.bincount(index, weights=alpha, minlength=bins)
    mean_h = np.bincount(index, weights=hit.astype(float), minlength=bins)
    good = count > 0
    return float(np.sum(np.abs(mean_a[good] - mean_h[good])) / max(1, hit.size)) if good.any() else float("nan")


def read_run(folder: str, receivers: int = 5, rows_per_class: int = 20000, seed: int = 0, ages: np.ndarray = AGES, warm_up: float = WARM_UP) -> dict:
    """The points of one run: per class, the hits, the spreads and the ages of the sampled received rows (after the warm-up)."""
    folder = os.fspath(folder)
    if not os.path.isfile(os.path.join(folder, "export_summary.json")) and os.path.isfile(os.path.join(folder, "parquet", "export_summary.json")):
        folder = os.path.join(folder, "parquet")
    import json
    with open(os.path.join(folder, "export_summary.json"), encoding="utf-8") as fh:
        info = json.load(fh)
    chosen = receivers_of(folder, info, receivers) or None
    run = load_run(folder, noise=NoiseModel(), missing="drop", receivers=chosen)
    obs = run.obs
    v2x = obs[(obs["src"] == "v2x").to_numpy(dtype=bool, na_value=False) & (obs["t_rx"].to_numpy(dtype=float) >= warm_up)]
    if chosen is not None:
        v2x = v2x[v2x["rx_station"].isin(chosen)]
    v2x = v2x[v2x["gt_match_id"].notna() & v2x["class"].notna()]
    arrays = truth_arrays(run.truth)
    out = dict(run=grid._number_of_folder(os.path.basename(os.path.dirname(folder) if os.path.basename(folder) == "parquet" else folder)),
               folder=folder, receivers=chosen, classes={})
    rng = np.random.default_rng(seed)
    for cls, rows in v2x.groupby("class", sort=False):
        cls = str(cls)
        if len(rows) > rows_per_class:
            rows = rows.iloc[np.sort(rng.choice(len(rows), rows_per_class, replace=False))]
        hit, spread, valid = hits_of(rows, arrays, ages)
        age = np.broadcast_to(ages[None, :], hit.shape)
        out["classes"][cls] = dict(rows=int(len(rows)), hit=hit[valid], spread=spread[valid], age=age[valid])
    return out


def fit_classes(parts: list[dict], epsilon: float = EPSILON) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pools the points of the runs per class, fits q, and gives the table per class and the curves per class and age bin."""
    pooled: dict[str, dict] = {}
    for part in parts:
        for cls, data in part["classes"].items():
            p = pooled.setdefault(cls, dict(hit=[], spread=[], age=[], rows=0, runs=0))
            p["hit"].append(data["hit"])
            p["spread"].append(data["spread"])
            p["age"].append(data["age"])
            p["rows"] += data["rows"]
            p["runs"] += 1
    table, curves = [], []
    for cls in sorted(pooled):
        p = pooled[cls]
        hit, spread, age = (np.concatenate(p[k]) if p[k] else np.zeros(0) for k in ("hit", "spread", "age"))
        hit = hit.astype(bool)
        q_assumed = Q_BY_CLASS.get(cls)
        row = dict(cls=cls, runs=p["runs"], rows=p["rows"], points=int(hit.size), q_assumed=q_assumed, q_fitted=None, ll_assumed=None,
                   ll_fitted=None, ce_assumed=None, ce_fitted=None, half_life_assumed=None, half_life_fitted=None, fitted=False)
        if hit.size >= MIN_POINTS and q_assumed is not None:
            q_fit, ll_fit = fit_q(hit, spread, age, epsilon)
            ll_as = log_likelihood(q_assumed, hit, spread, age, epsilon)
            a_as = alpha_of_age(spread, age, np.full(spread.shape, q_assumed), epsilon)
            a_fit = alpha_of_age(spread, age, np.full(spread.shape, q_fit), epsilon)
            spread0 = float(np.median(spread[age <= 0.1 + 1e-9])) if (age <= 0.1 + 1e-9).any() else float(np.median(spread))
            sx = np.sqrt(max(spread0, 1e-12))
            row.update(q_fitted=round(q_fit, 6), ll_assumed=round(ll_as, 1), ll_fitted=round(ll_fit, 1),
                       ce_assumed=round(calibration_error(a_as, hit), 4), ce_fitted=round(calibration_error(a_fit, hit), 4),
                       half_life_assumed=round(float(theory.half_life(sx, 0.0, 0.0, q_assumed, epsilon)), 3),
                       half_life_fitted=round(float(theory.half_life(sx, 0.0, 0.0, q_fit, epsilon)), 3), fitted=True)
            for a in np.unique(np.round(age, 1)):
                sel = np.isclose(age, a)
                curves.append(dict(cls=cls, age=float(a), n=int(sel.sum()), hit_rate=round(float(hit[sel].mean()), 4),
                                   alpha_assumed=round(float(a_as[sel].mean()), 4), alpha_fitted=round(float(a_fit[sel].mean()), 4)))
        table.append(row)
    return pd.DataFrame(table), pd.DataFrame(curves)


def q_options(table: pd.DataFrame) -> str:
    return " ".join("--q %s=%.6g" % (r.cls, r.q_fitted) for r in table.itertuples() if r.fitted)


def format_table(table: pd.DataFrame) -> str:
    lines = ["%-16s %5s %8s %9s %10s %10s %7s %7s %8s %8s" % ("class", "runs", "rows", "points", "q_assumed", "q_fitted", "ce_as", "ce_fit", "hl_as", "hl_fit")]
    for r in table.itertuples():
        f = lambda v, fmt: "-" if v is None or (isinstance(v, float) and np.isnan(v)) else fmt % v
        lines.append("%-16s %5d %8d %9d %10s %10s %7s %7s %8s %8s" % (r.cls, r.runs, r.rows, r.points, f(r.q_assumed, "%.4g"), f(r.q_fitted, "%.4g"),
                                                                     f(r.ce_assumed, "%.4f"), f(r.ce_fitted, "%.4f"), f(r.half_life_assumed, "%.2f"),
                                                                     f(r.half_life_fitted, "%.2f")))
    lines.append("q in m^2/s^3; ce = calibration error of the curve against the hits (10 bins); hl = half-life in s with the median spread of a fresh report")
    return "\n".join(lines)


def draw(curves: pd.DataFrame, path: str) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    if curves.empty:
        return False
    classes = sorted(curves["cls"].unique())
    fig, axes = plt.subplots(1, len(classes), figsize=(4.0 * len(classes), 3.4), squeeze=False)
    for ax, cls in zip(axes[0], classes):
        c = curves[curves["cls"] == cls]
        ax.plot(c["age"], c["hit_rate"], "o", ms=3, color="#333333", label="measured")
        ax.plot(c["age"], c["alpha_assumed"], "--", color="#1f77b4", label="assumed q")
        ax.plot(c["age"], c["alpha_fitted"], "-", color="#d62728", label="fitted q")
        ax.set_title(cls)
        ax.set_xlabel("age of the report (s)")
        ax.set_ylim(0, 1.02)
        ax.grid(alpha=0.3)
    axes[0][0].set_ylabel("share of reports within 1.5 m")
    axes[0][0].legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def _read_one(args) -> dict:
    folder, receivers, rows, seed, warm_up = args
    try:
        return read_run(folder, receivers, rows, seed, warm_up=warm_up)
    except Exception as exc:
        return dict(run=None, folder=folder, receivers=None, classes={}, error="%s: %s" % (type(exc).__name__, exc))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fit the motion value q of the reliability curve per class on the calibration runs of the grid (task D3).")
    ap.add_argument("root", help="the results folder of the grid (holds the run folders aoc-grid-<n>)")
    ap.add_argument("--seeds", default="10,11", help="the seeds of the calibration runs (default 10,11)")
    ap.add_argument("--runs", default=None, help="only these run numbers (default: every run of the calibration seeds)")
    ap.add_argument("--receivers", type=int, default=5, help="receivers read per run: the egos of the events first, then others (default 5)")
    ap.add_argument("--rows", type=int, default=20000, help="received rows sampled per class and run (default 20000)")
    ap.add_argument("--warmup", type=float, default=WARM_UP, help="rows received before this time are left out (default 60 s)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="runs read in parallel")
    ap.add_argument("--out", default=None, help="write the table per class to this CSV file")
    ap.add_argument("--curves", default=None, help="write the hit rate and the two curves per class and age to this CSV file")
    ap.add_argument("--figure", default=None, help="draw the curves to this file (needs matplotlib)")
    args = ap.parse_args(argv)
    seeds = {int(s) for s in args.seeds.split(",") if s.strip()}
    numbers = None
    if args.runs:
        from aoc.analysis.gridcheck import parse_numbers
        numbers = set(parse_numbers(args.runs))
    folders = []
    for name in sorted(os.listdir(args.root)) if os.path.isdir(args.root) else []:
        n = grid._number_of_folder(name)
        if n is None or n >= grid.N_RUNS:
            continue
        if numbers is not None and n not in numbers:
            continue
        if numbers is None and grid.cell_of_run(n)["seed"] not in seeds:
            continue
        if os.path.isfile(os.path.join(args.root, name, "parquet", "export_summary.json")):
            folders.append(os.path.join(args.root, name, "parquet"))
    if not folders:
        print("no exported calibration run under %s" % args.root)
        return 1
    print("reading %d run(s) with %d job(s): %d receivers and up to %d rows per class each, ages 0.1 to %.1f s" % (
        len(folders), args.jobs, args.receivers, args.rows, AGES[-1]), flush=True)
    work = [(f, args.receivers, args.rows, k, args.warmup) for k, f in enumerate(folders)]
    parts = None
    if args.jobs > 1:
        try:
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                parts = list(pool.map(_read_one, work))
        except (OSError, PermissionError) as exc:
            print("parallel reading not possible (%s); reading one by one" % exc, flush=True)
    if parts is None:
        parts = [_read_one(w) for w in work]
    for p in parts:
        if p.get("error"):
            print("  run %s: %s" % (p.get("run"), p["error"]))
    table, curves = fit_classes([p for p in parts if not p.get("error")])
    print(format_table(table))
    if args.out:
        table.to_csv(args.out, index=False)
        print("written: %s" % args.out)
    if args.curves:
        curves.to_csv(args.curves, index=False)
        print("written: %s" % args.curves)
    if args.figure:
        print("figure: %s" % (args.figure if draw(curves, args.figure) else "not drawn (matplotlib is not installed, or no class fitted)"))
    options = q_options(table)
    print("for aoc-replay-grid: %s" % (options or "(no class fitted)"))
    return 0 if len(table) and table["fitted"].all() else 1


if __name__ == "__main__":
    sys.exit(main())
