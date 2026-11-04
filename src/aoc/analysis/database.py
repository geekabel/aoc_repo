"""The results database of the core grid (task D4): the tables of D3 aggregated per cell, with confidence intervals,
and the tests of the hypotheses H1 and H3 of the plan.

Inputs (the small tables brought back from the workstation, ``results/``):

  - ``d3/metrics.csv``: one row per run, way and hold (the metrics of B8), with the cell, the seed and the kind;
  - ``d3/bins.csv``: the reliability diagram per run, way and hold;
  - ``d3/events.csv``: the belief of the egos in their pedestrian during the occlusion, per event and way;
  - ``d2/gridcheck_runs.csv``: the facts of each run (the penetration as drawn, the cars, the channel busy ratio of
    the received CPMs, the CPM rate), joined to the metrics by the run number.

Outputs (``results/d4/``):

  - ``runs.csv``: the metrics per run, way and hold with the facts of the run (the base table);
  - ``cells.csv``: per cell (density, penetration, message configuration), hold and way: the mean of each metric over
    the evaluation seeds with its 95 % confidence interval (Student's t over the seeds), and the number of runs;
  - ``factors.csv``: the same per level of one factor (density, penetration, message configuration) and way;
  - ``h1.csv``: per cell, the relative gain of B3 on the calibration error against B0, B1, B2 and B4 (paired by
    seed), with its confidence interval, the regime of the cell (aged: B0's error at least H1_FRESH; else fresh) and
    the verdict of the plan (aged: at least 30 % against B0 and B1; fresh: not worse than B0 in every run);
    ``h1_overall.csv`` the same over every evaluation run;
  - ``h3.csv``: per cell, the stale-confidence rate of B3 against B0, B1 and B2 (paired by seed), the ratio of the
    means, the awareness of B3 against the other, and the verdict of the plan (under B0 in every run, halved, the
    awareness within 2 points); ``h3_overall.csv``;
  - ``holds.csv``: per hold and way, the metrics over the runs of the hold check (the seeds 0 and 1 of every cell);
  - ``reliability.csv``: the reliability diagram per hold and way summed over the evaluation runs (the data of
    figure F5), and ``reliability_pen.csv`` per penetration;
  - ``events.csv``: per cell and way, the belief in the hidden pedestrian during the occlusion (mean, lowest, at
    the end, with V2X and with the own sensors), the CPM rows in the gap;
  - ``summary.md``: the numbers of the papers in prose and tables.

    aoc-database [--results results] [--out results/d4]

The calibration runs (seeds 10 and 11) are left out of every aggregate: they served the fit of q. The exit code is
0 when the tables are written; the verdicts are in the tables, not in the exit code.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

from aoc.analysis import grid
from aoc.analysis.metrics import WAYS

CELL = ["density", "pen", "msg"]
METRICS = ["calibration_error", "brier", "coverage", "sharpness", "awareness_ratio", "rmse", "stale_rate", "ghost_rate", "time_to_confident",
           "confident_share", "wrong_groups", "cbr_mean", "pdr_mean", "age_median", "age_p95", "fused_objects"]
FACTS = ["pen_share", "cars_mean", "background_eligible", "rate_median_hz", "cbr_p95", "delay_median_ms", "cpm_rx_after_warmup"]
H1_GAIN = 0.30                                   # the plan: the calibration error of B3 at least 30 % below B0 and B1 where objects get old
H1_FRESH = 0.05                                  # a cell where B0's calibration error stays under this is a cell where every object is fresh
H3_RATIO = 0.5                                   # the plan: the stale-confidence rate of B3 halved against B0 ...
H3_AWARENESS = 0.02                              # ... with the awareness within 2 points
EVENT_COLUMNS = ["v2x_gap_mean", "v2x_gap_min", "v2x_gap_end", "own_gap_end", "own_gap_min", "cpm_rows_in_gap", "groups_max"]


def mean_ci(values, level: float = 0.95) -> tuple[float, float, float, int]:
    """The mean of the values and its confidence interval (Student's t), with the count of finite values."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    n = int(v.size)
    if n == 0:
        return np.nan, np.nan, np.nan, 0
    m = float(v.mean())
    if n < 2:
        return m, np.nan, np.nan, n
    half = float(stats.t.ppf(0.5 + level / 2.0, n - 1) * v.std(ddof=1) / np.sqrt(n))
    return m, m - half, m + half, n


def _order(table: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Sorts a table by the order of the grid (density, penetration, message configuration, way, hold)."""
    ranks = {"density": {d: i for i, d in enumerate(grid.DENSITIES)}, "pen": {p: i for i, p in enumerate(grid.PENETRATIONS)},
             "msg": {m: i for i, m in enumerate(grid.MESSAGES)}, "way": {w: i for i, w in enumerate(WAYS)}}
    key = [table[k].map(ranks[k]).fillna(99) if k in ranks else table[k] for k in keys if k in table.columns]
    if not key:
        return table
    order = np.lexsort([k.to_numpy() for k in reversed(key)])
    return table.iloc[order].reset_index(drop=True)


def load(results: str) -> dict:
    """The tables of D2 and D3 from the results folder."""
    d3, d2 = os.path.join(results, "d3"), os.path.join(results, "d2")
    out = dict(metrics=pd.read_csv(os.path.join(d3, "metrics.csv")))
    for name, columns in (("bins", ["run", "kind", "hold", "way", "bin", "pen"]), ("events", ["run", "kind", "way"])):
        path = os.path.join(d3, name + ".csv")
        out[name] = pd.read_csv(path) if os.path.isfile(path) else pd.DataFrame(columns=columns)
    facts_path = os.path.join(d2, "gridcheck_runs.csv")
    out["facts"] = pd.read_csv(facts_path) if os.path.isfile(facts_path) else pd.DataFrame(columns=["run"])
    if out["facts"]["run"].duplicated().any():
        raise ValueError("%s: a run appears twice" % facts_path)
    if out["metrics"].duplicated(["run", "way", "hold"]).any():
        raise ValueError("%s: a run, way and hold appears twice" % os.path.join(d3, "metrics.csv"))
    qfit_path = os.path.join(d3, "qfit.csv")
    out["qfit"] = pd.read_csv(qfit_path) if os.path.isfile(qfit_path) else pd.DataFrame()
    return out


def runs_table(metrics: pd.DataFrame, facts: pd.DataFrame) -> pd.DataFrame:
    """The metrics per run, way and hold, with the facts of the run from the check of D2."""
    keep = [c for c in FACTS if c in facts.columns]
    table = metrics.merge(facts[["run"] + keep], on="run", how="left") if len(facts) and keep else metrics.copy()
    return _order(table, ["hold", *CELL, "seed", "way"])


def aggregate(table: pd.DataFrame, keys: list[str], columns: list[str]) -> pd.DataFrame:
    """The mean and the confidence interval of each column per group of keys, over the evaluation runs."""
    rows = []
    for values, part in table.groupby(keys, sort=False):
        row = dict(zip(keys, values if isinstance(values, tuple) else (values,)))
        row["runs"] = int(part["run"].nunique())
        for c in columns:
            if c in part.columns:
                m, lo, hi, n = mean_ci(part[c])
                row[c] = round(m, 5) if np.isfinite(m) else np.nan
                row[c + "_lo"], row[c + "_hi"] = (round(lo, 5), round(hi, 5)) if np.isfinite(lo) else (np.nan, np.nan)
        rows.append(row)
    return _order(pd.DataFrame(rows, columns=keys + ["runs"] + columns if not rows else None), keys)


def paired_gain(table: pd.DataFrame, keys: list[str], metric: str, way: str, against: str, relative: bool = True) -> pd.DataFrame:
    """Per group, the gain of ``way`` on ``metric`` against another way, paired by run: 1 - way / against per run when
    relative (the runs where the other way has a value of 0 are left out), else against - way; then the mean and the
    confidence interval over the runs (NaN under 2 runs), the share of those runs where ``way`` is not worse, the lowest
    gain, and the means of both ways over them. A way absent from the table gives an empty result; a run that appears
    twice for a way is an error (``pivot`` refuses it)."""
    columns = keys + ["against", "runs", "gain", "gain_lo", "gain_hi", "gain_min", "not_worse_share", "way_mean", "against_mean"]
    if way not in set(table["way"]) or against not in set(table["way"]):
        return pd.DataFrame(columns=columns)
    pivot = table.pivot(index=keys + ["run"], columns="way", values=metric)
    rows = []
    for values, part in pivot.groupby(level=keys if len(keys) > 1 else keys[0], sort=False):
        a, b = part[way].to_numpy(dtype=float), part[against].to_numpy(dtype=float)
        ok = np.isfinite(a) & np.isfinite(b)
        if relative:
            ok &= b > 0
        gain = 1.0 - a[ok] / b[ok] if relative else b[ok] - a[ok]
        m, lo, hi, n = mean_ci(gain)
        row = dict(zip(keys, values if isinstance(values, tuple) else (values,)))
        row.update(against=against, runs=int(ok.sum()), gain=round(m, 4), gain_lo=round(lo, 4) if np.isfinite(lo) else np.nan,
                   gain_hi=round(hi, 4) if np.isfinite(hi) else np.nan, gain_min=round(float(gain.min()), 4) if ok.any() else np.nan,
                   not_worse_share=round(float(np.mean(a[ok] <= b[ok])), 4) if ok.any() else np.nan,
                   way_mean=round(float(a[ok].mean()), 5) if ok.any() else np.nan, against_mean=round(float(b[ok].mean()), 5) if ok.any() else np.nan)
        rows.append(row)
    return _order(pd.DataFrame(rows, columns=columns), keys)


def _verdict(passed, applies) -> np.ndarray:
    """PASS or FAIL where the rule applies and the test could be made, else n/a."""
    passed = np.asarray(passed, dtype=object)
    return np.where(applies, np.where(pd.isna(passed), "n/a", np.where(passed.astype(bool), "PASS", "FAIL")), "n/a")


def _h1_verdict(table: pd.DataFrame) -> pd.DataFrame:
    """The verdict of H1 per row: in a cell where objects get old (B0's calibration error at least H1_FRESH, ``regime``
    aged), the lower end of the gain at least H1_GAIN against B0 and B1; in a cell where every object is fresh, B3 not
    worse than B0 in every run. The second rule is also given on its own against B0 (``not_worse``)."""
    keys = table.columns[:table.columns.get_loc("against")].tolist()
    b0 = table[table["against"] == "B0"][keys + ["against_mean"]].rename(columns={"against_mean": "b0_error"})
    table = table.merge(b0, on=keys, how="left")
    table["regime"] = np.where(table["b0_error"].isna(), "n/a", np.where(table["b0_error"] >= H1_FRESH, "aged", "fresh"))
    gain_ok = pd.Series(np.where(table["gain_lo"].isna(), None, table["gain_lo"] >= H1_GAIN), index=table.index, dtype=object)
    worse_ok = pd.Series(np.where(table["not_worse_share"].isna(), None, table["not_worse_share"] >= 1.0), index=table.index, dtype=object)
    table["verdict"] = _verdict(np.where(table["regime"] == "fresh", worse_ok, gain_ok), table["against"].isin(["B0", "B1"]))
    table["not_worse"] = _verdict(worse_ok, table["against"] == "B0")
    return table


def h1_tables(runs: pd.DataFrame, hold: float = 5.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """H1 per cell and overall: the gain of B3 on the calibration error against B0, B1, B2 and B4."""
    base = runs[(runs["hold"] == hold) & (runs["kind"] == "evaluation")]
    parts, overall = [], []
    for against in ("B0", "B1", "B2", "B4"):
        parts.append(paired_gain(base, CELL, "calibration_error", "B3", against))
        o = paired_gain(base.assign(all="all"), ["all"], "calibration_error", "B3", against)
        overall.append(o)
    cells = _h1_verdict(pd.concat([p for p in parts if len(p)], ignore_index=True))
    over = _h1_verdict(pd.concat([p for p in overall if len(p)], ignore_index=True)).drop(columns="all")
    return _order(cells, CELL + ["against"]), over


def h3_tables(runs: pd.DataFrame, hold: float = 5.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """H3 per cell and overall: the stale-confidence rate of B3 against B0, B1 and B2."""
    base = runs[(runs["hold"] == hold) & (runs["kind"] == "evaluation")]
    parts, overall = [], []
    for against in ("B0", "B1", "B2"):
        parts.append(_h3_rows(base, CELL, against))
        overall.append(_h3_rows(base.assign(all="all"), ["all"], against))
    cells = pd.concat([p for p in parts if len(p)], ignore_index=True)
    over = pd.concat([p for p in overall if len(p)], ignore_index=True).drop(columns="all")
    return _order(cells, CELL + ["against"]), over


def _h3_rows(base: pd.DataFrame, keys: list[str], against: str) -> pd.DataFrame:
    """H3 against one way: the difference of the stale-confidence rate (the other minus B3) with its interval, the ratio
    of the means, the paired difference of the awareness (B3 minus the other) with its interval and its lowest value;
    the verdict against B0: B3 under B0 in every run, the ratio at most H3_RATIO and the awareness within H3_AWARENESS."""
    stale = paired_gain(base, keys, "stale_rate", "B3", against, relative=False)
    if not len(stale):
        return pd.DataFrame(columns=stale.columns.tolist()[:-2] + ["ratio", "way_mean", "against_mean", "awareness_diff", "awareness_diff_lo",
                                                                   "awareness_diff_hi", "awareness_diff_min", "verdict"])
    aware = paired_gain(base, keys, "awareness_ratio", against, "B3", relative=False)      # B3 minus the other, paired by run
    aware = aware.rename(columns={"gain": "awareness_diff", "gain_lo": "awareness_diff_lo", "gain_hi": "awareness_diff_hi", "gain_min": "awareness_diff_min"})
    table = stale.merge(aware[keys + ["awareness_diff", "awareness_diff_lo", "awareness_diff_hi", "awareness_diff_min"]], on=keys, how="left")
    table.insert(table.columns.get_loc("gain_min") + 1, "ratio", (table["way_mean"] / table["against_mean"].replace(0, np.nan)).round(4))
    tested = table["against"] == "B0"
    passed = pd.Series(np.where(table["not_worse_share"].isna(), None,
                                (table["not_worse_share"] >= 1.0) & (table["ratio"] <= H3_RATIO) & (table["awareness_diff"] >= -H3_AWARENESS)),
                       index=table.index, dtype=object)
    table["verdict"] = _verdict(passed, tested)
    return table


def reliability(bins: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """The reliability diagram summed over the evaluation runs, per the keys (hold and way, plus a factor if wanted)."""
    b = bins[bins["kind"] == "evaluation"].copy()
    if not len(b):
        return pd.DataFrame(columns=keys + ["bin", "low", "high", "count", "runs", "belief_mean", "share_there", "plausibility_mean", "share_of_objects"])
    b["sum_belief"] = b["belief_mean"].fillna(0.0) * b["count"]
    b["sum_there"] = b["share_there"].fillna(0.0) * b["count"]
    b["sum_pl"] = b["plausibility_mean"].fillna(0.0) * b["count"]
    g = b.groupby(keys + ["bin"], sort=False).agg(low=("low", "first"), high=("high", "first"), count=("count", "sum"), sum_belief=("sum_belief", "sum"),
                                                  sum_there=("sum_there", "sum"), sum_pl=("sum_pl", "sum"), runs=("run", "nunique")).reset_index()
    n = g["count"].replace(0, np.nan)
    g["belief_mean"] = (g["sum_belief"] / n).round(5)
    g["share_there"] = (g["sum_there"] / n).round(5)
    g["plausibility_mean"] = (g["sum_pl"] / n).round(5)
    g["share_of_objects"] = (g["count"] / g.groupby(keys)["count"].transform("sum")).round(5)
    return _order(g.drop(columns=["sum_belief", "sum_there", "sum_pl"]), keys + ["bin"])


def pooled_calibration_error(diagram: pd.DataFrame) -> float:
    """The calibration error of a summed reliability diagram: the gap between the mean belief and the share really there
    per bin, weighted by the objects of the bin (object-weighted, unlike the mean of the per-run errors)."""
    n = diagram["count"].to_numpy(dtype=float)
    if n.sum() == 0:
        return np.nan
    gap = (diagram["belief_mean"].fillna(0.0) - diagram["share_there"].fillna(0.0)).abs().to_numpy()
    return float((n * gap).sum() / n.sum())


def events_table(events: pd.DataFrame) -> pd.DataFrame:
    """Per cell and way (the ways of ``events.csv``: D3 wrote B0, B1, B3 and B4), the means and intervals over the events
    of the evaluation runs of the cell (``events``: 3 per run, so correlated within a run; ``runs``: the runs)."""
    e = events[events["kind"] == "evaluation"]
    if not len(e):
        return pd.DataFrame(columns=CELL + ["way", "runs", "events"])
    table = aggregate(e, CELL + ["way"], EVENT_COLUMNS + ["gap"])
    counts = e.groupby(CELL + ["way"], sort=False).size().rename("events").reset_index()
    table = table.merge(counts, on=CELL + ["way"], how="left")
    table.insert(table.columns.get_loc("runs") + 1, "events", table.pop("events"))
    return table


def summary_text(tables: dict, qfit: pd.DataFrame) -> str:
    """The numbers of the papers, in prose and tables."""
    runs, h1o, h3o, holds, rel = tables["runs"], tables["h1_overall"], tables["h3_overall"], tables["holds"], tables["reliability"]
    e5 = runs[(runs["hold"] == 5.0) & (runs["kind"] == "evaluation")]
    lines = ["# The results database of the core grid (task D4)", ""]
    lines.append("%d evaluation runs (%d cells x %d seeds) replayed with the hold of 5 s; %d runs with the holds of 2 and 10 s. "
                 "The calibration runs (seeds 10 and 11) served the fit of q and are left out. Every mean below weighs the runs equally "
                 "(each run one value); the reliability diagram weighs the objects." % (
                     e5["run"].nunique(), e5.groupby(CELL).ngroups, e5["seed"].nunique(), runs[(runs["hold"] != 5.0)]["run"].nunique()))
    if len(qfit):
        lines.append("")
        lines.append("## The motion value q (fitted on the calibration runs)")
        lines.append("")
        lines.append("| class | q assumed | q fitted | half-life assumed (s) | half-life fitted (s) | calibration error assumed | fitted |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for r in qfit.itertuples():
            lines.append("| %s | %.3g | %.3g | %.2f | %.2f | %.3f | %.3f |" % (r.cls, r.q_assumed, r.q_fitted, r.half_life_assumed, r.half_life_fitted,
                                                                             r.ce_assumed, r.ce_fitted))
    lines.append("")
    lines.append("## The five ways over the evaluation runs (hold 5 s, mean and 95 % interval over the runs)")
    lines.append("")
    lines.append("| way | calibration error | Brier | coverage | awareness | RMSE (m) | stale rate | ghost rate | time to confident (s) |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for way in WAYS:
        part = e5[e5["way"] == way]
        cells = []
        for c in ("calibration_error", "brier", "coverage", "awareness_ratio", "rmse", "stale_rate", "ghost_rate", "time_to_confident"):
            m, lo, hi, _ = mean_ci(part[c])
            cells.append("%.3f [%.3f, %.3f]" % (m, lo, hi) if np.isfinite(lo) else "%.3f" % m)
        lines.append("| %s | %s |" % (way, " | ".join(cells)))
    lines.append("")
    lines.append("## H1: the calibration error of B3 against the other ways (paired by run)")
    lines.append("")
    lines.append("| against | runs | gain of B3 (mean over the runs) | 95 %% interval | over the cells: mean, 95 %% interval | lowest run | "
                 "runs where B3 is not worse | verdict (at least %d %% against B0 and B1) |" % round(100 * H1_GAIN))
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    h1c = tables["h1"]
    for r in h1o.itertuples():
        cm, clo, chi, _ = mean_ci(h1c[h1c["against"] == r.against]["gain"])
        lines.append("| %s | %d | %.1f %% | [%.1f %%, %.1f %%] | %.1f %% [%.1f %%, %.1f %%] | %.1f %% | %.0f %% | %s |" % (
            r.against, r.runs, 100 * r.gain, 100 * r.gain_lo, 100 * r.gain_hi, 100 * cm, 100 * clo, 100 * chi, 100 * r.gain_min,
            100 * r.not_worse_share, r.verdict))
    lines.append("")
    lines.append("The interval over the runs treats the %d runs as one sample; the interval over the cells (the %d cell means) is the one "
                 "that respects the design. The verdict reads the lower end of the two-sided 95 %% interval of the cell (one-sided at "
                 "97.5 %%), with no correction for the %d cells. The rule of the plan applies where objects get old (B0's calibration "
                 "error at least %.2f: regime aged); where every object is fresh, B3 must not be worse than B0 in every run." % (
                     int(h1o["runs"].max()), h1c[h1c["against"] == "B0"].shape[0], h1c[h1c["against"] == "B0"].shape[0], H1_FRESH))
    for against in ("B0", "B1"):
        part = h1c[h1c["against"] == against]
        lines.append("")
        lines.append("Per cell against %s: %d of %d cells pass (%d aged, %d fresh); the smallest gain is %.1f %% (%s/%s/%s), its lower end %.1f %%." % (
            against, int((part["verdict"] == "PASS").sum()), len(part), int((part["regime"] == "aged").sum()), int((part["regime"] == "fresh").sum()),
            100 * part["gain"].min(), *part.loc[part["gain"].idxmin(), CELL].tolist(), 100 * part["gain_lo"].min()))
    lines.append("")
    lines.append("## H3: the stale-confidence rate of B3 against the other ways")
    lines.append("")
    lines.append("| against | B3 | the other | difference (the other minus B3), 95 %% interval | ratio B3 / the other | runs where B3 is not above | "
                 "awareness, B3 minus the other: mean [95 %% interval], lowest run | verdict (under B0 in every run, ratio at most %.1f, "
                 "awareness within %.0f points) |" % (H3_RATIO, 100 * H3_AWARENESS))
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in h3o.itertuples():
        lines.append("| %s | %.3f | %.3f | %.3f [%.3f, %.3f] | %.3f | %.0f %% | %+.4f [%+.4f, %+.4f], %+.4f | %s |" % (
            r.against, r.way_mean, r.against_mean, r.gain, r.gain_lo, r.gain_hi, r.ratio, 100 * r.not_worse_share, r.awareness_diff,
            r.awareness_diff_lo, r.awareness_diff_hi, r.awareness_diff_min, r.verdict))
    lines.append("")
    lines.append("## The hold (the %d runs of the hold check, the seeds 0 and 1 of every cell, at every hold)" % (
        int(holds["runs"].max()) if len(holds) else 0))
    lines.append("")
    lines.append("| hold (s) | " + " | ".join("%s: calibration error, stale rate" % w for w in WAYS) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in WAYS) + " |")
    for hold, part in (holds.groupby("hold", sort=True) if len(holds) else []):
        by = part.set_index("way")
        lines.append("| %g | " % hold + " | ".join("%.3f, %.3f" % (by.loc[w, "calibration_error"], by.loc[w, "stale_rate"]) if w in by.index else "-"
                                                  for w in WAYS) + " |")
    if not len(holds):
        lines.append("| - | " + " | ".join("no hold check" for _ in WAYS) + " |")
    lines.append("")
    lines.append("## The reliability diagram of B3 (hold 5 s, every evaluation run)")
    lines.append("")
    lines.append("| bin | objects | mean belief | share really there |")
    lines.append("| --- | --- | --- | --- |")
    r3 = rel[(rel["way"] == "B3") & (rel["hold"] == 5.0)]
    for r in r3.itertuples():
        lines.append("| %.1f to %.1f | %d | %s | %s |" % (r.low, r.high, r.count, "%.3f" % r.belief_mean if r.count else "-",
                                                         "%.3f" % r.share_there if r.count else "-"))
    lines.append("")
    lines.append("Calibration error pooled over the objects (the diagram above, every way): " + ", ".join(
        "%s %.3f" % (way, pooled_calibration_error(rel[(rel["way"] == way) & (rel["hold"] == 5.0)])) for way in WAYS
        if len(rel[(rel["way"] == way) & (rel["hold"] == 5.0)])) + ".")
    lines.append("")
    lines.append("## The factors (hold 5 s, B3 and B1)")
    lines.append("")
    f = tables["factors"]
    for factor in CELL:
        part = f[(f["factor"] == factor) & (f["way"].isin(["B1", "B3"]))]
        lines.append("| %s | way | calibration error | awareness | stale rate |" % factor)
        lines.append("| --- | --- | --- | --- | --- |")
        for r in part.itertuples():
            lines.append("| %s | %s | %.3f [%.3f, %.3f] | %.3f | %.3f |" % (r.level, r.way, r.calibration_error, r.calibration_error_lo, r.calibration_error_hi,
                                                                       r.awareness_ratio, r.stale_rate))
        lines.append("")
    ev = tables["events"]
    if len(ev):
        b3 = ev[ev["way"] == "B3"]
        lines.append("## The occluded pedestrian (%d events of the evaluation runs, B3)" % int(b3["events"].sum()))
        lines.append("")
        lines.append("Belief in the hidden pedestrian during the gap with V2X: mean %.3f, lowest %.3f; with the own sensors only at the end of the gap: %.3f. "
                     "CPM rows about the pedestrian received in the gap: %.0f on average (%.0f at 10 %% penetration, %.0f at 100 %%)." % (
                         b3["v2x_gap_mean"].mean(), b3["v2x_gap_min"].mean(), b3["own_gap_end"].mean(), b3["cpm_rows_in_gap"].mean(),
                         b3[b3["pen"] == 10]["cpm_rows_in_gap"].mean(), b3[b3["pen"] == 100]["cpm_rows_in_gap"].mean()))
    return "\n".join(lines) + "\n"


def build(results: str) -> dict:
    """Every table of the database from the results folder."""
    data = load(results)
    runs = runs_table(data["metrics"], data["facts"])
    evaluation = runs[runs["kind"] == "evaluation"]
    main_hold = evaluation[evaluation["hold"] == 5.0]
    cells = aggregate(main_hold, CELL + ["way"], METRICS + [c for c in FACTS if c in main_hold.columns])
    cells.insert(3, "hold", 5.0)
    factors = []
    for factor in CELL:
        f = aggregate(main_hold, [factor, "way"], METRICS)
        f.insert(0, "factor", factor)
        f = f.rename(columns={factor: "level"})
        factors.append(f)
    factors = pd.concat(factors, ignore_index=True)
    h1, h1_overall = h1_tables(runs)
    h3, h3_overall = h3_tables(runs)
    checked = evaluation[evaluation["run"].isin(evaluation[evaluation["hold"] != 5.0]["run"].unique())]
    holds = aggregate(checked, ["hold", "way"], METRICS)
    rel = reliability(data["bins"], ["hold", "way"])
    rel_pen = reliability(data["bins"][data["bins"]["hold"] == 5.0], ["hold", "pen", "way"])
    events = events_table(data["events"])
    tables = dict(runs=runs, cells=cells, factors=factors, h1=h1, h1_overall=h1_overall, h3=h3, h3_overall=h3_overall, holds=holds,
                  reliability=rel, reliability_pen=rel_pen, events=events)
    tables["summary"] = summary_text(tables, data["qfit"])
    return tables


def write(tables: dict, out: str) -> list[str]:
    os.makedirs(out, exist_ok=True)
    written = []
    for name, table in tables.items():
        path = os.path.join(out, name + (".md" if name == "summary" else ".csv"))
        if name == "summary":
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(table)
        else:
            table.to_csv(path, index=False)
        written.append(path)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the results database of the core grid (task D4) from the tables of D2 and D3.")
    ap.add_argument("--results", default="results", help="the results folder with d2/ and d3/ (default: results)")
    ap.add_argument("--out", default=None, help="the output folder (default: <results>/d4)")
    args = ap.parse_args(argv)
    out = args.out or os.path.join(args.results, "d4")
    try:
        tables = build(args.results)
    except FileNotFoundError as exc:
        print("ERROR: %s" % exc)
        return 2
    for path in write(tables, out):
        print("written: %s" % path)
    print(tables["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
