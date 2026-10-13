"""Tests of the discount (task B5): the discounting operations, the reliability of each object, the three laws, the check of a run."""
import itertools

import numpy as np
import pandas as pd
import pytest

from aoc.analysis.calibration import CalibrationBins, brier_score, expected_calibration_error, fit_scalar
from aoc.analysis.motion import true_motion
from aoc.fusion import belief, discounting
from aoc.fusion.alignment import Q_BY_CLASS, align, predict
from aoc.fusion.bba import build_evidence
from aoc.fusion.belief import CLASS, EXISTENCE, Frame
from aoc.fusion.discounting import (
    TrustModel,
    age_reliability,
    alpha_of_age,
    check_run,
    declared_reliability,
    discount_evidence,
    position_spread,
    really_there,
    reliability,
)
from aoc.fusion.replay import Run, load_run
from aoc.theory import reliability as theory

TARGET = Frame("target", ("a", "h", "r"))          # airplane, helicopter, rocket: the frame of the examples of Mercier et al.


@pytest.fixture(scope="module")
def run(exported_runs):
    folder, _ = exported_runs["run_aoc_sensors"]
    return load_run(folder)


@pytest.fixture(scope="module")
def checked(run):
    """The check of the sample run with the default settings, computed once."""
    return check_run(run)


def random_masses(frame, n, rng, with_conflict=True):
    table = rng.random((n, frame.n_sets)) * (rng.random((n, frame.n_sets)) < 0.6)
    table[:, frame.omega] += 0.05
    if not with_conflict:
        table[:, 0] = 0.0
    return table / table.sum(axis=1, keepdims=True)


def masses(frame, **by_label):
    row = np.zeros(frame.n_sets)
    for label, value in by_label.items():
        row[frame.index(label.split("_"))] = value
    return row


def as_dict(frame, row, digits=10):
    return {frame.label(number): round(float(value), digits) for number, value in enumerate(row) if abs(value) > 1e-12}


def implicability(frame, table, subset):
    """b(A) = bel(A) + m(empty): the function that the disjunctive rule multiplies."""
    return belief.belief(frame, table, subset) + belief.conflict(frame, table)


def objects(**columns):
    """A table of objects like ``aligned.objects``: received cars with the noise of the replay, unless said otherwise."""
    n = len(next(iter(columns.values())))
    base = {"src": ["v2x"] * n, "class": ["passenger_car"] * n, "sensor": [None] * n, "meas_src": ["radar"] * n, "age_now": [0.0] * n,
            "P11": [0.09] * n, "P12": [0.0] * n, "P22": [0.09] * n, "Pv11": [0.25] * n, "Pv22": [0.25] * n,
            "x": [0.0] * n, "y": [0.0] * n, "vx": [10.0] * n, "vy": [0.0] * n}
    base.update(columns)
    text = ("src", "class", "sensor", "meas_src")
    return pd.DataFrame({name: pd.Series(values, dtype=object if name in text else float) for name, values in base.items()})


# ----------------------------------------------------------------------------
# Classical discounting and the disjunctive rule
# ----------------------------------------------------------------------------
def test_classical_discounting():
    m = masses(TARGET, a=0.5, r=0.5)
    out = belief.discount(TARGET, m, 0.6)[0]                                  # Example 1 of Mercier et al. 2008: discount rate 0.4
    assert as_dict(TARGET, out) == {"a": 0.3, "r": 0.3, "a|h|r": 0.4}
    assert np.allclose(belief.discount(TARGET, m, 1.0)[0], m)                 # fully reliable: nothing changes
    assert np.allclose(belief.discount(TARGET, m, 0.0), belief.vacuous(TARGET, 1))      # not reliable: "I do not know"


def test_classical_discounting_row_by_row():
    row = masses(CLASS, car=0.5, car_truck=0.2, car_truck_bicycle_pedestrian=0.2)
    row[0] = 0.1                                                              # some conflict
    out = belief.discount(CLASS, np.tile(row, (3, 1)), [1.0, 0.5, 0.0])
    assert as_dict(CLASS, out[0]) == {"empty": 0.1, "car": 0.5, "car|truck": 0.2, "car|truck|bicycle|pedestrian": 0.2}
    assert as_dict(CLASS, out[1]) == {"empty": 0.05, "car": 0.25, "car|truck": 0.1, "car|truck|bicycle|pedestrian": 0.6}      # the conflict is lowered too
    assert as_dict(CLASS, out[2]) == {"car|truck|bicycle|pedestrian": 1.0}
    rng = np.random.default_rng(3)
    table = random_masses(CLASS, 50, rng)
    r = rng.random(50)
    out = belief.discount(CLASS, table, r)
    assert belief.is_valid(CLASS, out).all()
    for subset in ("car", ("car", "truck"), ("truck", "bicycle", "pedestrian")):
        assert (belief.belief(CLASS, out, subset) <= belief.belief(CLASS, table, subset) + 1e-12).all()           # less certain
        assert (belief.plausibility(CLASS, out, subset) >= belief.plausibility(CLASS, table, subset) - 1e-12).all()   # more possible


def test_discounting_refuses_bad_reliabilities():
    m = masses(EXISTENCE, E=0.9, E_notE=0.1)
    for bad in (1.2, -0.1, np.nan):
        with pytest.raises(ValueError, match="between 0 and 1"):
            belief.discount(EXISTENCE, m, bad)
    with pytest.raises(ValueError, match="one per mass function"):
        belief.discount(EXISTENCE, m, [0.5, 0.5])


def test_disjunctive_rule():
    rng = np.random.default_rng(4)
    for frame in (EXISTENCE, TARGET, CLASS):
        a, b = random_masses(frame, 30, rng), random_masses(frame, 30, rng)
        out = belief.disjunctive(frame, a, b)
        assert belief.is_valid(frame, out).all()
        assert np.allclose(out, belief.disjunctive(frame, b, a))                                  # the order does not matter
        for number in range(frame.n_sets):
            subset = frame.subset(number)
            assert np.allclose(implicability(frame, out, subset), implicability(frame, a, subset) * implicability(frame, b, subset))
        nothing_known = belief.vacuous(frame, 30)
        contradiction = np.zeros((1, frame.n_sets))
        contradiction[0, 0] = 1.0
        assert np.allclose(belief.disjunctive(frame, a, nothing_known), nothing_known)            # "one of the two is right" and one says nothing
        assert np.allclose(belief.disjunctive(frame, a, contradiction), a)                        # a single row is used for every row
    with pytest.raises(ValueError, match="same number of rows"):
        belief.disjunctive(EXISTENCE, np.tile(masses(EXISTENCE, E=1.0), (2, 1)), np.tile(masses(EXISTENCE, E=1.0), (3, 1)))


def test_discounting_is_the_disjunctive_rule_with_a_simple_mass_function():
    rng = np.random.default_rng(5)
    table = random_masses(TARGET, 20, rng)
    kernel = np.zeros(TARGET.n_sets)
    kernel[0], kernel[TARGET.omega] = 0.7, 0.3                               # Remark 2 of Mercier et al. 2008
    assert np.allclose(belief.discount(TARGET, table, 0.7), belief.disjunctive(TARGET, table, kernel))


# ----------------------------------------------------------------------------
# Contextual discounting
# ----------------------------------------------------------------------------
def test_contextual_discounting_examples_of_the_paper():
    """Examples 2 and 3 of Mercier, Quost and Denoeux (2008). The paper gives discount rates: reliability = 1 - rate."""
    m = masses(TARGET, a=0.5, r=0.5)
    singles = ["a", "h", "r"]
    case1 = belief.contextual_discount(TARGET, m, singles, [0.6, 1.0, 1.0])[0]
    assert as_dict(TARGET, case1) == {"a": 0.5, "r": 0.3, "a|r": 0.2}
    case2 = belief.contextual_discount(TARGET, m, singles, [1.0, 0.4, 1.0])[0]
    assert as_dict(TARGET, case2) == {"a": 0.2, "a|h": 0.3, "r": 0.2, "h|r": 0.3}
    case3 = belief.contextual_discount(TARGET, m, singles, [0.6, 0.4, 1.0])[0]
    assert as_dict(TARGET, case3) == {"a": 0.2, "a|h": 0.3, "r": 0.12, "a|r": 0.08, "h|r": 0.18, "a|h|r": 0.12}
    assert np.allclose(case3, belief.contextual_discount(TARGET, case1, singles, [1.0, 0.4, 1.0])[0])       # one context after the other
    coarse = belief.contextual_discount(TARGET, m, ["a", ("h", "r")], [0.4, 0.9])[0]                         # Example 3
    assert as_dict(TARGET, coarse) == {"a": 0.45, "r": 0.18, "a|r": 0.27, "h|r": 0.02, "a|h|r": 0.08}


@pytest.mark.parametrize("frame, partition", [
    (EXISTENCE, ["E", "notE"]),
    (TARGET, ["a", "h", "r"]),
    (TARGET, ["a", ("h", "r")]),
    (CLASS, [("car", "truck"), ("bicycle", "pedestrian")]),
    (CLASS, ["car", "truck", "bicycle", "pedestrian"]),
])
def test_contextual_discounting_in_closed_form(frame, partition):
    """Proposition 10 of the paper: b'(A) = b(A) times the reliabilities of the contexts that are not inside A."""
    rng = np.random.default_rng(6)
    table = random_masses(frame, 25, rng)
    r = rng.random((25, len(partition)))
    out = belief.contextual_discount(frame, table, partition, r)
    assert belief.is_valid(frame, out).all()
    parts = [frame.subset(frame.index(p)) for p in partition]
    for number in range(frame.n_sets):
        subset = frame.subset(number)
        factor = np.ones(25)
        for position, part in enumerate(parts):
            if not part <= subset:
                factor = factor * r[:, position]
        assert np.allclose(implicability(frame, out, subset), implicability(frame, table, subset) * factor)
    shuffled = list(reversed(range(len(partition))))
    assert np.allclose(out, belief.contextual_discount(frame, table, [partition[i] for i in shuffled], r[:, shuffled]))


def test_one_context_for_the_whole_frame_is_classical_discounting():
    rng = np.random.default_rng(7)
    for frame in (EXISTENCE, TARGET, CLASS):
        table = random_masses(frame, 30, rng)
        r = rng.random(30)
        assert np.allclose(belief.contextual_discount(frame, table, [frame.elements], r[:, None]), belief.discount(frame, table, r))


def product_space_discount(frame, row, contexts, reliabilities):
    """Contextual discounting from its definition, with plain Python sets (Equation 2 of the corrigendum of 2016).

    Every piece of knowledge is put on the product space (answer, state of the source), the pieces are combined
    with the conjunctive rule, and the result is projected on the answers.
    """
    answers = frame.elements
    everything = frozenset((w, state) for w in answers for state in ("R", "NR"))
    combined = {}
    for number in range(frame.n_sets):                      # "if the source is reliable, what it says is true"
        if row[number]:
            said = frame.subset(number)
            combined[frozenset({(w, "R") for w in said} | {(w, "NR") for w in answers})] = float(row[number])
    for context, r in zip(contexts, reliabilities):         # "if the truth is in this context, the source is reliable with belief r"
        context = frame.subset(frame.index(context))
        piece = {frozenset({(w, "R") for w in context} | {(w, s) for w in answers if w not in context for s in ("R", "NR")}): r, everything: 1.0 - r}
        merged = {}
        for (left, a), (right, b) in itertools.product(combined.items(), piece.items()):
            merged[left & right] = merged.get(left & right, 0.0) + a * b
        combined = merged
    out = np.zeros(frame.n_sets)
    for focal, value in combined.items():
        out[frame.index({w for w, _ in focal})] += value
    return out


@pytest.mark.parametrize("frame, contexts", [
    (EXISTENCE, ["E", "notE"]),
    (EXISTENCE, ["notE"]),
    (EXISTENCE, ["E", ("E", "notE")]),
    (TARGET, ["a", ("h", "r")]),
    (TARGET, [("a", "h"), ("h", "r")]),                     # contexts that overlap: not a partition
    (TARGET, ["a", ("a", "h"), ("a", "h", "r")]),
    (CLASS, [("car", "truck"), ("truck", "bicycle"), "pedestrian"]),
    (Frame("one", ("only",)), ["only"]),
    (Frame("six", tuple("abcdef")), ["a", ("b", "c"), ("c", "d", "e")]),
    (Frame("eight", tuple("abcdefgh")), [("a", "b"), ("b", "c", "d"), "h", tuple("abcdefgh")]),
])
def test_contextual_discounting_against_its_definition(frame, contexts):
    rng = np.random.default_rng(8)
    table = random_masses(frame, 6, rng)
    r = rng.random((6, len(contexts)))
    out = belief.contextual_discount(frame, table, contexts, r)
    assert belief.is_valid(frame, out).all()
    for row in range(6):
        assert np.allclose(out[row], product_space_discount(frame, table[row], contexts, r[row]))


def test_kernel_of_the_corrigendum_example():
    """Example 1 of the corrigendum of 2016: two answers, contexts {w1}, {w2} and the whole frame."""
    two = Frame("two", ("w1", "w2"))
    b1, b2, b12 = 0.7, 0.6, 0.9
    a1, a2, a12 = 1 - b1, 1 - b2, 1 - b12
    kernel = belief.discount_kernel(two, ["w1", "w2", ("w1", "w2")], [b1, b2, b12])[0]
    assert kernel == pytest.approx([b1 * b2 * a12 + b12, a1 * b2 * a12, b1 * a2 * a12, a1 * a2 * a12])
    wrong = [b1 * b2 * b12, a1 * b2 * b12, b1 * a2 * b12, a1 * a2 * b12 + a12]              # the result of the article of 2012, corrected in 2016
    assert not np.allclose(kernel, wrong)


def test_contextual_discounting_refuses_bad_contexts():
    m = masses(TARGET, a=1.0)
    with pytest.raises(ValueError, match="contexts"):
        belief.contextual_discount(TARGET, m, [], [])
    with pytest.raises(ValueError, match="contexts"):
        belief.contextual_discount(TARGET, m, ["a", "a"], [0.5, 0.5])
    with pytest.raises(ValueError, match="contexts"):
        belief.contextual_discount(TARGET, m, [()], [0.5])
    with pytest.raises(ValueError, match="one reliability per context"):
        belief.contextual_discount(TARGET, m, ["a", "h"], [0.5])
    with pytest.raises(ValueError, match="between 0 and 1"):
        belief.contextual_discount(TARGET, m, ["a"], [1.5])
    with pytest.raises(ValueError, match="not an answer"):
        belief.contextual_discount(TARGET, m, ["z"], [0.5])
    with pytest.raises(ValueError, match="sets of answers"):
        belief.contextual_discount(TARGET, m, [3], [0.5])
    with pytest.raises(ValueError, match="at most 12"):
        belief.contextual_discount(Frame("eight", tuple("abcdefgh")), np.eye(256)[255], [tuple("abcdefgh"[:k]) for k in range(1, 9)] + list("bcdef"), 0.5)


def test_how_the_reliabilities_of_the_contexts_are_read():
    two = np.tile(masses(EXISTENCE, E=0.8, E_notE=0.2), (2, 1))
    per_context = belief.contextual_discount(EXISTENCE, two, ["E", "notE"], [1.0, 0.5])            # a list: one number per context
    assert np.allclose(belief.belief(EXISTENCE, per_context, "E"), [0.4, 0.4])
    per_row = belief.contextual_discount(EXISTENCE, two, ["E", "notE"], [[1.0, 1.0], [1.0, 0.5]])   # a table: one row per mass function
    assert np.allclose(belief.belief(EXISTENCE, per_row, "E"), [0.8, 0.4])
    same_for_all = belief.contextual_discount(EXISTENCE, two, ["E", "notE"], 0.5)                   # one number: every context
    assert np.allclose(same_for_all, belief.contextual_discount(EXISTENCE, two, ["E", "notE"], [0.5, 0.5]))
    singletons = belief.contextual_discount(TARGET, masses(TARGET, a=1.0), TARGET.elements, 0.5)     # the answers one by one, not the whole frame
    assert not np.allclose(singletons, belief.discount(TARGET, masses(TARGET, a=1.0), 0.5))


def test_for_the_existence_evidence_contextual_and_classical_discounting_are_the_same():
    """The evidence of B4 supports E only. Then only the reliability in the context "not there" counts."""
    rng = np.random.default_rng(9)
    score, r_there, r_not_there = rng.random(40), rng.random(40), rng.random(40)
    evidence = belief.simple_support(EXISTENCE, "E", score)
    contextual = belief.contextual_discount(EXISTENCE, evidence, ["E", "notE"], np.column_stack([r_there, r_not_there]))
    assert np.allclose(contextual, belief.discount(EXISTENCE, evidence, r_not_there))
    assert np.allclose(belief.belief(EXISTENCE, contextual, "E"), score * r_not_there)
    against = belief.simple_support(EXISTENCE, "notE", score)                 # evidence against an object would need the other reliability
    contextual = belief.contextual_discount(EXISTENCE, against, ["E", "notE"], np.column_stack([r_there, r_not_there]))
    assert np.allclose(belief.belief(EXISTENCE, contextual, "notE"), score * r_there)


def test_operations_agree_with_the_library_dst_py_of_godwin():
    pytest.importorskip("dempster_shafer", reason="the library dst-py is not installed")
    import dempster_shafer as ds
    from dempster_shafer.core.mass_function import MassFunction
    rng = np.random.default_rng(10)
    for frame in (EXISTENCE, TARGET):
        sets = [frame.subset(a) for a in range(1, frame.n_sets)]
        to_theirs = lambda row: MassFunction({tuple(sorted(s)): float(row[frame.index(s)]) for s in sets if row[frame.index(s)] > 0}, frozenset(frame.elements))
        to_row = lambda theirs: np.array([0.0] + [float(theirs[frozenset(s)]) if frozenset(s) in theirs else 0.0 for s in sets])
        table, other = random_masses(frame, 15, rng, with_conflict=False), random_masses(frame, 15, rng, with_conflict=False)
        for row, second, r in zip(table, other, rng.random(15)):
            assert np.allclose(belief.discount(frame, row, r)[0], to_row(ds.discount_classical(to_theirs(row), float(r))))
            assert np.allclose(belief.disjunctive(frame, row, second)[0], to_row(ds.combine_disjunctive(to_theirs(row), to_theirs(second))))


# ----------------------------------------------------------------------------
# The reliability of each object
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("label, name", [("passenger_car", "car (moving)"), ("heavy_truck", "truck (moving)"),
                                         ("cyclist", "bicycle"), ("pedestrian", "pedestrian")])
def test_reliability_is_the_curve_of_the_theory_note(label, name):
    sx, sv, sxv, q, *_ = theory.CLASSES[name]
    ages = np.array([0.0, 0.13, 0.5, 1.0, 2.0, 5.0])
    table = objects(age_now=ages, **{"class": [label] * len(ages)}, P11=[sx**2] * len(ages), P22=[sx**2] * len(ages),
                    Pv11=[sv**2] * len(ages), Pv22=[sv**2] * len(ages))
    alpha = age_reliability(table)
    assert np.allclose(alpha, theory.alpha(ages, sx, sv, sxv, q))
    assert alpha[0] > 0.99999 and (np.diff(alpha) < 0).all()                  # fresh: full trust. Then it only falls
    assert np.allclose(reliability(table), alpha)                             # no declared confidence: nothing else


def test_reliability_uses_the_largest_variance_of_the_predicted_place():
    table = objects(age_now=[0.0, 0.8, 0.8, 2.0], P11=[0.09, 0.09, 0.5, 0.2], P12=[0.0, 0.0, 0.1, -0.15], P22=[0.09, 0.09, 0.2, 0.3],
                    Pv11=[0.25, 0.25, 0.1, 0.3], Pv22=[0.25, 0.25, 0.4, 0.3])
    q = np.full(4, Q_BY_CLASS["passenger_car"])
    _, cov = predict(table, table["age_now"].to_numpy(), q)                   # the covariance that the alignment gives
    largest = np.linalg.eigvalsh(cov[:, :2, :2])[:, -1]
    assert np.allclose(position_spread(table) + q * table["age_now"].to_numpy() ** 3 / 3, largest)
    assert np.allclose(age_reliability(table), 1 - np.exp(-theory.EPS**2 / (2 * largest)))
    mean_variance = (cov[:, 0, 0] + cov[:, 1, 1]) / 2
    assert (age_reliability(table) <= 1 - np.exp(-theory.EPS**2 / (2 * mean_variance)) + 1e-12).all()       # the careful side


def test_reliability_parameters():
    table = objects(age_now=[0.8, 0.8, 0.8], **{"class": ["passenger_car", "unknown_thing", "pedestrian"]})
    car, unknown, pedestrian = reliability(table)
    assert car == pytest.approx(1 - np.exp(-1.5**2 / (2 * (0.09 + 0.64 * 0.25 + 4.0 * 0.512 / 3))))       # the curve, by hand
    assert unknown == pytest.approx(car) and pedestrian > car + 0.1            # a class without a value gets the largest one (the car)
    assert reliability(table, TrustModel(q_by_class={"passenger_car": 1.0}))[0] > car + 0.1                # less motion: trusted longer
    assert reliability(table, TrustModel(q_by_class={}, q_default=0.5))[1] == pytest.approx(pedestrian)     # 0.5 is the value of a pedestrian
    assert reliability(table, TrustModel(epsilon=3.0))[0] > car + 0.1                                      # more tolerance: trusted longer
    assert alpha_of_age(0.0, 0.0, 4.0) == 1.0                                  # no uncertainty at all
    for bad in (dict(law="other"), dict(epsilon=0.0), dict(epsilon=np.inf), dict(epsilon=None), dict(q_default=-1.0), dict(q_by_class={"x": "fast"}),
                dict(sensor_default=1.5), dict(cutoff_age=-1.0), dict(cutoff_age=np.nan)):
        with pytest.raises(ValueError):
            TrustModel(**bad)
    with pytest.raises(ValueError, match="covariance is missing"):
        age_reliability(objects(age_now=[0.5], P11=[np.nan]))
    with pytest.raises(ValueError, match="P12 is too large"):
        age_reliability(objects(age_now=[0.5], P12=[0.2]))
    for law in ("aoc", "cutoff"):
        for bad_age in (-0.5, np.nan):
            with pytest.raises(ValueError, match="age is missing or negative"):
                reliability(objects(age_now=[bad_age]), TrustModel(law=law))
    for column in ("age_now", "P12", "src", "class"):
        with pytest.raises(ValueError, match="no column %s" % column):
            reliability(objects(age_now=[0.5]).drop(columns=column))
    for bad in (dict(spread=-1.0, age=1.0, q=1.0), dict(spread=1.0, age=1.0, q=-1.0), dict(spread=np.nan, age=1.0, q=1.0)):
        with pytest.raises(ValueError, match="not negative"):
            alpha_of_age(**bad)


def test_sender_confidence_and_sensor_reliability():
    table = objects(src=["v2x", "v2x", "radar", "radar"], sensor=[None, None, "FrontRadar", "RearRadar"], age_now=[0.5, 0.5, 0.0, 0.0],
                    sender_conf=[0.8, np.nan, 0.2, np.nan])
    curve = age_reliability(table)
    assert np.allclose(reliability(table), curve * [0.8, 1.0, 1.0, 1.0])       # an empty confidence counts as 1; a sensor row has none
    assert np.allclose(reliability(table, TrustModel(use_sender_confidence=False)), curve)
    model = TrustModel(sensor_reliability={"FrontRadar": 0.9}, sensor_default=0.7)
    assert np.allclose(reliability(table, model), curve * [0.8, 1.0, 0.9, 0.7])
    assert np.allclose(declared_reliability(table, model), [0.8, 1.0, 0.9, 0.7])
    for bad in (1.4, -0.1, np.inf):
        with pytest.raises(ValueError, match="outside 0 to 1"):
            reliability(objects(sender_conf=[bad]))


def test_the_three_laws():
    ages = [0.2, 1.0, 1.0 + 1e-6, 1.1, 1.5]
    table = objects(src=["v2x", "v2x", "v2x", "v2x", "radar"], sensor=[None, None, None, None, "FrontRadar"], age_now=ages, sender_conf=[0.5] * 5)
    assert reliability(table, TrustModel(law="full_trust")).tolist() == [1.0, 1.0, 1.0, 1.0, 1.0]
    assert reliability(table, TrustModel(law="cutoff")).tolist() == [1.0, 1.0, 0.0, 0.0, 1.0]  # received and older than 1 s: dropped. Exactly 1 s: kept
    assert reliability(table, TrustModel(law="cutoff", cutoff_age=0.1)).tolist() == [0.0, 0.0, 0.0, 0.0, 1.0]
    aoc = reliability(table)
    by_hand = 1 - np.exp(-1.5**2 / (2 * (0.09 + 0.04 * 0.25 + 4.0 * 0.008 / 3)))
    assert aoc[0] == pytest.approx(0.5 * by_hand) and (np.diff(aoc[:4]) < 0).all()
    assert aoc[4] == pytest.approx(theory.alpha(1.5, 0.3, 0.5, 0.0, 4.0))                      # a sensor row has no sender confidence


def test_discount_of_the_evidence():
    table = objects(age_now=[0.1, 0.8, 3.0, 30.0], **{"class": ["passenger_car", "pedestrian", "passenger_car", "passenger_car"]})
    evidence = build_evidence(table)
    trusted = discount_evidence(evidence, table)
    alpha = np.array([theory.alpha(age, 0.3, 0.5, 0.0, q) for age, q in zip([0.1, 0.8, 3.0, 30.0], [4.0, 0.5, 4.0, 4.0])])
    assert len(trusted) == 4 and np.allclose(trusted.reliability, alpha)
    assert np.allclose(belief.belief(EXISTENCE, trusted.existence, "E"), 0.99 * alpha)
    assert np.allclose(belief.plausibility(EXISTENCE, trusted.existence, "E"), 1.0)            # no evidence against the object, at any age
    assert (trusted.existence[:, EXISTENCE.index("notE")] == 0.0).all() and belief.is_valid(EXISTENCE, trusted.existence).all()
    assert trusted.existence[3] == pytest.approx([0.0, 0.0, 0.0, 1.0], abs=1e-3)               # very old: "I do not know"
    assert np.array_equal(trusted.classes, evidence.classes) and trusted.classes is not evidence.classes      # the class is not lowered
    full = discount_evidence(evidence, table, TrustModel(law="full_trust"))
    assert np.array_equal(full.existence, evidence.existence)
    cut = discount_evidence(evidence, table, TrustModel(law="cutoff"))
    assert belief.belief(EXISTENCE, cut.existence, "E").tolist() == pytest.approx([0.99, 0.99, 0.0, 0.0])
    with pytest.raises(ValueError, match="holds 4 objects"):
        discount_evidence(evidence, table.iloc[:2])


def test_discount_on_a_real_tick(run):
    tick = max(run.replay(run.egos[0]), key=lambda t: len(t.v2x))
    aligned = align(tick)
    evidence = build_evidence(aligned.objects)
    trusted = discount_evidence(evidence, aligned.objects)
    assert len(trusted) == len(aligned) > 0
    largest = np.linalg.eigvalsh(aligned.cov[:, :2, :2])[:, -1]                # same motion values as the alignment by default
    assert np.allclose(trusted.reliability, 1 - np.exp(-theory.EPS**2 / (2 * largest)))
    assert np.allclose(belief.belief(EXISTENCE, trusted.existence, "E"), evidence.exist_score * trusted.reliability)


# ----------------------------------------------------------------------------
# Calibration error
# ----------------------------------------------------------------------------
def test_calibration_error():
    confidence = np.array([0.95] * 10 + [0.05] * 10)
    assert expected_calibration_error(confidence, [1] * 10 + [0] * 10) == pytest.approx(0.05)
    assert expected_calibration_error(confidence, [1] * 5 + [0] * 5 + [0] * 10) == pytest.approx(0.5 * 0.45 + 0.5 * 0.05)
    assert expected_calibration_error([1.0, 1.0, 0.0], [1, 1, 0]) == 0.0      # a confidence of 1 is in the last bin
    assert np.isnan(expected_calibration_error([], []))
    rng = np.random.default_rng(11)
    p = rng.random(200_000)
    assert expected_calibration_error(p, rng.random(200_000) < p) < 0.005     # a true probability is calibrated
    assert expected_calibration_error(p, rng.random(200_000) < p**2) > 0.1    # an over-confident one is not
    with pytest.raises(ValueError, match="between 0 and 1"):
        expected_calibration_error([1.2], [1])
    with pytest.raises(ValueError, match="one hit"):
        expected_calibration_error([0.2, 0.3], [1])
    for bad_hit in (np.nan, 0.5, 2):
        with pytest.raises(ValueError, match="True or False"):
            expected_calibration_error([0.2], [bad_hit])
    for bad_bins in (0, 2.5):
        with pytest.raises(ValueError, match="number of bins"):
            CalibrationBins(bad_bins)


def test_brier_score():
    assert brier_score([1.0, 0.0, 0.5, 0.5], [1, 0, 1, 0]) == pytest.approx(0.125)
    assert brier_score([0.9, 0.9], [1, 0]) == pytest.approx((0.01 + 0.81) / 2)
    assert np.isnan(brier_score([], []))
    same_for_all = np.full(1000, 0.99)                          # every object gets 0.99 and 99% are there
    hit = np.arange(1000) >= 10
    told_apart = np.where(hit, 0.99, 0.01)                      # the missing ones get a low confidence
    assert expected_calibration_error(same_for_all, hit) == pytest.approx(0.0) and expected_calibration_error(told_apart, hit) == pytest.approx(0.01)
    assert brier_score(told_apart, hit) < 0.02 * brier_score(same_for_all, hit)        # only the Brier score sees the difference


def test_calibration_bins_piece_by_piece():
    rng = np.random.default_rng(12)
    p = rng.random(5000)
    hit = rng.random(5000) < p
    bins = CalibrationBins()
    for part in np.array_split(np.arange(5000), 7):
        bins.add(p[part], hit[part])
    assert bins.total == 5000 and bins.error() == pytest.approx(expected_calibration_error(p, hit))
    assert bins.brier() == pytest.approx(np.mean((p - hit) ** 2))
    table = bins.table()
    assert len(table) == 10 and sum(line["count"] for line in table) == 5000
    for number, line in enumerate(table):
        inside = (p >= number / 10) & (p < (number + 1) / 10)
        assert line["count"] == inside.sum() and line["confidence"] == pytest.approx(p[inside].mean()) and line["frequency"] == pytest.approx(hit[inside].mean())
    by_hand = sum(line["count"] * abs(line["confidence"] - line["frequency"]) for line in table) / 5000
    assert bins.error() == pytest.approx(by_hand)


def test_fit_of_the_motion_value_finds_the_true_one():
    rng = np.random.default_rng(13)
    n, q_true, sx, sv = 60_000, 1.5, 0.3, 0.5
    age = rng.uniform(0.0, 4.0, n)
    variance = sx**2 + age**2 * sv**2 + q_true * age**3 / 3
    error = rng.standard_normal((n, 2)) * np.sqrt(variance)[:, None]           # a Gaussian error, as the curve assumes
    hit = np.hypot(error[:, 0], error[:, 1]) <= theory.EPS
    spread = sx**2 + age**2 * sv**2
    brier = lambda q: float(np.mean((alpha_of_age(spread, age, q) - hit) ** 2))
    assert fit_scalar(brier, 1e-3, 100.0) == pytest.approx(q_true, rel=0.1)
    assert expected_calibration_error(alpha_of_age(spread, age, q_true), hit) < 0.01
    with pytest.raises(ValueError, match="limits"):
        fit_scalar(brier, 0.0, 1.0)


# ----------------------------------------------------------------------------
# The real motion of the objects
# ----------------------------------------------------------------------------
def simulated_truth(q, n_objects=300, steps=41, dt=0.1, seed=14):
    """Objects that move as the reliability curve assumes: constant velocity plus a white-noise acceleration of intensity q."""
    rng = np.random.default_rng(seed)
    lines = []
    for number in range(n_objects):
        state = np.array([0.0, 0.0, 10.0, 0.0])
        for step in range(steps):
            lines.append(dict(t=round(step * dt, 6), obj_id="o%d" % number, x=state[0], y=state[1], vx=state[2], vy=state[3], **{"class": "passenger_car"}))
            jolt = rng.multivariate_normal([0.0, 0.0], q * np.array([[dt**3 / 3, dt**2 / 2], [dt**2 / 2, dt]]), size=2)      # exact step, per axis
            state = state + np.array([state[2] * dt + jolt[0, 0], state[3] * dt + jolt[1, 0], jolt[0, 1], jolt[1, 1]])
    return pd.DataFrame(lines)


def test_true_motion_recovers_the_motion_value_of_a_simulated_run():
    table = true_motion(simulated_truth(q=2.0), horizons=(0.5, 1.0, 2.0))
    assert table["horizon"].tolist() == [0.5, 1.0, 2.0] and (table["n"] > 5000).all()
    assert np.allclose(table["q_effective"], 2.0, rtol=0.1)
    assert np.allclose(table["spread"], np.sqrt(2.0 * table["horizon"] ** 3 / 3), rtol=0.05)
    assert np.allclose(table["within"], table["within_if_gaussian"], atol=0.02)      # the motion is Gaussian here, as the curve assumes
    still = simulated_truth(q=0.0, n_objects=3)
    assert np.allclose(true_motion(still)["spread"], 0.0, atol=1e-9) and (true_motion(still)["within"] == 1.0).all()


def test_true_motion_by_hand():
    """Three objects seen at 0 s and 1 s. Their speed says 10 m/s along x. They end 0 m, 1 m and 4 m to the side."""
    lines = []
    for name, side in (("a", 0.0), ("b", 1.0), ("c", 4.0)):
        lines.append(dict(t=0.0, obj_id=name, x=0.0, y=0.0, vx=10.0, vy=0.0, **{"class": "passenger_car"}))
        lines.append(dict(t=1.0, obj_id=name, x=10.0, y=side, vx=10.0, vy=0.0, **{"class": "passenger_car"}))
    line = true_motion(pd.DataFrame(lines), horizons=(1.0,), epsilon=1.5).iloc[0]
    size = np.sqrt((0 + 1 + 16) / 6)                                           # mean of the squared error over 3 objects and 2 axes
    assert line["n"] == 3 and line["median_error"] == pytest.approx(1.0) and line["within"] == pytest.approx(2 / 3)
    assert line["spread"] == pytest.approx(size) and line["q_effective"] == pytest.approx(3 * size**2)
    assert line["within_if_gaussian"] == pytest.approx(1 - np.exp(-1.5**2 / (2 * size**2)))
    with pytest.raises(ValueError, match="empty time, place or speed"):
        true_motion(pd.DataFrame(lines).assign(x=np.nan))


def test_true_motion_edge_cases(run):
    table = true_motion(run.truth, horizons=(0.5, 1.0))
    assert (table["n"] > 0).all() and (table["within"] <= 1.0).all() and (table["spread"] > 0).all()
    assert true_motion(run.truth, classes=["no_such_class"])["n"].tolist() == [0, 0, 0]
    assert true_motion(run.truth, classes=["passenger_car"], horizons=(0.5, 1.0)).equals(table)          # the sample run holds cars only
    with pytest.raises(ValueError, match="multiple of the step"):
        true_motion(run.truth, horizons=(0.25,))
    holes = simulated_truth(q=1.0, n_objects=2, steps=21)
    holes = holes[~holes["t"].between(0.75, 1.25)]                               # a hole in the truth: no pair may cross it
    assert true_motion(holes, horizons=(0.5,))["n"].iloc[0] == 2 * (3 + 3)


# ----------------------------------------------------------------------------
# The check of a run and the command
# ----------------------------------------------------------------------------
def test_really_there():
    truth = pd.DataFrame({"obj_id": ["a", "b"], "x": [0.0, 10.0], "y": [0.0, 0.0]})
    reports = pd.DataFrame({"gt_match_id": pd.Series(["a", "a", "a", "b", "gone", None], dtype=object),
                            "x_t": [1.5, 0.0, 1.2, 10.0, 0.0, 0.0], "y_t": [0.0, 1.5001, 1.0, -1.4, 0.0, 0.0]})
    assert really_there(reports, truth, 1.5).tolist() == [True, False, False, True, False, False]      # the limit itself is inside
    assert really_there(reports, truth, 2.0).tolist() == [True, True, True, True, False, False]
    assert really_there(reports.iloc[:0], truth, 1.5).tolist() == []


def recount(run, q=4.0, hold=None, cutoff=1.0, score=0.99):
    """The numbers of ``check_run``, counted again object by object with plain formulas (the sample run is isotropic)."""
    lines = []
    for ego in run.egos:
        for tick in run.replay(ego, hold=hold):
            aligned = align(tick)
            place = {name: (x, y) for name, x, y in zip(tick.truth["obj_id"], tick.truth["x"], tick.truth["y"])}
            for row, report in enumerate(aligned.objects.to_dict("records")):
                assert report["P11"] == report["P22"] and report["P12"] == 0.0 and report["Pv11"] == report["Pv22"]
                age = report["age_now"]
                variance = report["P11"] + age**2 * report["Pv11"] + q * age**3 / 3
                declared = report["sender_conf"] if report["src"] == "v2x" and report["sender_conf"] == report["sender_conf"] else 1.0
                true_xy = place.get(report["gt_match_id"])
                there = true_xy is not None and np.hypot(aligned.state[row, 0] - true_xy[0], aligned.state[row, 1] - true_xy[1]) <= 1.5
                lines.append(dict(received=report["src"] == "v2x", age=age, there=there, gone=true_xy is None,
                                  alpha=(1 - np.exp(-1.5**2 / (2 * variance))) * declared))
    table = pd.DataFrame(lines)
    old = table["received"] & (table["age"] > cutoff + 1e-9)
    young = table["received"] & ~old
    beliefs = {"full_trust": np.full(len(table), score), "cutoff": np.where(old, 0.0, score), "aoc": score * table["alpha"].to_numpy()}
    hit = table["there"].to_numpy(dtype=float)

    def calibration_error(confidence):
        total = 0.0
        for number in range(10):
            inside = (np.minimum((confidence * 10).astype(int), 9) == number)
            if inside.any():
                total += inside.sum() * abs(confidence[inside].mean() - hit[inside].mean())
        return total / len(table)

    return dict(objects=len(table), received=int(table["received"].sum()), received_old=int(old.sum()), gone=int(table["gone"].sum()),
                there_young=hit[young].mean(), there_old=hit[old].mean() if old.any() else None,
                alpha_young=table["alpha"][young].mean(), alpha_old=table["alpha"][old].mean() if old.any() else None,
                calibration_error={law: calibration_error(values) for law, values in beliefs.items()},
                brier={law: float(np.mean((values - hit) ** 2)) for law, values in beliefs.items()}, table=table, hit=hit)


def assert_same_counts(c, want):
    for name in ("objects", "received", "received_old", "gone"):
        assert c[name] == want[name], name
    for name in ("there_young", "there_old", "alpha_young", "alpha_old"):
        assert (c[name] is None and want[name] is None) or c[name] == pytest.approx(want[name], abs=6e-5), name
    for law in discounting.LAWS:
        assert c["calibration_error"][law] == pytest.approx(want["calibration_error"][law], abs=6e-5), law
        assert c["brier"][law] == pytest.approx(want["brier"][law], abs=6e-5), law


def test_check_of_a_run_against_a_recount(run, checked):
    c = checked
    want = recount(run)
    assert c["result"] == "PASS" and c["problems"] == [] and c["hold"] is None
    assert_same_counts(c, want)
    assert want["received_old"] > 100 and want["there_young"] > want["there_old"] + 0.5 and want["alpha_young"] > want["alpha_old"] + 0.5
    assert c["brier"]["aoc"] < c["brier"]["cutoff"] < c["brier"]["full_trust"]              # on this sample: the discount is the best of the three


def test_check_with_a_memory_limit_another_motion_value_and_a_declared_confidence(run, checked):
    limited = check_run(run, hold=2.0)
    assert_same_counts(limited, recount(run, hold=2.0))
    assert limited["hold"] == 2.0 and limited["objects"] < checked["objects"]
    assert_same_counts(check_run(run, TrustModel(q_by_class={"passenger_car": 1.0}, cutoff_age=0.5)), recount(run, q=1.0, cutoff=0.5))
    obs = run.obs.copy()
    obs["sender_conf"] = np.where(obs["src"] == "v2x", 0.5, np.nan)                          # every sender declares a confidence of 0.5
    halved = Run(obs=obs, truth=run.truth, noise=run.noise, info=run.info)
    c = check_run(halved)
    assert_same_counts(c, recount(halved))
    assert c["alpha_young"] == pytest.approx(0.5 * checked["alpha_young"], abs=1e-4)
    assert check_run(halved, TrustModel(use_sender_confidence=False))["alpha_young"] == checked["alpha_young"]
    fitted = check_run(halved, fit=True)                                                     # the fit keeps the declared confidence
    again = check_run(halved, TrustModel(q_by_class={"passenger_car": fitted["fitted_q"]["passenger_car"]}))
    assert fitted["fitted_error"] == pytest.approx(again["calibration_error"]["aoc"], abs=2e-4)
    assert fitted["fitted_brier"] == pytest.approx(again["brier"]["aoc"], abs=2e-4)


def test_fit_of_the_motion_value_on_a_run(run):
    c = check_run(run, fit=True)
    want = recount(run)
    table, hit = want["table"], want["hit"]
    received = table["received"].to_numpy()
    spread = 0.09 + table["age"].to_numpy() ** 2 * 0.25

    def gap(q):                                                                              # the Brier score over the received objects
        return float(np.mean((0.99 * (1 - np.exp(-1.5**2 / (2 * (spread[received] + q * table["age"].to_numpy()[received] ** 3 / 3)))) - hit[received]) ** 2))

    grid = np.exp(np.linspace(np.log(1e-3), np.log(100.0), 2001))
    best = grid[int(np.argmin([gap(q) for q in grid]))]
    fitted = c["fitted_q"]["passenger_car"]
    assert set(c["fitted_q"]) == {"passenger_car"} and fitted == pytest.approx(best, rel=0.02)
    assert gap(fitted) <= gap(4.0) and c["fitted_at_limit"] == []
    assert c["fitted_rows"]["passenger_car"] == (want["received"], pytest.approx(want["received_old"] / want["received"], abs=6e-5))
    again = check_run(run, TrustModel(q_by_class={"passenger_car": fitted}))                 # the fitted value, through the normal path
    assert c["fitted_error"] == pytest.approx(again["calibration_error"]["aoc"], abs=2e-4)
    assert c["fitted_brier"] == pytest.approx(again["brier"]["aoc"], abs=2e-4)
    with pytest.raises(ValueError, match="law 'aoc'"):
        check_run(run, TrustModel(law="cutoff"))


def test_check_of_odd_runs(run):
    sensors_only = Run(obs=run.obs[run.obs["src"] != "v2x"], truth=run.truth, noise=run.noise, info=run.info)
    c = check_run(sensors_only, fit=True)
    assert c["result"] == "PASS" and c["received"] == 0 and c["there_old"] is None and c["fitted_q"] == {}
    assert c["calibration_error"]["cutoff"] == c["calibration_error"]["full_trust"]         # nothing received: nothing to cut
    empty = check_run(Run(obs=run.obs.iloc[:0], truth=run.truth))
    assert empty["result"] == "FAIL" and empty["problems"] == ["the run holds no object"] and empty["calibration_error"]["aoc"] is None
    leaver = run.obs.loc[run.obs["src"] == "v2x", "gt_match_id"].value_counts().index[0]     # this true object leaves after 3 s
    shortened = Run(obs=run.obs, truth=run.truth[(run.truth["obj_id"] != leaver) | (run.truth["t"] <= 3.0)], noise=run.noise, info=run.info)
    c = check_run(shortened)
    want = recount(shortened)
    assert c["result"] == "PASS" and c["gone"] == want["gone"] > 0
    assert_same_counts(c, want)


def test_check_reports_a_broken_discount(run, monkeypatch, exported_runs, capsys):
    def against(frame, masses, reliability):                                                 # a "discount" that moves the lost mass to notE
        out = np.array(masses, dtype=float) * np.asarray(reliability)[:, None]
        out[:, frame.index("notE")] += 1.0 - out.sum(axis=1)
        return out

    monkeypatch.setattr(belief, "discount", against)
    c = check_run(run)
    assert c["result"] == "FAIL"
    assert any(p.startswith("discount by age (B3): ") and "evidence against them" in p for p in c["problems"])
    assert any(p.startswith("cutoff (B1): ") and "evidence against them" in p for p in c["problems"])
    assert not any(p.startswith("full trust (B0)") for p in c["problems"])                   # a reliability of 1 moves nothing

    def raised(frame, masses, reliability):                                                  # a "discount" that gives every object full support
        out = np.zeros_like(np.asarray(masses, dtype=float))
        out[:, frame.index("E")] = 1.0
        return out

    monkeypatch.setattr(belief, "discount", raised)
    problems = check_run(run)["problems"]
    for law in discounting.LAW_NAMES.values():
        assert any(p.startswith(law + ": ") and "beliefs were raised" in p for p in problems)
    assert any("full trust (B0): the evidence of" in p for p in problems) and any("not handled as the cutoff says" in p for p in problems)
    folder, _ = exported_runs["run_aoc_sensors"]
    assert discounting.main([str(folder)]) == 3
    assert "result:              FAIL (" in capsys.readouterr().out


#: The check of the sample run, computed once (Python 3.13). It must not move with the versions of Python, numpy or pandas.
EXPECTED = dict(objects=2791, received=1688, received_old=266, calibration_error={"full_trust": 0.0910, "cutoff": 0.0304, "aoc": 0.0174})


def test_check_is_the_same_whatever_the_python_and_pandas_version(checked):
    c = checked
    assert (c["objects"], c["received"], c["received_old"]) == (EXPECTED["objects"], EXPECTED["received"], EXPECTED["received_old"])
    for law, value in EXPECTED["calibration_error"].items():
        assert c["calibration_error"][law] == pytest.approx(value, abs=2e-4)



def test_command(exported_runs, capsys):
    folder, _ = exported_runs["run_aoc_sensors"]
    assert discounting.main([str(folder), "--fit", "--q", "passenger_car=1.0"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("B5 DISCOUNT CHECK") and "result:              PASS" in out
    assert "changed: passenger_car 1" in out and "kept until the end of the run" in out
    assert "best fit of the motion value on this run" in out and "      passenger_car " in out and "with it, discount by age" in out
    for name in discounting.LAW_NAMES.values():
        assert name in out
    lines = {line.split("  ")[3].strip(): line.split() for line in out.splitlines() if line.startswith("      ") and "(B" in line}
    c = check_run(load_run(folder), TrustModel(q_by_class={"passenger_car": 1.0}))
    for law, name in discounting.LAW_NAMES.items():                                          # the printed numbers are the ones of the check
        assert lines[name][-2:] == ["%.3f" % c["calibration_error"][law], "%.3f" % c["brier"][law]]
    assert "received objects:    %d, of which %.1f%% are older than 1 s" % (c["received"], 100 * c["received_old"] / c["received"]) in out
    assert "really there:        %.1f%% of the received objects up to 1 s, %.1f%% of the older ones" % (100 * c["there_young"], 100 * c["there_old"]) in out
    assert discounting.main([str(folder), "--motion", "--no-sender-confidence", "--cutoff", "0.5", "--epsilon", "2", "--hold", "3"]) == 0
    out = capsys.readouterr().out
    assert "tolerance 2.0 m" in out and "sender confidence not used" in out and "older than 0.5 s" in out and "1.0 s ahead: error per axis" in out
    assert "forgotten when it is older than 3 s" in out
    assert discounting.main([str(folder / "nothing_here")]) == 2
    for bad in (["--q", "passenger_car"], ["--q", "car=1.0"], ["--q", "=2"], ["--hold", "0"], ["--epsilon", "-1"]):
        assert discounting.main([str(folder), *bad]) == 2, bad
    err = capsys.readouterr().err
    assert "CLASS=VALUE" in err and "'car' is not a class of the observations" in err and "--hold must be a positive" in err
