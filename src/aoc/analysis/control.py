"""The analysis of the control grid (task E3): H4 per pair of rules and the points of figure F9.

The control grid (``docs/control_grid.md``) runs five generation rules of the CP service (``etsi``, ``etsi_abs``,
``aoc``, ``aoc2``, ``aocdev``) under the same conditions: a condition is a density, a penetration and a load level
(12 conditions), each repeated with 4 seeds. The rules of one condition and seed see the same traffic, so the
analysis pairs them by seed. For each run it takes from the raw logs (``aoc.analysis.rulecheck``):

    load      the channel busy ratio seen by the receivers (the mean over the received CPMs of a receiver, then over
              the receivers) and the CPM bytes per sender-second of the run (without the empty CPMs: the bytes of
              the CPMs that carry something, over the time the equipped cars were present), with the CPM rate
    service   the share of the time an object without V2X (a vehicle that sent no CPM, or a pedestrian) within
              200 m of a receiver is known with a reliability alpha(age) at or above the target 0.9
              (``time_above_target``, pooled over the object ticks of every receiver), and the share of the time it
              is known at all (``known``)

H4 (the plan): the target reached with at least 20 % less channel load, or more time above the target at equal load.
On a pair (a rule against a reference rule, same condition and seed) the verdict is PASS when the rule's load is at
most 80 % of the reference's and its time above the target is not lower (within one point), or when its time above
the target is higher by more than one point at a load not higher (within one percent). The load side of the pair is
the busy ratio when the condition has no load station, and the CPM bytes per sender-second when it has (the load
stations set most of the busy ratio there; ``docs/control_grid.md``). Both verdicts are in the tables. A condition
passes when its mean pair over the seeds passes (``verdict``); ``verdict_ci`` asks more: the whole paired interval
over the seeds (Student's t) must pass. A rule passes H4 against a reference when every condition passes, and
``h4.csv`` also pools the seed pairs of a rule (48 on the full grid): the count that pass and one-sided Wilcoxon
tests of the two sides of the rule.

    aoc-control ROOT [--out results/e3] [--warmup 60] [--range 200] [--hold 5] [--jobs 4] [--reference etsi,etsi_abs] [--refresh]

reads the exported runs under ``ROOT`` (``aoc-control-<n>/parquet``), keeps the per-run metrics in ``runs.csv`` (a run
already there with the same export and the same options is not read again), and writes ``pairs.csv`` (one row per
run, rule and reference), ``cells.csv`` (one row per condition and rule, the mean over the seeds common to the
rules of the condition: the points of F9), ``cell_pairs.csv`` (the verdicts per condition), ``h4.csv``,
``frontier.csv`` (the points on the Pareto frontier, over the grid, per load level and per condition) and
``summary.txt``.
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

from aoc.analysis import grid, rulecheck
from aoc.analysis.database import mean_ci

LOAD_LESS = rulecheck.H4_LOAD_LESS               # the plan: at least 20 % less load
TOLERANCE = rulecheck.H4_TOLERANCE               # one point on the time above the target, one percent on the load
verdict = rulecheck.h4_verdict                   # the verdict of one pair, shared with aoc-rulecheck --against
REFERENCES = ("etsi", "etsi_abs")
AOC_RULES = ("aoc", "aoc2", "aocdev")
CONDITION = ["density", "pen", "load"]           # a condition of the grid: the rules are compared inside it
OPTION_COLUMNS = ["exported", "warmup", "range_m", "hold"]                 # what the cache checks before it reuses a row
RUN_COLUMNS = ["run", "density", "pen", "rule", "load", "seed", "seconds", "senders", "present_s", "cpm_rate_hz", "bytes_per_s",
               "bytes_per_s_without_empty", "bytes_per_sender", "objects_per_cpm", "cbr_mean", "receivers", "receivers_exposed", "pairs",
               "time_above_target", "known", "time_above_target_receivers"] + OPTION_COLUMNS
LOAD_MEASURES = {"cbr": "cbr_mean", "bytes": "bytes_per_sender"}       # the load side of a pair: the busy ratio, or the CPM bytes per sender-second
METRICS = ["cpm_rate_hz", "bytes_per_sender", "bytes_per_s_without_empty", "objects_per_cpm", "cbr_mean", "time_above_target", "known"]
RATIOS = {"bytes_ratio": "bytes_per_sender", "cbr_ratio": "cbr_mean", "cpm_ratio": "cpm_rate_hz"}
DIFFS = {"time_diff": "time_above_target", "known_diff": "known"}
SAVE_EVERY = 10                                  # runs read between two writes of the cache


# ---------------------------------------------------------------------------------------------------------------------
# the runs
# ---------------------------------------------------------------------------------------------------------------------
def find_runs(root: str, design: grid.Design = grid.CONTROL) -> pd.DataFrame:
    """The exported runs under root: run number, folder, cell and seed (from the run number), and the time of the export."""
    rows = []
    prefix = "aoc-%s-" % design.config
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not name.startswith(prefix) or not name[len(prefix):].isdigit():
            continue
        folder = os.path.join(root, name)
        summary = os.path.join(folder, "parquet", "export_summary.json")
        if not os.path.isfile(summary):
            continue
        n = int(name[len(prefix):])
        if n >= design.n_runs:
            continue
        cell = design.cell_of_run(n)
        rows.append(dict(run=n, folder=folder, **{k: cell[k] for k in design.names}, seed=cell["seed"], exported=round(os.path.getmtime(summary), 1)))
    return pd.DataFrame(rows, columns=["run", "folder"] + list(design.names) + ["seed", "exported"]).sort_values("run").reset_index(drop=True)


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
    present = s.get("present_s", np.nan)
    with_presence = np.isfinite(present) and present > 0
    out = dict(run=run, **{k: cell[k] for k in design.names}, seed=cell["seed"], seconds=s["seconds"], senders=s["senders"], present_s=present,
               # the rates per sender-second when truth gives the presence of the senders; per sender over the run otherwise
               cpm_rate_hz=s["cpm_rate_hz_present"] if with_presence else s["cpm_rate_hz"],
               bytes_per_s=s["bytes_per_s"], bytes_per_s_without_empty=s["bytes_per_s_without_empty"],
               bytes_per_sender=s["bytes_per_sender_s"] if with_presence else (round(s["bytes_per_s_without_empty"] / s["senders"], 1) if s["senders"] else np.nan),
               objects_per_cpm=s["objects_per_cpm"], cbr_mean=s["cbr_mean"], receivers=s["receivers"], receivers_exposed=s["receivers_exposed"],
               pairs=s["pairs"], time_above_target=s["time_above_target"], known=s["known"], time_above_target_receivers=s["time_above_target_receivers"],
               exported=np.nan, warmup=warmup, range_m=range_m, hold=hold)
    return out


def _metrics_job(args):
    folder, run, warmup, range_m, hold, exported = args
    try:
        out = run_metrics(folder, run, warmup, range_m, hold)
        out["exported"] = exported
        return out
    except Exception as exc:                                        # one bad run does not stop the grid; it is reported
        return dict(run=run, error="%s: %s" % (type(exc).__name__, exc))


def collect(root: str, out: str | None = None, warmup: float = 60.0, range_m: float = 200.0, hold: float = 5.0, jobs: int = 1,
            design: grid.Design = grid.CONTROL, refresh: bool = False, log=print) -> pd.DataFrame:
    """The metrics of every exported run under root. A run already in out/runs.csv with the same export time and the same options is
    kept; the others are read (and written to the cache as they come, so a crash loses little)."""
    found = find_runs(root, design)
    cache_path = os.path.join(out, "runs.csv") if out else None
    cached = pd.read_csv(cache_path) if cache_path and os.path.isfile(cache_path) and not refresh else pd.DataFrame(columns=RUN_COLUMNS)
    for col in OPTION_COLUMNS:
        if col not in cached.columns:
            cached[col] = np.nan
    if len(cached):
        fresh = found.set_index("run")["exported"]
        same_options = np.isclose(cached["warmup"].astype(float), warmup) & np.isclose(cached["range_m"].astype(float), range_m) & \
            np.isclose(cached["hold"].astype(float), hold)
        same_export = cached["run"].map(fresh).notna() & np.isclose(cached["exported"].astype(float), cached["run"].map(fresh).astype(float), rtol=0, atol=1.0)
        cached = cached[same_options & same_export].drop_duplicates("run", keep="last")
    todo = found[~found["run"].isin(set(cached["run"]))]
    log("runs exported: %d, in the cache: %d, to read: %d" % (len(found), len(cached), len(todo)))
    work = [(f, int(n), warmup, range_m, hold, float(e)) for f, n, e in zip(todo["folder"], todo["run"], todo["exported"])]
    rows, errors = [], []

    def save():
        parts = [t for t in (cached, pd.DataFrame(rows, columns=RUN_COLUMNS)) if len(t)]
        table = pd.concat(parts, ignore_index=True) if parts else cached
        table = table.sort_values("run").reset_index(drop=True)
        if cache_path and len(table):
            os.makedirs(out, exist_ok=True)
            table.to_csv(cache_path, index=False)
        return table

    def take(k, r):
        if "error" in r:
            errors.append(r)
            log("  run %d: %s" % (r["run"], r["error"]))
        else:
            rows.append(r)
        if k % SAVE_EVERY == 0 or k == len(work):
            log("  read %d of %d" % (k, len(work)))
            save()

    if work and jobs > 1:
        try:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                futures = [pool.submit(_metrics_job, w) for w in work]
                for k, future in enumerate(as_completed(futures), 1):
                    take(k, future.result())
        except BrokenExecutor as exc:                               # a worker died (out of memory, say): keep what is there
            log("the workers stopped (%s): %d run(s) read, the rest is left for the next call" % (exc, len(rows)))
    elif work:
        for k, w in enumerate(work, 1):
            take(k, _metrics_job(w))
    runs = save()
    if errors:
        log("%d run(s) could not be read" % len(errors))
    return runs


# ---------------------------------------------------------------------------------------------------------------------
# the pairs, the cells and the verdicts
# ---------------------------------------------------------------------------------------------------------------------
def load_measure(load: int) -> str:
    """The load side of a pair in a condition: the busy ratio without load stations, the CPM bytes with them."""
    return "cbr" if int(load) == 0 else "bytes"


def _check_unique(runs: pd.DataFrame):
    if not runs["run"].is_unique:
        raise ValueError("a run number appears twice in the runs table: %s" % sorted(runs.loc[runs["run"].duplicated(), "run"].unique()))


def pairs(runs: pd.DataFrame, references=REFERENCES, log=None) -> pd.DataFrame:
    """One row per run and reference: the rule against the reference rule of the same condition and seed. A run whose reference is
    missing gives no row (and a line of the log)."""
    _check_unique(runs)
    key = CONDITION + ["seed"]
    rows, missing = [], []
    for ref in references:
        base = runs[runs["rule"] == ref].set_index(key)
        for _, r in runs[runs["rule"] != ref].iterrows():
            k = tuple(r[c] for c in key)
            if k not in base.index:
                missing.append((ref,) + k)
                continue
            b = base.loc[k]
            row = dict(density=r["density"], pen=r["pen"], load=r["load"], seed=r["seed"], rule=r["rule"], reference=ref, run=r["run"], run_ref=b["run"])
            for m in METRICS:
                row[m] = r[m]
                row[m + "_ref"] = b[m]
            for name, m in RATIOS.items():
                row[name] = r[m] / b[m] if (np.isfinite(b[m]) and b[m]) else np.nan
            for name, m in DIFFS.items():
                row[name] = r[m] - b[m]
            row["verdict_cbr"] = verdict(row["cbr_ratio"], row["time_diff"])
            row["verdict_bytes"] = verdict(row["bytes_ratio"], row["time_diff"])
            row["load_measure"] = load_measure(r["load"])
            row["load_ratio"] = row["cbr_ratio"] if row["load_measure"] == "cbr" else row["bytes_ratio"]
            row["verdict"] = row["verdict_cbr"] if row["load_measure"] == "cbr" else row["verdict_bytes"]
            rows.append(row)
    if missing and log:
        shown = sorted(set((m[0], m[1], m[2], m[3]) for m in missing))
        text = "; ".join("%s missing in %s/%s/%s" % m for m in shown[:12]) + (" ..." if len(shown) > 12 else "")
        log("no reference run for %d run(s): %s" % (len(missing), text))
    columns = ["density", "pen", "load", "seed", "rule", "reference", "run", "run_ref"] + [c for m in METRICS for c in (m, m + "_ref")] + \
              list(RATIOS) + list(DIFFS) + ["verdict_cbr", "verdict_bytes", "load_measure", "load_ratio", "verdict"]
    return pd.DataFrame(rows, columns=columns)


def balanced(runs: pd.DataFrame, log=None) -> pd.DataFrame:
    """The runs of each condition restricted to the seeds that every rule present in the condition has, so that the means of the
    rules rest on the same traffic."""
    _check_unique(runs)
    keep, dropped = [], []
    for cond, part in runs.groupby(CONDITION, sort=False):
        seeds = None
        for _, rp in part.groupby("rule"):
            seeds = set(rp["seed"]) if seeds is None else seeds & set(rp["seed"])
        inside = part["seed"].isin(seeds or set())
        keep.append(part[inside])
        if (~inside).any():
            dropped.append("%s/%s/%s: seeds %s" % (cond + (sorted(int(s) for s in part.loc[~inside, "seed"].unique()),)))
    if dropped and log:
        log("runs left out of the means, their seed is not run for every rule of the condition: " + "; ".join(dropped))
    return pd.concat(keep, ignore_index=True) if keep else runs.iloc[0:0]


def cells(runs: pd.DataFrame, log=None) -> pd.DataFrame:
    """One row per condition and rule: the mean over the common seeds of each metric, with its interval and its range (the points of F9)."""
    runs = balanced(runs, log)
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


def _log_mean_ci(values):
    """The geometric mean of ratios and its interval (Student's t on the logs)."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v) & (v > 0)]
    if not v.size:
        return np.nan, np.nan, np.nan, 0
    m, lo, hi, n = mean_ci(np.log(v))
    return float(np.exp(m)), float(np.exp(lo)) if n >= 2 else np.nan, float(np.exp(hi)) if n >= 2 else np.nan, n


def cell_pairs(pair_table: pd.DataFrame) -> pd.DataFrame:
    """One row per condition, rule and reference: the mean pair over the seeds (the ratios as geometric means) with its paired interval,
    the verdict on the mean, the verdict on the whole interval, and the count of seed pairs that pass."""
    rows = []
    for (density, pen, load, rule, ref), part in pair_table.groupby(["density", "pen", "load", "rule", "reference"], sort=False):
        row = dict(density=density, pen=pen, load=load, rule=rule, reference=ref, seeds=int(len(part)))
        for c in RATIOS:
            m, lo, hi, _ = _log_mean_ci(part[c])
            row[c], row[c + "_lo"], row[c + "_hi"] = round(m, 4), round(lo, 4), round(hi, 4)
        for c in list(DIFFS) + ["time_above_target", "time_above_target_ref"]:
            m, lo, hi, n = mean_ci(part[c])
            row[c] = round(m, 4)
            row[c + "_lo"], row[c + "_hi"] = (round(lo, 4), round(hi, 4)) if n >= 2 else (np.nan, np.nan)
        row["time_diff_min"], row["time_diff_max"] = round(float(part["time_diff"].min()), 4), round(float(part["time_diff"].max()), 4)
        row["verdict_cbr"] = verdict(row["cbr_ratio"], row["time_diff"])
        row["verdict_bytes"] = verdict(row["bytes_ratio"], row["time_diff"])
        row["load_measure"] = load_measure(load)
        lr = "cbr_ratio" if row["load_measure"] == "cbr" else "bytes_ratio"
        row["load_ratio"], row["load_ratio_lo"], row["load_ratio_hi"] = row[lr], row[lr + "_lo"], row[lr + "_hi"]
        row["verdict"] = row["verdict_cbr"] if row["load_measure"] == "cbr" else row["verdict_bytes"]
        # the strict reading: the worst end of the paired intervals must pass too (one seed gives no interval: n/a)
        row["verdict_ci"] = verdict(row["load_ratio_hi"], row["time_diff_lo"]) if np.isfinite(row["time_diff_lo"]) else "n/a"
        row["seed_pairs_pass"] = int((part["verdict"] == "PASS").sum())
        rows.append(row)
    return pd.DataFrame(rows)


def _wilcoxon_greater(values) -> float:
    """One-sided Wilcoxon signed-rank p-value for 'the median is above zero' (nan when scipy or the data are short)."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size < 5:
        return np.nan
    try:
        from scipy import stats
        if np.all(v == 0):
            return 1.0
        return float(stats.wilcoxon(v, alternative="greater", zero_method="zsplit").pvalue)
    except (ImportError, ValueError):
        return np.nan


def h4_table(cell_pair_table: pd.DataFrame, pair_table: pd.DataFrame | None = None, design: grid.Design = grid.CONTROL) -> pd.DataFrame:
    """Per rule and reference: the conditions that pass (in all, per load level, on the interval), the pooled seed pairs with one-sided
    Wilcoxon tests of the two sides of H4, and the verdict of H4 (every condition of the design passes)."""
    expected = 1
    for name in CONDITION:
        expected *= len(design.factors[name])
    rows = []
    for (rule, ref), part in cell_pair_table.groupby(["rule", "reference"], sort=False):
        row = dict(rule=rule, reference=ref, conditions=int(len(part)), conditions_expected=expected, conditions_pass=int((part["verdict"] == "PASS").sum()),
                   conditions_pass_ci=int((part["verdict_ci"] == "PASS").sum()))
        for load in design.factors["load"]:
            p = part[part["load"] == load]
            row["pass_load_%d" % load] = "%d/%d" % (int((p["verdict"] == "PASS").sum()), len(p))
        row["load_ratio_mean"] = round(float(np.exp(np.log(part["load_ratio"].clip(lower=1e-12)).mean())), 3) if len(part) else np.nan
        row["load_ratio_max"] = round(float(part["load_ratio"].max()), 3)
        row["time_diff_mean"] = round(float(part["time_diff"].mean()), 4)
        row["time_diff_min"] = round(float(part["time_diff"].min()), 4)
        if pair_table is not None and len(pair_table):
            sp = pair_table[(pair_table["rule"] == rule) & (pair_table["reference"] == ref)]
            row["seed_pairs"] = int(len(sp))
            row["seed_pairs_pass"] = int((sp["verdict"] == "PASS").sum())
            # less load: log(0.8) - log(ratio) > 0; the time held: time_diff + 0.01 > 0
            row["p_less_load"] = round(_wilcoxon_greater(np.log(LOAD_LESS) - np.log(sp["load_ratio"].clip(lower=1e-12))), 4)
            row["p_time_held"] = round(_wilcoxon_greater(sp["time_diff"] + TOLERANCE), 4)
        complete = row["conditions"] == expected
        row["h4"] = ("PASS" if row["conditions_pass"] == row["conditions"] else "FAIL") if complete else "incomplete (%d/%d)" % (row["conditions"], expected)
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
    """The points on the frontier for each load measure: over the whole grid ('all'), within each load level ('load n') and within each
    condition ('condition d/p/l': the five rules under the same conditions)."""
    columns = ["measure", "scope", "density", "pen", "rule", "load", "x", "time_above_target"]
    rows = []
    if not len(cell_table):
        return pd.DataFrame(columns=columns)
    for measure, col in LOAD_MEASURES.items():
        scopes = [("all", cell_table)] + [("load %d" % load, cell_table[cell_table["load"] == load]) for load in sorted(cell_table["load"].unique())]
        scopes += [("condition %s/%s/%s" % cond, part) for cond, part in cell_table.groupby(CONDITION, sort=False)]
        for scope, part in scopes:
            on = part[frontier(part, col)]
            for _, r in on.sort_values(col).iterrows():
                rows.append(dict(measure=measure, scope=scope, density=r["density"], pen=r["pen"], rule=r["rule"], load=r["load"], x=r[col],
                                 time_above_target=r["time_above_target"]))
    return pd.DataFrame(rows, columns=columns)


def condition_frontier_counts(front: pd.DataFrame, measure: str) -> dict:
    """Per rule: in how many conditions the rule is on the frontier of its condition, and the count of conditions."""
    f = front[(front["measure"] == measure) & front["scope"].str.startswith("condition")]
    conditions = f["scope"].nunique()
    return dict(conditions=int(conditions), per_rule={r: int(f[f["rule"] == r]["scope"].nunique()) for r in grid.CONTROL.factors["rule"]})


def format_summary(runs: pd.DataFrame, cell_table: pd.DataFrame, cell_pair_table: pd.DataFrame, h4: pd.DataFrame, front: pd.DataFrame,
                   design: grid.Design = grid.CONTROL) -> str:
    lines = ["the control grid: %s" % design.describe(),
             "runs read: %d of %d; conditions (density/pen/load) with at least one run: %d of 12; seeds per condition and rule in the means: %s" % (
                 len(runs), design.n_runs, runs.groupby(CONDITION).ngroups if len(runs) else 0,
                 "none" if not len(cell_table) else "%d to %d" % (cell_table["seeds"].min(), cell_table["seeds"].max())),
             "the metric: the share of the time an object without V2X within 200 m is known with alpha(age) >= 0.9 (pooled over the object ticks)",
             "the load: the busy ratio (mean over the received CPMs of a receiver, then over the receivers) and the CPM bytes per sender-second", ""]
    if len(cell_table):
        lines.append("per rule, the mean over the conditions (and the range over the conditions):")
        for rule in design.factors["rule"]:
            c = cell_table[cell_table["rule"] == rule]
            if c.empty:
                continue
            lines.append("  %-9s time above target %.3f (%.3f to %.3f), known %.3f, CBR %.3f (%.3f to %.3f), bytes per sender-second %.0f (%.0f to %.0f), "
                         "CPM rate %.2f Hz" % (
                             rule, c["time_above_target"].mean(), c["time_above_target"].min(), c["time_above_target"].max(), c["known"].mean(),
                             c["cbr_mean"].mean(), c["cbr_mean"].min(), c["cbr_mean"].max(), c["bytes_per_sender"].mean(), c["bytes_per_sender"].min(),
                             c["bytes_per_sender"].max(), c["cpm_rate_hz"].mean()))
        lines.append("")
    if len(h4):
        lines.append("H4 per rule and reference (a condition passes when its mean pair over the seeds passes; the load side is the busy ratio at load 0, "
                     "the CPM bytes per sender-second at load 12 and 36; p_less_load and p_time_held: one-sided Wilcoxon over the seed pairs, "
                     "the load at most 80 % and the time within one point):")
        lines.append("  " + h4.to_string(index=False).replace("\n", "\n  "))
        lines.append("")
    if len(cell_pair_table):
        failed = cell_pair_table[cell_pair_table["verdict"] != "PASS"]
        if len(failed):
            lines.append("conditions that do not pass:")
            for _, r in failed.iterrows():
                lines.append("  %s/%s/%d, %s against %s: load ratio %.3f (%s), time above target %+.4f (seed pairs passing: %d of %d)" % (
                    r["density"], r["pen"], r["load"], r["rule"], r["reference"], r["load_ratio"], r["load_measure"], r["time_diff"],
                    r["seed_pairs_pass"], r["seeds"]))
            lines.append("")
    if len(front):
        lines.append("the frontier (the points no other point beats on both the load and the time above the target):")
        for measure in LOAD_MEASURES:
            for scope in [s for s in front["scope"].unique() if not s.startswith("condition")]:
                f = front[(front["measure"] == measure) & (front["scope"] == scope)]
                if f.empty:
                    continue
                counts = f["rule"].value_counts()
                names = ", ".join("%s %d" % (r, counts[r]) for r in design.factors["rule"] if r in counts)
                lines.append("  %s, %s: %d point(s): %s" % (measure, scope, len(f), names))
            cf = condition_frontier_counts(front, measure)
            names = ", ".join("%s %d" % (r, k) for r, k in cf["per_rule"].items() if k)
            lines.append("  %s, within each condition (the five rules at the same conditions, %d condition(s)): on the frontier in: %s" % (
                measure, cf["conditions"], names or "none"))
    return "\n".join(lines)


def analyse(root: str, out: str, warmup: float = 60.0, range_m: float = 200.0, hold: float = 5.0, jobs: int = 1, references=REFERENCES,
            design: grid.Design = grid.CONTROL, refresh: bool = False, log=print) -> dict:
    unknown = [r for r in references if r not in design.factors["rule"]]
    if unknown:
        raise ValueError("unknown reference rule(s) %s; the rules are %s" % (", ".join(unknown), ", ".join(design.factors["rule"])))
    runs = collect(root, out, warmup, range_m, hold, jobs, design, refresh, log)
    pair_table = pairs(runs, references, log) if len(runs) else pd.DataFrame()
    cell_table = cells(runs, log) if len(runs) else pd.DataFrame()
    cp = cell_pairs(pair_table) if len(pair_table) else pd.DataFrame()
    h4 = h4_table(cp, pair_table, design) if len(cp) else pd.DataFrame()
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
    ap.add_argument("--refresh", action="store_true", help="read every run again, whatever the cache holds")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.root):
        print("ERROR: %s is not a folder" % args.root)
        return 2
    try:
        result = analyse(args.root, args.out, args.warmup, args.range, args.hold, args.jobs, tuple(args.reference.split(",")), refresh=args.refresh)
    except ValueError as exc:
        print("ERROR: %s" % exc)
        return 2
    print(result["summary"])
    print("written: %s (runs.csv, pairs.csv, cells.csv, cell_pairs.csv, h4.csv, frontier.csv, summary.txt)" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
