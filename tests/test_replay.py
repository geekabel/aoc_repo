"""Tests of the replay loader (task B2)."""
import numpy as np
import pandas as pd
import pytest

from aoc.fusion import replay as replay_module
from aoc.fusion.noise import NoiseModel
from aoc.fusion.replay import Run, check_run, load_run
from aoc.io.export import OBS_COLUMNS


@pytest.fixture(scope="module")
def run(exported_runs):
    folder, _ = exported_runs["run_aoc_sensors"]
    return load_run(folder)


@pytest.fixture(scope="module")
def ticks(run):
    """Every tick of every vehicle of the sample run."""
    return {ego: list(run.replay(ego)) for ego in run.egos}


# ----------------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------------
def test_load_applies_the_default_noise_model(run):
    assert run.noise == NoiseModel()
    assert run.info["result"] == "PASS"
    assert np.allclose(run.obs["P11"], 0.09) and np.allclose(run.obs["Pv11"], 0.25)
    assert run.egos == ["flow0.0", "flow0.1", "flow0.2", "flow0.3", "flow0.4"]


def test_load_without_noise_keeps_the_logged_values(exported_runs):
    folder, _ = exported_runs["run_aoc_sensors"]
    plain = load_run(folder, noise=None)
    logged = pd.read_parquet(folder / "observations.parquet")
    assert plain.noise is None
    pd.testing.assert_frame_equal(plain.obs.drop(columns="range_m"), logged)        # the sensor tables add range_m only


def test_load_of_a_folder_without_export_fails_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="aoc-export"):
        load_run(tmp_path)


# ----------------------------------------------------------------------------
# Replay of the sample run
# ----------------------------------------------------------------------------
def test_every_row_is_delivered_exactly_once(run, ticks):
    obs = run.obs
    is_v2x = obs["src"] == "v2x"
    own = obs["gt_match_id"] == obs["rx_station"]
    for ego in run.egos:
        mine = obs["rx_station"] == ego
        local = pd.concat([tick.local for tick in ticks[ego]])
        new = pd.concat([tick.v2x_new for tick in ticks[ego]])
        assert sorted(local.index) == sorted(obs.index[mine & ~is_v2x])
        assert sorted(new.index) == sorted(obs.index[mine & is_v2x & ~own])
    assert (is_v2x & own).sum() == 230                         # CPM objects that describe their receiver


def test_ticks_are_regular_and_cover_the_run(run, ticks):
    for ego in run.egos:
        times = np.array([tick.t for tick in ticks[ego]])
        assert np.allclose(np.diff(times), 0.1)
        assert times[-1] == pytest.approx(14.0)
        first_row = run.obs.loc[run.obs["rx_station"] == ego, "t_sim"].min()
        assert times[0] == pytest.approx(np.ceil(first_row * 10 - 1e-6) / 10)


def test_rows_arrive_at_the_right_tick(ticks):
    for vehicle_ticks in ticks.values():
        for tick in vehicle_ticks:
            assert ((tick.local["t_sim"] > tick.t - 0.1 - 1e-9) & (tick.local["t_sim"] <= tick.t + 1e-9)).all()
            assert ((tick.v2x_new["t_rx"] > tick.t - 0.1 - 1e-9) & (tick.v2x_new["t_rx"] <= tick.t + 1e-9)).all()
            for table in (tick.local, tick.v2x_new, tick.v2x):
                assert np.allclose(table["age_now"], tick.t - table["t_meas"])
                assert (table["age_now"] >= -1e-9).all()
                assert (table["rx_station"] == tick.ego).all()
            assert (tick.local["src"] != "v2x").all() and (tick.v2x["src"] == "v2x").all()


def test_known_objects_are_the_newest_per_sender_and_object(run, ticks):
    obs = run.obs
    for ego in ("flow0.0", "flow0.2"):
        mine = obs[(obs["rx_station"] == ego) & (obs["src"] == "v2x") & (obs["gt_match_id"] != ego)]
        for tick in ticks[ego][::7]:
            got = tick.v2x
            assert not got.duplicated(["tx_station", "obj_id"]).any()
            seen = mine[mine["t_rx"] <= tick.t + 1e-9]
            want = seen.sort_values(["t_meas", "t_rx"]).groupby(["tx_station", "obj_id"]).tail(1)
            assert sorted(got.index) == sorted(want.index)


def test_hold_drops_the_old_objects(run, ticks):
    held = list(run.replay("flow0.0", hold=1.0))
    assert len(held) == len(ticks["flow0.0"])
    dropped = 0
    for short, full in zip(held, ticks["flow0.0"]):
        assert (short.v2x["age_now"] <= 1.0 + 1e-6).all()
        assert sorted(short.v2x.index) == sorted(full.v2x.index[full.v2x["age_now"] <= 1.0 + 1e-9])
        dropped += len(full.v2x) - len(short.v2x)
    assert dropped > 0                                          # the sample does hold objects older than 1 s


def test_objects_about_the_receiver_can_be_kept(run):
    kept = pd.concat([tick.v2x_new for tick in run.replay("flow0.1", drop_self=False)])
    all_rows = run.obs[(run.obs["rx_station"] == "flow0.1") & (run.obs["src"] == "v2x")]
    assert len(kept) == len(all_rows)
    assert (kept["gt_match_id"] == "flow0.1").any()


def test_start_and_end_times(run, ticks):
    part = list(run.replay("flow0.0", t_start=8.0, t_end=9.0))
    assert [round(tick.t, 1) for tick in part] == [round(8.0 + 0.1 * i, 1) for i in range(11)]
    full_at_8 = next(tick for tick in ticks["flow0.0"] if abs(tick.t - 8.0) < 1e-9)
    assert sorted(part[0].v2x.index) == sorted(full_at_8.v2x.index)       # CPMs received before the start are known


def test_longer_period_still_delivers_every_row(run, ticks):
    slow = list(run.replay("flow0.0", period=0.5))
    assert np.allclose(np.diff([tick.t for tick in slow]), 0.5)
    assert sum(len(tick.local) for tick in slow) == sum(len(tick.local) for tick in ticks["flow0.0"])
    assert sum(len(tick.v2x_new) for tick in slow) == sum(len(tick.v2x_new) for tick in ticks["flow0.0"])
    assert sum(len(tick.truth) for tick in slow) > 0
    for tick in slow:
        assert np.allclose(tick.truth["t"], tick.t)


def test_ego_state_and_truth(run, ticks):
    truth = run.truth
    for tick in ticks["flow0.3"][::5]:
        me = truth[(truth["obj_id"] == "flow0.3") & np.isclose(truth["t"], tick.t)]
        assert len(me) == 1
        assert tick.ego_state["x"] == pytest.approx(float(me["x"].iloc[0]))
        assert tick.ego_state["heading_deg"] == pytest.approx(float(me["heading_deg"].iloc[0]))
        assert "flow0.3" not in set(tick.truth["obj_id"])
        assert len(tick.truth) == int(np.isclose(truth["t"], tick.t).sum()) - 1


def test_unknown_vehicle_gives_no_tick(run):
    assert list(run.replay("nobody")) == []


def test_period_must_fit_the_steps_of_the_truth(run):
    for bad in (0.05, 0.25, 0.0, -0.1, float("nan")):
        with pytest.raises(ValueError):
            next(run.replay("flow0.0", period=bad))
    assert next(run.replay("flow0.0", period=0.3)).t == pytest.approx(3.3)


def test_a_change_of_a_tick_table_does_not_reach_the_next_ticks(run, ticks):
    changed = []
    for tick in run.replay("flow0.0"):
        for table in (tick.local, tick.v2x_new, tick.v2x, tick.truth):
            if len(table):
                table.loc[:, "x"] = -1.0
        changed.append(tick)
    for mine, clean in zip(ticks["flow0.0"], run.replay("flow0.0")):
        assert (clean.v2x["x"] != -1.0).all() and (clean.truth["x"] != -1.0).all()
        assert clean.v2x["x"].tolist() == mine.v2x["x"].tolist()
    assert (run.obs["x"] != -1.0).all() and (run.truth["x"] != -1.0).all()


def test_text_columns_are_plain_strings(ticks):
    tick = ticks["flow0.0"][50]
    assert tick.local["tx_station"].tolist() == [None] * len(tick.local)
    assert (tick.v2x["tx_station"] == "nobody").dtype == bool
    assert tick.v2x["reference_time_ms"].dtype == np.float64 and tick.v2x["reference_time_ms"].notna().all()
    assert tick.local.dtypes.tolist() == tick.v2x.dtypes.tolist()


def test_check_of_the_sample_run_passes(run, exported_runs, capsys):
    result = check_run(run)
    assert result["result"] == "PASS" and result["problems"] == []
    assert result["rows_outside_truth_time"] == 0
    assert result["measurements_checked"] == result["measurements"] > 1000
    folder, _ = exported_runs["run_aoc_sensors"]
    assert replay_module.main([str(folder)]) == 0
    assert "result:              PASS" in capsys.readouterr().out
    assert replay_module.main([str(folder / "missing")]) == 2
    assert replay_module.main([str(folder), "--period", "0.05"]) == 2
    assert replay_module.main([str(folder), "--sigma-x", "-1"]) == 2


def broken_runs(run):
    """Copies of the sample run with one fault each. The check must catch every one of them."""
    obs, truth, model = run.obs, run.truth, run.noise
    rng = np.random.default_rng(0)

    shifted = truth.assign(x=truth["x"] + 100.0)                                    # the truth table moved by 100 m
    yield "truth moved", Run(obs=obs, truth=shifted, noise=model)

    per_row = obs.assign(x=obs["gt_x"] + 0.3 * rng.standard_normal(len(obs)))        # noise drawn per row
    yield "noise per row", Run(obs=per_row, truth=truth, noise=model)

    linked = obs.assign(y=obs["gt_y"] + (obs["x"] - obs["gt_x"]))                    # same error on x and y
    yield "linked errors", Run(obs=linked, truth=truth, noise=model)

    large = obs.assign(x=obs["gt_x"] + 3.0 * (obs["x"] - obs["gt_x"]))               # noise 3 times too large
    yield "noise too large", Run(obs=large, truth=truth, noise=model)

    few = large[large["rx_station"] == "flow0.4"]                                    # the same on a small part
    yield "noise too large, few rows", Run(obs=few, truth=truth, noise=model)

    biased = obs.assign(vx=obs["vx"] + 0.2)                                          # speed error with a mean
    yield "biased", Run(obs=biased, truth=truth, noise=model)

    yield "empty", Run(obs=obs.iloc[:0], truth=truth, noise=model)


def test_check_catches_broken_runs(run):
    for name, broken in broken_runs(run):
        result = check_run(broken)
        assert result["result"] == "FAIL", name


def test_rows_without_a_time_are_refused_or_left_out(exported_runs, tmp_path):
    folder, _ = exported_runs["run_aoc_sensors"]
    obs = pd.read_parquet(folder / "observations.parquet")
    obs.loc[obs.index[:4], "t_rx"] = np.nan
    obs.loc[obs.index[10:12], "gt_match_id"] = "nobody"
    obs.to_parquet(tmp_path / "observations.parquet")
    pd.read_parquet(folder / "truth.parquet").to_parquet(tmp_path / "truth.parquet")
    with pytest.raises(ValueError, match="4 of"):
        load_run(tmp_path)
    kept = load_run(tmp_path, missing="drop")
    assert kept.dropped == 6 and len(kept.obs) == len(obs) - 6
    assert replay_module.main([str(tmp_path)]) == 2
    assert replay_module.main([str(tmp_path), "--drop-unmatched"]) == 0
    # a run built by hand with a missing time is refused at replay
    with pytest.raises(ValueError, match="no time"):
        next(Run(obs=obs, truth=kept.truth).replay("flow0.0"))


# ----------------------------------------------------------------------------
# Replay of a hand-made run: the exact rule for the known objects
# ----------------------------------------------------------------------------
def row(**values):
    base = {name: None for name, _, _ in OBS_COLUMNS}
    base.update(run_id="r", seed=0, rx_station="ego", sensor=None, weather=None)
    base.update(values)
    if base["t_sim"] is None:
        base["t_sim"] = base["t_rx"]
    base["age"] = base["t_rx"] - base["t_meas"]
    return base


def hand_made_run():
    cpm = dict(src="v2x", meas_src="radar", gt_match_id="car")
    rows = [
        row(src="radar", meas_src="radar", sensor="front", obj_id="7", gt_match_id="car", t_sim=1.0, t_rx=1.0, t_meas=1.0),
        row(tx_station="A", obj_id="1", t_rx=1.05, t_meas=1.0, **cpm),
        row(tx_station="B", obj_id="1", t_rx=1.07, t_meas=0.9, **cpm),       # same object id, other sender: another key
        row(tx_station="A", obj_id="1", t_rx=1.25, t_meas=1.2, **cpm),       # newer measurement from A
        row(tx_station="A", obj_id="1", t_rx=1.35, t_meas=1.1, **cpm),       # late arrival of an older measurement: ignored
        row(tx_station="A", obj_id="2", t_rx=1.40, t_meas=1.3, **cpm),       # exactly on a tick
    ]
    obs = pd.DataFrame(rows)
    steps = np.round(np.arange(0.5, 2.01, 0.1), 1)
    truth = pd.DataFrame({"t": np.tile(steps, 2), "obj_id": np.repeat(["car", "ego"], len(steps)),
                          "x": 0.0, "y": 0.0, "vx": 1.0, "vy": 0.0, "speed": 1.0, "heading_deg": 90.0})
    return Run(obs=obs, truth=truth)


def test_known_objects_step_by_step():
    ticks = {round(tick.t, 1): tick for tick in hand_made_run().replay("ego")}
    assert min(ticks) == 1.0 and max(ticks) == 2.0

    def known(t):
        table = ticks[t].v2x
        return sorted(zip(table["tx_station"], table["obj_id"], table["t_meas"].round(1), table["age_now"].round(2)))

    assert len(ticks[1.0].local) == 1 and len(ticks[1.0].v2x) == 0
    assert known(1.1) == [("A", "1", 1.0, 0.1), ("B", "1", 0.9, 0.2)]
    assert known(1.2) == [("A", "1", 1.0, 0.2), ("B", "1", 0.9, 0.3)]                 # nothing new: the ages grow
    assert known(1.3) == [("A", "1", 1.2, 0.1), ("B", "1", 0.9, 0.4)]
    assert known(1.4) == [("A", "1", 1.2, 0.2), ("A", "2", 1.3, 0.1), ("B", "1", 0.9, 0.5)]
    assert len(ticks[1.4].v2x_new) == 2                                               # the late arrival is still delivered as new
    assert known(2.0) == [("A", "1", 1.2, 0.8), ("A", "2", 1.3, 0.7), ("B", "1", 0.9, 1.1)]
    assert ticks[1.5].ego_state == {"x": 0.0, "y": 0.0, "vx": 1.0, "vy": 0.0, "speed": 1.0, "heading_deg": 90.0}
    assert ticks[1.5].truth["obj_id"].tolist() == ["car"]


def test_two_copies_of_one_measurement_the_last_received_wins():
    run = hand_made_run()
    cpm = dict(src="v2x", meas_src="radar", gt_match_id="car", tx_station="C", obj_id="9")
    extra = [row(t_rx=1.05, t_meas=1.0, cbr=0.1, **cpm),
             row(t_rx=1.25, t_meas=1.0 - 1e-16, cbr=0.2, **cpm)]                   # the same time, written one bit lower
    run = Run(obs=pd.DataFrame(run.obs.to_dict("records") + extra), truth=run.truth)
    ticks = {round(tick.t, 1): tick for tick in run.replay("ego")}
    from_c = ticks[1.3].v2x[ticks[1.3].v2x["tx_station"] == "C"]
    assert from_c["t_rx"].tolist() == [1.25] and from_c["cbr"].tolist() == [0.2]


def test_known_objects_with_a_hold_time():
    ticks = {round(tick.t, 1): tick for tick in hand_made_run().replay("ego", hold=0.5)}
    assert sorted(ticks[1.4].v2x["tx_station"]) == ["A", "A", "B"]                    # B is 0.5 s old: still in
    assert sorted(ticks[1.5].v2x["tx_station"]) == ["A", "A"]                         # B is 0.6 s old: out
    assert len(ticks[1.9].v2x) == 0
