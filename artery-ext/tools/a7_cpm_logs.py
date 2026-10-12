#!/usr/bin/env python3
"""
A7: locate and export the CPM transmission and reception logs of an Artery run.

The collective perception service of Artery (artery.envmod.service.CpService)
records two result vectors per vehicle, in the module World.node[i].middleware.CP:
  transmission:vector(cpmStationId), transmission:vector(cpmReferenceTime)
  reception:vector(cpmStationId),    reception:vector(cpmReferenceTime)
Each sent or received CPM gives one row in each vector: the simulation time,
the station id of the sender and the reference time of the CPM (milliseconds
since 2004-01-01, the ITS time base).

This script reads the OMNeT++ vector file (*.vec, text format), writes
  cpm_tx.csv     one row per CPM sent
  cpm_rx.csv     one row per CPM received, matched to the CPM sent
  cpm_links.csv  one row per link (sender, receiver): counts and gaps
  a7_summary.txt and a7_summary.json
and prints the summary.

Times: t_tx_s and t_rx_s are simulation times. The reference time of a CPM is
the time of the sender's own position data. The script converts it to
simulation time with the start date of the run (parameter middleware.datetime),
so that ages can be computed:
  ref_lag_ms    in cpm_tx.csv: sending time minus reference time
  age_at_rx_ms  in cpm_rx.csv: reception time minus reference time It needs Python 3.8 or newer and no extra package.

Usage:  python3 a7_cpm_logs.py RESULT_DIR_OR_VEC_FILE [--out OUT_DIR]
"""
import argparse
import csv
import datetime
import glob
import json
import os
import re
import statistics
import sys

CP_VECTOR = re.compile(r"^(transmission|reception):vector\((cpmStationId|cpmReferenceTime)\)$")
NODE = re.compile(r"\.node\[(\d+)\]\.")
DATETIME = re.compile(r'^param\s+\S*node\S*middleware\.datetime\s+"\\?"?([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8})')
ITS_EPOCH = datetime.datetime(2004, 1, 1)


def find_vec(path):
    if os.path.isfile(path):
        return path
    files = sorted(glob.glob(os.path.join(path, "*.vec")), key=os.path.getmtime)
    if not files:
        sys.exit("A7: no .vec file in %s. Was the run started with vector recording on?" % path)
    return files[-1]


def read_vec(path):
    """Return the run attributes, and the CP vectors as {(node, direction, field): [(event, time, value), ...]}."""
    attrs, wanted, data, all_names = {}, {}, {}, set()
    attrs["_base_s"] = None
    with open(path, "r", errors="replace") as f:
        for line in f:
            c = line[0] if line else ""
            if c.isdigit():
                parts = line.split(None, 1)
                key = wanted.get(parts[0])
                if key is None:
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    data[key].append((int(parts[1]), float(parts[2]), float(parts[3])))
            elif line.startswith("vector "):
                parts = line.split()
                if len(parts) < 4:
                    continue
                vid, module, name = parts[1], parts[2], parts[3]
                all_names.add(name)
                m = CP_VECTOR.match(name)
                n = NODE.search(module + ".")
                if m and n:
                    key = (int(n.group(1)), m.group(1), m.group(2))
                    wanted[vid] = key
                    data.setdefault(key, [])
            elif line.startswith("param "):
                m = DATETIME.match(line)
                if m and attrs["_base_s"] is None:
                    try:
                        start = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                        attrs["_base_s"] = (start - ITS_EPOCH).total_seconds()
                    except ValueError:
                        pass
            elif line.startswith("attr "):
                parts = line.rstrip("\n").split(" ", 2)
                if len(parts) == 3:
                    attrs[parts[1]] = parts[2].strip('"')
    return attrs, data, all_names


def pair(data, node, direction):
    """Join the station id vector and the reference time vector of one node on the event number."""
    ids = data.get((node, direction, "cpmStationId"), [])
    refs = {(e, t): v for e, t, v in data.get((node, direction, "cpmReferenceTime"), [])}
    rows = []
    # several CPMs can share one event number only on reception of different senders, never in practice;
    # the vectors are written in the same order, so the position is the safe fallback
    ref_list = data.get((node, direction, "cpmReferenceTime"), [])
    for i, (e, t, sid) in enumerate(ids):
        ref = refs.get((e, t))
        if ref is None and i < len(ref_list):
            ref = ref_list[i][2]
        rows.append((t, int(sid), int(ref) if ref is not None else None))
    return rows


def pct(values, q):
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser(description="Export the CPM tx/rx logs of an Artery run to CSV.")
    ap.add_argument("results", help="result folder of the run, or the .vec file")
    ap.add_argument("--out", default=None, help="output folder (default: next to the .vec file)")
    args = ap.parse_args()

    vec = find_vec(args.results)
    out = args.out or os.path.dirname(os.path.abspath(vec))
    os.makedirs(out, exist_ok=True)
    attrs, data, all_names = read_vec(vec)
    if not data:
        has_mock = any("Cpm" in n or "cpm" in n for n in all_names)
        sys.exit("A7: the file %s has no CPM vector (transmission:vector(cpmStationId)). "
                 "Check that the CP service was enabled and that result-recording-modes=all was set.%s"
                 % (vec, "" if has_mock else " No vector name contains 'cpm' at all."))

    nodes = sorted({k[0] for k in data})
    tx = {n: pair(data, n, "transmission") for n in nodes}
    rx = {n: pair(data, n, "reception") for n in nodes}

    # which node owns which station id (a station id can change with a pseudonym change)
    owner, sent_at = {}, {}
    for n in nodes:
        for t, sid, ref in tx[n]:
            owner.setdefault(sid, n)
            sent_at[(sid, ref)] = t

    tx_rows = [(t, n, sid, ref) for n in nodes for t, sid, ref in tx[n]]
    tx_rows.sort()

    # reference time (ms since 2004-01-01) -> simulation time: subtract the time base of the run
    offsets = [ref / 1000.0 - t for t, _, _, ref in tx_rows if ref is not None]
    base = attrs.get("_base_s")
    base_source = "start date of the run (middleware.datetime)"
    if base is None or (offsets and not (-1.0 <= base - max(offsets) <= 1.0)):
        base = max(offsets) if offsets else float("nan")
        base_source = "estimated from the data (start date not found in the vector file)"

    def ref_sim(ref):
        return None if ref is None else ref / 1000.0 - base

    ref_lag = []
    with open(os.path.join(out, "cpm_tx.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_tx_s", "tx_node", "station_id", "reference_time_ms", "ref_lag_ms"])
        for t, n, sid, ref in tx_rows:
            lag = None if ref is None else (t - ref_sim(ref)) * 1000.0
            if lag is not None:
                ref_lag.append(lag)
            w.writerow(["%.6f" % t, n, sid, ref, "" if lag is None else "%.3f" % lag])

    rx_rows, unmatched, delays, ages = [], 0, [], []
    links = {}
    for n in nodes:
        for t, sid, ref in rx[n]:
            t_tx = sent_at.get((sid, ref))
            src = owner.get(sid)
            if t_tx is None:
                unmatched += 1
                delay = None
            else:
                delay = (t - t_tx) * 1000.0
                delays.append(delay)
            age = None if ref is None else (t - ref_sim(ref)) * 1000.0
            if age is not None:
                ages.append(age)
            rx_rows.append((t, n, src, sid, ref, t_tx, delay, age))
            if src is not None:
                links.setdefault((src, n), []).append(t)
    rx_rows.sort(key=lambda r: (r[0], r[1]))
    with open(os.path.join(out, "cpm_rx.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_rx_s", "rx_node", "tx_node", "station_id", "reference_time_ms", "t_tx_s", "delay_ms",
                    "age_at_rx_ms"])
        for t, n, src, sid, ref, t_tx, delay, age in rx_rows:
            w.writerow(["%.6f" % t, n, "" if src is None else src, sid, ref,
                        "" if t_tx is None else "%.6f" % t_tx, "" if delay is None else "%.3f" % delay,
                        "" if age is None else "%.3f" % age])

    # per link: number of CPMs and gaps between two receptions in a row
    gaps_all = []
    sent_count = {n: len(tx[n]) for n in nodes}
    with open(os.path.join(out, "cpm_links.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tx_node", "rx_node", "received", "first_rx_s", "last_rx_s", "gap_median_s", "gap_p95_s", "gap_max_s"])
        for (src, dst), times in sorted(links.items()):
            times.sort()
            gaps = [b - a for a, b in zip(times, times[1:])]
            gaps_all.extend(gaps)
            w.writerow([src, dst, len(times), "%.3f" % times[0], "%.3f" % times[-1],
                        "" if not gaps else "%.3f" % statistics.median(gaps),
                        "" if not gaps else "%.3f" % pct(gaps, 0.95),
                        "" if not gaps else "%.3f" % max(gaps)])

    # sending interval of each vehicle, and lag of the reference time behind the sending time
    intervals = []
    for n in nodes:
        ts = [t for t, _, _ in tx[n]]
        intervals.extend(b - a for a, b in zip(ts, ts[1:]))

    t_end = max([r[0] for r in tx_rows] + [r[0] for r in rx_rows] + [0.0])
    n_tx, n_rx = len(tx_rows), len(rx_rows)
    summary = {
        "vec_file": vec,
        "run": attrs.get("runnumber", ""), "config": attrs.get("configname", ""), "network": attrs.get("network", ""),
        "last_cpm_time_s": round(t_end, 3),
        "vehicles_with_cp_service": len(nodes),
        "vehicles_that_sent": sum(1 for n in nodes if sent_count[n] > 0),
        "cpm_sent": n_tx, "cpm_received": n_rx,
        "cpm_received_matched_to_a_sent_cpm": n_rx - unmatched,
        "receivers_per_cpm_mean": round(n_rx / n_tx, 2) if n_tx else 0.0,
        "links_with_traffic": len(links),
        "tx_interval_s": {"median": round(statistics.median(intervals), 3) if intervals else None,
                          "p95": round(pct(intervals, 0.95), 3) if intervals else None,
                          "max": round(max(intervals), 3) if intervals else None},
        "tx_rate_hz_mean": round(1.0 / statistics.mean(intervals), 2) if intervals else None,
        "delay_ms": {"median": round(statistics.median(delays), 3) if delays else None,
                     "p95": round(pct(delays, 0.95), 3) if delays else None,
                     "max": round(max(delays), 3) if delays else None},
        "rx_gap_s": {"median": round(statistics.median(gaps_all), 3) if gaps_all else None,
                     "p95": round(pct(gaps_all, 0.95), 3) if gaps_all else None,
                     "max": round(max(gaps_all), 3) if gaps_all else None},
        "reference_time_lag_ms": {"median": round(statistics.median(ref_lag), 1) if ref_lag else None,
                                  "max": round(max(ref_lag), 1) if ref_lag else None},
        "age_at_reception_ms": {"median": round(statistics.median(ages), 1) if ages else None,
                                "p95": round(pct(ages, 0.95), 1) if ages else None,
                                "max": round(max(ages), 1) if ages else None},
        "time_base": base_source,
        "files": {k: os.path.join(out, k) for k in ("cpm_tx.csv", "cpm_rx.csv", "cpm_links.csv")},
    }
    with open(os.path.join(out, "a7_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    s = summary
    lines = [
        "A7 CPM LOG SUMMARY",
        "  vector file:        %s" % vec,
        "  config, network:    %s, %s" % (s["config"], s["network"]),
        "  vehicles with CP:   %d (%d sent at least one CPM)" % (s["vehicles_with_cp_service"], s["vehicles_that_sent"]),
        "  CPMs sent:          %d" % n_tx,
        "  CPMs received:      %d (%d matched to a sent CPM), %.1f receivers per CPM on average"
        % (n_rx, n_rx - unmatched, s["receivers_per_cpm_mean"]),
        "  sending interval:   median %s s, 95%% below %s s, longest %s s (mean rate %s CPM/s per vehicle)"
        % (s["tx_interval_s"]["median"], s["tx_interval_s"]["p95"], s["tx_interval_s"]["max"], s["tx_rate_hz_mean"]),
        "  delay from sending to reception: median %s ms, 95%% below %s ms, longest %s ms"
        % (s["delay_ms"]["median"], s["delay_ms"]["p95"], s["delay_ms"]["max"]),
        "  gap between two receptions on one link: median %s s, 95%% below %s s, longest %s s (%d links)"
        % (s["rx_gap_s"]["median"], s["rx_gap_s"]["p95"], s["rx_gap_s"]["max"], s["links_with_traffic"]),
        "  reference time is older than the sending time by: median %s ms, at most %s ms"
        % (s["reference_time_lag_ms"]["median"], s["reference_time_lag_ms"]["max"]),
        "  age of a CPM when it is received (reception minus reference time): median %s ms, 95%% below %s ms, longest %s ms"
        % (s["age_at_reception_ms"]["median"], s["age_at_reception_ms"]["p95"], s["age_at_reception_ms"]["max"]),
        "  time base:          %s" % base_source,
        "  files:              %s" % out,
        "                      cpm_tx.csv, cpm_rx.csv, cpm_links.csv, a7_summary.json",
    ]
    text = "\n".join(lines)
    with open(os.path.join(out, "a7_summary.txt"), "w") as f:
        f.write(text + "\n")
    print(text)

    ok = n_tx > 0 and n_rx > 0 and (n_rx - unmatched) >= 0.99 * n_rx
    if not ok:
        print("A7 CHECK FAILED: sent %d, received %d, matched %d" % (n_tx, n_rx, n_rx - unmatched))
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
