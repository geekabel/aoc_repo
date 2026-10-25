"""Tests of the age check against the law of the note (task C2), on synthetic single links."""
import numpy as np
import pandas as pd
import pytest

from aoc.analysis import ages
from aoc.analysis.ages import GRID, cdf_of_gaps, check_ages, compare_grid, compare_run, gaps_of_window, law_noise, mean_of_gaps, theory_cdf
from aoc.io.synthetic import Car, Scenario, write_run
from aoc.theory.averages import mean_age_periodic


def link_scenario(period, loss, seed=0, duration=330.0):
    cars = (Car("object", x=300.0, vx=10.0), Car("sender", x=260.0, vx=10.0), Car("receiver", x=80.0, vx=10.0))
    return Scenario(cars, duration=duration, sensor_range=65.0, cpm_period=period, delay=0.02, loss=loss, seed=seed,
                    name="link-T%g-p%g" % (period, loss))


def test_gaps_cdf_and_mean_by_hand():
    assert gaps_of_window(np.array([5.0, 12.0, 13.5]), 20.0, t_start=10.0).tolist() == [1.5, 6.5]
    assert gaps_of_window(np.array([5.0]), 20.0, t_start=10.0).tolist() == [] and gaps_of_window(np.array([]), 20.0).tolist() == []
    gaps = np.array([1.0, 1.0, 2.0])
    grid = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 3.0])
    assert cdf_of_gaps(gaps, grid).tolist() == pytest.approx([0.0, 1.5 / 4, 3 / 4, 3.5 / 4, 1.0, 1.0])
    assert mean_of_gaps(gaps) == pytest.approx((1 + 1 + 4) / (2 * 4))
    assert np.isnan(cdf_of_gaps(np.array([]), grid)).all() and np.isnan(mean_of_gaps(np.array([])))


def test_law_and_its_noise():
    assert theory_cdf(np.array([0.0, 0.25, 0.5, 1.0]), 0.5, 0.0).tolist() == pytest.approx([0.0, 0.5, 1.0, 1.0])        # no loss: uniform on [0, T]
    assert theory_cdf(np.array([0.0, 1.0, 2.0]), 1.0, 0.5).tolist() == pytest.approx([0.0, 0.5, 0.75])                # p^k left after k periods
    sd, ks = law_noise(1.0, 0.0, 640.0, draws=50)
    assert sd == pytest.approx(0.0, abs=1e-9) and ks == pytest.approx(0.0, abs=1e-9)                                  # no loss: no noise
    sd, ks = law_noise(1.0, 0.3, 640.0, draws=200)
    assert 0.03 < sd / mean_age_periodic(1.0, 0.3) < 0.07 and 0.02 < ks < 0.08
    assert all(np.isnan(v) for v in law_noise(1.0, 0.3, 0.5))


def test_one_link_follows_the_law(tmp_path):
    write_run(link_scenario(0.5, 0.3), tmp_path / "aoc-link-0" / "parquet")
    row = compare_run(tmp_path / "aoc-link-0" / "parquet", draws=100)
    assert row["T"] == 0.5 and row["p"] == 0.3 and row["links"] == 2 and row["receptions"] > 800
    assert row["mean_law"] == pytest.approx(mean_age_periodic(0.5, 0.3), abs=1e-4)
    assert abs(row["rel_gap"]) < 3 * row["rel_noise"] + 0.01 and row["ks"] < 0.05
    assert row["d0_median"] == pytest.approx(0.02, abs=1e-4) and row["d0_p95"] == pytest.approx(0.02, abs=1e-4)
    assert len(row["cdf"]) == len(GRID) and row["cdf"][0] == 0.0 and row["cdf"][-1] == pytest.approx(1.0)
    assert check_ages(pd.DataFrame([row])) == []
    # the same run with settings that lie about the loss
    liar = dict(row, p=0.0, mean_law=round(mean_age_periodic(0.5, 0.0), 4), rel_noise=0.0, ks_noise=0.0)
    liar["rel_gap"] = round(liar["mean_measured"] / liar["mean_law"] - 1.0, 4)
    liar["ks"] = round(float(np.max(np.abs(row["cdf"] - theory_cdf(GRID, 0.5, 0.0)))), 4)
    problems = check_ages(pd.DataFrame([liar]))
    assert any("mean age" in p for p in problems) and any("distribution functions differ" in p for p in problems)


def test_grid_and_command(tmp_path, capsys):
    k = 0
    for period in (0.5, 0.2):
        for loss in (0.0, 0.3):
            write_run(link_scenario(period, loss, seed=k, duration=200.0), tmp_path / ("aoc-link-%d" % k) / "parquet")
            k += 1
    table = compare_grid(tmp_path, draws=100)
    assert len(table) == 4 and list(table["p"]) == [0.0, 0.0, 0.3, 0.3] and list(table["T"]) == [0.5, 0.2, 0.5, 0.2]
    assert check_ages(table) == []
    assert (table.loc[table["p"] == 0.0, "rel_gap"].abs() < 0.01).all()                       # no loss: the age is uniform on [0, T]
    figure = tmp_path / "f3.png"
    assert ages.main([str(tmp_path), "--out", str(figure), "--table", str(tmp_path / "ages.csv"), "--draws", "100"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("C2 AGE CHECK") and "result:              PASS" in text and figure.stat().st_size > 1000
    assert len(pd.read_csv(tmp_path / "ages.csv")) == 4
    assert ages.main([str(tmp_path / "nothing")]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ages.main([str(empty)]) == 3


def test_figure_script(tmp_path):
    import os
    import pathlib
    import shutil
    import subprocess
    import sys

    script = tmp_path / "f03_ages.py"                                                     # a copy: the script writes next to itself
    shutil.copy(pathlib.Path(__file__).parents[1] / "figures" / "f03_ages.py", script)
    env = {**os.environ, "MPLBACKEND": "Agg"}
    done = subprocess.run([sys.executable, str(script), str(tmp_path / "nowhere")], capture_output=True, text=True, env=env)
    assert done.returncode == 0 and "nothing drawn" in done.stdout
    runs = tmp_path / "runs"
    write_run(link_scenario(0.5, 0.1, duration=100.0), runs / "aoc-link-0" / "parquet")
    done = subprocess.run([sys.executable, str(script), str(runs)], capture_output=True, text=True, env=env)
    assert done.returncode == 0 and "written:" in done.stdout, done.stdout + done.stderr
    assert (tmp_path / "f03_ages.pdf").stat().st_size > 1000 and (tmp_path / "f03_ages.csv").exists()
