"""The quality check of the core grid (task D2): every exported run of D1 read once, the facts of the run against
its cell, the three events of each run, and a table per cell.

D1 ran the 432 runs and indexed them (``aoc-grid``); D2 asks whether the runs hold what the analysis of D3 will
rely on. For every run folder ``aoc-grid-<n>`` with an export that passed, this module reads the summary, the
nodes, the truth, the sent and received CPMs, and the rows about the pedestrians of the local and observations
tables, and writes down:

  - the run: simulated time and steps; the pedestrians (three, ``ped_1`` to ``ped_3``); the 15 actors of the events
    (the truck, the ego and the three helpers of each event) among the vehicles with a middleware; the cars on the
    map after the warm-up (mean over the steps); the share
    of the background cars that sent at least one CPM (the penetration as it came out of the draw); the median
    CPM rate of the senders present long enough; the channel busy ratio seen by the receivers after the warm-up;
    the CPM rows received after the warm-up;
  - each event k: whether ``ego_k`` saw ``ped_k`` with its own sensors, then lost it (the first gap of at least
    1.5 s), the distances when lost and seen again, the CPM rows about ``ped_k`` that ``ego_k`` received during the
    gap (from the actors of the event, from the other cars), the age of the first one, and the cars that saw the
    pedestrian during at least four steps out of five of the gap (the witnesses).

The checks of a run (PASS when all hold): the export passed and its variables are those of its run number; the
run covers 329.5 s or more; three pedestrians; the 15 actors have a middleware; the three egos and the nine helpers
sent CPMs; the seven tables are there; the share of the background cars present at least 2 s that sent a CPM is
within three binomial standard deviations plus 0.02 of the penetration (at least 0.97 at 100 %); the CPM rate of
the senders matches the message configuration (2hz: 1.9 to 2.1 Hz and 10hz: 9.5 to 10.5 Hz for the median and
the 10th and 90th percentiles of the senders; etsi: median between 0.8 and 10.5 Hz, the rule of ETSI goes from
1 Hz under the DCC to 10 Hz); the mean number of cars is between half and twice the expected count of the density.
The events are reported and counted per cell, not checked: how many events of a cell have an occlusion and a
CPM in the gap is a result of the grid (it depends on the penetration and on the traffic), not a defect. The
counts of CPMs in the gap, the first age and the witnesses are counted over the events with an occlusion only;
the events table keeps, for an ego without a gap of 1.5 s, its longest gap, marked as no occlusion.

    aoc-gridcheck ROOT [--out runs.csv] [--events events.csv] [--cells cells.csv] [--jobs N] [--runs 0-11,36]

The exit code is 0 when every run read passes, 1 otherwise. ``--runs`` limits the check to some run numbers (a
quick trial); the cell table then holds those runs only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from aoc.analysis import grid
from aoc.analysis.vru import HELPER_SHARE, LOST_WITHIN, MAX_GAP, MIN_GAP, SEEN_WITHIN, distance_at, first_gap

WARM_UP = 60.0                                  # s: the analysis leaves out the first 60 s
MIN_TIME = 329.5                                # s: a run must cover this much
EVENTS = (1, 2, 3)
ACTOR_KINDS = ("truck", "ego", "helper_a", "helper_b", "helper_c")
EXPECTED_CARS = {"low": 15.0, "medium": 25.0, "high": 40.0}
RATE_BANDS = {"etsi": (0.8, 10.5), "2hz": (1.9, 2.1), "10hz": (9.5, 10.5)}      # Hz, the CPM rate of a sender
PERIODIC = ("2hz", "10hz")                      # the configurations whose senders are strictly periodic: every sender in the band
PEN_SLACK = 0.02                                # on top of three binomial standard deviations: the cars that leave before their first CPM
PEN_FULL = 0.97                                 # at 100 %, the share of background senders must reach this
MIN_PRESENCE = 2.0                              # s: a background car counts in the penetration when present at least this long
TABLES = ("truth", "nodes", "cpm_tx", "cpm_obj", "cpm_rx", "local", "observations")
MIN_SENDER_SPAN = 20.0                          # s: a sender present at least this long counts in the rate


def actors_of(k: int) -> dict:
    return {kind: "%s_%d" % (kind, k) for kind in ACTOR_KINDS}


def all_actors() -> list[str]:
    return [actors_of(k)[kind] for k in EVENTS for kind in ACTOR_KINDS]


def _read(folder: str, name: str, columns=None, filters=None) -> pd.DataFrame | None:
    path = os.path.join(folder, name + ".parquet")
    if not os.path.isfile(path):
        return None
    return pq.read_table(path, columns=columns, filters=filters).to_pandas()


def run_number_of(folder: str) -> int | None:
    """The run number from the folder name ``aoc-grid-<n>``, or from its parent when the folder is its ``parquet`` subfolder."""
    folder = os.path.normpath(os.fspath(folder))
    n = grid._number_of_folder(os.path.basename(folder))
    if n is None and os.path.basename(folder) == "parquet":
        n = grid._number_of_folder(os.path.basename(os.path.dirname(folder)))
    return n


def event_timeline(k: int, truth: pd.DataFrame, local_about: pd.DataFrame, v2x_about: pd.DataFrame, nodes: set) -> dict:
    """The event k of one run: the ego's view of the pedestrian, the gap, the CPMs received in it, the witnesses.

    ``local_about``: the sensor rows about the pedestrians (columns t, node_id, obj_id); ``v2x_about``: the CPM rows
    about the pedestrians received (columns t_rx, rx_station, tx_station, gt_match_id, age, class)."""
    a = actors_of(k)
    ego, ped = a["ego"], "ped_%d" % k
    out = dict(event=k, ego=ego, ped=ped, ego_present=bool((truth["obj_id"] == ego).any()), ego_middleware=ego in nodes,
               ped_present=bool((truth["obj_id"] == ped).any()),
               ego_sees=False, ego_first=None, lost=None, seen_again=None, gap=None, lost_distance=None, seen_again_distance=None,
               occlusion=False, cpm_in_gap=0, cpm_from_actors=0, cpm_from_others=0, first_cpm_age=None, first_cpm_delay=None,
               witnesses=0, actor_witnesses=0, cpm_about_ped=0, cpm_senders=0)
    if not out["ped_present"]:
        return out
    ped_truth = truth[truth["obj_id"] == ped]
    out["ped_from"], out["ped_to"] = float(ped_truth["t"].min()), float(ped_truth["t"].max())
    mine = local_about[(local_about["obj_id"] == ped) & (local_about["node_id"] == ego)]
    times = np.unique(mine["t"].to_numpy(float))
    out["ego_sees"] = times.size > 0
    out["ego_first"] = None if not times.size else float(times.min())
    rx_all = v2x_about[(v2x_about["gt_match_id"] == ped) & (v2x_about["rx_station"] == ego)]
    out["cpm_about_ped"] = int(len(rx_all))
    out["cpm_senders"] = int(rx_all["tx_station"].nunique())
    gap = first_gap(times, MIN_GAP) if times.size else None
    if gap is None:
        return out
    t0, t1 = gap
    out.update(lost=t0, seen_again=t1, gap=round(t1 - t0, 3), lost_distance=distance_at(truth, ego, ped, t0),
               seen_again_distance=distance_at(truth, ego, ped, t1))
    out["occlusion"] = bool(MIN_GAP <= out["gap"] <= MAX_GAP and out["lost_distance"] is not None and out["lost_distance"] <= LOST_WITHIN
                            and out["seen_again_distance"] is not None and out["seen_again_distance"] <= SEEN_WITHIN)
    rx = rx_all[(rx_all["t_rx"] > t0) & (rx_all["t_rx"] < t1)].sort_values("t_rx")
    event_actors = set(a.values())
    out["cpm_in_gap"] = int(len(rx))
    out["cpm_from_actors"] = int(rx["tx_station"].isin(event_actors).sum())
    out["cpm_from_others"] = out["cpm_in_gap"] - out["cpm_from_actors"]
    if len(rx):
        out["first_cpm_age"] = float(rx["age"].iloc[0])
        out["first_cpm_delay"] = round(float(rx["t_rx"].iloc[0]) - t0, 3)
    steps = np.round(np.arange(t0 + 0.1, t1 - 0.05, 0.1), 1)
    seeing = local_about[(local_about["obj_id"] == ped) & (local_about["t"] > t0) & (local_about["t"] < t1) & (local_about["node_id"] != ego)]
    witnesses = []
    for who, rows in seeing.groupby("node_id"):
        seen = np.unique(np.round(rows["t"].to_numpy(float), 1))
        if steps.size and np.isin(steps, seen).sum() >= HELPER_SHARE * steps.size:
            witnesses.append(str(who))
    out["witnesses"] = len(witnesses)
    out["actor_witnesses"] = sum(1 for w in witnesses if w in event_actors)
    return out


def read_run(folder: str) -> dict:
    """The facts of one exported run and its three events. Keys: see ``RUN_COLUMNS`` and ``event_timeline``."""
    folder = os.fspath(folder)
    if not os.path.isfile(os.path.join(folder, "export_summary.json")) and os.path.isfile(os.path.join(folder, "parquet", "export_summary.json")):
        folder = os.path.join(folder, "parquet")
    with open(os.path.join(folder, "export_summary.json"), encoding="utf-8") as fh:
        info = json.load(fh)
    number = run_number_of(folder)
    expected = grid.cell_of_run(number) if number is not None and number < grid.N_RUNS else None
    cell = grid.cell_from_variables(info.get("variables") or {})
    out = dict(run=number, folder=folder, result=info.get("result"), t_last_s=info.get("t_last_s"), steps=info.get("steps"),
               seed=info.get("seed"), density=None, pen=None, msg=None, kind=None, cell_matches=None, events=[], checks={}, notes=[])
    if expected:
        out.update({k: expected[k] for k in ("density", "pen", "msg", "kind")})
        out["cell_matches"] = bool(cell is not None and cell == {k: expected[k] for k in ("density", "pen", "msg")} and info.get("seed") == expected["seed"])
    elif cell is not None:
        out.update(cell)
    checks = out["checks"]
    checks["export_passed"] = info.get("result") == "PASS"
    checks["cell_matches"] = bool(out["cell_matches"])
    checks["covers_the_run"] = bool(out["t_last_s"] is not None and out["t_last_s"] >= MIN_TIME)
    missing = [name for name in TABLES if not os.path.isfile(os.path.join(folder, name + ".parquet"))]
    checks["tables_present"] = not missing
    if missing:
        out["notes"].append("missing table(s): " + ", ".join(missing))

    truth = _read(folder, "truth", columns=["t", "obj_id", "x", "y", "class"])
    nodes_table = _read(folder, "nodes", columns=["node_id"])
    tx = _read(folder, "cpm_tx", columns=["t_tx", "tx_id"])
    rx = _read(folder, "cpm_rx", columns=["t_rx", "cbr", "delay"])
    if truth is None:
        return finish(out)
    peds = sorted(truth.loc[truth["class"] == "pedestrian", "obj_id"].dropna().astype(str).unique().tolist())
    out["pedestrians"] = len(peds)
    checks["three_pedestrians"] = peds == ["ped_%d" % k for k in EVENTS]
    nodes = set(nodes_table["node_id"].dropna().astype(str)) if nodes_table is not None else set(truth.loc[truth["class"] != "pedestrian", "obj_id"].astype(str))
    actors = all_actors()
    out["actors_present"] = sum(1 for a in actors if a in nodes)
    checks["actors_present"] = out["actors_present"] == len(actors)
    background = sorted(nodes - set(actors))
    out["vehicles"] = len(nodes)
    out["background_cars"] = len(background)
    span = truth.groupby("obj_id")["t"].agg(["min", "max"])
    present = set(span.index[(span["max"] - span["min"]) >= MIN_PRESENCE].astype(str))
    eligible = [car for car in background if car in present]
    out["background_eligible"] = len(eligible)

    # the cars on the map after the warm-up
    late = truth[(truth["t"] >= WARM_UP) & (truth["class"] != "pedestrian")]
    per_step = late.groupby("t")["obj_id"].nunique()
    out["cars_mean"] = round(float(per_step.mean()), 1) if len(per_step) else None
    out["cars_max"] = int(per_step.max()) if len(per_step) else None
    expected_cars = EXPECTED_CARS.get(out["density"])
    checks["cars_as_expected"] = bool(out["cars_mean"] is not None and expected_cars is not None and 0.5 * expected_cars <= out["cars_mean"] <= 2.0 * expected_cars)

    # the senders: the penetration as drawn, and the CPM rate
    out["senders"] = out["background_senders"] = 0
    out["pen_share"] = out["rate_median_hz"] = out["rate_p10_hz"] = out["rate_p90_hz"] = None
    senders_needed = [actors_of(k)[kind] for k in EVENTS for kind in ("ego", "helper_a", "helper_b", "helper_c")]
    if tx is not None and len(tx):
        tx = tx.dropna(subset=["tx_id"])
        tx["tx_id"] = tx["tx_id"].astype(str)
        senders = set(tx["tx_id"])
        out["senders"] = len(senders)
        out["background_senders"] = len(senders & set(background))
        out["pen_share"] = round(len(senders & set(eligible)) / len(eligible), 3) if eligible else None
        g = tx.groupby("tx_id")["t_tx"].agg(["count", "min", "max"])
        g = g[(g["max"] - g["min"]) >= MIN_SENDER_SPAN]
        rates = ((g["count"] - 1) / (g["max"] - g["min"])).to_numpy(float)
        if rates.size:
            out["rate_median_hz"] = round(float(np.median(rates)), 2)
            out["rate_p10_hz"], out["rate_p90_hz"] = round(float(np.percentile(rates, 10)), 2), round(float(np.percentile(rates, 90)), 2)
        out["truck_senders"] = sum(1 for k in EVENTS if actors_of(k)["truck"] in senders)
        checks["egos_and_helpers_send"] = all(s in senders for s in senders_needed)
    else:
        out["truck_senders"] = 0
        checks["egos_and_helpers_send"] = False
    pen = out["pen"]
    if pen is not None and out["pen_share"] is not None and eligible:
        p = pen / 100.0
        sigma = (p * (1 - p) / len(eligible)) ** 0.5
        checks["penetration_as_drawn"] = out["pen_share"] >= PEN_FULL if pen == 100 else abs(out["pen_share"] - p) <= PEN_SLACK + 3 * sigma
    else:
        checks["penetration_as_drawn"] = False
    band = RATE_BANDS.get(out["msg"])
    rates_in = (out["rate_median_hz"],) if out["msg"] not in PERIODIC else (out["rate_median_hz"], out["rate_p10_hz"], out["rate_p90_hz"])
    checks["rate_matches_msg"] = bool(band is not None and all(r is not None and band[0] <= r <= band[1] for r in rates_in))

    # the channel after the warm-up
    out["cbr_mean"] = out["cbr_p95"] = out["delay_median_ms"] = None
    out["cpm_rx_after_warmup"] = 0
    if rx is not None and len(rx):
        late_rx = rx[rx["t_rx"] >= WARM_UP]
        out["cpm_rx_after_warmup"] = int(len(late_rx))
        cbr = late_rx["cbr"].dropna().to_numpy(float)
        if cbr.size:
            out["cbr_mean"], out["cbr_p95"] = round(float(cbr.mean()), 4), round(float(np.percentile(cbr, 95)), 4)
        delay = late_rx["delay"].dropna().to_numpy(float)
        if delay.size:
            out["delay_median_ms"] = round(1000.0 * float(np.median(delay)), 2)

    # the events: the rows about the pedestrians only
    obs_about = _read(folder, "observations", columns=["t_sim", "rx_station", "src", "tx_station", "gt_match_id", "t_rx", "age", "class"],
                      filters=[("gt_match_id", "in", peds)]) if peds else None
    local_about = _read(folder, "local", columns=["t", "node_id", "obj_id"], filters=[("obj_id", "in", peds)]) if peds else None
    if local_about is None and obs_about is not None:                # a run without local.parquet: the sensor rows of the observations table
        out["notes"].append("no local table: the witnesses are counted among the receivers of the observations table only")
        own = obs_about[obs_about["src"] != "v2x"]
        local_about = pd.DataFrame({"t": own["t_sim"], "node_id": own["rx_station"], "obj_id": own["gt_match_id"]})
    if obs_about is not None and local_about is not None:
        for col in ("node_id", "obj_id"):
            local_about[col] = local_about[col].astype(str)
        v2x_about = obs_about[obs_about["src"] == "v2x"].copy()
        for col in ("rx_station", "tx_station", "gt_match_id"):
            v2x_about[col] = v2x_about[col].astype(str)
        for k in EVENTS:
            out["events"].append(event_timeline(k, truth, local_about, v2x_about, nodes))
    return finish(out)


def finish(out: dict) -> dict:
    out["ok"] = all(out["checks"].values())
    return out


RUN_COLUMNS = ["run", "density", "pen", "msg", "seed", "kind", "ok", "result", "t_last_s", "steps", "pedestrians", "vehicles", "background_cars",
               "background_eligible", "actors_present", "cars_mean", "cars_max", "senders", "background_senders", "pen_share", "truck_senders", "rate_median_hz",
               "rate_p10_hz", "rate_p90_hz", "cbr_mean", "cbr_p95", "delay_median_ms", "cpm_rx_after_warmup", "failed_checks", "folder"]
EVENT_COLUMNS = ["run", "density", "pen", "msg", "seed", "kind", "event", "ego", "ped", "ego_present", "ego_middleware", "ped_present", "ego_sees",
                 "ego_first", "lost", "seen_again", "gap", "lost_distance", "seen_again_distance", "occlusion", "cpm_in_gap", "cpm_from_actors", "cpm_from_others",
                 "first_cpm_age", "first_cpm_delay", "witnesses", "actor_witnesses", "cpm_about_ped", "cpm_senders"]


def runs_table(results: list[dict]) -> pd.DataFrame:
    rows = []
    for r in results:
        row = {c: r.get(c) for c in RUN_COLUMNS}
        row["failed_checks"] = ",".join(k for k, v in r["checks"].items() if not v)
        rows.append(row)
    return pd.DataFrame(rows, columns=RUN_COLUMNS).sort_values("run").reset_index(drop=True)


def events_table(results: list[dict]) -> pd.DataFrame:
    rows = []
    for r in results:
        for e in r["events"]:
            row = {c: r.get(c) for c in ("run", "density", "pen", "msg", "seed", "kind")}
            row.update({c: e.get(c) for c in EVENT_COLUMNS if c not in row})
            rows.append(row)
    return pd.DataFrame(rows, columns=EVENT_COLUMNS).sort_values(["run", "event"]).reset_index(drop=True)


def cells_table(runs: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """One row per cell: the runs, the cars, the penetration as drawn, the CPM rate, the channel, and the events."""
    if runs.empty:
        return pd.DataFrame()
    keys = ["density", "pen", "msg"]
    g = runs.groupby(keys, sort=False)
    cells = g.agg(runs=("run", "count"), passed=("ok", "sum"), cars=("cars_mean", "mean"), pen_share=("pen_share", "mean"),
                  rate_hz=("rate_median_hz", "median"), cbr=("cbr_mean", "mean"), cbr_p95=("cbr_p95", "mean"),
                  cpm_rx=("cpm_rx_after_warmup", "mean"), truck_senders=("truck_senders", "sum")).reset_index()
    if not events.empty:
        e = events.groupby(keys, sort=False)
        ev = e.agg(events=("event", "count"), ego_sees=("ego_sees", "sum"), occlusions=("occlusion", "sum")).reset_index()
        occ = events[events["occlusion"].astype(bool)]                   # the CPMs in the gap, the first age and the witnesses: occlusions only
        if len(occ):
            o = occ.groupby(keys, sort=False).agg(cpm_in_gap=("cpm_in_gap", lambda s: int((s > 0).sum())), cpm_in_gap_mean=("cpm_in_gap", "mean"),
                                                 from_others=("cpm_from_others", lambda s: int((s > 0).sum())), first_cpm_age=("first_cpm_age", "median"),
                                                 witnesses=("witnesses", "median")).reset_index()
            ev = ev.merge(o, on=keys, how="left")
        else:
            for col in ("cpm_in_gap", "cpm_in_gap_mean", "from_others", "first_cpm_age", "witnesses"):
                ev[col] = np.nan
        cells = cells.merge(ev, on=keys, how="left")
    order = {d: i for i, d in enumerate(grid.DENSITIES)}, {p: i for i, p in enumerate(grid.PENETRATIONS)}, {m: i for i, m in enumerate(grid.MESSAGES)}
    cells["_o"] = [(order[0].get(d, 9), order[1].get(p, 9), order[2].get(m, 9)) for d, p, m in zip(cells["density"], cells["pen"], cells["msg"])]
    return cells.sort_values("_o").drop(columns="_o").reset_index(drop=True)


def format_cells(cells: pd.DataFrame) -> str:
    if cells.empty:
        return "no run read"
    lines = ["%-7s %4s %-5s %5s %5s %5s %6s %5s %6s %8s %5s %6s %6s %6s %7s %6s" % (
        "density", "pen", "msg", "runs", "pass", "cars", "pen_dr", "rate", "cbr", "cpm_rx", "ev", "occl", "cpm_g", "others", "age1", "witn")]
    for r in cells.itertuples():
        def f(v, fmt):
            return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else fmt % v
        lines.append("%-7s %4s %-5s %5d %5d %5s %6s %5s %6s %8s %5s %6s %6s %6s %7s %6s" % (
            r.density, r.pen, r.msg, r.runs, r.passed, f(r.cars, "%.0f"), f(r.pen_share, "%.2f"), f(r.rate_hz, "%.1f"), f(r.cbr, "%.3f"),
            f(r.cpm_rx, "%.0f"), f(getattr(r, "events", None), "%d"), f(getattr(r, "occlusions", None), "%d"), f(getattr(r, "cpm_in_gap", None), "%d"),
            f(getattr(r, "from_others", None), "%d"), f(getattr(r, "first_cpm_age", None), "%.2f"), f(getattr(r, "witnesses", None), "%.0f")))
    lines.append("columns: cars = mean cars on the map after 60 s; pen_dr = share of background cars that sent a CPM; rate = median CPM rate of a sender (Hz);")
    lines.append("  cbr = mean channel busy ratio over the CPMs received after 60 s; cpm_rx = CPM rows received per run after 60 s; ev = events read;")
    lines.append("  occl = events with an occlusion; over those: cpm_g = events with a CPM about the pedestrian received by the ego in the gap; others = of")
    lines.append("  them, with a CPM from a car that is not an actor of the event; age1 = median age of the first CPM in the gap (s); witn = median cars")
    lines.append("  seeing the pedestrian through the gap")
    return "\n".join(lines)


def _read_one(folder: str) -> dict:
    try:
        return read_run(folder)
    except Exception as exc:                                  # a broken run must not stop the 431 others
        return finish(dict(run=run_number_of(folder), folder=folder, result="ERROR", events=[], checks={"read": False}, notes=["%s: %s" % (type(exc).__name__, exc)],
                           density=None, pen=None, msg=None, seed=None, kind=None, t_last_s=None, steps=None))


def find_runs(root: str, numbers: list[int] | None = None) -> list[str]:
    out = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        n = grid._number_of_folder(name)
        if n is None or (numbers is not None and n not in numbers):
            continue
        if os.path.isfile(os.path.join(root, name, "parquet", "export_summary.json")):
            out.append(os.path.join(root, name, "parquet"))
    return out


def parse_numbers(text: str) -> list[int]:
    out = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the exported runs of the core grid (task D2): the facts of each run against its cell, the three events.")
    ap.add_argument("root", help="the results folder of the grid (holds the run folders aoc-grid-<n>)")
    ap.add_argument("--out", default=None, help="write one row per run to this CSV file")
    ap.add_argument("--events", default=None, help="write one row per event (three per run) to this CSV file")
    ap.add_argument("--cells", default=None, help="write the table per cell to this CSV file")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="runs read in parallel (default: half the CPUs)")
    ap.add_argument("--runs", default=None, help="only these run numbers, for example 0-11,36 (default: every run found)")
    args = ap.parse_args(argv)
    numbers = parse_numbers(args.runs) if args.runs else None
    folders = find_runs(args.root, numbers)
    if not folders:
        print("no exported run under %s" % args.root)
        return 1
    print("reading %d run(s) with %d job(s)" % (len(folders), args.jobs), flush=True)
    results = None
    if args.jobs > 1:
        try:
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                results = list(pool.map(_read_one, folders))
        except (OSError, PermissionError) as exc:                 # no shared memory for the pool (a chroot, a locked-down system): one by one
            print("parallel reading not possible (%s); reading one by one" % exc, flush=True)
    if results is None:
        results = [_read_one(f) for f in folders]
    runs, events = runs_table(results), events_table(results)
    cells = cells_table(runs, events)
    print(format_cells(cells))
    failed = runs[~runs["ok"].astype(bool)]
    print("runs read: %d of the %d of the grid, passed: %d, failed: %d" % (len(runs), grid.N_RUNS, int(runs["ok"].sum()), len(failed)))
    for r in failed.itertuples():
        print("  run %s (%s/%s/%s seed %s): %s" % (r.run, r.density, r.pen, r.msg, r.seed, r.failed_checks))
    for r in results:
        for note in r.get("notes", []):
            print("  run %s: %s" % (r.get("run"), note))
    if not events.empty:
        occ = events[events["occlusion"].astype(bool)]
        print("events: %d read, %d with the ego seeing the pedestrian, %d with an occlusion; of those, %d with a CPM in the gap, "
              "%d with one from a car that is not an actor" % (len(events), int(events["ego_sees"].sum()), len(occ),
                                                                 int((occ["cpm_in_gap"] > 0).sum()), int((occ["cpm_from_others"] > 0).sum())))
    for path, table in ((args.out, runs), (args.events, events), (args.cells, cells)):
        if path:
            table.to_csv(path, index=False)
            print("written: %s" % path)
    print("result: %s" % ("PASS" if len(failed) == 0 else "FAIL"))
    return 0 if len(failed) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
