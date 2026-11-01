"""Tests of the calibration of the motion value q (aoc.analysis.qfit, task D3)."""
import json
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis import grid, qfit
from aoc.fusion.alignment import Q_BY_CLASS
from aoc.fusion.discounting import alpha_of_age
from aoc.io.synthetic import Car, Scenario, write_run


def report_frame(n: int, vx: float, true_vx: float = 1.0, sigma: float = 0.3) -> tuple[pd.DataFrame, dict]:
    """n reports of one object that really moves at true_vx along x, each report with the speed vx and the noise covariance."""
    t_meas = np.linspace(1.0, 10.0, n)
    rows = pd.DataFrame({"x": true_vx * t_meas, "y": 0.0, "vx": vx, "vy": 0.0, "t_meas": t_meas, "gt_match_id": "o1",
                         "P11": sigma**2, "P12": 0.0, "P22": sigma**2, "Pv11": 0.25, "Pv22": 0.25})
    tt = np.arange(0.0, 20.0, 0.1)
    arrays = {"o1": (tt, true_vx * tt, np.zeros_like(tt))}
    return rows, arrays


def test_hits_of_moves_the_report_forward():
    rows, arrays = report_frame(5, vx=1.0)                                      # the right speed: every prediction is exact
    hit, spread, valid = qfit.hits_of(rows, arrays)
    assert hit.shape == (5, len(qfit.AGES)) and hit.all() and valid.all()
    assert np.allclose(spread[:, 0], 0.09 + 0.01 * 0.25) and (np.diff(spread, axis=1) > 0).all()     # P + age^2 Pv, growing
    rows, arrays = report_frame(5, vx=3.0)                                      # 2 m/s off: wrong after 0.75 s
    hit, spread, valid = qfit.hits_of(rows, arrays)
    assert hit[:, qfit.AGES <= 0.7].all() and not hit[:, qfit.AGES >= 0.8].any()
    rows, arrays = report_frame(3, vx=1.0)
    rows["t_meas"] = [1.0, 14.0, 19.0]                                          # the object leaves at 19.9 s: later uses are not valid
    hit, spread, valid = qfit.hits_of(rows, arrays)
    assert valid[0].all() and valid[1].all() and not valid[2].all() and valid[2, 0]


def test_fit_q_recovers_a_known_motion_value():
    rng = np.random.default_rng(3)
    n = 4000
    age = np.tile(qfit.AGES, n // len(qfit.AGES))
    spread = np.full(age.shape, 0.09)
    for q_true in (0.05, 1.0):
        alpha = alpha_of_age(spread, age, np.full(age.shape, q_true))
        hit = rng.random(age.shape) < alpha
        q, ll, at_edge = qfit.fit_q(hit, spread, age)
        assert 0.6 * q_true < q < 1.6 * q_true and not at_edge, (q_true, q)
        assert ll >= qfit.log_likelihood(q_true, hit, spread, age) - 1e-6
        assert qfit.calibration_error(alpha_of_age(spread, age, np.full(age.shape, q)), hit) < 0.05
        # the summed form gives the same fit
        table = qfit.summarise(hit, spread, age)
        hits, n, sp, ag = qfit.expand(table)
        q2, ll2, _ = qfit.fit_q(hits, sp, ag, n=n)
        assert q2 == pytest.approx(q, rel=1e-3) and ll2 == pytest.approx(ll, rel=1e-6) and len(table) == len(qfit.AGES)
    hit = np.ones(200, dtype=bool)                                                  # every report always right: q at the lower edge
    assert qfit.fit_q(hit, np.full(200, 0.09), np.tile(qfit.AGES[:10], 20))[2]


def grid_run(folder: pathlib.Path, n: int) -> pathlib.Path:
    """A synthetic run (cars at constant speed: the assumed q is far too careful) in the layout of the grid."""
    cars = (Car("e1", x=-120.0, y=0.0, vx=12.0), Car("e2", x=-170.0, y=3.5, vx=12.0), Car("w1", x=120.0, y=-3.5, vx=-12.0),
            Car("ped_1", x=0.0, y=10.0, vx=1.0, cls="pedestrian"), Car("n1", x=5.0, y=-100.0, vy=10.0), Car("s1", x=-5.0, y=120.0, vy=-10.0))
    folder.mkdir(parents=True, exist_ok=True)
    write_run(Scenario(cars, duration=20.0, sensor_range=150.0, cpm_period=0.5, delay=0.05, seed=n, name="synthetic-q-%d" % n), folder)
    info = json.loads((folder / "export_summary.json").read_text(encoding="utf-8"))
    c = grid.cell_of_run(n)
    info["variables"] = dict(density=c["density"], pen=str(c["pen"]), msg=c["msg"])
    info["seed"] = c["seed"]
    (folder / "export_summary.json").write_text(json.dumps(info), encoding="utf-8")
    return folder


def test_read_run_and_fit_classes_on_a_synthetic_run(tmp_path):
    folder = grid_run(tmp_path / "aoc-grid-10" / "parquet", 10)
    part = qfit.read_run(str(folder), receivers=6, rows_per_class=500, warm_up=2.0)
    assert part["run"] == 10 and set(part["classes"]) == {"passenger_car", "pedestrian"}
    car = part["classes"]["passenger_car"]
    assert car["rows"] == 500 and set(car["table"].columns) == {"age", "spread", "hits", "n"} and car["table"]["n"].sum() <= 500 * len(qfit.AGES)
    table, curves = qfit.fit_classes([part])
    t = table.set_index("cls")
    assert t.loc["passenger_car", "fitted"] and t.loc["passenger_car", "q_fitted"] < Q_BY_CLASS["passenger_car"]      # constant speed: less motion than assumed
    assert t.loc["passenger_car", "ll_fitted"] >= t.loc["passenger_car", "ll_assumed"]
    assert t.loc["passenger_car", "ce_fitted"] <= t.loc["passenger_car", "ce_assumed"]
    assert t.loc["passenger_car", "half_life_fitted"] > t.loc["passenger_car", "half_life_assumed"]
    c = curves[curves["cls"] == "passenger_car"].sort_values("age")
    assert len(c) == len(qfit.AGES) and c["hit_rate"].iloc[0] > 0.9 and (c["n"] > 0).all()
    assert "--q passenger_car=" in qfit.q_options(table) and "pedestrian=" in qfit.q_options(table)
    text = qfit.format_table(table)
    assert "passenger_car" in text and "q in m^2/s^3" in text
    assert t.loc["pedestrian", "at_edge"] and "WARNING: the fit of pedestrian" in text          # a walker at constant speed: q at the lower edge
    moving = qfit.read_run(str(folder), receivers=6, rows_per_class=500, warm_up=2.0, min_speed=5.0)
    assert "pedestrian" not in moving["classes"] and "passenger_car" in moving["classes"]                     # the pedestrian walks at 1 m/s


def test_the_command_on_a_small_grid(tmp_path):
    grid_run(tmp_path / "aoc-grid-10" / "parquet", 10)                                    # seed 10: calibration
    grid_run(tmp_path / "aoc-grid-11" / "parquet", 11)                                    # seed 11: calibration
    grid_run(tmp_path / "aoc-grid-0" / "parquet", 0)                                      # seed 0: evaluation, left out
    out, curves, fig = tmp_path / "q.csv", tmp_path / "curves.csv", tmp_path / "q.png"
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.qfit", str(tmp_path), "--jobs", "1", "--warmup", "2", "--rows", "500",
                           "--receivers", "6", "--out", str(out), "--curves", str(curves), "--figure", str(fig)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "reading 2 run(s)" in proc.stdout and "for aoc-replay-grid: --q" in proc.stdout
    table = pd.read_csv(out)
    assert len(table) == 2 and table["runs"].tolist() == [2, 2] and table["fitted"].all()
    assert len(pd.read_csv(curves)) == 2 * len(qfit.AGES)
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        assert "not drawn" in proc.stdout
    else:
        assert fig.is_file() and fig.stat().st_size > 5000
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.qfit", str(tmp_path), "--jobs", "1", "--seeds", "7"], capture_output=True, text=True)
    assert proc.returncode == 1 and "no exported calibration run" in proc.stdout
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.qfit", str(tmp_path), "--jobs", "1", "--runs", "0"], capture_output=True, text=True)
    assert proc.returncode == 2 and "an evaluation run" in proc.stdout                                      # the separation of the seeds holds


def test_receivers_of():
    info = dict(receivers=["flow.2", "ego_3", "flow.1", "ego_1", "helper_a_1"])
    assert qfit.receivers_of("", info, 3) == ["ego_1", "ego_3", "flow.1"]
    assert qfit.receivers_of("", {}, 3) == []
