"""The noise model of the replay (task B2, decision of 2026-10-12 on finding 1 of A8).

Why it is needed
  In the Artery logs, the position of an object is the exact true position, taken at the last
  simulation step. It carries no error, and it can be newer than the measurement time that the
  source declares. A fusion cannot be evaluated on such data.

What this module does, for each row of the observations table
  1. It takes the true state of the object at the declared measurement time ``t_meas``.
  2. It adds Gaussian noise: sigma_x on each position axis, sigma_v on each speed axis.
  3. It fills the covariance columns with the covariance of this noise.

The noise is tied to the measurement, not to the row
  One measurement can appear in several rows: the same CPM object is received by many vehicles,
  and a sender can repeat one measurement in two CPMs. All these rows get the same noisy values.
  The noise of a measurement depends only on the seed and on four things: who measured, with what,
  which object, and when. It does not depend on the order of the rows or on the other rows.
  So a subset of a run gives the same values as the full run.

Assumptions (each one can be changed later)
  - The noise is white: two measurements of one object at two times get independent errors.
  - The declared covariance is the true covariance of the noise (an honest source).
  - The error is isotropic, with no correlation between axes or between position and speed.
  - A CPM object is one measurement made by its sender, with the sigma of the sensor behind it.
    So a CAM relayed by several senders (sensors of the Artery example) gets one independent error
    per sender. The shared origin of these copies is not in the noise.

The values are the same on every machine up to the last digit (about 1e-15): the logarithm and the
sine of the processor can differ by one rounding step. Compare two runs with a tolerance, not with ==.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

_GOLDEN = np.uint64(0x9E3779B97F4A7C15)
_MIX_1 = np.uint64(0xBF58476D1CE4E5B9)
_MIX_2 = np.uint64(0x94D049BB133111EB)
_US = 1_000_000                      # times are compared in whole microseconds
_OBJ_SPAN = 10**13                   # room for the time in the combined key (object code, time)


@dataclass(frozen=True)
class NoiseModel:
    """Standard deviations of the measurement error, per axis.

    sigma_x    position error in m (default 0.3, the value of the theory note)
    sigma_v    speed error in m/s (default 0.5, the value of the theory note)
    seed       changes every noise value; the same seed gives the same values
    overrides  other values for some origins of the measurement, by the column ``meas_src``,
               for example ``{"cam_msg": (0.5, 0.2)}`` as (sigma_x, sigma_v)
    """

    sigma_x: float = 0.3
    sigma_v: float = 0.5
    seed: int = 0
    overrides: Mapping[str, tuple[float, float]] = field(default_factory=dict)

    def __post_init__(self):
        values = [(self.sigma_x, self.sigma_v), *self.overrides.values()]
        if not all(sx >= 0.0 and sv >= 0.0 for sx, sv in values):               # also refuses NaN
            raise ValueError("sigma_x and sigma_v must be numbers that are not negative")

    def sigmas(self, meas_src: pd.Series) -> tuple[np.ndarray, np.ndarray]:
        """sigma_x and sigma_v of each row, from its origin."""
        sx = np.full(len(meas_src), float(self.sigma_x))
        sv = np.full(len(meas_src), float(self.sigma_v))
        for origin, (ox, ov) in self.overrides.items():
            rows = (meas_src == origin).to_numpy(dtype=bool, na_value=False)
            sx[rows], sv[rows] = float(ox), float(ov)
        return sx, sv


# ----------------------------------------------------------------------------
# Noise values from keys (no random generator state: a value depends only on its key)
# ----------------------------------------------------------------------------
def _mix(z: np.ndarray) -> np.ndarray:
    """The last step of the splitmix64 generator: it spreads every input bit over the 64 output bits."""
    with np.errstate(over="ignore"):
        z = (z ^ (z >> np.uint64(30))) * _MIX_1
        z = (z ^ (z >> np.uint64(27))) * _MIX_2
        return z ^ (z >> np.uint64(31))


def _hash_text(text) -> np.uint64:
    return np.uint64(int.from_bytes(hashlib.blake2b(str(text).encode("utf-8"), digest_size=8).digest(), "little"))


def _hash_column(values: pd.Series) -> np.ndarray:
    """A 64-bit number for each text of a column. It is the same on every machine and in every version."""
    codes, uniques = pd.factorize(values, use_na_sentinel=False)
    table = np.array([_hash_text("\x00missing" if pd.isna(u) else u) for u in uniques], dtype=np.uint64)
    return table[codes] if len(table) else np.zeros(len(values), dtype=np.uint64)


def measurement_keys(seed: int, who: pd.Series, what: pd.Series, obj: pd.Series, t_meas: pd.Series) -> np.ndarray:
    """One 64-bit key per row. Two rows of the same measurement get the same key."""
    key = np.full(len(who), _hash_text("aoc-noise-%d" % int(seed)), dtype=np.uint64)
    time_us = np.round(t_meas.to_numpy(dtype=float) * _US).astype(np.int64).astype(np.uint64)
    for part in (_hash_column(who), _hash_column(what), _hash_column(obj), time_us):
        with np.errstate(over="ignore"):
            key = _mix((key ^ part) + _GOLDEN)
    return key


def standard_normals(keys: np.ndarray, n: int = 4) -> np.ndarray:
    """n independent standard normal values per key, as an array of shape (len(keys), n)."""
    def uniform(stream: int) -> np.ndarray:
        with np.errstate(over="ignore"):
            bits = _mix(keys + np.uint64(stream) * _GOLDEN)
        return ((bits >> np.uint64(12)).astype(np.float64) + 0.5) / 2.0**52           # strictly between 0 and 1

    out = np.empty((len(keys), n))
    for pair in range((n + 1) // 2):                                                # Box-Muller, two values per pair
        radius = np.sqrt(-2.0 * np.log(uniform(2 * pair + 1)))
        angle = 2.0 * np.pi * uniform(2 * pair + 2)
        out[:, 2 * pair] = radius * np.cos(angle)
        if 2 * pair + 1 < n:
            out[:, 2 * pair + 1] = radius * np.sin(angle)
    return out


# ----------------------------------------------------------------------------
# True state at any time
# ----------------------------------------------------------------------------
def true_state_at(truth: pd.DataFrame, obj_id: pd.Series, t: pd.Series) -> pd.DataFrame:
    """True x, y, vx, vy of each object at each time, by linear interpolation between the logged steps.

    Returns one row per input row, with the columns gt_x, gt_y, gt_vx, gt_vy and gt_gap.
    gt_gap is 0 when the time lies between two logged steps of the object that follow each other.
    Else (before its first step, after its last one, or inside a hole of its log) the state of the
    nearest logged step is used, and gt_gap is the distance to it in seconds.
    The values are NaN for an object that is not in the truth table, and for a missing id or time.
    """
    raw_code, names = pd.factorize(truth["obj_id"], use_na_sentinel=False)
    raw_t = np.round(truth["t"].to_numpy(dtype=float) * _US).astype(np.int64)
    order = np.lexsort((raw_t, raw_code))                                           # by object, then by time
    tr_code, tr_t = raw_code[order].astype(np.int64), raw_t[order]
    tr_key = tr_code * _OBJ_SPAN + tr_t
    state = truth[["x", "y", "vx", "vy"]].to_numpy(dtype=float)[order]
    code_of = {str(name): code for code, name in enumerate(names) if not pd.isna(name)}
    steps = np.diff(tr_t)[np.diff(tr_code) == 0]
    hole_us = 1.5 * float(np.median(steps)) if len(steps) else np.inf                # two steps further apart: a hole

    asked_code, asked_names = pd.factorize(obj_id, use_na_sentinel=False)
    table = np.array([-1 if pd.isna(name) else code_of.get(str(name), -1) for name in asked_names], dtype=np.int64)
    code = table[asked_code] if len(table) else np.full(len(obj_id), -1, dtype=np.int64)
    t_float = t.to_numpy(dtype=float, na_value=np.nan)
    known = (code >= 0) & np.isfinite(t_float)
    out = np.full((len(code), 5), np.nan)
    if known.any() and len(tr_t):
        c, tu = code[known], np.round(t_float[known] * _US).astype(np.int64)
        lo = np.searchsorted(tr_key, c * _OBJ_SPAN + tu, side="right") - 1          # last step at or before the time
        lo_ok = (lo >= 0) & (tr_code[np.clip(lo, 0, None)] == c)
        lo = np.where(lo_ok, lo, lo + 1)                                            # before the first step: use the first
        hi = np.minimum(lo + 1, len(tr_t) - 1)
        hi = np.where(lo_ok & (tr_code[hi] == c), hi, lo)                           # after the last step: use the last
        span = (tr_t[hi] - tr_t[lo]).astype(float)
        hole = span > hole_us
        nearest = np.where(tu - tr_t[lo] <= tr_t[hi] - tu, lo, hi)
        lo, hi = np.where(hole, nearest, lo), np.where(hole, nearest, hi)           # inside a hole: use the nearest step
        span = (tr_t[hi] - tr_t[lo]).astype(float)
        weight = np.where(span > 0, np.clip((tu - tr_t[lo]) / np.where(span > 0, span, 1.0), 0.0, 1.0), 0.0)
        values = state[lo] + weight[:, None] * (state[hi] - state[lo])
        gap = np.where(tu < tr_t[lo], tr_t[lo] - tu, np.where(tu > tr_t[hi], tu - tr_t[hi], 0)) / _US
        out[known] = np.column_stack([values, gap])
    return pd.DataFrame(out, columns=["gt_x", "gt_y", "gt_vx", "gt_vy", "gt_gap"], index=obj_id.index)


# ----------------------------------------------------------------------------
# The noise model applied to the observations table
# ----------------------------------------------------------------------------
def apply_noise(obs: pd.DataFrame, truth: pd.DataFrame, model: NoiseModel | None = None, missing: str = "raise") -> pd.DataFrame:
    """Returns a copy of the observations table with noisy states at the measurement time.

    Changed columns
      x, y, vx, vy            true state at t_meas plus noise
      P11, P12, P22           position covariance: sigma_x^2 on the diagonal, 0 outside
      Pv11, Pv22              speed covariance: sigma_v^2
      t_pos                   now equal to t_meas
      gt_dist                 distance between the noisy position and the true position at t_meas
    New columns (ground truth, for evaluation only: a fusion must not read them)
      gt_x, gt_y, gt_vx, gt_vy   true state at t_meas
      gt_gap                     0, or the time distance in s to the nearest logged true state

    missing: what to do with rows that have no true object or no measurement time ("raise" or "drop").
    """
    model = model or NoiseModel()
    if missing not in ("raise", "drop"):
        raise ValueError('missing must be "raise" or "drop"')
    out = obs.copy()
    state = true_state_at(truth, out["gt_match_id"], out["t_meas"])
    lost = state["gt_x"].isna().to_numpy()
    if lost.any():
        if missing == "raise":
            raise ValueError("%d of %d rows have no true object in the truth table, or no measurement time. "
                             'Use missing="drop" to leave them out.' % (int(lost.sum()), len(out)))
        out, state = out.loc[~lost].copy(), state.loc[~lost]

    is_v2x = (out["src"] == "v2x").to_numpy(dtype=bool, na_value=False)
    who = out["rx_station"].where(~is_v2x, out["tx_station"])              # the vehicle that made the measurement
    what = out["sensor"].where(~is_v2x, "cpm")                             # its sensor, or its CPM track
    keys = measurement_keys(model.seed, who, what, out["obj_id"], out["t_meas"])
    z = standard_normals(keys, 4)
    sx, sv = model.sigmas(out["meas_src"])

    out["x"] = state["gt_x"].to_numpy() + sx * z[:, 0]
    out["y"] = state["gt_y"].to_numpy() + sx * z[:, 1]
    out["vx"] = state["gt_vx"].to_numpy() + sv * z[:, 2]
    out["vy"] = state["gt_vy"].to_numpy() + sv * z[:, 3]
    out["P11"], out["P12"], out["P22"] = sx**2, 0.0, sx**2
    out["Pv11"], out["Pv22"] = sv**2, sv**2
    out["t_pos"] = out["t_meas"]
    out["gt_dist"] = np.hypot(sx * z[:, 0], sx * z[:, 1])
    for name in ("gt_x", "gt_y", "gt_vx", "gt_vy", "gt_gap"):
        out[name] = state[name].to_numpy()
    return out
