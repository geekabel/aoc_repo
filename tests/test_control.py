"""The analysis of the control grid (task E3): the pairs by seed, the cells, the verdicts of H4 and the frontier."""
import json
import os
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis import control, grid

ROOT = pathlib.Path(__file__).resolve().parents[1]
RULES = grid.CONTROL.factors["rule"]


def fake_runs(seeds=(0, 1), density="low", pen=25, loads=(0, 12), noise=0.0):
    """A runs table where etsi_abs sends 80 % of the bytes of etsi for the same time above the target, aoc 40 % with one point less,
    aoc2 80 % with two points more, aocdev 95 % with the same time (so: PASS, PASS, PASS, FAIL against etsi)."""
    bytes_share = {"etsi": 1.0, "etsi_abs": 0.8, "aoc": 0.4, "aoc2": 0.8, "aocdev": 0.95}
    time_diff = {"etsi": 0.0, "etsi_abs": 0.0, "aoc": -0.01, "aoc2": 0.02, "aocdev": 0.0}
    rows = []
    for load in loads:
        for seed in seeds:
            base_bytes = 1000.0 + 100 * seed
            base_cbr = 0.1 + 0.3 * (load > 0)
            for rule in RULES:
                b = base_bytes * bytes_share[rule]
                cbr = base_cbr * (bytes_share[rule] if load == 0 else 1.0) + noise * seed
                rows.append(dict(run=grid.CONTROL.run_number(seed, density=density, pen=pen, rule=rule, load=load), density=density, pen=pen, rule=rule, load=load,
                                 seed=seed, seconds=270.0, senders=10, cpm_rate_hz=2.0 * bytes_share[rule], bytes_per_s=10 * b, bytes_per_s_without_empty=10 * b,
                                 bytes_per_sender=b, objects_per_cpm=1.5, cbr_mean=round(cbr, 4), receivers=5, receivers_exposed=5, pairs=1000,
                                 time_above_target=0.9 + time_diff[rule], known=0.95, time_above_target_receivers=0.9 + time_diff[rule]))
    return pd.DataFrame(rows, columns=control.RUN_COLUMNS)


def test_the_verdict_of_a_pair():
    assert control.verdict(0.8, 0.0) == "PASS"                      # 20 % less load, same time
    assert control.verdict(0.8, -0.01) == "PASS"                    # within one point
    assert control.verdict(0.8, -0.02) == "FAIL"                    # the time dropped by more than a point
    assert control.verdict(0.81, 0.0) == "FAIL"                     # not 20 % less
    assert control.verdict(1.0, 0.02) == "PASS"                     # more time at equal load
    assert control.verdict(1.01, 0.02) == "PASS"                    # equal load within one percent
    assert control.verdict(1.02, 0.02) == "FAIL"
    assert control.verdict(1.0, 0.01) == "FAIL"                     # one point is not more than one point
    assert control.verdict(np.nan, 0.0) == "n/a" and control.verdict(0.5, np.nan) == "n/a"
    assert control.load_measure(0) == "cbr" and control.load_measure(12) == "bytes" and control.load_measure(36) == "bytes"


def test_pairs_cells_and_the_verdict_of_h4():
    runs = fake_runs()
    pairs = control.pairs(runs)
    assert len(pairs) == 2 * 2 * 4 * 2                               # loads x seeds x (rules but the reference) x references
    against_etsi = pairs[pairs["reference"] == "etsi"]
    aoc = against_etsi[(against_etsi["rule"] == "aoc") & (against_etsi["load"] == 12) & (against_etsi["seed"] == 1)].iloc[0]
    assert aoc["bytes_ratio"] == pytest.approx(0.4) and aoc["time_diff"] == pytest.approx(-0.01) and aoc["load_measure"] == "bytes"
    assert aoc["run_ref"] == grid.CONTROL.run_number(1, density="low", pen=25, rule="etsi", load=12)
    # at load 0 the busy ratio is the load side, at load 12 the bytes (there the busy ratio does not move with the rule)
    dev0 = against_etsi[(against_etsi["rule"] == "aocdev") & (against_etsi["load"] == 0)]
    dev12 = against_etsi[(against_etsi["rule"] == "aocdev") & (against_etsi["load"] == 12)]
    assert (dev0["load_measure"] == "cbr").all() and np.allclose(dev0["load_ratio"], 0.95)
    assert np.allclose(dev12["cbr_ratio"], 1.0) and np.allclose(dev12["load_ratio"], 0.95)
    verdicts = against_etsi.groupby("rule")["verdict"].agg(lambda s: set(s))
    assert verdicts["etsi_abs"] == {"PASS"} and verdicts["aoc"] == {"PASS"} and verdicts["aoc2"] == {"PASS"} and verdicts["aocdev"] == {"FAIL"}
    # aoc against etsi_abs: 0.4 / 0.8 = half the bytes, one point less: PASS; aocdev against etsi_abs: more bytes, FAIL
    against_abs = pairs[pairs["reference"] == "etsi_abs"]
    assert set(against_abs[against_abs["rule"] == "aoc"]["verdict"]) == {"PASS"}
    assert set(against_abs[against_abs["rule"] == "aocdev"]["verdict"]) == {"FAIL"}
    assert "etsi_abs" not in set(against_abs["rule"]) and "etsi" in set(against_abs["rule"])
    cells = control.cells(runs)
    assert len(cells) == 2 * 5 and set(cells["seeds"]) == {2}
    c = cells[(cells["rule"] == "aoc") & (cells["load"] == 0)].iloc[0]
    assert c["bytes_per_sender"] == pytest.approx(0.4 * 1050) and c["bytes_per_sender_min"] == pytest.approx(400) and c["bytes_per_sender_max"] == pytest.approx(440)
    assert c["time_above_target"] == pytest.approx(0.89) and c["seed_list"] == "0,1" and c["pairs"] == 2000
    assert np.isfinite(c["bytes_per_sender_lo"])                     # two seeds give an interval
    cp = control.cell_pairs(pairs)
    assert len(cp) == 2 * 4 * 2
    row = cp[(cp["rule"] == "aoc") & (cp["reference"] == "etsi") & (cp["load"] == 12)].iloc[0]
    assert row["verdict"] == "PASS" and row["seed_pairs_pass"] == 2 and row["seeds"] == 2 and row["load_measure"] == "bytes"
    h4 = control.h4_table(cp)
    by = h4.set_index(["rule", "reference"])
    assert by.loc[("aoc", "etsi"), "h4"] == "PASS" and by.loc[("aoc", "etsi"), "cells_pass"] == 2 and by.loc[("aoc", "etsi"), "pass_load_12"] == "1/1"
    assert by.loc[("aocdev", "etsi"), "h4"] == "FAIL" and by.loc[("aocdev", "etsi"), "cells_pass"] == 0
    assert by.loc[("etsi", "etsi_abs"), "h4"] == "FAIL"              # etsi sends more than etsi_abs for the same time
    text = control.format_summary(runs, cells, cp, h4, control.frontier_table(cells))
    assert "H4 per rule" in text and "aocdev" in text and "cells that do not pass" in text


def test_a_cell_passes_on_its_mean_pair_not_on_every_seed():
    runs = fake_runs(seeds=(0, 1), loads=(0,))
    # seed 1 of aoc loses three points: its pair fails; the mean pair over the seeds (two points less) decides the cell
    k = (runs["rule"] == "aoc") & (runs["seed"] == 1)
    runs.loc[k, "time_above_target"] = 0.87
    cp = control.cell_pairs(control.pairs(runs, ("etsi",)))
    row = cp[cp["rule"] == "aoc"].iloc[0]
    assert row["seed_pairs_pass"] == 1 and row["time_diff"] == pytest.approx(-0.02) and row["verdict"] == "FAIL"
    runs.loc[k, "time_above_target"] = 0.886                                                   # seed 1: 1.4 points less, its pair fails
    runs.loc[(runs["rule"] == "aoc") & (runs["seed"] == 0), "time_above_target"] = 0.895       # seed 0: half a point less
    row = control.cell_pairs(control.pairs(runs, ("etsi",)))
    row = row[row["rule"] == "aoc"].iloc[0]
    assert row["seed_pairs_pass"] == 1 and row["time_diff"] == pytest.approx(-0.0095) and row["verdict"] == "PASS"   # the mean is within one point


def test_the_frontier():
    points = pd.DataFrame({"x": [0.1, 0.2, 0.3, 0.2, 0.15, np.nan], "time_above_target": [0.5, 0.7, 0.9, 0.6, 0.7, 0.9]})
    on = control.frontier(points, "x")
    assert on.tolist() == [True, False, True, False, True, False]    # (0.2, 0.7) is beaten by (0.15, 0.7); (0.2, 0.6) by both
    runs = fake_runs()
    front = control.frontier_table(control.cells(runs))
    assert set(front["measure"]) == {"cbr", "bytes"} and set(front["scope"]) == {"all", "load 0", "load 12"}
    bytes_all = front[(front["measure"] == "bytes") & (front["scope"] == "all")]
    assert set(bytes_all["rule"]) == {"aoc", "aoc2"}                  # aoc: fewest bytes; aoc2: the highest time; the others are beaten
    assert bytes_all["x"].is_monotonic_increasing


def test_a_reference_twice_in_a_cell_is_refused():
    runs = pd.concat([fake_runs(seeds=(0,), loads=(0,))] * 2, ignore_index=True)
    with pytest.raises(ValueError, match="twice"):
        control.pairs(runs, ("etsi",))


def test_find_runs_reads_the_folders(tmp_path):
    for n in (0, 7, 239, 240, 5):
        d = tmp_path / ("aoc-control-%d" % n) / "parquet"
        d.mkdir(parents=True)
        if n != 5:                                                    # run 5 has no export summary: not exported yet
            (d / "export_summary.json").write_text(json.dumps({"variables": grid.CONTROL.cell_of_run(n)}) if n < 240 else "{}")
    (tmp_path / "aoc-grid-3" / "parquet").mkdir(parents=True)
    found = control.find_runs(str(tmp_path))
    assert found["run"].tolist() == [0, 7, 239]                       # 240 is out of the design, aoc-grid-3 is another design
    assert found.iloc[1]["rule"] == "etsi" and found.iloc[1]["seed"] == 3 and found.iloc[2]["load"] == 36 and found.iloc[2]["rule"] == "aocdev"
    assert control.find_runs(str(tmp_path / "nowhere")).empty


def test_collect_keeps_the_cache(tmp_path, monkeypatch):
    root, out = tmp_path / "root", tmp_path / "out"
    for n in (0, 1):
        d = root / ("aoc-control-%d" % n) / "parquet"
        d.mkdir(parents=True)
        (d / "export_summary.json").write_text("{}")
    calls = []

    def fake_metrics(folder, run, warmup, range_m, hold):
        calls.append(run)
        row = fake_runs(seeds=(run % 4,), loads=(0,)).iloc[0].to_dict()
        row["run"] = run
        return row

    monkeypatch.setattr(control, "run_metrics", fake_metrics)
    runs = control.collect(str(root), str(out), log=lambda *a: None)
    assert sorted(calls) == [0, 1] and len(runs) == 2 and os.path.isfile(out / "runs.csv")
    calls.clear()
    (root / "aoc-control-4" / "parquet").mkdir(parents=True)
    (root / "aoc-control-4" / "parquet" / "export_summary.json").write_text("{}")
    runs = control.collect(str(root), str(out), log=lambda *a: None)
    assert calls == [4] and runs["run"].tolist() == [0, 1, 4]        # only the new run is read
    import shutil
    shutil.rmtree(root / "aoc-control-1")
    runs = control.collect(str(root), str(out), log=lambda *a: None)
    assert runs["run"].tolist() == [0, 4]                             # a run that is gone leaves the cache


def test_an_unreadable_run_is_reported_not_fatal(tmp_path, monkeypatch):
    root, out = tmp_path / "root", tmp_path / "out"
    d = root / "aoc-control-0" / "parquet"
    d.mkdir(parents=True)
    (d / "export_summary.json").write_text("{}")
    monkeypatch.setattr(control, "run_metrics", lambda *a: (_ for _ in ()).throw(FileNotFoundError("cpm_rx.parquet")))
    messages = []
    runs = control.collect(str(root), str(out), log=messages.append)
    assert runs.empty and any("could not be read" in m for m in messages) and any("cpm_rx.parquet" in m for m in messages)


def test_f09_draws_from_the_cells(tmp_path):
    pytest.importorskip("matplotlib")
    from aoc.analysis import gridfigures
    cells = control.cells(fake_runs(loads=(0, 12, 36)))
    (tmp_path / "e3").mkdir()
    cells.to_csv(tmp_path / "e3" / "cells.csv", index=False)
    numbers = gridfigures.f09_frontier(str(tmp_path), str(tmp_path / "f09.pdf"))
    assert (tmp_path / "f09.pdf").stat().st_size > 1000
    assert numbers["aoc"]["cells"] == 3 and "frontier_bytes" in numbers and numbers["frontier_bytes"].get("aoc", 0) >= 1
    # the script of the figure takes the results folder and writes the PDF next to itself
    here = tmp_path / "figures"
    here.mkdir()
    script = ROOT / "figures" / "f09_control.py"
    (here / script.name).write_text(script.read_text())
    proc = subprocess.run([sys.executable, str(here / script.name), str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 0 and "written:" in proc.stdout and (here / "f09_control.pdf").is_file(), proc.stdout + proc.stderr
    pd.DataFrame(columns=cells.columns).to_csv(tmp_path / "e3" / "cells.csv", index=False)
    with pytest.raises(gridfigures.NoData):
        gridfigures.f09_frontier(str(tmp_path), str(tmp_path / "f09.pdf"))
