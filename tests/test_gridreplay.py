"""Tests of the replay of the grid (aoc.analysis.gridreplay, task D3) and of the pieces it relies on: the evaluation from a
start time and one receiver at a time (aoc.analysis.metrics), the loader's receivers filter."""
import json
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis import grid, gridreplay
from aoc.analysis.metrics import EvaluationModel, EvaluationState, evaluate_into, evaluate_run
from aoc.fusion.replay import load_run
from aoc.io.synthetic import Car, Scenario, write_run

GAP = (10.0, 13.0)


def grid_scenario(seed: int = 0) -> Scenario:
    """The occlusion of the pilot test with the names of the grid: ped_1 walking, ego_1 behind, helper_a_1 ahead, and a background
    car flow.1 that sends too."""
    cars = (Car("ped_1", x=100.0, vx=1.25, cls="pedestrian"), Car("ego_1", x=60.0, vx=1.25), Car("helper_a_1", x=140.0, vx=1.25),
            Car("flow.1", x=20.0, vx=1.25))
    return Scenario(cars, duration=20.0, sensor_range=60.0, cpm_period=0.5, delay=0.05, seed=seed, name="synthetic-grid-%d" % seed)


def write_grid_run(folder: pathlib.Path, n: int, seed: int = 0) -> pathlib.Path:
    """A synthetic run in the folder layout of the grid, with the ego's own rows about the pedestrian cut out for 3 s."""
    folder.mkdir(parents=True, exist_ok=True)
    write_run(grid_scenario(seed), folder)
    obs = pd.read_parquet(folder / "observations.parquet")
    own = (obs["rx_station"] == "ego_1") & (obs["src"] != "v2x") & (obs["gt_match_id"] == "ped_1") & (obs["t_sim"] > GAP[0]) & (obs["t_sim"] < GAP[1])
    obs[~own.to_numpy(dtype=bool)].to_parquet(folder / "observations.parquet", index=False)
    info = json.loads((folder / "export_summary.json").read_text(encoding="utf-8"))
    c = grid.cell_of_run(n)
    info["variables"] = dict(density=c["density"], pen=str(c["pen"]), msg=c["msg"])
    info["seed"] = c["seed"]
    info["receivers"] = ["ego_1", "helper_a_1", "flow.1"]
    (folder / "export_summary.json").write_text(json.dumps(info), encoding="utf-8")
    return folder


# ---------------------------------------------------------------- the evaluation pieces
def test_the_loader_reads_the_received_rows_of_some_receivers_only(exported_runs):
    data = exported_runs["run_aoc_sensors"][0]
    full = load_run(data)
    part = load_run(data, receivers=["flow0.3"])
    v2x = part.obs[part.obs["src"] == "v2x"]
    assert set(v2x["rx_station"]) == {"flow0.3"} and len(v2x) == int(((full.obs["src"] == "v2x") & (full.obs["rx_station"] == "flow0.3")).sum())
    assert int((part.obs["src"] != "v2x").sum()) == int((full.obs["src"] != "v2x").sum())              # the sensor rows of every vehicle
    a = full.obs[(full.obs["rx_station"] == "flow0.3")].sort_values(["t_sim", "src", "obj_id"])[["x", "y", "P11"]].to_numpy()
    b = part.obs[(part.obs["rx_station"] == "flow0.3")].sort_values(["t_sim", "src", "obj_id"])[["x", "y", "P11"]].to_numpy()
    assert np.allclose(a, b)                                                                            # the same noise


def test_the_evaluation_one_receiver_at_a_time_equals_the_whole_one(exported_runs):
    data = exported_runs["run_aoc_sensors"][0]
    egos = ["flow0.3", "flow0.4"]
    model = EvaluationModel(hold=2.0)
    whole = evaluate_run(load_run(data), model, egos=egos, t_start=5.0)
    state = EvaluationState()
    for ego in egos:
        evaluate_into(load_run(data, receivers=[ego]), model, state, egos=[ego], t_start=5.0)
    parts = state.rows("", model)
    cols = [c for c in whole.columns if c not in ("seconds", "run_id")]
    a, b = whole[cols].reset_index(drop=True), parts[cols].reset_index(drop=True)
    assert a.columns.equals(b.columns) and all((a[c].isna() == b[c].isna()).all() and (a[c].dropna() == b[c].dropna()).all() for c in cols)
    later = evaluate_run(load_run(data), model, egos=egos, t_start=10.0)
    assert later["ticks"].iloc[0] < whole["ticks"].iloc[0] and later["ticks"].iloc[0] == 2 * 41                 # 10.0 to 14.0 s, two receivers
    bins = state.bins("", model)
    assert len(bins) == 50 and bins["count"].sum() == 5 * whole["fused_objects"].iloc[0] and set(bins["way"]) == set(whole["way"])


# ---------------------------------------------------------------- the grid replay
def test_receivers_of_and_paths(tmp_path):
    info = dict(receivers=["ego_1", "ego_2", "flow.3", "flow.1", "helper_a_1", "truck_2"])
    assert gridreplay.receivers_of(info, "all") == info["receivers"]
    assert gridreplay.receivers_of(info, "egos") == ["ego_1", "ego_2"]
    assert gridreplay.receivers_of(info, "1") == ["ego_1", "ego_2", "flow.1"]
    folder = write_grid_run(tmp_path / "aoc-grid-0" / "parquet", 0)
    from_table = gridreplay.receivers_of({}, "all", str(folder))
    assert "ego_1" in from_table and "flow.1" in from_table                                    # no list in the summary: the observations table
    assert gridreplay.result_path("/x", 7, (5.0,)) == "/x/run-007-hold-5.json" and gridreplay.result_path("/x", 7, (2.0, 10.0)).endswith("run-007-hold-2-10.json")


def test_replay_run_gives_the_metrics_the_bins_and_the_events(tmp_path):
    folder = write_grid_run(tmp_path / "aoc-grid-13" / "parquet", 13)                    # low, 10, 2hz, seed 1: evaluation
    out = gridreplay.replay_run(str(folder), holds=(5.0,), q_by_class=None, receivers="all", warm_up=2.0)
    assert out["run"] == 13 and out["density"] == "low" and out["msg"] == "2hz" and out["kind"] == "evaluation"
    assert out["receivers"] == ["ego_1", "helper_a_1", "flow.1"]
    metrics = pd.DataFrame(out["metrics"])
    assert len(metrics) == 5 and set(metrics["way"]) == set(gridreplay.WAYS) and (metrics["hold"] == 5.0).all()
    assert metrics["ticks"].iloc[0] > 0 and metrics["calibration_error"].between(0, 1).all() and metrics["received_rows"].iloc[0] > 0
    bins = pd.DataFrame(out["bins"])
    assert len(bins) == 50 and bins["count"].sum() == 5 * metrics["fused_objects"].iloc[0]
    events = pd.DataFrame(out["events"])
    assert len(events) == 4 and set(events["way"]) == set(gridreplay.EVENT_WAYS) and (events["event"] == 1).all()
    assert events["occlusion"].all() and events["lost"].iloc[0] == pytest.approx(GAP[0], abs=0.11)
    assert events["seen_again"].iloc[0] == pytest.approx(GAP[1], abs=0.11)
    b3 = events.set_index("way").loc["B3"]
    assert b3["v2x_gap_mean"] > 0.9 and b3["own_gap_end"] < 0.3 and b3["cpm_rows_in_gap"] >= 5
    egos_only = gridreplay.replay_run(str(folder), holds=(5.0,), receivers="egos", warm_up=2.0, events=False)
    assert egos_only["receivers"] == ["ego_1"] and egos_only["events"] == [] and pd.DataFrame(egos_only["metrics"])["ticks"].iloc[0] < metrics["ticks"].iloc[0]


def test_the_command_is_resumable_and_aggregates(tmp_path):
    root, out = tmp_path / "grid", tmp_path / "out"
    write_grid_run(root / "aoc-grid-0" / "parquet", 0, seed=0)                              # low, 10, etsi, seed 0
    write_grid_run(root / "aoc-grid-22" / "parquet", 22, seed=1)                            # low, 10, 2hz, seed 10: calibration
    (root / "aoc-grid-5").mkdir()                                                           # not exported
    cmd = [sys.executable, "-m", "aoc.analysis.gridreplay", str(root), "--out", str(out), "--jobs", "1", "--warmup", "2", "--q", "pedestrian=0.1"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "runs: 2 found, 0 done before, 2 to replay" in proc.stdout and "q fitted: pedestrian 0.1" in proc.stdout and "result: PASS" in proc.stdout
    metrics = pd.read_csv(out / "metrics.csv")
    assert len(metrics) == 10 and sorted(metrics["kind"].unique()) == ["calibration", "evaluation"] and (metrics["hold"] == 5.0).all()
    assert len(pd.read_csv(out / "bins.csv")) == 100 and len(pd.read_csv(out / "events.csv")) == 8
    assert "etsi    10     1 |" in proc.stdout                                               # the summary over the evaluation runs
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0 and "2 done before, 0 to replay" in proc.stdout
    proc = subprocess.run(cmd + ["--holds", "2,10", "--runs", "0", "--no-events"], capture_output=True, text=True)
    assert proc.returncode == 0 and "1 to replay" in proc.stdout and (out / "run-000-hold-2-10.json").is_file()
    metrics = pd.read_csv(out / "metrics.csv")
    assert len(metrics) == 20 and sorted(metrics["hold"].unique()) == [2.0, 5.0, 10.0]
    proc = subprocess.run(cmd + ["--aggregate"], capture_output=True, text=True)
    assert proc.returncode == 0 and "written" in proc.stdout
    proc = subprocess.run(cmd + ["--q", "nobody=1"], capture_output=True, text=True)
    assert proc.returncode == 2 and "not a class" in proc.stdout
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.gridreplay", str(tmp_path / "nowhere"), "--out", str(out / "x")], capture_output=True, text=True)
    assert proc.returncode == 1 and "no exported run" in proc.stdout


def test_summary_table_and_format():
    rows = []
    for run, msg in ((0, "etsi"), (1, "etsi"), (12, "2hz")):
        for way in gridreplay.WAYS:
            rows.append(dict(run=run, density="low", pen=10, msg=msg, seed=0, kind="evaluation", way=way, hold=5.0, calibration_error=0.1,
                             brier=0.05, awareness_ratio=0.5, stale_rate=0.1, rmse=0.3))
    rows.append(dict(run=36, density="low", pen=10, msg="etsi", seed=10, kind="calibration", way="B3", hold=5.0, calibration_error=0.9,
                     brier=0.9, awareness_ratio=0.1, stale_rate=0.9, rmse=0.9))
    table = gridreplay.summary_table(pd.DataFrame(rows))
    assert len(table) == 10 and table["msg"].tolist()[:5] == ["etsi"] * 5 and (table["ece"] == 0.1).all()     # the calibration run is left out
    text = gridreplay.format_summary(table)
    assert "etsi    10     2 |" in text and "0.1000 (0.0500)" in text and gridreplay.format_summary(pd.DataFrame()) == "no evaluation run replayed yet"
