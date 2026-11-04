"""Tests of the figures of the core grid (aoc.analysis.gridfigures, task D5): drawn from the handmade tables of the
database test, and from the real tables when they are there."""
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from test_database import fake_results

from aoc.analysis import database, gridfigures

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytest.importorskip("matplotlib")


@pytest.fixture
def results(tmp_path):
    root = fake_results(tmp_path / "results", holds=(5.0, 2.0, 10.0))
    ages = np.round(np.arange(0.1, 5.01, 0.1), 1)
    curves = []
    for cls, q in (("passenger_car", 0.27), ("heavy_truck", 0.12)):
        for age in ages:
            curves.append(dict(cls=cls, age=age, n=1000, hit_rate=float(np.exp(-q * age)), alpha_assumed=float(np.exp(-4 * q * age)),
                               alpha_fitted=float(np.exp(-q * age))))
    pd.DataFrame(curves).to_csv(root / "d3" / "qfit_curves.csv", index=False)
    assert database.main(["--results", str(root)]) == 0
    return root


def test_every_figure_is_drawn_and_prints_its_numbers(results, tmp_path, capsys):
    out = tmp_path / "figs"
    assert gridfigures.main(["--results", str(results), "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["f02_alpha.pdf", "f05_calibration.pdf", "f07_awareness.pdf", "f08_stale.pdf"]
    assert all(p.stat().st_size > 1000 for p in out.iterdir())
    text = capsys.readouterr().out
    assert text.count("written:") == 4 and "passenger_car" in text and "B3" in text and "cbr_range" in text


def test_the_numbers_of_the_figures(results, tmp_path):
    numbers = gridfigures.f02_alpha(str(results), str(tmp_path / "a.pdf"))
    assert set(numbers) == {"passenger_car"} and numbers["passenger_car"]["q_fitted"] == 0.27          # the truck has no row in qfit.csv
    numbers = gridfigures.f05_reliability(str(results), str(tmp_path / "b.pdf"))
    assert numbers["B0"]["pooled_calibration_error"] == pytest.approx(0.1, abs=0.01) and numbers["B3"]["pooled_calibration_error"] == pytest.approx(0.0, abs=0.01)
    assert numbers["B3"]["top_bin_share_of_objects"] == pytest.approx(100 / 280, abs=0.01)
    numbers = gridfigures.f07_awareness(str(results), str(tmp_path / "c.pdf"))
    assert numbers["B3"]["awareness_ratio"][100] > numbers["B3"]["awareness_ratio"][10]
    assert numbers["B1"]["calibration_error"][10] > numbers["B3"]["calibration_error"][10]
    numbers = gridfigures.f08_stale(str(results), str(tmp_path / "d.pdf"))
    assert numbers["B0"]["stale_rate"]["mean"] == pytest.approx(0.99) and numbers["B1"]["stale_rate"]["max"] == 0.0 and numbers["cbr_range"] == (0.05, 0.05)


def test_without_results_nothing_is_drawn(tmp_path, capsys):
    assert gridfigures.main(["--results", str(tmp_path / "nowhere"), "--out", str(tmp_path / "figs")]) == 0
    text = capsys.readouterr().out
    assert text.count("nothing drawn") == 4 and "written" not in text
    assert gridfigures.main(["--only", "f99", "--results", str(tmp_path), "--out", str(tmp_path)]) == 2


def test_the_figure_scripts(results, tmp_path, monkeypatch):
    """The scripts of figures/ take the results folder on the command line and write the PDF next to themselves."""
    scripts = sorted((ROOT / "figures").glob("f0[2578]_*.py"))
    assert [s.name for s in scripts] == ["f02_alpha.py", "f05_calibration.py", "f07_awareness.py", "f08_stale.py"]
    here = tmp_path / "figures"
    here.mkdir()
    for script in scripts:
        (here / script.name).write_text(script.read_text())
        proc = subprocess.run([sys.executable, str(here / script.name), str(results)], capture_output=True, text=True)
        assert proc.returncode == 0 and "written:" in proc.stdout, proc.stdout + proc.stderr
        assert (here / (script.stem + ".pdf")).is_file()


@pytest.mark.skipif(not (ROOT / "results" / "d4" / "cells.csv").is_file(), reason="the database of D4 is not there")
def test_the_real_figures(tmp_path):
    numbers = gridfigures.f05_reliability(str(ROOT / "results"), str(tmp_path / "f05.pdf"))
    assert numbers["B3"]["pooled_calibration_error"] < numbers["B1"]["pooled_calibration_error"] < numbers["B0"]["pooled_calibration_error"]
    numbers = gridfigures.f08_stale(str(ROOT / "results"), str(tmp_path / "f08.pdf"))
    assert numbers["B3"]["stale_rate"]["max"] < numbers["B0"]["stale_rate"]["min"]                     # H3 against B0, the result of Nov 4, 2026
