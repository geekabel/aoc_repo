"""The replay of the five ways on every run of the core grid (task D3, experiment E3).

Every exported run of D1 is replayed offline with the pipeline of B2 to B7 for the five ways of the plan (B0 full
trust, B1 cutoff at 1 s, B2 Bayesian inflation, B3 the discount by age with the cautious rule, B4 the discount with
Dempster only), and the metrics of B8 are written down per run and per way: calibration error and Brier score of the
belief, coverage, sharpness, awareness ratio, RMSE, stale-confidence rate, ghost rate, time to confident, with the
network side (channel busy ratio, delivery ratio, age at reception). The reliability diagram of every way (the data of
figure F5) and the belief of each ego in its pedestrian during the occlusion (the events of the grid, the data behind
F6 at scale; traced with the first hold of ``--holds``) come with them.

What the replay does with the grid:

  - the warm-up is left out: nothing is counted before 60 s (the memory of a receiver at 60 s is the one of a full
    replay: the replay loads every earlier report and the hold drops the old ones; the objects already in range at
    60 s are not counted as entries);
  - the receiver forgets a received object older than the hold, 5 s by default, the same for every way; 2 s and 10 s
    are run as a check on a part of the grid (``--holds 2,10 --runs ...``);
  - the motion value q of the reliability curve comes from the calibration runs (``aoc-fit-q``, option ``--q``); the
    evaluation runs are the seeds 0 to 9, the calibration runs the seeds 10 and 11, told apart by the column ``kind``;
  - a run is loaded one receiver at a time (the received rows of a dense run are tens of millions), the receivers
    being those of its observations table: the actors of the events and the 8 cars drawn by the exporter; the
    measures of the receivers add up into one row per way and hold;
  - the result of a run is one JSON file in the output folder; a run whose file exists is not replayed again, so the
    replay can run in several sittings and the failures can be rerun alone.

    aoc-replay-grid ROOT --out DIR [--holds 5] [--q class=value ... | --q-from qfit.csv] [--runs 0-11,36]
                    [--receivers all|egos|N] [--jobs N] [--warmup 60] [--no-events] [--aggregate]

``--aggregate`` reads the JSON files of DIR and writes ``metrics.csv`` (one row per run, way and hold, with the cell),
``bins.csv`` (the reliability diagrams) and ``events.csv`` (one row per event and way), and prints a table per
message configuration and penetration for the hold of 5 s. The exit code is 0 when every run asked for has its file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from aoc.analysis import grid
from aoc.analysis.gridcheck import ACTOR_KINDS, EVENTS, actors_of, parse_numbers, run_number_of
from aoc.analysis.metrics import WAYS, EvaluationModel, EvaluationState, evaluate_into
from aoc.analysis.trace import both_views
from aoc.analysis.vru import MIN_GAP, first_gap
from aoc.fusion.alignment import Q_BY_CLASS
from aoc.fusion.discounting import TrustModel, _q_options
from aoc.fusion.noise import NoiseModel
from aoc.fusion.replay import load_run

WARM_UP = 60.0
DEFAULT_HOLDS = (5.0,)
EVENT_WAYS = ("B0", "B1", "B3", "B4")               # B2 is not a belief
MARGIN = 1.0                                        # s around the gap in the event traces


def receivers_of(info: dict, option: str, folder: str | None = None) -> list[str]:
    """The receivers to replay: 'all' = those of the observations table (the actors and the drawn cars); 'egos' = the
    egos of the events only; a number N = the egos and the first N drawn cars. A run exported with every receiver
    (no list in its summary) gets the vehicles of its observations table."""
    listed = [str(r) for r in (info.get("receivers") or [])]
    if not listed and folder is not None:
        import pyarrow.parquet as pq
        column = pq.read_table(os.path.join(folder, "observations.parquet"), columns=["rx_station"]).column(0)
        listed = sorted(str(v) for v in column.unique().to_pylist() if v is not None)
    actors = {actors_of(k)[kind] for k in EVENTS for kind in ACTOR_KINDS}
    egos = sorted(r for r in listed if r.startswith("ego"))
    others = sorted(r for r in listed if r not in actors)
    if option == "all":
        return listed
    if option == "egos":
        return egos
    n = int(option)
    return egos + others[:n]


def _last(values: pd.Series):
    """The last finite value of a series, rounded, or None."""
    v = values.to_numpy(dtype=float)
    v = v[np.isfinite(v)]
    return round(float(v[-1]), 4) if v.size else None


def event_rows(run, ego: str, ped: str, model: EvaluationModel) -> list[dict]:
    """The belief of the ego in its pedestrian around the occlusion, per way: the mean, the lowest and the last value
    with V2X during the gap, the last value with the own sensors only, the CPM rows received in the gap."""
    obs = run.obs
    mine = obs[(obs["rx_station"] == ego).to_numpy(dtype=bool, na_value=False) & (obs["gt_match_id"] == ped).to_numpy(dtype=bool, na_value=False)
               & (obs["src"] != "v2x").to_numpy(dtype=bool, na_value=False)]
    times = np.unique(mine["t_sim"].to_numpy(dtype=float))
    gap = first_gap(times, MIN_GAP) if times.size else None
    base = dict(ego=ego, ped=ped, ego_sees=bool(times.size), lost=None, seen_again=None, gap=None, occlusion=False)
    if gap is None:
        return [dict(base, way=w) for w in EVENT_WAYS]
    lost, seen = gap
    base.update(lost=lost, seen_again=seen, gap=round(seen - lost, 3), occlusion=bool(seen - lost >= MIN_GAP))
    traces = both_views(run, ego, ped, model, ways=EVENT_WAYS, t_start=max(0.0, lost - MARGIN), t_end=seen + MARGIN)
    inside = traces[(traces["t"] > lost) & (traces["t"] < seen)]
    rows = []
    for way in EVENT_WAYS:
        v2x = inside[(inside["view"] == "with V2X") & (inside["way"] == way)]
        own = inside[(inside["view"] == "own sensors") & (inside["way"] == way)]
        rows.append(dict(base, way=way,
                         v2x_gap_mean=round(float(v2x["bel"].mean()), 4) if len(v2x) and v2x["bel"].notna().any() else None,
                         v2x_gap_min=round(float(v2x["bel"].min()), 4) if len(v2x) and v2x["bel"].notna().any() else None,
                         v2x_gap_end=_last(v2x["bel"]), own_gap_end=_last(own["bel"]),
                         own_gap_min=round(float(own["bel"].min()), 4) if len(own) and own["bel"].notna().any() else None,
                         cpm_rows_in_gap=int(v2x["n_new"].sum()) if len(v2x) else 0,
                         groups_max=int(v2x["groups"].max()) if len(v2x) else 0))
    return rows


def replay_run(folder: str, holds=DEFAULT_HOLDS, q_by_class: dict | None = None, receivers: str = "all", warm_up: float = WARM_UP,
               events: bool = True) -> dict:
    """Replays one exported run for the given holds: the metrics per way and hold, the reliability diagrams, the events."""
    folder = os.fspath(folder)
    if not os.path.isfile(os.path.join(folder, "export_summary.json")) and os.path.isfile(os.path.join(folder, "parquet", "export_summary.json")):
        folder = os.path.join(folder, "parquet")
    with open(os.path.join(folder, "export_summary.json"), encoding="utf-8") as fh:
        info = json.load(fh)
    number = run_number_of(folder)
    cell = grid.cell_of_run(number) if number is not None and number < grid.N_RUNS else {}
    trust = TrustModel(q_by_class=q_by_class) if q_by_class else TrustModel()
    models = {h: EvaluationModel(hold=h, trust=trust) for h in holds}
    states = {h: EvaluationState() for h in holds}
    chosen = receivers_of(info, receivers, folder)
    start = time.perf_counter()
    event_table, notes = [], []
    run_id = info.get("run_id", "")
    for r in chosen:
        run = load_run(folder, noise=NoiseModel(), missing="drop", receivers=[r])
        if r not in run.egos:
            notes.append("%s has no row" % r)
            continue
        for h in holds:
            evaluate_into(run, models[h], states[h], egos=[r], t_start=warm_up)
        if events and r.startswith("ego"):
            k = r.rsplit("_", 1)[-1]
            ped = "ped_%s" % k if k.isdigit() else "ped"
            if (run.truth["obj_id"] == ped).any():
                for row in event_rows(run, r, ped, models[holds[0]]):
                    row.update(event=int(k) if k.isdigit() else 0)
                    event_table.append(row)
    seconds = time.perf_counter() - start
    out = dict(run=number, run_id=run_id, folder=folder, **{k: cell.get(k) for k in ("density", "pen", "msg", "seed", "kind")},
               receivers=chosen, holds=list(holds), warm_up=warm_up, q_by_class=dict(trust.q_by_class), seconds=round(seconds, 1),
               notes=notes, metrics=[], bins=[], events=event_table)
    for h in holds:
        rows = states[h].rows(run_id, models[h])
        out["metrics"].extend(rows.to_dict(orient="records"))
        out["bins"].extend(states[h].bins(run_id, models[h]).to_dict(orient="records"))
    return out


def _clean(value):
    """JSON-friendly values."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def _work(args) -> dict:
    folder, path, holds, q_by_class, receivers, warm_up, events = args
    try:
        out = replay_run(folder, holds, q_by_class, receivers, warm_up, events)
    except Exception as exc:
        return dict(run=run_number_of(folder), folder=folder, error="%s: %s" % (type(exc).__name__, exc))
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_clean(out), fh)
    os.replace(tmp, path)
    return dict(run=out["run"], folder=folder, seconds=out["seconds"], error=None)


def find_runs(root: str, numbers=None) -> list[tuple[int, str]]:
    out = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        n = grid._number_of_folder(name)
        if n is None or (numbers is not None and n not in numbers):
            continue
        if os.path.isfile(os.path.join(root, name, "parquet", "export_summary.json")):
            out.append((n, os.path.join(root, name, "parquet")))
    return out


def result_path(out_dir: str, number: int, holds) -> str:
    tag = "-".join("%g" % h for h in holds)
    return os.path.join(out_dir, "run-%03d-hold-%s.json" % (number, tag))


def aggregate(out_dir: str) -> dict:
    """Reads every result file of the folder: the tables of metrics, bins and events, with the cell of each run."""
    metrics, bins, events = [], [], []
    for name in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        if not (name.startswith("run-") and name.endswith(".json")):
            continue
        with open(os.path.join(out_dir, name), encoding="utf-8") as fh:
            r = json.load(fh)
        cell = {k: r.get(k) for k in ("run", "density", "pen", "msg", "seed", "kind")}
        for row in r.get("metrics", []):
            metrics.append(dict(cell, **row, receivers=len(r.get("receivers") or []), replay_seconds=r.get("seconds")))
        for row in r.get("bins", []):
            bins.append(dict(cell, **row))
        for row in r.get("events", []):
            events.append(dict(cell, hold=r["holds"][0] if r.get("holds") else None, **row))
    return dict(metrics=pd.DataFrame(metrics), bins=pd.DataFrame(bins), events=pd.DataFrame(events))


def summary_table(metrics: pd.DataFrame, hold: float = 5.0, kind: str = "evaluation") -> pd.DataFrame:
    """Per message configuration and penetration (every density together), the mean calibration error, Brier score and
    awareness of each way over the evaluation runs."""
    m = metrics[(metrics["hold"] == hold) & (metrics["kind"] == kind)]
    if m.empty:
        return pd.DataFrame()
    g = m.groupby(["msg", "pen", "way"], sort=False).agg(runs=("run", "nunique"), ece=("calibration_error", "mean"), brier=("brier", "mean"),
                                                        awareness=("awareness_ratio", "mean"), stale=("stale_rate", "mean"),
                                                        rmse=("rmse", "mean")).reset_index()
    order = {m_: i for i, m_ in enumerate(grid.MESSAGES)}, {p: i for i, p in enumerate(grid.PENETRATIONS)}, {w: i for i, w in enumerate(WAYS)}
    g["_o"] = [(order[0].get(a, 9), order[1].get(b, 9), order[2].get(c, 9)) for a, b, c in zip(g["msg"], g["pen"], g["way"])]
    return g.sort_values("_o").drop(columns="_o").reset_index(drop=True)


def format_summary(table: pd.DataFrame) -> str:
    if table.empty:
        return "no evaluation run replayed yet"
    lines = ["%-5s %4s %5s | %s" % ("msg", "pen", "runs", "  ".join("%-18s" % w for w in WAYS)),
             "calibration error of the belief (mean over the evaluation runs), Brier score in brackets"]
    for (msg, pen), part in table.groupby(["msg", "pen"], sort=False):
        by = part.set_index("way")
        cells = []
        for w in WAYS:
            if w in by.index:
                cells.append("%.4f (%.4f)      " % (by.loc[w, "ece"], by.loc[w, "brier"]))
            else:
                cells.append("%-18s" % "-")
        lines.append("%-5s %4s %5d | %s" % (msg, pen, int(part["runs"].max()), "  ".join(cells)))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Replay the five ways on the runs of the core grid (task D3) and write the metrics per run.")
    ap.add_argument("root", help="the results folder of the grid (holds the run folders aoc-grid-<n>)")
    ap.add_argument("--out", required=True, help="the folder of the results: one JSON file per run, then the CSV tables")
    ap.add_argument("--holds", default="5", help="the holds in s, comma-separated (default 5; the check of the plan: 2,10)")
    ap.add_argument("--q", action="append", default=[], metavar="CLASS=VALUE", help="motion value of the reliability curve for one class (from aoc-fit-q)")
    ap.add_argument("--q-from", default=None, metavar="QFIT_CSV", help="read the fitted motion values from the table written by aoc-fit-q --out")
    ap.add_argument("--runs", default=None, help="only these run numbers, for example 0-11,36 (default: every exported run)")
    ap.add_argument("--receivers", default="all",
                    help="the receivers replayed per run: all (the actors and the drawn cars, default), egos, or a number N of drawn cars "
                         "(in name order) beside the egos")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="runs replayed in parallel (default: half the CPUs)")
    ap.add_argument("--warmup", type=float, default=WARM_UP, help="nothing is counted before this time (default 60 s)")
    ap.add_argument("--no-events", action="store_true", help="do not trace the belief of the egos in their pedestrian")
    ap.add_argument("--aggregate", action="store_true", help="only read the result files of --out and write the CSV tables")
    args = ap.parse_args(argv)
    holds = tuple(float(h) for h in args.holds.split(",") if h.strip())
    if not holds or any(h <= 0 for h in holds):
        print("--holds needs positive numbers of seconds")
        return 2
    try:
        pairs = list(args.q)
        if args.q_from:
            fitted = pd.read_csv(args.q_from)
            fitted = fitted[fitted["fitted"].astype(bool) & fitted["q_fitted"].notna()] if "fitted" in fitted.columns else fitted
            pairs = ["%s=%r" % (r.cls, float(r.q_fitted)) for r in fitted.itertuples()] + pairs
        q_by_class = _q_options(pairs) if pairs else None
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(str(exc))
        return 2
    if args.receivers not in ("all", "egos") and not args.receivers.isdigit():
        print("--receivers must be all, egos or a number")
        return 2
    os.makedirs(args.out, exist_ok=True)
    if not args.aggregate:
        numbers = set(parse_numbers(args.runs)) if args.runs else None
        found = find_runs(args.root, numbers)
        if not found:
            print("no exported run under %s" % args.root)
            return 1
        todo = [(f, result_path(args.out, n, holds), holds, q_by_class, args.receivers, args.warmup, not args.no_events)
                for n, f in found if not os.path.isfile(result_path(args.out, n, holds))]
        changed = {k: v for k, v in (q_by_class or {}).items() if Q_BY_CLASS.get(k) != v}
        print("runs: %d found, %d done before, %d to replay with %d job(s); holds %s; receivers %s; q %s" % (
            len(found), len(found) - len(todo), len(todo), args.jobs, ", ".join("%g s" % h for h in holds), args.receivers,
            "fitted: " + ", ".join("%s %.4g" % kv for kv in sorted(changed.items())) if changed else "of the note"), flush=True)
        results = None
        if args.jobs > 1 and todo:
            try:
                with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                    results = []
                    for r in pool.map(_work, todo):
                        results.append(r)
                        print("  run %s: %s" % (r["run"], ("error: " + r["error"]) if r["error"] else "%.0f s" % r["seconds"]), flush=True)
            except (OSError, PermissionError) as exc:
                print("parallel replay not possible (%s); one run at a time" % exc, flush=True)
        if results is None:
            results = []
            for w in [w for w in todo if not os.path.isfile(w[1])]:      # the runs the pool wrote before it failed are kept
                r = _work(w)
                results.append(r)
                print("  run %s: %s" % (r["run"], ("error: " + r["error"]) if r["error"] else "%.0f s" % r["seconds"]), flush=True)
        failed = [r for r in results if r["error"]]
        missing = [n for n, _ in found if not os.path.isfile(result_path(args.out, n, holds))]
    else:
        numbers = set(parse_numbers(args.runs)) if args.runs else None
        failed = []
        missing = [n for n, _ in find_runs(args.root, numbers) if not os.path.isfile(result_path(args.out, n, holds))]
    tables = aggregate(args.out)
    for name, table in tables.items():
        path = os.path.join(args.out, name + ".csv")
        table.to_csv(path, index=False)
        print("written: %s (%d rows)" % (path, len(table)))
    if len(tables["metrics"]):
        print(format_summary(summary_table(tables["metrics"], hold=holds[0])))
    if failed:
        print("runs that failed: %s" % ", ".join(str(r["run"]) for r in failed))
    if missing:
        print("runs without a result: %s" % grid.compact(sorted(missing)))
    print("result: %s" % ("PASS" if not failed and not missing else "FAIL"))
    return 0 if not failed and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
