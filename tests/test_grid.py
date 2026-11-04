"""Tests of the core grid (task D1): the run numbering and the index (aoc.analysis.grid), the scenario files, the
configuration, and the receiver sampling of the exporter."""
import configparser
import json
import pathlib
import re
import subprocess
import sys

import pandas as pd
import pytest

from aoc.analysis import grid
from aoc.io import export

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data" / "run_aoc_sensors"


# ---------------------------------------------------------------- the numbering
def test_run_numbers_cover_the_grid_once():
    numbers = [grid.run_number(d, p, m, s) for d in grid.DENSITIES for p in grid.PENETRATIONS for m in grid.MESSAGES for s in range(12)]
    assert numbers == list(range(grid.N_RUNS)) and grid.N_RUNS == 432
    for n in (0, 11, 12, 36, 143, 144, 431):
        c = grid.cell_of_run(n)
        assert grid.run_number(c["density"], c["pen"], c["msg"], c["seed"]) == n
    assert grid.cell_of_run(0) == dict(density="low", pen=10, msg="etsi", seed=0, kind="evaluation")
    assert grid.cell_of_run(431) == dict(density="high", pen=100, msg="10hz", seed=11, kind="calibration")
    assert grid.cell_runs("medium", 25, "2hz") == list(range(192, 204))
    with pytest.raises(ValueError):
        grid.cell_of_run(432)
    with pytest.raises(ValueError):
        grid.run_number("low", 10, "etsi", 12)
    table = grid.runs_table()
    assert len(table) == 432 and (table["kind"] == "evaluation").sum() == 360 and (table["kind"] == "calibration").sum() == 72


def test_the_shell_numbering_agrees_with_the_python_one():
    """d1_grid.sh --cell computes the run numbers in bash; the formula must be the one of grid.py."""
    text = (ROOT / "artery-ext" / "tools" / "d1_grid.template.sh").read_text(encoding="utf-8")
    assert "base=$(( ((d * 4 + p) * 3 + m) * 12 ))" in text and 'seq -s \' \' "$base" $((base + 11))' in text
    assert "N_RUNS=432" in text


def test_compact():
    assert grid.compact([1, 2, 3, 7, 9, 10]) == "1-3,7,9-10" and grid.compact([]) == "" and grid.compact([4]) == "4"


# ---------------------------------------------------------------- the index
def fake_run(root: pathlib.Path, n: int, result: str = "PASS", cell: dict | None = None, seed: int | None = None):
    c = grid.cell_of_run(n) if n < grid.N_RUNS else grid.cell_of_run(0)
    cell = cell or dict(density=c["density"], pen=str(c["pen"]), msg=c["msg"])
    folder = root / ("aoc-grid-%d" % n) / "parquet"
    folder.mkdir(parents=True)
    (folder / "export_summary.json").write_text(json.dumps(dict(
        run_id="grid-%d" % n, result=result, t_last_s=299.9, steps=3000, receivers=["ego_1", "ego_2", "ego_3", "flow.1"],
        rows=dict(observations=1000, cpm_rx=200, local=300), objects_true=20, size_bytes=dict(observations=2e6),
        variables=dict(cell, incl="1"), seed=c["seed"] if seed is None else seed)), encoding="utf-8")


def test_index_and_check(tmp_path):
    for n in (0, 1, 10, 11, 12):
        fake_run(tmp_path, n)
    fake_run(tmp_path, 36, result="FAIL")                                             # exporter not happy
    fake_run(tmp_path, 37, cell=dict(density="high", pen="50", msg="2hz"))             # not the cell of its number
    fake_run(tmp_path, 38, seed=3)                                                     # not the seed of its number
    (tmp_path / "aoc-grid-39").mkdir()                                                 # ran, not exported
    fake_run(tmp_path, 41, cell=dict(T="0.5s"))                                        # exported, not a run of the grid
    fake_run(tmp_path, 999)                                                            # a folder outside the grid
    (tmp_path / "exit-codes.txt").write_text("0 0 0\n1 0 0\n39 1 -\n40 0 1\n11 0 1\n11 0 0\n12 0 1\n", encoding="utf-8")
    table = grid.index_runs(tmp_path)
    assert len(table) == 433 and table["run"].tolist() == list(range(432)) + [999]
    row = table.set_index("run")
    assert row.loc[0, "exported"] and row.loc[0, "cell_matches"] and row.loc[0, "receivers"] == 4 and row.loc[0, "parquet_mb"] == 2.0
    assert row.loc[0, "kind"] == "evaluation" and row.loc[10, "kind"] == "calibration"
    assert not row.loc[39, "exported"] and row.loc[39, "exit_code"] == 1 and pd.isna(row.loc[39, "export_code"])
    assert row.loc[40, "export_code"] == 1 and not row.loc[37, "cell_matches"] and row.loc[37, "density"] == "high"
    assert not row.loc[38, "cell_matches"] and row.loc[38, "seed"] == 3
    assert not row.loc[41, "cell_matches"] and row.loc[41, "has_variables"] == False and row.loc[0, "has_variables"]  # noqa: E712
    assert row.loc[12, "export_code"] == 1 and row.loc[11, "export_code"] == 0                # the last line of a run counts
    c = grid.check_grid(table)
    assert c["done"] == 5 and c["wrong_cell"] == [37, 38] and c["failed_export"] == [36] and c["no_variables"] == [41] and c["stray"] == [999]
    assert c["failed_run"] == [39, 40]                                                  # 12 was exported again with PASS: not failed
    assert c["missing"][:3] == [2, 3, 4] and 36 in c["missing"] and 37 in c["missing"] and len(c["missing"]) == 427 and not c["complete"]
    per = c["per_cell"].set_index(["density", "pen", "msg"])
    assert per.loc[("low", 10, "etsi"), "evaluation"] == 2 and per.loc[("low", 10, "etsi"), "calibration"] == 2
    assert per.loc[("low", 10, "2hz"), "evaluation"] == 1 and per["evaluation"].sum() == 3
    text = grid.format_check(c)
    assert "runs done: 5 of 432 (3 evaluation, 2 calibration)" in text and "cells complete: 0 of 36, started: 2" in text
    assert "runs that ended with an error: 39-40" in text and "did not pass: 36" in text and "not those of their run number" in text
    assert "without the variables of the design (density, pen, msg" in text and "outside the grid (ignored): 999" in text
    assert "--runs 2-9,13-431" in text and "grid: incomplete" in text


def test_a_run_without_the_grid_variables_is_not_counted(tmp_path):
    fake_run(tmp_path, 0, cell=dict(T="0.5s"))
    table = grid.index_runs(tmp_path)
    assert table.loc[0, "exported"] and table.loc[0, "cell_matches"] == False  # noqa: E712
    c = grid.check_grid(table)
    assert c["done"] == 0 and c["no_variables"] == [0] and not c["complete"]


def test_the_complete_grid_passes(tmp_path):
    for n in range(432):
        fake_run(tmp_path, n)
    c = grid.check_grid(grid.index_runs(tmp_path))
    assert c["complete"] and c["missing"] == [] and (c["per_cell"]["evaluation"] == 10).all() and (c["per_cell"]["calibration"] == 2).all()
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.grid", str(tmp_path), "--out", str(tmp_path / "index.csv"),
                           "--missing", str(tmp_path / "missing.txt")], capture_output=True, text=True)
    assert proc.returncode == 0 and "grid: complete" in proc.stdout, proc.stdout + proc.stderr
    assert len(pd.read_csv(tmp_path / "index.csv")) == 432 and (tmp_path / "missing.txt").read_text() == ""
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.grid", str(tmp_path / "nowhere")], capture_output=True, text=True)
    assert proc.returncode == 1 and "missing: 432 run(s): 0-431" in proc.stdout
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.grid", "x", "--list"], capture_output=True, text=True)
    assert proc.returncode == 0 and len(proc.stdout.strip().splitlines()) == 433


# ---------------------------------------------------------------- the scenario and the configuration
def test_scenario_files_are_up_to_date(tmp_path):
    """The route files and SUMO configurations are written by make_grid_scenario.py; the committed copies must match."""
    tool = ROOT / "artery-ext" / "tools" / "make_grid_scenario.py"
    proc = subprocess.run([sys.executable, str(tool), "--check"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = subprocess.run([sys.executable, str(tool), "--out", str(tmp_path)], capture_output=True, text=True)
    expected = ["grid_%s.%s" % (d, ext) for d in grid.DENSITIES for ext in ("rou.xml", "sumo.cfg")]
    expected += ["control_%s_p%d.%s" % (d, p, ext) for d in grid.DENSITIES for p in grid.PENETRATIONS for ext in ("rou.xml", "sumo.cfg")]
    expected += ["load_%d.xml" % n for n in (0, 12, 24, 36)]
    assert proc.returncode == 0 and sorted(p.name for p in tmp_path.iterdir()) == sorted(expected)


def test_route_files_hold_the_three_events_and_the_flows():
    for density, rate in (("low", 250), ("medium", 450), ("high", 700)):
        text = (ROOT / "artery-ext" / "grid" / ("grid_%s.rou.xml" % density)).read_text(encoding="utf-8")
        for k in (1, 2, 3):
            for who in ("truck", "helper_a", "helper_b", "ego", "helper_c"):
                assert re.search(r'<vehicle id="%s_%d" ' % (who, k), text), (density, who, k)
            assert re.search(r'<person id="ped_%d" ' % k, text)
        assert text.count('period="exp(%.5f)"' % (rate / 3600.0)) == 3 and text.count("<flow ") == 3
        assert 'id="bg_S"' not in text and 'route="from_S"' not in text                       # the south approach: event vehicles only
        departs = [float(m) for m in re.findall(r' depart="([0-9.]+)"', text)]
        assert departs == sorted(departs)                                                # SUMO wants the file sorted
        egos = [float(m) for m in re.findall(r'<vehicle id="ego_\d+"[^>]* depart="([0-9.]+)"', text)]
        assert egos == [pytest.approx(t + 16.6 - 13.1) for t in (90.0, 180.0, 270.0)]     # C5 timing from the start of a light cycle
        peds = [float(m) for m in re.findall(r'<person id="ped_\d+"[^>]* depart="([0-9.]+)"', text)]
        assert peds == [90.0, 180.0, 270.0] and 90.0 >= 60.0 and max(departs) < 330.0      # the first event after the warm-up


def test_services_files_match_the_penetrations():
    for pen in grid.PENETRATIONS:
        text = (ROOT / "artery-ext" / "config" / ("services-grid-%d.xml" % pen)).read_text(encoding="utf-8")
        assert text.count("<service ") == 2 and "CaService" in text and "CpService" in text
        if pen == 100:
            assert "<filters" not in text                                                  # every car is equipped
        else:
            ca, cp = text.split("CpService")
            assert "<filters" not in ca                                                    # CA on every car, as in C5
            assert cp.count('<penetration rate="%.2f" />' % (pen / 100.0)) == 1
            assert cp.count('<name pattern="ego_[0-9]+|helper_[abc]_[0-9]+" />') == 1 and cp.count('<filters operator="or">') == 1


def test_the_configuration_holds_the_factors_of_the_grid():
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str
    ini.read_string((ROOT / "artery-ext" / "grid" / "omnetpp.ini").read_text(encoding="utf-8"))
    g = ini["Config grid"]
    assert g["*.traci.launcher.sumocfg"] == '"grid_${density=low,medium,high}.sumo.cfg"'
    assert g["*.node[*].middleware.services"] == 'xmldoc("services-grid-${pen=10,25,50,100}.xml")'
    assert g["*.aocLogger.label"] == '"${msg=etsi,2hz,10hz}"' and g["repeat"] == "12"
    for key, values in (("objectInclusionConfig", "1,0,0"), ("withDccRestriction", "true,false,false"), ("minInterval", "0.1s,0.5s,0.1s"),
                        ("maxInterval", "1s,0.5s,0.1s")):
        assert g["*.node[*].middleware.CP." + key].endswith("=%s ! msg}" % values)        # parallel to msg: no extra runs
    general = ini["General"]
    assert general["sim-time-limit"] == "330s" and general["seed-set"] == "${repetition}" and general["*.traci.launcher.seed"] == "${repetition}"
    assert general["*.traci.ignorePersons"] == "false" and general["*.traci.mapper.personType"] == '"artery.envmod.PlainPerson"'
    assert ini["Config grid_quick"]["sim-time-limit"] == "60s"
    assert 'string label = default("");' in (ROOT / "artery-ext" / "aoc" / "AocLogger.ned").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the receivers of the exporter
def test_choose_receivers():
    tx = pd.DataFrame({"tx_id": ["flow.1", "flow.2", "ego_1", "flow.3", "flow.1", "helper_b_2"]})
    local = pd.DataFrame({"node_id": ["flow.1", "ego_2", "ped_1", "truck_1"]})
    nodes = pd.DataFrame({"node_id": ["flow.1", "flow.2", "flow.3", "flow.4", "ego_1", "ego_2", "ego", "helper_a", "truck_1", "helper_b_2"]})
    actors = {"ego", "ego_1", "ego_2", "helper_a", "truck_1", "helper_b_2"}
    assert export.choose_receivers("all", tx, local, nodes, 0) is None
    chosen = export.choose_receivers("auto:2", tx, local, nodes, 0)
    assert actors <= chosen and len(chosen) == 8 and chosen - actors <= {"flow.1", "flow.2", "flow.3"}   # the actors, then 2 senders
    assert export.choose_receivers("auto:2", tx, local, nodes, 0) == chosen                 # the same seed, the same draw
    assert export.choose_receivers("auto:9", tx, local, nodes, 0) == actors | {"flow.1", "flow.2", "flow.3", "flow.4"}  # too few senders: every car
    assert export.ACTOR_PATTERN.fullmatch("helper_c_12") and not export.ACTOR_PATTERN.fullmatch("helper_d_1") and not export.ACTOR_PATTERN.fullmatch("ego_1x")
    assert export.choose_receivers("flow.2, ego_1", tx, local, nodes, 0) == {"flow.2", "ego_1"}
    with pytest.raises(SystemExit):
        export.choose_receivers("auto:x", tx, local, nodes, 0)


@pytest.mark.parametrize("option", ["auto:2", "flow0.3,flow0.4"])
def test_the_exporter_keeps_the_observations_of_the_chosen_receivers(tmp_path, option):
    done = subprocess.run([sys.executable, "-m", "aoc.io.export", str(DATA), "--out", str(tmp_path), "--receivers", option],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    summary = json.loads((tmp_path / "export_summary.json").read_text(encoding="utf-8"))
    assert summary["result"] == "PASS" and summary["receivers_option"] == option and len(summary["receivers"]) == 2
    obs = pd.read_parquet(tmp_path / "observations.parquet")
    assert set(obs["rx_station"]) == set(summary["receivers"])
    assert summary["checks"]["local_rows_of_receivers"] == int((obs["src"] != "v2x").sum()) and summary["checks"]["local_rows_all"] == 1103
    assert summary["rows"]["cpm_rx"] == 943 and summary["rows"]["local"] == 1103             # the other tables hold every vehicle
    assert len(pd.read_parquet(tmp_path / "local.parquet")) == 1103 and pd.read_parquet(tmp_path / "local.parquet")["node_id"].nunique() == 5
    if option == "auto:2":
        assert summary["receivers"] == ["flow0.3", "flow0.4"]                                # drawn with the seed of the run


# ---------------------------------------------------------------- the script
@pytest.mark.parametrize("args, message", [
    (["--cell", "foo", "100", "etsi"], "density must be"),
    (["--cell", "low", "26", "etsi"], "penetration must be"),
    (["--runs", "3,x"], "bad run list element"),
    (["--runs", "430-440"], "run 432 does not exist"),
    (["--bogus"], "Unknown option"),
])
def test_the_script_refuses_bad_arguments(tmp_path, args, message):
    """The run list is computed before anything else; a bad argument must stop the script, not start runs with odd names."""
    proc = subprocess.run(["bash", str(ROOT / "artery-ext" / "scripts" / "d1_grid.sh")] + args, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "D1_ROOT": str(tmp_path / "nowhere")})
    assert proc.returncode == 2 and message in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    assert not (tmp_path / "nowhere").exists()


def test_the_script_checks_its_options_first(tmp_path):
    proc = subprocess.run(["bash", str(ROOT / "artery-ext" / "scripts" / "d1_grid.sh"), "--runs", "0"], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "D1_ROOT": str(tmp_path / "nowhere"), "D1_RECEIVERS": "eight"})
    assert proc.returncode == 2 and "D1_RECEIVERS must be a whole number" in proc.stdout
    proc = subprocess.run(["bash", str(ROOT / "artery-ext" / "scripts" / "d1_grid.sh"), "--runs", "0"], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "D1_ROOT": str(tmp_path / "nowhere")})
    assert proc.returncode == 1 and "does not exist" in proc.stdout and "D1 FAIL" in proc.stdout     # the install of A6 is missing


def test_the_control_design():
    """The control grid of E2: the run numbers, the cells, the variables, the route files and the services file."""
    c = grid.CONTROL
    assert c.n_runs == 240 and c.names == ("density", "pen", "rule", "load") and c.cal_seeds == () and c.eval_seeds == (0, 1, 2, 3)
    assert c.cell_of_run(0) == dict(density="low", pen=25, rule="etsi", load=0, seed=0, kind="evaluation")
    assert c.run_number(3, density="high", pen=100, rule="aocdev", load=36) == 239 and c.cell_of_run(239)["rule"] == "aocdev"
    assert c.run_number(1, density="low", pen="100", rule="aoc", load="12") == (((0 * 2 + 1) * 5 + 2) * 3 + 1) * 4 + 1
    assert c.cell_from_variables({"density": "high", "pen": "25", "rule": "aoc2", "load": "0", "grule": "aoc"}) == dict(density="high", pen=25, rule="aoc2", load=0)
    assert c.cell_from_variables({"density": "high", "pen": "25", "rule": "2hz", "load": "0"}) is None and c.cell_from_variables({}) is None
    assert len(c.cell_runs(density="low", pen=25, rule="etsi", load=0)) == 4 and len(c.seed_runs([0])) == 60
    assert grid.cell_of_run(5, c)["seed"] == 1 and len(grid.runs_table(c)) == 240 and "240 runs" in c.describe()
    for density in ("low", "high"):
        for pen in (25, 100):
            text = (ROOT / "artery-ext" / "grid" / ("control_%s_p%d.rou.xml" % (density, pen))).read_text(encoding="utf-8")
            assert len(re.findall(r'<flow id="eq_[NEW]"', text)) == 3 and len(re.findall(r'<flow id="bg_[NEW]"', text)) == (0 if pen == 100 else 3)
            assert re.search(r'<vehicle id="ego_3" ', text) and re.search(r'<person id="ped_1" ', text)
    text = (ROOT / "artery-ext" / "config" / "services-control.xml").read_text(encoding="utf-8")
    assert text.count("<service ") == 2 and "AocCpService" in text and "CaService" in text and "AocLoadService" not in text
    assert text.count('<name pattern="eq_[NEW]\\.[0-9]+|ego_[0-9]+|helper_[abc]_[0-9]+" />') == 2
    load = (ROOT / "artery-ext" / "config" / "services-load.xml").read_text(encoding="utf-8")
    assert load.count("<service ") == 1 and "AocLoadService" in load
    for n in (0, 12, 36):
        stations = (ROOT / "artery-ext" / "grid" / ("load_%d.xml" % n)).read_text(encoding="utf-8")
        assert stations.count("<rsu ") == n
    first = re.search(r'<rsu id="[^"]+" positionX="([0-9.]+)" positionY="([0-9.]+)"', (ROOT / "artery-ext" / "grid" / "load_12.xml").read_text())
    assert abs(float(first.group(1)) - 300.42) < 60 and abs(float(first.group(2)) - 299.5) < 60          # the closest stations first
    for name in ("eq_N.12", "ego_2", "helper_c_1"):
        assert re.fullmatch(r"eq_[NEW]\.[0-9]+|ego_[0-9]+|helper_[abc]_[0-9]+", name)
    for name in ("bg_N.3", "truck_1", "ped_2", "eq_S.1"):
        assert not re.fullmatch(r"eq_[NEW]\.[0-9]+|ego_[0-9]+|helper_[abc]_[0-9]+", name)


def test_the_control_configuration_holds_the_factors():
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.read(ROOT / "artery-ext" / "grid" / "omnetpp.ini")
    control = ini["Config control"]
    assert control["*.traci.launcher.sumocfg"] == '"control_${density=low,high}_p${pen=25,100}.sumo.cfg"'
    assert control["*.aoclogger.label"] == '"${rule=etsi,etsi_abs,aoc,aoc2,aocdev}-${load=0,12,36}"'
    assert control["*.staticnodes.nodes"] == 'xmldoc("load_${load}.xml")' and control["repeat"] == "4"
    assert ini["General"]["*.rsu*.vanetza[*].dcc.typename"] == '"NoRateControlDccEntity"' and ini["General"]["*.rsu*.middleware.load.rate"] == "25Hz"
    assert control["*.node[*].middleware.cp.generationrule"] == '${grule="etsi","etsi_abs","aoc","aoc","aoc" ! rule}'
    assert control["*.node[*].middleware.cp.repeats"] == "${n=1,1,1,2,1 ! rule}" and control["*.node[*].middleware.cp.deviationfraction"] == "${dev=0,0,0,0,0.5 ! rule}"
    assert control["*.node[*].middleware.services"] == 'xmldoc("services-control.xml")'


def test_index_and_check_of_the_control_grid(tmp_path):
    c = grid.CONTROL
    for n in (0, 1, 7):
        cell = c.cell_of_run(n)
        folder = tmp_path / ("aoc-control-%d" % n) / "parquet"
        folder.mkdir(parents=True)
        variables = {k: str(cell[k]) for k in c.names}
        json.dump({"result": "PASS", "seed": cell["seed"], "variables": variables, "rows": {}, "size_bytes": {}}, open(folder / "export_summary.json", "w"))
    (tmp_path / "aoc-grid-5" / "parquet").mkdir(parents=True)                                    # a run of the other design: ignored
    table = grid.index_runs(tmp_path, c)
    assert len(table) == 240 and table["exported"].sum() == 3 and set(table.columns) >= {"density", "pen", "rule", "load", "seed"}
    check = grid.check_grid(table, c)
    assert check["done"] == 3 and len(check["missing"]) == 237 and check["stray"] == []
    text = grid.format_check(check, c)
    assert "runs done: 3 of 240" in text and "--control --runs" in text and "low/25/etsi/0 2+0" in text
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.grid", str(tmp_path), "--design", "control"], capture_output=True, text=True)
    assert proc.returncode == 1 and "3 of 240" in proc.stdout
