"""Tests of the quality check of the core grid (aoc.analysis.gridcheck, task D2) on handmade runs: the tables of an
exported run written directly, with three events, background cars, a known penetration and CPM rate."""
import json
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis import grid, gridcheck
from aoc.io.export import write_parquet

STEP = 0.1
RATES = {"etsi": 5.0, "2hz": 2.0, "10hz": 10.0}
STARTS = (90.0, 180.0, 270.0)


def write_grid_run(folder: pathlib.Path, n: int, *, result: str = "PASS", background: int = 20, t_end: float = 330.0, drop_actor: str | None = None,
                   pen_share: float | None = None, rate: float | None = None, no_cpm_event: int | None = None, variables: dict | None = None,
                   blink_event: int | None = None, tables: tuple = ("truth", "nodes", "cpm_tx", "cpm_obj", "cpm_rx", "local", "observations")):
    """A run of the grid number n: 3 events (truck, ego, helpers, pedestrian), ``background`` cars, the penetration and the
    CPM rate of its cell unless given. The ego sees the pedestrian, loses it 2.7 s behind the truck, sees it again (in the
    event ``blink_event`` it loses it for 0.3 s only); helper_a sees it throughout and sends it in its CPMs; the ego receives
    them. ``drop_actor`` leaves one actor out of every table."""
    cell = grid.cell_of_run(n)
    pen_share = cell["pen"] / 100.0 if pen_share is None else pen_share
    rate = RATES[cell["msg"]] if rate is None else rate
    period = 1.0 / rate
    times = np.round(np.arange(STEP, t_end + STEP / 2, STEP), 1)
    truth, local, obs, tx, rx = [], [], [], [], []
    nodes = ["flow_%d" % i for i in range(background)]
    senders = set(nodes[: int(round(pen_share * background))])
    for i, car in enumerate(nodes):                                   # the background cars: present all along, in a line far away
        for t in times:
            truth.append((t, car, 500.0 + 10 * i, 500.0, "passenger_car"))
        if car in senders:
            for t in np.arange(1.0, t_end, period):
                tx.append((round(float(t), 3), car))
    for k, start in enumerate(STARTS, start=1):
        a = gridcheck.actors_of(k)
        ped = "ped_%d" % k
        t_out = start + 16.6                                          # the pedestrian out of the alley
        for who in a.values():
            if who != drop_actor:
                nodes.append(who)
        hidden = (-62.0, -24.0) if k != blink_event else (-40.0, -36.0)
        for t in times:
            if start - 20 <= t <= start + 40:
                for who in a.values():                                # the actors: the truck stands at (0, 5); the ego comes from the south
                    if who == drop_actor:
                        continue
                    if who == a["ego"]:
                        x, y = -62.0 + 13.89 * (t - t_out - 1.1), -3.0
                    elif who == a["truck"]:
                        x, y = -6.0, 3.0
                    else:
                        x, y = 30.0 - 13.0 * (t - t_out), 8.0
                    truth.append((t, who, x, y, "passenger_car"))
            if t_out - 16.6 <= t <= t_out + 14:
                truth.append((t, ped, 1.0, 20.0 - 1.25 * max(0.0, t - t_out), "pedestrian"))
                if t >= t_out:                                        # out of the alley: helper_a sees it; the ego too, but for a gap of 2.7 s
                    if a["helper_a"] != drop_actor:
                        local.append((t, a["helper_a"], ped))
                    ego_x = -62.0 + 13.89 * (t - t_out - 1.1)
                    if -80.0 <= ego_x <= 0.0 and not (hidden[0] < ego_x < hidden[1]) and a["ego"] != drop_actor:
                        local.append((t, a["ego"], ped))
        for who in (a["ego"], a["helper_a"], a["helper_b"], a["helper_c"]):
            if who == drop_actor:
                continue
            for t in np.arange(start - 19.5, start + 40, period):
                tx.append((round(float(t), 3), who))
                if who == a["helper_a"] and t_out <= t <= t_out + 14 and k != no_cpm_event:
                    obs.append((round(float(t) + 0.05, 3), a["ego"], "v2x", who, ped, 0.1, "pedestrian"))
                rx.append((round(float(t) + 0.002, 3), a["ego"], 0.12, 0.002))
    folder.mkdir(parents=True, exist_ok=True)
    truth = pd.DataFrame(truth, columns=["t", "obj_id", "x", "y", "class"])
    truth["station_type"] = np.where(truth["class"] == "pedestrian", 1, 5)
    out = {}
    out["truth"] = truth
    out["nodes"] = pd.DataFrame({"node_id": nodes, "node": ["node[%d]" % i for i in range(len(nodes))]})
    out["local"] = pd.DataFrame(local, columns=["t", "node_id", "obj_id"])
    o = pd.DataFrame(obs, columns=["t_rx", "rx_station", "src", "tx_station", "gt_match_id", "age", "class"])
    o["t_sim"] = o["t_rx"]
    own = pd.DataFrame(local, columns=["t_sim", "rx_station", "gt_match_id"])
    own["src"], own["tx_station"], own["t_rx"], own["age"], own["class"] = "radar", None, own["t_sim"], 0.0, "pedestrian"
    out["observations"] = pd.concat([o, own], ignore_index=True)[["t_sim", "rx_station", "src", "tx_station", "gt_match_id", "t_rx", "age", "class"]]
    out["cpm_tx"] = pd.DataFrame(tx, columns=["t_tx", "tx_id"])
    out["cpm_rx"] = pd.DataFrame(rx, columns=["t_rx", "rx_id", "cbr", "delay"])
    out["cpm_obj"] = pd.DataFrame({"station_id": pd.Series([], dtype="int64"), "object_id": pd.Series([], dtype="int64")})
    for name in tables:
        write_parquet(out[name], folder / (name + ".parquet"))
    info = dict(result=result, t_last_s=float(times[-1]), steps=int(len(times)), seed=cell["seed"],
                variables=dict(density=cell["density"], pen=str(cell["pen"]), msg=cell["msg"]) if variables is None else variables,
                receivers=[a for a in gridcheck.all_actors()] + nodes[:8], rows={})
    (folder / "export_summary.json").write_text(json.dumps(info), encoding="utf-8")
    return folder


def test_a_good_run_passes_and_its_events_are_read(tmp_path):
    folder = write_grid_run(tmp_path / "aoc-grid-13" / "parquet", 13)                 # low, 10, 2hz, seed 1
    r = gridcheck.read_run(str(folder))
    assert r["ok"], r["checks"]
    assert r["run"] == 13 and r["density"] == "low" and r["pen"] == 10 and r["msg"] == "2hz" and r["seed"] == 1 and r["kind"] == "evaluation"
    assert r["pedestrians"] == 3 and r["actors_present"] == 15 and r["background_cars"] == 20 and r["background_eligible"] == 20 and r["vehicles"] == 35
    assert r["background_senders"] == 2 and r["pen_share"] == 0.1 and r["rate_median_hz"] == pytest.approx(2.0, abs=0.05)
    assert r["cars_mean"] == pytest.approx(20 + 5 * 3 * 60 / 270, abs=1.0) and r["truck_senders"] == 0
    assert r["cbr_mean"] == pytest.approx(0.12) and r["delay_median_ms"] == pytest.approx(2.0) and r["cpm_rx_after_warmup"] > 0
    assert len(r["events"]) == 3
    for k, e in enumerate(r["events"], start=1):
        assert e["event"] == k and e["ego"] == "ego_%d" % k and e["ego_present"] and e["ego_middleware"] and e["ped_present"] and e["ego_sees"]
        assert e["occlusion"] and e["gap"] == pytest.approx(2.8, abs=0.2) and e["lost_distance"] < 100 and e["seen_again_distance"] < 40
        assert e["cpm_in_gap"] >= 4 and e["cpm_from_actors"] == e["cpm_in_gap"] and e["cpm_from_others"] == 0
        assert e["first_cpm_age"] == pytest.approx(0.1) and 0 < e["first_cpm_delay"] < 0.6
        assert e["witnesses"] == 1 and e["actor_witnesses"] == 1 and e["cpm_senders"] == 1


@pytest.mark.parametrize("change, failed", [
    (dict(result="FAIL"), {"export_passed"}),
    (dict(t_end=200.0), {"covers_the_run", "three_pedestrians"}),             # the third pedestrian never comes
    (dict(drop_actor="helper_b_2"), {"actors_present", "egos_and_helpers_send"}),
    (dict(pen_share=0.6), {"penetration_as_drawn"}),
    (dict(pen_share=0.25, background=60, n=301), {"penetration_as_drawn"}),        # 25 % drawn in a 10 % cell (high/10/2hz): 0.15 off, 3 sigma + 0.02 = 0.14
    (dict(rate=4.0), {"rate_matches_msg"}),
    (dict(rate=2.15), {"rate_matches_msg"}),                                        # the senders of 2hz are strictly periodic
    (dict(variables=dict(density="high", pen="10", msg="2hz")), {"cell_matches"}),
    (dict(tables=("nodes", "cpm_tx", "cpm_obj", "cpm_rx", "local", "observations")), {"tables_present"}),     # no truth
])
def test_the_checks_catch_the_defects(tmp_path, change, failed):
    n = change.pop("n", 13)
    r = gridcheck.read_run(str(write_grid_run(tmp_path / ("aoc-grid-%d" % n) / "parquet", n, **change)))
    assert not r["ok"] and {k for k, v in r["checks"].items() if not v} == failed


def test_near_the_edges_of_the_bands(tmp_path):
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-301" / "parquet", 301, pen_share=0.18, background=60)))  # 0.08 off: fine
    assert r["checks"]["penetration_as_drawn"]
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-13b" / "parquet", 13, rate=2.05)))
    assert r["checks"]["rate_matches_msg"]
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-108" / "parquet", 108, pen_share=0.95)))                 # low, 100, etsi: 0.95 < 0.97
    assert not r["checks"]["penetration_as_drawn"]
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-108b" / "parquet", 108, pen_share=1.0)))
    assert r["checks"]["penetration_as_drawn"]


def test_a_blink_is_not_an_occlusion_and_is_left_out_of_the_cell_counts(tmp_path):
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-0" / "parquet", 0, blink_event=2)))
    e = r["events"][1]
    assert r["ok"] and e["ego_sees"] and not e["occlusion"] and e["gap"] < 1.0 and e["cpm_in_gap"] >= 0      # the longest gap is kept, marked
    runs, events = gridcheck.runs_table([r]), gridcheck.events_table([r])
    cells = gridcheck.cells_table(runs, events)
    assert cells.loc[0, "events"] == 3 and cells.loc[0, "occlusions"] == 2 and cells.loc[0, "cpm_in_gap"] == 2
    assert "events: 3 read" not in gridcheck.format_cells(cells)


def test_a_run_without_middleware_tables_or_with_an_absent_ego(tmp_path):
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-0" / "parquet", 0, drop_actor="ego_3")))
    e = r["events"][2]
    assert not r["ok"] and not e["ego_present"] and not e["ego_middleware"] and not e["ego_sees"] and e["gap"] is None and e["cpm_in_gap"] == 0
    assert r["checks"]["actors_present"] is False and r["checks"]["egos_and_helpers_send"] is False
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-1", 1)))                                 # the run folder, not its parquet subfolder
    assert r["ok"] and r["run"] == 1
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-999" / "parquet", 0)))                  # a number outside the grid
    assert r["run"] == 999 and r["density"] == "low" and not r["checks"]["cell_matches"]
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-2" / "parquet", 2, tables=("truth", "nodes", "cpm_tx", "cpm_obj", "cpm_rx", "observations"))))
    assert not r["ok"] and {k for k, v in r["checks"].items() if not v} == {"tables_present"} and any("no local table" in n for n in r["notes"])
    assert all(e["witnesses"] == 1 for e in r["events"])                                                    # helper_a is a receiver: still counted


def test_an_event_without_cpms_is_reported_not_failed(tmp_path):
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-0" / "parquet", 0, no_cpm_event=2)))
    assert r["ok"] and r["events"][1]["cpm_in_gap"] == 0 and r["events"][1]["first_cpm_age"] is None and r["events"][0]["cpm_in_gap"] > 0


def test_the_etsi_rate_band_and_the_tables(tmp_path):
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-24" / "parquet", 24, rate=6.0)))     # low, 10, 10hz: 6 Hz is wrong
    assert not r["checks"]["rate_matches_msg"]
    r = gridcheck.read_run(str(write_grid_run(tmp_path / "aoc-grid-1" / "parquet", 1, rate=6.0)))       # low, 10, etsi: 6 Hz is fine
    assert r["checks"]["rate_matches_msg"]


def test_the_command_writes_the_tables(tmp_path):
    for n in (0, 1, 2, 12, 13):
        write_grid_run(tmp_path / ("aoc-grid-%d" % n) / "parquet", n, background=10)
    write_grid_run(tmp_path / "aoc-grid-14" / "parquet", 14, background=10, drop_actor="truck_3")
    (tmp_path / "aoc-grid-15").mkdir()                                                # not exported: skipped
    out, events, cells = tmp_path / "runs.csv", tmp_path / "events.csv", tmp_path / "cells.csv"
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.gridcheck", str(tmp_path), "--out", str(out), "--events", str(events),
                           "--cells", str(cells), "--jobs", "2"], capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "reading 6 run(s)" in proc.stdout and "runs read: 6 of the 432 of the grid, passed: 5, failed: 1" in proc.stdout
    assert "run 14 (low/10/2hz seed 2): actors_present" in proc.stdout
    assert "result: FAIL" in proc.stdout
    assert "events: 18 read, 18 with the ego seeing the pedestrian, 18 with an occlusion; of those, 18 with a CPM in the gap" in proc.stdout
    runs, ev, ce = pd.read_csv(out), pd.read_csv(events), pd.read_csv(cells)
    assert len(runs) == 6 and runs["ok"].sum() == 5 and len(ev) == 18 and ev["occlusion"].all()
    assert len(ce) == 2 and ce["runs"].tolist() == [3, 3] and ce["msg"].tolist() == ["etsi", "2hz"]                 # runs 0-2 etsi, 12-14 2hz
    assert ce.set_index("msg").loc["2hz", "passed"] == 2 and ce.set_index("msg").loc["2hz", "events"] == 9 and ce.set_index("msg").loc["2hz", "rate_hz"] == 2.0
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.gridcheck", str(tmp_path), "--runs", "0-2", "--jobs", "1"], capture_output=True, text=True)
    assert proc.returncode == 0 and "runs read: 3 of the 432 of the grid, passed: 3" in proc.stdout and "result: PASS" in proc.stdout
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.gridcheck", str(tmp_path / "nowhere")], capture_output=True, text=True)
    assert proc.returncode == 1 and "no exported run" in proc.stdout


def test_a_broken_run_does_not_stop_the_others(tmp_path):
    write_grid_run(tmp_path / "aoc-grid-0" / "parquet", 0, background=10)
    bad = tmp_path / "aoc-grid-1" / "parquet"
    bad.mkdir(parents=True)
    (bad / "export_summary.json").write_text("{", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.gridcheck", str(tmp_path), "--jobs", "1"], capture_output=True, text=True)
    assert proc.returncode == 1 and "runs read: 2 of the 432 of the grid, passed: 1, failed: 1" in proc.stdout and "run 1: JSONDecodeError" in proc.stdout
