"""Tests of the occluded-VRU check (task C5) on hand-made runs, and of the scenario files."""
import json
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis import vru
from aoc.analysis.vru import check_pilot, first_gap, intervals, longest_gap, read_vru_run

ROOT = pathlib.Path(__file__).resolve().parents[1]
STEP = 0.1


def fake_run(folder: pathlib.Path, helpers: int = 2, seed: int = 0, gap: tuple = (17.5, 20.1), cpm_in_gap: bool = True,
             helper_a_sees: bool = True, off_helper_sends: bool = False, cpm_class: str = "pedestrian", pedestrian: bool = True,
             truck_sends: bool = False, truck_sees: bool = True, late_gap: tuple | None = None):
    """An exported run of the occluded-VRU scenario, made by hand: the ego comes from the south at 13.9 m/s on a
    straight line, the pedestrian walks west across the road, helper_a comes the other way. The ego sees the
    pedestrian from 16.8 s on except during ``gap``; helper_a sees it from 16.8 s on; helper_a sends it in a CPM
    every 0.5 s and the ego receives each one 0.03 s later."""
    folder.mkdir(parents=True, exist_ok=True)
    t = np.round(np.arange(0.1, 40.0, STEP), 1)
    truth = []
    for tt in t:
        truth.append(dict(t=tt, obj_id="ego", x=300.0, y=300.0 - 13.9 * (tt - 3.5), speed=13.9, heading_deg=0.0, vx=0.0, vy=-13.9,
                          length=4.5, width=1.8, station_type=5))
        truth.append(dict(t=tt, obj_id="helper_a", x=297.0, y=-300.0 + 13.9 * tt, speed=13.9, heading_deg=180.0, vx=0.0, vy=13.9,
                          length=4.5, width=1.8, station_type=5))
        truth.append(dict(t=tt, obj_id="truck", x=304.0, y=50.0, speed=0.0, heading_deg=0.0, vx=0.0, vy=0.0, length=12.0, width=2.5, station_type=8))
        if pedestrian:
            truth.append(dict(t=tt, obj_id="ped", x=330.0 - 1.25 * tt, y=45.0, speed=1.25, heading_deg=270.0, vx=-1.25, vy=0.0,
                              length=0.5, width=0.5, station_type=1))
    truth = pd.DataFrame(truth)
    truth["class"] = truth["station_type"].map({1: "pedestrian", 5: "passenger_car", 8: "heavy_truck"})
    pos = truth.set_index(["obj_id", "t"])

    obs = []

    def local(who, tt, sensor):
        p = pos.loc[("ped", tt)]
        obs.append(dict(t_sim=tt, rx_station=who, src="radar", tx_station=pd.NA, obj_id="7", **{"class": "pedestrian"}, x=p["x"], y=p["y"],
                        vx=p["vx"], vy=p["vy"], t_meas=tt, t_rx=tt, age=0.0, gt_match_id="ped", sensor=sensor, gt_dist=0.0, t_pos=tt))

    for tt in t:
        if tt < 16.8 or not pedestrian:
            continue
        if not (gap[0] < tt < gap[1]) and not (late_gap and late_gap[0] < tt < late_gap[1]):
            local("ego", tt, "Radar")
            if tt >= gap[1]:
                local("ego", tt, "Camera")
        if helper_a_sees:
            local("helper_a", tt, "Camera")
            local("helper_a", tt, "Lidar")
        if truck_sees:
            local("truck", tt, "Lidar")
    tx_rows = []
    senders = ["helper_a"] + (["helper_b"] if helpers >= 2 else []) + (["helper_c"] if helpers >= 3 else [])
    if off_helper_sends:
        senders.append("helper_c")
    if truck_sends:
        senders.append("truck")
    for k, tt in enumerate(np.round(np.arange(0.2, 40.0, 0.5), 1)):
        for who in senders:
            tx_rows.append(dict(t_tx=tt, tx_node="node[1]", tx_id=who, station_id=hash(who) % 1000, reference_time_ms=int(tt * 1000), t_ref=tt,
                                n_objects=1, size_bytes=80))
            if tt < 16.8 or not pedestrian:
                continue
            if cpm_in_gap or not (gap[0] < tt + 0.03 < gap[1]):
                p = pos.loc[("ped", tt)]
                obs.append(dict(t_sim=tt + 0.03, rx_station="ego", src="v2x", tx_station=who, obj_id="7", **{"class": cpm_class}, x=p["x"], y=p["y"],
                                vx=p["vx"], vy=p["vy"], t_meas=tt, t_rx=tt + 0.03, age=0.03, gt_match_id="ped", sensor=pd.NA, gt_dist=0.0, t_pos=tt))
    columns = ["t_sim", "rx_station", "src", "tx_station", "obj_id", "class", "x", "y", "vx", "vy", "t_meas", "t_rx", "age",
               "gt_match_id", "sensor", "gt_dist", "t_pos"]
    # the ego's own CPMs about the pedestrian, received by helper_a: not a sender for the ego
    for tt in np.round(np.arange(20.5, 30.0, 0.5), 1):
        if pedestrian:
            p = pos.loc[("ped", tt)]
            obs.append(dict(t_sim=tt + 0.03, rx_station="helper_a", src="v2x", tx_station="ego", obj_id="7", **{"class": "pedestrian"}, x=p["x"],
                            y=p["y"], vx=p["vx"], vy=p["vy"], t_meas=tt, t_rx=tt + 0.03, age=0.03, gt_match_id="ped", sensor=pd.NA, gt_dist=0.0, t_pos=tt))
    obs = pd.DataFrame(obs, columns=columns)
    obs["tx_station"] = obs["tx_station"].astype("string")
    obs["sensor"] = obs["sensor"].astype("string")
    truth.to_parquet(folder / "truth.parquet", index=False)
    obs.to_parquet(folder / "observations.parquet", index=False)
    pd.DataFrame(tx_rows).to_parquet(folder / "cpm_tx.parquet", index=False)
    (folder / "export_summary.json").write_text(json.dumps({"run_id": "vru-%d-%d" % (helpers, seed), "config": "vru", "seed": seed,
                                                            "variables": {"helpers": str(helpers)}}), encoding="utf-8")
    return folder


def test_intervals_and_gap():
    times = np.array([1.0, 1.1, 1.2, 1.5, 1.6, 3.0, 3.1, 3.2, 3.3, 6.0])
    assert intervals(times) == [(1.0, 1.2), (1.5, 1.6), (3.0, 3.3), (6.0, 6.0)]
    assert longest_gap(times) == (3.3, 6.0)
    assert first_gap(times, 1.0) == (1.6, 3.0) and first_gap(times, 0.2) == (1.2, 1.5)
    assert first_gap(times, 10.0) == (3.3, 6.0)                                    # none that long: the longest
    assert longest_gap(np.array([1.0, 1.1])) is None and first_gap(np.array([1.0]), 1.0) is None and intervals(np.array([])) == []


def test_a_good_run_passes(tmp_path):
    r = read_vru_run(fake_run(tmp_path / "aoc-vru-0" / "parquet"))
    assert r["ok"], r["checks"]
    assert r["pedestrian"] == "ped" and r["appears"] == 16.8 and r["ego_first"] == 16.8
    assert r["lost"] == 17.5 and r["seen_again"] == 20.1 and r["gap"] == pytest.approx(2.6)
    assert r["lost_distance"] < 100 and r["seen_again_distance"] < 40
    assert r["helpers_seeing"] == {"helper_a": (25, 25), "truck": (25, 25)}
    assert r["cpm_in_gap"] == {"helper_a": 5, "helper_b": 5} and r["first_cpm_in_gap"] == pytest.approx(17.73)
    assert r["first_cpm_age"] == pytest.approx(0.03) and r["cpm_classes"] == ["pedestrian"]
    assert r["senders"] == ["helper_a", "helper_b"] and r["other_senders"] == [] and r["sent_by_off"] == []
    assert r["configured_helpers"] == ["helper_a", "helper_b"]
    text = vru.format_run(r)
    assert "PASS" in text and "gap 2.6 s" in text and "helper_a (25 of 25 steps), truck (25 of 25 steps)" in text


def test_the_occlusion_is_the_first_long_gap_not_the_longest(tmp_path):
    """A longer hole later (the ego passes the pedestrian, another car hides it) must not be taken for the occlusion."""
    r = read_vru_run(fake_run(tmp_path / "aoc-vru-0" / "parquet", late_gap=(30.0, 35.0)))
    assert r["ok"] and r["lost"] == 17.5 and r["seen_again"] == 20.1


@pytest.mark.parametrize("change, failed", [
    (dict(gap=(17.5, 17.9)), {"occlusion_gap", "seen_again_close"}),    # a blink of 0.4 s is not an occlusion, and the ego is still far
    (dict(gap=(17.5, 30.0)), {"occlusion_gap", "seen_again_close"}),    # 12.5 s: the ego never had it at the crossing
    (dict(cpm_in_gap=False), {"cpm_received_in_gap"}),
    (dict(helper_a_sees=False), {"helper_sees_in_gap"}),                   # the truck sees it, but it is not a helper
    (dict(off_helper_sends=True), {"helpers_as_configured", "senders_are_helpers"}),
    (dict(truck_sends=True), {"senders_are_helpers"}),
    (dict(cpm_class="passenger_car"), {"cpm_class_pedestrian"}),
    (dict(pedestrian=False), {"one_pedestrian"}),
])
def test_a_bad_run_is_caught(tmp_path, change, failed):
    r = read_vru_run(fake_run(tmp_path / "aoc-vru-0" / "parquet", **change))
    assert not r["ok"]
    assert {k for k, v in r["checks"].items() if not v} == failed


def test_the_pilot_and_the_command(tmp_path):
    k = 0
    for helpers in (1, 2, 3):
        for seed in (0, 1):
            fake_run(tmp_path / ("aoc-vru-%d" % k) / "parquet", helpers=helpers, seed=seed)
            k += 1
    runs = [read_vru_run(f) for f in vru.find_runs(tmp_path)]
    assert len(runs) == 6 and check_pilot(runs) == (True, [])
    assert check_pilot(runs[:5])[0] is False
    out = tmp_path / "vru.csv"
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.vru", str(tmp_path), "--pilot", "--out", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "runs: 6, passed: 6" in proc.stdout and "pilot: the six runs are there" in proc.stdout and "result: PASS" in proc.stdout
    table = pd.read_csv(out)
    assert len(table) == 6 and table["ok"].all() and np.allclose(table["gap"], 2.6)
    assert sorted(table["helpers"]) == [1, 1, 2, 2, 3, 3]


def test_a_run_without_cpm_fails_the_command(tmp_path):
    fake_run(tmp_path / "aoc-vru-0" / "parquet", cpm_in_gap=False)
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.vru", str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 1 and "result: FAIL" in proc.stdout and "failed checks: cpm_received_in_gap" in proc.stdout


def test_scenario_files_are_up_to_date(tmp_path):
    """The SUMO files of the scenario are written by make_vru_scenario.py; the committed copies must match."""
    proc = subprocess.run([sys.executable, str(ROOT / "artery-ext" / "tools" / "make_vru_scenario.py"), "--check"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout
    proc = subprocess.run([sys.executable, str(ROOT / "artery-ext" / "tools" / "make_vru_scenario.py"), "--out", str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 0 and len(list(tmp_path.glob("vru.*"))) == 5


def cp_cars(n: int, cars: list) -> list:
    """The cars on which services-vruN.xml runs the CP service, with the name filter as Artery applies it:
    the service is installed when regex_match(SUMO id, pattern) xor (match == "inverse")."""
    import re
    import xml.etree.ElementTree as ET
    root = ET.parse(ROOT / "artery-ext" / "config" / ("services-vru%d.xml" % n)).getroot()
    cp = [s for s in root.findall("service") if s.get("type") == "artery.envmod.service.CpService"][0]
    names = cp.findall("filters/name")
    assert len(names) == 1
    pattern, inverse = names[0].get("pattern"), names[0].get("match") == "inverse"
    return [c for c in cars if bool(re.fullmatch(pattern, c)) != inverse]


def test_scenario_files_say_what_the_ini_expects():
    """The names in the route file, the services files and the configuration agree."""
    vru_dir = ROOT / "artery-ext" / "vru"
    rou = (vru_dir / "vru.rou.xml").read_text(encoding="utf-8")
    for who in ("ego", "truck", "ped", "helper_a", "helper_b", "helper_c"):
        assert 'id="%s"' % who in rou
    assert 'allow="pedestrian"' in (vru_dir / "vru.edg.xml").read_text(encoding="utf-8")
    assert 'type="building"' in (vru_dir / "vru.poly.geo.xml").read_text(encoding="utf-8")
    ini = (vru_dir / "omnetpp.ini").read_text(encoding="utf-8")
    assert "ignorePersons = false" in ini and "PlainPerson" in ini and 'obstacleTypes = "building"' in ini
    assert 'services-vru${helpers=1,2,3}.xml' in ini and 'sensors-vru.xml' in ini
    cars = ["ego", "truck", "helper_a", "helper_b", "helper_c", "east.0", "west.3"]
    for n in (1, 2, 3):
        assert cp_cars(n, cars) == ["ego"] + ["helper_a", "helper_b", "helper_c"][:n]
    sensors = (ROOT / "artery-ext" / "config" / "sensors-vru.xml").read_text(encoding="utf-8")
    for name in ("Camera", "Radar", "Lidar"):
        assert 'name="%s"' % name in sensors and "environmentModel.%s.fovRange" % name in ini
