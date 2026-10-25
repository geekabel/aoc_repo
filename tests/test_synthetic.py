"""Tests of the pipeline v1 on synthetic streams (task B9): streams whose right answer is known."""
import numpy as np
import pandas as pd
import pytest

from aoc.analysis.metrics import WAYS, EvaluationModel, evaluate_run
from aoc.fusion.alignment import Q_BY_CLASS, align
from aoc.fusion.association import associate
from aoc.fusion.bba import build_evidence
from aoc.fusion.combination import CombinationModel, fuse
from aoc.fusion.discounting import TrustModel, discount_evidence
from aoc.fusion.kinematics import fuse_states
from aoc.fusion.noise import NoiseModel
from aoc.fusion.replay import Run, check_run, load_run
from aoc.fusion.replay import main as replay_main
from aoc.io import synthetic
from aoc.io.export import OBS_COLUMNS
from aoc.io.synthetic import SCENARIOS, Car, Scenario, convoy, crossroads, leaver, lossy, make_observations, make_run, make_truth, write_run
from aoc.theory import reliability as theory
from aoc.theory.averages import mean_age_periodic

Q_CAR = Q_BY_CLASS["passenger_car"]
SX, SV = NoiseModel().sigma_x, NoiseModel().sigma_v


def belief_of_age(age):
    """What the AoC law gives a received object of this age: the score 0.99 times the reliability curve of the note."""
    return 0.99 * theory.alpha(np.asarray(age, dtype=float), SX, SV, 0.0, Q_CAR)


# ----------------------------------------------------------------------------
# The stream itself
# ----------------------------------------------------------------------------
def test_tables_follow_the_schema_and_the_scenario():
    scenario = convoy(duration=10.0)
    truth = make_truth(scenario)
    exact = make_observations(scenario, truth)
    assert list(exact.columns) == [name for name, _, _ in OBS_COLUMNS]
    for name, kind, _ in OBS_COLUMNS:
        assert {"string": "string", "int64": "Int64", "int32": "int32", "float64": "float64"}[kind] == str(exact[name].dtype), name
    assert exact[["P11", "exist_score"]].isna().all().all() and (exact["gt_dist"] == 0.0).all()                 # exact states, no noise yet
    run = make_run(scenario)
    obs = run.obs
    assert list(obs.columns)[:len(OBS_COLUMNS)] == list(exact.columns)                                         # the noise model adds gt_x and friends
    assert list(truth.columns) == ["t", "obj_id", "x", "y", "speed", "heading_deg", "vx", "vy", "length", "width", "station_type", "class"]
    assert len(truth) == 5 * 101 and truth["speed"].to_numpy().min() == pytest.approx(15.0)
    # by hand: 25 m apart, sensor range 60 m: the cars see 2, 3, 4, 3, 2 others; every sender reaches the 4 others;
    # the messages of the last step arrive after the end of the run and are never seen
    local, v2x = obs[obs["src"] != "v2x"], obs[obs["src"] == "v2x"]
    assert len(local) == 14 * 101 and len(v2x) == 14 * 4 * 100
    assert np.allclose(v2x["age"].to_numpy(dtype=float), 0.1) and (v2x["sender_conf"] == 1.0).all() and v2x["pdr_window"].eq(1.0).all()
    assert (local["age"] == 0.0).all() and local["tx_station"].isna().all() and (local["sensor"] == "radar").all()
    assert v2x["sensor"].isna().all() and (v2x["meas_src"] == "radar").all() and (obs["dcc_state"] == "relaxed").all()
    assert (obs["gt_match_id"] != obs["rx_station"]).to_numpy()[obs["src"].to_numpy() != "v2x"].all()        # a sensor never sees its own car
    assert np.isfinite(obs[["P11", "P22", "Pv11", "Pv22"]].to_numpy()).all()                                     # the noise model filled them
    assert obs["gt_dist"].mean() == pytest.approx(SX * np.sqrt(np.pi / 2), rel=0.05)                              # Rayleigh mean of the noise
    assert run.info["run_id"] == "synthetic-convoy" and run.egos == ["c0", "c1", "c2", "c3", "c4"]


def test_cars_come_and_go():
    scenario = Scenario((Car("a", vx=10.0), Car("b", x=30.0, vx=10.0, t_in=2.0, t_out=6.0), Car("c", x=-30.0, vx=10.0)), duration=10.0)
    run = make_run(scenario)
    life = run.truth[run.truth["obj_id"] == "b"]["t"].astype(float)
    assert life.min() == 2.0 and life.max() == 6.0
    about_b = run.obs[run.obs["gt_match_id"] == "b"]
    assert about_b["t_meas"].min() == 2.0 and about_b["t_meas"].max() == 6.0
    by_b = run.obs[run.obs["rx_station"] == "b"]
    assert by_b["t_sim"].min() == 2.0 and by_b["t_sim"].max() <= 6.0 + 1e-9                                     # it gets nothing once it left
    late = make_run(Scenario(scenario.cars, duration=10.0, delay=(0.1, 1.5), seed=5))
    assert late.obs.loc[late.obs["rx_station"] == "b", "t_sim"].max() <= 6.0 + 1e-9                             # even with slow messages
    assert len(list(late.replay("b"))) == 41                                                                     # its replay stops when it leaves


def test_same_seed_same_stream():
    one, two = make_run(lossy(duration=5.0)), make_run(lossy(duration=5.0))
    pd.testing.assert_frame_equal(one.obs, two.obs)
    other = make_run(lossy(duration=5.0, seed=7))
    assert len(other.obs) != len(one.obs) or not np.allclose(other.obs["t_rx"].to_numpy(), one.obs["t_rx"].to_numpy())


def test_scenario_checks():
    cars = (Car("a"), Car("b", x=10.0))
    for bad in (dict(cars=()), dict(cars=(Car("a"), Car("a"))), dict(cars=cars, step=0.0), dict(cars=cars, cpm_period=0.25),
                dict(cars=cars, loss=1.0), dict(cars=cars, delay=-0.1), dict(cars=cars, delay=(0.5, 0.2)), dict(cars=cars, delay="0.1"),
                dict(cars=(Car("a", t_in=1.0, t_out=1.0),)), dict(cars=(Car("a", wobble=-1.0),)), dict(cars=(Car("a", t_in=25.0),)),
                dict(cars=(Car("a", t_in=0.05),)), dict(cars=(Car("a", t_out=3.14),))):
        with pytest.raises(ValueError):
            Scenario(**bad)
    assert Scenario(cars, delay=(0.1, 0.5)).delay_bounds == (0.1, 0.5) and Scenario(cars).delay_bounds == (0.1, 0.1)
    assert Scenario(cars, delay=[0.1, 0.5]).delay_bounds == (0.1, 0.5)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_replay_check_passes(name):
    out = check_run(make_run(SCENARIOS[name]()))
    assert out["result"] == "PASS", out["problems"]
    assert out["ticks_without_ego_state"] == 0 and out["v2x_rows_about_the_receiver"] > 0                      # a sender reports the receiver too


# ----------------------------------------------------------------------------
# Known answers, formula by formula
# ----------------------------------------------------------------------------
def test_relay_gives_the_formulas_of_the_note():
    """Two senders report one object to a receiver that cannot see it. Every belief follows from the curve, every state from CI."""
    cars = (Car("o", vx=5.0, sends=False), Car("s1", x=30.0, vx=5.0), Car("s2", x=-30.0, vx=5.0), Car("r", x=200.0, vx=5.0, sends=False))
    for delay in (0.3, 1.2):
        run = make_run(Scenario(cars, duration=4.0, sensor_range=40.0, delay=delay, name="relay"))
        ticks = 0
        for tick in run.replay("r"):
            aligned = align(tick)
            if not len(aligned):
                continue
            groups = associate(aligned)
            assert len(aligned) == 2 and groups.max() == 0                                                     # one real object, two reports
            ages = aligned.objects["age_now"].to_numpy(dtype=float)
            assert np.allclose(ages, delay)                                                                     # measured at t - delay, received at t
            evidence = build_evidence(aligned.objects)
            a = belief_of_age(ages)
            expected = {("aoc", "cautious"): a.max(), ("aoc", "conjunctive"): 1.0 - np.prod(1.0 - a),
                        ("full_trust", "conjunctive"): 1.0 - 0.01**2, ("cutoff", "conjunctive"): 0.0 if delay > 1.0 else 1.0 - 0.01**2}
            for (law, rule), value in expected.items():
                fused = fuse(aligned.objects, groups, discount_evidence(evidence, aligned.objects, TrustModel(law=law)), CombinationModel(cpm_rule=rule))
                assert fused.objects["bel_exists"].to_numpy()[0] == pytest.approx(value, abs=1e-9), (law, rule, delay)
                assert fused.objects["pl_exists"].to_numpy()[0] == pytest.approx(1.0, abs=1e-9)
            states = fuse_states(aligned, groups)                                                             # equal covariances: CI averages
            assert states.state[0] == pytest.approx(aligned.state.mean(axis=0), abs=1e-9)
            ticks += 1
        assert ticks >= 25
        # the same through the whole evaluation: every object is there, so the calibration error is 1 minus the mean belief
        rows = evaluate_run(Run(obs=run.obs[run.obs["rx_station"] == "r"], truth=run.truth, noise=run.noise, info=run.info),
                            EvaluationModel(hold=None)).set_index("way")
        a = belief_of_age(delay)                                                # a message measured at t - delay arrives at t: the age is the delay
        assert (rows["ghost_objects"] == 0).all() and (rows["fused_objects"] == ticks).all()
        assert rows.loc["B3", "calibration_error"] == pytest.approx(1.0 - a, abs=2e-4)
        assert rows.loc["B4", "calibration_error"] == pytest.approx((1.0 - a)**2, abs=2e-4)
        assert rows.loc["B0", "calibration_error"] <= 0.01**2 + 1e-4 and rows.loc["B0", "brier"] <= 0.01**4 + 1e-4
        assert rows.loc["B1", "calibration_error"] == pytest.approx(1.0 if delay > 1.0 else 0.0, abs=2e-4)


def test_age_law_of_the_note_on_a_lossy_stream():
    """Periodic messages with loss: the mean age of the newest known report follows the law of task A3."""
    period, loss, delay = 0.5, 0.3, 0.2
    run = make_run(lossy(duration=150.0, cpm_period=period, loss=loss, delay=delay))                            # long enough for the mean to settle
    ages = []
    for ego in run.egos:
        for tick in run.replay(ego):
            ages.extend(tick.v2x["age_now"].tolist())
    # the ticks sample the phase at 0, 0.1, ..., 0.4 s, so the mean phase is (period - step) / 2 instead of period / 2
    expected = delay + (period - 0.1) / 2.0 + period * loss / (1.0 - loss)
    assert len(ages) > 50_000 and np.mean(ages) == pytest.approx(expected, rel=0.04)
    assert abs(np.mean(ages) - (delay + mean_age_periodic(period, loss))) < 0.1
    assert np.mean(ages) > delay + (period - 0.1) / 2.0 + 0.1                                                  # the loss makes the reports older
    v2x = run.obs[run.obs["src"] == "v2x"]
    # the share seen by the receiver is read on the delivered messages only, so its window of 10 holds at least one success
    assert v2x["pdr_window"].mean() == pytest.approx((1.0 + 9.0 * (1.0 - loss)) / 10.0, abs=0.02)
    assert np.allclose(v2x["age"].to_numpy(dtype=float), delay)


def test_convoy_is_easy_for_every_way():
    rows = evaluate_run(make_run(convoy(duration=10.0)), EvaluationModel(hold=None)).set_index("way")
    assert list(rows.index) == list(WAYS)
    assert (rows["wrong_groups"] == 0).all() and (rows["ghost_objects"] == 0).all() and (rows["stale_objects"] == 0).all()
    assert (rows["awareness_ratio"] >= 0.99).all() and (rows["entries"] == 20).all() and (rows["time_to_confident"] == 0.0).all()
    assert (rows["calibration_error"] <= 0.011).all()                                                           # every object is there: 1 - 0.99 at most
    assert (rows.loc[["B0", "B1", "B3", "B4"], "rmse"] == rows.loc["B0", "rmse"]).all() and 0.15 < rows.loc["B0", "rmse"] < SX
    assert rows.loc["B2", "rmse"] < 0.8 * rows.loc["B0", "rmse"]                                                # independent noises: the Kalman way wins
    assert rows["cbr_mean"].eq(0.05).all() and rows["pdr_mean"].eq(1.0).all() and rows["age_median"].eq(0.1).all()


def test_leaver_stale_rates_by_hand():
    """A car leaves at 8 s. Its last report is measured at 8 s; the stale belief of each way follows from the curve."""
    cars = tuple(Car("c%d" % k, x=25.0 * k, vx=15.0, t_out=8.0 if k == 2 else None, sends=(k == 1)) for k in range(5))
    rows = evaluate_run(make_run(Scenario(cars, duration=16.0, name="one-sender-leaver")), EvaluationModel(hold=None)).set_index("way")
    ticks = np.round(np.arange(91, 161) * 0.1, 6)                               # stale once older than 1 s: ticks 9.1 to 16.0
    a = belief_of_age(ticks - 8.0)
    n_receivers = 3                                                             # c0, c3 and c4 get the reports of c1
    assert (rows["stale_objects"] == n_receivers * len(ticks)).all()
    assert rows.loc["B0", "stale_rate"] == pytest.approx(0.99, abs=1e-4)
    assert rows.loc["B1", "stale_rate"] == 0.0
    assert rows.loc["B3", "stale_rate"] == pytest.approx(a.mean(), abs=1e-4)                                    # one report: the curve itself
    assert rows.loc["B4", "stale_rate"] == rows.loc["B3", "stale_rate"]
    assert rows.loc["B3", "stale_rate"] < 0.5 * rows.loc["B0", "stale_rate"]                                   # H3 on a stream where it must hold
    assert (rows["awareness_ratio"] > 0.8).all()                                   # c1 does not see c4 (75 m): c0 and c1 never hear of c4, nor c4 of c1
    # with every car sending, the old reports are mostly too vague to be grouped (coincidence test) and count one by one
    many = evaluate_run(make_run(leaver(duration=16.0)), EvaluationModel(hold=None)).set_index("way")
    assert (many["stale_objects"] > 4 * len(ticks)).all() and many.loc["B0", "stale_rate"] >= 0.99 and many.loc["B1", "stale_rate"] == 0.0
    assert a.min() <= many.loc["B3", "stale_rate"] <= a.max() and many.loc["B3", "stale_rate"] < 0.5 * many.loc["B0", "stale_rate"]


def test_calibration_holds_when_the_motion_follows_the_model():
    """Cars with a random acceleration of intensity q: the curve is exact, so B3 is calibrated and full trust is not.
    On straight lines (q_true = 0) the curve is too careful: coverage 100% and a worse calibration error than full trust.
    This is the risk of H1 found on the Artery runs, reproduced on a stream where the cause is known."""
    def stream(wobble):
        cars = tuple(Car("c%d" % k, x=30.0 * k, vx=10.0, wobble=wobble) for k in range(5))
        return make_run(Scenario(cars, duration=20.0, sensor_range=40.0, cpm_period=0.5, delay=(0.0, 2.0), loss=0.2, seed=3, name="wobble"))
    exact = evaluate_run(stream(Q_CAR), EvaluationModel(hold=None)).set_index("way")
    assert exact.loc["B3", "calibration_error"] < 0.03 and exact.loc["B0", "calibration_error"] > 0.5
    assert exact.loc["B3", "coverage"] > 0.7 and exact.loc["B0", "coverage"] == 0.0
    assert exact.loc["B3", "ghost_rate"] < 0.2 * exact.loc["B0", "ghost_rate"]
    straight = evaluate_run(stream(0.0), EvaluationModel(hold=None)).set_index("way")
    assert straight.loc["B3", "coverage"] == 1.0 and straight.loc["B3", "calibration_error"] > 0.1
    assert straight.loc["B3", "calibration_error"] > straight.loc["B0", "calibration_error"]                   # too careful


# ----------------------------------------------------------------------------
# The command
# ----------------------------------------------------------------------------
def test_command_writes_a_run_the_other_commands_can_read(tmp_path, capsys):
    folder = tmp_path / "run"
    assert synthetic.main([str(folder), "--scenario", "lossy", "--duration", "6", "--delay", "0.2", "0.8", "--loss", "0.1", "--seed", "4"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("B9 SYNTHETIC RUN") and "delay 0.2 to 0.8 s, loss 0.1" in text
    run = load_run(folder)
    assert run.info["synthetic"] and run.info["seed"] == 4 and len(run.egos) == 5
    exact = pd.read_parquet(folder / "observations.parquet")
    assert np.allclose(exact["gt_dist"], 0.0) and (run.obs["gt_dist"] > 0).any()                                # exact on disk, noisy once loaded
    assert replay_main([str(folder)]) == 0 and "PASS" in capsys.readouterr().out
    assert synthetic.main([str(tmp_path / "bad"), "--delay", "1", "2", "3"]) == 2
    assert synthetic.main([str(tmp_path / "bad"), "--loss", "1.5"]) == 2
    info = write_run(crossroads(duration=3.0), tmp_path / "again")
    assert info["rows"]["observations"] == len(pd.read_parquet(tmp_path / "again" / "observations.parquet"))
