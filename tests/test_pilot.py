"""Tests of the belief trace (aoc.analysis.trace) and of the pilot command (aoc.analysis.pilot, task C7) on synthetic runs."""
import json
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aoc.analysis import pilot
from aoc.analysis.metrics import EvaluationModel
from aoc.analysis.trace import belief_trace, both_views, without_v2x
from aoc.fusion.replay import load_run
from aoc.io.synthetic import Car, Scenario, make_run, write_run

ROOT = pathlib.Path(__file__).resolve().parents[1]
GAP = (10.0, 13.0)          # the ego's own sensors miss the pedestrian between these times (3 s: alpha of a pedestrian falls to 0.15)


def occlusion_scenario(seed: int = 0, helper_sends: bool = True) -> Scenario:
    """A pedestrian walking slowly, the ego 40 m behind it and a helper 40 m ahead of it, all three on the same line and
    at the same speed: both cars see the pedestrian within the sensor range of 60 m, and the helper sends it in its CPMs
    every 0.5 s."""
    cars = (Car("ped", x=100.0, vx=1.25, cls="pedestrian"), Car("ego", x=60.0, vx=1.25), Car("helper_a", x=140.0, vx=1.25, sends=helper_sends))
    return Scenario(cars, duration=20.0, sensor_range=60.0, cpm_period=0.5, delay=0.05, seed=seed, name="synthetic-occlusion-%d" % seed)


def hide(obs: pd.DataFrame, ego: str = "ego", target: str = "ped", gap: tuple = GAP) -> pd.DataFrame:
    """Takes out the ego's own rows about the target during the gap: the occlusion."""
    own = (obs["rx_station"] == ego) & (obs["src"] != "v2x") & (obs["gt_match_id"] == target) & (obs["t_sim"] > gap[0]) & (obs["t_sim"] < gap[1])
    return obs[~own.to_numpy(dtype=bool)]


def occlusion_run(folder: pathlib.Path, seed: int = 0, helper_sends: bool = True, helpers: int = 1) -> pathlib.Path:
    """Writes the exported form of the scenario with the occlusion cut into the observations."""
    write_run(occlusion_scenario(seed, helper_sends), folder)
    obs = pd.read_parquet(folder / "observations.parquet")
    hide(obs).to_parquet(folder / "observations.parquet", index=False)
    info = json.loads((folder / "export_summary.json").read_text(encoding="utf-8"))
    info["variables"]["helpers"] = str(helpers)
    (folder / "export_summary.json").write_text(json.dumps(info), encoding="utf-8")
    return folder


def test_without_v2x_keeps_the_sensor_rows_only():
    run = make_run(occlusion_scenario())
    own = without_v2x(run)
    assert (own.obs["src"] != "v2x").all() and len(own.obs) == int((run.obs["src"] != "v2x").sum())
    assert own.weather == run.weather and own.truth is run.truth


def test_the_trace_follows_the_pedestrian_with_and_without_v2x(tmp_path):
    run = load_run(occlusion_run(tmp_path / "run"))
    model = EvaluationModel(hold=5.0)
    with_v2x = belief_trace(run, "ego", "ped", model, ways=("B0", "B3"), t_start=8.0, t_end=15.0)
    own = belief_trace(run, "ego", "ped", model, ways=("B0", "B3"), local_only=True, t_start=8.0, t_end=15.0)
    assert set(with_v2x["way"]) == {"B0", "B3"} and len(with_v2x) == 2 * 71 and len(own) == len(with_v2x)
    gap = (with_v2x["t"] > GAP[0]) & (with_v2x["t"] < GAP[1])
    b3 = with_v2x[(with_v2x["way"] == "B3")]
    # with V2X: the helper's CPMs keep the belief high through the gap, and no own detection is counted there
    assert (b3[gap[b3.index]]["n_local"] == 0).all() and b3[gap[b3.index]]["n_new"].sum() >= 5
    assert b3[gap[b3.index]]["bel"].min() > 0.9 and np.allclose(b3["pl"], 1.0)
    # own sensors only: the last report ages, the belief falls with the curve of the pedestrian, then jumps back
    o3 = own[own["way"] == "B3"].set_index("t")
    inside = o3[(o3.index > GAP[0]) & (o3.index < GAP[1])]
    assert inside["bel"].is_monotonic_decreasing and inside["bel"].iloc[0] > 0.95 and inside["bel"].iloc[-1] < 0.3
    assert (inside["groups"] == 0).all() and np.allclose(inside["age_last_local"], inside.index - GAP[0] + 0.0, atol=0.11)
    assert float(o3[np.isclose(o3.index, 13.1)]["bel"].iloc[0]) > 0.95                 # seen again
    # full trust keeps the stale own report at its score
    o0 = own[own["way"] == "B0"].set_index("t")
    assert np.allclose(o0[(o0.index > GAP[0]) & (o0.index < GAP[1])]["bel"], 0.99, atol=1e-6)
    both = both_views(run, "ego", "ped", model, ways=("B3",), t_start=8.0, t_end=15.0)
    assert set(both["view"]) == {"with V2X", "own sensors"} and len(both) == 2 * 71


def test_the_own_memory_is_forgotten_after_the_hold(tmp_path):
    run = load_run(occlusion_run(tmp_path / "run", helper_sends=False))
    long_gap = (6.0, 18.0)
    run = replace_obs(run, hide(run.obs, gap=long_gap))
    own = belief_trace(run, "ego", "ped", EvaluationModel(hold=5.0), ways=("B3",), local_only=True, t_start=5.0, t_end=19.0).set_index("t")
    inside = own[(own.index > long_gap[0]) & (own.index < long_gap[1])]
    assert inside[inside.index <= 11.0]["bel"].notna().all()                              # remembered for 5 s
    assert inside[inside.index > 11.05]["bel"].isna().all()                               # then forgotten
    assert np.isclose(inside[inside.index <= 11.0]["age_last_local"].max(), 5.0, atol=0.11)
    kept = belief_trace(run, "ego", "ped", EvaluationModel(hold=None), ways=("B3",), local_only=True, t_start=5.0, t_end=19.0)
    assert kept["bel"].notna().all()                                                      # no hold: remembered to the end
    with pytest.raises(ValueError):
        belief_trace(run, "ego", "ped", EvaluationModel(), ways=("B3", "B9"))


def replace_obs(run, obs):
    from dataclasses import replace
    return replace(run, obs=obs)


def test_the_trace_is_nan_where_the_ego_knows_nothing(tmp_path):
    run = load_run(occlusion_run(tmp_path / "run", helper_sends=False))
    own = belief_trace(run, "ego", "ped", EvaluationModel(), ways=("B3",), local_only=True, t_start=0.0, t_end=1.0)
    assert own["bel"].notna().all()                                                      # seen from the first tick on
    v2x = belief_trace(run, "ego", "ped", EvaluationModel(), ways=("B3",), t_start=GAP[0] + 0.5, t_end=GAP[1] - 0.5)
    assert v2x["bel"].isna().all() and (v2x["n_new"] == 0).all()                        # nobody tells the ego: nothing known


def test_the_pilot_passes_on_a_run_with_cpms_and_fails_without(tmp_path):
    good = pilot.pilot_run(str(occlusion_run(tmp_path / "good")), metrics=False)
    assert good["ok"], good["checks"]
    assert good["timeline"]["lost"] == pytest.approx(10.0, abs=0.11) and good["timeline"]["seen_again"] == pytest.approx(13.0, abs=0.11)
    assert good["v2x_gap_mean"] > 0.9 and good["own_gap_end"] < 0.3 and good["cpm_rows_in_gap"] >= 5
    assert good["b0_gap_mean"] > 0.95 and len(good["metrics"]) == 0
    text = pilot.format_run(good)
    assert "PASS" in text and "own sensors only" in text
    bad = pilot.pilot_run(str(occlusion_run(tmp_path / "bad", helper_sends=False)), metrics=False)
    assert not bad["ok"] and {k for k, v in bad["checks"].items() if not v} >= {"cpm_in_gap", "v2x_belief_holds"}


def test_the_pilot_with_metrics_and_the_command(tmp_path):
    occlusion_run(tmp_path / "aoc-vru-0", seed=0, helpers=1)
    occlusion_run(tmp_path / "aoc-vru-1", seed=1, helpers=2)
    out, traces, figure = tmp_path / "pilot.csv", tmp_path / "traces.csv", tmp_path / "f6.png"
    proc = subprocess.run([sys.executable, "-m", "aoc.analysis.pilot", str(tmp_path), "--out", str(out), "--traces", str(traces),
                           "--figure", str(figure)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "runs: 2, passed: 2" in proc.stdout and "result: PASS" in proc.stdout and "metrics (whole run" in proc.stdout
    metrics = pd.read_csv(out)
    assert len(metrics) == 10 and set(metrics["way"]) == {"B0", "B1", "B2", "B3", "B4"} and sorted(metrics["helpers"].unique()) == [1, 2]
    assert metrics["calibration_error"].between(0.0, 1.0).all()
    table = pd.read_csv(traces)
    assert set(table["view"]) == {"with V2X", "own sensors"} and set(table["run"]) == {"aoc-vru-0", "aoc-vru-1"}
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        assert "matplotlib is not installed" in proc.stdout
    else:
        assert figure.is_file() and figure.stat().st_size > 10000 and "(aoc-vru-1)" in proc.stdout      # the run with 2 helpers is drawn


def test_the_figure_script(tmp_path):
    occlusion_run(tmp_path / "aoc-vru-0", seed=0, helpers=2)
    script = ROOT / "figures" / "f06_money.py"
    copy = tmp_path / "f06_money.py"
    copy.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(copy), str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "written:" in proc.stdout or "no figure written" in proc.stdout
    proc = subprocess.run([sys.executable, str(copy), str(tmp_path / "nowhere")], capture_output=True, text=True)
    assert proc.returncode == 0 and "nothing drawn" in proc.stdout
