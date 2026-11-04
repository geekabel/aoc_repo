"""Tests of the check of the AoC generation rule (aoc.analysis.rulecheck, task E1): the curve and the periods against the
theory module, the three tables on a handmade run, the command."""
import json
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis import rulecheck
from aoc.io.export import write_parquet
from aoc.theory import reliability

ROOT = pathlib.Path(__file__).resolve().parents[1]
STEP = 0.1


def write_run(folder: pathlib.Path, periods: dict, t_end: float = 40.0, delay: float = 0.05, stop_until: float = 20.0, lag_ms: int = 0) -> pathlib.Path:
    """A run with one sender ``a`` (a car at the origin) that sends three objects with the periods given, one receiver
    ``r`` 50 m away that gets every CPM after ``delay``: the car ``c1`` at 10 m/s, the car ``c2`` stopped until
    ``stop_until`` then at 10 m/s, the pedestrian ``p`` at 1 m/s. The periods are keyed c1, c2_stopped, c2_moving, p."""
    times = np.round(np.arange(STEP, t_end + STEP / 2, STEP), 1)
    truth, tx, obj, rx = [], [], [], []
    for t in times:
        truth.append((t, "a", 0.0, 0.0, "passenger_car", 10.0))
        truth.append((t, "r", 50.0, 0.0, "passenger_car", 0.0))
        truth.append((t, "c1", 20.0 + 10.0 * t, 5.0, "passenger_car", 10.0))
        c2_speed = 0.0 if t < stop_until else 10.0
        truth.append((t, "c2", 30.0 + (0.0 if t < stop_until else 10.0 * (t - stop_until)), -5.0, "passenger_car", c2_speed))
        truth.append((t, "p", 40.0 + 1.0 * t, 10.0, "pedestrian", 1.0))
    # the sender's inclusions: each object on its own schedule, the CPM at the step that carries at least one object
    schedule = {}
    for name, (cls, period_key) in {"c1": ("passenger_car", "c1"), "p": ("pedestrian", "p")}.items():
        schedule[name] = [round(float(t), 1) for t in np.arange(1.0, t_end, periods[period_key])]
    c2 = [round(float(t), 1) for t in np.arange(1.0, min(stop_until, t_end), periods["c2_stopped"])]       # stopped, then the state change
    c2 += [round(float(t), 1) for t in np.arange(stop_until, t_end, periods["c2_moving"])]                 # sent at once, then moving
    schedule["c2"] = sorted(set(c2))
    by_step = {}
    for name, ts in schedule.items():
        for t in ts:
            by_step.setdefault(t, []).append(name)
    speed_of = {"c1": lambda t: 10.0, "c2": lambda t: 0.0 if t < stop_until else 10.0, "p": lambda t: 1.0}
    cls_of = {"c1": "passenger_car", "c2": "passenger_car", "p": "pedestrian"}
    ref = 100000
    for t in times:
        names = by_step.get(float(t), [])
        if not names and (round(float(t) * 10) % 10 != 0):                 # a CPM without objects every second (the sensor information)
            continue
        ref += 100
        tx.append((float(t), "a", 1, ref, len(names), 40 + 30 * len(names)))
        rx.append((float(t) + delay, "r", 1, ref, 0.05))
        for k, name in enumerate(names):
            obj.append((1, ref, k, float(t), "a", name, float(t) - lag_ms / 1000.0, speed_of[name](t) * 100.0, cls_of[name], lag_ms))
    p = folder / "parquet"
    p.mkdir(parents=True, exist_ok=True)
    write_parquet(pd.DataFrame(truth, columns=["t", "obj_id", "x", "y", "class", "speed"]), p / "truth.parquet")
    frame = pd.DataFrame(tx, columns=["t_tx", "tx_id", "station_id", "reference_time_ms", "n_objects", "size_bytes"])
    frame["has_sensor_info"] = ((frame["n_objects"] == 0) & (frame["t_tx"].round(1) % 2 == 0)).astype(int)   # half of the CPMs without objects are empty
    write_parquet(frame, p / "cpm_tx.parquet")
    write_parquet(pd.DataFrame(obj, columns=["station_id", "reference_time_ms", "obj_index", "t_tx", "tx_id", "gt_match_id", "t_meas", "speed_cms", "class",
                                             "meas_delta_ms"]), p / "cpm_obj.parquet")
    write_parquet(pd.DataFrame(rx, columns=["t_rx", "rx_id", "station_id", "reference_time_ms", "cbr"]), p / "cpm_rx.parquet")
    write_parquet(pd.DataFrame({"node_id": ["a", "r"], "station_id": [1, 2]}), p / "nodes.parquet")
    json.dump({"t_last_s": t_end, "variables": {"rule": "test"}}, open(p / "export_summary.json", "w"))
    return folder


def test_the_curve_and_the_periods_match_the_theory():
    for cls, par in reliability.CLASSES.items():
        sx, sv, sxv, q = par[:4]
        ages = np.array([0.1, 0.5, 1.0, 2.0, 5.0])
        assert np.allclose(rulecheck.alpha_of_age(ages, q, sx, sv), reliability.alpha(ages, sx, sv, sxv, q))
        assert rulecheck.age_limit(0.9, q, sx, sv) == pytest.approx(reliability.age_at_level(0.9, sx, sv, sxv, q), abs=1e-6)
    assert rulecheck.age_limit(0.9, 0.5, 0.3, 0.5) == pytest.approx(0.98, abs=0.01)                          # the pedestrian of the note
    assert rulecheck.period(0.9, 0.5, 0.3, 0.5, delay=0.13) == pytest.approx(0.85, abs=0.01)                # the example of the note
    assert rulecheck.period(0.9, 0.5, 0.3, 0.5, delay=0.13, repeats=2) == pytest.approx(0.425, abs=0.01)
    assert rulecheck.age_limit(0.9, 1.0, 1.5, 0.5) == 0.0 and rulecheck.period(0.9, 1.0, 1.5, 0.5) == 0.0    # a sender too noisy for the target
    assert rulecheck.sent_at(0.85) == pytest.approx(0.8) and rulecheck.sent_at(0.8) == pytest.approx(0.8) and rulecheck.sent_at(0.973) == pytest.approx(0.9)
    rule = rulecheck.Rule()
    table = rule.table().set_index(["cls", "state"])
    assert table.loc[("passenger_car", "moving"), "sent_at"] == pytest.approx(0.9, abs=1e-3)
    assert table.loc[("any", "stopped"), "sent_at"] == pytest.approx(4.5, abs=1e-3)
    assert rulecheck.Rule(max_period=10).table().set_index(["cls", "state"]).loc[("any", "stopped"), "sent_at"] == pytest.approx(6.9, abs=1e-3)
    assert rule.curve("passenger_car", 0.2)[3] == "stopped" and rule.curve("passenger_car", 3.0)[3] == "moving" and rule.curve("bus", 3.0)[0] == rule.q["default"]


def test_the_tables_on_a_handmade_run(tmp_path):
    rule = rulecheck.Rule()
    periods = {"c1": 0.9, "c2_stopped": 4.5, "c2_moving": 0.9, "p": 2.0}
    run = write_run(tmp_path / "run", periods)
    result = rulecheck.analyse(str(run), rule, warmup=2.0, range_m=200.0, hold=5.0)
    senders = result["senders"].set_index("tx_id")
    assert list(senders.index) == ["a"] and 1.5 < senders.loc["a", "cpm_rate_hz"] < 3.0 and senders.loc["a", "with_objects"] > 0.5
    inc = result["inclusions"].set_index(["cls", "state"])
    car = inc.loc[("passenger_car", "moving")]
    assert car["sent_at"] == pytest.approx(0.9, abs=1e-3) and car["median"] == pytest.approx(0.9, abs=1e-3) and car["at_period"] > 0.9 and car["longer"] == 0
    stopped = inc.loc[("passenger_car", "stopped")]
    assert stopped["sent_at"] == pytest.approx(4.5, abs=1e-3) and stopped["intervals"] == 4                  # 1, 5.5, 10, 14.5, 19, then 20 (the state change)
    assert stopped["shorter_state_change"] == pytest.approx(0.25)
    ped = inc.loc[("pedestrian", "moving")]
    assert ped["sent_at"] == pytest.approx(1.1, abs=1e-3) and ped["median"] == pytest.approx(2.0, abs=1e-3) and ped["longer"] == 1.0 and ped["at_period"] == 0
    rec = result["receivers"].set_index("rx_id")
    assert list(rec.index) == ["r"] and rec.loc["r", "cbr_mean"] == pytest.approx(0.05)
    # the three objects without V2X (the sender a is left out): the car c1 above the target all the time while within range (period
    # 0.9 + delay 0.05 < limit 1.07; it leaves the range at 23 s), the pedestrian for (limit 1.24 - delay) / 2.0 of the time, the
    # stopped car all the time: about (210 + 380 + 380 * 0.59) / 970
    assert 0.78 < rec.loc["r", "time_above_target"] < 0.88 and rec.loc["r", "known"] > 0.99
    everything = rulecheck.analyse(str(run), rule, warmup=2.0, range_m=200.0, hold=5.0, objects="all")["receivers"].set_index("rx_id")
    assert everything.loc["r", "pairs"] == rec.loc["r", "pairs"] + 381 and everything.loc["r", "known"] < 0.8          # the sender a (381 ticks), never reported
    s = result["summary"]
    assert s["senders"] == 1 and s["time_above_target"] == rec.loc["r", "time_above_target"] and s["label"] == "{'rule': 'test'}"


def test_a_faster_sender_is_known_better_and_the_report(tmp_path):
    rule = rulecheck.Rule()
    slow = rulecheck.analyse(str(write_run(tmp_path / "slow", {"c1": 2.0, "c2_stopped": 4.5, "c2_moving": 2.0, "p": 2.0})), rule, 2.0, 200.0, 5.0)
    fast = rulecheck.analyse(str(write_run(tmp_path / "fast", {"c1": 0.5, "c2_stopped": 0.5, "c2_moving": 0.5, "p": 0.5})), rule, 2.0, 200.0, 5.0)
    assert fast["summary"]["time_above_target"] > slow["summary"]["time_above_target"] and fast["summary"]["bytes_per_s"] > slow["summary"]["bytes_per_s"]
    text = rulecheck.format_report(slow, fast, rule)
    assert text.count("H4 on this run, the load as") == 2 and "H4 on this run, the load as the CPM bytes without the empty CPMs: not met" in text
    assert "run against the other" in text and "the rule: target 0.90" in text
    text = rulecheck.format_report(fast, slow, rule)
    assert "the CPM bytes without the empty CPMs: not met" in text                                     # more load, not less


def test_the_command(tmp_path):
    run = write_run(tmp_path / "run", {"c1": 0.9, "c2_stopped": 4.5, "c2_moving": 0.9, "p": 1.1})
    other = write_run(tmp_path / "other", {"c1": 0.3, "c2_stopped": 0.3, "c2_moving": 0.3, "p": 0.5})
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.rulecheck", str(run), "--against", str(other), "--warmup", "2", "--out", str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "the CPM bytes without the empty CPMs: PASS" in proc.stdout, proc.stdout
    assert "the channel busy ratio, the load of the plan: not met" in proc.stdout                      # the same busy ratio in both fixtures
    assert (out / "run_inclusions.csv").is_file() and (out / "against_receivers.csv").is_file()
    summary = json.load(open(out / "summary.json"))
    assert summary["run"]["bytes_per_s"] < 0.8 * summary["against"]["bytes_per_s"] and summary["run"]["receivers_exposed"] == 1
    assert summary["run"]["bytes_per_s_without_empty"] < summary["run"]["bytes_per_s"]
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.rulecheck", str(tmp_path / "nowhere")], capture_output=True, text=True)
    assert proc.returncode == 2 and "ERROR" in proc.stdout


@pytest.mark.skipif(not (ROOT / "results" / "e1" / "quick_aoc_vs_etsi" / "run_inclusions.csv").is_file(), reason="the tables of E1 are not there")
def test_the_quick_run_of_the_sandbox():
    """The service did what the rule says on the quick run of Nov 4, 2026 (the C++ side, through its logs)."""
    folder = ROOT / "results" / "e1" / "quick_aoc_vs_etsi"
    inc = pd.read_csv(folder / "run_inclusions.csv").set_index(["cls", "state"])
    car = inc.loc[("passenger_car", "moving")]
    assert car["median"] == pytest.approx(0.9, abs=1e-3) and car["at_period"] >= 0.85 and car["sent_at"] == pytest.approx(0.9, abs=1e-3)
    stopped = inc.loc[("passenger_car", "stopped")]
    assert stopped["sent_at"] == pytest.approx(4.5, abs=1e-3) and stopped["p90"] >= 4.3 and stopped["shorter_state_change"] > 0.2
    summary = json.load(open(folder / "summary.json"))
    assert summary["run"]["bytes_per_s"] < 0.5 * summary["against"]["bytes_per_s"] and summary["run"]["cbr_mean"] < 0.8 * summary["against"]["cbr_mean"]
    assert summary["run"]["time_above_target"] >= summary["against"]["time_above_target"] - 0.01
    etsi = pd.read_csv(folder / "against_inclusions.csv").set_index(["cls", "state"])
    assert etsi.loc[("passenger_car", "stopped"), "median"] < 0.5                         # CpService: the position change relative to the sender
