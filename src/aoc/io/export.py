#!/usr/bin/env python3
"""
A8: export the logs of the AoC logger (CSV) to parquet, with the schema of the AoC plan.

Input: the folder written by the AocLogger module of Artery for one run
       (<result-dir>/aoc-<configname>-<runnumber>), with the files
       truth.csv, local.csv, cpm_tx.csv, cpm_obj.csv, cpm_rx.csv, cbr.csv, nodes.csv, run.csv

Output, in <input>/parquet:
  truth.parquet         true state of every object at every step (0.1 s)
  cpm_tx.parquet        one row per CPM sent
  cpm_obj.parquet       one row per object of each CPM sent, with its true identity
  cpm_rx.parquet        one row per CPM received, matched to the CPM sent
  local.parquet         one row per (vehicle, sensor, object) detection
  observations.parquet  the table of the AoC plan: one row per object that a vehicle gets,
                        from its own sensors (one row per step) or from a CPM (one row per reception)
  export_summary.json   counts and checks

Frame: x to the east, y to the south, in meters (the frame of Artery and OMNeT++).
Times are simulation times in seconds.

Usage:  python a8_export.py RUN_DIR [--out OUT_DIR] [--weather NAME] [--pdr-window 1.0]
Needs:  pandas and pyarrow.
"""
import argparse
import json
import math
import os
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

EXPORT_VERSION = "1.1"

# station types of ETSI (vanetza::geonet::StationType)
STATION_TYPES = {0: "unknown", 1: "pedestrian", 2: "cyclist", 3: "moped", 4: "motorcycle", 5: "passenger_car", 6: "bus",
                 7: "light_truck", 8: "heavy_truck", 9: "trailer", 10: "special_vehicle", 11: "tram", 15: "rsu"}
# class written in the CPM by CpService: vehicle sub class = traffic participant type, VRU profile number
VRU_PROFILES = {1: "pedestrian", 2: "cyclist", 3: "motorcycle", 4: "animal"}
# sensor category of Artery -> source name in the observations table
SOURCES = {"Radar": "radar", "FoV": "fov", "see-through": "see_through", "CA": "cam_msg"}
# channel busy ratio -> state, limits of the reactive scheme of ETSI TS 102 687 V1.2.1 (Table A.1, informative)
DCC_LIMITS = [0.30, 0.40, 0.50, 0.60]
DCC_STATES = ["relaxed", "active_1", "active_2", "active_3", "restrictive"]

# columns of the observations table, in the order of the plan, then the extra columns
OBS_COLUMNS = [
    ("run_id", "string", "identifier of the simulation run (OMNeT++ run id)"),
    ("seed", "int32", "seed set of the run"),
    ("t_sim", "float64", "time of the row in s: the step for a sensor row, the reception time for a CPM row"),
    ("rx_station", "string", "vehicle that gets the object (SUMO id)"),
    ("src", "string", "source: v2x for a CPM, else the sensor category (radar, fov, see_through, cam_msg)"),
    ("tx_station", "string", "sender of the CPM (SUMO id); empty for a sensor row"),
    ("obj_id", "string", "identifier given by the source: CPM object id, or track id of the local environment model"),
    ("class", "string", "class of the object as given by the source"),
    ("x", "float64", "position east in m"),
    ("y", "float64", "position south in m"),
    ("vx", "float64", "speed east in m/s"),
    ("vy", "float64", "speed south in m/s"),
    ("P11", "float64", "position covariance, xx in m2; empty until the noise model is defined"),
    ("P12", "float64", "position covariance, xy in m2; empty until the noise model is defined"),
    ("P22", "float64", "position covariance, yy in m2; empty until the noise model is defined"),
    ("Pv11", "float64", "speed covariance, xx; empty until the noise model is defined"),
    ("Pv22", "float64", "speed covariance, yy; empty until the noise model is defined"),
    ("exist_score", "float64", "existence score of the source; empty until the evidence model is defined"),
    ("sender_conf", "float64", "confidence declared by the sender, 0 to 1: CPM object perception quality / 15"),
    ("t_meas", "float64", "time of the measurement in s, as declared by the source"),
    ("t_rx", "float64", "time at which the receiver gets the row in s"),
    ("age", "float64", "t_rx minus t_meas in s"),
    ("cbr", "float64", "last channel busy ratio measured by the receiver, 0 to 1"),
    ("dcc_state", "string", "state deduced from cbr with the limits of ETSI TS 102 687 Table A.1"),
    ("pdr_window", "float64", "share of the CPMs of this sender received by this receiver in the last window"),
    ("weather", "string", "weather of the run (option --weather)"),
    ("gt_match_id", "string", "true object behind the row (SUMO id)"),
    # extra columns
    ("sensor", "string", "name of the sensor for a sensor row"),
    ("gt_dist", "float64", "distance in m between the row and the true object at t_pos"),
    ("t_pos", "float64", "time of the true state that the position comes from in s"),
    ("tx_station_id", "int64", "ETSI station id of the sender of the CPM"),
    ("reference_time_ms", "int64", "reference time of the CPM, ms since 2004-01-01"),
    ("meas_src", "string", "where the sender got the measurement of a CPM object from: an on-board sensor category, or cam_msg"),
]


def die(msg):
    print("A8 EXPORT ERROR: " + msg)
    sys.exit(2)


def read_csv(run_dir, name, dtypes):
    path = os.path.join(run_dir, name)
    if not os.path.isfile(path):
        die("the file %s is missing. Was the run made with the network AocWorld?" % path)
    return pd.read_csv(path, dtype=dtypes, keep_default_na=False, na_values=[""])


def velocity(speed, heading_deg):
    """Speed and heading (degrees from north, clockwise) to components east and south."""
    h = np.deg2rad(heading_deg)
    return speed * np.sin(h), -speed * np.cos(h)


def last_at_or_before(sorted_times, t):
    """For each t, the largest value of sorted_times that is <= t (NaN if none)."""
    idx = np.searchsorted(sorted_times, t, side="right") - 1
    out = np.where(idx >= 0, sorted_times[np.clip(idx, 0, None)], np.nan)
    return out


def cpm_class(label):
    if not isinstance(label, str) or not label:
        return ""
    kind, _, num = label.partition(":")
    try:
        n = int(num)
    except ValueError:
        return kind
    if kind == "vehicle":
        return STATION_TYPES.get(n, "vehicle_%d" % n)
    if kind == "vru":
        return VRU_PROFILES.get(n, "vru_%d" % n)
    return kind


def dcc_state(cbr):
    idx = np.searchsorted(DCC_LIMITS, cbr, side="right")
    out = np.array(DCC_STATES, dtype=object)[np.clip(idx, 0, len(DCC_STATES) - 1)]
    return np.where(np.isnan(cbr), None, out)


def write_parquet(df, path, descriptions=None):
    table = pa.Table.from_pandas(df, preserve_index=False)
    if descriptions:
        fields = []
        for f in table.schema:
            d = descriptions.get(f.name)
            fields.append(f.with_metadata({"description": d}) if d else f)
        table = table.cast(pa.schema(fields, metadata=table.schema.metadata))
    pq.write_table(table, path, compression="zstd")


def main():
    ap = argparse.ArgumentParser(description="Export the AoC logger files of one run to parquet.")
    ap.add_argument("run_dir", help="folder aoc-<configname>-<runnumber> of the run")
    ap.add_argument("--out", default=None, help="output folder (default: RUN_DIR/parquet)")
    ap.add_argument("--weather", default="", help="weather label stored in the observations table")
    ap.add_argument("--pdr-window", type=float, default=1.0, help="window of pdr_window in s (default 1.0)")
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        die("%s is not a folder" % run_dir)
    out = args.out or os.path.join(run_dir, "parquet")
    os.makedirs(out, exist_ok=True)

    # ---------------------------------------------------------------- read
    run = read_csv(run_dir, "run.csv", {"key": "string", "value": "string"})
    run = dict(zip(run["key"], run["value"].fillna("")))
    run_id = run.get("run_id", "")
    try:
        seed = int(run.get("seed_set", "0") or 0)
    except ValueError:
        seed = 0

    truth = read_csv(run_dir, "truth.csv", {"obj_id": "string", "station_type": "Int64"})
    nodes = read_csv(run_dir, "nodes.csv", {"node": "string", "node_id": "string", "station_id": "Int64", "module": "string"})
    tx = read_csv(run_dir, "cpm_tx.csv", {"tx_node": "string", "tx_id": "string", "station_id": "int64", "reference_time_ms": "int64"})
    obj = read_csv(run_dir, "cpm_obj.csv", {"station_id": "int64", "reference_time_ms": "int64", "object_id": "Int64",
                                            "class_label": "string", "sensor_ids": "string", "object_age_ms": "Int64",
                                            "quality": "Int64", "speed_cms": "Int64", "direction_raw": "Int64"})
    rx = read_csv(run_dir, "cpm_rx.csv", {"rx_node": "string", "rx_id": "string", "station_id": "int64", "reference_time_ms": "int64"})
    local = read_csv(run_dir, "local.csv", {"node": "string", "node_id": "string", "sensor": "string", "category": "string",
                                            "obj_id": "string", "track_id": "int64"})
    cbr = read_csv(run_dir, "cbr.csv", {"node": "string", "node_id": "string"})

    checks = {}
    if truth.empty:
        die("truth.csv has no row: the simulation had no object")

    # ---------------------------------------------------------------- truth
    truth["vx"], truth["vy"] = velocity(truth["speed"].to_numpy(float), truth["heading_deg"].to_numpy(float))
    truth["class"] = truth["station_type"].map(STATION_TYPES).astype("string")
    truth = truth.sort_values(["obj_id", "t"], kind="stable").reset_index(drop=True)
    steps = np.sort(truth["t"].unique())
    # check of the speed components against the displacement between two steps
    g = truth.groupby("obj_id", sort=False)
    dt = g["t"].shift(-1) - truth["t"]
    ok = (dt > 0) & (dt < 0.11)
    ex = ((g["x"].shift(-1) - truth["x"]) / dt - (truth["vx"] + g["vx"].shift(-1)) / 2)[ok]
    ey = ((g["y"].shift(-1) - truth["y"]) / dt - (truth["vy"] + g["vy"].shift(-1)) / 2)[ok]
    err = np.hypot(ex, ey)
    checks["truth_velocity_error_mps_median"] = round(float(err.median()), 4) if len(err) else None
    checks["truth_velocity_error_mps_p95"] = round(float(err.quantile(0.95)), 4) if len(err) else None

    truth_cols = ["t", "obj_id", "x", "y", "speed", "heading_deg", "vx", "vy", "length", "width", "station_type", "class"]
    truth = truth[truth_cols]

    # ---------------------------------------------------------------- CPMs sent
    tx = tx.sort_values("t_tx", kind="stable").reset_index(drop=True)
    tx["ref_lag"] = tx["t_tx"] - tx["t_ref"]
    key = ["station_id", "reference_time_ms"]
    dup = int(tx.duplicated(key).sum())
    checks["cpm_tx_duplicate_keys"] = dup
    if dup:
        tx = tx.drop_duplicates(key, keep="first")

    # ---------------------------------------------------------------- objects of the CPMs
    obj = obj.merge(tx[key + ["t_tx", "t_ref", "tx_id", "ego_x", "ego_y"]], on=key, how="left", validate="many_to_one")
    checks["cpm_obj_without_cpm"] = int(obj["t_tx"].isna().sum())
    obj = obj[obj["t_tx"].notna()].reset_index(drop=True)
    obj["x"] = obj["ego_x"] + obj["x_cm"] / 100.0
    obj["y"] = obj["ego_y"] + obj["y_cm"] / 100.0
    obj["t_meas"] = obj["t_ref"] - obj["meas_delta_ms"] / 1000.0
    # the position written in the CPM is the true position at the last step of the environment model
    obj["t_pos"] = last_at_or_before(steps, obj["t_tx"].to_numpy(float))
    obj["class"] = obj["class_label"].map(cpm_class).astype("string")

    # true object behind each CPM object: the nearest true object at t_pos, the sender excluded
    gt_id = np.full(len(obj), None, dtype=object)
    gt_dist = np.full(len(obj), np.nan)
    gt_heading = np.full(len(obj), np.nan)
    truth_by_t = {t: d for t, d in truth.groupby("t", sort=False)}
    for t_pos, idx in obj.groupby("t_pos", sort=False).indices.items():
        d = truth_by_t.get(t_pos)
        if d is None or d.empty:
            continue
        tid = d["obj_id"].to_numpy(object)
        txy = d[["x", "y"]].to_numpy(float)
        th = d["heading_deg"].to_numpy(float)
        o = obj.iloc[idx]
        oxy = o[["x", "y"]].to_numpy(float)
        dist = np.hypot(oxy[:, None, 0] - txy[None, :, 0], oxy[:, None, 1] - txy[None, :, 1])
        dist[o["tx_id"].to_numpy(object)[:, None] == tid[None, :]] = np.inf
        j = np.argmin(dist, axis=1)
        best = dist[np.arange(len(idx)), j]
        found = np.isfinite(best)
        gt_id[idx[found]] = tid[j[found]]
        gt_dist[idx[found]] = best[found]
        gt_heading[idx[found]] = th[j[found]]
    obj["gt_match_id"] = pd.array(gt_id, dtype="string")
    obj["gt_dist"] = gt_dist
    matched = obj["gt_dist"] <= 0.02
    checks["cpm_obj_rows"] = int(len(obj))
    checks["cpm_obj_matched_within_2cm"] = int(matched.sum())
    checks["cpm_obj_gt_dist_max_m"] = round(float(obj["gt_dist"].max()), 4) if len(obj) else None
    obj.loc[~matched, "gt_match_id"] = pd.NA

    # speed components: the speed value of the CPM, with the true heading of the matched object.
    # The direction written in the CPM by Artery (commit e0ddd8c) is in tenths of a radian instead of
    # tenths of a degree, so it is kept as direction_raw but not used.
    speed = obj["speed_cms"].astype("float64") / 100.0
    obj["vx"], obj["vy"] = velocity(speed.to_numpy(float), np.where(matched, gt_heading, np.nan))
    raw = obj["direction_raw"].astype("float64")
    both = matched & raw.notna()
    if both.any():
        as_rad = np.abs(((raw[both] / 10.0) - np.deg2rad(gt_heading[both.to_numpy()]) + math.pi) % (2 * math.pi) - math.pi)
        as_deg = np.abs(((raw[both] / 10.0) - gt_heading[both.to_numpy()] + 180.0) % 360.0 - 180.0)
        checks["cpm_direction_error_if_tenths_of_radian_median_rad"] = round(float(np.median(as_rad)), 3)
        checks["cpm_direction_error_if_tenths_of_degree_median_deg"] = round(float(np.median(as_deg)), 1)
    obj["sender_conf"] = obj["quality"].astype("float64") / 15.0

    # where the sender got each measurement from: the sensor of the sender whose last detection of the
    # matched object is at the declared measurement time (to the millisecond). An on-board sensor wins
    # over the CAM sensor when both detected the object at that time.
    det = local[["node_id", "obj_id", "category", "t_last"]].copy()
    det["ms"] = np.floor(det["t_last"].to_numpy(float) * 1000.0 + 1e-6).astype("int64")
    det["src"] = det["category"].map(SOURCES).fillna(det["category"])
    det["rank"] = np.where(det["category"] == "CA", 1, 0)
    det = det.sort_values("rank", kind="stable").drop_duplicates(["node_id", "obj_id", "ms"], keep="first")
    obj["_ms"] = np.round(obj["t_meas"].to_numpy(float) * 1000.0).astype("int64")
    origin = obj.merge(det[["node_id", "obj_id", "ms", "src"]], left_on=["tx_id", "gt_match_id", "_ms"],
                       right_on=["node_id", "obj_id", "ms"], how="left")["src"].to_numpy(object)
    obj["meas_src"] = pd.array(origin, dtype="string")
    obj = obj.drop(columns=["_ms"])
    counts = obj["meas_src"].fillna("unknown").value_counts()
    checks["cpm_obj_by_origin"] = {str(k): int(v) for k, v in counts.items()}
    obj["delta_positive"] = obj["meas_delta_ms"] > 0
    checks["cpm_obj_measured_before_reference_time_share"] = round(float(obj["delta_positive"].mean()), 4) if len(obj) else None

    # ---------------------------------------------------------------- CPMs received
    rx = rx.merge(tx[key + ["t_tx", "t_ref", "tx_id", "ego_x", "ego_y", "n_objects"]], on=key, how="left", validate="many_to_one")
    checks["cpm_rx_rows"] = int(len(rx))
    checks["cpm_rx_matched_to_a_sent_cpm"] = int(rx["t_tx"].notna().sum())
    rx["delay"] = rx["t_rx"] - rx["t_tx"]
    rx["age_ref"] = rx["t_rx"] - rx["t_ref"]
    rx["distance"] = np.hypot(rx["rx_x"] - rx["ego_x"], rx["rx_y"] - rx["ego_y"])
    rx = rx.sort_values(["t_rx", "rx_id"], kind="stable").reset_index(drop=True)

    # share of the CPMs of the sender received on this link in the last window
    w = args.pdr_window
    pdr = np.full(len(rx), np.nan)
    tx_times = {k: np.sort(v.to_numpy(float)) for k, v in tx.groupby("tx_id")["t_tx"]}
    for (tx_id, rx_id), idx in rx[rx["tx_id"].notna()].groupby(["tx_id", "rx_id"], sort=False).indices.items():
        rows = rx.index[rx["tx_id"].notna()][idx]
        t_rx = rx.loc[rows, "t_rx"].to_numpy(float)
        t_sent_rx = np.sort(rx.loc[rows, "t_tx"].to_numpy(float))        # sending times of the CPMs received on this link
        sent = tx_times.get(tx_id, np.array([]))
        n_sent = np.searchsorted(sent, t_rx, side="right") - np.searchsorted(sent, t_rx - w, side="right")
        n_got = np.searchsorted(t_sent_rx, t_rx, side="right") - np.searchsorted(t_sent_rx, t_rx - w, side="right")
        pdr[rows] = np.where(n_sent > 0, n_got / np.maximum(n_sent, 1), np.nan)
    rx["pdr_window"] = np.clip(pdr, 0.0, 1.0)

    # last channel busy ratio of the receiver
    cbr = cbr.drop_duplicates(["node_id", "t"], keep="last").sort_values("t", kind="stable")

    def with_cbr(df, t_col, id_col):
        if cbr.empty or df.empty:
            df["cbr"] = np.nan
            return df
        order = df[t_col].argsort(kind="stable")
        left = df.iloc[order].copy()
        left["_row"] = order
        merged = pd.merge_asof(left, cbr[["t", "node_id", "cbr"]].rename(columns={"t": "_t_cbr", "node_id": id_col}),
                               left_on=t_col, right_on="_t_cbr", by=id_col, direction="backward")
        merged = merged.sort_values("_row", kind="stable").drop(columns=["_row", "_t_cbr"])
        merged.index = df.index
        return merged

    rx = with_cbr(rx, "t_rx", "rx_id")

    # ---------------------------------------------------------------- local detections
    local = local.merge(truth[["t", "obj_id", "x", "y", "vx", "vy", "class"]], on=["t", "obj_id"], how="left")
    checks["local_rows"] = int(len(local))
    checks["local_rows_with_true_state"] = int(local["x"].notna().sum())
    local["src"] = local["category"].map(SOURCES).fillna(local["category"]).astype("string")
    local = with_cbr(local, "t", "node_id")

    # ---------------------------------------------------------------- observations
    n_loc = len(local)
    obs_local = pd.DataFrame({
        "t_sim": local["t"], "rx_station": local["node_id"], "src": local["src"], "tx_station": pd.array([pd.NA] * n_loc, dtype="string"),
        "obj_id": local["track_id"].astype("string"), "class": local["class"], "x": local["x"], "y": local["y"],
        "vx": local["vx"], "vy": local["vy"], "sender_conf": np.nan, "t_meas": local["t_last"], "t_rx": local["t"],
        "cbr": local["cbr"], "pdr_window": np.nan, "gt_match_id": local["obj_id"], "sensor": local["sensor"],
        "gt_dist": np.where(local["x"].notna(), 0.0, np.nan), "t_pos": local["t"],
        "tx_station_id": pd.array([pd.NA] * n_loc, dtype="Int64"), "reference_time_ms": pd.array([pd.NA] * n_loc, dtype="Int64"),
        "meas_src": local["src"],
    })
    v2x = rx[rx["t_tx"].notna()].merge(
        obj[key + ["object_id", "class", "x", "y", "vx", "vy", "sender_conf", "t_meas", "t_pos", "gt_match_id", "gt_dist", "meas_src"]],
        on=key, how="inner")
    obs_v2x = pd.DataFrame({
        "t_sim": v2x["t_rx"], "rx_station": v2x["rx_id"], "src": pd.array(["v2x"] * len(v2x), dtype="string"), "tx_station": v2x["tx_id"],
        "obj_id": v2x["object_id"].astype("string"), "class": v2x["class"], "x": v2x["x"], "y": v2x["y"],
        "vx": v2x["vx"], "vy": v2x["vy"], "sender_conf": v2x["sender_conf"], "t_meas": v2x["t_meas"], "t_rx": v2x["t_rx"],
        "cbr": v2x["cbr"], "pdr_window": v2x["pdr_window"], "gt_match_id": v2x["gt_match_id"],
        "sensor": pd.array([pd.NA] * len(v2x), dtype="string"), "gt_dist": v2x["gt_dist"], "t_pos": v2x["t_pos"],
        "tx_station_id": v2x["station_id"].astype("Int64"), "reference_time_ms": v2x["reference_time_ms"].astype("Int64"),
        "meas_src": v2x["meas_src"],
    })
    obs = pd.concat([obs_local, obs_v2x], ignore_index=True)
    obs["run_id"] = pd.array([run_id] * len(obs), dtype="string")
    obs["seed"] = np.int32(seed)
    for c in ("P11", "P12", "P22", "Pv11", "Pv22", "exist_score"):
        obs[c] = np.nan
    obs["age"] = obs["t_rx"] - obs["t_meas"]
    obs["dcc_state"] = pd.array(dcc_state(obs["cbr"].to_numpy(float)), dtype="string")
    obs["weather"] = pd.array([args.weather or pd.NA] * len(obs), dtype="string")
    obs = obs.sort_values(["t_sim", "rx_station", "src"], kind="stable").reset_index(drop=True)
    obs = obs[[c for c, _, _ in OBS_COLUMNS]]
    for c, typ, _ in OBS_COLUMNS:
        if typ == "string":
            obs[c] = obs[c].astype("string")
        elif typ == "int64":
            obs[c] = obs[c].astype("Int64")
        elif typ == "int32":
            obs[c] = obs[c].astype("int32")
        else:
            obs[c] = obs[c].astype("float64")

    # ---------------------------------------------------------------- write
    obj_out = obj.drop(columns=["ego_x", "ego_y", "delta_positive"])
    write_parquet(truth, os.path.join(out, "truth.parquet"))
    write_parquet(tx, os.path.join(out, "cpm_tx.parquet"))
    write_parquet(obj_out, os.path.join(out, "cpm_obj.parquet"))
    write_parquet(rx, os.path.join(out, "cpm_rx.parquet"))
    write_parquet(local, os.path.join(out, "local.parquet"))
    write_parquet(nodes, os.path.join(out, "nodes.parquet"))
    write_parquet(obs, os.path.join(out, "observations.parquet"), {c: d for c, _, d in OBS_COLUMNS})

    # ---------------------------------------------------------------- summary and verdict
    v2x_rows = int((obs["src"] == "v2x").sum())
    summary = {
        "export_version": EXPORT_VERSION, "logger_version": run.get("logger_version", ""), "run_dir": run_dir, "out_dir": out,
        "run_id": run_id, "config": run.get("config", ""), "seed": seed, "weather": args.weather,
        "t_first_s": float(steps[0]), "t_last_s": float(steps[-1]), "steps": int(len(steps)),
        "objects_true": int(truth["obj_id"].nunique()), "vehicles_with_middleware": int(nodes["node_id"].nunique()),
        "rows": {"truth": int(len(truth)), "cpm_tx": int(len(tx)), "cpm_obj": int(len(obj)), "cpm_rx": int(len(rx)),
                 "local": int(len(local)), "observations": int(len(obs)), "observations_v2x": v2x_rows,
                 "observations_local": int(len(obs)) - v2x_rows},
        "objects_per_cpm_mean": round(float(tx["n_objects"].mean()), 2) if len(tx) else None,
        "age_v2x_s": {"median": round(float(obs.loc[obs["src"] == "v2x", "age"].median()), 4) if v2x_rows else None,
                      "p95": round(float(obs.loc[obs["src"] == "v2x", "age"].quantile(0.95)), 4) if v2x_rows else None,
                      "max": round(float(obs.loc[obs["src"] == "v2x", "age"].max()), 4) if v2x_rows else None},
        "sources": {str(k): int(v) for k, v in obs["src"].value_counts().items()},
        "classes_true": {str(k): int(v) for k, v in truth.drop_duplicates("obj_id")["class"].value_counts().items()},
        "checks": checks,
        "size_bytes": {f: os.path.getsize(os.path.join(out, f)) for f in sorted(os.listdir(out)) if f.endswith(".parquet")},
    }
    problems = []
    if len(tx) == 0:
        problems.append("no CPM was sent")
    if len(rx) == 0:
        problems.append("no CPM was received")
    if checks["cpm_rx_matched_to_a_sent_cpm"] < checks["cpm_rx_rows"]:
        problems.append("some received CPMs have no sent CPM")
    if checks["cpm_obj_rows"] and checks["cpm_obj_matched_within_2cm"] < 0.999 * checks["cpm_obj_rows"]:
        problems.append("some CPM objects have no true object within 2 cm")
    step_rows = local["category"] != "CA"
    if int(local.loc[step_rows, "x"].isna().sum()) > 0:
        problems.append("some sensor detections have no true state")
    if checks["truth_velocity_error_mps_median"] is not None and checks["truth_velocity_error_mps_median"] > 0.5:
        problems.append("the speed components do not agree with the displacement")
    summary["problems"] = problems
    summary["result"] = "PASS" if not problems else "FAIL"
    with open(os.path.join(out, "export_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    r = summary["rows"]
    c = checks
    print("A8 EXPORT SUMMARY")
    print("  run:                 %s (config %s, seed set %d)" % (run_id, summary["config"], seed))
    print("  time covered:        %.1f s to %.1f s, %d steps" % (steps[0], steps[-1], len(steps)))
    print("  true objects:        %d (%s)" % (summary["objects_true"], ", ".join("%d %s" % (v, k) for k, v in summary["classes_true"].items())))
    print("  CPMs:                %d sent, %d received, %.1f objects per CPM on average" % (r["cpm_tx"], r["cpm_rx"], summary["objects_per_cpm_mean"] or 0))
    print("  observations:        %d rows = %d from CPMs + %d from on-board sensors" % (r["observations"], r["observations_v2x"], r["observations_local"]))
    print("  sources:             " + ", ".join("%s %d" % (k, v) for k, v in summary["sources"].items()))
    a = summary["age_v2x_s"]
    print("  age of CPM objects:  median %s s, 95%% below %s s, longest %s s" % (a["median"], a["p95"], a["max"]))
    print("  check, CPM objects matched to a true object within 2 cm: %d of %d (largest distance %s m)"
          % (c["cpm_obj_matched_within_2cm"], c["cpm_obj_rows"], c["cpm_obj_gt_dist_max_m"]))
    print("  check, received CPMs matched to a sent CPM: %d of %d" % (c["cpm_rx_matched_to_a_sent_cpm"], c["cpm_rx_rows"]))
    print("  check, sensor detections with a true state: %d of %d" % (c["local_rows_with_true_state"], c["local_rows"]))
    print("  check, speed components against displacement: median error %s m/s" % c["truth_velocity_error_mps_median"])
    if "cpm_direction_error_if_tenths_of_radian_median_rad" in c:
        print("  note, direction field of the CPM: median error %s rad if read as tenths of a radian, %s deg if read as tenths of a degree"
              % (c["cpm_direction_error_if_tenths_of_radian_median_rad"], c["cpm_direction_error_if_tenths_of_degree_median_deg"]))
    print("  share of CPM objects measured before the reference time: %s" % c["cpm_obj_measured_before_reference_time_share"])
    print("  CPM objects by origin of the measurement: " + ", ".join("%s %d" % (k, v) for k, v in c["cpm_obj_by_origin"].items()))
    total = sum(summary["size_bytes"].values())
    print("  files:               %s (%.1f MB of parquet)" % (out, total / 1e6))
    print("  result:              %s%s" % (summary["result"], "" if not problems else " (" + "; ".join(problems) + ")"))
    return 0 if not problems else 3


if __name__ == "__main__":
    sys.exit(main())
