"""Tests of the metrics of a run (task B8)."""
import numpy as np
import pandas as pd
import pytest

from aoc.analysis import metrics
from aoc.analysis.calibration import brier_score, expected_calibration_error
from aoc.analysis.metrics import WAYS, EvaluationModel, _Collector, _distance_confidence, evaluate_run, network_metrics
from aoc.fusion.alignment import align
from aoc.fusion.association import associate
from aoc.fusion.bba import build_evidence
from aoc.fusion.combination import CombinationModel, fuse, group_truth
from aoc.fusion.discounting import TrustModel, discount_evidence
from aoc.fusion.kinematics import fuse_states
from aoc.fusion.replay import Run, load_run


@pytest.fixture(scope="module")
def run(exported_runs):
    folder, _ = exported_runs["run_aoc_sensors"]
    return load_run(folder)


@pytest.fixture(scope="module")
def table(run):
    return evaluate_run(run, EvaluationModel(hold=2.0))


# ----------------------------------------------------------------------------
# Pieces
# ----------------------------------------------------------------------------
def test_collector_by_hand():
    c = _Collector()
    bel = np.array([0.95, 0.95, 0.95, 0.95, 0.15, 0.15])
    pl = np.array([1.0, 1.0, 1.0, 1.0, 0.4, 0.4])
    hit = np.array([True, True, True, True, False, True])
    c.add(bel, pl, hit)
    c.in_range, c.known = 10, 7
    c.se, c.n_there = 2.0 * 5 * 0.5**2, 5
    c.ghost_sum, c.n_ghost = 0.15, 1
    c.entries, c.confident_times = 4, [0.0, 0.3, 0.5]
    row = c.row()
    assert row["fused_objects"] == 6 and row["awareness_ratio"] == 0.7 and row["rmse"] == 0.5 and row["ghost_rate"] == 0.15
    assert row["calibration_error"] == pytest.approx(expected_calibration_error(bel, hit), abs=1e-4)
    assert row["brier"] == pytest.approx(brier_score(bel, hit), abs=1e-4)
    assert row["sharpness"] == pytest.approx(np.mean(pl - bel), abs=1e-4)
    assert row["coverage"] == pytest.approx(4 / 6, abs=1e-4)        # the high bin: 0.95 <= 1.0 <= 1.0 holds; the low bin: 0.15 <= 0.5 <= 0.4 fails
    assert row["time_to_confident"] == 0.3 and row["confident_share"] == 0.75
    assert row["stale_rate"] is None and row["stale_objects"] == 0
    empty = _Collector().row()
    assert empty["fused_objects"] == 0 and empty["calibration_error"] is None and empty["awareness_ratio"] is None and empty["coverage"] is None


def test_distance_confidence():
    cov = np.zeros((3, 4, 4))
    cov[0, :2, :2] = np.eye(2) * 0.09
    cov[1, :2, :2] = np.diag([0.09, 4.0])
    cov[2, :2, :2] = np.eye(2) * 1e-12
    out = _distance_confidence(cov, 1.5)
    assert out[0] == pytest.approx(1 - np.exp(-1.5**2 / (2 * 0.09)))
    assert out[1] == pytest.approx(1 - np.exp(-1.5**2 / (2 * 4.0)))          # the largest variance
    assert out[2] == pytest.approx(1.0)


def test_network_metrics(run):
    m = network_metrics(run)
    obs = run.obs
    received = obs[obs["src"] == "v2x"]
    assert m["received_rows"] == len(received)
    per_tick = obs.dropna(subset=["cbr"]).drop_duplicates(["rx_station", "t_sim"])                         # a tick with 15 objects counts once
    per_message = received.drop_duplicates(["rx_station", "tx_station", "t_rx"])
    assert m["cbr_mean"] == pytest.approx(per_tick["cbr"].astype(float).mean(), abs=1e-4)
    assert m["pdr_mean"] == pytest.approx(per_message["pdr_window"].astype(float).mean(), abs=1e-4)
    assert len(per_tick) < len(obs) and len(per_message) < len(received)                                     # the two means differ from the row means
    assert m["age_median"] == pytest.approx(received["age"].astype(float).median(), abs=1e-4)
    assert m["age_p95"] == pytest.approx(np.percentile(received["age"].astype(float), 95), abs=1e-4)
    bare = Run(obs=obs.drop(columns=["cbr", "pdr_window"]), truth=run.truth)
    assert network_metrics(bare)["cbr_mean"] is None and network_metrics(bare)["pdr_mean"] is None


def test_evaluation_model_checks():
    for bad in (dict(hold=0.0), dict(epsilon=-1.0), dict(known_belief=1.5), dict(trust=TrustModel(law="cutoff")), dict(period=0.0)):
        with pytest.raises(ValueError):
            EvaluationModel(**bad)
    assert EvaluationModel(hold=None).hold is None


# ----------------------------------------------------------------------------
# A whole run
# ----------------------------------------------------------------------------
def test_evaluation_rows(table):
    assert list(table["way"]) == list(WAYS) and len(table) == 5
    assert (table["hold"] == 2.0).all() and (table["wrong_groups"] == 0).all() and (table["no_ego_state"] == 0).all()
    for column in ("calibration_error", "brier", "coverage", "sharpness", "awareness_ratio", "rmse", "ghost_rate"):
        assert table[column].notna().all() and (table[column] >= 0).all(), column
    assert (table["coverage"] <= 1).all() and (table["awareness_ratio"] <= 1).all()
    assert (table["really_there"] <= table["fused_objects"]).all() and (table["ghost_objects"] + table["really_there"] == table["fused_objects"]).all()
    assert (table["stale_objects"] == 0).all() and table["stale_rate"].isna().all()                         # nobody leaves the sample run
    by_way = table.set_index("way")
    assert by_way.loc["B0", "ghost_rate"] == pytest.approx(0.99, abs=1e-3)                                  # full trust believes every ghost
    assert by_way.loc["B3", "ghost_rate"] < 0.5 * by_way.loc["B0", "ghost_rate"]                           # H3, first half: the stale confidence is halved
    assert by_way.loc["B3", "awareness_ratio"] >= by_way.loc["B0", "awareness_ratio"] - 0.02               # H3, second half: awareness within 2 points
    assert by_way.loc["B3", "calibration_error"] < by_way.loc["B0", "calibration_error"]
    assert (by_way.loc[["B0", "B1", "B3", "B4"], "rmse"] == by_way.loc["B0", "rmse"]).all()                 # same places: covariance intersection
    assert by_way.loc["B2", "rmse"] != by_way.loc["B0", "rmse"]                                              # the Kalman way
    assert 0.0 < by_way.loc["B0", "sharpness"] < 0.01 < by_way.loc["B3", "sharpness"]                        # full trust: pl 1 and bel at least 0.99
    assert (table["entries"] > 0).all() and (table["confident_share"] <= 1).all()


WAYS_BY_HAND = {"B0": ("full_trust", "conjunctive"), "B1": ("cutoff", "conjunctive"), "B3": ("aoc", "cautious"), "B4": ("aoc", "conjunctive")}


def test_evaluation_against_a_recount(run, table):
    """The confidence metrics of B0, B1, B3 and B4, counted again tick by tick with the modules themselves."""
    rows = {way: [] for way in WAYS_BY_HAND}
    in_range_total = 0
    known = {way: 0 for way in WAYS_BY_HAND}
    se = {way: 0.0 for way in WAYS_BY_HAND}
    for ego in run.egos:
        for tick in run.replay(ego, hold=2.0):
            truth = tick.truth
            inside = np.hypot(truth["x"] - tick.ego_state["x"], truth["y"] - tick.ego_state["y"]) <= 200.0
            names_in = set(truth["obj_id"][inside])
            in_range_total += len(names_in)
            aligned = align(tick)
            if not len(aligned):
                continue
            groups = associate(aligned)
            evidence = build_evidence(aligned.objects)
            states = fuse_states(aligned, groups)
            place = dict(zip(truth["obj_id"], zip(truth["x"], truth["y"])))
            for way, (law, rule) in WAYS_BY_HAND.items():
                fused = fuse(aligned.objects, groups, discount_evidence(evidence, aligned.objects, TrustModel(law=law)), CombinationModel(cpm_rule=rule))
                chosen, agree = group_truth(fused, aligned.objects)
                bel = fused.objects["bel_exists"].to_numpy()
                known_here = set()
                for k in range(len(fused)):
                    xy = place.get(chosen[k]) if agree[k] else None
                    error = np.hypot(states.state[k, 0] - xy[0], states.state[k, 1] - xy[1]) if xy is not None else np.inf
                    hit = error <= 1.5
                    rows[way].append((bel[k], hit))
                    if hit:
                        se[way] += error**2
                    if hit and bel[k] >= 0.5 and chosen[k] in names_in:
                        known_here.add(chosen[k])                                                            # a true object known twice counts once
                known[way] += len(known_here)
    by_way = table.set_index("way")
    for way, values in rows.items():
        values = np.array(values, dtype=float)
        assert by_way.loc[way, "fused_objects"] == len(values)
        assert by_way.loc[way, "calibration_error"] == pytest.approx(expected_calibration_error(values[:, 0], values[:, 1]), abs=1e-4)
        assert by_way.loc[way, "brier"] == pytest.approx(brier_score(values[:, 0], values[:, 1]), abs=1e-4)
        there = int(values[:, 1].sum())
        assert by_way.loc[way, "really_there"] == there and by_way.loc[way, "rmse"] == pytest.approx(np.sqrt(se[way] / (2 * there)), abs=1e-4)
        bins = np.minimum((values[:, 0] * 10).astype(int), 9)                                                 # coverage: bin by bin, by hand
        covered = sum((values[bins == b, 0].mean() <= values[bins == b, 1].mean() + 1e-9) * (bins == b).sum() for b in set(bins))
        assert by_way.loc[way, "coverage"] == pytest.approx(covered / len(values), abs=1e-4)
        ghosts = values[values[:, 1] == 0]
        assert by_way.loc[way, "ghost_rate"] == pytest.approx(ghosts[:, 0].mean(), abs=1e-4) and by_way.loc[way, "ghost_objects"] == len(ghosts)
        assert by_way.loc[way, "objects_in_range"] == in_range_total
        assert by_way.loc[way, "awareness_ratio"] == pytest.approx(known[way] / in_range_total, abs=1e-4)


def test_stale_objects_when_a_true_object_leaves(run):
    leaver = run.obs.loc[run.obs["src"] == "v2x", "gt_match_id"].value_counts().index[0]
    leaves_at = float(run.truth.loc[run.truth["obj_id"] == leaver, "t"].min()) + 3.0
    truth = run.truth[(run.truth["obj_id"] != leaver) | (run.truth["t"] <= leaves_at)]                     # it leaves the simulation 3 s after its entry
    obs = run.obs[(run.obs["gt_match_id"] != leaver) | (run.obs["t_meas"] <= leaves_at)]                  # and nobody measures it after that
    short = Run(obs=obs, truth=truth, noise=run.noise, info=run.info)
    table = evaluate_run(short, EvaluationModel(hold=None)).set_index("way")                               # no memory limit: its reports stay
    assert (table["stale_objects"] > 0).all()
    assert table.loc["B0", "stale_rate"] >= 0.99                                                             # full trust: at least the score of one report
    assert table.loc["B1", "stale_rate"] == pytest.approx(0.0, abs=1e-9)                                   # the cutoff drops them after 1 s
    assert table.loc["B3", "stale_rate"] < 0.1


def _entries_by_hand(run: Run, radius: float = 200.0) -> int:
    """How many times a true object enters the range of a vehicle, read from the truth alone."""
    truth = run.truth.astype({"t": float, "x": float, "y": float})
    count = 0
    for ego in run.egos:
        own = truth[truth["obj_id"] == ego].set_index("t")
        for other in truth["obj_id"].unique():
            if other == ego:
                continue
            both = own.join(truth[truth["obj_id"] == other].set_index("t"), rsuffix="_o", how="inner").sort_index()
            inside = (np.hypot(both["x"] - both["x_o"], both["y"] - both["y_o"]) <= radius).to_numpy()
            present = np.zeros(len(own), dtype=bool)
            present[own.index.get_indexer(both.index)] = inside                                               # absent from the simulation: out of range
            count += int(present[0]) + int((present[1:] & ~present[:-1]).sum())
    return count


def test_entries_and_time_to_confident(run, table):
    """The entries are counted from the truth alone; objects that come late are confident one tick or so after their entry."""
    assert (table["entries"] == _entries_by_hand(run)).all()
    assert (table["time_to_confident"] == 0.0).all() and (table["confident_share"] == 1.0).all()           # everybody is there from the first tick
    ego = run.egos[0]
    start = float(run.truth["t"].min())
    comes_at = {name: start + 1.0 + (k % 3) for k, name in enumerate(sorted(set(run.truth["obj_id"]) - {ego}))}   # the others come 1, 2 or 3 s late
    late_truth = run.truth["t"].astype(float) >= run.truth["obj_id"].map(comes_at).fillna(start).astype(float)
    late_obs = run.obs["t_meas"].astype(float) >= run.obs["gt_match_id"].map(comes_at).fillna(start).astype(float)
    shifted = Run(obs=run.obs[late_obs & (run.obs["rx_station"] == ego)], truth=run.truth[late_truth], noise=run.noise, info=run.info)
    rows = evaluate_run(shifted, EvaluationModel(hold=2.0)).set_index("way")
    assert (rows["entries"] == _entries_by_hand(shifted)).all() and (rows["entries"] > 0).all()
    assert (rows["time_to_confident"] >= 0.0).all() and (rows["time_to_confident"] <= 0.2).all()           # the first report comes at the same tick or the next
    assert (rows["confident_share"] <= 1.0).all() and (rows["confident_share"] > 0.9).all()
    assert (rows["entries"] < table.set_index("way")["entries"]).all()                                      # one vehicle instead of all of them


def test_evaluation_of_odd_runs(run):
    sensors_only = Run(obs=run.obs[run.obs["src"] != "v2x"], truth=run.truth, noise=run.noise, info=run.info)
    table = evaluate_run(sensors_only).set_index("way")
    assert table.loc["B0", "ghost_rate"] == table.loc["B3", "ghost_rate"] or np.isnan(table.loc["B3", "ghost_rate"])      # no received object to discount
    empty = evaluate_run(Run(obs=run.obs.iloc[:0], truth=run.truth))
    assert len(empty) == 5 and (empty["fused_objects"] == 0).all() and empty["calibration_error"].isna().all()
    assert empty["awareness_ratio"].isna().all() and (empty["ticks"] == 0).all()                            # no vehicle, no tick


def test_command(exported_runs, capsys, tmp_path):
    folder, _ = exported_runs["run_aoc_sensors"]
    out = tmp_path / "rows.parquet"
    assert metrics.main([str(folder), "--hold", "2", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert text.startswith("B8 EVALUATION") and "forgotten after 2 s" in text and "rows written to" in text
    rows = pd.read_parquet(out)
    assert list(rows["way"]) == list(WAYS)
    for _, row in rows.iterrows():
        line = next(line for line in text.splitlines() if line.startswith("  %s " % row["way"]))
        assert "%.3f" % row["calibration_error"] in line and "%.1f%%" % (100 * row["awareness_ratio"]) in line
    assert metrics.main([str(folder), "--hold", "0", "--out", str(tmp_path / "rows.csv")]) == 0
    assert "kept until the end of the run" in capsys.readouterr().out and len(pd.read_csv(tmp_path / "rows.csv")) == 5
    assert metrics.main([str(folder), "--out", str(tmp_path / "rows.txt")]) == 2
    assert metrics.main([str(folder / "nothing_here")]) == 2
    assert metrics.main([str(folder), "--q", "car=1"]) == 2
