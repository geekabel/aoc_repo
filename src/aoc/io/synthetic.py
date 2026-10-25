"""Synthetic streams (task B9): small runs whose right answer is known.

A run from Artery is realistic, but nobody knows what the right fusion result is. A synthetic run is the
opposite: a few cars on straight lines, an on-board sensor with a known range, messages with a known delay
and a known loss, a car that leaves at a known time. On such a stream the pipeline must give numbers that
can be computed by hand or from the formulas of the theory note. The tests of ``tests/test_synthetic.py``
do exactly that, for the whole pipeline v1 (align, associate, evidence, discount, fuse, kinematics, metrics).

    from aoc.io.synthetic import Car, Scenario, make_run, write_run

    cars = (Car("a", x=0.0, y=0.0, vx=10.0), Car("b", x=50.0, y=5.0, vx=10.0), Car("c", x=100.0, y=0.0, vx=-10.0))
    run = make_run(Scenario(cars, duration=10.0, delay=0.2, loss=0.1))      # a Run, as load_run gives one
    write_run(Scenario(cars), "runs/synthetic")                             # parquet files for the aoc-* commands

What the stream holds
  Every car is a vehicle with an on-board sensor ("radar") that sees every other car within ``sensor_range``
  at every step, and sends a CPM every ``cpm_period`` with the cars it sees, measured at the time of sending.
  A CPM reaches every other car within ``cpm_range``; each copy is lost with probability ``loss`` and
  arrives after ``delay`` (a fixed value, or a value drawn between two bounds). The measurement noise is the
  one of the replay (``NoiseModel``): the values written in the table are exact, and ``make_run`` adds the noise
  the way ``load_run`` does.

Motion
  Constant speed on a straight line by default. ``turn`` adds a constant turn rate (degrees per second).
  ``wobble`` adds a random acceleration of intensity q (m^2/s^3), the motion model of the alignment:
  with wobble equal to the motion value of the class, the reliability curve is exact for this stream.

Run this module to write a run:  python -m aoc.io.synthetic OUT_FOLDER [--scenario NAME]   (command ``aoc-synthetic``)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..fusion.noise import NoiseModel, apply_noise
from ..fusion.replay import Run
from .export import OBS_COLUMNS, dcc_state, write_parquet

_US = 1_000_000


@dataclass(frozen=True)
class Car:
    """One true object, which is also a vehicle with a sensor and a radio.

    name        its SUMO-like id
    x, y        place at ``t_in`` in m (x east, y south, as in Artery)
    vx, vy      speed in m/s
    t_in        time it enters the simulation in s
    t_out       time it leaves, or None to stay until the end
    cls         class of the observations table (passenger_car, pedestrian, ...)
    turn        turn rate in degrees per second (0: straight line)
    wobble      intensity q of a random acceleration in m^2/s^3 (0: constant speed)
    sends       whether it sends CPMs
    """

    name: str
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    t_in: float = 0.0
    t_out: float | None = None
    cls: str = "passenger_car"
    turn: float = 0.0
    wobble: float = 0.0
    sends: bool = True


@dataclass(frozen=True)
class Scenario:
    """The settings of one synthetic run."""

    cars: tuple[Car, ...]
    duration: float = 20.0            # end of the run in s
    step: float = 0.1                 # step of the truth and of the sensors in s
    sensor_range: float = 60.0        # range of the on-board sensor in m
    cpm_period: float = 0.1           # time between two CPMs of one car in s (a multiple of step)
    cpm_range: float = 400.0          # a CPM reaches the cars within this distance in m
    delay: float | tuple[float, float] = 0.1     # age at reception in s: fixed, or drawn between two bounds
    loss: float = 0.0                 # probability that one copy of a CPM is lost
    cbr: float = 0.05                 # channel busy ratio written in every row
    noise: NoiseModel = field(default_factory=NoiseModel)
    seed: int = 0                     # seed of the delays, the losses and the wobble
    name: str = "synthetic"

    def __post_init__(self):
        if not self.cars or len({car.name for car in self.cars}) != len(self.cars):
            raise ValueError("a scenario needs at least one car, and every car needs its own name")
        if not (self.step > 0 and self.duration > self.step and self.cpm_period > 0):
            raise ValueError("step and cpm_period must be positive, and duration longer than one step")
        if abs(round(self.cpm_period / self.step) * self.step - self.cpm_period) > 1e-9:
            raise ValueError("cpm_period must be a multiple of step")
        if not 0.0 <= self.loss < 1.0:
            raise ValueError("loss must be between 0 (included) and 1 (excluded)")
        bounds = tuple(self.delay) if isinstance(self.delay, (tuple, list)) else (self.delay, self.delay)
        if len(bounds) != 2 or not all(isinstance(b, (int, float)) for b in bounds) or not (0.0 <= bounds[0] <= bounds[1]):
            raise ValueError("delay must be a value that is not negative, or a pair (low, high)")
        for car in self.cars:
            if car.t_in < 0 or (car.t_out is not None and car.t_out <= car.t_in) or car.wobble < 0:
                raise ValueError("car %s: t_in must not be negative, t_out must be after t_in, wobble must not be negative" % car.name)
            if car.t_in >= self.duration:
                raise ValueError("car %s enters at %g s, after the end of the run" % (car.name, car.t_in))
            for when in (car.t_in, car.t_out):
                if when is not None and abs(round(when / self.step) * self.step - when) > 1e-9:
                    raise ValueError("car %s: t_in and t_out must be multiples of the step (%g s)" % (car.name, self.step))

    @property
    def delay_bounds(self) -> tuple[float, float]:
        return tuple(self.delay) if isinstance(self.delay, (tuple, list)) else (self.delay, self.delay)


def _round_time(value: float) -> float:
    """Rounds a time to the microsecond, so that 0.1 * 3 and 0.3 are the same time."""
    return round(value * _US) / _US


# ----------------------------------------------------------------------------
# The truth
# ----------------------------------------------------------------------------
def make_truth(scenario: Scenario) -> pd.DataFrame:
    """The true state of every car at every step, with the columns of the truth table of the exporter."""
    rng = np.random.default_rng(scenario.seed + 1)
    dt = scenario.step
    frames = []
    for code, car in enumerate(scenario.cars):
        t_end = scenario.duration if car.t_out is None else min(car.t_out, scenario.duration)
        n = int(round((t_end - car.t_in) / dt)) + 1
        if n < 1:
            continue
        t = _round_time(car.t_in) + np.arange(n) * dt
        x = np.empty((n, 2))
        v = np.empty((n, 2))
        x[0], v[0] = (car.x, car.y), (car.vx, car.vy)
        angle = np.deg2rad(car.turn) * dt
        rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        kick = np.sqrt(car.wobble * dt) * rng.standard_normal((n, 2)) if car.wobble > 0 else np.zeros((n, 2))
        for k in range(1, n):
            v[k] = rot @ v[k - 1] + kick[k]
            x[k] = x[k - 1] + 0.5 * (v[k - 1] + v[k]) * dt
        speed = np.hypot(v[:, 0], v[:, 1])
        heading = np.degrees(np.arctan2(v[:, 0], -v[:, 1])) % 360.0                 # clockwise from north, y grows south
        frames.append(pd.DataFrame({"t": np.round(t, 6), "obj_id": car.name, "x": x[:, 0], "y": x[:, 1], "speed": speed,
                                    "heading_deg": heading, "vx": v[:, 0], "vy": v[:, 1], "length": 4.5, "width": 1.8,
                                    "station_type": 5, "class": car.cls}))
    truth = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["t", "obj_id", "x", "y", "speed", "heading_deg", "vx", "vy", "length", "width", "station_type", "class"])
    truth = truth.sort_values(["t", "obj_id"], kind="stable").reset_index(drop=True)
    truth["obj_id"] = truth["obj_id"].astype("string")
    truth["class"] = truth["class"].astype("string")
    truth["station_type"] = truth["station_type"].astype("Int64")
    return truth


# ----------------------------------------------------------------------------
# The observations
# ----------------------------------------------------------------------------
def _grid(truth: pd.DataFrame, names: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Times, places (steps x cars x 2, NaN when absent) and presence (steps x cars)."""
    times = np.unique(np.round(truth["t"].to_numpy(dtype=float), 6))
    place = np.full((len(times), len(names), 2), np.nan)
    code = {name: k for k, name in enumerate(names)}
    rows_t = np.searchsorted(times, np.round(truth["t"].to_numpy(dtype=float), 6))
    rows_c = np.array([code[name] for name in truth["obj_id"].astype(object)])
    place[rows_t, rows_c, 0] = truth["x"].to_numpy(dtype=float)
    place[rows_t, rows_c, 1] = truth["y"].to_numpy(dtype=float)
    return times, place, np.isfinite(place[:, :, 0])


def make_observations(scenario: Scenario, truth: pd.DataFrame | None = None) -> pd.DataFrame:
    """The observations table of the scenario with exact states (no noise), one row per object a car gets at a time."""
    return make_tables(scenario, truth)["observations"]


def make_tables(scenario: Scenario, truth: pd.DataFrame | None = None) -> dict:
    """The tables of a synthetic run with exact states: truth, observations, and the messages as the exporter writes them.

    cpm_tx     one row per CPM sent (t_tx, tx_id, station_id, reference_time_ms, n_objects)
    cpm_rx     one row per copy received (t_rx, rx_id, station_id, reference_time_ms, tx_id, t_tx, delay)
    cpm_drop   one row per copy lost (t_drop, rx_id, station_id, reference_time_ms, tx_id, t_tx); a lost copy is counted at its arrival time
    """
    truth = make_truth(scenario) if truth is None else truth
    names = [car.name for car in scenario.cars]
    classes = {car.name: car.cls for car in scenario.cars}
    times, place, present = _grid(truth, names)
    n_t, n = len(times), len(names)
    rng = np.random.default_rng(scenario.seed + 2)
    dist = np.linalg.norm(place[:, :, None, :] - place[:, None, :, :], axis=-1)        # steps x looker x object
    not_self = ~np.eye(n, dtype=bool)[None]
    sees = present[:, :, None] & present[:, None, :] & (dist <= scenario.sensor_range) & not_self

    # the on-board sensor: one row per step, looker and object seen
    k, looker, obj = np.nonzero(sees)
    local = pd.DataFrame({
        "t_sim": times[k], "rx_station": np.array(names, dtype=object)[looker], "src": "radar", "tx_station": None,
        "obj_id": ["%s/%s" % (names[a], names[b]) for a, b in zip(looker, obj)],
        "class": [classes[names[b]] for b in obj], "x": place[k, obj, 0], "y": place[k, obj, 1],
        "sender_conf": np.nan, "t_meas": times[k], "t_rx": times[k], "pdr_window": np.nan,
        "gt_match_id": np.array(names, dtype=object)[obj], "sensor": "radar", "tx_station_id": np.nan,
        "reference_time_ms": np.nan, "meas_src": "radar"})

    # the messages: a sender sends what it sees, every cpm_period, to the cars in radio range
    period_steps = int(round(scenario.cpm_period / scenario.step))
    sending = (np.arange(n_t) % period_steps == 0)[:, None] & present & np.array([car.sends for car in scenario.cars])[None]
    reaches = present[:, :, None] & present[:, None, :] & (dist <= scenario.cpm_range) & not_self        # steps x sender x receiver
    message = sending[:, :, None] & reaches
    lost = rng.uniform(size=message.shape) < scenario.loss
    low, high = scenario.delay_bounds
    delay = rng.uniform(low, high, size=message.shape) if high > low else np.full(message.shape, float(low))
    # the share of the messages of a sender that a receiver got, over its last 10 messages
    pdr = np.full(message.shape, np.nan)
    for s in range(n):
        for r in range(n):
            sent = np.flatnonzero(message[:, s, r])
            if len(sent):
                got = (~lost[sent, s, r]).astype(float)
                window = np.array([got[max(0, i - 9):i + 1].mean() for i in range(len(got))])
                pdr[sent, s, r] = window
    arrival = times[:, None, None] + delay                                                  # steps x sender x receiver
    delivered = message & ~lost & (arrival <= times[-1] + 1e-9)                              # a copy that arrives after the end is never seen
    last_step = np.where(present.any(axis=0), np.array([times[np.flatnonzero(present[:, c])].max() if present[:, c].any() else -1.0
                                                        for c in range(n)]), -1.0)
    delivered &= arrival <= last_step[None, None, :] + 1e-9                                  # nor a copy that arrives after the receiver left
    k, s, r, o = np.nonzero(delivered[:, :, :, None] & sees[:, :, None, :])
    v2x = pd.DataFrame({
        "t_sim": times[k] + delay[k, s, r], "rx_station": np.array(names, dtype=object)[r], "src": "v2x",
        "tx_station": np.array(names, dtype=object)[s], "obj_id": ["%s/%s" % (names[a], names[b]) for a, b in zip(s, o)],
        "class": [classes[names[b]] for b in o], "x": place[k, o, 0], "y": place[k, o, 1],
        "sender_conf": 1.0, "t_meas": times[k], "t_rx": times[k] + delay[k, s, r], "pdr_window": pdr[k, s, r],
        "gt_match_id": np.array(names, dtype=object)[o], "sensor": None, "tx_station_id": s.astype(float),
        "reference_time_ms": np.round(times[k] * 1000.0), "meas_src": "radar"})

    k, s = np.nonzero(sending & reaches.any(axis=2))                                        # a CPM is sent when somebody can hear it
    n_seen = sees.sum(axis=2)
    cpm_tx = pd.DataFrame({"t_tx": times[k], "tx_id": np.array(names, dtype=object)[s], "station_id": s, "reference_time_ms": np.round(times[k] * 1000.0),
                           "n_objects": n_seen[k, s]})
    k, s, r = np.nonzero(delivered)
    cpm_rx = pd.DataFrame({"t_rx": times[k] + delay[k, s, r], "rx_id": np.array(names, dtype=object)[r], "station_id": s,
                           "reference_time_ms": np.round(times[k] * 1000.0), "tx_id": np.array(names, dtype=object)[s], "t_tx": times[k],
                           "delay": delay[k, s, r]})
    k, s, r = np.nonzero(message & lost)
    cpm_drop = pd.DataFrame({"t_drop": times[k] + delay[k, s, r], "rx_id": np.array(names, dtype=object)[r], "station_id": s,
                             "reference_time_ms": np.round(times[k] * 1000.0), "tx_id": np.array(names, dtype=object)[s], "t_tx": times[k]})

    obs = pd.concat([local, v2x], ignore_index=True)
    obs["run_id"] = scenario.name
    obs["seed"] = np.int32(scenario.seed)
    obs["vx"] = obs["vy"] = np.nan
    state = truth.set_index(["obj_id", "t"])[["vx", "vy"]]
    at = pd.MultiIndex.from_arrays([obs["gt_match_id"].astype("string"), np.round(obs["t_meas"].to_numpy(dtype=float), 6)])
    obs[["vx", "vy"]] = state.reindex(at).to_numpy(dtype=float)
    for c in ("P11", "P12", "P22", "Pv11", "Pv22", "exist_score"):
        obs[c] = np.nan
    obs["age"] = obs["t_rx"] - obs["t_meas"]
    obs["cbr"] = float(scenario.cbr)
    obs["dcc_state"] = dcc_state(obs["cbr"].to_numpy(float))
    obs["weather"] = None
    obs["gt_dist"] = 0.0
    obs["t_pos"] = obs["t_meas"]
    obs = obs.sort_values(["t_sim", "rx_station", "src"], kind="stable").reset_index(drop=True)
    obs = obs[[c for c, _, _ in OBS_COLUMNS]]
    for c, typ, _ in OBS_COLUMNS:
        if typ == "string":
            obs[c] = obs[c].astype("string")
        elif typ == "int64":
            obs[c] = obs[c].astype("Int64")
        elif typ == "int32":
            obs[c] = obs[c].astype("int32")
        else:
            obs[c] = obs[c].astype("float64")
    return {"truth": truth, "observations": obs, "cpm_tx": cpm_tx, "cpm_rx": cpm_rx, "cpm_drop": cpm_drop}


def summary(scenario: Scenario, obs: pd.DataFrame, truth: pd.DataFrame) -> dict:
    """The content of export_summary.json for a synthetic run."""
    v2x = obs[obs["src"] == "v2x"]
    return {"run_id": scenario.name, "synthetic": True, "seed": scenario.seed, "cars": len(scenario.cars),
            "duration_s": scenario.duration, "step_s": scenario.step, "sensor_range_m": scenario.sensor_range,
            "cpm_period_s": scenario.cpm_period, "cpm_range_m": scenario.cpm_range, "delay_s": list(scenario.delay_bounds),
            "loss": scenario.loss, "variables": {"T": "%gs" % scenario.cpm_period, "p": "%g" % scenario.loss},
            "rows": {"truth": int(len(truth)), "observations": int(len(obs)), "observations_v2x": int(len(v2x)),
                     "observations_local": int(len(obs) - len(v2x))},
            "age_v2x_s": {"median": round(float(v2x["age"].median()), 4) if len(v2x) else None,
                          "max": round(float(v2x["age"].max()), 4) if len(v2x) else None},
            "result": "PASS"}


def make_run(scenario: Scenario, noise: NoiseModel | None | bool = True) -> Run:
    """The synthetic run, ready for replay, with the noise applied the way ``load_run`` does it."""
    truth = make_truth(scenario)
    obs = make_observations(scenario, truth)
    model = scenario.noise if noise is True else (noise or None)
    if model is not None and len(obs):
        obs = apply_noise(obs, truth, model)
    return Run(obs=obs, truth=truth, noise=model, info=summary(scenario, obs, truth))


def write_run(scenario: Scenario, folder: str | os.PathLike) -> dict:
    """Writes observations.parquet (exact states), truth.parquet, the message tables and export_summary.json, as ``aoc-export`` does."""
    folder = os.fspath(folder)
    os.makedirs(folder, exist_ok=True)
    tables = make_tables(scenario)
    truth, obs = tables["truth"], tables["observations"]
    write_parquet(truth, os.path.join(folder, "truth.parquet"))
    write_parquet(obs, os.path.join(folder, "observations.parquet"), {c: d for c, _, d in OBS_COLUMNS})
    for name in ("cpm_tx", "cpm_rx", "cpm_drop"):
        write_parquet(tables[name], os.path.join(folder, name + ".parquet"))
    info = summary(scenario, obs, truth)
    info["rows"].update(cpm_tx=int(len(tables["cpm_tx"])), cpm_rx=int(len(tables["cpm_rx"])), cpm_drop=int(len(tables["cpm_drop"])))
    with open(os.path.join(folder, "export_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    return info


# ----------------------------------------------------------------------------
# Named scenarios
# ----------------------------------------------------------------------------
def crossroads(**changes) -> Scenario:
    """Six cars on two roads that cross: the default scenario. Every car sees two or three others."""
    cars = (Car("e1", x=-120.0, y=0.0, vx=12.0), Car("e2", x=-170.0, y=3.5, vx=12.0), Car("w1", x=120.0, y=-3.5, vx=-12.0),
            Car("s1", x=0.0, y=-120.0, vy=10.0), Car("s2", x=3.5, y=-160.0, vy=10.0), Car("n1", x=-3.5, y=120.0, vy=-10.0))
    return Scenario(cars, **(dict(duration=20.0, name="synthetic-crossroads") | changes))


def convoy(**changes) -> Scenario:
    """Five cars in a line, 25 m apart, at the same speed: every car sees its neighbours for the whole run."""
    cars = tuple(Car("c%d" % k, x=25.0 * k, y=0.0, vx=15.0) for k in range(5))
    return Scenario(cars, **(dict(duration=20.0, name="synthetic-convoy") | changes))


def leaver(**changes) -> Scenario:
    """A convoy where one car leaves the simulation at 8 s: its reports go stale."""
    cars = tuple(Car("c%d" % k, x=25.0 * k, y=0.0, vx=15.0, t_out=8.0 if k == 2 else None) for k in range(5))
    return Scenario(cars, **(dict(duration=20.0, name="synthetic-leaver") | changes))


def lossy(**changes) -> Scenario:
    """The convoy with slow messages (2 Hz), long delays (0.1 to 1.5 s) and a loss of 30%."""
    changes = dict(cpm_period=0.5, delay=(0.1, 1.5), loss=0.3, name="synthetic-lossy") | changes
    return convoy(**changes)


SCENARIOS = {"crossroads": crossroads, "convoy": convoy, "leaver": leaver, "lossy": lossy}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write a synthetic run (observations.parquet, truth.parquet) whose right answer is known.")
    ap.add_argument("out_dir", help="folder to write")
    ap.add_argument("--scenario", default="crossroads", choices=sorted(SCENARIOS), help="named scenario (default crossroads)")
    ap.add_argument("--duration", type=float, default=None, help="end of the run in s")
    ap.add_argument("--delay", type=float, nargs="+", default=None, metavar="S", help="age at reception: one value, or low high")
    ap.add_argument("--loss", type=float, default=None, help="probability that one copy of a CPM is lost")
    ap.add_argument("--cpm-period", type=float, default=None, help="time between two CPMs of one car in s")
    ap.add_argument("--seed", type=int, default=None, help="seed of the delays, the losses and the wobble")
    args = ap.parse_args(argv)
    changes = {}
    if args.duration is not None:
        changes["duration"] = args.duration
    if args.delay is not None:
        if len(args.delay) > 2:
            print("ERROR: --delay takes one or two values", file=sys.stderr)
            return 2
        changes["delay"] = args.delay[0] if len(args.delay) == 1 else (args.delay[0], args.delay[1])
    if args.loss is not None:
        changes["loss"] = args.loss
    if args.cpm_period is not None:
        changes["cpm_period"] = args.cpm_period
    if args.seed is not None:
        changes["seed"] = args.seed
    try:
        scenario = SCENARIOS[args.scenario](**changes)
        info = write_run(scenario, args.out_dir)
    except (ValueError, OSError) as err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 2
    print("B9 SYNTHETIC RUN")
    print("  run:                 %s, written to %s" % (info["run_id"], os.path.abspath(args.out_dir)))
    print("  cars:                %d, %g s, step %g s" % (info["cars"], info["duration_s"], info["step_s"]))
    print("  sensor range %g m, CPM every %g s to %g m, delay %s s, loss %g"
          % (info["sensor_range_m"], info["cpm_period_s"], info["cpm_range_m"], "%g to %g" % tuple(info["delay_s"]), info["loss"]))
    print("  rows:                %d truth, %d observations (%d sensor, %d CPM)"
          % (info["rows"]["truth"], info["rows"]["observations"], info["rows"]["observations_local"], info["rows"]["observations_v2x"]))
    print("  age at reception:    median %s s, max %s s" % (info["age_v2x_s"]["median"], info["age_v2x_s"]["max"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
