"""The check of the AoC generation rule in an exported run (task E1): what the senders did against what the rule
says, and what the receivers got out of it. Standalone: numpy, pandas and pyarrow only, so that the workstation
scripts can embed it.

    aoc-rulecheck RUN_DIR [--against OTHER_RUN_DIR] [--target 0.9] [--delay 0.1] [--repeats 1] [--q class=value,...]
                  [--warmup 10] [--range 200] [--hold 5] [--out DIR]

Three tables, from the raw logs of the run (``parquet/cpm_tx``, ``cpm_obj``, ``cpm_rx``, ``truth``):

  senders      per sender: CPMs per second, bytes per second, bytes and objects per CPM, the share of CPMs with an
               object container
  inclusions   per class and motion state (moving, stopped): the period the rule gives, the intervals between two
               inclusions of the same object by the same sender (median, p10, p90), the share of intervals at the
               period (within one generation step), shorter (the object changed state, or was sent for a
               deviation) and longer (the object was out of view)
  receivers    per receiver: the share of the time an object within range is known with a reliability at or above
               the target (``time_above_target``: the reliability alpha(age) of the latest report received, with
               the curve of the rule, 0 when no report was received in the hold), the share of the time it is
               known at all (``known``), the mean channel busy ratio seen by the receiver. The objects counted are
               the ones without V2X (not in ``nodes``: the pedestrians, and the cars without a station), the ones
               collective perception is for; ``--objects all`` counts every object but the receiver (the stations
               are then known through their CAMs, which these logs do not hold)

With ``--against`` the same tables of a second run (the rules of ETSI, say) are printed next to the first and the
summary gives the ratios: the channel load and the time above target of the first run against the second (H4).
The rule's parameters are those of ``AocCpService.ned`` by default (the fitted q of D3); give the ones of the run.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np
import pandas as pd

Q_DEFAULT = "passenger_car=0.269,heavy_truck=0.118,pedestrian=0.0252,cyclist=0.98,default=0.269"
H4_TOLERANCE = 0.01                    # the time above the target may be this much lower for the same verdict (one point)
MIN_EXPOSURE = 50                      # ticks (5 s of object time) under which a receiver does not count in the mean over the receivers
MOVING = ("moving", 0.3, 0.5)          # the position and speed errors of the sender (NED: sigmaX, sigmaV)
STOPPED = ("stopped", 0.5, 0.05)       # the parked vehicle of the note (sigmaXStopped, sigmaVStopped)


def alpha_of_age(age, q, sigma_x, sigma_v, eps=1.5):
    """The reliability curve of the note: 1 - exp(-eps^2 / (2 (sx^2 + sv^2 age^2 + q age^3 / 3)))."""
    age = np.asarray(age, dtype=float)
    variance = sigma_x ** 2 + sigma_v ** 2 * age ** 2 + q * age ** 3 / 3.0
    return 1.0 - np.exp(-eps ** 2 / (2.0 * variance))


def age_limit(target, q, sigma_x, sigma_v, eps=1.5):
    """The age at which the curve falls to the target (0 when the target is not met even fresh), by bisection like the service."""
    if alpha_of_age(0.0, q, sigma_x, sigma_v, eps) <= target:
        return 0.0
    lo, hi = 0.0, 1.0
    while alpha_of_age(hi, q, sigma_x, sigma_v, eps) > target:
        hi *= 2.0
        if hi > 1e6:
            return hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if alpha_of_age(mid, q, sigma_x, sigma_v, eps) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def period(target, q, sigma_x, sigma_v, eps=1.5, delay=0.1, repeats=1, max_period=1e9):
    """The sending period of the rule: (age limit - delay) / n, never negative, at most max_period."""
    return min(max_period, max(0.0, age_limit(target, q, sigma_x, sigma_v, eps) - delay) / max(1, repeats))


def sent_at(period_s, step=0.1):
    """The interval the service produces for a period: the last generation step at or before the period."""
    return math.floor(period_s / step + 1e-6) * step


def parse_q(text):
    out = {}
    for item in text.split(","):
        name, _, value = item.partition("=")
        out[name.strip()] = float(value)
    if "default" not in out:
        raise SystemExit("--q needs a default entry")
    return out


class Rule:
    """The parameters of the rule, as the service holds them."""

    def __init__(self, target=0.9, eps=1.5, delay=0.1, repeats=1, q=None, sigma_x=0.3, sigma_v=0.5, stopped_speed=0.5, q_stopped=0.001,
                 sigma_x_stopped=0.5, sigma_v_stopped=0.05, step=0.1, max_period=4.5):
        self.target, self.eps, self.delay, self.repeats, self.step, self.max_period = target, eps, delay, repeats, step, max_period
        self.q = q or parse_q(Q_DEFAULT)
        self.moving = (sigma_x, sigma_v)
        self.stopped_speed = stopped_speed
        self.stopped = (q_stopped, sigma_x_stopped, sigma_v_stopped)

    def curve(self, cls, speed):
        """(q, sigma_x, sigma_v, state) of an object of a class at a speed."""
        if self.stopped_speed > 0 and speed < self.stopped_speed:
            return self.stopped + ("stopped",)
        return (self.q.get(cls, self.q["default"]),) + self.moving + ("moving",)

    def period_of(self, cls, speed):
        q, sx, sv, _ = self.curve(cls, speed)
        return period(self.target, q, sx, sv, self.eps, self.delay, self.repeats, self.max_period)

    def alpha(self, age, cls, speed):
        q, sx, sv, _ = self.curve(cls, speed)
        return alpha_of_age(age, q, sx, sv, self.eps)

    def table(self):
        rows = []
        for cls, q in self.q.items():
            rows.append(dict(cls=cls, state="moving", q=q, age_limit=round(age_limit(self.target, q, *self.moving, self.eps), 3),
                             period=round(self.period_of(cls, self.stopped_speed + 1), 3), sent_at=sent_at(self.period_of(cls, self.stopped_speed + 1), self.step)))
        if self.stopped_speed > 0:
            q, sx, sv = self.stopped
            rows.append(dict(cls="any", state="stopped", q=q, age_limit=round(age_limit(self.target, q, sx, sv, self.eps), 3),
                             period=round(self.period_of("any", 0.0), 3), sent_at=sent_at(self.period_of("any", 0.0), self.step)))
        return pd.DataFrame(rows)


def read_run(folder):
    p = os.path.join(folder, "parquet")
    tables = {name: pd.read_parquet(os.path.join(p, name + ".parquet")) for name in ("cpm_tx", "cpm_obj", "cpm_rx", "truth")}
    nodes_path = os.path.join(p, "nodes.parquet")
    tables["nodes"] = pd.read_parquet(nodes_path) if os.path.isfile(nodes_path) else pd.DataFrame(columns=["node_id"])
    info_path = os.path.join(p, "export_summary.json")
    tables["info"] = json.load(open(info_path)) if os.path.isfile(info_path) else {}
    return tables


def senders_table(tx, warmup, t_end):
    """Per sender: the CPM rate, the bytes and the objects."""
    t = tx[tx["t_tx"] >= warmup]
    seconds = max(t_end - warmup, 1e-9)
    t = t.assign(empty=(t["n_objects"] == 0) & (t["has_sensor_info"] == 0) if "has_sensor_info" in t.columns else (t["n_objects"] == 0))
    g = t.groupby("tx_id")
    out = pd.DataFrame({"cpms": g.size(), "bytes": g["size_bytes"].sum(), "objects": g["n_objects"].sum(),
                        "with_objects": g["n_objects"].apply(lambda s: float((s > 0).mean())),
                        "empty_bytes": t.loc[t["empty"]].groupby("tx_id")["size_bytes"].sum().reindex(g.size().index, fill_value=0)})
    out["cpm_rate_hz"] = (out["cpms"] / seconds).round(3)
    out["bytes_per_s"] = (out["bytes"] / seconds).round(1)
    out["bytes_per_s_without_empty"] = ((out["bytes"] - out["empty_bytes"]) / seconds).round(1)       # the CPMs with nothing in them
    out["bytes_per_cpm"] = (out["bytes"] / out["cpms"]).round(1)
    out["objects_per_cpm"] = (out["objects"] / out["cpms"]).round(2)
    out["with_objects"] = out["with_objects"].round(3)
    return out.reset_index().sort_values("tx_id").reset_index(drop=True)


def inclusions_table(obj, rule, warmup, tx=None):
    """Per class and state: the intervals between two inclusions of the same object by the same sender."""
    if tx is not None and "ref_lag" in tx.columns and "ref_lag" not in obj.columns:
        obj = obj.merge(tx[["station_id", "reference_time_ms", "ref_lag"]].drop_duplicates(["station_id", "reference_time_ms"]),
                        on=["station_id", "reference_time_ms"], how="left")
    columns = ["cls", "state", "intervals", "period", "sent_at", "median", "p10", "p90", "at_period", "shorter", "shorter_state_change", "longer",
               "longer_state_change"]
    o = obj[(obj["t_tx"] >= warmup) & obj["gt_match_id"].notna()].copy()
    o["speed"] = o["speed_cms"].fillna(0).astype(float) / 100.0
    # the lag of a report: the position of the sender is older than the step (ref_lag), the measurement older still (meas_delta)
    o["lag"] = (o["ref_lag"].fillna(0).astype(float) if "ref_lag" in o.columns else 0.0) + (
        o["meas_delta_ms"].fillna(0).clip(lower=0).astype(float) / 1000.0 if "meas_delta_ms" in o.columns else 0.0)
    o = o.sort_values(["tx_id", "gt_match_id", "t_tx"])
    g = o.groupby(["tx_id", "gt_match_id"])
    o["next_t"], o["next_speed"], o["next_lag"] = g["t_tx"].shift(-1), g["speed"].shift(-1), g["lag"].shift(-1)
    o = o[o["next_t"].notna()].copy()
    if o.empty:
        return pd.DataFrame(columns=columns)
    o["interval"] = (o["next_t"] - o["t_tx"]).round(3)
    o["state"] = [rule.curve(cls, speed)[3] for cls, speed in zip(o["class"], o["speed"])]
    o["state_next"] = [rule.curve(cls, speed)[3] for cls, speed in zip(o["class"], o["next_speed"])]
    # the service sends at the last step at or before (period - lag), with the lag of the report it sends
    o["expected"] = [sent_at(rule.period_of(cls, speed) - lag, rule.step) for cls, speed, lag in zip(o["class"], o["speed"], o["next_lag"])]
    tol = rule.step / 2 + 1e-6
    rows = []
    for (cls, state), part in o.groupby(["class", "state"], sort=True):
        period_s = rule.period_of(cls, 0.0 if state == "stopped" else rule.stopped_speed + 1)
        exp = part["expected"]
        at = (part["interval"] - exp).abs() <= tol
        shorter = part["interval"] < exp - tol
        longer = part["interval"] > exp + tol
        changed = part["state_next"] != part["state"]
        rows.append(dict(cls=cls, state=state, intervals=int(len(part)), period=round(period_s, 3), sent_at=sent_at(period_s, rule.step),
                         median=round(float(part["interval"].median()), 3), p10=round(float(part["interval"].quantile(0.1)), 3),
                         p90=round(float(part["interval"].quantile(0.9)), 3), at_period=round(float(at.mean()), 3), shorter=round(float(shorter.mean()), 3),
                         shorter_state_change=round(float((shorter & changed).mean()), 3), longer=round(float(longer.mean()), 3),
                         longer_state_change=round(float((longer & changed).mean()), 3)))
    return pd.DataFrame(rows, columns=columns)


def receivers_table(tables, rule, warmup, t_end, range_m, hold, receivers=None, objects="unequipped"):
    """Per receiver: the share of the time an object within range is known with alpha at or above the target."""
    truth, rx, obj = tables["truth"], tables["cpm_rx"], tables["cpm_obj"]
    stations = set(tables["nodes"]["node_id"].dropna().astype(str)) if objects == "unequipped" else set()
    reports = rx.merge(obj[["station_id", "reference_time_ms", "gt_match_id", "t_meas", "speed_cms", "class"]], on=["station_id", "reference_time_ms"], how="inner")
    reports = reports[reports["gt_match_id"].notna()]
    columns = ["rx_id", "pairs", "known", "time_above_target", "cbr_mean"]
    if truth.empty or not np.isfinite(t_end) or t_end <= warmup:
        return pd.DataFrame(columns=columns)
    ticks = np.round(np.arange(math.ceil(warmup * 10) / 10.0, t_end + 1e-9, 0.1), 1)
    truth = truth.copy()
    truth["t"] = truth["t"].round(1)
    truth = truth[truth["t"].isin(set(ticks))]
    X = truth.pivot_table(index="t", columns="obj_id", values="x").reindex(ticks)
    Y = truth.pivot_table(index="t", columns="obj_id", values="y").reindex(ticks)
    objects_list = [str(o) for o in X.columns]
    Xv, Yv = X.to_numpy(dtype=float), Y.to_numpy(dtype=float)
    cls_of = truth.drop_duplicates("obj_id").set_index("obj_id")["class"].to_dict()
    cbr = rx[rx["t_rx"] >= warmup].groupby("rx_id")["cbr"].mean()
    wanted = sorted(set(reports["rx_id"].unique()) | set(cbr.index)) if receivers is None else sorted(receivers)
    moving_speed = rule.stopped_speed + 1.0
    rows = []
    for r in wanted:
        if r not in objects_list:
            continue
        k = objects_list.index(r)
        d = np.hypot(Xv - Xv[:, [k]], Yv - Yv[:, [k]])
        in_range = np.isfinite(d) & (d <= range_m)
        in_range[:, k] = False
        for j, o in enumerate(objects_list):
            if o in stations:
                in_range[:, j] = False
        pairs = int(in_range.sum())
        mine = reports[reports["rx_id"] == r]
        above = known = 0
        for o, rep in mine.groupby("gt_match_id"):
            if o not in objects_list:
                continue
            j = objects_list.index(o)
            if not in_range[:, j].any():
                continue
            rep = rep.sort_values("t_rx")
            t_rx = rep["t_rx"].to_numpy(dtype=float)
            t_meas = np.maximum.accumulate(rep["t_meas"].to_numpy(dtype=float))     # the freshest report received so far
            speed = rep["speed_cms"].fillna(0).to_numpy(dtype=float) / 100.0
            idx = np.searchsorted(t_rx, ticks, side="right") - 1
            have = idx >= 0
            safe = np.clip(idx, 0, None)
            age = np.where(have, ticks - t_meas[safe], np.inf)
            fresh = have & (age <= hold)
            age0 = np.where(fresh, age, 0.0)
            alpha = rule.alpha(age0, cls_of.get(o, "unknown"), moving_speed)
            if rule.stopped_speed > 0:                                              # a stopped report takes the parked curve
                q, sx, sv = rule.stopped
                alpha = np.where(speed[safe] < rule.stopped_speed, alpha_of_age(age0, q, sx, sv, rule.eps), alpha)
            alpha = np.where(fresh, alpha, 0.0)
            known += int((in_range[:, j] & fresh).sum())
            above += int((in_range[:, j] & (alpha >= rule.target)).sum())
        rows.append(dict(rx_id=r, pairs=pairs, known=round(known / pairs, 4) if pairs else np.nan, time_above_target=round(above / pairs, 4) if pairs else np.nan,
                         cbr_mean=round(float(cbr.get(r, np.nan)), 4)))
    return pd.DataFrame(rows, columns=columns)


def analyse(folder, rule, warmup, range_m, hold, receivers=None, objects="unequipped"):
    tables = read_run(folder)
    t_end = float(tables["info"].get("t_last_s") or tables["truth"]["t"].max())
    out = dict(senders=senders_table(tables["cpm_tx"], warmup, t_end), inclusions=inclusions_table(tables["cpm_obj"], rule, warmup, tables["cpm_tx"]),
               receivers=receivers_table(tables, rule, warmup, t_end, range_m, hold, receivers, objects))
    s, r = out["senders"], out["receivers"]
    exposed = r[r["pairs"] >= MIN_EXPOSURE]                               # the receivers with enough object time for a share
    pairs = float(r["pairs"].sum()) if len(r) else 0.0
    out["summary"] = dict(folder=folder, label=str(tables["info"].get("variables") or tables["info"].get("iteration_vars") or ""), seconds=round(t_end - warmup, 1),
                          senders=int(len(s)), cpm_rate_hz=round(float(s["cpm_rate_hz"].mean()), 3) if len(s) else np.nan,
                          bytes_per_s=round(float(s["bytes_per_s"].sum()), 1) if len(s) else np.nan,
                          bytes_per_s_without_empty=round(float(s["bytes_per_s_without_empty"].sum()), 1) if len(s) else np.nan,
                          objects_per_cpm=round(float(s["objects"].sum() / max(s["cpms"].sum(), 1)), 2) if len(s) else np.nan,
                          cbr_mean=round(float(r["cbr_mean"].mean()), 4) if len(r) else np.nan,
                          pairs=int(pairs), receivers=int(len(r)), receivers_exposed=int(len(exposed)),
                          time_above_target=round(float((r["time_above_target"] * r["pairs"]).sum() / pairs), 4) if pairs else np.nan,     # pooled over the pairs
                          known=round(float((r["known"] * r["pairs"]).sum() / pairs), 4) if pairs else np.nan,
                          # the mean over the receivers with enough object time
                          time_above_target_receivers=round(float(exposed["time_above_target"].mean()), 4) if len(exposed) else np.nan)
    return out


def format_report(result, against=None, rule=None):
    lines = []
    if rule is not None:
        lines.append("the rule: target %.2f, tolerance %.2f m, delay %.2f s, n = %d, step %.1f s, period at most %.1f s" % (
            rule.target, rule.eps, rule.delay, rule.repeats, rule.step, rule.max_period))
        lines.append(rule.table().to_string(index=False))
        lines.append("")
    for name, res in (("run", result), ("against", against)):
        if res is None:
            continue
        s = res["summary"]
        lines.append("%s: %s (%s), %.0f s after the warm-up" % (name, s["folder"], s["label"], s["seconds"]))
        lines.append("  senders %d, CPM rate %.3f Hz per sender, %.0f bytes/s in all (%.0f without the empty CPMs), %.2f objects per CPM; CBR %.4f" % (
            s["senders"], s["cpm_rate_hz"], s["bytes_per_s"], s["bytes_per_s_without_empty"], s["objects_per_cpm"], s["cbr_mean"]))
        lines.append("  receivers %d (%d with %d ticks of object time or more): time above target %.4f pooled over the %d object ticks "
                     "(%.4f on average over the exposed receivers), known %.4f" % (
                         s["receivers"], s["receivers_exposed"], MIN_EXPOSURE, s["time_above_target"], s["pairs"], s["time_above_target_receivers"], s["known"]))
        lines.append("  inclusions per class and state:")
        lines.append("    " + res["inclusions"].to_string(index=False).replace("\n", "\n    "))
    if against is not None:
        a, b = result["summary"], against["summary"]
        ratio = lambda k: (a[k] / b[k]) if (b[k] and np.isfinite(b[k]) and np.isfinite(a[k])) else np.nan
        lines.append("")
        lines.append("run against the other: bytes/s %.3f (%.3f without the empty CPMs), CPM rate %.3f, CBR %.3f, time above target %.3f, known %.3f" % (
            ratio("bytes_per_s"), ratio("bytes_per_s_without_empty"), ratio("cpm_rate_hz"), ratio("cbr_mean"), ratio("time_above_target"), ratio("known")))
        for load, name in (("cbr_mean", "the channel busy ratio, the load of the plan"), ("bytes_per_s_without_empty", "the CPM bytes without the empty CPMs")):
            less_load = a[load] <= 0.8 * b[load] and a["time_above_target"] >= b["time_above_target"] - H4_TOLERANCE
            more_time = a["time_above_target"] > b["time_above_target"] + H4_TOLERANCE and a[load] <= b[load] * (1 + H4_TOLERANCE)
            lines.append("H4 on this run, the load as %s: %s" % (name, "PASS" if (less_load or more_time) else "not met"))
        lines.append("(the plan: the target reached with at least 20 %% less load, or more time above the target at equal load; within %.0f point here; "
                     "the busy ratio here is the mean over the receptions, which leans to the busy moments)" % (100 * H4_TOLERANCE))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check the AoC generation rule in an exported run (task E1).")
    ap.add_argument("run", help="the exported run folder (with parquet/)")
    ap.add_argument("--against", default=None, help="a second run to compare with (the rules of ETSI, say)")
    ap.add_argument("--target", type=float, default=0.9)
    ap.add_argument("--eps", type=float, default=1.5)
    ap.add_argument("--delay", type=float, default=0.1)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--q", default=Q_DEFAULT, help="q per class, class=value,... with a default")
    ap.add_argument("--sigma-x", type=float, default=0.3)
    ap.add_argument("--sigma-v", type=float, default=0.5)
    ap.add_argument("--stopped-speed", type=float, default=0.5, help="m/s; 0 turns the stopped state off")
    ap.add_argument("--q-stopped", type=float, default=0.001)
    ap.add_argument("--sigma-x-stopped", type=float, default=0.5)
    ap.add_argument("--sigma-v-stopped", type=float, default=0.05)
    ap.add_argument("--step", type=float, default=0.1, help="the generation step of the service (minInterval)")
    ap.add_argument("--max-period", type=float, default=4.5, help="the period of the rule never exceeds this (maxPeriod of the service)")
    ap.add_argument("--warmup", type=float, default=10.0)
    ap.add_argument("--range", type=float, default=200.0, help="the range of the receiver metric, m")
    ap.add_argument("--hold", type=float, default=5.0, help="a report older than this is forgotten by the receiver metric")
    ap.add_argument("--objects", choices=["unequipped", "all"], default="unequipped", help="the objects of the receiver metric (default: the ones without V2X)")
    ap.add_argument("--out", default=None, help="write the tables (CSV) and the summary (JSON) there")
    args = ap.parse_args(argv)
    rule = Rule(args.target, args.eps, args.delay, args.repeats, parse_q(args.q), args.sigma_x, args.sigma_v, args.stopped_speed, args.q_stopped,
                args.sigma_x_stopped, args.sigma_v_stopped, args.step, args.max_period)
    try:
        result = analyse(args.run, rule, args.warmup, args.range, args.hold, objects=args.objects)
        against = analyse(args.against, rule, args.warmup, args.range, args.hold, objects=args.objects) if args.against else None
    except FileNotFoundError as exc:
        print("ERROR: %s" % exc)
        return 2
    print(format_report(result, against, rule))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for name, res in (("run", result), ("against", against)):
            if res is None:
                continue
            for table in ("senders", "inclusions", "receivers"):
                res[table].to_csv(os.path.join(args.out, "%s_%s.csv" % (name, table)), index=False)
        with open(os.path.join(args.out, "summary.json"), "w") as fh:
            json.dump({"run": result["summary"], "against": against["summary"] if against else None}, fh, indent=1)
        print("written: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
