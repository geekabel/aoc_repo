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
from aoc.analysis.ealpha import CLASSES, AlphaIntegral, check_ealpha, compare_grid, compare_run, law_ealpha, law_noise, measured_ealpha, run_gaps
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


def link_scenario(period, loss, seed=0, duration=330.0, delay=0.02):
    cars = (Car("object", x=300.0, vx=10.0), Car("sender", x=260.0, vx=10.0), Car("receiver", x=80.0, vx=10.0))
    return Scenario(cars, duration=duration, sensor_range=65.0, cpm_period=period, delay=delay, loss=loss, seed=seed,
                    name="link-T%g-p%g" % (period, loss))


def brute_force_ealpha(folder, par, warmup=10.0, step=0.001):
    """E[alpha] by sampling the age of the newest received report every millisecond, without the gaps machinery."""
    obs = pd.read_parquet(folder / "observations.parquet")
    t_end = float(obs["t_sim"].astype(float).max())
    received = obs[(obs["src"] == "v2x") & (obs["gt_match_id"] != obs["rx_station"])]
    total, count = 0.0, 0
    for _, rows in received.groupby(["rx_station", "tx_station", "gt_match_id"]):
        rows = rows.sort_values("t_rx")
        t_rx, t_meas = rows["t_rx"].to_numpy(dtype=float), rows["t_meas"].to_numpy(dtype=float)
        times = np.arange(warmup, t_end, step)
        newest = np.searchsorted(t_rx, times, side="right") - 1
        first = int(np.searchsorted(t_rx, warmup, side="left"))
        use = newest >= first                                                                               # after the first reception kept
        ages = times[use] - t_meas[newest[use]]
        total += float(np.asarray(a2.alpha(ages, *par)).sum())
        count += int(use.sum())
    return total / count


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


def test_run_gaps_pairs_each_age_at_reception_with_the_gap_that_follows(tmp_path):
    write_run(link_scenario(0.5, 0.3, delay=(0.05, 0.25), duration=60.0), tmp_path / "aoc-link-0" / "parquet")
    row, d0, gaps, window = run_gaps(tmp_path / "aoc-link-0" / "parquet", warmup=10.0)
    obs = pd.read_parquet(tmp_path / "aoc-link-0" / "parquet" / "observations.parquet")
    t_end = float(obs["t_sim"].astype(float).max())
    by_hand_d0, by_hand_gaps = [], []
    received = obs[(obs["src"] == "v2x") & (obs["gt_match_id"] != obs["rx_station"])]
    for _, rows in received.groupby(["rx_station", "tx_station", "gt_match_id"], sort=True):
        rows = rows.sort_values("t_rx")
        rows = rows[rows["t_rx"] >= 10.0]
        t_rx = rows["t_rx"].to_numpy(dtype=float)
        by_hand_gaps.extend(np.diff(np.append(t_rx, t_end)).tolist())
        by_hand_d0.extend((rows["t_rx"] - rows["t_meas"]).to_numpy(dtype=float).tolist())                   # the age at reception of each row
    assert gaps.tolist() == pytest.approx(by_hand_gaps, abs=1e-9) and d0.tolist() == pytest.approx(by_hand_d0, abs=1e-9)
    assert window == pytest.approx(sum(by_hand_gaps)) and 0.05 <= d0.min() and d0.max() <= 0.25 and d0.std() > 0.03
    assert row["T"] == 0.5 and row["p"] == 0.3


def test_measure_matches_a_brute_force_sample_with_a_variable_delay(tmp_path):
    """The gaps machinery against a plain sample of the age every millisecond, on a stream whose d0 varies between 0.01 and 0.30 s."""
    folder = tmp_path / "aoc-link-0" / "parquet"
    write_run(link_scenario(0.5, 0.3, delay=(0.01, 0.30), duration=120.0, seed=4), folder)
    rows = compare_run(folder, draws=20, classes={"car (moving)": CAR, "pedestrian": PED})
    for r in rows:
        assert r["measured"] == pytest.approx(brute_force_ealpha(folder, CLASSES[r["cls"]]), abs=5e-4)
    # with d0 forced to zero the measure would be above by several percent: the pairing with d0 matters
    row, d0, gaps, window = run_gaps(folder)
    integral = AlphaIntegral(CAR, horizon=20.0)
    assert measured_ealpha(d0, gaps, integral) < measured_ealpha(0.0 * d0, gaps, integral) - 0.02


def test_fixed_delay_run_against_the_exact_form(tmp_path):
    """No loss, delay 0.13 s (the d0 of the note): the measure equals the exact form at that d0 and differs from the one at d0 = 0."""
    folder = tmp_path / "aoc-link-0" / "parquet"
    write_run(link_scenario(1.0, 0.0, delay=0.13, duration=120.0), folder)
    rows = compare_run(folder, draws=20, classes={"car (moving)": CAR})
    assert rows[0]["d0_median"] == pytest.approx(0.13, abs=1e-4) and abs(rows[0]["gap_exact"]) < 0.002
    at_zero = law_ealpha(1.0, 0.0, CAR, 0.0)["exact"]
    assert abs(rows[0]["measured"] / at_zero - 1.0) > 0.03


def test_classes_are_the_ones_of_the_note():
    assert all(CLASSES[name] == a2.CLASSES[name][:4] for name in a2.CLASSES) and set(CLASSES) == set(a2.CLASSES)


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
