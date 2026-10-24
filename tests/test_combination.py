"""Tests of the combination (task B6): the rules on tables, the fusion of a group, the check of a run."""
import itertools

import numpy as np
import pandas as pd
import pytest

from aoc.fusion import belief, combination
from aoc.fusion.alignment import align
from aoc.fusion.association import associate
from aoc.fusion.bba import build_evidence
from aoc.fusion.belief import CLASS, EXISTENCE, Frame
from aoc.fusion.combination import CombinationModel, Fused, check_run, fuse, group_truth, really_there_fused
from aoc.fusion.discounting import Discounted, TrustModel, discount_evidence
from aoc.fusion.replay import Run, load_run

ABC = Frame("abc", ("a", "b", "c"))


@pytest.fixture(scope="module")
def run(exported_runs):
    folder, _ = exported_runs["run_aoc_sensors"]
    return load_run(folder)


def masses(frame, **by_label):
    """A mass function from labels: a=0.3 puts 0.3 on {a}, ab=0.5 on {a, b}, O=0.2 on the whole frame, empty=0.1 on the empty set."""
    row = np.zeros(frame.n_sets)
    for label, value in by_label.items():
        if label == "O":
            row[frame.omega] = value
        elif label == "empty":
            row[0] = value
        else:
            row[frame.index(tuple(label))] = value
    return row


def as_dict(frame, row, digits=10):
    return {frame.label(number): round(float(value), digits) for number, value in enumerate(row) if abs(value) > 1e-12}


def random_masses(frame, n, rng, with_conflict=True, min_ignorance=0.05):
    table = rng.random((n, frame.n_sets)) * (rng.random((n, frame.n_sets)) < 0.6)
    table[:, frame.omega] += min_ignorance
    if not with_conflict:
        table[:, 0] = 0.0
    return table / table.sum(axis=1, keepdims=True)


def brute_conjunctive(frame, a, b):
    out = np.zeros(frame.n_sets)
    for left, right in itertools.product(range(frame.n_sets), repeat=2):
        out[left & right] += a[left] * b[right]
    return out


# ----------------------------------------------------------------------------
# Conjunctive rule and Dempster's rule
# ----------------------------------------------------------------------------
def test_conjunctive_rule_examples():
    m1, m2 = masses(ABC, ab=0.3, bc=0.5, O=0.2), masses(ABC, b=0.3, bc=0.4, O=0.3)
    assert as_dict(ABC, belief.conjunctive(ABC, m1, m2)[0], 2) == {"b": 0.42, "a|b": 0.09, "b|c": 0.43, "a|b|c": 0.06}     # Table 2 of Denoeux 2008
    e1, e2 = belief.simple_support(EXISTENCE, "E", 0.9), belief.simple_support(EXISTENCE, "E", 0.5)
    assert belief.conjunctive(EXISTENCE, e1, e2)[0] == pytest.approx([0.0, 0.95, 0.0, 0.05])                        # 1 - 0.1 * 0.5
    against = belief.simple_support(EXISTENCE, "notE", 0.6)
    both = belief.conjunctive(EXISTENCE, e1, against)[0]
    assert both == pytest.approx([0.54, 0.36, 0.06, 0.04])                                                            # conflict 0.9 * 0.6
    assert belief.dempster(EXISTENCE, e1, against)[0] == pytest.approx([0.0, 0.36 / 0.46, 0.06 / 0.46, 0.04 / 0.46])


@pytest.mark.parametrize("frame", [EXISTENCE, ABC, CLASS])
def test_conjunctive_rule_against_a_brute_force(frame):
    rng = np.random.default_rng(20)
    a, b = random_masses(frame, 20, rng), random_masses(frame, 20, rng)
    out = belief.conjunctive(frame, a, b)
    assert belief.is_valid(frame, out).all()
    for row in range(20):
        assert np.allclose(out[row], brute_conjunctive(frame, a[row], b[row]))
    assert np.allclose(out, belief.conjunctive(frame, b, a))
    q = belief.commonality(frame, out)
    assert np.allclose(q, belief.commonality(frame, a) * belief.commonality(frame, b))                              # commonalities multiply
    assert np.allclose(belief.conjunctive(frame, a, belief.vacuous(frame, 20)), a)                                   # "I do not know" changes nothing
    assert np.allclose(belief.conjunctive(frame, a, belief.vacuous(frame, 1)), a)                                    # a single row serves every row
    c = random_masses(frame, 20, rng)
    assert np.allclose(belief.conjunctive(frame, belief.conjunctive(frame, a, b), c), belief.conjunctive(frame, a, belief.conjunctive(frame, b, c)))
    with pytest.raises(ValueError, match="same number of rows"):
        belief.conjunctive(frame, a[:2], a[:3])


def test_normalize():
    table = np.array([[0.5, 0.4, 0.0, 0.1], [0.0, 0.7, 0.1, 0.2], [1.0, 0.0, 0.0, 0.0]])
    out = belief.normalize(EXISTENCE, table)
    assert out[0] == pytest.approx([0.0, 0.8, 0.0, 0.2])
    assert out[1] == pytest.approx(table[1])
    assert out[2] == pytest.approx(table[2])                                 # nothing but conflict: left as it is
    assert belief.is_valid(EXISTENCE, out[:2]).all()


# ----------------------------------------------------------------------------
# Commonality, weights and the cautious rule
# ----------------------------------------------------------------------------
def test_commonality_against_its_definition():
    rng = np.random.default_rng(21)
    for frame in (EXISTENCE, ABC, CLASS):
        table = random_masses(frame, 10, rng)
        q = belief.commonality(frame, table)
        for a in range(frame.n_sets):
            want = sum(table[:, b] for b in range(frame.n_sets) if b & a == a)
            assert np.allclose(q[:, a], want)


def test_weights_of_the_examples_of_denoeux():
    """Tables 2 and 3 of Denoeux (2008), frame {a, b, c}."""
    m1 = masses(ABC, ab=0.3, bc=0.5, O=0.2)
    w1 = belief.weights(ABC, m1)[0]
    assert as_dict(ABC, w1, 6) == {"empty": 1.0, "a": 1.0, "b": 1.75, "a|b": 0.4, "c": 1.0, "a|c": 1.0, "b|c": round(2 / 7, 6), "a|b|c": 1.0}
    m2 = masses(ABC, b=0.3, bc=0.4, O=0.3)
    w2 = belief.weights(ABC, m2)[0]
    assert w2[ABC.index("b")] == pytest.approx(0.7) and w2[ABC.index(("b", "c"))] == pytest.approx(3 / 7)
    m3 = masses(ABC, a=0.3, c=0.4, O=0.3)
    w3 = belief.weights(ABC, m3)[0]
    assert w3[0] == pytest.approx(7 / 5) and w3[ABC.index("a")] == pytest.approx(0.5) and w3[ABC.index("c")] == pytest.approx(3 / 7)
    for m in (m1, m2, m3):
        assert np.allclose(belief.from_weights(ABC, belief.weights(ABC, m)), m)


def test_weights_of_a_simple_support_and_of_a_combination():
    rng = np.random.default_rng(22)
    for frame in (EXISTENCE, ABC, CLASS):
        for subset in ([frame.elements[0]], list(frame.elements[:2])):
            if len(subset) == len(frame.elements):
                continue                                                       # the whole frame has no weight of its own
            s = 0.7
            w = belief.weights(frame, belief.simple_support(frame, subset, s))[0]
            want = np.ones(frame.n_sets)
            want[frame.index(subset)] = 1 - s
            assert np.allclose(w, want)                                        # one weight below 1: the support, on its own set
        a, b = random_masses(frame, 15, rng), random_masses(frame, 15, rng)
        assert np.allclose(belief.weights(frame, belief.conjunctive(frame, a, b)), belief.weights(frame, a) * belief.weights(frame, b))
        assert np.allclose(belief.from_weights(frame, belief.weights(frame, a)), a)
    with pytest.raises(ValueError, match="not dogmatic"):
        belief.weights(EXISTENCE, masses(EXISTENCE, E=1.0))
    with pytest.raises(ValueError, match="positive"):
        belief.from_weights(EXISTENCE, [1.0, 0.0, 1.0, 1.0])
    with pytest.raises(ValueError, match="whole frame must be 1"):
        belief.from_weights(EXISTENCE, [1.0, 0.5, 1.0, 0.9])
    with pytest.raises(ValueError, match="negative"):
        belief.weights(EXISTENCE, [0.0, 1.2, -0.4, 0.2])


def test_cautious_rule_examples_of_denoeux():
    m1, m2 = masses(ABC, ab=0.3, bc=0.5, O=0.2), masses(ABC, b=0.3, bc=0.4, O=0.3)
    out = belief.cautious(ABC, m1, m2)[0]
    assert as_dict(ABC, out, 6) == {"b": 0.6, "a|b": 0.12, "b|c": 0.2, "a|b|c": 0.08}                              # Table 2
    m3 = masses(ABC, a=0.3, c=0.4, O=0.3)
    out = belief.cautious(ABC, m1, m3)[0]                                                                            # Table 3
    want = {"empty": 0.6102, "a": 0.0612, "b": 0.0918, "a|b": 0.0367, "c": 0.1143, "b|c": 0.0612, "a|b|c": 0.0245}       # the paper prints 2 digits
    got = as_dict(ABC, out, 4)
    for label, value in want.items():
        assert got[label] == pytest.approx(value, abs=0.0001), label
    assert np.allclose(belief.normalize(ABC, 0.5 * out), belief.normalize(ABC, out))                                # a row that does not sum to 1
    normalized = as_dict(ABC, belief.normalize(ABC, out)[0], 3)
    for label, value in {"a": 0.16, "b": 0.24, "a|b": 0.094, "c": 0.29, "b|c": 0.16, "a|b|c": 0.063}.items():
        assert normalized[label] == pytest.approx(value, abs=0.005), label
    assert "empty" not in normalized
    assert belief.conjunctive(ABC, m1, m3)[0][0] == pytest.approx(0.27, abs=0.005)                                   # the conflict of the conjunctive rule


@pytest.mark.parametrize("frame", [EXISTENCE, ABC, CLASS])
def test_cautious_rule_properties(frame):
    rng = np.random.default_rng(23)
    a, b, c = (random_masses(frame, 12, rng) for _ in range(3))
    out = belief.cautious(frame, a, b)
    assert belief.is_valid(frame, out).all()
    assert np.allclose(out, belief.cautious(frame, b, a))                                                           # commutative
    assert np.allclose(belief.cautious(frame, a, a), a)                                                             # idempotent
    assert np.allclose(belief.cautious(frame, belief.cautious(frame, a, b), c), belief.cautious(frame, a, belief.cautious(frame, b, c)))
    assert np.allclose(belief.weights(frame, out), np.minimum(belief.weights(frame, a), belief.weights(frame, b)))  # the definition
    separable = belief.simple_support(frame, frame.elements[0], rng.random(12))                                    # a separable mass function
    assert np.allclose(belief.cautious(frame, separable, belief.vacuous(frame, 1)), separable)                     # "I do not know" changes it not
    with pytest.raises(ValueError, match="not dogmatic"):
        belief.cautious(frame, a, belief.simple_support(frame, frame.elements[0], 1.0))


def test_cautious_rule_keeps_the_larger_support_and_counts_shared_evidence_once():
    s1, s2 = np.array([0.9, 0.3, 0.0]), np.array([0.5, 0.3, 0.7])
    e1, e2 = belief.simple_support(EXISTENCE, "E", s1), belief.simple_support(EXISTENCE, "E", s2)
    assert np.allclose(belief.belief(EXISTENCE, belief.cautious(EXISTENCE, e1, e2), "E"), np.maximum(s1, s2))
    assert np.allclose(belief.belief(EXISTENCE, belief.conjunctive(EXISTENCE, e1, e2), "E"), 1 - (1 - s1) * (1 - s2))   # the conjunctive rule adds up
    three = belief.combine(EXISTENCE, [e1, e1, e1], belief.cautious)
    assert np.allclose(three, e1)                                                                                   # the same report three times: once
    assert np.allclose(belief.combine(EXISTENCE, [], belief.cautious), belief.vacuous(EXISTENCE, 1))
    assert np.allclose(belief.combine(EXISTENCE, [e2]), e2)


def test_rules_agree_with_the_library_dst_py_of_godwin():
    pytest.importorskip("dempster_shafer", reason="the library dst-py is not installed")
    import dempster_shafer as ds
    from dempster_shafer.core.mass_function import MassFunction
    rng = np.random.default_rng(24)
    for frame in (EXISTENCE, ABC):
        sets = [frame.subset(a) for a in range(1, frame.n_sets)]
        to_theirs = lambda row: MassFunction({tuple(sorted(s)): float(row[frame.index(s)]) for s in sets if row[frame.index(s)] > 0}, frozenset(frame.elements))
        to_row = lambda theirs: np.array([float(theirs[frozenset()]) if frozenset() in theirs else 0.0]
                                         + [float(theirs[frozenset(s)]) if frozenset(s) in theirs else 0.0 for s in sets])
        a, b = random_masses(frame, 12, rng, with_conflict=False), random_masses(frame, 12, rng, with_conflict=False)
        for row, second in zip(a, b):
            assert np.allclose(belief.conjunctive(frame, row, second)[0], to_row(ds.combine_conjunctive(to_theirs(row), to_theirs(second), normalization=False)))
            assert np.allclose(belief.dempster(frame, row, second)[0], to_row(ds.combine_conjunctive(to_theirs(row), to_theirs(second))))
    # The cautious rule of dst-py is not used as a referee: on Table 2 of Denoeux (2008) it returns an empty mass function.


# ----------------------------------------------------------------------------
# The fusion of a group
# ----------------------------------------------------------------------------
def objects(**columns):
    n = len(next(iter(columns.values())))
    base = {"src": ["v2x"] * n, "tx_station": [None] * n, "sensor": [None] * n, "class": ["passenger_car"] * n, "t_meas": [1.0] * n,
            "gt_match_id": ["o1"] * n, "x_t": [0.0] * n, "y_t": [0.0] * n}
    base.update(columns)
    text = ("src", "tx_station", "sensor", "class", "gt_match_id")
    return pd.DataFrame({name: pd.Series(values, dtype=object if name in text else float) for name, values in base.items()})


def trusted_from(scores, classes=None):
    existence = belief.simple_support(EXISTENCE, "E", scores)
    n = len(scores)
    if classes is None:
        class_table = belief.vacuous(CLASS, n)
    else:
        class_table = np.vstack([belief.simple_support(CLASS, c, 0.9) if c else belief.vacuous(CLASS, 1) for c in classes])
    return Discounted(existence=existence, classes=class_table, reliability=np.ones(n))


def test_fuse_one_group_of_received_objects():
    table = objects(src=["v2x"] * 3, tx_station=["car2", "car3", "car3"], t_meas=[1.0, 1.4, 1.2])
    fused = fuse(table, [5, 5, 5], trusted_from([0.9, 0.5, 0.2]))
    assert len(fused) == 1 and fused.members.tolist() == [0, 0, 0]
    line = fused.objects.iloc[0]
    assert (line["group"], line["n_sources"], line["n_local"], line["n_received"], line["n_senders"], line["newest"]) == (5, 3, 0, 3, 2, 1)
    assert line["bel_exists"] == pytest.approx(0.9) and line["pl_exists"] == pytest.approx(1.0)                  # cautious: the best report
    assert line["class"] == "passenger_car" and line["conflict_class"] == 0.0 and line["p_car"] == pytest.approx(0.25)   # no class evidence
    ablation = fuse(table, [5, 5, 5], trusted_from([0.9, 0.5, 0.2]), CombinationModel(cpm_rule="conjunctive"))
    assert ablation.objects["bel_exists"].iloc[0] == pytest.approx(1 - 0.1 * 0.5 * 0.8)                          # Dempster only adds up


def test_fuse_local_and_received_evidence():
    table = objects(src=["radar", "radar", "v2x", "v2x"], sensor=["FrontRadar", "RearRadar", None, None], tx_station=[None, None, "car2", "car3"],
                    t_meas=[2.0, 2.0, 1.5, 1.8])
    fused = fuse(table, [0, 0, 0, 0], trusted_from([0.5, 0.5, 0.6, 0.6]))
    line = fused.objects.iloc[0]
    assert (line["n_local"], line["n_received"], line["n_senders"], line["newest"]) == (2, 2, 2, 0)
    own = 1 - 0.5 * 0.5                                                      # the two radars: conjunctive
    theirs = 0.6                                                             # the two senders: cautious, the larger support
    assert line["bel_exists"] == pytest.approx(1 - (1 - own) * (1 - theirs)) # across: conjunctive
    assert np.allclose(fused.existence[0], [0.0, line["bel_exists"], 0.0, 1 - line["bel_exists"]])
    alone = fuse(table.iloc[:2], [0, 0], trusted_from([0.5, 0.5]))          # on-board only
    assert alone.objects["bel_exists"].iloc[0] == pytest.approx(own) and alone.objects["n_received"].iloc[0] == 0
    only_received = fuse(table.iloc[2:], [3, 3], trusted_from([0.6, 0.6]))
    assert only_received.objects["bel_exists"].iloc[0] == pytest.approx(0.6)


def test_fuse_several_groups_and_classes():
    table = objects(src=["radar", "v2x", "v2x", "v2x", "radar"], tx_station=[None, "a", "b", "a", None], t_meas=[3.0, 1.0, 2.0, 2.5, 3.0],
                    **{"class": ["passenger_car", "passenger_car", "heavy_truck", "passenger_car", "cyclist"]})
    trusted = trusted_from([0.9, 0.3, 0.3, 0.3, 0.9], ["car", "car", "truck", "car", "bicycle"])
    fused = fuse(table, np.array([2, 0, 0, 0, 7]), trusted)
    assert fused.objects["group"].tolist() == [0, 2, 7] and fused.members.tolist() == [1, 0, 0, 0, 2]
    first = fused.objects.iloc[0]
    assert (first["n_received"], first["n_senders"], first["newest"], first["class"]) == (3, 2, 3, "passenger_car")
    assert first["bel_exists"] == pytest.approx(0.3)                         # three reports of 0.3 with the cautious rule: 0.3
    assert first["conflict_class"] > 0.0                                     # car against truck
    assert first["p_car"] == pytest.approx(first["p_truck"]) and first["p_truck"] > first["p_bicycle"] == first["p_pedestrian"]
    ablation = fuse(table, np.array([2, 0, 0, 0, 7]), trusted, CombinationModel(cpm_rule="conjunctive")).objects.iloc[0]
    assert ablation["p_car"] > ablation["p_truck"]                           # Dempster only counts the two "car" reports twice; the cautious rule once
    assert fused.objects["class"].tolist() == ["passenger_car", "passenger_car", "cyclist"]
    assert fused.objects["conflict_class"].iloc[1] == 0.0 and fused.objects["p_bicycle"].iloc[2] > 0.9
    pignistic = belief.pignistic(CLASS, fused.classes)
    assert np.allclose(pignistic.sum(axis=1), 1.0)


def test_fuse_edge_cases():
    table = objects(src=["v2x"], tx_station=["a"])
    empty = fuse(table.iloc[:0], np.zeros(0, dtype=int), trusted_from([]))
    assert len(empty) == 0 and empty.existence.shape == (0, 4) and list(empty.objects.columns)[:3] == ["group", "n_sources", "n_local"]
    with pytest.raises(ValueError, match="one group number"):
        fuse(table, [0, 1], trusted_from([0.5]))
    with pytest.raises(ValueError, match="whole numbers"):
        fuse(table, [-1], trusted_from([0.5]))
    with pytest.raises(ValueError, match="rule must be"):
        CombinationModel(cpm_rule="average")
    with pytest.raises(ValueError, match="min_ignorance"):
        CombinationModel(min_ignorance=1.0)
    certain = fuse(objects(src=["v2x", "v2x"], tx_station=["a", "b"]), [0, 0], trusted_from([1.0, 0.5]))             # a certainty: not dogmatic any more
    assert certain.objects["bel_exists"].iloc[0] == pytest.approx(1.0, abs=1e-8) and belief.is_valid(EXISTENCE, certain.existence).all()
    across = fuse(objects(src=["radar", "v2x"], tx_station=[None, "a"]), [0, 0], trusted_from([1.0, 0.5]), CombinationModel(across_rule="cautious"))
    assert across.objects["bel_exists"].iloc[0] == pytest.approx(1.0, abs=1e-8)                                   # the floor is applied across too
    no_time = fuse(objects(src=["v2x", "v2x"], tx_station=["a", "b"], t_meas=[np.nan, np.nan]), [0, 0], trusted_from([0.2, 0.5]))
    assert no_time.objects["newest"].iloc[0] == 0


def test_fuse_on_a_real_tick(run):
    tick = max(run.replay(run.egos[0]), key=lambda t: len(t.v2x))
    aligned = align(tick)
    groups = associate(aligned)
    trusted = discount_evidence(build_evidence(aligned.objects), aligned.objects)
    fused = fuse(aligned.objects, groups, trusted, t=tick.t, ego=tick.ego)
    assert fused.t == tick.t and fused.ego == tick.ego
    assert len(fused) == len(np.unique(groups)) and fused.objects["n_sources"].sum() == len(aligned)
    assert belief.is_valid(EXISTENCE, fused.existence).all() and belief.is_valid(CLASS, fused.classes).all()
    assert (fused.objects["bel_exists"] <= fused.objects["pl_exists"] + 1e-12).all() and (fused.objects["pl_exists"] == 1.0).all()
    member_bel = belief.belief(EXISTENCE, trusted.existence, "E")
    best = np.zeros(len(fused))
    np.maximum.at(best, fused.members, member_bel)
    only_received = (fused.objects["n_local"] == 0).to_numpy()
    assert np.allclose(fused.objects["bel_exists"].to_numpy()[only_received], best[only_received])               # cautious: the best report
    with_local = ~only_received
    assert (fused.objects["bel_exists"].to_numpy()[with_local] >= best[with_local] - 1e-12).all()                 # distinct evidence adds up
    there = really_there_fused(fused, aligned.objects, tick.truth, 1.5)
    assert there.dtype == bool and len(there) == len(fused)
    chosen, agree = group_truth(fused, aligned.objects)
    assert agree.all() and all(isinstance(name, str) for name in chosen)


def test_group_truth_and_really_there():
    table = objects(src=["v2x"] * 5, tx_station=list("abcde"), gt_match_id=["o1", "o1", "o2", None, "o3"], t_meas=[1.0, 2.0, 1.0, 1.0, 1.0],
                    x_t=[0.0, 1.0, 10.0, 0.0, 20.0], y_t=[0.0] * 5)
    fused = fuse(table, [0, 0, 1, 2, 3], trusted_from([0.5] * 5))
    chosen, agree = group_truth(fused, table)
    assert chosen.tolist() == ["o1", "o2", None, "o3"] and agree.tolist() == [True, True, False, True]
    truth = pd.DataFrame({"obj_id": ["o1", "o2"], "x": [2.0, 10.0], "y": [0.0, 1.6]})
    assert really_there_fused(fused, table, truth, 1.5).tolist() == [True, False, False, False]                  # newest member of o1 at 1.0 m; o2 at 1.6 m; o3 gone
    mixed = fuse(table, [0, 0, 0, 1, 2], trusted_from([0.5] * 5))
    assert group_truth(mixed, table)[1].tolist() == [False, False, True]                                           # o1 and o2 in one group: wrong
    close = pd.DataFrame({"obj_id": ["o1", "o2", "o3"], "x": [1.0, 10.0, 20.0], "y": [0.0, 0.0, 0.0]})            # every newest member is in place
    assert really_there_fused(mixed, table, close, 1.5).tolist() == [False, False, True]                           # a wrong group is never "there"
    assert really_there_fused(fused, table, close, 1.5).tolist() == [True, True, False, True]


# ----------------------------------------------------------------------------
# The check of a run and the command
# ----------------------------------------------------------------------------
def recount(run, hold=None):
    """The fused belief of the four variants, counted again object by object with the plain rules."""
    rows = {name: [] for name in ("B0", "B1", "B3", "B4")}
    hits = []
    for ego in run.egos:
        for tick in run.replay(ego, hold=hold):
            aligned = align(tick)
            if not len(aligned):
                continue
            groups = associate(aligned)
            objects_ = aligned.objects
            received = (objects_["src"] == "v2x").to_numpy()
            age = objects_["age_now"].to_numpy(dtype=float)
            alpha_aoc = discount_evidence(build_evidence(objects_), objects_).reliability
            laws = {"B0": np.ones(len(objects_)), "B1": np.where(received & (age > 1.0 + 1e-9), 0.0, 1.0), "B3": alpha_aoc, "B4": alpha_aoc}
            place = dict(zip(tick.truth["obj_id"], zip(tick.truth["x"], tick.truth["y"])))
            for number in np.unique(groups):
                members = np.flatnonzero(groups == number)
                names = {objects_["gt_match_id"].iloc[r] for r in members}
                newest = members[int(np.argmax(objects_["t_meas"].to_numpy(dtype=float)[members]))]
                xy = place.get(objects_["gt_match_id"].iloc[newest]) if len(names) == 1 else None
                hits.append(xy is not None and np.hypot(objects_["x_t"].iloc[newest] - xy[0], objects_["y_t"].iloc[newest] - xy[1]) <= 1.5)
                for name, alpha in laws.items():
                    s = 0.99 * alpha[members]
                    local, cpm = s[~received[members]], s[received[members]]
                    own = 1 - np.prod(1 - local) if len(local) else None
                    theirs = (cpm.max() if name == "B3" else 1 - np.prod(1 - cpm)) if len(cpm) else None
                    parts = [p for p in (own, theirs) if p is not None]
                    rows[name].append(1 - np.prod([1 - p for p in parts]))
    return {name: np.array(values) for name, values in rows.items()}, np.array(hits, dtype=float)


def test_check_of_a_run_against_a_recount(run):
    c = check_run(run)
    beliefs, hit = recount(run)
    assert c["result"] == "PASS" and c["problems"] == [] and c["fused"] == len(hit) and c["wrong_groups"] == 0
    for name in ("B0", "B1", "B3", "B4"):
        want = np.mean((beliefs[name] - hit) ** 2)
        assert c["brier"][name] == pytest.approx(want, abs=6e-5), name
        total = 0.0
        for b in range(10):
            inside = np.minimum((beliefs[name] * 10).astype(int), 9) == b
            if inside.any():
                total += inside.sum() * abs(beliefs[name][inside].mean() - hit[inside].mean())
        assert c["calibration_error"][name] == pytest.approx(total / len(hit), abs=6e-5), name
    assert c["several_senders"] > 0 and c["gain_mean"] > 0 and c["gain_max"] >= c["gain_mean"]
    assert c["brier"]["B3"] < c["brier"]["B1"] < c["brier"]["B0"]
    assert c["conflict_class_max"] == 0.0                                    # all cars in the sample run
    limited = check_run(run, hold=2.0)
    assert limited["hold"] == 2.0 and limited["objects"] < c["objects"]
    beliefs, hit = recount(run, hold=2.0)
    assert limited["brier"]["B3"] == pytest.approx(np.mean((beliefs["B3"] - hit) ** 2), abs=6e-5)
    with pytest.raises(ValueError, match="law 'aoc'"):
        check_run(run, trust=TrustModel(law="cutoff"))


def test_check_of_odd_runs(run):
    sensors_only = Run(obs=run.obs[run.obs["src"] != "v2x"], truth=run.truth, noise=run.noise, info=run.info)
    c = check_run(sensors_only)
    assert c["result"] == "PASS" and c["with_received"] == 0 and c["gain_mean"] is None
    assert c["calibration_error"]["B0"] == c["calibration_error"]["B1"] == c["calibration_error"]["B3"] == c["calibration_error"]["B4"]
    empty = check_run(Run(obs=run.obs.iloc[:0], truth=run.truth))
    assert empty["result"] == "FAIL" and empty["problems"] == ["the run holds no object"]


def test_check_reports_a_broken_rule(run, monkeypatch, exported_runs, capsys):
    def too_sure(frame, a, b):                                               # a "cautious rule" that adds up like the conjunctive rule
        return belief.conjunctive(frame, a, b)

    monkeypatch.setitem(combination.RULES, "cautious", too_sure)
    c = check_run(run)
    assert c["result"] == "FAIL" and any("B3: the cautious rule gave" in p for p in c["problems"])

    def against(frame, a, b):                                                # a rule that puts mass on the empty set of the existence frame
        out = belief.conjunctive(frame, a, b)
        if frame is EXISTENCE:
            out[:, 0] += 0.5 * out[:, frame.omega]
            out[:, frame.omega] *= 0.5
        return out

    monkeypatch.setitem(combination.RULES, "cautious", belief.cautious)
    monkeypatch.setitem(combination.RULES, "conjunctive", against)
    problems = check_run(run)["problems"]
    assert any("conflict on the existence frame" in p for p in problems)
    folder, _ = exported_runs["run_aoc_sensors"]
    assert combination.main([str(folder)]) == 3
    assert "result:              FAIL (" in capsys.readouterr().out


def test_command(exported_runs, capsys):
    folder, _ = exported_runs["run_aoc_sensors"]
    assert combination.main([str(folder), "--hold", "2", "--q", "passenger_car=1.0"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("B6 COMBINATION CHECK") and "result:              PASS" in out and "forgotten when it is older than 2 s" in out
    for label in ("full trust (B0)", "cutoff (B1)", "discount, cautious rule (B3)", "discount, Dempster only (B4)"):
        assert label in out
    c = check_run(load_run(folder), trust=TrustModel(q_by_class={"passenger_car": 1.0}), hold=2.0)
    lines = {line.split("  ")[3].strip(): line.split() for line in out.splitlines() if line.startswith("      ") and "(B" in line}
    for name, label in {"B0": "full trust (B0)", "B1": "cutoff (B1)", "B3": "discount, cautious rule (B3)", "B4": "discount, Dempster only (B4)"}.items():
        assert lines[label][-2:] == ["%.3f" % c["calibration_error"][name], "%.3f" % c["brier"][name]]
    assert "fused into %d real objects" % c["fused"] in out
    assert combination.main([str(folder / "nothing_here")]) == 2
    assert combination.main([str(folder), "--hold", "-1"]) == 2
    assert combination.main([str(folder), "--q", "car=1"]) == 2


def test_fused_type_holds_what_it_says():
    fused = Fused(t=1.0, ego="x", objects=pd.DataFrame({"group": [0]}), existence=np.zeros((1, 4)), classes=np.zeros((1, 16)), members=np.zeros(1, dtype=int))
    assert len(fused) == 1
