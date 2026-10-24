"""Tests of the kinematic fusion (task B7): covariance intersection and the other methods, the fusion of a tick, the check of a run."""
import numpy as np
import pandas as pd
import pytest

from aoc.fusion import kinematics
from aoc.fusion.alignment import Aligned, align
from aoc.fusion.association import associate
from aoc.fusion.kinematics import (
    FusedStates,
    KinematicsModel,
    check_run,
    ci_weight,
    covariance_intersection,
    fuse_states,
    independent_fusion,
    true_states,
    with_states,
)
from aoc.fusion.replay import Run, load_run


@pytest.fixture(scope="module")
def run(exported_runs):
    folder, _ = exported_runs["run_aoc_sensors"]
    return load_run(folder)


def random_spd(rng, d, n=None):
    """Random covariances: well conditioned, symmetric, positive."""
    shape = (d, d) if n is None else (n, d, d)
    a = rng.standard_normal(shape)
    return a @ np.swapaxes(a, -1, -2) + d * np.eye(d)


def brute_omega(pa, pb, criterion, grid=200001):
    ia, ib = np.linalg.inv(pa), np.linalg.inv(pb)
    w = np.linspace(0.0, 1.0, grid)
    sizes = [np.linalg.det(np.linalg.inv(o * ia + (1 - o) * ib)) if criterion == "det" else np.trace(np.linalg.inv(o * ia + (1 - o) * ib)) for o in w]
    return w[int(np.argmin(sizes))]


# ----------------------------------------------------------------------------
# Two estimates
# ----------------------------------------------------------------------------
def test_covariance_intersection_by_hand():
    pa, pb = np.diag([1.0, 4.0]), np.diag([4.0, 1.0])
    assert ci_weight(pa, pb)[0] == pytest.approx(0.5, abs=1e-6)                 # the two are mirror images: the same weight
    x, p = covariance_intersection([0.0, 0.0], pa, [1.0, 1.0], pb)
    assert x == pytest.approx([0.2, 0.8]) and p == pytest.approx(1.6 * np.eye(2))   # P^-1 = 0.5 diag(1, 1/4) + 0.5 diag(1/4, 1) = diag(5/8, 5/8)
    assert np.linalg.det(p) == pytest.approx(2.56) and np.linalg.det(p) < min(np.linalg.det(pa), np.linalg.det(pb))
    x, p = independent_fusion([0.0, 0.0], pa, [1.0, 1.0], pb)
    assert x == pytest.approx([0.2, 0.8]) and p == pytest.approx(0.8 * np.eye(2))   # the inverse covariances add up
    x, p = covariance_intersection([1.0, 2.0], pa, [1.0, 2.0], pa)
    assert x == pytest.approx([1.0, 2.0]) and p == pytest.approx(pa)                # the same estimate twice: nothing is learnt
    x, p = independent_fusion([1.0, 2.0], pa, [1.0, 2.0], pa)
    assert p == pytest.approx(pa / 2)                                               # the Kalman way counts it twice


def test_weight_against_a_brute_force():
    rng = np.random.default_rng(30)
    for d in (2, 4):
        for _ in range(6):
            pa, pb = random_spd(rng, d), random_spd(rng, d)
            for criterion in ("det", "trace"):
                assert ci_weight(pa, pb, criterion)[0] == pytest.approx(brute_omega(pa, pb, criterion), abs=2e-5)
    with pytest.raises(ValueError, match="criterion"):
        ci_weight(pa, pb, "volume")


def test_covariance_intersection_properties():
    rng = np.random.default_rng(31)
    n, d = 50, 4
    pa, pb = random_spd(rng, d, n), random_spd(rng, d, n)
    xa, xb = rng.standard_normal((n, d)), rng.standard_normal((n, d))
    x, p = covariance_intersection(xa, pa, xb, pb)
    assert x.shape == (n, d) and p.shape == (n, d, d)
    assert np.allclose(p, np.swapaxes(p, 1, 2)) and (np.linalg.eigvalsh(p) > 0).all()
    assert (np.linalg.det(p) <= np.minimum(np.linalg.det(pa), np.linalg.det(pb)) * (1 + 1e-9)).all()     # smaller than both members
    x_back, p_back = covariance_intersection(xb, pb, xa, pa)
    assert np.allclose(x, x_back, atol=1e-5) and np.allclose(p, p_back, atol=1e-5)                        # the order does not matter
    for omega in (0.0, 1.0):
        x_edge, p_edge = covariance_intersection(xa, pa, xb, pb, omega=omega)
        assert np.allclose(x_edge, xb if omega == 0.0 else xa) and np.allclose(p_edge, pb if omega == 0.0 else pa)
    x_t, p_t = covariance_intersection(xa, pa, xb, pb, criterion="trace")
    assert (np.trace(p_t, axis1=1, axis2=2) <= np.minimum(np.trace(pa, axis1=1, axis2=2), np.trace(pb, axis1=1, axis2=2)) * (1 + 1e-9)).all()
    assert (np.trace(p_t, axis1=1, axis2=2) <= np.trace(p, axis1=1, axis2=2) * (1 + 1e-9)).all()           # the trace criterion gives the smaller trace
    assert (np.linalg.det(p) <= np.linalg.det(p_t) * (1 + 1e-9)).all()                                      # and the determinant criterion the smaller determinant
    with pytest.raises(ValueError, match="between 0 and 1"):
        covariance_intersection(xa, pa, xb, pb, omega=1.5)
    with pytest.raises(ValueError, match="tables of states"):
        covariance_intersection(xa, pa, xb[:, :2], pb)
    with pytest.raises(ValueError, match="missing"):
        covariance_intersection(np.full(d, np.nan), pa[0], xb[0], pb[0])
    with pytest.raises(ValueError, match="positive definite"):
        covariance_intersection(xa[0], np.zeros((d, d)), xb[0], pb[0])
    with pytest.raises(ValueError, match="positive definite"):
        covariance_intersection(xa[0], -pa[0], xb[0], pb[0])
    with pytest.raises(ValueError, match="symmetric"):
        covariance_intersection(xa[0], pa[0] + np.triu(np.ones((d, d)), 1), xb[0], pb[0])
    with pytest.raises(ValueError, match="same shape"):
        ci_weight(pa, pb[0])


def test_the_criterion_reaches_the_fusion_of_a_tick():
    rng = np.random.default_rng(34)
    states, covs = rng.standard_normal((6, 4)), random_spd(rng, 4, 6)
    covs[::2] *= np.array([10.0, 0.1, 10.0, 0.1])[:, None] * np.array([10.0, 0.1, 10.0, 0.1])[None, :]      # stretched one way, then the other
    aligned, groups = tick_like(states, covs, [0, 0, 1, 1, 2, 2])
    by_det = fuse_states(aligned, groups, KinematicsModel("ci", "det"))
    by_trace = fuse_states(aligned, groups, KinematicsModel("ci", "trace"))
    assert not np.allclose(by_det.cov, by_trace.cov)
    assert (np.trace(by_trace.cov, axis1=1, axis2=2) <= np.trace(by_det.cov, axis1=1, axis2=2) * (1 + 1e-9)).all()
    assert (np.linalg.det(by_det.cov) <= np.linalg.det(by_trace.cov) * (1 + 1e-9)).all()


def test_weight_in_special_cases():
    same = np.eye(4) * 2.0
    x, p = covariance_intersection([0.0, 0.0, 0.0, 0.0], same, [2.0, 4.0, 6.0, 8.0], same)
    assert x == pytest.approx([1.0, 2.0, 3.0, 4.0]) and p == pytest.approx(same)                        # equal covariances: the states are averaged
    assert ci_weight(np.eye(2), 10 * np.eye(2))[0] == 1.0 and ci_weight(10 * np.eye(2), np.eye(2))[0] == 0.0    # one member is better in every way
    rng = np.random.default_rng(35)
    q, _ = np.linalg.qr(rng.standard_normal((4, 4)))
    bad = q @ np.diag([1e-6, 1e-2, 1e2, 1e6]) @ q.T
    bad = (bad + bad.T) / 2
    other = random_spd(rng, 4)
    for criterion in ("det", "trace"):                                       # badly conditioned: still the right weight
        assert ci_weight(bad, other, criterion)[0] == pytest.approx(brute_omega(bad, other, criterion), abs=2e-5)
    x, p = covariance_intersection(np.zeros(4), bad, np.ones(4), other)
    assert np.isfinite(x).all() and (np.linalg.eigvalsh(p) > 0).all()


def test_covariance_intersection_stays_safe_with_shared_errors():
    """Two estimates that share a part of their error. The Kalman way becomes too sure, covariance intersection does not."""
    rng = np.random.default_rng(32)
    n, d = 40000, 2
    shared = rng.standard_normal((n, d)) * 1.0                                 # the part of the error that both estimates share
    ea = shared + rng.standard_normal((n, d)) * 0.5
    eb = shared + rng.standard_normal((n, d)) * 0.5
    pa = np.broadcast_to(np.eye(d) * (1.0 + 0.25), (n, d, d))                 # each estimate is honest about its own error
    pb = pa
    x_ci, p_ci = covariance_intersection(ea, pa, eb, pb)
    x_in, p_in = independent_fusion(ea, pa, eb, pb)
    nees = lambda e, p: float(np.mean(np.einsum("ni,nij,nj->n", e, np.linalg.inv(p), e)))
    assert nees(x_ci, p_ci) <= d * 1.02                                        # safe: the covariance is at least the real error
    assert nees(x_in, p_in) > d * 1.5                                          # too sure: it halved the covariance of a shared error
    independent_a = rng.standard_normal((n, d)) * np.sqrt(1.25)
    independent_b = rng.standard_normal((n, d)) * np.sqrt(1.25)
    x_in, p_in = independent_fusion(independent_a, pa, independent_b, pb)
    assert abs(nees(x_in, p_in) - d) < 0.1                                     # with independent errors the Kalman way is right
    x_ci, p_ci = covariance_intersection(independent_a, pa, independent_b, pb)
    assert nees(x_ci, p_ci) < d * 0.6                                          # and covariance intersection is larger than needed


# ----------------------------------------------------------------------------
# A whole tick
# ----------------------------------------------------------------------------
def tick_like(states, covs, groups, t_meas=None):
    n = len(states)
    objects = pd.DataFrame({"t_meas": t_meas if t_meas is not None else np.arange(n, dtype=float), "gt_match_id": pd.Series(["o"] * n, dtype=object)})
    return Aligned(t=1.0, ego="ego", objects=objects, state=np.asarray(states, dtype=float), cov=np.asarray(covs, dtype=float)), np.asarray(groups)


def plain_sequential(states, covs, rows, model):
    x, p = states[rows[0]], covs[rows[0]]
    for r in rows[1:]:
        if model.method == "ci":
            x, p = covariance_intersection(x, p, states[r], covs[r], criterion=model.criterion, steps=model.steps)
        else:
            x, p = independent_fusion(x, p, states[r], covs[r])
    return x, p


def test_fuse_states_against_a_plain_loop():
    rng = np.random.default_rng(33)
    for trial in range(20):
        n = int(rng.integers(1, 14))
        states, covs = rng.standard_normal((n, 4)) * 5, random_spd(rng, 4, n)
        groups = rng.integers(0, 5, n) * 3                                     # numbers with gaps, groups of any size, some empty
        aligned, groups = tick_like(states, covs, groups, t_meas=rng.random(n))
        for model in (KinematicsModel("ci", "det"), KinematicsModel("ci", "trace"), KinematicsModel("independent")):
            fused = fuse_states(aligned, groups, model)
            numbers = np.unique(groups)
            assert len(fused) == len(numbers) and fused.n_members.sum() == n
            for k, number in enumerate(numbers):
                rows = np.flatnonzero(groups == number)
                assert (fused.members[rows] == k).all()
                x, p = plain_sequential(states, covs, rows, model)
                assert np.allclose(fused.state[k], x) and np.allclose(fused.cov[k], p)
                if len(rows) == 1:
                    assert np.array_equal(fused.state[k], states[rows[0]]) and np.array_equal(fused.cov[k], covs[rows[0]])
        newest = fuse_states(aligned, groups, KinematicsModel("newest"))
        for k, number in enumerate(numbers):
            rows = np.flatnonzero(groups == number)
            pick = rows[int(np.argmax(aligned.objects["t_meas"].to_numpy()[rows]))]
            assert np.array_equal(newest.state[k], states[pick]) and np.array_equal(newest.cov[k], covs[pick])
        fast = fuse_states(aligned, groups, KinematicsModel("fast_ci"))
        for k, number in enumerate(numbers):
            rows = np.flatnonzero(groups == number)
            weights = 1.0 / np.trace(covs[rows], axis1=1, axis2=2)
            weights = weights / weights.sum()
            info = np.sum(weights[:, None, None] * np.linalg.inv(covs[rows]), axis=0)
            p = np.linalg.inv(info)
            x = p @ np.sum(weights[:, None] * np.einsum("nij,nj->ni", np.linalg.inv(covs[rows]), states[rows]), axis=0)
            assert np.allclose(fast.cov[k], p) and np.allclose(fast.state[k], x)


def test_fuse_states_edge_cases():
    aligned, groups = tick_like(np.zeros((0, 4)), np.zeros((0, 4, 4)), np.zeros(0, dtype=int))
    empty = fuse_states(aligned, groups)
    assert len(empty) == 0 and empty.state.shape == (0, 4) and empty.cov.shape == (0, 4, 4)
    aligned, groups = tick_like(np.zeros((2, 4)), np.broadcast_to(np.eye(4), (2, 4, 4)).copy(), [0, 0])
    with pytest.raises(ValueError, match="one group number"):
        fuse_states(aligned, [0])
    with pytest.raises(ValueError, match="whole numbers"):
        fuse_states(aligned, [0, -1])
    for bad in (dict(method="average"), dict(criterion="volume"), dict(steps=0)):
        with pytest.raises(ValueError):
            KinematicsModel(**bad)
    bad = Aligned(t=1.0, ego="e", objects=aligned.objects, state=np.full((2, 4), np.nan), cov=aligned.cov)
    with pytest.raises(ValueError, match="missing"):
        fuse_states(bad, groups)


def test_fuse_states_on_a_real_tick(run):
    tick = max(run.replay(run.egos[0]), key=lambda t: len(t.v2x))
    aligned = align(tick)
    groups = associate(aligned)
    fused = fuse_states(aligned, groups)
    assert len(fused) == len(np.unique(groups)) and (fused.n_members >= 1).all()
    det_fused = np.linalg.det(fused.cov)
    smallest = np.full(len(fused), np.inf)
    np.minimum.at(smallest, fused.members, np.linalg.det(aligned.cov))
    assert (det_fused <= smallest * (1 + 1e-9)).all()                           # never larger than the smallest member
    alone = fused.n_members == 1
    rows = np.array([np.flatnonzero(fused.members == k)[0] for k in np.flatnonzero(alone)], dtype=int)
    assert np.allclose(fused.state[alone], aligned.state[rows]) and np.allclose(fused.cov[alone], aligned.cov[rows])
    table = with_states(pd.DataFrame({"group": np.unique(groups)}), fused)
    assert list(table.columns) == ["group", "x", "y", "vx", "vy", "sigma_xy", "n_states"]
    assert np.allclose(table["sigma_xy"], np.sqrt((fused.cov[:, 0, 0] + fused.cov[:, 1, 1]) / 2))
    with pytest.raises(ValueError, match="holds"):
        with_states(table.iloc[:1], fused)
    truth = true_states(aligned, fused.members, len(fused), tick.truth)
    assert truth.shape == (len(fused), 4) and np.isfinite(truth).all()         # every group names one true object in the sample run
    error = np.hypot(fused.state[:, 0] - truth[:, 0], fused.state[:, 1] - truth[:, 1])
    assert np.median(error) < 1.0


def test_true_states_without_one_true_object():
    objects = pd.DataFrame({"gt_match_id": pd.Series(["o1", "o2", "o1", None], dtype=object)})
    aligned = Aligned(t=0.0, ego="e", objects=objects, state=np.zeros((4, 4)), cov=np.broadcast_to(np.eye(4), (4, 4, 4)).copy())
    truth = pd.DataFrame({"obj_id": ["o1", "o2"], "x": [1.0, 2.0], "y": [0.0, 0.0], "vx": [3.0, 4.0], "vy": [0.0, 0.0]})
    out = true_states(aligned, np.array([0, 0, 1, 2]), 3, truth)
    assert np.isnan(out[0]).all() and out[1].tolist() == [1.0, 0.0, 3.0, 0.0] and np.isnan(out[2]).all()


# ----------------------------------------------------------------------------
# The check of a run and the command
# ----------------------------------------------------------------------------
def test_check_of_a_run_against_a_recount(run):
    """The three sequential methods are recounted with a plain loop. fast_ci and newest are recounted with fuse_states itself:
    their formulas are checked against plain code in test_fuse_states_against_a_plain_loop, here only the measures are."""
    c = check_run(run)
    assert c["result"] == "PASS" and c["problems"] == [] and c["groups_of_several"] > 0 and c["without_truth"] == 0
    rows = {name: [] for name in c["measures"]}
    models = {"ci": KinematicsModel("ci", "det"), "ci_trace": KinematicsModel("ci", "trace"), "fast_ci": KinematicsModel("fast_ci"),
              "independent": KinematicsModel("independent"), "newest": KinematicsModel("newest")}
    for ego in run.egos:
        for tick in run.replay(ego):
            aligned = align(tick)
            if not len(aligned):
                continue
            groups = associate(aligned)
            table = tick.truth.set_index("obj_id")
            for number in np.unique(groups):
                members = np.flatnonzero(groups == number)
                if len(members) < 2:
                    continue
                name = aligned.objects["gt_match_id"].iloc[members[0]]
                true = table.loc[name, ["x", "y", "vx", "vy"]].to_numpy(dtype=float)
                for key, model in models.items():
                    x, p = (plain_sequential(aligned.state, aligned.cov, members, model) if key in ("ci", "ci_trace", "independent")
                            else (fuse_states(aligned, groups, model).state[list(np.unique(groups)).index(number)],
                                  fuse_states(aligned, groups, model).cov[list(np.unique(groups)).index(number)]))
                    e = x - true
                    rows[key].append((e[0] ** 2 + e[1] ** 2, np.hypot(e[0], e[1]) <= 1.5, e[:2] @ np.linalg.inv(p[:2, :2]) @ e[:2],
                                      e[2:] @ np.linalg.inv(p[2:, 2:]) @ e[2:]))
    for key, values in rows.items():
        values = np.array(values, dtype=float)
        m = c["measures"][key]
        assert m["n"] == len(values)
        assert m["rmse"] == pytest.approx(np.sqrt(values[:, 0].sum() / (2 * len(values))), abs=1e-3)
        assert m["within"] == pytest.approx(values[:, 1].mean(), abs=1e-3)
        assert m["nees"] == pytest.approx(values[:, 2].mean(), abs=2e-3)
        assert m["nees_speed"] == pytest.approx(values[:, 3].mean(), abs=2e-3)
    assert c["measures"]["ci"]["nees"] <= c["measures"]["independent"]["nees"]                             # covariance intersection is the safer one
    assert c["order_shift"] is not None and 0.0 <= c["order_shift"] < 1.0 and c["groups_of_three"] > 0
    assert c["measures"]["ci"]["rmse"] <= c["measures"]["newest"]["rmse"] * 1.05


def test_check_of_odd_runs(run, monkeypatch, exported_runs, capsys):
    sensors_only = Run(obs=run.obs[run.obs["src"] != "v2x"], truth=run.truth, noise=run.noise, info=run.info)
    c = check_run(sensors_only)
    assert c["result"] == "PASS" and c["measures"]["ci"]["rmse"] is not None                               # two radars see the same car
    empty = check_run(Run(obs=run.obs.iloc[:0], truth=run.truth))
    assert empty["result"] == "FAIL" and empty["problems"] == ["the run holds no object"]

    def too_sure(xa, pa, xb, pb, omega=None, criterion="det", steps=40):                                    # a "covariance intersection" far too sure of itself
        x, p = independent_fusion(xa, pa, xb, pb)
        return x, p / 4.0

    monkeypatch.setattr(kinematics, "covariance_intersection", too_sure)
    c = check_run(run)
    assert c["result"] == "FAIL" and any("more sure than its members" in p for p in c["problems"])
    assert not any("larger" in p for p in c["problems"])                                                    # a smaller covariance keeps the promise
    monkeypatch.undo()

    def too_careful(xa, pa, xb, pb):                                                                           # a "Kalman way" that inflates the covariance
        x, p = kinematics.covariance_intersection(xa, pa, xb, pb)
        return x, p * 4.0

    monkeypatch.setattr(kinematics, "independent_fusion", too_careful)
    c = check_run(run)
    assert c["result"] == "FAIL"
    assert any(p.startswith("independent: ") and "larger determinant than the smallest member" in p for p in c["problems"])
    assert not any(p.startswith("ci") for p in c["problems"])
    folder, _ = exported_runs["run_aoc_sensors"]
    assert kinematics.main([str(folder)]) == 3
    assert "result:              FAIL (" in capsys.readouterr().out


def test_command(exported_runs, capsys):
    folder, _ = exported_runs["run_aoc_sensors"]
    assert kinematics.main([str(folder), "--hold", "2"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("B7 KINEMATICS CHECK") and "result:              PASS" in out and "forgotten when it is older than 2 s" in out
    c = check_run(load_run(folder), hold=2.0)
    for label, key in (("covariance intersection, determinant", "ci"), ("independent (Kalman)", "independent"), ("newest member alone", "newest")):
        line = next(line for line in out.splitlines() if label in line)
        assert line.split()[-4:] == ["%.3f" % c["measures"][key]["rmse"], "%.1f%%" % (100 * c["measures"][key]["within"]),
                                     "%.2f" % c["measures"][key]["nees"], "%.2f" % c["measures"][key]["nees_speed"]]
    assert kinematics.main([str(folder / "nothing_here")]) == 2
    assert kinematics.main([str(folder), "--hold", "0"]) == 2


def test_fused_states_type():
    fused = FusedStates(state=np.zeros((2, 4)), cov=np.zeros((2, 4, 4)), members=np.array([0, 1, 1]), n_members=np.array([1, 2]))
    assert len(fused) == 2
