"""Tests of the E[alpha] check against the formulas of the note (task C3), on synthetic single links."""
import os
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from scipy import integrate

from aoc.analysis import ealpha
from aoc.analysis.ealpha import CLASSES, AlphaIntegral, check_ealpha, compare_grid, compare_run, law_ealpha, law_noise, measured_ealpha
from aoc.io.synthetic import Car, Scenario, write_run
from aoc.theory import averages as a3
from aoc.theory import reliability as a2

PED, CAR = CLASSES["pedestrian"], CLASSES["car (moving)"]


def has_matplotlib() -> bool:
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return False
    return True


def link_scenario(period, loss, seed=0, duration=330.0):
    cars = (Car("object", x=300.0, vx=10.0), Car("sender", x=260.0, vx=10.0), Car("receiver", x=80.0, vx=10.0))
    return Scenario(cars, duration=duration, sensor_range=65.0, cpm_period=period, delay=0.02, loss=loss, seed=seed,
                    name="link-T%g-p%g" % (period, loss))


def test_integral_of_alpha_against_quadrature():
    integral = AlphaIntegral(PED, horizon=8.0)
    for lo, hi in ((0.0, 0.5), (0.02, 1.37), (2.0, 7.5), (0.0, 0.0)):
        exact = integrate.quad(lambda d: float(a2.alpha(d, *PED)), lo, hi)[0]
        assert integral.between(lo, hi) == pytest.approx(exact, abs=2e-5)
    assert integral.between([0.0, 1.0], [1.0, 2.0]).tolist() == pytest.approx(
        [integrate.quad(lambda d: float(a2.alpha(d, *PED)), 0.0, 1.0)[0], integrate.quad(lambda d: float(a2.alpha(d, *PED)), 1.0, 2.0)[0]], abs=2e-5)


def test_measured_ealpha_by_hand():
    integral = AlphaIntegral(CAR, horizon=10.0)
    d0, gaps = np.array([0.1, 0.05, 0.1]), np.array([1.0, 2.0, 0.5])
    by_hand = sum(integrate.quad(lambda d: float(a2.alpha(d, *CAR)), a, a + g)[0] for a, g in zip(d0, gaps)) / gaps.sum()
    assert measured_ealpha(d0, gaps, integral) == pytest.approx(by_hand, abs=1e-5)
    assert np.isnan(measured_ealpha(np.array([]), np.array([]), integral))
    flat = AlphaIntegral((0.3, 0.0, 0.0, 0.0), horizon=10.0)                                              # no motion: alpha is a constant
    assert measured_ealpha(d0, gaps, flat) == pytest.approx(float(a2.alpha(0.0, 0.3, 0.0, 0.0, 0.0)), abs=1e-9)


def test_the_three_forms_and_the_noise():
    law = law_ealpha(0.1, 0.0, PED, 0.0)
    assert law["exact"] == pytest.approx(integrate.quad(lambda a: float(a2.alpha(a, *PED)), 0.0, 0.1)[0] / 0.1, abs=1e-6)
    for period in (1.0, 0.5, 0.2, 0.1):
        for loss in (0.0, 0.1, 0.3, 0.5):
            law = law_ealpha(period, loss, PED, 0.05)
            assert abs(law["plateau"] / law["exact"] - 1.0) < 0.03                                          # the plateau surrogate, A3
            assert law["expo"] <= law["exact"] + 1e-9                                                      # the exponential one is pessimistic
    integral = AlphaIntegral(PED, horizon=20.0)
    assert law_noise(1.0, 0.0, 640.0, np.array([0.02]), integral, draws=50) == pytest.approx(0.0, abs=1e-9)
    assert 0.0 < law_noise(1.0, 0.5, 640.0, np.array([0.02]), integral, draws=100) < 0.1
    assert np.isnan(law_noise(1.0, 0.5, 0.5, np.array([0.02]), integral))


def test_one_link_against_the_law(tmp_path):
    write_run(link_scenario(0.5, 0.3), tmp_path / "aoc-link-0" / "parquet")
    rows = compare_run(tmp_path / "aoc-link-0" / "parquet", draws=200)
    assert [r["cls"] for r in rows] == list(CLASSES) and all(r["d0_median"] == pytest.approx(0.02, abs=1e-4) for r in rows)
    assert all(r["window_s"] == pytest.approx(2 * 320.0, abs=2.0) for r in rows)
    for r in rows:
        assert abs(r["gap_exact"]) < 3 * r["noise"] + 0.01 and abs(r["gap_plateau"]) < 0.05
        assert 0.0 < r["measured"] <= 1.0 and r["expo"] <= r["exact"] + 1e-4
    assert check_ealpha(pd.DataFrame(rows)) == []
    # the same run with settings that lie about the loss, and a systematic error over several runs
    truck = next(r for r in rows if r["cls"] == "truck (moving)")                                          # a class whose curve falls within 2 s
    liar = dict(truck, p=0.0)
    law = law_ealpha(0.5, 0.0, CLASSES["truck (moving)"], 0.02)
    liar.update(exact=round(law["exact"], 4), gap_exact=round(liar["measured"] / law["exact"] - 1.0, 4), noise=0.0)
    assert any("E[alpha] measured" in p for p in check_ealpha(pd.DataFrame([liar])))
    biased = pd.DataFrame([dict(rows[0], gap_exact=0.04, noise=0.005) for _ in range(6)])
    assert any("systematic" in p for p in check_ealpha(biased))
    assert check_ealpha(biased.assign(gap_exact=[0.04, -0.04, 0.03, -0.03, 0.01, -0.01])) == []


def test_grid_command_and_figure(tmp_path, capsys):
    k = 0
    for period in (0.5, 0.2):
        for loss in (0.0, 0.3):
            write_run(link_scenario(period, loss, seed=k, duration=200.0), tmp_path / ("aoc-link-%d" % k) / "parquet")
            k += 1
    table = compare_grid(tmp_path, draws=100, classes={"pedestrian": PED, "car (moving)": CAR})
    assert len(table) == 8 and check_ealpha(table) == []
    assert (table.loc[table["p"] == 0.0, "gap_exact"].abs() < 0.002).all()                              # no loss: no noise, the integral itself
    figure = tmp_path / "f4.png"
    assert ealpha.main([str(tmp_path), "--out", str(figure), "--table", str(tmp_path / "ealpha.csv"), "--draws", "50"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("C3 E[ALPHA] CHECK") and "result:              PASS" in text and "class pedestrian" in text
    assert len(pd.read_csv(tmp_path / "ealpha.csv")) == 4 * len(CLASSES)
    if has_matplotlib():
        assert figure.stat().st_size > 1000 and "figure:" in text
    else:
        assert not figure.exists()
    assert ealpha.main([str(tmp_path / "nothing")]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ealpha.main([str(empty)]) == 3
    # the figure script, run from a copy so that it writes in the temporary folder
    script = tmp_path / "f04_ealpha.py"
    shutil.copy(pathlib.Path(__file__).parents[1] / "figures" / "f04_ealpha.py", script)
    env = {**os.environ, "MPLBACKEND": "Agg"}
    done = subprocess.run([sys.executable, str(script), str(tmp_path / "nowhere")], capture_output=True, text=True, env=env)
    assert done.returncode == 0 and "nothing drawn" in done.stdout
    done = subprocess.run([sys.executable, str(script), str(tmp_path)], capture_output=True, text=True, env=env)
    assert done.returncode == 0 and (tmp_path / "f04_ealpha.csv").exists(), done.stdout + done.stderr
    if has_matplotlib():
        assert "written:" in done.stdout and (tmp_path / "f04_ealpha.pdf").stat().st_size > 1000


def test_a3_results_are_consistent():
    """The closed form of the plateau surrogate matches the exact law within 3% on the grid of the plan, as A3 says (1.5% at d0 = 0.13 s)."""
    for name, par in CLASSES.items():
        dd0, tau2 = a2.plateau_surrogate(*par)
        for period in (1.0, 0.5, 0.2, 0.1):
            for loss in (0.0, 0.1, 0.3, 0.5):
                exact = a3.e_exact_periodic(period, loss, par, a2.D0)
                plateau = a3.e_plateau_periodic(period, loss, dd0, tau2, a2.D0)
                assert abs(plateau / exact - 1.0) < 0.03, (name, period, loss)
