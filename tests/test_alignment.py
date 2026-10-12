"""Tests of the time alignment (task B3)."""
import numpy as np
import pandas as pd
import pytest

from aoc.fusion.alignment import Q_BY_CLASS, Q_DEFAULT, align, predict, process_noise
from aoc.fusion.replay import load_run
from aoc.theory import reliability


def one_row(**values):
    base = dict(x=10.0, y=-5.0, vx=2.0, vy=-1.0, P11=0.09, P12=0.0, P22=0.09, Pv11=0.25, Pv22=0.25)
    base.update(values)
    return pd.DataFrame([base])


@pytest.fixture(scope="module")
def run(exported_runs):
    folder, _ = exported_runs["run_aoc_sensors"]
    return load_run(folder)


# ----------------------------------------------------------------------------
# The motion model
# ----------------------------------------------------------------------------
def test_fresh_object_is_not_changed():
    state, cov = predict(one_row(), np.array([0.0]), np.array([4.0]))
    assert state[0].tolist() == [10.0, -5.0, 2.0, -1.0]
    assert np.allclose(cov[0], np.diag([0.09, 0.09, 0.25, 0.25]))


def test_old_object_moves_and_its_uncertainty_grows():
    age, q = 2.0, 0.5
    state, cov = predict(one_row(), np.array([age]), np.array([q]))
    assert state[0].tolist() == pytest.approx([14.0, -7.0, 2.0, -1.0])
    position = 0.09 + age**2 * 0.25 + q * age**3 / 3.0
    assert cov[0, 0, 0] == pytest.approx(position) and cov[0, 1, 1] == pytest.approx(position)
    assert cov[0, 2, 2] == pytest.approx(0.25 + q * age) and cov[0, 3, 3] == pytest.approx(0.25 + q * age)
    assert cov[0, 0, 2] == pytest.approx(age * 0.25 + q * age**2 / 2.0) and cov[0, 2, 0] == cov[0, 0, 2]
    assert cov[0, 1, 3] == pytest.approx(cov[0, 0, 2])
    assert cov[0, 0, 1] == 0.0 and cov[0, 0, 3] == 0.0 and cov[0, 1, 2] == 0.0
    assert np.all(np.linalg.eigvalsh(cov[0]) > 0.0)


@pytest.mark.parametrize("name", ["pedestrian", "car (moving)", "vehicle (parked)"])
@pytest.mark.parametrize("age", [0.13, 1.0, 3.0])
def test_position_variance_is_equation_1_of_the_theory_note(name, age):
    sx, sv, sxv, q = reliability.CLASSES[name][:4]
    _, cov = predict(one_row(P11=sx**2, P22=sx**2, Pv11=sv**2, Pv22=sv**2), np.array([age]), np.array([q]))
    assert cov[0, 0, 0] == pytest.approx(float(reliability.sigma2(age, sx, sv, sxv, q)))


def test_axes_keep_their_own_values():
    state, cov = predict(one_row(P11=0.04, P12=0.01, P22=0.16, Pv11=0.25, Pv22=1.0), np.array([1.0]), np.array([0.0]))
    assert cov[0, 0, 0] == pytest.approx(0.29) and cov[0, 1, 1] == pytest.approx(1.16)
    assert cov[0, 0, 1] == pytest.approx(0.01) and cov[0, 1, 0] == pytest.approx(0.01)
    assert cov[0, 0, 2] == pytest.approx(0.25) and cov[0, 1, 3] == pytest.approx(1.0)


def test_prediction_matches_simulated_motion():
    # many objects with a random error at time 0 and a random acceleration afterwards
    rng = np.random.default_rng(0)
    n, age, q, steps = 40_000, 1.5, 2.0, 300
    dt = age / steps
    ex, ev = 0.3 * rng.standard_normal(n), 0.5 * rng.standard_normal(n)          # error of the estimate at time 0
    px, pv = np.zeros(n), np.zeros(n)                                            # true motion away from constant velocity
    for _ in range(steps):
        px += pv * dt
        pv += np.sqrt(q * dt) * rng.standard_normal(n)
    error_position = ex + age * ev - px                                          # predicted minus true
    error_speed = ev - pv
    _, cov = predict(one_row(), np.array([age]), np.array([q]))
    assert error_position.var() == pytest.approx(cov[0, 0, 0], rel=0.03)
    assert error_speed.var() == pytest.approx(cov[0, 2, 2], rel=0.03)
    assert np.cov(error_position, error_speed)[0, 1] == pytest.approx(cov[0, 0, 2], rel=0.04)


def test_bad_inputs_are_refused():
    with pytest.raises(ValueError, match="noise model"):
        predict(one_row(P11=np.nan), np.array([0.0]), np.array([1.0]))
    with pytest.raises(ValueError, match="ages"):
        predict(one_row(), np.array([-0.5]), np.array([1.0]))
    with pytest.raises(ValueError, match="ages"):
        predict(one_row(), np.array([np.nan]), np.array([1.0]))


def test_bad_covariances_and_shapes_are_refused():
    for bad in (dict(P11=-0.01), dict(Pv22=-1.0), dict(P12=0.2)):                # 0.2 is more than sqrt(0.09 * 0.09)
        with pytest.raises(ValueError, match="variance"):
            predict(one_row(**bad), np.array([0.0]), np.array([1.0]))
    with pytest.raises(ValueError, match="variance"):
        predict(one_row(), np.array([0.0]), np.array([-1.0]))
    two = pd.concat([one_row(), one_row()], ignore_index=True)
    with pytest.raises(ValueError, match="per row"):
        predict(two, np.array([0.0]), np.array([1.0, 1.0]))


def test_intensity_by_class():
    classes = pd.Series(["passenger_car", "pedestrian", "cyclist", "heavy_truck", "something_new", None], dtype=object)
    q = process_noise(classes)
    assert q.tolist() == pytest.approx([4.0, 0.5, 0.98, 1.44, Q_DEFAULT, Q_DEFAULT])
    assert Q_DEFAULT == max(Q_BY_CLASS.values()) == 4.0
    assert process_noise(classes, {"pedestrian": 9.0}, q_default=1.0).tolist() == [1.0, 9.0, 1.0, 1.0, 1.0, 1.0]


# ----------------------------------------------------------------------------
# A tick of the sample run
# ----------------------------------------------------------------------------
def test_align_keeps_every_object_of_the_tick(run):
    seen = 0
    for tick in run.replay("flow0.0"):
        aligned = align(tick)
        n_local = len(tick.local)
        assert len(aligned) == n_local + len(tick.v2x) == len(aligned.state) == len(aligned.cov)
        assert aligned.t == tick.t and aligned.ego == "flow0.0"
        objects = aligned.objects
        assert objects.index.tolist() == list(range(len(objects)))
        assert objects["source"][:n_local].tolist() == tick.local["sensor"].tolist()
        assert objects["source"][n_local:].tolist() == ["v2x:" + s for s in tick.v2x["tx_station"]]
        assert np.allclose(objects[["x_t", "y_t", "vx_t", "vy_t"]].to_numpy(), aligned.state)
        assert np.allclose(objects["x_t"], objects["x"] + objects["age_now"] * objects["vx"])
        assert np.allclose(objects["sigma_t"] ** 2, (aligned.cov[:, 0, 0] + aligned.cov[:, 1, 1]) / 2.0)
        assert np.allclose(objects["sigma_t"][:n_local], 0.3)
        seen += len(aligned)
    assert seen > 500


def test_align_of_an_empty_tick(run):
    tick = next(iter(run.replay("flow0.0")))
    empty = type(tick)(t=tick.t, ego=tick.ego, ego_state=None, local=tick.local.iloc[:0], v2x_new=tick.v2x_new.iloc[:0],
                       v2x=tick.v2x.iloc[:0], truth=tick.truth)
    aligned = align(empty)
    assert len(aligned) == 0 and aligned.state.shape == (0, 4) and aligned.cov.shape == (0, 4, 4)
    assert "source" in aligned.objects.columns


def test_aligned_positions_agree_with_the_truth(run):
    # squared position error in units of the predicted covariance: 2 on average if the covariance is right
    scores, ages = [], []
    for ego in run.egos:
        for tick in run.replay(ego):
            aligned = align(tick)
            true = tick.truth.set_index("obj_id")
            for i, name in enumerate(aligned.objects["gt_match_id"]):
                if name in true.index:
                    diff = aligned.state[i, :2] - true.loc[name, ["x", "y"]].to_numpy(dtype=float)
                    scores.append(diff @ np.linalg.solve(aligned.cov[i, :2, :2], diff))
                    ages.append(aligned.objects["age_now"][i])
    scores, ages = np.array(scores), np.array(ages)
    fresh = ages < 1e-6
    assert fresh.sum() > 1000 and scores[fresh].mean() == pytest.approx(2.0, abs=0.2)
    young = (ages >= 1e-6) & (ages <= 0.3)
    assert young.sum() > 500 and 1.0 < scores[young].mean() < 2.5


def test_several_rows_of_one_sensor_for_one_object_keep_the_newest(run):
    # with a tick of 0.2 s, a sensor gives two rows per object: one per sensor step
    checked = 0
    for tick in run.replay("flow0.1", period=0.2):
        repeated = tick.local.duplicated(["sensor", "obj_id"], keep=False)
        aligned = align(tick)
        from_sensors = aligned.objects[~aligned.objects["source"].str.startswith("v2x:")]
        assert not from_sensors.duplicated(["sensor", "obj_id"]).any()
        assert len(from_sensors) == len(tick.local.drop_duplicates(["sensor", "obj_id"]))
        if repeated.any():
            newest = tick.local.groupby(["sensor", "obj_id"])["t_meas"].max()
            got = from_sensors.set_index(["sensor", "obj_id"])["t_meas"]
            assert (got == newest.reindex(got.index)).all()
            checked += 1
    assert checked > 10


def test_an_object_without_a_source_is_refused(run):
    tick = next(t for t in run.replay("flow0.0") if len(t.local) and len(t.v2x))
    tick.local.loc[tick.local.index[0], "sensor"] = None
    with pytest.raises(ValueError, match="no source"):
        align(tick)
    tick = next(t for t in run.replay("flow0.0") if len(t.local) and len(t.v2x))
    tick.v2x.loc[tick.v2x.index[0], "tx_station"] = None
    with pytest.raises(ValueError, match="no source"):
        align(tick)


def test_source_names_are_plain_strings(run):
    for tick in list(run.replay("flow0.0"))[::20]:
        aligned = align(tick)
        assert aligned.objects["source"].dtype == object
        assert all(isinstance(name, str) for name in aligned.objects["source"])
