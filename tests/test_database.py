"""Tests of the results database (aoc.analysis.database, task D4) on a handmade set of tables and on the real tables of
results/ when they are there."""
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis import database, grid
from aoc.analysis.metrics import WAYS

ROOT = pathlib.Path(__file__).resolve().parents[1]


def fake_results(root: pathlib.Path, seeds=range(12), holds=(5.0,), gain: float = 0.5) -> pathlib.Path:
    """Tables of D2 and D3 for every cell: B3's calibration error is (1 - gain) times B1's, B0 twice B1, B2 between; the
    stale rate of B3 is 0.3, B0 0.99, B1 0; the awareness grows with the penetration."""
    rng = np.random.default_rng(0)
    metrics, bins, events, facts = [], [], [], []
    for n in range(grid.N_RUNS):
        c = grid.cell_of_run(n)
        if c["seed"] not in seeds:
            continue
        base = 0.10 + 0.0002 * c["pen"] + rng.normal(0, 0.005)
        ece = {"B0": 2 * base, "B1": base, "B2": 0.8 * base, "B3": (1 - gain) * base, "B4": (1 - gain) * base * 1.1}
        stale = {"B0": 0.99, "B1": 0.0, "B2": 0.07, "B3": 0.3, "B4": 0.31}
        facts.append(dict(run=n, pen_share=c["pen"] / 100 + rng.normal(0, 0.02), cars_mean=20.0, background_eligible=80, rate_median_hz=10.0,
                          cbr_p95=0.1, delay_median_ms=1.0, cpm_rx_after_warmup=1000))
        for hold in holds if c["seed"] in (0, 1) else (5.0,):
            for way in WAYS:
                metrics.append(dict(run=n, density=c["density"], pen=c["pen"], msg=c["msg"], seed=c["seed"], kind=c["kind"], run_id="r%d" % n, way=way,
                                    name=way, hold=hold, ticks=1000, objects=5000, no_ego_state=0, seconds=1.0, cbr_mean=0.05, pdr_mean=0.99, age_median=0.06,
                                    age_p95=0.15, received_rows=100, fused_objects=4000, calibration_error=ece[way] * (1 + 0.1 * (hold - 5)),
                                    brier=ece[way] + 0.05, coverage=0.5, sharpness=0.4, awareness_ratio=0.6 + 0.004 * c["pen"], objects_in_range=100,
                                    rmse=0.4, really_there=90, stale_rate=stale[way], stale_objects=10, ghost_rate=stale[way], ghost_objects=10,
                                    wrong_groups=1, entries=20, time_to_confident=0.2, confident_share=0.9, receivers=23, replay_seconds=100.0))
                for b in range(10):
                    count = 100 if b in (0, 9) else 10
                    bins.append(dict(run=n, density=c["density"], pen=c["pen"], msg=c["msg"], seed=c["seed"], kind=c["kind"], run_id="r%d" % n, way=way,
                                     hold=hold, bin=b, low=b / 10, high=(b + 1) / 10, count=count, belief_mean=(b + 0.5) / 10,
                                     share_there=(b + 0.5) / 10 + (0.1 if way == "B0" else 0.0), plausibility_mean=1.0))
            if hold == 5.0:
                for k in (1, 2, 3):
                    for way in ("B0", "B1", "B3", "B4"):
                        events.append(dict(run=n, density=c["density"], pen=c["pen"], msg=c["msg"], seed=c["seed"], kind=c["kind"], hold=hold,
                                           ego="ego_%d" % k, ped="ped_%d" % k, ego_sees=True, lost=100.0, seen_again=102.7, gap=2.7, occlusion=True, way=way,
                                           v2x_gap_mean=0.99, v2x_gap_min=0.95, v2x_gap_end=0.99, own_gap_end=0.4, own_gap_min=0.4,
                                           cpm_rows_in_gap=30 + c["pen"] // 2, groups_max=2, event=k))
    (root / "d3").mkdir(parents=True, exist_ok=True)
    (root / "d2").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(metrics).to_csv(root / "d3" / "metrics.csv", index=False)
    pd.DataFrame(bins).to_csv(root / "d3" / "bins.csv", index=False)
    pd.DataFrame(events).to_csv(root / "d3" / "events.csv", index=False)
    pd.DataFrame(facts).to_csv(root / "d2" / "gridcheck_runs.csv", index=False)
    pd.DataFrame([dict(cls="passenger_car", runs=72, rows=1000, points=50000, q_assumed=4.0, q_fitted=0.27, ll_assumed=-1.0, ll_fitted=-0.5,
                       ce_assumed=0.2, ce_fitted=0.04, half_life_assumed=1.0, half_life_fitted=1.9, fitted=True, at_edge=False)]).to_csv(
        root / "d3" / "qfit.csv", index=False)
    return root


def test_mean_ci():
    m, lo, hi, n = database.mean_ci([1.0, 2.0, 3.0, 4.0])
    assert m == 2.5 and n == 4 and lo == pytest.approx(2.5 - 2.0541, abs=0.01) and hi == pytest.approx(2.5 + 2.0541, abs=0.01)
    assert database.mean_ci([np.nan, 2.0]) == (2.0, np.nan, np.nan, 1) or np.isnan(database.mean_ci([np.nan, 2.0])[1])
    assert database.mean_ci([]) [3] == 0


def test_the_database_on_handmade_tables(tmp_path):
    results = fake_results(tmp_path / "results")
    tables = database.build(str(results))
    runs = tables["runs"]
    assert len(runs) == 432 * 5 + 72 * 5 * 0 and "pen_share" in runs.columns and runs["pen_share"].notna().all()
    cells = tables["cells"]
    assert len(cells) == 36 * 5 and (cells["runs"] == 10).all() and (cells["hold"] == 5.0).all()         # the calibration seeds left out
    b3 = cells[cells["way"] == "B3"].set_index(["density", "pen", "msg"])
    assert (b3["calibration_error_lo"] <= b3["calibration_error"]).all() and (b3["calibration_error"] <= b3["calibration_error_hi"]).all()
    h1 = tables["h1"]
    assert len(h1) == 36 * 4 and (h1[h1["against"] == "B1"]["gain"].round(2) == 0.5).all() and (h1[h1["against"].isin(["B0", "B1"])]["verdict"] == "PASS").all()
    assert (h1[h1["against"] == "B0"]["not_worse"] == "PASS").all() and (h1[h1["against"] == "B4"]["verdict"] == "").all()
    assert h1[h1["against"] == "B0"]["gain"].round(2).eq(0.75).all()
    o = tables["h1_overall"].set_index("against")
    assert o.loc["B1", "verdict"] == "PASS" and o.loc["B1", "runs"] == 360 and o.loc["B1", "gain"] == pytest.approx(0.5, abs=0.01)
    h3 = tables["h3_overall"].set_index("against")
    assert h3.loc["B0", "verdict"] == "PASS" and h3.loc["B0", "gain"] == pytest.approx(0.69, abs=0.01) and h3.loc["B1", "not_worse_share"] == 0.0
    factors = tables["factors"]
    assert set(factors["factor"]) == {"density", "pen", "msg"} and len(factors) == (3 + 4 + 3) * 5
    pen = factors[(factors["factor"] == "pen") & (factors["way"] == "B3")].set_index("level")
    assert pen.loc[100, "awareness_ratio"] > pen.loc[10, "awareness_ratio"] and pen.index.tolist() == [10, 25, 50, 100]      # the order of the grid
    assert len(tables["holds"]) == 0 and "no hold check" in tables["summary"]                                                # no hold check here
    rel = tables["reliability"]
    assert len(rel) == 5 * 10 and rel[rel["way"] == "B3"]["count"].sum() == 360 * 280 and rel["share_of_objects"].round(3).between(0, 1).all()
    assert rel[(rel["way"] == "B0") & (rel["bin"] == 5)]["share_there"].iloc[0] == pytest.approx(0.65)
    assert len(tables["reliability_pen"]) == 4 * 5 * 10
    ev = tables["events"]
    assert len(ev) == 36 * 4 and (ev["runs"] == 10).all() and ev[ev["pen"] == 100]["cpm_rows_in_gap"].iloc[0] > ev[ev["pen"] == 10]["cpm_rows_in_gap"].iloc[0]
    text = tables["summary"]
    assert "H1" in text and "| B1 | 360 | 50.0 % |" in text and "36 of 36 cells pass" in text and "passenger_car" in text and "PASS" in text


def test_a_failing_hypothesis_and_the_hold_check(tmp_path):
    results = fake_results(tmp_path / "results", holds=(5.0, 2.0, 10.0), gain=0.2)
    tables = database.build(str(results))
    h1 = tables["h1_overall"].set_index("against")
    assert h1.loc["B1", "verdict"] == "FAIL" and h1.loc["B0", "verdict"] == "PASS"                   # 20 % against B1, 60 % against B0
    holds = tables["holds"]
    assert sorted(holds["hold"].unique()) == [2.0, 5.0, 10.0] and (holds["runs"] == 72).all()
    by = holds.set_index(["hold", "way"])
    assert by.loc[(10.0, "B3"), "calibration_error"] > by.loc[(2.0, "B3"), "calibration_error"]
    rel = tables["reliability"]
    assert sorted(rel["hold"].unique()) == [2.0, 5.0, 10.0]


def test_the_command(tmp_path):
    results = fake_results(tmp_path / "results")
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.database", "--results", str(results)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = results / "d4"
    assert all((out / name).is_file() for name in ("runs.csv", "cells.csv", "factors.csv", "h1.csv", "h1_overall.csv", "h3.csv", "h3_overall.csv",
                                                      "holds.csv", "reliability.csv", "reliability_pen.csv", "events.csv", "summary.md"))
    assert "# The results database" in proc.stdout and "written:" in proc.stdout
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.database", "--results", str(tmp_path / "nowhere")], capture_output=True, text=True)
    assert proc.returncode == 2 and "ERROR" in proc.stdout


@pytest.mark.skipif(not (ROOT / "results" / "d3" / "metrics.csv").is_file(), reason="the tables of D3 are not there")
def test_the_real_tables_build():
    tables = database.build(str(ROOT / "results"))
    assert len(tables["cells"]) == 36 * 5 and tables["h1_overall"].set_index("against").loc["B1", "runs"] == 360
    assert (tables["h1"][tables["h1"]["against"].isin(["B0", "B1"])]["verdict"] == "PASS").all()      # the result of Nov 4, 2026
