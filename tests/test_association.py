"""Tests of the association (task B3)."""
import numpy as np
import pandas as pd
import pytest

from aoc.fusion import association as association_module
from aoc.fusion.alignment import Aligned, align
from aoc.fusion.association import (
    AssociationConfig,
    associate,
    best_matches,
    can_match,
    check_run,
    oracle_groups,
    pair_costs,
    pair_counts,
    source_order,
)
from aoc.fusion.replay import load_run

POSITION = AssociationConfig()
WITH_SPEED = AssociationConfig(use_velocity=True)


@pytest.fixture(scope="module")
def run(exported_runs):
    folder, _ = exported_runs["run_aoc_sensors"]
    return load_run(folder)


def make(rows, sigma=0.3, sigma_v=0.5):
    """An aligned tick made by hand. Each row: (source, true object, x, y) or (source, true object, x, y, sigma)."""
    source, truth, state, cov = [], [], [], []
    for row in rows:
        s = row[4] if len(row) > 4 else sigma
        source.append(row[0])
        truth.append(row[1])
        state.append([row[2], row[3], 0.0, 0.0])
        cov.append(np.diag([s**2, s**2, sigma_v**2, sigma_v**2]))
    objects = pd.DataFrame({"source": pd.Series(source, dtype=object), "gt_match_id": pd.Series(truth, dtype=object),
                            "age_now": 0.0})
    return Aligned(t=0.0, ego="ego", objects=objects, state=np.array(state, dtype=float).reshape(-1, 4),
                   cov=np.array(cov, dtype=float).reshape(-1, 4, 4))


def same_partition(a, b):
    """True if two lists of group numbers describe the same groups."""
    pairs = set(zip(a.tolist(), b.tolist()))
    return len(pairs) == len(set(a.tolist())) == len(set(b.tolist()))


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
def test_settings():
    assert POSITION.dimension == 2 and POSITION.gate == pytest.approx(9.2103, abs=1e-3)
    assert WITH_SPEED.dimension == 4 and WITH_SPEED.gate == pytest.approx(13.2767, abs=1e-3)
    assert POSITION.log_density == pytest.approx(np.log(0.01))
    assert WITH_SPEED.log_density == pytest.approx(np.log(0.01 * 0.03))
    for bad in (dict(gate_probability=1.0), dict(gate_probability=0.0), dict(density_position=0.0), dict(density_velocity=-1.0)):
        with pytest.raises(ValueError):
            AssociationConfig(**bad)


# ----------------------------------------------------------------------------
# Comparing two objects
# ----------------------------------------------------------------------------
def test_distance_and_cost_of_a_simple_pair():
    a = make([("s1", "a", 0.0, 0.0)])
    b = make([("s2", "a", 1.0, 0.0)])
    distance, cost = pair_costs(a.state, a.cov, b.state, b.cov, POSITION)
    assert distance[0, 0] == pytest.approx(1.0 / 0.18)
    assert cost[0, 0] == pytest.approx(1.0 / 0.18 + np.log((2 * np.pi) ** 2 * 0.18**2) + 2 * np.log(0.01))


@pytest.mark.parametrize("config", [POSITION, WITH_SPEED])
def test_pair_costs_against_the_plain_formula(config):
    rng = np.random.default_rng(1)
    d = config.dimension

    def random_objects(n):
        state = rng.normal(0.0, 3.0, (n, 4))
        root = rng.normal(0.0, 0.6, (n, 4, 4))
        return state, root @ root.transpose(0, 2, 1) + 0.05 * np.eye(4)

    (sa, ca), (sb, cb) = random_objects(30), random_objects(40)
    distance, cost = pair_costs(sa, ca, sb, cb, config)
    n_allowed = 0
    for i in range(30):
        for j in range(40):
            diff = (sa[i] - sb[j])[:d]
            joint = (ca[i] + cb[j])[:d, :d]
            d2 = diff @ np.linalg.solve(joint, diff)
            c = d2 + np.log(np.linalg.det(2 * np.pi * joint)) + 2 * config.log_density
            if d2 <= config.gate and c < 0:
                n_allowed += 1
            if np.isfinite(distance[i, j]):                    # the pair was fully compared
                assert distance[i, j] == pytest.approx(d2) and cost[i, j] == pytest.approx(c)
            else:                                              # skipped by the first pass: it must not be an allowed match
                assert d2 > config.gate
    assert n_allowed > 20


def test_a_vague_object_cannot_match_anything():
    rng = np.random.default_rng(2)
    for config in (POSITION, WITH_SPEED):
        root = rng.normal(0.0, 1.0, (300, 4, 4)) * rng.uniform(0.2, 6.0, (300, 1, 1))
        cov = root @ root.transpose(0, 2, 1) + 0.01 * np.eye(4)
        usable = can_match(cov, config)
        assert usable.any() and (~usable).any()
        state = np.zeros((300, 4))                             # all at the same place: the easiest case for a match
        fresh = np.broadcast_to(np.diag([0.09, 0.09, 0.25, 0.25]), (1, 4, 4))
        distance, cost = pair_costs(state, cov, np.zeros((1, 4)), fresh, config)
        allowed = (distance[:, 0] <= config.gate) & (cost[:, 0] < 0.0)
        assert not allowed[~usable].any()


# ----------------------------------------------------------------------------
# The best global choice
# ----------------------------------------------------------------------------
def test_best_matches_looks_at_the_whole_table():
    # taking the lowest cost first (-9 for group 0 and object 0) would leave object 1 without a match
    cost = np.array([[-9.0, -8.0], [-7.0, 5.0]])
    distance = np.zeros((2, 2))
    assert sorted(best_matches(distance, cost, gate=9.0)) == [(0, 1), (1, 0)]


def test_best_matches_respects_the_two_tests():
    distance = np.array([[1.0, 20.0, 1.0]])
    cost = np.array([[-5.0, -9.0, 0.5]])
    assert best_matches(distance, cost, gate=9.0) == [(0, 0)]                  # column 1 fails the gate, column 2 the coincidence test
    assert best_matches(np.array([[20.0]]), np.array([[-5.0]]), gate=9.0) == []
    assert best_matches(np.zeros((0, 3)), np.zeros((0, 3)), gate=9.0) == []
    assert best_matches(np.zeros((2, 0)), np.zeros((2, 0)), gate=9.0) == []


def test_one_group_takes_one_object_of_a_source():
    cost = np.array([[-5.0, -4.0]])                                           # two objects want the same group
    assert best_matches(np.zeros((1, 2)), cost, gate=9.0) == [(0, 0)]


# ----------------------------------------------------------------------------
# Groups
# ----------------------------------------------------------------------------
def test_three_objects_seen_by_three_sources():
    rows = []
    for source, shift in (("radar_a", 0.0), ("radar_b", 0.2), ("v2x:car7", -0.2)):
        rows += [(source, "a", 0.0 + shift, 0.0), (source, "b", 10.0 + shift, 0.0), (source, "c", 0.0 + shift, 12.0)]
    aligned = make(rows)
    groups = associate(aligned)
    assert groups.tolist() == [0, 1, 2, 0, 1, 2, 0, 1, 2]
    assert same_partition(groups, oracle_groups(aligned))


def test_objects_far_apart_stay_alone():
    groups = associate(make([("s1", "a", 0.0, 0.0), ("s2", "b", 3.0, 0.0), ("s3", "c", 0.0, 3.0)]))
    assert groups.tolist() == [0, 1, 2]


def test_two_objects_of_one_source_are_never_in_one_group():
    # s2 reports two objects 20 cm apart, both close to the object of s1
    aligned = make([("s1", "a", 0.0, 0.0), ("s2", "a", 0.1, 0.0), ("s2", "b", -0.1, 0.0), ("s1", "b", 9.0, 9.0)])
    groups = associate(aligned)
    assert groups[1] != groups[2]
    assert sorted(groups.tolist()) == [0, 1, 1, 2] or sorted(groups.tolist()) == [0, 0, 1, 2]
    assert (groups[0] == groups[1]) != (groups[0] == groups[2])              # exactly one of the two joins s1


def test_a_vague_object_stays_alone_even_at_the_same_place():
    aligned = make([("s1", "a", 0.0, 0.0), ("v2x:old", "a", 0.0, 0.0, 30.0), ("s2", "a", 0.1, 0.1)])
    assert associate(aligned).tolist() == [0, 1, 0]                           # s1 and s2 form one group, the sender is alone


def test_a_chain_through_a_common_neighbour():
    # the third object is too far from the first one, but close to the second one: it joins through it
    aligned = make([("s1", "a", 0.0, 0.0), ("s2", "a", 1.0, 0.0), ("s3", "a", 2.0, 0.0)])
    distance, _ = pair_costs(aligned.state[:1], aligned.cov[:1], aligned.state[2:], aligned.cov[2:], POSITION)
    assert distance[0, 0] > POSITION.gate
    assert associate(aligned).tolist() == [0, 0, 0]


def test_order_of_the_rows_does_not_change_the_groups():
    rng = np.random.default_rng(3)
    rows = []
    for k in range(12):                                                       # 12 real objects on a grid of 8 m
        cx, cy = 8.0 * (k % 4), 8.0 * (k // 4)
        for source in ("radar_a", "radar_b", "v2x:1", "v2x:2", "v2x:3"):
            if rng.uniform() < 0.8:
                rows.append((source, "obj%d" % k, cx + 0.3 * rng.standard_normal(), cy + 0.3 * rng.standard_normal()))
    aligned = make(rows)
    groups = associate(aligned)
    assert same_partition(groups, oracle_groups(aligned))
    order = rng.permutation(len(rows))
    shuffled = make([rows[i] for i in order])
    assert same_partition(associate(shuffled), groups[order])


def test_group_numbers_are_dense_and_cover_every_object(run):
    for tick in list(run.replay("flow0.1"))[::9]:
        aligned = align(tick)
        groups = associate(aligned)
        assert len(groups) == len(aligned)
        if len(groups):
            assert sorted(set(groups.tolist())) == list(range(groups.max() + 1))
            pairs = set(zip(groups.tolist(), aligned.objects["source"]))
            assert len(pairs) == len(groups)                                   # one object per source in a group


def test_exact_estimates_can_be_compared():
    # a covariance of zero (an exact estimate) must not break the comparison
    for config in (POSITION, WITH_SPEED):
        aligned = make([("s1", "a", 5.0, 5.0), ("s2", "a", 5.0, 5.0), ("s3", "b", 5.02, 5.0)], sigma=0.0, sigma_v=0.0)
        assert associate(aligned, config).tolist() == [0, 0, 1]              # 2 cm apart is another object when both are exact


def test_an_object_without_a_source_is_refused():
    for missing in (None, np.nan):
        aligned = make([("s1", "a", 0.0, 0.0), (missing, "a", 0.0, 0.0)])
        with pytest.raises(ValueError, match="source"):
            associate(aligned)


def test_empty_tick():
    assert associate(make([])).tolist() == []
    assert pair_counts(make([]), np.zeros(0, dtype=np.int64))["same_group"] == 0


def test_with_speed_two_objects_at_one_place_with_other_speeds_stay_apart():
    aligned = make([("s1", "a", 0.0, 0.0), ("s2", "b", 0.2, 0.0)])
    aligned.state[1, 2] = 8.0                                                 # the second one drives at 8 m/s
    assert associate(aligned, POSITION).tolist() == [0, 0]
    assert associate(aligned, WITH_SPEED).tolist() == [0, 1]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def test_sources_are_processed_sensors_first():
    objects = pd.DataFrame({"source": ["v2x:b", "RearRadar", "v2x:a", "FrontRadar", "v2x:b"]})
    assert source_order(objects) == ["FrontRadar", "RearRadar", "v2x:a", "v2x:b"]


def test_oracle_groups():
    aligned = make([("s1", "a", 0, 0), ("s2", "b", 0, 0), ("s3", "a", 50, 50), ("s1", None, 0, 0), ("s2", None, 0, 0)])
    assert oracle_groups(aligned).tolist() == [0, 1, 0, 2, 3]


def test_pair_counts():
    aligned = make([("s1", "a", 0, 0), ("s2", "a", 0, 0), ("s3", "a", 0, 0), ("s1", "b", 0, 0), ("s2", "b", 0, 0)])
    aligned.objects.loc[2, "age_now"] = 2.0                                   # the third object is old
    counts = pair_counts(aligned, np.array([0, 0, 1, 2, 0]))
    # pairs of one real object from two sources: (0,1) (0,2) (1,2) for a, (3,4) for b
    assert counts["same_truth"] == 4 and counts["same_truth_found"] == 1      # only (0,1) is in one group
    assert counts["fresh_same_truth"] == 2 and counts["fresh_same_truth_found"] == 1
    assert counts["recent_same_truth"] == 2 and counts["recent_same_truth_found"] == 1
    assert pair_counts(aligned, np.array([0, 0, 1, 2, 0]), recent_age=3.0)["recent_same_truth"] == 4
    # pairs in one group from two sources: (0,1) and (0,4); (1,4) is the same source
    assert counts["same_group"] == 2 and counts["same_group_true"] == 1


# ----------------------------------------------------------------------------
# The sample runs
# ----------------------------------------------------------------------------
def test_check_of_the_sample_run_passes(run, exported_runs, capsys):
    result = check_run(run)
    assert result["result"] == "PASS" and result["problems"] == []
    assert result["precision"] == 1.0 and result["recall_fresh"] > 0.97
    assert result["same_source_twice_in_a_group"] == 0
    folder, _ = exported_runs["run_aoc_sensors"]
    assert association_module.main([str(folder)]) == 0
    assert "result:              PASS" in capsys.readouterr().out
    assert association_module.main([str(folder), "--with-speed", "--hold", "1.0"]) == 0
    assert association_module.main([str(folder / "missing")]) == 2
    assert association_module.main([str(folder), "--gate", "2"]) == 2


def test_check_of_the_run_with_the_example_sensors_passes(exported_runs):
    folder, _ = exported_runs["run_example_sensors"]
    result = check_run(load_run(folder))
    assert result["result"] == "PASS" and result["precision"] > 0.999 and result["recall_fresh"] > 0.97


def test_check_fails_on_a_bad_association(run, monkeypatch):
    monkeypatch.setattr(association_module, "associate", lambda aligned, config=None: np.zeros(len(aligned), dtype=np.int64))
    merged = check_run(run)                                                   # everything in one group
    assert merged["result"] == "FAIL" and merged["precision"] < 0.99 and merged["same_source_twice_in_a_group"] > 0
    monkeypatch.setattr(association_module, "associate", lambda aligned, config=None: np.arange(len(aligned), dtype=np.int64))
    split = check_run(run)                                                    # every object alone
    assert split["result"] == "FAIL" and split["recall_fresh"] == 0.0

    def young_only(aligned, config=None):                                     # objects older than 0.5 s are never matched
        groups = associate(aligned, config)
        old = aligned.objects["age_now"].to_numpy() > 0.5
        groups[old] = groups.max(initial=-1) + 1 + np.arange(int(old.sum()))
        return groups

    monkeypatch.setattr(association_module, "associate", young_only)
    partial = check_run(run)
    assert partial["recall_fresh"] > 0.95 and partial["recall_recent"] < 0.90 and partial["result"] == "FAIL"


def test_check_with_a_longer_tick_period(run):
    result = check_run(run, period=0.2)
    assert result["result"] == "PASS" and result["same_source_twice_in_a_group"] == 0


def test_check_of_a_run_without_pairs_fails(run):
    alone = type(run)(obs=run.obs[run.obs["src"] != "v2x"].query("sensor == 'FrontLongRangeRadar'"), truth=run.truth, noise=run.noise)
    result = check_run(alone)
    assert result["result"] == "FAIL" and result["precision"] is None
