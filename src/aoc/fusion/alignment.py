"""Time alignment (first half of task B3).

At a fusion tick, the objects that a vehicle knows were measured at different times: a radar row
is fresh, a CPM object can be one second old. Before two objects can be compared, both must be
brought to the same time. This module moves every object to the time of the tick.

The motion model is the one of the theory note: constant velocity, plus an unknown acceleration
of intensity q. For an object of age ``a`` (time of the tick minus its measurement time):

    position at the tick   = position + a * speed
    position variance      = position variance + a^2 * speed variance + q * a^3 / 3
    speed variance         = speed variance + q * a
    position-speed link    = a * speed variance + q * a^2 / 2

The first line of the variance is Equation (1) of the note. So an old object is not only moved:
its uncertainty grows, and the association and the fusion see it.

    from aoc.fusion.alignment import align
    aligned = align(tick)          # tick comes from Run.replay
    aligned.objects                # one row per object: sensor rows first, then the known CPM objects
    aligned.state                  # x, y, vx, vy at the time of the tick, one line per row
    aligned.cov                    # the 4 x 4 covariance of each line, same order
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from ..theory import reliability

_Q = {name: values[3] for name, values in reliability.CLASSES.items()}

#: Intensity q of the unknown acceleration, in m^2/s^3, by class of the observations table.
#: The values are the assumed values of the theory note (``aoc.theory.reliability.CLASSES``).
Q_BY_CLASS: dict[str, float] = {
    "pedestrian": _Q["pedestrian"],
    "cyclist": _Q["bicycle"],
    "passenger_car": _Q["car (moving)"],
    "moped": _Q["car (moving)"],
    "motorcycle": _Q["car (moving)"],
    "light_truck": _Q["truck (moving)"],
    "heavy_truck": _Q["truck (moving)"],
    "bus": _Q["truck (moving)"],
    "trailer": _Q["truck (moving)"],
    "tram": _Q["truck (moving)"],
    "special_vehicle": _Q["truck (moving)"],
}
#: Value for a class that is not in the table: the largest one, to stay on the careful side.
Q_DEFAULT: float = max(_Q.values())


@dataclass
class Aligned:
    """The objects known by one vehicle at one tick, all moved to the time of the tick."""

    t: float                  # time of the tick in s
    ego: str                  # the vehicle
    objects: pd.DataFrame     # one row per object, index 0..n-1, with the columns of the tick tables plus the ones below
    state: np.ndarray         # shape (n, 4): x, y, vx, vy at the time of the tick
    cov: np.ndarray           # shape (n, 4, 4): covariance of the state, order x, y, vx, vy

    def __len__(self) -> int:
        return len(self.objects)


def process_noise(classes: pd.Series, q_by_class: Mapping[str, float] | None = None, q_default: float | None = None) -> np.ndarray:
    """The intensity q of each row, from its class."""
    table = Q_BY_CLASS if q_by_class is None else q_by_class
    default = Q_DEFAULT if q_default is None else float(q_default)
    return np.array([float(table.get(c, default)) if isinstance(c, str) else default for c in classes.to_numpy(dtype=object)],
                    dtype=float)


def predict(frame: pd.DataFrame, age: np.ndarray, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Moves each row of a table of observations forward by its age. Returns (state, covariance).

    The table must hold x, y, vx, vy and the covariance columns P11, P12, P22, Pv11, Pv22.
    """
    n = len(frame)
    age = np.asarray(age, dtype=float)
    q = np.asarray(q, dtype=float)
    need = ["x", "y", "vx", "vy", "P11", "P12", "P22", "Pv11", "Pv22"]
    values = np.column_stack([frame[name].to_numpy(dtype=float, na_value=np.nan) for name in need]) if n else np.zeros((0, len(need)))
    if age.shape != (n,) or q.shape != (n,):
        raise ValueError("one age and one intensity q are needed per row")
    if not np.isfinite(values).all():
        raise ValueError("a state or a covariance is missing. Load the run with a noise model: it fills the covariance columns.")
    if n and (not np.isfinite(age).all() or (age < -1e-6).any()):
        raise ValueError("the ages must be numbers that are not negative")
    if n and ((values[:, [4, 6, 7, 8]] < 0.0).any() or (values[:, 5] ** 2 > values[:, 4] * values[:, 6] + 1e-12).any()
              or not np.isfinite(q).all() or (q < 0.0).any()):
        raise ValueError("a variance or an intensity q is negative, or P12 is too large for P11 and P22")
    age = np.clip(age, 0.0, None)
    x, y, vx, vy, p11, p12, p22, pv11, pv22 = values.T

    state = np.column_stack([x + age * vx, y + age * vy, vx, vy])
    cov = np.zeros((n, 4, 4))
    grow = q * age**3 / 3.0
    cov[:, 0, 0] = p11 + age**2 * pv11 + grow
    cov[:, 1, 1] = p22 + age**2 * pv22 + grow
    cov[:, 0, 1] = cov[:, 1, 0] = p12
    link = q * age**2 / 2.0
    cov[:, 0, 2] = cov[:, 2, 0] = age * pv11 + link
    cov[:, 1, 3] = cov[:, 3, 1] = age * pv22 + link
    cov[:, 2, 2] = pv11 + q * age
    cov[:, 3, 3] = pv22 + q * age
    return state, cov


def _newest_per_track(local: pd.DataFrame) -> pd.DataFrame:
    """One row per sensor and track: the newest one.

    With a tick period longer than the step of the sensors, a tick holds several rows of one sensor
    for one object. A sensor reports a real object once, so only its newest row is kept.
    """
    if len(local) < 2:
        return local
    sensor = local["sensor"].to_numpy(dtype=object)
    track = local["obj_id"].to_numpy(dtype=object)
    t_meas = local["t_meas"].to_numpy(dtype=float)
    newest: dict[tuple, int] = {}
    for row in range(len(local)):
        key = (sensor[row], track[row])
        if key not in newest or t_meas[row] >= t_meas[newest[key]]:
            newest[key] = row
    if len(newest) == len(local):
        return local
    return local.iloc[sorted(newest.values())]


def align(tick, q_by_class: Mapping[str, float] | None = None, q_default: float | None = None) -> Aligned:
    """Moves every object of a tick to the time of the tick.

    The objects are the sensor rows of the tick (``tick.local``) and the known CPM objects (``tick.v2x``).
    If a sensor gives several rows for one of its tracks in the tick (tick period longer than the sensor step),
    only the newest row is kept.
    New columns of ``objects``:
      source      who gave the object: the name of the sensor, or "v2x:" plus the sender
      x_t, y_t, vx_t, vy_t   state at the time of the tick
      sigma_t     position uncertainty at the time of the tick in m (one number: the root of the mean of the two variances)
      q           the intensity of the unknown acceleration used for this row
    """
    local, v2x = _newest_per_track(tick.local), tick.v2x
    if local["sensor"].isna().any() or v2x["tx_station"].isna().any():
        raise ValueError("an object has no source: a sensor row without a sensor name, or a CPM row without a sender")
    if len(local) and len(v2x):
        objects = pd.concat([local, v2x], ignore_index=True)
    else:
        objects = (local if len(local) else v2x).reset_index(drop=True)
    n = len(objects)
    source = np.empty(n, dtype=object)
    source[:len(local)] = local["sensor"].to_numpy(dtype=object)
    source[len(local):] = ["v2x:%s" % sender for sender in v2x["tx_station"].to_numpy(dtype=object)]
    q = process_noise(objects["class"], q_by_class, q_default)
    state, cov = predict(objects, objects["age_now"].to_numpy(dtype=float), q)
    new = pd.DataFrame({"source": pd.Series(source, dtype=object, index=objects.index),
                        "x_t": state[:, 0], "y_t": state[:, 1], "vx_t": state[:, 2], "vy_t": state[:, 3],
                        "sigma_t": np.sqrt((cov[:, 0, 0] + cov[:, 1, 1]) / 2.0), "q": q}, index=objects.index)
    return Aligned(t=tick.t, ego=tick.ego, objects=pd.concat([objects, new], axis=1), state=state, cov=cov)
