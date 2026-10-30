"""The sensor models and the weather tables (task C6 of the plan).

What a sensor is here
  Artery sees an object or not: a cone with a range and an opening angle, a line-of-sight check, no miss,
  no false alarm, no weather. The plan wants more: a detection probability per weather, and a reliability of
  each on-board sensor from a (weather, range bin) table, used by the contextual discounting (B5). Both are
  done here, in Python, on the logs (the A8 decision for the noise: changeable without a new simulation,
  repeatable with a seed).

Two tables per sensor and per weather, by range bin
  detection    the probability that the sensor sees an object at that distance. At the replay, each sensor
               row of the logs is kept with this probability and dropped otherwise (a missed detection).
               A CPM object is kept when at least one sensor of its sender kept the object at the measurement
               time (the sender cannot send what it did not see).
  reliability  the reliability alpha_i of a report of the sensor at that distance, between 0 and 1, which
               the discounting multiplies into the reliability of the object (``TrustModel``). It stands for
               what Artery does not simulate: wrong sizes, wrong classes, ghosts near the edge of the range.

The values below are assumed. They are not fitted to data. The plan fits them on hold-out runs later (E3);
``SensorTables.to_json`` and ``from_json`` carry a fitted table. A sensor that is not in the table (the four
radars of the Artery example, the "radar" of the synthetic streams) is kept at every distance and has the
reliability ``sensor_default`` of the trust model (1 today).

The three sensors of the plan (artery-ext/config/sensors-vru.xml) and their assumed values:

    sensor   range  bins (m)        detection clear   detection rain    reliability (both)
    Camera    80 m  0-30-60-80      0.98 0.95 0.85    0.85 0.70 0.45    1 1 1
    Radar    150 m  0-50-100-150    0.97 0.95 0.90    0.95 0.92 0.85    1 1 1
    Lidar    100 m  0-40-70-100     0.99 0.97 0.90    0.90 0.75 0.55    1 1 1

The reasons for the detection values, in one line each: a camera falls first in rain and at long range; a radar
hardly minds the rain and keeps its range; a lidar is the best in the clear and loses the most in rain (the beam
scatters on the drops). An object beyond the last bin takes the values of the last bin.

The reliability is 1 in every bin (decision of task C7, Oct 30, 2026). Artery makes no ghost and no wrong
class: every report is true, so any reliability below 1 is pessimistic on its logs. The first guesses
(``GUESSED_RELIABILITY``: 0.97 to 0.90 in the clear, 0.90 to 0.70 in rain for the camera, and so on) made the
proposed law under-confident on the pilot run of C5 (calibration error 0.040 against 0.010 for the Bayesian
way, 0.094 in rain). The plan calibrates this table on hold-out runs; on Artery logs that calibration gives 1.
The mechanism stays, for a sensor model with false alarms or for real data.

The drops are tied to the measurement, as the noise is: the same seed gives the same drops, whatever the
order of the rows, and a subset of a run gives the same drops as the full run.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

import numpy as np
import pandas as pd

from .noise import _GOLDEN, DETECTION_STREAM, _mix, measurement_keys, true_state_at

WEATHERS = ("clear", "rain")
DEFAULT_WEATHER = "clear"


@dataclass(frozen=True)
class SensorModel:
    """One on-board sensor: its cone, and its two tables by weather and range bin.

    name         the name of the sensor in the logs (column ``sensor``)
    range_m      range of the cone in m
    fov_deg      opening angle of the cone in degrees
    bins         upper edges of the range bins in m, the last one being the range
    detection    weather -> probability of detection in each bin
    reliability  weather -> reliability alpha_i in each bin
    """

    name: str
    range_m: float
    fov_deg: float
    bins: tuple
    detection: Mapping[str, tuple]
    reliability: Mapping[str, tuple]

    def __post_init__(self):
        bins = tuple(float(b) for b in self.bins)
        if not bins or any(b <= 0 for b in bins) or any(b2 <= b1 for b1, b2 in zip(bins, bins[1:])):
            raise ValueError("the bins of %s must be increasing positive distances" % self.name)
        if abs(bins[-1] - float(self.range_m)) > 1e-9:
            raise ValueError("the last bin of %s must be its range" % self.name)
        for table_name, table in (("detection", self.detection), ("reliability", self.reliability)):
            for weather, values in table.items():
                values = [float(v) for v in values]
                if len(values) != len(bins):
                    raise ValueError("%s of %s in %s: %d values for %d bins" % (table_name, self.name, weather, len(values), len(bins)))
                if not all(0.0 <= v <= 1.0 for v in values):
                    raise ValueError("%s of %s in %s: a value is not between 0 and 1" % (table_name, self.name, weather))
        if set(self.detection) != set(self.reliability):
            raise ValueError("%s: the two tables must name the same weathers" % self.name)
        object.__setattr__(self, "bins", bins)
        object.__setattr__(self, "detection", {w: tuple(float(v) for v in vs) for w, vs in self.detection.items()})
        object.__setattr__(self, "reliability", {w: tuple(float(v) for v in vs) for w, vs in self.reliability.items()})

    @property
    def weathers(self) -> tuple:
        return tuple(self.detection)

    def bin_of(self, distance) -> np.ndarray:
        """The index of the bin of each distance (the last bin beyond the range)."""
        d = np.asarray(distance, dtype=float)
        return np.minimum(np.searchsorted(np.asarray(self.bins), d, side="left"), len(self.bins) - 1)

    def _lookup(self, table: Mapping[str, tuple], weather: str, distance) -> np.ndarray:
        if weather not in table:
            raise ValueError("the sensor %s has no table for the weather %r (it has: %s)" % (self.name, weather, ", ".join(table)))
        values = np.asarray(table[weather], dtype=float)
        out = values[self.bin_of(distance)]
        return np.where(np.isfinite(np.asarray(distance, dtype=float)), out, values[-1])

    def detection_probability(self, weather: str, distance) -> np.ndarray:
        """The probability of detection at each distance (an unknown distance takes the last bin)."""
        return self._lookup(self.detection, weather, distance)

    def reliability_at(self, weather: str, distance) -> np.ndarray:
        """The reliability alpha_i at each distance (an unknown distance takes the last bin)."""
        return self._lookup(self.reliability, weather, distance)

    def as_dict(self) -> dict:
        return {"name": self.name, "range_m": self.range_m, "fov_deg": self.fov_deg, "bins": list(self.bins),
                "detection": {w: list(v) for w, v in self.detection.items()},
                "reliability": {w: list(v) for w, v in self.reliability.items()}}


ONE = {"clear": (1.0, 1.0, 1.0), "rain": (1.0, 1.0, 1.0)}
DEFAULT_SENSORS = (
    SensorModel("Camera", 80.0, 120.0, (30.0, 60.0, 80.0),
                detection={"clear": (0.98, 0.95, 0.85), "rain": (0.85, 0.70, 0.45)}, reliability=ONE),
    SensorModel("Radar", 150.0, 120.0, (50.0, 100.0, 150.0),
                detection={"clear": (0.97, 0.95, 0.90), "rain": (0.95, 0.92, 0.85)}, reliability=ONE),
    SensorModel("Lidar", 100.0, 360.0, (40.0, 70.0, 100.0),
                detection={"clear": (0.99, 0.97, 0.90), "rain": (0.90, 0.75, 0.55)}, reliability=ONE),
)
# the first guesses of the reliability (task C6), kept for the tests of the mechanism and as an example of a table
GUESSED_RELIABILITY = {
    "Camera": {"clear": (0.97, 0.95, 0.90), "rain": (0.90, 0.80, 0.70)},
    "Radar": {"clear": (0.95, 0.93, 0.90), "rain": (0.93, 0.90, 0.85)},
    "Lidar": {"clear": (0.98, 0.96, 0.92), "rain": (0.90, 0.82, 0.72)},
}


def with_reliability(tables: "SensorTables", values: Mapping[str, Mapping[str, tuple]]) -> "SensorTables":
    """A copy of the tables with other reliability values, by sensor name (the sensors not named keep theirs)."""
    sensors = {}
    for name, model in tables.sensors.items():
        if name in values:
            model = SensorModel(model.name, model.range_m, model.fov_deg, model.bins, model.detection, dict(values[name]))
        sensors[name] = model
    return SensorTables(sensors, seed=tables.seed)


@dataclass(frozen=True)
class SensorTables:
    """The sensor models by name, and what to do with a sensor that is not in the table.

    sensors   the models, by name of the sensor
    seed      changes every drop of the detection model; the same seed gives the same drops
    """

    sensors: Mapping[str, SensorModel] = field(default_factory=lambda: {s.name: s for s in DEFAULT_SENSORS})
    seed: int = 0

    def __post_init__(self):
        weathers = None
        for name, model in self.sensors.items():
            if not isinstance(model, SensorModel) or model.name != name:
                raise ValueError("the sensors must be SensorModel objects stored under their name")
            if weathers is not None and set(model.weathers) != weathers:
                raise ValueError("every sensor must have the same weathers (%s has %s)" % (name, ", ".join(model.weathers)))
            weathers = set(model.weathers)
        object.__setattr__(self, "sensors", MappingProxyType(dict(self.sensors)))

    @property
    def weathers(self) -> tuple:
        """The weathers of the tables (the same for every sensor)."""
        for model in self.sensors.values():
            return tuple(sorted(model.weathers))
        return ()

    def __eq__(self, other):
        return isinstance(other, SensorTables) and dict(self.sensors) == dict(other.sensors) and self.seed == other.seed

    def known(self, names: pd.Series) -> np.ndarray:
        return names.isin(list(self.sensors)).to_numpy(dtype=bool)

    def detection_probability(self, names: pd.Series, weather: str, distance) -> np.ndarray:
        """The probability of detection of each row, 1 for a sensor that is not in the table."""
        out = np.ones(len(names))
        d = np.asarray(distance, dtype=float)
        for name, model in self.sensors.items():
            rows = (names == name).to_numpy(dtype=bool, na_value=False)
            if rows.any():
                out[rows] = model.detection_probability(weather, d[rows])
        return out

    def reliability(self, names: pd.Series, weather: str, distance, default: float = 1.0) -> np.ndarray:
        """The reliability alpha_i of each row, ``default`` for a sensor that is not in the table."""
        out = np.full(len(names), float(default))
        d = np.asarray(distance, dtype=float)
        for name, model in self.sensors.items():
            rows = (names == name).to_numpy(dtype=bool, na_value=False)
            if rows.any():
                out[rows] = model.reliability_at(weather, d[rows])
        return out

    def as_dict(self) -> dict:
        return {"sensors": [m.as_dict() for m in self.sensors.values()], "seed": self.seed}

    def to_json(self, path) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.as_dict(), fh, indent=1)
            fh.write("\n")

    @classmethod
    def from_dict(cls, data: dict) -> "SensorTables":
        models = [SensorModel(m["name"], float(m["range_m"]), float(m["fov_deg"]), tuple(m["bins"]),
                              {w: tuple(v) for w, v in m["detection"].items()}, {w: tuple(v) for w, v in m["reliability"].items()})
                  for m in data["sensors"]]
        return cls({m.name: m for m in models}, seed=int(data.get("seed", 0)))

    @classmethod
    def from_json(cls, path) -> "SensorTables":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


DEFAULT_TABLES = SensorTables()


def uniforms(keys: np.ndarray, stream: int = DETECTION_STREAM) -> np.ndarray:
    """One number strictly between 0 and 1 per key, the same on every machine, independent of the noise values
    of the same key (the noise uses the streams 1 to 6 of the generator, this one the stream 7)."""
    with np.errstate(over="ignore"):
        bits = _mix(keys + np.uint64(stream) * _GOLDEN)
    return ((bits >> np.uint64(12)).astype(np.float64) + 0.5) / 2.0**52


def station_distance(obs: pd.DataFrame, truth: pd.DataFrame) -> np.ndarray:
    """The true distance in m between the station that made each measurement and the object, at the measurement
    time: the receiver for a sensor row, the sender for a CPM row. NaN when either is not in the truth."""
    is_v2x = (obs["src"] == "v2x").to_numpy(dtype=bool, na_value=False)
    who = obs["rx_station"].where(~is_v2x, obs["tx_station"])
    me = true_state_at(truth, who, obs["t_meas"])
    it = true_state_at(truth, obs["gt_match_id"], obs["t_meas"])
    return np.hypot(me["gt_x"].to_numpy() - it["gt_x"].to_numpy(), me["gt_y"].to_numpy() - it["gt_y"].to_numpy())


def _has_row_within(rows: pd.DataFrame, sensor_rows: pd.DataFrame, tolerance: float) -> np.ndarray:
    """For each CPM row, whether ``sensor_rows`` holds a row of its sender about the same object with a measurement
    time within ``tolerance`` seconds of the CPM's measurement time."""
    if not len(rows) or not len(sensor_rows):
        return np.zeros(len(rows), dtype=bool)
    station = pd.Series(sensor_rows["rx_station"].astype(object).to_numpy(), index=sensor_rows.index)
    objects = pd.Series(sensor_rows["gt_match_id"].astype(object).to_numpy(), index=sensor_rows.index)
    group = pd.factorize(pd.MultiIndex.from_arrays([station, objects]))[0]
    uniques = pd.MultiIndex.from_arrays([station, objects]).unique()
    code_of = {key: k for k, key in enumerate(uniques)}
    span = 10**13
    have = np.sort(group.astype(np.int64) * span + np.round(sensor_rows["t_meas"].to_numpy(dtype=float) * 1e6).astype(np.int64))
    asked_keys = list(zip(rows["tx_station"].astype(object).to_numpy(), rows["gt_match_id"].astype(object).to_numpy()))
    asked_group = np.array([code_of.get(key, -1) for key in asked_keys], dtype=np.int64)
    asked_t = np.round(rows["t_meas"].to_numpy(dtype=float) * 1e6).astype(np.int64)
    asked = asked_group * span + asked_t
    tol = int(round(tolerance * 1e6))
    lo = np.searchsorted(have, asked - tol, side="left")
    hi = np.searchsorted(have, asked + tol, side="right")
    return (asked_group >= 0) & (hi > lo)


def apply_detection(obs: pd.DataFrame, truth: pd.DataFrame, tables: SensorTables | None = None,
                    weather: str = DEFAULT_WEATHER) -> tuple[pd.DataFrame, dict]:
    """Returns the observations without the rows the sensors missed in this weather, and the counts.

    New column ``range_m``: the true distance between the station that made the measurement and the object.
    A sensor row is kept with the probability of detection of its sensor at that distance (one draw per
    measurement: the same for every copy of it). A CPM row is kept when its sender kept at least one sensor row
    about the object at the measurement time (within 1 ms: a CPM declares that time in whole milliseconds). A CPM
    row whose sender has no sensor row about the object at that time is kept with the probability that one of the
    sender's own sensors of the table in range sees it (a sender that has sensor rows but none of a sensor of the
    table is never missed; a sender without any sensor row is taken to carry every sensor of the table).
    """
    tables = tables or DEFAULT_TABLES
    out = obs.copy()
    out["range_m"] = station_distance(out, truth)
    is_v2x = (out["src"] == "v2x").to_numpy(dtype=bool, na_value=False)
    local = ~is_v2x
    keep = np.ones(len(out), dtype=bool)
    counts = {"weather": weather, "sensor_rows": int(local.sum()), "sensor_rows_dropped": 0, "cpm_rows": int(is_v2x.sum()),
              "cpm_rows_dropped": 0, "cpm_rows_without_sensor_row": 0}
    if not tables.sensors or not len(out):
        return out, counts

    # the sensor rows: one draw per measurement
    if local.any():
        rows = out[local]
        p = tables.detection_probability(rows["sensor"], weather, rows["range_m"].to_numpy())
        keys = measurement_keys(tables.seed, rows["rx_station"], rows["sensor"], rows["obj_id"], rows["t_meas"])
        kept_local = uniforms(keys) < p
        keep[local] = kept_local
        counts["sensor_rows_dropped"] = int((~kept_local).sum())
    # the CPM rows: the sender must have kept a sensor row about the object at the measurement time (within 1 ms:
    # a CPM declares the measurement time in whole milliseconds)
    if is_v2x.any():
        rows = out[is_v2x]
        sender_rows = out[local]
        kept_rows = sender_rows[keep[local]]
        has_row = _has_row_within(rows, sender_rows, 1e-3)
        seen = _has_row_within(rows, kept_rows, 1e-3)
        # no sensor row of the sender about the object at that time: a draw with the sender's own sensors of the
        # table in range (a sender with no sensor of the table is never missed)
        own = sender_rows.groupby("rx_station")["sensor"].agg(lambda names: frozenset(n for n in names if n in tables.sensors))
        d = rows["range_m"].to_numpy(dtype=float)
        miss_all = np.ones(len(rows))
        modelled = np.zeros(len(rows), dtype=bool)               # the sender has a sensor of the table (or is unknown)
        for station, sensors in own.items():
            at = (rows["tx_station"] == station).to_numpy(dtype=bool, na_value=False)
            if not at.any() or not sensors:
                continue
            modelled |= at
            for name in sensors:
                model = tables.sensors[name]
                in_range = at & ~(d > model.range_m)
                miss_all[in_range] = miss_all[in_range] * (1.0 - model.detection_probability(weather, d[in_range]))
        unknown_sender = ~rows["tx_station"].isin(own.index).to_numpy(dtype=bool, na_value=False)
        if unknown_sender.any():                                 # no sensor row of this sender at all: every sensor of the table
            modelled |= unknown_sender
            for model in tables.sensors.values():
                in_range = unknown_sender & ~(d > model.range_m)
                miss_all[in_range] = miss_all[in_range] * (1.0 - model.detection_probability(weather, d[in_range]))
        keys = measurement_keys(tables.seed, rows["tx_station"], pd.Series(["cpm"] * len(rows), index=rows.index), rows["obj_id"], rows["t_meas"])
        drawn = np.where(modelled, uniforms(keys) < 1.0 - miss_all, True)
        kept_v2x = np.where(has_row, seen, drawn)
        keep[is_v2x] = kept_v2x
        counts["cpm_rows_dropped"] = int((~kept_v2x).sum())
        counts["cpm_rows_without_sensor_row"] = int((~has_row).sum())
    return out[keep].copy(), counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Print the sensor models and the weather tables of the plan, or write them to a JSON file.")
    ap.add_argument("--write", default=None, metavar="FILE.json", help="write the default tables to this file")
    ap.add_argument("--read", default=None, metavar="FILE.json", help="print the tables of this file instead of the defaults")
    args = ap.parse_args(argv)
    tables = SensorTables.from_json(args.read) if args.read else DEFAULT_TABLES
    print("sensor   range   fov   bins (m)          weather   detection            reliability")
    for model in tables.sensors.values():
        for k, weather in enumerate(model.weathers):
            head = "%-8s %5.0f m %4.0f  %-16s" % (model.name, model.range_m, model.fov_deg, "-".join("%g" % b for b in (0.0,) + model.bins)) if k == 0 else " " * 38
            print("%s  %-8s  %-20s %s" % (head, weather, " ".join("%.2f" % v for v in model.detection[weather]),
                                         " ".join("%.2f" % v for v in model.reliability[weather])))
    print("detection values assumed, not fitted; reliability 1 by decision (task C7); seed %d" % tables.seed)
    if args.write:
        tables.to_json(args.write)
        print("written: %s" % args.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
