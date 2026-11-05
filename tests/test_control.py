"""The analysis of the control grid (task E3): the pairs by seed, the conditions, the verdicts of H4, the frontier and the cache."""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis import control, grid

ROOT = pathlib.Path(__file__).resolve().parents[1]
RULES = grid.CONTROL.factors["rule"]
BYTES_SHARE = {"etsi": 1.0, "etsi_abs": 0.8, "aoc": 0.4, "aoc2": 0.8, "aocdev": 0.95}
TIME_DIFF = {"etsi": 0.0, "etsi_abs": 0.0, "aoc": -0.01, "aoc2": 0.02, "aocdev": 0.0}


def fake_runs(seeds=(0, 1), densities=("low",), pens=(25,), loads=(0, 12), rules=RULES):
    """A runs table where etsi_abs sends 80 % of the bytes of etsi for the same time above the target, aoc 40 % with one point less,
    aoc2 80 % with two points more, aocdev 95 % with the same time (so: PASS, PASS, PASS, FAIL against etsi)."""
    rows = []
    for density in densities:
        for pen in pens:
            for load in loads:
                for seed in seeds:
                    base_bytes = 1000.0 + 100 * seed
                    base_cbr = 0.1 + 0.3 * (load > 0)
                    for rule in rules:
                        b = base_bytes * BYTES_SHARE[rule]
                        cbr = base_cbr * (BYTES_SHARE[rule] if load == 0 else 1.0)
                        rows.append(dict(run=grid.CONTROL.run_number(seed, density=density, pen=pen, rule=rule, load=load), density=density, pen=pen,
                                         rule=rule, load=load, seed=seed, seconds=270.0, senders=10, present_s=2000.0, cpm_rate_hz=2.0 * BYTES_SHARE[rule],
                                         bytes_per_s=10 * b, bytes_per_s_without_empty=10 * b, bytes_per_sender=b, objects_per_cpm=1.5,
                                         cbr_mean=round(cbr, 4), receivers=5, receivers_exposed=5, pairs=1000, time_above_target=0.9 + TIME_DIFF[rule],
                                         known=0.95, time_above_target_receivers=0.9 + TIME_DIFF[rule], exported=1.0, warmup=60.0, range_m=200.0, hold=5.0))
    return pd.DataFrame(rows, columns=control.RUN_COLUMNS)


def full_grid(seeds=(0, 1, 2, 3)):
    return fake_runs(seeds, grid.CONTROL.factors["density"], grid.CONTROL.factors["pen"], grid.CONTROL.factors["load"])


def test_the_verdict_of_a_pair():
    assert control.verdict(0.8, 0.0) == "PASS"                      # 20 % less load, same time
    assert control.verdict(0.8, -0.01) == "PASS"                    # within one point
    assert control.verdict(0.8, 0.89 - 0.9) == "PASS"               # one point computed in floating point is one point
    assert control.verdict(0.8, -0.02) == "FAIL"                    # the time dropped by more than a point
    assert control.verdict(0.81, 0.0) == "FAIL"                     # not 20 % less
    assert control.verdict(1.0, 0.02) == "PASS"                     # more time at equal load
    assert control.verdict(1.01, 0.02) == "PASS"                    # equal load within one percent
    assert control.verdict(1.02, 0.02) == "FAIL"
    assert control.verdict(1.0, 0.01) == "FAIL"                     # one point is not more than one point
    assert control.verdict(np.nan, 0.0) == "n/a" and control.verdict(0.5, np.nan) == "n/a"
    assert control.load_measure(0) == "cbr" and control.load_measure(12) == "bytes" and control.load_measure(36) == "bytes"
    from aoc.analysis import rulecheck
    assert control.verdict is rulecheck.h4_verdict                   # one function for aoc-control and aoc-rulecheck --against


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
    assert row["bytes_ratio"] == pytest.approx(0.4) and row["load_ratio_hi"] == pytest.approx(0.4)
    assert row["verdict_ci"] == "PASS"                                 # no spread: the interval is a point
    h4 = control.h4_table(cp, pairs)
    by = h4.set_index(["rule", "reference"])
    assert by.loc[("aoc", "etsi"), "conditions_pass"] == 2 and by.loc[("aoc", "etsi"), "pass_load_12"] == "1/1" and by.loc[("aoc", "etsi"), "pass_load_36"] == "0/0"
    assert by.loc[("aoc", "etsi"), "h4"] == "incomplete (2/12)"      # two of the twelve conditions: no verdict on H4 yet
    assert by.loc[("aoc", "etsi"), "seed_pairs"] == 4 and by.loc[("aoc", "etsi"), "seed_pairs_pass"] == 4
    assert by.loc[("aocdev", "etsi"), "conditions_pass"] == 0
    assert np.isnan(by.loc[("aoc", "etsi"), "p_less_load"])          # four seed pairs are too few for the test
    text = control.format_summary(runs, cells, cp, h4, control.frontier_table(cells))
    assert "H4 per rule" in text and "aocdev" in text and "conditions that do not pass" in text and "within each condition" in text


def test_h4_on_the_full_grid():
    runs = full_grid()
    pairs = control.pairs(runs)
    h4 = control.h4_table(control.cell_pairs(pairs), pairs).set_index(["rule", "reference"])
    assert h4.loc[("aoc", "etsi"), "h4"] == "PASS" and h4.loc[("aoc", "etsi"), "conditions"] == 12 and h4.loc[("aoc", "etsi"), "seed_pairs"] == 48
    assert h4.loc[("aocdev", "etsi"), "h4"] == "FAIL" and h4.loc[("etsi", "etsi_abs"), "h4"] == "FAIL"     # etsi sends more than etsi_abs for the same time
    assert h4.loc[("aoc", "etsi"), "p_less_load"] < 0.001 and h4.loc[("aoc", "etsi"), "p_time_held"] == 1.0   # the time is exactly one point under: never above
    assert h4.loc[("aoc2", "etsi"), "p_time_held"] < 0.001 and h4.loc[("aocdev", "etsi"), "p_less_load"] == 1.0
    assert h4.loc[("aoc", "etsi"), "load_ratio_mean"] == pytest.approx(0.4, abs=1e-3)


def test_a_condition_passes_on_its_mean_pair_and_the_interval_is_stricter():
    runs = fake_runs(seeds=(0, 1), loads=(0,))
    # seed 1 of aoc loses three points: its pair fails; the mean pair over the seeds (two points less) decides the condition
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
    assert row["time_diff_lo"] < -0.01 and row["verdict_ci"] == "FAIL"                         # the paired interval is not
    one = control.cell_pairs(control.pairs(fake_runs(seeds=(0,), loads=(0,)), ("etsi",)))
    assert set(one["verdict_ci"]) == {"n/a"} and np.isnan(one["time_diff_lo"]).all()          # one seed gives no interval


def test_a_missing_reference_and_unequal_seeds():
    runs = fake_runs(seeds=(0, 1, 2), loads=(0,))
    runs = runs[~((runs["rule"] == "etsi") & (runs["seed"] == 2))]                           # etsi of seed 2 is not run yet
    runs = runs[~((runs["rule"] == "aoc2") & (runs["seed"].isin((1, 2))))]                   # aoc2 has seed 0 only
    messages = []
    pairs = control.pairs(runs, ("etsi",), log=messages.append)
    assert set(pairs[pairs["rule"] == "aoc"]["seed"]) == {0, 1} and any("no reference run" in m for m in messages)
    cells = control.cells(runs, log=messages.append)
    assert set(cells["seeds"]) == {1} and set(cells["seed_list"]) == {"0"}                   # the means rest on the common seed only
    assert any("left out of the means" in m for m in messages)
    runs = runs[runs["rule"] != "aoc2"]
    cells = control.cells(runs)
    assert set(cells["seed_list"]) == {"0,1"}                                                # seed 2 is out: etsi does not have it
    no_ref = control.pairs(runs[runs["rule"] != "etsi"], ("etsi",))
    assert no_ref.empty and list(no_ref.columns)[:4] == ["density", "pen", "load", "seed"]
    assert control.h4_table(control.cell_pairs(pairs), pairs).set_index(["rule", "reference"]).loc[("aoc", "etsi"), "h4"].startswith("incomplete")


def test_nan_metrics_give_n_a():
    runs = fake_runs(seeds=(0,), loads=(0,))
    runs.loc[runs["rule"] == "aoc", "time_above_target"] = np.nan
    pairs = control.pairs(runs, ("etsi",))
    assert set(pairs[pairs["rule"] == "aoc"]["verdict"]) == {"n/a"}
    cp = control.cell_pairs(pairs)
    assert cp[cp["rule"] == "aoc"].iloc[0]["verdict"] == "n/a"
    h4 = control.h4_table(cp, pairs).set_index(["rule", "reference"])
    assert h4.loc[("aoc", "etsi"), "conditions_pass"] == 0


def test_the_table_survives_the_csv_cache(tmp_path):
    runs = fake_runs()
    runs.to_csv(tmp_path / "runs.csv", index=False)
    back = pd.read_csv(tmp_path / "runs.csv")
    assert back["pen"].dtype.kind == "i" and back["load"].dtype.kind == "i"
    assert np.allclose(control.pairs(back)["load_ratio"], control.pairs(runs)["load_ratio"])
    pd.testing.assert_frame_equal(control.cells(back).drop(columns="seed_list"), control.cells(runs).drop(columns="seed_list"), check_dtype=False)


def test_the_frontier():
    points = pd.DataFrame({"x": [0.1, 0.2, 0.3, 0.2, 0.15, np.nan], "time_above_target": [0.5, 0.7, 0.9, 0.6, 0.7, 0.9]})
    on = control.frontier(points, "x")
    assert on.tolist() == [True, False, True, False, True, False]    # (0.2, 0.7) is beaten by (0.15, 0.7); (0.2, 0.6) by both
    cells = control.cells(full_grid((0, 1)))
    front = control.frontier_table(cells)
    assert set(front["measure"]) == {"cbr", "bytes"} and {"all", "load 0", "load 12", "load 36"} <= set(front["scope"])
    assert front["scope"].str.startswith("condition").sum() > 0
    bytes_all = front[(front["measure"] == "bytes") & (front["scope"] == "all")]
    assert set(bytes_all["rule"]) == {"aoc", "aoc2"}                  # aoc: fewest bytes; aoc2: the highest time; the others are beaten
    assert bytes_all["x"].is_monotonic_increasing
    counts = control.condition_frontier_counts(front, "bytes")
    assert counts["conditions"] == 12 and counts["per_rule"]["aoc"] == 12 and counts["per_rule"]["aoc2"] == 12 and counts["per_rule"]["aocdev"] == 0
    cbr = control.condition_frontier_counts(front, "cbr")
    assert cbr["per_rule"]["aocdev"] == 0 and cbr["per_rule"]["aoc2"] == 12      # at load 12 and 36 the busy ratio is the same: the highest time wins


def test_duplicates_and_unknown_references_are_refused():
    runs = pd.concat([fake_runs(seeds=(0,), loads=(0,))] * 2, ignore_index=True)
    with pytest.raises(ValueError, match="twice"):
        control.pairs(runs, ("etsi",))
    with pytest.raises(ValueError, match="twice"):
        control.cells(runs)
    with pytest.raises(ValueError, match="unknown reference"):
        control.analyse(str(ROOT), str(ROOT / "nowhere"), references=("foo",), log=lambda *a: None)


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
    assert found["exported"].notna().all()
    assert control.find_runs(str(tmp_path / "nowhere")).empty


def _export(root, n):
    d = root / ("aoc-control-%d" % n) / "parquet"
    d.mkdir(parents=True, exist_ok=True)
    (d / "export_summary.json").write_text("{}")


def test_collect_keeps_the_cache_and_knows_when_it_is_stale(tmp_path, monkeypatch):
    root, out = tmp_path / "root", tmp_path / "out"
    for n in (0, 1):
        _export(root, n)
    calls = []

    def fake_metrics(folder, run, warmup, range_m, hold):
        calls.append(run)
        row = fake_runs(seeds=(run % 4,), loads=(0,)).iloc[0].to_dict()
        row.update(run=run, warmup=warmup, range_m=range_m, hold=hold)
        return row

    monkeypatch.setattr(control, "run_metrics", fake_metrics)
    quiet = dict(log=lambda *a: None)
    runs = control.collect(str(root), str(out), **quiet)
    assert sorted(calls) == [0, 1] and len(runs) == 2 and os.path.isfile(out / "runs.csv")
    calls.clear()
    _export(root, 4)
    runs = control.collect(str(root), str(out), **quiet)
    assert calls == [4] and runs["run"].tolist() == [0, 1, 4]        # only the new run is read
    calls.clear()
    shutil.rmtree(root / "aoc-control-1")
    runs = control.collect(str(root), str(out), **quiet)
    assert calls == [] and runs["run"].tolist() == [0, 4]            # a run that is gone leaves the cache
    runs = control.collect(str(root), str(out), hold=2.0, **quiet)
    assert sorted(calls) == [0, 4]                                    # other options: every run is read again
    calls.clear()
    summary = root / "aoc-control-0" / "parquet" / "export_summary.json"
    os.utime(summary, (summary.stat().st_atime, summary.stat().st_mtime + 100))
    runs = control.collect(str(root), str(out), hold=2.0, **quiet)
    assert calls == [0]                                               # a run exported again is read again
    calls.clear()
    control.collect(str(root), str(out), hold=2.0, refresh=True, **quiet)
    assert sorted(calls) == [0, 4]
    calls.clear()
    control.collect(str(root), str(out), hold=2.0, jobs=2, **quiet)  # the pool path, nothing to read
    assert calls == []


def test_an_unreadable_run_is_reported_not_fatal(tmp_path, monkeypatch):
    root, out = tmp_path / "root", tmp_path / "out"
    _export(root, 0)
    monkeypatch.setattr(control, "run_metrics", lambda *a: (_ for _ in ()).throw(FileNotFoundError("cpm_rx.parquet")))
    messages = []
    runs = control.collect(str(root), str(out), log=messages.append)
    assert runs.empty and any("could not be read" in m for m in messages) and any("cpm_rx.parquet" in m for m in messages)


def test_run_metrics_checks_the_variables(tmp_path, monkeypatch):
    from aoc.analysis import rulecheck
    summary = dict(variables={"density": "high", "pen": "25", "rule": "etsi", "load": "0"}, seconds=270.0, senders=1, cpm_rate_hz=1.0, bytes_per_s=1.0,
                   bytes_per_s_without_empty=1.0, objects_per_cpm=1.0, cbr_mean=0.1, receivers=1, receivers_exposed=1, pairs=10, time_above_target=0.5,
                   known=0.6, time_above_target_receivers=0.5, present_s=np.nan)
    monkeypatch.setattr(rulecheck, "analyse", lambda *a, **k: dict(summary=summary))
    with pytest.raises(ValueError, match="not the cell"):
        control.run_metrics(str(tmp_path), 0)                        # run 0 is low/25/etsi/0, the variables say high
    summary["variables"]["density"] = "low"
    row = control.run_metrics(str(tmp_path), 0, hold=3.0)
    assert row["density"] == "low" and row["hold"] == 3.0 and row["bytes_per_sender"] == 1.0 and row["cpm_rate_hz"] == 1.0   # per sender over the run
    summary.update(present_s=100.0, cpm_rate_hz_present=2.5, bytes_per_sender_s=7.0)
    row = control.run_metrics(str(tmp_path), 0)
    assert row["cpm_rate_hz"] == 2.5 and row["bytes_per_sender"] == 7.0                       # per sender-second when the presence is known


def test_f09_draws_from_the_cells(tmp_path):
    pytest.importorskip("matplotlib")
    from aoc.analysis import gridfigures
    runs = full_grid((0, 1))
    cells = control.cells(runs)
    (tmp_path / "e3").mkdir()
    cells.to_csv(tmp_path / "e3" / "cells.csv", index=False)
    control.cell_pairs(control.pairs(runs)).to_csv(tmp_path / "e3" / "cell_pairs.csv", index=False)
    numbers = gridfigures.f09_frontier(str(tmp_path), str(tmp_path / "f09.pdf"))
    assert (tmp_path / "f09.pdf").stat().st_size > 1000
    assert numbers["aoc"]["conditions"] == 12 and numbers["frontier_bytes_load_36"].get("aoc", 0) >= 1
    assert numbers["pair_aoc"]["conditions_pass"] == 12 and numbers["pair_aocdev"]["conditions_pass"] == 0
    # the script of the figure takes the results folder and writes the PDF next to itself; without the pairs the figure still draws
    (tmp_path / "e3" / "cell_pairs.csv").unlink()
    here = tmp_path / "figures"
    here.mkdir()
    script = ROOT / "figures" / "f09_control.py"
    (here / script.name).write_text(script.read_text())
    proc = subprocess.run([sys.executable, str(here / script.name), str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 0 and "written:" in proc.stdout and (here / "f09_control.pdf").is_file(), proc.stdout + proc.stderr
    pd.DataFrame(columns=cells.columns).to_csv(tmp_path / "e3" / "cells.csv", index=False)
    with pytest.raises(gridfigures.NoData):
        gridfigures.f09_frontier(str(tmp_path), str(tmp_path / "f09.pdf"))


@pytest.mark.skipif(not (ROOT / "results" / "e3" / "sandbox" / "runs.csv").is_file(), reason="the sandbox tables are not there")
def test_the_sandbox_tables_of_e3():
    """The 30 runs of seed 0 of the low-density conditions, run in the sandbox of the session: the tables of the repository come from
    the runs table, and say what docs/control_analysis.md quotes."""
    runs = pd.read_csv(ROOT / "results" / "e3" / "sandbox" / "runs.csv")
    assert len(runs) == 32 and set(runs["density"]) == {"low"}
    pairs = control.pairs(runs)
    cp = control.cell_pairs(pairs)
    kept = pd.read_csv(ROOT / "results" / "e3" / "sandbox" / "cell_pairs.csv", keep_default_na=False, na_values=[""])   # "n/a" is a verdict
    pd.testing.assert_frame_equal(cp.reset_index(drop=True), kept, check_dtype=False, check_exact=False, atol=1e-6)
    against = cp[cp["reference"] == "etsi"].set_index(["density", "pen", "load", "rule"])
    aoc_free = against.loc[("low", 25, 0, "aoc")]
    assert aoc_free["verdict"] == "PASS" and 0.55 < aoc_free["load_ratio"] < 0.65 and abs(aoc_free["time_diff"]) < 0.01
    assert against.loc[("low", 25, 12, "aoc"), "load_ratio"] < 0.5 and against.loc[("low", 25, 12, "aoc"), "verdict"] == "PASS"
    assert against.loc[("low", 25, 36, "aoc"), "time_diff"] < -0.04 and against.loc[("low", 25, 36, "aoc"), "verdict"] == "FAIL"   # the saturated channel
    assert against.loc[("low", 100, 36, "aoc2"), "time_diff"] > -0.02                                                              # n = 2 holds the time
    h4 = control.h4_table(cp, pairs).set_index(["rule", "reference"])
    assert h4.loc[("aoc", "etsi"), "h4"] == "incomplete (6/12)" and h4.loc[("aoc", "etsi"), "pass_load_0"] == "2/2"
    assert h4.loc[("aoc", "etsi"), "pass_load_36"] == "0/2"
    cells = control.cells(runs)
    assert set(cells["seeds"]) == {1} and len(cells) == 30
    etsi = runs[(runs["rule"] == "etsi") & (runs["seed"] == 0)]
    assert (etsi["cpm_rate_hz"].round(1) == 10.0).sum() == 4 and etsi["cpm_rate_hz"].min() < 2.5          # 10 Hz, 2 Hz under DCC at 36 stations
