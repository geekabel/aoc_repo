"""Tests of the mass functions (task B4)."""
import itertools

import numpy as np
import pytest

from aoc.fusion import belief
from aoc.fusion.belief import CLASS, EXISTENCE, Frame


def random_masses(frame, n, rng, with_conflict=False):
    table = rng.dirichlet(np.ones(frame.n_sets), n)
    if not with_conflict:
        table[:, 0] = 0.0
        table /= table.sum(axis=1, keepdims=True)
    return table


# ----------------------------------------------------------------------------
# Frames and the numbering of the sets
# ----------------------------------------------------------------------------
def test_sets_of_the_existence_frame():
    assert EXISTENCE.n_sets == 4 and EXISTENCE.omega == 3
    assert [EXISTENCE.label(a) for a in range(4)] == ["empty", "E", "notE", "E|notE"]
    assert EXISTENCE.index("E") == 1 and EXISTENCE.index("notE") == 2
    assert EXISTENCE.index(["E", "notE"]) == 3 and EXISTENCE.index([]) == 0
    assert EXISTENCE.subset(3) == frozenset({"E", "notE"}) and EXISTENCE.subset(0) == frozenset()


def test_sets_of_the_class_frame():
    assert CLASS.elements == ("car", "truck", "bicycle", "pedestrian")
    assert CLASS.n_sets == 16 and CLASS.omega == 15
    assert CLASS.index(("car", "truck")) == 3 and CLASS.label(3) == "car|truck"
    assert CLASS.index("pedestrian") == 8
    assert CLASS.sizes.tolist() == [bin(a).count("1") for a in range(16)]
    for number in range(16):
        assert CLASS.index(CLASS.subset(number)) == number


def test_bad_frames_and_sets_are_refused():
    with pytest.raises(ValueError):
        Frame("twice", ("a", "a"))
    with pytest.raises(ValueError):
        Frame("none", ())
    with pytest.raises(ValueError, match="not an answer"):
        EXISTENCE.index("maybe")
    with pytest.raises(ValueError):
        EXISTENCE.subset(4)
    with pytest.raises(ValueError, match="4 columns"):
        belief.belief(EXISTENCE, np.ones((2, 3)) / 3, "E")


# ----------------------------------------------------------------------------
# Building mass functions
# ----------------------------------------------------------------------------
def test_vacuous_mass_function_knows_nothing():
    table = belief.vacuous(CLASS, 3)
    assert table.shape == (3, 16) and (table[:, 15] == 1.0).all() and table.sum() == 3.0
    assert belief.belief(CLASS, table, "car").tolist() == [0.0] * 3
    assert belief.plausibility(CLASS, table, "car").tolist() == [1.0] * 3
    assert np.allclose(belief.pignistic(CLASS, table), 0.25)


def test_simple_support():
    table = belief.simple_support(EXISTENCE, "E", [0.0, 0.9, 1.0])
    assert np.allclose(table, [[0, 0, 0, 1], [0, 0.9, 0, 0.1], [0, 1, 0, 0]])
    assert belief.is_valid(EXISTENCE, table).all()
    on_two = belief.simple_support(CLASS, ("car", "truck"), 0.6)
    assert on_two.shape == (1, 16) and on_two[0, 3] == pytest.approx(0.6) and on_two[0, 15] == pytest.approx(0.4)
    assert np.allclose(belief.simple_support(EXISTENCE, ("E", "notE"), 0.3), [[0, 0, 0, 1]])      # support for the whole frame says nothing
    for bad in ([1.2], [-0.1], [np.nan]):
        with pytest.raises(ValueError, match="between 0 and 1"):
            belief.simple_support(EXISTENCE, "E", bad)
    with pytest.raises(ValueError, match="not empty"):
        belief.simple_support(EXISTENCE, [], 0.5)


def test_valid_and_invalid_rows():
    table = np.array([[0, 0.9, 0, 0.1], [0, 0.9, 0, 0.2], [0, 1.1, 0, -0.1], [0.2, 0.3, 0.5, 0.0], [0, np.nan, 0, 1.0]])
    assert belief.is_valid(EXISTENCE, table).tolist() == [True, False, False, True, False]


# ----------------------------------------------------------------------------
# Belief, plausibility, pignistic probability
# ----------------------------------------------------------------------------
def test_readings_of_a_hand_made_example():
    # m(car) = 0.5, m(car or truck) = 0.3, m(anything) = 0.2
    m = np.zeros((1, 16))
    m[0, CLASS.index("car")], m[0, CLASS.index(("car", "truck"))], m[0, CLASS.omega] = 0.5, 0.3, 0.2
    assert belief.belief(CLASS, m, "car")[0] == pytest.approx(0.5)
    assert belief.plausibility(CLASS, m, "car")[0] == pytest.approx(1.0)
    assert belief.belief(CLASS, m, "truck")[0] == pytest.approx(0.0)
    assert belief.plausibility(CLASS, m, "truck")[0] == pytest.approx(0.5)
    assert belief.belief(CLASS, m, ("car", "truck"))[0] == pytest.approx(0.8)
    assert belief.plausibility(CLASS, m, "pedestrian")[0] == pytest.approx(0.2)
    assert belief.pignistic(CLASS, m)[0] == pytest.approx([0.5 + 0.15 + 0.05, 0.15 + 0.05, 0.05, 0.05])


def test_existence_readings():
    m = belief.simple_support(EXISTENCE, "E", [0.9])
    assert belief.belief(EXISTENCE, m, "E")[0] == pytest.approx(0.9)
    assert belief.plausibility(EXISTENCE, m, "E")[0] == pytest.approx(1.0)
    assert belief.belief(EXISTENCE, m, "notE")[0] == pytest.approx(0.0)
    assert belief.plausibility(EXISTENCE, m, "notE")[0] == pytest.approx(0.1)
    assert belief.pignistic(EXISTENCE, m)[0] == pytest.approx([0.95, 0.05])


@pytest.mark.parametrize("frame", [EXISTENCE, CLASS, Frame("three", ("a", "b", "c"))])
def test_readings_against_the_definitions(frame):
    rng = np.random.default_rng(0)
    table = random_masses(frame, 40, rng, with_conflict=True)
    sets = [frozenset(c) for r in range(len(frame.elements) + 1) for c in itertools.combinations(frame.elements, r)]
    for target in sets:
        want_bel = sum(table[:, frame.index(b)] for b in sets if b and b <= target)
        want_pl = sum(table[:, frame.index(b)] for b in sets if b & target)
        assert np.allclose(belief.belief(frame, table, target), want_bel if target else 0.0)
        assert np.allclose(belief.plausibility(frame, table, target), want_pl if target else 0.0)
    want_bet = np.zeros((40, len(frame.elements)))
    for b in sets:
        for element in b:
            want_bet[:, frame.elements.index(element)] += table[:, frame.index(b)] / len(b)
    want_bet /= (1.0 - table[:, 0])[:, None]
    assert np.allclose(belief.pignistic(frame, table), want_bet)
    assert np.allclose(belief.conflict(frame, table), table[:, 0])


@pytest.mark.parametrize("frame", [EXISTENCE, CLASS])
def test_laws_that_every_mass_function_obeys(frame):
    rng = np.random.default_rng(1)
    table = random_masses(frame, 200, rng)
    bet = belief.pignistic(frame, table)
    assert np.allclose(bet.sum(axis=1), 1.0)
    for position, element in enumerate(frame.elements):
        others = [e for e in frame.elements if e != element]
        bel, pl = belief.belief(frame, table, element), belief.plausibility(frame, table, element)
        assert (bel <= bet[:, position] + 1e-12).all() and (bet[:, position] <= pl + 1e-12).all()
        assert np.allclose(pl, 1.0 - belief.belief(frame, table, others))          # plausible = not certainly something else
    assert np.allclose(belief.belief(frame, table, frame.elements), 1.0)
    assert np.allclose(belief.plausibility(frame, table, frame.elements), 1.0)


def test_pignistic_leaves_the_conflict_out():
    m = np.array([[0.5, 0.4, 0.0, 0.1], [1.0, 0.0, 0.0, 0.0]])
    bet = belief.pignistic(EXISTENCE, m)
    assert bet[0] == pytest.approx([0.9, 0.1])                 # (0.4 + 0.05) / 0.5 and 0.05 / 0.5
    assert np.isnan(bet[1]).all()                              # nothing but conflict: no probability can be given
    assert belief.conflict(EXISTENCE, m).tolist() == [0.5, 1.0]


def test_one_mass_function_can_be_given_as_one_row():
    assert belief.belief(EXISTENCE, [0, 0.7, 0.1, 0.2], "E")[0] == pytest.approx(0.7)
    assert belief.pignistic(EXISTENCE, [0, 0.7, 0.1, 0.2]).shape == (1, 2)


def test_readings_agree_with_the_library_dst_py_of_godwin():
    ds = pytest.importorskip("dempster_shafer.core.mass_function", reason="the library dst-py is not installed")
    rng = np.random.default_rng(2)
    for frame in (EXISTENCE, CLASS):
        table = random_masses(frame, 30, rng)
        sets = [frame.subset(a) for a in range(1, frame.n_sets)]
        for row in table:
            theirs = ds.MassFunction({tuple(sorted(s)): float(row[frame.index(s)]) for s in sets}, frozenset(frame.elements))
            for target in sets:
                assert belief.belief(frame, row, target)[0] == pytest.approx(theirs.belief(target))
                assert belief.plausibility(frame, row, target)[0] == pytest.approx(theirs.plausibility(target))
