"""The single-link runs (task C1): read the grid of runs and check each run against its period and its loss.

The single-link scenario (``artery-ext/link``) puts three cars on a straight road at the same speed. The
receiver knows the object only through the CPMs of the sender. The CPMs are sent with a fixed period T and each
copy is lost with probability p (``AocCpService``). The grid of the plan is T in {1, 0.5, 0.2, 0.1} s and
p in {0, 0.1, 0.3, 0.5}: 16 runs. This module reads the exported runs and checks that each one is what its
settings say, before tasks C2 and C3 compare them with the formulas of the theory note.

    aoc-link ROOT                    # every run folder under ROOT with an export_summary.json
    aoc-link ROOT --grid             # also checks that the 16 runs of the plan are all there, once each

The checks of one run:
  - the period: the median time between two CPMs of a sender is T, within 1%;
  - the loss: the share of the copies dropped, among the copies that arrived or were dropped, is p within
    three standard errors (plus 0.005 for the rounding);
  - the receiver got the object: at least one received row about an object that the receiver's own sensors
    never saw, and the share of the sender's CPMs that reached the receiver (from its first reception on) is at
    least 0.9 (1 - p) - 0.05;
  - the single link: in the scenario of the plan the receiver's own sensors see nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

GRID_T = (1.0, 0.5, 0.2, 0.1)
GRID_P = (0.0, 0.1, 0.3, 0.5)


def parse_time(text) -> float | None:
    """'0.5s' or '500ms' or '0.5' to seconds."""
    if text is None:
        return None
    text = str(text).strip()
    for unit, factor in (("ms", 1e-3), ("us", 1e-6), ("s", 1.0)):
        if text.endswith(unit):
            try:
                return float(text[:-len(unit)]) * factor
            except ValueError:
                return None
    try:
        return float(text)
    except ValueError:
        return None


def find_runs(root: str | os.PathLike) -> list[str]:
    """The folders under ``root`` (itself included) that hold an export_summary.json, in order of name."""
    root = os.fspath(root)
    found = []
    for folder, _, files in os.walk(root):
        if "export_summary.json" in files:
            found.append(folder)
    return sorted(found)


def read_link_run(folder: str | os.PathLike) -> dict:
    """The numbers of one exported run that the single-link check needs. Missing tables give None."""
    folder = os.fspath(folder)
    with open(os.path.join(folder, "export_summary.json"), encoding="utf-8") as fh:
        info = json.load(fh)
    variables = info.get("variables") or {}
    out = dict(folder=folder, run_id=info.get("run_id", ""), config=info.get("config", ""), seed=info.get("seed"),
               T=parse_time(variables.get("T")), p=None if variables.get("p") is None else float(variables["p"]))

    def table(name):
        path = os.path.join(folder, name + ".parquet")
        return pd.read_parquet(path) if os.path.isfile(path) else None

    tx, rx, drop, obs = table("cpm_tx"), table("cpm_rx"), table("cpm_drop"), table("observations")
    out["cpm_sent"] = None if tx is None else int(len(tx))
    out["cpm_received"] = None if rx is None else int(len(rx))
    out["cpm_dropped"] = None if drop is None else int(len(drop))
    # the period: median gap between two CPMs of one sender
    out["period_median"] = None
    if tx is not None and len(tx) > 1:
        gaps = tx.sort_values("t_tx").groupby("tx_id")["t_tx"].diff().dropna().to_numpy(dtype=float)
        out["period_median"] = round(float(np.median(gaps)), 4) if len(gaps) else None
    # the loss: dropped among (received + dropped)
    out["loss_share"] = None
    if rx is not None and drop is not None and len(rx) + len(drop) > 0:
        out["loss_share"] = round(len(drop) / (len(rx) + len(drop)), 4)
    # the delay of a copy
    out["delay_median"] = None
    if rx is not None and len(rx) and "delay" in rx.columns:
        out["delay_median"] = round(float(np.nanmedian(rx["delay"].to_numpy(dtype=float))), 4)
    # what the receivers got about the objects they do not see themselves
    out["links"] = out["object_rows"] = out["age_median"] = out["object_share"] = None
    out["receiver_sees"] = None
    if obs is not None and len(obs):
        is_v2x = (obs["src"] == "v2x").to_numpy(dtype=bool, na_value=False)
        own = obs[~is_v2x]
        seen = set(zip(own["rx_station"].astype(object), own["gt_match_id"].astype(object)))
        v2x = obs[is_v2x]
        v2x = v2x[(v2x["gt_match_id"] != v2x["rx_station"]).to_numpy(dtype=bool, na_value=True)]            # rows about the receiver itself
        unseen = v2x[[(r, o) not in seen for r, o in zip(v2x["rx_station"].astype(object), v2x["gt_match_id"].astype(object))]]
        out["object_rows"] = int(len(unseen))
        out["links"] = int(unseen.groupby(["tx_station", "rx_station", "gt_match_id"]).ngroups)
        out["age_median"] = round(float(unseen["age"].astype(float).median()), 4) if len(unseen) else None
        if tx is not None and len(unseen) and "t_meas" in unseen.columns:
            # share of the sender's CPMs that reached the receiver, from the first one received to the end
            shares = []
            for (sender, receiver, _), rows in unseen.groupby(["tx_station", "rx_station", "gt_match_id"]):
                first = float(rows["t_meas"].astype(float).min())
                sent = tx[(tx["tx_id"] == sender) & (tx["t_tx"] >= first - 1e-6)]
                if len(sent):
                    shares.append(rows["t_meas"].astype(float).nunique() / len(sent))
            out["object_share"] = round(float(np.mean(shares)), 4) if shares else None
        receivers = set(v2x["rx_station"].astype(object))
        out["receiver_sees"] = int(own["rx_station"].isin(receivers).sum())
    return out


def read_grid(root: str | os.PathLike) -> pd.DataFrame:
    """One row per exported run under ``root``."""
    rows = [read_link_run(folder) for folder in find_runs(root)]
    columns = ["folder", "run_id", "config", "seed", "T", "p", "cpm_sent", "cpm_received", "cpm_dropped", "period_median", "loss_share",
               "delay_median", "links", "object_rows", "object_share", "age_median", "receiver_sees"]
    return pd.DataFrame(rows, columns=columns)


def check_grid(table: pd.DataFrame, grid: bool = False, single_link: bool = False) -> list[str]:
    """The problems of a grid of runs; an empty list is a PASS."""
    problems = []
    if not len(table):
        return ["no exported run found"]
    for _, row in table.iterrows():
        name = os.path.basename(os.path.dirname(row["folder"])) or row["folder"]
        if row["T"] is None or row["p"] is None or np.isnan(row["T"]) or np.isnan(row["p"]):
            problems.append("%s: the variables T and p are not in export_summary.json" % name)
            continue
        if row["period_median"] is None or np.isnan(row["period_median"]):
            problems.append("%s: no CPM sent" % name)
        elif abs(row["period_median"] - row["T"]) > 0.01 * row["T"]:
            problems.append("%s: the CPM period is %.3f s, not %.3f s" % (name, row["period_median"], row["T"]))
        n = (row["cpm_received"] or 0) + (row["cpm_dropped"] or 0)
        if row["cpm_dropped"] is None:
            problems.append("%s: no table of the dropped CPMs" % name)
        elif n == 0:
            problems.append("%s: no CPM arrived" % name)
        else:
            tolerance = 3.0 * np.sqrt(row["p"] * (1.0 - row["p"]) / n) + 0.005
            if abs(row["loss_share"] - row["p"]) > tolerance:
                problems.append("%s: the loss is %.3f, not %.2f (tolerance %.3f)" % (name, row["loss_share"], row["p"], tolerance))
        if not row["object_rows"]:
            problems.append("%s: the receiver got nothing about an object it does not see" % name)
        elif row["object_share"] is not None and not np.isnan(row["object_share"]) and row["object_share"] < 0.9 * (1.0 - row["p"]) - 0.05:
            problems.append("%s: the receiver got only %.0f%% of the sender's CPMs about the object" % (name, 100 * row["object_share"]))
        if single_link and row["receiver_sees"]:
            problems.append("%s: the receiver's own sensors see something (%d rows): not a single link" % (name, row["receiver_sees"]))
    if grid:
        cells = {(round(float(t), 3), round(float(p), 3)) for t, p in zip(table["T"], table["p"]) if t is not None and p is not None}
        wanted = {(t, p) for t in GRID_T for p in GRID_P}
        missing = sorted(wanted - cells)
        if missing:
            problems.append("runs missing from the grid: " + ", ".join("T=%g p=%g" % c for c in missing))
        counts = table.groupby(["T", "p"]).size()
        if (counts > 1).any():
            problems.append("some cells of the grid have more than one run: " + ", ".join("T=%g p=%g" % c for c in counts[counts > 1].index))
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read the single-link runs under a folder and check each one against its period T and its loss p.")
    ap.add_argument("root", help="folder that holds the exported runs (each with an export_summary.json)")
    ap.add_argument("--grid", action="store_true", help="also check that the 16 runs of the plan are all there, once each")
    ap.add_argument("--single-link", action="store_true", help="also check that the receiver's own sensors see nothing")
    ap.add_argument("--out", default=None, help="write the table to this file (.csv or .parquet)")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.root):
        print("ERROR: %s is not a folder" % args.root, file=sys.stderr)
        return 2
    table = read_grid(args.root)
    problems = check_grid(table, grid=args.grid, single_link=args.single_link)
    if args.out:
        if args.out.endswith(".parquet"):
            table.to_parquet(args.out, index=False)
        else:
            table.to_csv(args.out, index=False)
    print("C1 LINK CHECK")
    print("  runs:                %d under %s" % (len(table), os.path.abspath(args.root)))
    print("  %-6s %-5s %7s %7s %7s %9s %7s %8s %8s %8s %6s" % ("T", "p", "sent", "got", "dropped", "period", "loss", "delay", "age", "share", "sees"))
    number = lambda value, digits=3: "none" if value is None or (isinstance(value, float) and np.isnan(value)) else ("%%.%df" % digits) % value
    for _, row in table.sort_values(["T", "p"], ascending=[False, True]).iterrows():
        print("  %-6s %-5s %7s %7s %7s %9s %7s %8s %8s %8s %6s" % (number(row["T"], 1), number(row["p"], 1), row["cpm_sent"], row["cpm_received"],
                                                                  row["cpm_dropped"], number(row["period_median"]), number(row["loss_share"]),
                                                                  number(row["delay_median"]), number(row["age_median"]), number(row["object_share"]),
                                                                  row["receiver_sees"]))
    print("  period: median time between two CPMs of a sender. loss: dropped among the copies that arrived or were dropped.")
    print("  delay: median delay of a copy. age: median age of the received objects the receiver does not see itself.")
    print("  share: of the sender's CPMs that reached the receiver. sees: rows of the receivers' own sensors.")
    print("  result:              %s%s" % ("PASS" if not problems else "FAIL", "" if not problems else "\n    " + "\n    ".join(problems)))
    return 0 if not problems else 3


if __name__ == "__main__":
    sys.exit(main())
