"""Tests of the noise model of the replay (task B2)."""
import numpy as np
import pandas as pd
import pytest
from scipy import stats

from aoc.fusion.noise import NoiseModel, apply_noise, measurement_keys, standard_normals, true_state_at


@pytest.fixture(scope="module")
def sample(exported_runs):
    folder, _ = exported_runs["run_aoc_sensors"]
    return pd.read_parquet(folder / "observations.parquet"), pd.read_parquet(folder / "truth.parquet")


def measurements(obs):
    """One row per measurement: a CPM object received by several vehicles counts once."""
    who = obs["tx_station"].where(obs["src"] == "v2x", obs["rx_station"])
    what = obs["sensor"].where(obs["src"] != "v2x", "cpm")
    t_us = np.round(obs["t_meas"] * 1e6).astype("int64")
    return obs[~pd.DataFrame({"who": who, "what": what, "obj": obs["obj_id"], "t": t_us}).duplicated().to_numpy()]


# ----------------------------------------------------------------------------
# The generator of the noise values
# ----------------------------------------------------------------------------
def many_keys(n_objects=400, n_times=500, seed=0):
    obj = pd.Series(np.repeat(np.arange(n_objects), n_times).astype(str))
    t = pd.Series(np.tile(np.arange(n_times) * 0.1, n_objects))
    who = pd.Series(["veh%d" % (i % 7) for i in range(len(obj))])
    what = pd.Series(["radar"] * len(obj))
    return measurement_keys(seed, who, what, obj, t)


def test_values_follow_a_standard_normal_law():
    z = standard_normals(many_keys(), 4)                      # 200 000 keys
    assert np.abs(z.mean(axis=0)).max() < 0.012               # 5 standard errors
    assert np.abs(z.std(axis=0) - 1.0).max() < 0.01
    for column in range(4):
        assert stats.kstest(z[:, column], "norm").pvalue > 1e-3
    correlation = np.corrcoef(z.T)
    assert np.abs(correlation - np.eye(4)).max() < 0.012


def test_values_of_one_object_at_two_times_are_independent():
    z = standard_normals(many_keys(n_objects=400, n_times=500), 4).reshape(400, 500, 4)
    for column in range(4):
        now, later = z[:, :-1, column].ravel(), z[:, 1:, column].ravel()
        assert abs(np.corrcoef(now, later)[0, 1]) < 0.012


def test_values_are_fixed_for_good():
    # These numbers must never change: every result of the project depends on them.
    keys = measurement_keys(0, pd.Series(["flow0.0", "flow0.1"]), pd.Series(["cpm", "FrontLongRangeRadar"]),
                            pd.Series(["15246", "1"]), pd.Series([3.1, 12.4]))
    assert keys.tolist() == FIXED_KEYS
    assert standard_normals(keys, 4) == pytest.approx(np.array(FIXED_VALUES), abs=1e-12)


def test_another_seed_gives_other_values():
    a, b = standard_normals(many_keys(50, 50, seed=0), 4), standard_normals(many_keys(50, 50, seed=1), 4)
    assert np.abs(a - b).min() > 0.0
    assert abs(np.corrcoef(a[:, 0], b[:, 0])[0, 1]) < 0.1


# ----------------------------------------------------------------------------
# The true state at any time
# ----------------------------------------------------------------------------
def tiny_truth():
    return pd.DataFrame({"t": [1.0, 1.1, 1.2, 5.0], "obj_id": ["a", "a", "a", "b"],
                         "x": [0.0, 1.0, 3.0, 9.0], "y": [0.0, -1.0, -3.0, 9.0],
                         "vx": [10.0, 10.0, 20.0, 0.0], "vy": [-10.0, -10.0, -20.0, 0.0]})


def test_true_state_between_two_steps_is_interpolated():
    got = true_state_at(tiny_truth(), pd.Series(["a", "a", "a"]), pd.Series([1.1, 1.05, 1.15]))
    assert got["gt_x"].tolist() == pytest.approx([1.0, 0.5, 2.0])
    assert got["gt_y"].tolist() == pytest.approx([-1.0, -0.5, -2.0])
    assert got["gt_vx"].tolist() == pytest.approx([10.0, 10.0, 15.0])
    assert got["gt_gap"].tolist() == [0.0, 0.0, 0.0]


def test_true_state_outside_the_life_of_the_object_uses_the_nearest_step():
    got = true_state_at(tiny_truth(), pd.Series(["a", "a", "b", "b"]), pd.Series([0.7, 1.5, 4.0, 5.5]))
    assert got["gt_x"].tolist() == pytest.approx([0.0, 3.0, 9.0, 9.0])
    assert got["gt_gap"].tolist() == pytest.approx([0.3, 0.3, 1.0, 0.5])


def test_true_state_of_an_unknown_object_is_missing():
    got = true_state_at(tiny_truth(), pd.Series(["a", "zz", None]), pd.Series([1.0, 1.0, 1.0]))
    assert got["gt_x"].isna().tolist() == [False, True, True]


def test_true_state_without_a_time_is_missing():
    got = true_state_at(tiny_truth(), pd.Series(["a", "a"]), pd.Series([1.0, np.nan]))
    assert got["gt_x"].isna().tolist() == [False, True]
    assert got["gt_gap"].isna().tolist() == [False, True]


def test_missing_id_never_matches_an_object_with_a_strange_name():
    truth = tiny_truth().replace({"obj_id": {"b": "nan"}})
    for missing in (None, np.nan, pd.NA):
        got = true_state_at(truth, pd.Series(["nan", missing], dtype=object), pd.Series([5.0, 5.0]))
        assert got["gt_x"].isna().tolist() == [False, True]


def test_true_state_inside_a_hole_of_the_log_uses_the_nearest_step():
    truth = pd.DataFrame({"t": [1.0, 1.1, 1.2, 3.0, 3.1], "obj_id": "a", "x": [0.0, 1.0, 2.0, 50.0, 51.0],
                          "y": 0.0, "vx": 10.0, "vy": 0.0})
    got = true_state_at(truth, pd.Series(["a"] * 4), pd.Series([1.15, 1.5, 2.9, 3.05]))
    assert got["gt_x"].tolist() == pytest.approx([1.5, 2.0, 50.0, 50.5])
    assert got["gt_gap"].tolist() == pytest.approx([0.0, 0.3, 0.1, 0.0])


# ----------------------------------------------------------------------------
# The noise on the observations table
# ----------------------------------------------------------------------------
def test_noise_is_repeatable_and_depends_on_the_seed(sample):
    obs, truth = sample
    first = apply_noise(obs, truth, NoiseModel(seed=3))
    again = apply_noise(obs, truth, NoiseModel(seed=3))
    other = apply_noise(obs, truth, NoiseModel(seed=4))
    pd.testing.assert_frame_equal(first, again)
    assert (first["x"] != other["x"]).all()


def test_input_table_is_not_changed(sample):
    obs, truth = sample
    before = obs.copy()
    apply_noise(obs, truth)
    pd.testing.assert_frame_equal(obs, before)


def test_one_measurement_has_the_same_values_for_every_receiver(sample):
    obs, truth = sample
    noisy = apply_noise(obs, truth)
    v2x = noisy[noisy["src"] == "v2x"]
    groups = v2x.groupby(["tx_station", "obj_id", "t_meas"])
    assert groups.ngroups < len(v2x)                                   # the sample does hold repeated measurements
    for name in ("x", "y", "vx", "vy"):
        assert groups[name].nunique().max() == 1


def test_noise_does_not_depend_on_the_other_rows_or_on_their_order(sample):
    obs, truth = sample
    full = apply_noise(obs, truth)
    part = apply_noise(obs.iloc[::3], truth)
    pd.testing.assert_frame_equal(part, full.iloc[::3])
    mixed = obs.sample(frac=1.0, random_state=1)
    pd.testing.assert_frame_equal(apply_noise(mixed, truth).sort_index(), full)


def test_error_has_the_mean_and_the_spread_of_the_model(sample):
    obs, truth = sample
    model = NoiseModel(sigma_x=0.3, sigma_v=0.5)
    once = measurements(apply_noise(obs, truth, model))
    n = len(once)
    assert n > 1000
    for name, sigma in (("x", 0.3), ("y", 0.3), ("vx", 0.5), ("vy", 0.5)):
        error = (once[name] - once["gt_" + name]).to_numpy()
        assert abs(error.mean()) < 5.0 * sigma / np.sqrt(n)
        assert error.std() == pytest.approx(sigma, rel=5.0 / np.sqrt(2.0 * n))
    # squared error scaled by the declared covariance: 2 on average for a 2D position, if the covariance is honest
    nees = ((once["x"] - once["gt_x"]) ** 2 / once["P11"] + (once["y"] - once["gt_y"]) ** 2 / once["P22"]).mean()
    assert nees == pytest.approx(2.0, abs=5.0 * 2.0 / np.sqrt(n))


def test_covariance_and_bookkeeping_columns(sample):
    obs, truth = sample
    noisy = apply_noise(obs, truth, NoiseModel(sigma_x=0.2, sigma_v=0.4))
    assert np.allclose(noisy["P11"], 0.04) and np.allclose(noisy["P22"], 0.04)
    assert (noisy["P12"] == 0.0).all()
    assert np.allclose(noisy["Pv11"], 0.16) and np.allclose(noisy["Pv22"], 0.16)
    assert (noisy["t_pos"] == noisy["t_meas"]).all()
    distance = np.hypot(noisy["x"] - noisy["gt_x"], noisy["y"] - noisy["gt_y"])
    assert np.allclose(noisy["gt_dist"], distance)
    assert (noisy["gt_gap"] == 0.0).all()
    assert list(noisy.columns[:len(obs.columns)]) == list(obs.columns)
    assert list(noisy.columns[len(obs.columns):]) == ["gt_x", "gt_y", "gt_vx", "gt_vy", "gt_gap"]


def test_without_noise_the_state_is_the_true_state_at_the_measurement_time(sample):
    obs, truth = sample
    exact = apply_noise(obs, truth, NoiseModel(sigma_x=0.0, sigma_v=0.0))
    assert (exact["x"] == exact["gt_x"]).all() and (exact["vy"] == exact["gt_vy"]).all()
    # where the log already holds the position of the measurement time, both agree (the CPM rounds to 1 cm)
    same_time = (obs["t_pos"] == obs["t_meas"]).to_numpy()
    assert same_time.sum() > 1000
    assert np.abs(exact["x"] - obs["x"])[same_time].max() <= 0.011
    assert np.abs(exact["y"] - obs["y"])[same_time].max() <= 0.011
    # the other rows were logged with a newer position: the noise model moves them back to the measurement time
    moved = np.hypot(exact["x"] - obs["x"], exact["y"] - obs["y"])[~same_time]
    assert len(moved) > 0 and moved.max() > 0.1


def test_other_sigma_for_one_origin(sample):
    obs, truth = sample
    noisy = apply_noise(obs, truth, NoiseModel(overrides={"radar": (0.0, 0.0)}))
    assert (noisy["meas_src"] == "radar").all()                 # the sample has radars only
    assert (noisy["x"] == noisy["gt_x"]).all() and (noisy["P11"] == 0.0).all()


def test_rows_without_a_true_object(sample):
    obs, truth = sample
    broken = obs.copy()
    broken.loc[broken.index[:5], "gt_match_id"] = "nobody"
    with pytest.raises(ValueError, match="5 of"):
        apply_noise(broken, truth)
    assert len(apply_noise(broken, truth, missing="drop")) == len(obs) - 5


def test_rows_without_a_measurement_time(sample):
    obs, truth = sample
    broken = obs.copy()
    broken.loc[broken.index[:3], "t_meas"] = np.nan
    with pytest.raises(ValueError, match="3 of"):
        apply_noise(broken, truth)
    kept = apply_noise(broken, truth, missing="drop")
    assert len(kept) == len(obs) - 3 and np.isfinite(kept["x"]).all()


def test_missing_sensor_name_does_not_depend_on_the_column_type():
    who, obj, t = pd.Series(["v", "v"]), pd.Series(["1", "1"]), pd.Series([1.0, 1.0])
    as_text = measurement_keys(0, who, pd.Series([None, "radar"], dtype="string"), obj, t)
    as_object = measurement_keys(0, who, pd.Series([np.nan, "radar"], dtype=object), obj, t)
    assert as_text.tolist() == as_object.tolist()
    assert as_text[0] != as_text[1]


def test_one_measurement_written_with_two_float_values_is_one_measurement():
    who, what, obj = pd.Series(["v", "v"]), pd.Series(["cpm", "cpm"]), pd.Series(["1", "1"])
    keys = measurement_keys(0, who, what, obj, pd.Series([27.3, 27.299999999999997]))
    assert keys[0] == keys[1]


@pytest.mark.parametrize("bad", [-1.0, float("nan")])
def test_bad_sigma_is_refused(bad):
    with pytest.raises(ValueError):
        NoiseModel(sigma_x=bad)
    with pytest.raises(ValueError):
        NoiseModel(overrides={"radar": (0.3, bad)})


FIXED_KEYS = [16461349135189070951, 13370100799857090529]
FIXED_VALUES = [
    [-0.38410553702154593, -0.11461973686945033, -0.19295763817223993, 0.030601086523219856],
    [-0.209252474897533, -0.11275591087045997, 1.3272419178098789, 1.4475757738305974],
]
