"""Tests of the evidence construction (task B4)."""
import numpy as np
import pandas as pd
import pytest

from aoc.fusion import bba, belief
from aoc.fusion.alignment import align
from aoc.fusion.bba import CLASS_OF_LABEL, EvidenceModel, build_evidence, check_run, existence_scores
from aoc.fusion.belief import CLASS, EXISTENCE
from aoc.fusion.replay import load_run


@pytest.fixture(scope="module")
def run(exported_runs):
    folder, _ = exported_runs["run_aoc_sensors"]
    return load_run(folder)


def table(**columns):
    n = len(next(iter(columns.values())))
    base = {"meas_src": ["radar"] * n, "class": ["passenger_car"] * n}
    base.update(columns)
    return pd.DataFrame({name: pd.Series(values, dtype=object if name in ("meas_src", "class") else float) for name, values in base.items()})


# ----------------------------------------------------------------------------
# Existence
# ----------------------------------------------------------------------------
def test_existence_mass_of_a_reported_object():
    evidence = build_evidence(table(meas_src=["radar", "cam_msg"]))
    assert len(evidence) == 2 and evidence.exist_score.tolist() == [0.99, 0.99]
    assert np.allclose(evidence.existence, [[0.0, 0.99, 0.0, 0.01]] * 2)           # columns: empty, {E}, {notE}, {E, notE}
    assert belief.belief(EXISTENCE, evidence.existence, "E").tolist() == pytest.approx([0.99, 0.99])
    assert belief.pignistic(EXISTENCE, evidence.existence)[0] == pytest.approx([0.995, 0.005])
    assert belief.plausibility(EXISTENCE, evidence.existence, "E").tolist() == pytest.approx([1.0, 1.0])
    assert (evidence.existence[:, EXISTENCE.index("notE")] == 0.0).all()           # a report never speaks against the object


def test_score_by_origin_of_the_measurement():
    model = EvidenceModel(existence_score={"radar": 0.95, "cam_msg": 0.6}, existence_default=0.5)
    objects = table(meas_src=["radar", "cam_msg", "lidar", None])
    assert existence_scores(objects, model).tolist() == [0.95, 0.6, 0.5, 0.5]
    assert np.allclose(build_evidence(objects, model).existence[:, 1], [0.95, 0.6, 0.5, 0.5])


def test_score_given_by_the_source_wins():
    objects = table(meas_src=["radar", "radar", "radar"], exist_score=[0.4, np.nan, 1.0])
    evidence = build_evidence(objects)
    assert evidence.exist_score.tolist() == [0.4, 0.99, 1.0]
    assert np.allclose(evidence.existence, [[0, 0.4, 0, 0.6], [0, 0.99, 0, 0.01], [0, 1.0, 0, 0.0]])
    with pytest.raises(ValueError, match="outside 0 to 1"):
        build_evidence(table(meas_src=["radar"], exist_score=[1.3]))


# ----------------------------------------------------------------------------
# Class
# ----------------------------------------------------------------------------
def test_class_mass_of_each_class_name():
    labels = ["passenger_car", "heavy_truck", "bus", "cyclist", "pedestrian"]
    evidence = build_evidence(table(**{"class": labels}))
    assert evidence.class_element.tolist() == ["car", "truck", "truck", "bicycle", "pedestrian"]
    for row, element in zip(evidence.classes, evidence.class_element):
        assert row[CLASS.index(element)] == pytest.approx(0.9) and row[CLASS.omega] == pytest.approx(0.1)
        assert row.sum() == pytest.approx(1.0)
    assert belief.pignistic(CLASS, evidence.classes[:1])[0] == pytest.approx([0.925, 0.025, 0.025, 0.025])


def test_unknown_class_gives_no_information():
    evidence = build_evidence(table(**{"class": ["motorcycle", "unknown", None, "rsu"]}))
    assert evidence.class_element.tolist() == [None] * 4
    assert np.allclose(evidence.classes, belief.vacuous(CLASS, 4))
    assert set(CLASS_OF_LABEL.values()) == set(CLASS.elements)


def test_class_confidence_and_own_table():
    model = EvidenceModel(class_confidence=0.6, class_of_label={"passenger_car": "car", "motorcycle": "bicycle"})
    evidence = build_evidence(table(**{"class": ["passenger_car", "motorcycle", "pedestrian"]}), model)
    assert evidence.class_element.tolist() == ["car", "bicycle", None]
    assert evidence.classes[0, CLASS.index("car")] == pytest.approx(0.6)
    assert evidence.classes[2, CLASS.omega] == 1.0


def test_bad_models_are_refused():
    for bad in (dict(existence_default=1.5), dict(class_confidence=-0.1), dict(existence_score={"radar": float("nan")}),
                dict(class_of_label={"passenger_car": "vehicle"})):
        with pytest.raises(ValueError):
            EvidenceModel(**bad)


def test_empty_table():
    evidence = build_evidence(table(meas_src=[], **{"class": []}))
    assert len(evidence) == 0 and evidence.existence.shape == (0, 4) and evidence.classes.shape == (0, 16)


# ----------------------------------------------------------------------------
# The sample run
# ----------------------------------------------------------------------------
def test_evidence_of_every_object_of_a_tick(run):
    seen = 0
    for tick in list(run.replay("flow0.0"))[::5]:
        aligned = align(tick)
        evidence = build_evidence(aligned.objects)
        assert len(evidence) == len(aligned)
        assert belief.is_valid(EXISTENCE, evidence.existence).all() and belief.is_valid(CLASS, evidence.classes).all()
        assert (evidence.class_element == "car").all()                 # the sample run has passenger cars only
        seen += len(evidence)
    assert seen > 100


def test_check_of_the_sample_run_passes(run, exported_runs, capsys):
    result = check_run(run)
    assert result["result"] == "PASS" and result["invalid"] == 0 and result["mean_score"] == 0.99
    assert result["by_origin"] == {"radar": result["objects"]} and result["by_class"] == {"car": result["objects"]}
    folder, _ = exported_runs["run_aoc_sensors"]
    assert bba.main([str(folder)]) == 0
    assert "result:              PASS" in capsys.readouterr().out
    assert bba.main([str(folder), "--score", "0.7", "--class-confidence", "0.5"]) == 0
    assert "existence score 0.70, class confidence 0.50" in capsys.readouterr().out
    assert bba.main([str(folder), "--score", "1.5"]) == 2
    assert bba.main([str(folder / "missing")]) == 2
