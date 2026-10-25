"""Tests of the single-link check (task C1) on synthetic grids, and of the exporter with dropped CPMs."""
import json
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis import link
from aoc.analysis.link import GRID_P, GRID_T, check_grid, parse_time, read_grid, read_link_run
from aoc.io.export import iteration_variables
from aoc.io.synthetic import Car, Scenario, make_tables, write_run

DATA = pathlib.Path(__file__).parent / "data"


def link_scenario(period: float, loss: float, seed: int = 0, duration: float = 30.0) -> Scenario:
    """The single link of the plan as a synthetic stream: the receiver knows the object only through the sender."""
    cars = (Car("object", x=300.0, vx=10.0), Car("sender", x=260.0, vx=10.0), Car("receiver", x=80.0, vx=10.0))
    return Scenario(cars, duration=duration, sensor_range=65.0, cpm_period=period, delay=0.02, loss=loss, seed=seed,
                    name="link-T%g-p%g" % (period, loss))


@pytest.fixture(scope="module")
def grid(tmp_path_factory):
    root = tmp_path_factory.mktemp("grid")
    for k, (period, loss) in enumerate((t, p) for t in GRID_T for p in GRID_P):
        write_run(link_scenario(period, loss, seed=k), root / ("aoc-link-%d" % k) / "parquet")
    return root


def test_parse_time_and_variables():
    assert parse_time("0.5s") == 0.5 and parse_time("200ms") == pytest.approx(0.2) and parse_time("1") == 1.0
    assert parse_time("abc") is None and parse_time(None) is None
    assert iteration_variables("$T=0.5s, $p=0.3") == {"T": "0.5s", "p": "0.3"} and iteration_variables("") == {}


def test_message_tables_of_the_generator():
    tables = make_tables(link_scenario(0.5, 0.3, duration=60.0))
    tx, rx, drop = tables["cpm_tx"], tables["cpm_rx"], tables["cpm_drop"]
    assert set(tx["tx_id"]) == {"object", "sender", "receiver"} and tx.groupby("tx_id")["t_tx"].diff().dropna().eq(0.5).all()
    assert len(rx) + len(drop) <= 2 * len(tx)                                   # two other cars per message; late copies are not counted
    assert len(drop) / (len(rx) + len(drop)) == pytest.approx(0.3, abs=0.05)
    assert np.allclose(rx["delay"], 0.02) and np.allclose(rx["t_rx"] - rx["t_tx"], rx["delay"])
    keys = set(zip(tx["station_id"], tx["reference_time_ms"]))
    assert set(zip(rx["station_id"], rx["reference_time_ms"])) <= keys and set(zip(drop["station_id"], drop["reference_time_ms"])) <= keys
    assert (tx.loc[tx["tx_id"] == "sender", "n_objects"] == 1).all() and (tx.loc[tx["tx_id"] == "receiver", "n_objects"] == 0).all()


def test_one_run_is_read_and_checked(grid):
    row = read_link_run(grid / "aoc-link-6" / "parquet")                        # T 0.5 s, p 0.3
    assert row["T"] == 0.5 and row["p"] == 0.3 and row["period_median"] == 0.5 and row["delay_median"] == 0.02
    assert row["receiver_sees"] == 0 and row["links"] == 2                      # sender about object, object about sender
    assert row["loss_share"] == pytest.approx(0.3, abs=0.06) and row["object_rows"] > 0
    assert row["object_share"] == pytest.approx(0.7, abs=0.08) and row["age_median"] == pytest.approx(0.02, abs=1e-6)


def test_grid_passes_and_its_problems_are_found(grid, tmp_path):
    table = read_grid(grid)
    assert len(table) == 16 and check_grid(table, grid=True, single_link=True) == []
    assert sorted(zip(table["T"], table["p"])) == sorted((t, p) for t in GRID_T for p in GRID_P)
    # a run whose settings lie
    liar = table.copy()
    liar.loc[0, "T"] = 0.2
    liar.loc[1, "p"] = 0.5
    problems = check_grid(liar)
    assert any("period is" in p for p in problems) and any("loss is" in p for p in problems)
    # a missing cell and a double cell
    partial = table.iloc[1:].copy()
    problems = check_grid(partial, grid=True)
    assert any("missing from the grid" in p for p in problems)
    double = pd.concat([table, table.iloc[:1]], ignore_index=True)
    assert any("more than one run" in p for p in check_grid(double, grid=True))
    # a receiver that sees something is not a single link
    sees = table.copy()
    sees.loc[2, "receiver_sees"] = 12
    assert not check_grid(sees) and any("not a single link" in p for p in check_grid(sees, single_link=True))
    # no run at all
    assert check_grid(table.iloc[:0]) == ["no exported run found"]


def test_command(grid, tmp_path, capsys):
    assert link.main([str(grid), "--grid", "--single-link", "--out", str(tmp_path / "grid.csv")]) == 0
    text = capsys.readouterr().out
    assert text.startswith("C1 LINK CHECK") and "result:              PASS" in text and len(pd.read_csv(tmp_path / "grid.csv")) == 16
    only_one = tmp_path / "one"
    shutil.copytree(grid / "aoc-link-6", only_one / "aoc-link-6")
    assert link.main([str(only_one), "--grid"]) == 3
    assert "missing from the grid" in capsys.readouterr().out
    assert link.main([str(tmp_path / "nothing")]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert link.main([str(empty)]) == 3


def test_exporter_handles_dropped_cpms(tmp_path):
    """A copy of the sample run with a cpm_drop.csv: the exporter writes cpm_drop.parquet and counts the drops."""
    src = DATA / "run_aoc_sensors"
    run = tmp_path / "aoc-link-0"
    shutil.copytree(src, run)
    rx = pd.read_csv(run / "cpm_rx.csv")
    dropped = rx.iloc[::7][["t_rx", "rx_node", "rx_id", "station_id", "reference_time_ms"]].rename(columns={"t_rx": "t_drop"})
    dropped.to_csv(run / "cpm_drop.csv", index=False)
    text = (run / "run.csv").read_text()
    (run / "run.csv").write_text(text + 'iteration_vars,"$T=0.5s, $p=0.3"\n')
    done = subprocess.run([sys.executable, "-m", "aoc.io.export", str(run), "--out", str(run / "parquet")], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    summary = json.loads((run / "parquet" / "export_summary.json").read_text())
    assert summary["result"] == "PASS" and summary["rows"]["cpm_drop"] == len(dropped) and summary["variables"] == {"T": "0.5s", "p": "0.3"}
    assert summary["checks"]["cpm_drop_matched_to_a_sent_cpm"] == len(dropped)
    drop = pd.read_parquet(run / "parquet" / "cpm_drop.parquet")
    assert len(drop) == len(dropped) and drop["t_tx"].notna().all() and "dropped on purpose" in done.stdout
    row = read_link_run(run / "parquet")
    assert row["T"] == 0.5 and row["p"] == 0.3 and row["cpm_dropped"] == len(dropped)
