"""The analysis of the control grid (task E3): H4 per pair of rules and the points of figure F9.

The control grid (``docs/control_grid.md``) runs five generation rules of the CP service (``etsi``, ``etsi_abs``,
``aoc``, ``aoc2``, ``aocdev``) in the same cells (density x pen x load x seed). The rules of one cell and seed see the
same traffic, so the analysis pairs them by seed. For each run it takes from the raw logs (``aoc.analysis.rulecheck``):

    load      the channel busy ratio seen by the receivers (the mean over the received CPMs) and the CPM bytes per
              second of the run (without the empty CPMs), with the CPM rate per sender
    service   the share of the time an object without V2X within 200 m of a receiver is known with a reliability
              alpha(age) at or above the target 0.9 (``time_above_target``, pooled over the object ticks of every
              receiver), and the share of the time it is known at all (``known``)

H4 (the plan): the target reached with at least 20 % less channel load, or more time above the target at equal load.
On a pair (a rule against a reference rule, same cell and seed) the verdict is PASS when the rule's load is at most
80 % of the reference's and its time above the target is not lower (within one point), or when its time above the
target is higher by more than one point at a load not higher (within one point). The load side of the pair is the
busy ratio when the cell has no load station, and the CPM bytes per second when it has (the load stations set most of
the busy ratio there; ``docs/control_grid.md``). Both verdicts are in the tables. A cell passes when its mean pair
over the seeds passes; a rule passes H4 against a reference when every cell passes.

    aoc-control ROOT [--out results/e3] [--warmup 60] [--range 200] [--hold 5] [--jobs 4] [--reference etsi,etsi_abs]

reads the exported runs under ``ROOT`` (``aoc-control-<n>/parquet``), keeps the per-run metrics in ``runs.csv`` (a run
already there is not read again), and writes ``pairs.csv`` (one row per run, rule and reference), ``cells.csv`` (one
row per cell and rule, the mean over the seeds: the points of F9), ``cell_pairs.csv`` (the verdicts per cell),
``frontier.csv`` (the cells on the Pareto frontier, time above the target against each load measure) and
``summary.txt``.
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from aoc.analysis import grid, rulecheck
from aoc.analysis.database import mean_ci

LOAD_LESS = 0.8                                  # the plan: at least 20 % less load
TOLERANCE = rulecheck.H4_TOLERANCE               # one point on the time above the target, one percent on the load
REFERENCES = ("etsi", "etsi_abs")
AOC_RULES = ("aoc", "aoc2", "aocdev")
RUN_COLUMNS = ["run", "density", "pen", "rule", "load", "seed", "seconds", "senders", "cpm_rate_hz", "bytes_per_s", "bytes_per_s_without_empty",
               "bytes_per_sender", "objects_per_cpm", "cbr_mean", "receivers", "receivers_exposed", "pairs", "time_above_target", "known",
               "time_above_target_receivers"]
LOAD_MEASURES = {"cbr": "cbr_mean", "bytes": "bytes_per_sender"}       # the load side of a pair: the busy ratio, or the CPM bytes per sender
METRICS = ["cpm_rate_hz", "bytes_per_sender", "bytes_per_s_without_empty", "objects_per_cpm", "cbr_mean", "time_above_target", "known"]


# ---------------------------------------------------------------------------------------------------------------------
# the runs
# ---------------------------------------------------------------------------------------------------------------------
def find_runs(root: str, design: grid.Design = grid.CONTROL) -> pd.DataFrame:
    """The exported runs under root: run number, folder, cell and seed (from the run number, checked against the variables)."""
    rows = []
    prefix = "aoc-%s-" % design.config
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not name.startswith(prefix) or not name[len(prefix):].isdigit():
            continue
        folder = os.path.join(root, name)
        if not os.path.isfile(os.path.join(folder, "parquet", "export_summary.json")):
            continue
        n = int(name[len(prefix):])
        if n >= design.n_runs:
            continue
        cell = design.cell_of_run(n)
        rows.append(dict(run=n, folder=folder, **{k: cell[k] for k in design.names}, seed=cell["seed"]))
    return pd.DataFrame(rows, columns=["run", "folder"] + list(design.names) + ["seed"]).sort_values("run").reset_index(drop=True)


def run_metrics(folder: str, run: int, warmup: float = 60.0, range_m: float = 200.0, hold: float = 5.0, rule: rulecheck.Rule | None = None,
                design: grid.Design = grid.CONTROL) -> dict:
    """The metrics of one exported run (the summary of rulecheck, the objects without V2X), with its cell."""
    rule = rule or rulecheck.Rule()
    result = rulecheck.analyse(folder, rule, warmup, range_m, hold, objects="unequipped")
    s = result["summary"]
    cell = design.cell_of_run(run)
    read = design.cell_from_variables(s.get("variables") or {})
    if read is not None and any(read[k] != cell[k] for k in design.names):
        raise ValueError("run %d: the variables of the run (%s) are not the cell of its run number (%s)" % (run, read, cell))
    out = dict(run=run, **{k: cell[k] for k in design.names}, seed=cell["seed"], seconds=s["seconds"], senders=s["senders"], cpm_rate_hz=s["cpm_rate_hz"],
               bytes_per_s=s["bytes_per_s"], bytes_per_s_without_empty=s["bytes_per_s_without_empty"],
               bytes_per_sender=round(s["bytes_per_s_without_empty"] / s["senders"], 1) if s["senders"] else np.nan,
               objects_per_cpm=s["objects_per_cpm"], cbr_mean=s["cbr_mean"], receivers=s["receivers"], receivers_exposed=s["receivers_exposed"],
               pairs=s["pairs"], time_above_target=s["time_above_target"], known=s["known"], time_above_target_receivers=s["time_above_target_receivers"])
    return out


def _metrics_job(args):
    folder, run, warmup, range_m, hold = args
    try:
        return run_metrics(folder, run, warmup, range_m, hold)
    except Exception as exc:                                        # one bad run does not stop the grid; it is reported
        return dict(run=run, error="%s: %s" % (type(exc).__name__, exc))


def collect(root: str, out: str | None = None, warmup: float = 60.0, range_m: float = 200.0, hold: float = 5.0, jobs: int = 1,
            design: grid.Design = grid.CONTROL, log=print) -> pd.DataFrame:
    """The metrics of every exported run under root; the runs already in out/runs.csv are kept, the others read."""
    found = find_runs(root, design)
    cache_path = os.path.join(out, "runs.csv") if out else None
    cached = pd.read_csv(cache_path) if cache_path and os.path.isfile(cache_path) else pd.DataFrame(columns=RUN_COLUMNS)
    cached = cached[cached["run"].isin(set(found["run"]))] if len(cached) else cached
    todo = found[~found["run"].isin(set(cached["run"]))]
    log("runs exported: %d, in the cache: %d, to read: %d" % (len(found), len(cached), len(todo)))
    work = [(f, int(n), warmup, range_m, hold) for f, n in zip(todo["folder"], todo["run"])]
    rows, errors = [], []
    if work:
        if jobs > 1:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                results = pool.map(_metrics_job, work)
        else:
            results = map(_metrics_job, work)
        for k, r in enumerate(results, 1):
            if "error" in r:
                errors.append(r)
                log("  run %d: %s" % (r["run"], r["error"]))
            else:
                rows.append(r)
            if k % 10 == 0 or k == len(work):
                log("  read %d of %d" % (k, len(work)))
    runs = pd.concat([cached, pd.DataFrame(rows, columns=RUN_COLUMNS)], ignore_index=True) if rows else cached
    runs = runs.sort_values("run").reset_index(drop=True)
    if cache_path and len(runs):
        os.makedirs(out, exist_ok=True)
        runs.to_csv(cache_path, index=False)
    if errors:
        log("%d run(s) could not be read" % len(errors))
    return runs


# ---------------------------------------------------------------------------------------------------------------------
# the pairs, the cells and the verdicts
# ---------------------------------------------------------------------------------------------------------------------
def verdict(load_ratio: float, time_diff: float) -> str:
    """H4 on one pair: PASS when the rule has at least 20 % less load for the same time above the target (within one
    point), or more time above the target (by more than one point) at a load not higher (within one percent)."""
    if not (np.isfinite(load_ratio) and np.isfinite(time_diff)):
        return "n/a"
    eps = 1e-9                                                   # a difference of exactly one point, computed in floating point, is one point
    less_load = load_ratio <= LOAD_LESS + eps and time_diff >= -TOLERANCE - eps
    more_time = time_diff > TOLERANCE + eps and load_ratio <= 1 + TOLERANCE + eps
    return "PASS" if (less_load or more_time) else "FAIL"


def load_measure(load: int) -> str:
    """The load side of a pair in a cell: the busy ratio without load stations, the CPM bytes with them."""
    return "cbr" if int(load) == 0 else "bytes"


def pairs(runs: pd.DataFrame, references=REFERENCES) -> pd.DataFrame:
    """One row per run and reference: the rule against the reference rule of the same cell and seed."""
    key = ["density", "pen", "load", "seed"]
    rows = []
    for ref in references:
        base = runs[runs["rule"] == ref].set_index(key)
        if base.index.has_duplicates:
            raise ValueError("the reference %s appears twice in a cell and seed" % ref)
        for _, r in runs[runs["rule"] != ref].iterrows():
            k = tuple(r[c] for c in key)
            if k not in base.index:
                continue
            b = base.loc[k]
            row = dict(density=r["density"], pen=r["pen"], load=r["load"], seed=r["seed"], rule=r["rule"], reference=ref, run=r["run"], run_ref=b["run"])
            for m in METRICS:
                row[m] = r[m]
                row[m + "_ref"] = b[m]
            row["bytes_ratio"] = r["bytes_per_sender"] / b["bytes_per_sender"] if b["bytes_per_sender"] else np.nan
            row["cbr_ratio"] = r["cbr_mean"] / b["cbr_mean"] if b["cbr_mean"] else np.nan
            row["cpm_ratio"] = r["cpm_rate_hz"] / b["cpm_rate_hz"] if b["cpm_rate_hz"] else np.nan
            row["time_diff"] = r["time_above_target"] - b["time_above_target"]
            row["known_diff"] = r["known"] - b["known"]
            row["verdict_cbr"] = verdict(row["cbr_ratio"], row["time_diff"])
            row["verdict_bytes"] = verdict(row["bytes_ratio"], row["time_diff"])
            row["load_measure"] = load_measure(r["load"])
            row["load_ratio"] = row["cbr_ratio"] if row["load_measure"] == "cbr" else row["bytes_ratio"]
            row["verdict"] = row["verdict_cbr"] if row["load_measure"] == "cbr" else row["verdict_bytes"]
            rows.append(row)
    columns = ["density", "pen", "load", "seed", "rule", "reference", "run", "run_ref"] + [c for m in METRICS for c in (m, m + "_ref")] + \
              ["bytes_ratio", "cbr_ratio", "cpm_ratio", "time_diff", "known_diff", "verdict_cbr", "verdict_bytes", "load_measure", "load_ratio", "verdict"]
    return pd.DataFrame(rows, columns=columns)


def cells(runs: pd.DataFrame) -> pd.DataFrame:
    """One row per cell and rule: the mean over the seeds of each metric, with its interval and its range (the points of F9)."""
    rows = []
    for (density, pen, rule, load), part in runs.groupby(["density", "pen", "rule", "load"], sort=False):
        row = dict(density=density, pen=pen, rule=rule, load=load, seeds=int(len(part)), seed_list=",".join(str(s) for s in sorted(part["seed"])))
        for m in METRICS:
            mean, lo, hi, n = mean_ci(part[m])
            row[m] = round(mean, 4) if np.isfinite(mean) else np.nan
            row[m + "_lo"], row[m + "_hi"] = (round(lo, 4), round(hi, 4)) if n >= 2 else (np.nan, np.nan)
            row[m + "_min"], row[m + "_max"] = round(float(part[m].min()), 4), round(float(part[m].max()), 4)
        row["pairs"] = int(part["pairs"].sum())
        rows.append(row)
    order = {r: i for i, r in enumerate(grid.CONTROL.factors["rule"])}
    out = pd.DataFrame(rows)
    if len(out):
        out = out.assign(_r=out["rule"].map(order)).sort_values(["density", "pen", "load", "_r"]).drop(columns="_r").reset_index(drop=True)
    return out


def cell_pairs(pair_table: pd.DataFrame) -> pd.DataFrame:
    """One row per cell, rule and reference: the mean pair over the seeds and its verdict, with the count of seed pairs that pass."""
    rows = []
    for (density, pen, load, rule, ref), part in pair_table.groupby(["density", "pen", "load", "rule", "reference"], sort=False):
        row = dict(density=density, pen=pen, load=load, rule=rule, reference=ref, seeds=int(len(part)))
        for c in ("bytes_ratio", "cbr_ratio", "cpm_ratio", "time_diff", "known_diff", "time_above_target", "time_above_target_ref"):
            row[c] = round(float(part[c].mean()), 4)
        row["time_diff_min"], row["time_diff_max"] = round(float(part["time_diff"].min()), 4), round(float(part["time_diff"].max()), 4)
        row["verdict_cbr"] = verdict(row["cbr_ratio"], row["time_diff"])
        row["verdict_bytes"] = verdict(row["bytes_ratio"], row["time_diff"])
        row["load_measure"] = load_measure(load)
        row["load_ratio"] = row["cbr_ratio"] if row["load_measure"] == "cbr" else row["bytes_ratio"]
        row["verdict"] = row["verdict_cbr"] if row["load_measure"] == "cbr" else row["verdict_bytes"]
        row["seed_pairs_pass"] = int((part["verdict"] == "PASS").sum())
        rows.append(row)
    return pd.DataFrame(rows)


def h4_table(cell_pair_table: pd.DataFrame) -> pd.DataFrame:
    """Per rule and reference: the cells that pass, in all and per load level; the verdict of H4 (every cell passes)."""
    rows = []
    for (rule, ref), part in cell_pair_table.groupby(["rule", "reference"], sort=False):
        row = dict(rule=rule, reference=ref, cells=int(len(part)), cells_pass=int((part["verdict"] == "PASS").sum()))
        for load in sorted(part["load"].unique()):
            p = part[part["load"] == load]
            row["pass_load_%d" % load] = "%d/%d" % (int((p["verdict"] == "PASS").sum()), len(p))
        row["load_ratio_mean"] = round(float(part["load_ratio"].mean()), 3)
        row["load_ratio_max"] = round(float(part["load_ratio"].max()), 3)
        row["time_diff_mean"] = round(float(part["time_diff"].mean()), 4)
        row["time_diff_min"] = round(float(part["time_diff"].min()), 4)
        row["h4"] = "PASS" if row["cells"] and row["cells_pass"] == row["cells"] else "FAIL"
        rows.append(row)
    return pd.DataFrame(rows)


def frontier(points: pd.DataFrame, x: str, y: str = "time_above_target") -> pd.Series:
    """True for the points no other point dominates (a lower or equal x with a higher or equal y, one of them strict)."""
    xv, yv = points[x].to_numpy(dtype=float), points[y].to_numpy(dtype=float)
    keep = np.zeros(len(points), dtype=bool)
    for i in range(len(points)):
        if not (np.isfinite(xv[i]) and np.isfinite(yv[i])):
            continue
        better = (xv <= xv[i]) & (yv >= yv[i]) & ((xv < xv[i]) | (yv > yv[i]))
        keep[i] = not better.any()
    return pd.Series(keep, index=points.index, name="frontier")


def frontier_table(cell_table: pd.DataFrame) -> pd.DataFrame:
    """The cells on the frontier for each load measure, over the whole grid and within each load level."""
    rows = []
    if not len(cell_table):
        return pd.DataFrame(columns=["measure", "scope", "density", "pen", "rule", "load", "x", "time_above_target"])
    for measure, col in LOAD_MEASURES.items():
        scopes = [("all", cell_table)] + [("load %d" % load, cell_table[cell_table["load"] == load]) for load in sorted(cell_table["load"].unique())]
        for scope, part in scopes:
            on = part[frontier(part, col)]
            for _, r in on.sort_values(col).iterrows():
                rows.append(dict(measure=measure, scope=scope, density=r["density"], pen=r["pen"], rule=r["rule"], load=r["load"], x=r[col],
                                 time_above_target=r["time_above_target"]))
    return pd.DataFrame(rows)


def format_summary(runs: pd.DataFrame, cell_table: pd.DataFrame, cell_pair_table: pd.DataFrame, h4: pd.DataFrame, front: pd.DataFrame,
                   design: grid.Design = grid.CONTROL) -> str:
    lines = ["the control grid: %s" % design.describe(),
             "runs read: %d of %d; cells with at least one run: %d of %d; seeds per cell: %s" % (
                 len(runs), design.n_runs, len(cell_table), design.n_cells,
                 "none" if not len(cell_table) else "%d to %d" % (cell_table["seeds"].min(), cell_table["seeds"].max())),
             "the metric: the share of the time an object without V2X within 200 m is known with alpha(age) >= 0.9 (pooled over the object ticks)", ""]
    if len(cell_table):
        lines.append("per rule, the mean over the cells (and the range over the cells):")
        for rule in design.factors["rule"]:
            c = cell_table[cell_table["rule"] == rule]
            if c.empty:
                continue
            lines.append("  %-9s time above target %.3f (%.3f to %.3f), known %.3f, CBR %.3f (%.3f to %.3f), bytes/s per sender %.0f (%.0f to %.0f), "
                         "CPM rate %.2f Hz" % (
                             rule, c["time_above_target"].mean(), c["time_above_target"].min(), c["time_above_target"].max(), c["known"].mean(),
                             c["cbr_mean"].mean(), c["cbr_mean"].min(), c["cbr_mean"].max(), c["bytes_per_sender"].mean(), c["bytes_per_sender"].min(),
                             c["bytes_per_sender"].max(), c["cpm_rate_hz"].mean()))
        lines.append("")
    if len(h4):
        lines.append("H4 per rule and reference (a cell passes when its mean pair over the seeds passes; the load side is the busy ratio at load 0, the CPM bytes "
                     "per sender at load 12 and 36):")
        lines.append("  " + h4.to_string(index=False).replace("\n", "\n  "))
        lines.append("")
    if len(cell_pair_table):
        failed = cell_pair_table[cell_pair_table["verdict"] != "PASS"]
        if len(failed):
            lines.append("cells that do not pass:")
            for _, r in failed.iterrows():
                lines.append("  %s/%s/%s/%d against %s: load ratio %.3f (%s), time above target %+.4f (seed pairs passing: %d of %d)" % (
                    r["density"], r["pen"], r["rule"], r["load"], r["reference"], r["load_ratio"], r["load_measure"], r["time_diff"],
                    r["seed_pairs_pass"], r["seeds"]))
            lines.append("")
    if len(front):
        lines.append("the frontier (the cells no other cell beats on both the load and the time above the target):")
        for measure in LOAD_MEASURES:
            for scope in front["scope"].unique():
                f = front[(front["measure"] == measure) & (front["scope"] == scope)]
                if f.empty:
                    continue
                counts = f["rule"].value_counts()
                names = ", ".join("%s %d" % (r, counts[r]) for r in design.factors["rule"] if r in counts)
                lines.append("  %s, %s: %d point(s): %s" % (measure, scope, len(f), names))
    return "\n".join(lines)


def analyse(root: str, out: str, warmup: float = 60.0, range_m: float = 200.0, hold: float = 5.0, jobs: int = 1, references=REFERENCES,
            design: grid.Design = grid.CONTROL, log=print) -> dict:
    runs = collect(root, out, warmup, range_m, hold, jobs, design, log)
    pair_table = pairs(runs, references) if len(runs) else pd.DataFrame()
    cell_table = cells(runs) if len(runs) else pd.DataFrame()
    cp = cell_pairs(pair_table) if len(pair_table) else pd.DataFrame()
    h4 = h4_table(cp) if len(cp) else pd.DataFrame()
    front = frontier_table(cell_table) if len(cell_table) else pd.DataFrame()
    text = format_summary(runs, cell_table, cp, h4, front, design)
    os.makedirs(out, exist_ok=True)
    for name, table in (("pairs", pair_table), ("cells", cell_table), ("cell_pairs", cp), ("h4", h4), ("frontier", front)):
        table.to_csv(os.path.join(out, name + ".csv"), index=False)
    with open(os.path.join(out, "summary.txt"), "w") as fh:
        fh.write(text + "\n")
    return dict(runs=runs, pairs=pair_table, cells=cell_table, cell_pairs=cp, h4=h4, frontier=front, summary=text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Analyse the control grid (task E3): H4 per pair of rules and the points of figure F9.")
    ap.add_argument("root", help="the results folder of the control grid (holds the run folders aoc-control-<n>)")
    ap.add_argument("--out", default="results/e3", help="the output folder (default: results/e3); runs.csv there is the cache of the per-run metrics")
    ap.add_argument("--warmup", type=float, default=60.0, help="the seconds left out at the start of each run (default: 60)")
    ap.add_argument("--range", type=float, default=200.0, help="the range of the receiver metric, m")
    ap.add_argument("--hold", type=float, default=5.0, help="a report older than this is forgotten by the receiver metric, s")
    ap.add_argument("--jobs", type=int, default=1, help="runs read in parallel")
    ap.add_argument("--reference", default=",".join(REFERENCES), help="the reference rules of the pairs (default: etsi,etsi_abs)")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.root):
        print("ERROR: %s is not a folder" % args.root)
        return 2
    result = analyse(args.root, args.out, args.warmup, args.range, args.hold, args.jobs, tuple(args.reference.split(",")))
    print(result["summary"])
    print("written: %s (runs.csv, pairs.csv, cells.csv, cell_pairs.csv, h4.csv, frontier.csv, summary.txt)" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
