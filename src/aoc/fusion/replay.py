"""The replay loader (task B2).

A simulation run is made once in Artery and exported once (``aoc-export``). This module loads the
exported run and plays it again for one vehicle, tick by tick, the way its fusion would live it.

    from aoc.fusion.replay import load_run

    run = load_run("results/.../parquet")            # the noise model is applied here
    for tick in run.replay("flow0.0"):               # one tick every 0.1 s
        tick.t           # time of the tick in s
        tick.local       # what the on-board sensors of the vehicle measured at this tick
        tick.v2x_new     # the CPM objects received since the last tick
        tick.v2x         # the newest state known from each sender about each object, with its age now
        tick.truth       # the true objects at this tick (for evaluation only)

``local``, ``v2x_new`` and ``v2x`` have the columns of the observations table, plus ``age_now``: the time
of the tick minus the measurement time. Text columns hold Python strings, and None where a value is missing.
Each table of a tick is a copy: changing it does not affect the next ticks.

What a fusion may read: every column, except those whose name starts with ``gt_`` (ground truth).

Run this module to check a run:  python -m aoc.fusion.replay RUN_FOLDER      (command ``aoc-replay``)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np
import pandas as pd

from .noise import NoiseModel, apply_noise
from .sensors import DEFAULT_TABLES, DEFAULT_WEATHER, SensorTables, apply_detection

_US = 1_000_000          # times are compared in whole microseconds
TRUTH_COLUMNS = ("gt_match_id", "gt_dist", "gt_x", "gt_y", "gt_vx", "gt_vy", "gt_gap")


def _plain(frame: pd.DataFrame) -> pd.DataFrame:
    """The same table with plain column types, so that a tick costs little.

    Text columns become Python strings, with None where a value is missing.
    Whole-number columns that allow missing values become float64, with NaN where a value is missing.
    So the sensor rows and the CPM rows of a tick have the same column types.
    """
    out = {}
    for name in frame.columns:
        col = frame[name]
        if pd.api.types.is_string_dtype(col.dtype) or col.dtype == object:
            plain = col.astype(object)
            out[name] = plain.where(col.notna().to_numpy(), None)
        elif pd.api.types.is_extension_array_dtype(col.dtype) and pd.api.types.is_integer_dtype(col.dtype):
            out[name] = col.astype("float64")
        else:
            out[name] = col
    return pd.DataFrame(out, index=frame.index)


def _truth_step_us(truth: pd.DataFrame) -> int:
    """Time between two steps of the truth table in microseconds (0 if it cannot be found)."""
    if not len(truth):
        return 0
    times = np.unique(np.round(truth["t"].to_numpy(dtype=float) * _US).astype(np.int64))
    return int(np.diff(times).min()) if len(times) > 1 else 0


@dataclass
class Tick:
    """What one vehicle knows at one fusion time."""

    t: float                     # time of the tick in s
    ego: str                     # the vehicle (SUMO id)
    ego_state: dict | None       # true x, y, vx, vy, speed, heading_deg of the vehicle at t; None if it is not in the simulation
    local: pd.DataFrame          # sensor detections of this tick
    v2x_new: pd.DataFrame        # CPM objects received since the last tick
    v2x: pd.DataFrame            # newest state per (sender, object) among all CPMs received so far
    truth: pd.DataFrame          # true objects at t, the vehicle itself left out


@dataclass
class Run:
    """One exported simulation run, ready for replay."""

    obs: pd.DataFrame                    # the observations table, with noise if a noise model was given
    truth: pd.DataFrame                  # the true state of every object at every step
    noise: NoiseModel | None = None      # the noise model that was applied, or None
    info: dict = field(default_factory=dict)       # content of export_summary.json
    dropped: int = 0                     # rows left out at loading (no true object or no time)
    weather: str = "clear"               # the weather of the replay (the detection and the reliability tables)
    sensors: object = None               # the SensorTables applied, or None
    detection: dict = field(default_factory=dict)  # the counts of apply_detection (rows the sensors missed)

    @property
    def egos(self) -> list[str]:
        """The vehicles that got at least one observation, in order of name."""
        return sorted(self.obs["rx_station"].dropna().unique().tolist())

    def replay(self, ego: str, period: float = 0.1, t_start: float | None = None, t_end: float | None = None,
               hold: float | None = None, drop_self: bool = True) -> Iterator[Tick]:
        """Yields one Tick per fusion time for one vehicle.

        period     time between two ticks in s (0.1 = the 10 Hz of the plan); a multiple of the step of the truth table
        t_start    first tick (default: the first tick where the vehicle has a row)
        t_end      last tick (default: the last simulation step, or the last true step of the vehicle if it leaves before)
        hold       an object known from a CPM leaves ``tick.v2x`` when its age is above this value in s.
                   Default None: it stays until the end. The fusion decides what an old object is worth.
        drop_self  leave out the CPM objects that describe the vehicle itself (default True).
                   The loader finds them with the ground truth. A real vehicle would use its own position.
        """
        if not period > 0.0:
            raise ValueError("period must be positive")
        period_us = int(round(period * _US))
        step_us = _truth_step_us(self.truth)
        if step_us and period_us % step_us:
            raise ValueError("period %g s is not a multiple of the step of the truth table (%g s)" % (period, step_us / _US))
        mine = self.obs[self.obs["rx_station"] == ego]
        if not np.isfinite(mine[["t_sim", "t_rx", "t_meas"]].to_numpy(dtype=float, na_value=np.nan)).all():
            raise ValueError("some rows of %s have no time (t_sim, t_rx or t_meas). Load the run with missing=\"drop\"." % ego)
        is_v2x = (mine["src"] == "v2x").to_numpy(dtype=bool, na_value=False)
        local, v2x = mine[~is_v2x], mine[is_v2x]
        if drop_self:
            v2x = v2x[(v2x["gt_match_id"] != ego).to_numpy(dtype=bool, na_value=True)]

        def tick_index(times: pd.Series) -> np.ndarray:
            """Index of the first tick at or after each time."""
            return -((-np.round(times.to_numpy(dtype=float) * _US).astype(np.int64)) // period_us)

        local = _plain(local.assign(_k=tick_index(local["t_sim"])).sort_values(["_k", "sensor", "obj_id"], kind="stable"))
        v2x = _plain(v2x.assign(_k=tick_index(v2x["t_rx"])).sort_values(["_k", "t_rx", "tx_station", "obj_id"], kind="stable"))
        truth = self.truth.assign(_k=tick_index(self.truth["t"]))
        truth = truth[np.round(truth["t"].to_numpy(dtype=float) * _US).astype(np.int64) % period_us == 0]   # steps on a tick
        truth = _plain(truth.sort_values(["_k", "obj_id"], kind="stable"))

        first_seen = min([int(f["_k"].iloc[0]) for f in (local, v2x) if len(f)], default=None)
        if first_seen is None and t_start is None:
            return
        k_first = first_seen if t_start is None else int(np.ceil(round(t_start * _US) / period_us))
        if t_end is None:
            k_last = int(self.truth["t"].max() * _US + 0.5) // period_us if len(self.truth) else first_seen
            k_last = max(k_last, max([int(f["_k"].iloc[-1]) for f in (local, v2x) if len(f)], default=k_last))
            mine_true = truth.loc[(truth["obj_id"] == ego).to_numpy(dtype=bool), "_k"]
            if len(mine_true):                                                   # a vehicle that left the simulation stops here
                k_last = min(k_last, int(mine_true.iloc[-1]))
        else:
            k_last = int(np.floor(round(t_end * _US) / period_us))

        local_k, v2x_k, truth_k = (f["_k"].to_numpy() for f in (local, v2x, truth))
        # age at the tick of the row; for the known objects it is set again at every tick
        local = local.assign(age_now=local_k * (period_us / _US) - local["t_meas"].to_numpy(dtype=float)).drop(columns="_k")
        v2x = v2x.assign(age_now=v2x_k * (period_us / _US) - v2x["t_meas"].to_numpy(dtype=float)).drop(columns="_k")
        truth_is_ego = (truth["obj_id"] == ego).to_numpy(dtype=bool)
        me = truth[truth_is_ego]
        me_k = truth_k[truth_is_ego]
        me_state = me[["x", "y", "vx", "vy", "speed", "heading_deg"]].to_numpy(dtype=float)
        truth, truth_k = truth[~truth_is_ego].drop(columns="_k"), truth_k[~truth_is_ego]
        local, v2x, truth = local.copy(), v2x.copy(), truth.copy()          # one block per column type
        v2x_sender = v2x["tx_station"].to_numpy(dtype=object)
        v2x_object = v2x["obj_id"].to_numpy(dtype=object)
        v2x_t_meas = v2x["t_meas"].to_numpy(dtype=float)
        v2x_meas_us = np.round(v2x_t_meas * _US).astype(np.int64)          # two copies of one measurement compare as equal
        hold_limit = None if hold is None else float(hold) + 1e-9

        newest: dict[tuple, int] = {}          # (sender, object) -> row of v2x with the newest measurement

        def take_in(first: int, last: int) -> None:
            for row in range(first, last):
                key = (v2x_sender[row], v2x_object[row])
                if key not in newest or v2x_meas_us[row] >= v2x_meas_us[newest[key]]:        # a tie: the last received wins
                    newest[key] = row

        take_in(0, int(np.searchsorted(v2x_k, k_first, side="left")))      # CPMs received before the first tick count as known

        for k in range(k_first, k_last + 1):
            t = k * period_us / _US
            a, b = np.searchsorted(local_k, [k, k + 1], side="left")
            now_local = local.iloc[a:b].copy()

            a, b = np.searchsorted(v2x_k, [k, k + 1], side="left")
            take_in(a, b)
            now_new = v2x.iloc[a:b].copy()

            if hold_limit is not None:
                for key in [key for key, row in newest.items() if t - v2x_t_meas[row] > hold_limit]:
                    del newest[key]
            rows = np.fromiter(newest.values(), dtype=np.int64, count=len(newest))
            rows.sort()
            known = v2x.take(rows)
            known["age_now"] = t - v2x_t_meas[rows]

            a, b = np.searchsorted(truth_k, [k, k + 1], side="left")
            at = int(np.searchsorted(me_k, k, side="left"))
            ego_state = None
            if at < len(me_k) and me_k[at] == k:
                ego_state = dict(zip(("x", "y", "vx", "vy", "speed", "heading_deg"), me_state[at].tolist()))
            yield Tick(t=t, ego=ego, ego_state=ego_state, local=now_local, v2x_new=now_new, v2x=known, truth=truth.iloc[a:b].copy())


def run_weather(obs: pd.DataFrame, info: dict) -> str | None:
    """The weather written by the exporter (option --weather), or None."""
    weather = info.get("weather") if isinstance(info, dict) else None
    if not weather and "weather" in obs.columns:
        values = obs["weather"].dropna().unique()
        weather = str(values[0]) if len(values) == 1 and str(values[0]) else None
    return str(weather) if weather else None


def load_run(folder: str | os.PathLike, noise: NoiseModel | None | bool = True, missing: str = "raise",
             sensors: SensorTables | None | bool = True, weather: str | None = None) -> Run:
    """Loads one exported run: the folder that holds observations.parquet and truth.parquet.

    noise    True: the default noise model (sigma_x 0.3 m, sigma_v 0.5 m/s, seed 0).
             A NoiseModel: this one. None or False: no noise, the logged values are kept as they are.
    missing  what to do with observations that have no true object or no time: "raise" or "drop".
             The number of rows left out is in ``run.dropped``.
    sensors  True: the default sensor tables (the detection probabilities of the plan, seed 0): the rows
             that a sensor missed in this weather are left out, and every row gets its ``range_m``.
             A SensorTables: this one. None or False: every logged row is kept.
    weather  the weather of the replay: "clear" or "rain" (or any weather of the tables). None: the weather
             the exporter wrote (option --weather), else "clear". It is kept in ``run.weather`` for the
             reliability tables of the discounting.
    """
    if missing not in ("raise", "drop"):
        raise ValueError('missing must be "raise" or "drop"')
    folder = os.fspath(folder)
    for name in ("observations.parquet", "truth.parquet"):
        if not os.path.isfile(os.path.join(folder, name)):
            raise FileNotFoundError("%s not found in %s. Export the run first with aoc-export." % (name, folder))
    obs = pd.read_parquet(os.path.join(folder, "observations.parquet"))
    truth = pd.read_parquet(os.path.join(folder, "truth.parquet"))
    info = {}
    if os.path.isfile(os.path.join(folder, "export_summary.json")):
        with open(os.path.join(folder, "export_summary.json"), encoding="utf-8") as fh:
            info = json.load(fh)
    n_rows = len(obs)
    timed = np.isfinite(obs[["t_sim", "t_rx", "t_meas"]].to_numpy(dtype=float, na_value=np.nan)).all(axis=1)
    timed &= obs["rx_station"].notna().to_numpy()
    if not timed.all():
        if missing == "raise":
            raise ValueError("%d of %d rows have no time or no receiver. Use missing=\"drop\" to leave them out."
                             % (int((~timed).sum()), n_rows))
        obs = obs[timed]
    model = NoiseModel() if noise is True else (noise or None)
    if model is not None:
        obs = apply_noise(obs, truth, model, missing=missing)
    dropped = n_rows - len(obs)
    weather = weather or run_weather(obs, info) or DEFAULT_WEATHER
    tables = DEFAULT_TABLES if sensors is True else (sensors or None)
    counts: dict = {}
    if tables is not None:
        if tables.sensors and weather not in tables.weathers:
            raise ValueError("the sensor tables have no weather %r (they have: %s)" % (weather, ", ".join(tables.weathers)))
        obs, counts = apply_detection(obs, truth, tables, weather)
    return Run(obs=obs, truth=truth, noise=model, info=info, dropped=dropped, weather=weather, sensors=tables, detection=counts)


# ----------------------------------------------------------------------------
# Check of a run from the command line
# ----------------------------------------------------------------------------
def _measurement_ids(obs: pd.DataFrame) -> pd.DataFrame:
    """The four things that name a measurement, one row per row of the table."""
    is_v2x = (obs["src"] == "v2x").to_numpy(dtype=bool, na_value=False)
    return pd.DataFrame({"who": obs["rx_station"].where(~is_v2x, obs["tx_station"]).astype(object),
                         "what": obs["sensor"].where(~is_v2x, "cpm").astype(object),
                         "obj": obs["obj_id"].astype(object),
                         "t_us": np.round(obs["t_meas"].to_numpy(dtype=float) * _US).astype(np.int64)}, index=obs.index)


def check_run(run: Run, period: float = 0.1) -> dict:
    """Replays every vehicle of a run and checks the noise against its model. Returns the numbers and the problems.

    The checks do not trust the columns written by the noise model. The true state is looked up again
    in the truth table, by another path (an exact match on the time), for every row whose measurement
    time is a step of the truth table.
    """
    obs, problems = run.obs, []
    out = {"rows": int(len(obs)), "vehicles": len(run.egos), "rows_left_out_at_loading": int(run.dropped)}
    if not len(obs):
        problems.append("the run holds no observation")
    if run.noise is not None and len(obs):
        ids = _measurement_ids(obs)
        # 1. every row of one measurement carries the same values
        spread = obs[["x", "y", "vx", "vy"]].groupby([ids[c] for c in ids.columns], dropna=False).nunique()
        out["measurements"] = int(len(spread))
        if int(spread.to_numpy().max()) > 1:
            problems.append("two rows of one measurement carry different values")

        # 2. the error against the truth table, looked up by an exact match on (object, time)
        truth = run.truth.assign(t_us=np.round(run.truth["t"].to_numpy(dtype=float) * _US).astype(np.int64))
        truth = truth.drop_duplicates(["obj_id", "t_us"]).set_index(["obj_id", "t_us"])[["x", "y", "vx", "vy"]]
        at = pd.MultiIndex.from_arrays([obs["gt_match_id"].astype(object), ids["t_us"]])
        true = truth.reindex(at).to_numpy(dtype=float)
        on_step = np.isfinite(true).all(axis=1)
        sx, sv = run.noise.sigmas(obs["meas_src"])
        scaled = (obs[["x", "y", "vx", "vy"]].to_numpy(dtype=float) - true) / np.column_stack([sx, sx, sv, sv])
        once = ~ids.duplicated().to_numpy()
        use = on_step & once & (sx > 0) & (sv > 0)
        e = scaled[use]
        n = len(e)
        out["rows_checked_against_truth"] = int(on_step.sum())
        out["measurements_checked"] = int(n)
        out["noise_mean"] = [round(float(v), 4) for v in e.mean(axis=0)] if n else []
        out["noise_std"] = [round(float(v), 4) for v in e.std(axis=0)] if n else []
        out["noise_largest_correlation"] = None
        out["rows_outside_truth_time"] = int((obs["gt_gap"] > 0).sum())
        if (sx > 0).any() and (sv > 0).any():
            if n < 30:
                problems.append("only %d measurements can be checked against the truth table" % n)
            else:
                if np.abs(e.mean(axis=0)).max() > 5.0 / np.sqrt(n):                      # 5 standard errors
                    problems.append("the mean of the noise is not zero")
                if np.abs(e.std(axis=0) - 1.0).max() > 5.0 / np.sqrt(2.0 * n):
                    problems.append("the spread of the noise does not match the model")
                corr = np.corrcoef(e.T) - np.eye(4)
                out["noise_largest_correlation"] = round(float(np.abs(corr).max()), 4)
                if np.abs(corr).max() > 5.0 / np.sqrt(n):
                    problems.append("the errors on x, y, vx, vy are linked")
        if out["rows_outside_truth_time"] > 0.01 * len(obs):
            problems.append("more than 1% of the rows have a measurement time outside the logged life of their true object")

    start = time.perf_counter()
    ticks = n_local = n_new = n_known = n_no_ego = 0
    oldest = 0.0
    for ego in run.egos:
        for tick in run.replay(ego, period=period):
            ticks += 1
            n_local += len(tick.local)
            n_new += len(tick.v2x_new)
            n_known += len(tick.v2x)
            n_no_ego += tick.ego_state is None
            if len(tick.v2x):
                oldest = max(oldest, float(tick.v2x["age_now"].max()))
                if tick.v2x.duplicated(["tx_station", "obj_id"]).any():
                    problems.append("an object is twice in the known objects of %s at %.1f s" % (ego, tick.t))
    out.update(ticks=ticks, local_rows=n_local, v2x_new_rows=n_new, v2x_known_mean=round(n_known / ticks, 2) if ticks else 0.0,
               oldest_known_age_s=round(oldest, 3), ticks_without_ego_state=int(n_no_ego),
               replay_seconds=round(time.perf_counter() - start, 2))
    is_v2x = (obs["src"] == "v2x").to_numpy(dtype=bool, na_value=False)
    own = (obs["gt_match_id"] == obs["rx_station"]).to_numpy(dtype=bool, na_value=False)
    out["v2x_rows_about_the_receiver"] = int((is_v2x & own).sum())
    if n_local != int((~is_v2x).sum()):
        problems.append("the replay gave %d sensor rows, the table holds %d" % (n_local, int((~is_v2x).sum())))
    if n_new != int((is_v2x & ~own).sum()):
        problems.append("the replay gave %d CPM rows, the table holds %d" % (n_new, int((is_v2x & ~own).sum())))
    if n_no_ego > 0.01 * max(ticks, 1):
        problems.append("the true state of the vehicle is missing on %d ticks" % n_no_ego)
    out["problems"] = list(dict.fromkeys(problems))
    out["result"] = "PASS" if not problems else "FAIL"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Load one exported run, apply the noise model, replay every vehicle and print the checks.")
    ap.add_argument("run_dir", help="folder with observations.parquet and truth.parquet (written by aoc-export)")
    ap.add_argument("--sigma-x", type=float, default=0.3, help="position noise per axis in m (default 0.3)")
    ap.add_argument("--sigma-v", type=float, default=0.5, help="speed noise per axis in m/s (default 0.5)")
    ap.add_argument("--seed", type=int, default=0, help="seed of the noise (default 0)")
    ap.add_argument("--period", type=float, default=0.1, help="time between two fusion ticks in s (default 0.1)")
    ap.add_argument("--drop-unmatched", action="store_true", help="leave out the rows without a true object or without a time")
    ap.add_argument("--weather", default=None, help="weather of the replay: clear or rain (default: the one the exporter wrote, else clear)")
    ap.add_argument("--no-sensor-tables", action="store_true", help="keep every logged sensor row (no detection probability)")
    args = ap.parse_args(argv)

    try:
        model = NoiseModel(sigma_x=args.sigma_x, sigma_v=args.sigma_v, seed=args.seed)
        run = load_run(args.run_dir, noise=model, missing="drop" if args.drop_unmatched else "raise",
                       sensors=not args.no_sensor_tables, weather=args.weather)
        c = check_run(run, period=args.period)
    except (FileNotFoundError, ValueError) as err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 2
    four = "x %s, y %s, vx %s, vy %s"
    print("B2 REPLAY CHECK")
    print("  run:                 %s" % run.info.get("run_id", os.path.abspath(args.run_dir)))
    print("  noise model:         sigma_x %.2f m, sigma_v %.2f m/s, seed %d" % (model.sigma_x, model.sigma_v, model.seed))
    d = run.detection
    if d:
        print("  sensor tables:       weather %s, seed %d: %d of %d sensor rows missed, %d of %d CPM rows not sent (%d without a sensor row of the sender)"
              % (run.weather, run.sensors.seed, d["sensor_rows_dropped"], d["sensor_rows"], d["cpm_rows_dropped"], d["cpm_rows"], d["cpm_rows_without_sensor_row"]))
    else:
        print("  sensor tables:       none (weather %s)" % run.weather)
    print("  observations:        %d rows, %d vehicles (%d rows left out at loading)" % (c["rows"], c["vehicles"], c["rows_left_out_at_loading"]))
    print("  measurements:        %d different ones (one measurement can reach several vehicles)" % c.get("measurements", 0))
    print("  checked against the truth table: %d measurements" % c.get("measurements_checked", 0))
    if c.get("noise_mean"):
        print("  noise, mean:         " + four % tuple(c["noise_mean"]) + " (in units of sigma, expected 0)")
        print("  noise, spread:       " + four % tuple(c["noise_std"]) + " (in units of sigma, expected 1)")
        print("  noise, largest link between x, y, vx, vy: %s (expected 0)" % c["noise_largest_correlation"])
    print("  rows with a measurement time outside the logged life of the true object: %d" % c.get("rows_outside_truth_time", 0))
    print("  replay:              %d ticks of %g s in %.1f s" % (c["ticks"], args.period, c["replay_seconds"]))
    print("  per tick, average:   %.2f sensor rows, %.2f new CPM objects, %.2f known CPM objects"
          % (c["local_rows"] / max(c["ticks"], 1), c["v2x_new_rows"] / max(c["ticks"], 1), c["v2x_known_mean"]))
    print("  oldest known CPM object: %.1f s" % c["oldest_known_age_s"])
    print("  CPM rows about the receiver itself, left out: %d" % c["v2x_rows_about_the_receiver"])
    print("  result:              %s%s" % (c["result"], "" if not c["problems"] else " (" + "; ".join(c["problems"]) + ")"))
    return 0 if not c["problems"] else 3


if __name__ == "__main__":
    sys.exit(main())
