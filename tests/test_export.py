"""Tests of the exporter (task A8) on two short runs of the Artery example scenario.

tests/data/run_example_sensors   14 s, the sensors of the Artery example (radars, CAM sensor, see-through sensor)
tests/data/run_aoc_sensors       14 s, the sensors of artery-ext/config/sensors-aoc.xml (radars only)
"""
import json
import pathlib

import pandas as pd
import pytest

from aoc.io import export, schema

# The 27 columns of the logging schema of the Plan, in the order of the Plan.
PLAN_COLUMNS = [
    "run_id", "seed", "t_sim", "rx_station", "src", "tx_station",
    "obj_id", "class", "x", "y", "vx", "vy", "P11", "P12", "P22", "Pv11", "Pv22",
    "exist_score", "sender_conf", "t_meas", "t_rx", "age",
    "cbr", "dcc_state", "pdr_window", "weather", "gt_match_id",
]

EXPECTED = {
    "run_example_sensors": {"cpm_tx": 390, "cpm_rx": 935, "cpm_obj": 318, "local": 2083, "v2x": 965,
                            "sources": {"radar", "v2x", "see_through", "cam_msg"}},
    "run_aoc_sensors": {"cpm_tx": 390, "cpm_rx": 943, "cpm_obj": 232, "local": 1103, "v2x": 667,
                        "sources": {"radar", "v2x"}},
}


@pytest.fixture(scope="module", params=list(EXPECTED))
def exported(request, exported_runs):
    folder, done = exported_runs[request.param]
    return request.param, folder, done


def test_export_passes_its_own_checks(exported):
    name, out, done = exported
    assert done.returncode == 0, done.stdout + done.stderr
    summary = json.loads((out / "export_summary.json").read_text())
    assert summary["result"] == "PASS" and summary["problems"] == []
    assert summary["export_version"] == export.EXPORT_VERSION


def test_row_counts(exported):
    name, out, _ = exported
    want = EXPECTED[name]
    rows = json.loads((out / "export_summary.json").read_text())["rows"]
    for table in ("cpm_tx", "cpm_rx", "cpm_obj", "local"):
        assert rows[table] == want[table]
        assert len(pd.read_parquet(out / f"{table}.parquet")) == want[table]
    obs = pd.read_parquet(out / "observations.parquet")
    assert len(obs) == want["v2x"] + want["local"]
    assert int((obs["src"] == "v2x").sum()) == want["v2x"]
    assert set(obs["src"].unique()) == want["sources"]


def test_columns_of_the_observations_table(exported):
    _, out, _ = exported
    obs = pd.read_parquet(out / "observations.parquet")
    assert list(obs.columns) == [name for name, _, _ in export.OBS_COLUMNS]
    assert list(obs.columns[:len(PLAN_COLUMNS)]) == PLAN_COLUMNS


def test_times_and_ages(exported):
    _, out, _ = exported
    obs = pd.read_parquet(out / "observations.parquet")
    assert ((obs["age"] - (obs["t_rx"] - obs["t_meas"])).abs() < 1e-9).all()
    assert (obs["age"] >= 0.0).all()
    v2x = obs[obs["src"] == "v2x"]
    assert (v2x["age"] > 0.0).all() and v2x["age"].max() < 1.0
    assert (v2x["t_sim"] == v2x["t_rx"]).all()
    assert v2x["tx_station"].notna().all()
    local = obs[obs["src"] != "v2x"]
    assert local["tx_station"].isna().all()


def test_every_object_has_a_true_object(exported):
    _, out, _ = exported
    obs = pd.read_parquet(out / "observations.parquet")
    assert obs["gt_match_id"].notna().all()
    assert obs.loc[obs["src"] == "v2x", "gt_dist"].max() <= 0.02


def test_schema_document_is_up_to_date():
    doc = pathlib.Path(__file__).parents[1] / "docs" / "observations_schema.md"
    assert doc.read_text(encoding="utf-8") == schema.markdown(), "run: python -m aoc.io.schema > docs/observations_schema.md"
