"""Tests of the sensor models and the weather tables (task C6): the tables, the misses at the replay, the reliability in the discount."""
import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis.metrics import EvaluationModel, evaluate_run
from aoc.fusion.alignment import align
from aoc.fusion.discounting import TrustModel, declared_reliability, reliability
from aoc.fusion.noise import measurement_keys, standard_normals
from aoc.fusion.replay import load_run
from aoc.fusion.sensors import DEFAULT_TABLES, SensorModel, SensorTables, apply_detection, station_distance, uniforms
from aoc.io.synthetic import Car, Scenario, make_run, write_run


def convoy(sensor_name="Camera", weather=None, duration=60.0, n=6, spacing=30.0, **changes) -> Scenario:
    """Cars in a line, 30 m apart, at 10 m/s: the sensor sees the next two cars (30 and 60 m) within 80 m."""
    cars = tuple(Car("c%d" % k, x=spacing * k, vx=10.0) for k in range(n))
    return Scenario(cars, duration=duration, sensor_range=80.0, sensor_name=sensor_name, weather=weather, delay=0.1, **changes)


def test_the_tables_of_the_plan():
    camera = DEFAULT_TABLES.sensors["Camera"]
    assert camera.bins == (30.0, 60.0, 80.0) and camera.weathers == ("clear", "rain")
    assert list(camera.bin_of([0.0, 29.9, 30.0, 30.1, 60.0, 79.0, 80.0, 200.0, np.nan])) == [0, 0, 0, 1, 1, 2, 2, 2, 2]
    assert list(camera.detection_probability("rain", [10.0, 45.0, 70.0, 500.0])) == [0.85, 0.70, 0.45, 0.45]
    assert list(camera.reliability_at("clear", [10.0, np.nan])) == [0.97, 0.90]           # an unknown distance takes the last bin
    for model in DEFAULT_TABLES.sensors.values():
        for weather in model.weathers:
            assert all(a >= b for a, b in zip(model.detection[weather], model.detection[weather][1:]))      # worse with the distance
            assert all(a >= b for a, b in zip(model.reliability[weather], model.reliability[weather][1:]))
        assert all(c >= r for c, r in zip(model.detection["clear"], model.detection["rain"]))              # worse in rain
    names = pd.Series(["Camera", "Lidar", "radar", None])
    assert list(DEFAULT_TABLES.detection_probability(names, "clear", [10.0, 90.0, 10.0, 10.0])) == [0.98, 0.90, 1.0, 1.0]
    assert list(DEFAULT_TABLES.reliability(names, "rain", [10.0, 10.0, 10.0, 10.0], default=0.5)) == [0.90, 0.90, 0.5, 0.5]


def test_a_bad_table_is_refused():
    with pytest.raises(ValueError):
        SensorModel("X", 80.0, 120.0, (30.0, 80.0, 60.0), {"clear": (1, 1, 1)}, {"clear": (1, 1, 1)})
    with pytest.raises(ValueError):
        SensorModel("X", 80.0, 120.0, (30.0, 60.0), {"clear": (1, 1, 1)}, {"clear": (1, 1)})                # the last bin is not the range
    with pytest.raises(ValueError):
        SensorModel("X", 80.0, 120.0, (30.0, 80.0), {"clear": (1, 1.2)}, {"clear": (1, 1)})
    with pytest.raises(ValueError):
        SensorModel("X", 80.0, 120.0, (30.0, 80.0), {"clear": (1, 1)}, {"rain": (1, 1)})
    with pytest.raises(ValueError):
        DEFAULT_TABLES.sensors["Camera"].detection_probability("snow", [10.0])
    with pytest.raises(ValueError):
        SensorTables({"Wrong": DEFAULT_TABLES.sensors["Camera"]})
    with pytest.raises(ValueError):
        TrustModel(weather="snow")
    fog = SensorModel("Camera", 80.0, 120.0, (30.0, 60.0, 80.0), {"clear": (1, 1, 1), "fog": (1, 1, 1)}, {"clear": (1, 1, 1), "fog": (1, 1, 1)})
    with pytest.raises(ValueError):
        SensorTables({"Camera": fog, "Radar": DEFAULT_TABLES.sensors["Radar"]})          # not the same weathers
    with pytest.raises(TypeError):
        DEFAULT_TABLES.sensors["Zzz"] = fog                                              # read-only


def test_json_round_trip_and_the_command(tmp_path):
    path = tmp_path / "tables.json"
    DEFAULT_TABLES.to_json(path)
    again = SensorTables.from_json(path)
    assert again == DEFAULT_TABLES
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [m["name"] for m in data["sensors"]] == ["Camera", "Radar", "Lidar"]
    proc = subprocess.run([sys.executable, "-m", "aoc.fusion.sensors", "--read", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0 and "Lidar" in proc.stdout and "values assumed" in proc.stdout


def test_station_distance_and_the_misses_by_bin():
    run = make_run(convoy(weather="rain"), sensors=None)
    local = run.obs[run.obs["src"] != "v2x"]
    r = station_distance(local, run.truth)
    assert np.allclose(np.sort(np.unique(np.round(r, 6))), [30.0, 60.0])                       # the next car and the one after
    kept, counts = apply_detection(run.obs, run.truth, DEFAULT_TABLES, "rain")
    assert counts["sensor_rows"] == len(local) and counts["cpm_rows"] == int((run.obs["src"] == "v2x").sum())
    kept_local = kept[kept["src"] != "v2x"]
    share = kept_local["range_m"].round(6).value_counts() / local.assign(r=r.round(6))["r"].value_counts()
    assert share[30.0] == pytest.approx(0.85, abs=0.02) and share[60.0] == pytest.approx(0.70, abs=0.02)   # bins 0-30 and 30-60
    # a CPM row is kept exactly when its sender kept a sensor row about the object at the measurement time
    keys = set(zip(kept_local["rx_station"], kept_local["gt_match_id"], np.round(kept_local["t_meas"] * 1e6).astype(np.int64)))
    v2x = kept[kept["src"] == "v2x"]
    assert all((a, b, t) in keys for a, b, t in zip(v2x["tx_station"], v2x["gt_match_id"], np.round(v2x["t_meas"] * 1e6).astype(np.int64)))
    all_v2x = run.obs[run.obs["src"] == "v2x"]
    assert counts["cpm_rows_dropped"] == len(all_v2x) - len(v2x) > 0 and counts["cpm_rows_without_sensor_row"] == 0


def test_the_misses_are_tied_to_the_measurement():
    run = make_run(convoy(weather="rain"), sensors=None)
    full, _ = apply_detection(run.obs, run.truth, DEFAULT_TABLES, "rain")
    again, _ = apply_detection(run.obs, run.truth, DEFAULT_TABLES, "rain")
    assert full.index.equals(again.index)                                                     # the same seed, the same drops
    other, _ = apply_detection(run.obs, run.truth, SensorTables(seed=1), "rain")
    assert not full.index.equals(other.index)
    part = run.obs[run.obs["t_sim"] < 20.0]
    subset, _ = apply_detection(part, run.truth, DEFAULT_TABLES, "rain")
    assert subset.index.equals(full[full["t_sim"] < 20.0].index)                              # a subset gives the same drops
    clear, counts = apply_detection(run.obs, run.truth, DEFAULT_TABLES, "clear")
    assert len(clear) > len(full) and counts["sensor_rows_dropped"] < 0.1 * counts["sensor_rows"]
    unknown = make_run(convoy(sensor_name="radar", weather="rain"))                          # not in the tables: nothing is missed
    assert unknown.detection["sensor_rows_dropped"] == 0 and unknown.detection["cpm_rows_dropped"] == 0
    assert "range_m" in unknown.obs.columns


def test_a_cpm_without_sensor_row_takes_a_draw():
    run = make_run(convoy(weather="rain"), sensors=None)
    obs = run.obs[run.obs["src"] == "v2x"]                                                     # the senders' own rows are gone
    kept, counts = apply_detection(obs, run.truth, DEFAULT_TABLES, "rain")
    assert counts["cpm_rows_without_sensor_row"] == len(obs)
    share = len(kept) / len(obs)
    assert 0.99 < share < 1.0                            # the sender is taken to carry the three sensors of the table: one of them sees it
    # the sender has sensor rows of the Camera, but none about this object at that time: a draw with the Camera alone
    local = run.obs[run.obs["src"] != "v2x"]
    shifted = obs.copy()
    shifted["t_meas"] = shifted["t_meas"] + 0.005                                              # 5 ms away: no sensor row within 1 ms
    kept, counts = apply_detection(pd.concat([local, shifted]), run.truth, DEFAULT_TABLES, "rain")
    v2x = kept[kept["src"] == "v2x"]
    assert counts["cpm_rows_without_sensor_row"] == len(obs) and 0.70 < len(v2x) / len(obs) < 0.85  # 0.85 at 30 m, 0.70 at 60 m
    # the sender has sensor rows, but of no sensor of the table: its CPM rows are never missed
    other = make_run(convoy(sensor_name="FrontLongRangeRadar", weather="rain"), sensors=None)
    kept, counts = apply_detection(other.obs, other.truth, DEFAULT_TABLES, "rain")
    assert counts["sensor_rows_dropped"] == 0 and counts["cpm_rows_dropped"] == 0


def test_a_cpm_matches_a_sensor_row_within_one_millisecond():
    """A CPM declares its measurement time in whole milliseconds; the sender's sensor row can be a fraction of a
    millisecond away (the CAM sensor of the Artery example). Such a row still counts as the sender's own detection."""
    run = make_run(convoy(weather="rain"), sensors=None)
    obs = run.obs.copy()
    is_v2x = (obs["src"] == "v2x").to_numpy()
    obs.loc[is_v2x, "t_meas"] = obs.loc[is_v2x, "t_meas"] + 0.0008                            # 0.8 ms away
    kept, counts = apply_detection(obs, run.truth, DEFAULT_TABLES, "rain")
    assert counts["cpm_rows_without_sensor_row"] == 0
    exact, counts_exact = apply_detection(run.obs, run.truth, DEFAULT_TABLES, "rain")
    assert counts["cpm_rows_dropped"] == counts_exact["cpm_rows_dropped"]


def test_the_sample_runs_of_artery_are_not_touched(exported_runs):
    """The sensors of the two sample runs are not in the tables: nothing is missed, in rain either."""
    for name in ("run_example_sensors", "run_aoc_sensors"):
        folder, _ = exported_runs[name]
        run = load_run(folder, weather="rain")
        assert run.detection["sensor_rows_dropped"] == 0 and run.detection["cpm_rows_dropped"] == 0
        assert run.dropped == 0


def test_the_draws_are_uniform_and_independent_of_the_noise():
    who = pd.Series(["a"] * 20000 + ["b"] * 20000)
    what = pd.Series(["Camera"] * 40000)
    obj = pd.Series([str(k % 97) for k in range(40000)])
    t = pd.Series(np.arange(40000) * 0.1)
    keys = measurement_keys(0, who, what, obj, t)
    u = uniforms(keys)
    assert abs(u.mean() - 0.5) < 0.01 and abs(u.std() - 0.2887) < 0.01 and u.min() > 0.0 and u.max() < 1.0
    z = standard_normals(keys, 4)
    assert all(abs(np.corrcoef(u, z[:, k])[0, 1]) < 0.02 for k in range(4))
    with pytest.raises(ValueError):
        standard_normals(keys, 7)                                                              # the stream of the detection


def test_load_run_applies_the_tables_and_reads_the_weather(tmp_path):
    write_run(convoy(weather="rain"), tmp_path / "rain")
    run = load_run(tmp_path / "rain")
    assert run.weather == "rain" and run.sensors is DEFAULT_TABLES and run.detection["sensor_rows_dropped"] > 0
    clear = load_run(tmp_path / "rain", weather="clear")
    assert clear.weather == "clear" and clear.detection["sensor_rows_dropped"] < run.detection["sensor_rows_dropped"]
    plain = load_run(tmp_path / "rain", sensors=None)
    assert plain.sensors is None and plain.detection == {} and "range_m" not in plain.obs.columns
    with pytest.raises(ValueError):
        load_run(tmp_path / "rain", weather="snow")
    write_run(convoy(), tmp_path / "none")
    assert load_run(tmp_path / "none").weather == "clear"


def test_the_reliability_of_a_sensor_row_follows_the_tables():
    run = make_run(convoy(weather="rain", duration=5.0))
    tick = next(t for t in run.replay("c0", period=0.1) if len(t.local) == 2)
    objects = align(tick).objects
    local = objects[~objects["source"].str.startswith("v2x")]
    assert sorted(np.round(local["range_m"], 3)) == [30.0, 60.0]
    declared = declared_reliability(local, TrustModel(weather="rain"))
    assert np.allclose(np.sort(declared), [0.80, 0.90])                                        # Camera in rain: 0.90 up to 30 m, 0.80 up to 60 m
    assert np.allclose(declared_reliability(local, TrustModel(weather="clear")), np.where(local["range_m"] <= 30.0, 0.97, 0.95))
    assert np.allclose(declared_reliability(local, TrustModel()), np.where(local["range_m"] <= 30.0, 0.97, 0.95))     # no weather: clear
    assert np.allclose(declared_reliability(local, TrustModel(sensor_reliability={"Camera": 0.5})), 0.5)           # a constant wins
    assert np.allclose(declared_reliability(local, TrustModel(sensor_tables=None)), 1.0)
    assert np.allclose(declared_reliability(local, TrustModel(sensor_tables=None, sensor_default=0.7)), 0.7)
    without = local.drop(columns="range_m")
    assert np.allclose(declared_reliability(without, TrustModel(weather="rain")), 0.70)         # no distance: the last bin
    assert np.allclose(reliability(local, TrustModel(weather="rain")), reliability(local, TrustModel(sensor_tables=None)) * declared)
    assert TrustModel().for_run(run).weather == "rain" and TrustModel(weather="clear").for_run(run).weather == "clear"


def test_rain_lowers_the_reliability_and_the_evaluation_runs():
    """With the tables, the reliability of a locally seen object is lower in rain than in the clear, and the
    evaluation runs under both weathers with the tables of the run."""
    means = {}
    for weather in ("clear", "rain"):
        run = make_run(convoy(weather=weather, duration=10.0, n=3))
        values = []
        for tick in run.replay("c0", period=0.5):
            objects = align(tick).objects
            local = objects[~objects["source"].str.startswith("v2x")]
            if len(local):
                values.append(reliability(local, TrustModel().for_run(run)).mean())
        means[weather] = float(np.mean(values))
        rows = evaluate_run(run, EvaluationModel(hold=None, trust=TrustModel(), period=0.5))
        assert len(rows) == 5 and rows["brier"].between(0.0, 1.0).all()
    assert means["rain"] < means["clear"] < 1.0


def test_for_run_takes_the_tables_of_the_run():
    run = make_run(convoy(weather="rain", duration=5.0), sensors=None)                       # loaded without the tables
    model = TrustModel().for_run(run)
    assert model.sensor_tables is None and model.weather == "rain"
    tick = next(t for t in run.replay("c0", period=0.1) if len(t.local) == 2)
    local = align(tick).objects
    local = local[~local["source"].str.startswith("v2x")]
    assert np.allclose(declared_reliability(local, model), 1.0)                                # no tables: fully reliable
    own = SensorTables(seed=3)
    assert TrustModel(sensor_tables=own).for_run(run).sensor_tables is own                     # set by hand: kept
    with_tables = make_run(convoy(weather="rain", duration=5.0), sensors=own)
    assert TrustModel().for_run(with_tables).sensor_tables is own


def test_make_run_with_noise_off_keeps_the_tables():
    run = make_run(convoy(weather="rain", duration=5.0), noise=None)
    assert run.noise is None and run.detection["sensor_rows_dropped"] > 0 and run.weather == "rain"
