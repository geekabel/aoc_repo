"""Mass functions on small frames, for many objects at once (first part of task B4).

A mass function says how much support each set of answers gets. Here the questions are small:
"does the object exist?" has two answers, "what is it?" has four. So a mass function is stored as a
row of numbers, one per set of answers, and the mass functions of all objects of a tick are one table.

How a set of answers is numbered
  The answers of a frame have a position: 0, 1, 2, ... A set of answers is the whole number whose
  bits are the positions of its members. For the existence frame ("E", "notE"):

      0 = no answer (the empty set, which holds the conflict)
      1 = {E}
      2 = {notE}
      3 = {E, notE} (the whole frame: "I do not know")

  A table of mass functions has one row per object and one column per set, in this order.

The three usual readings of a mass function
  belief        support that is certain for a set: the masses of the sets inside it
  plausibility  support that is possible for a set: the masses of the sets that touch it
  pignistic     one probability per answer, to take a decision: each mass is shared equally
                between the answers of its set

The empty set is kept (the open-world reading of the Transferable Belief Model): after a combination,
its mass is the conflict between the sources. The pignistic probability leaves it out and rescales.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class Frame:
    """A question and its possible answers."""

    name: str
    elements: tuple[str, ...]

    def __post_init__(self):
        if not 1 <= len(self.elements) <= 8 or len(set(self.elements)) != len(self.elements):
            raise ValueError("a frame needs 1 to 8 different answers")

    @property
    def n_sets(self) -> int:
        """Number of sets of answers, the empty set included."""
        return 1 << len(self.elements)

    @property
    def omega(self) -> int:
        """Number of the whole frame."""
        return self.n_sets - 1

    def index(self, subset: str | Iterable[str]) -> int:
        """Number of a set of answers. A single answer can be given as a text."""
        members = (subset,) if isinstance(subset, str) else tuple(subset)
        number = 0
        for member in members:
            if member not in self.elements:
                raise ValueError("%r is not an answer of the frame %s" % (member, self.name))
            number |= 1 << self.elements.index(member)
        return number

    def subset(self, number: int) -> frozenset[str]:
        """The answers of the set with this number."""
        if not 0 <= number < self.n_sets:
            raise ValueError("the frame %s has no set number %d" % (self.name, number))
        return frozenset(e for i, e in enumerate(self.elements) if number >> i & 1)

    def label(self, number: int) -> str:
        """A short text for a set: "empty", "E", "E|notE"."""
        members = [e for i, e in enumerate(self.elements) if number >> i & 1]
        return "|".join(members) if members else "empty"

    @cached_property
    def sizes(self) -> np.ndarray:
        """Number of answers in each set."""
        return np.array([bin(a).count("1") for a in range(self.n_sets)], dtype=np.int64)

    @cached_property
    def inside(self) -> np.ndarray:
        """inside[b, a] is True when set b is inside set a and is not empty."""
        sets = np.arange(self.n_sets)
        return ((sets[:, None] & sets[None, :]) == sets[:, None]) & (sets[:, None] != 0)

    @cached_property
    def touches(self) -> np.ndarray:
        """touches[b, a] is True when sets a and b share an answer."""
        sets = np.arange(self.n_sets)
        return (sets[:, None] & sets[None, :]) != 0


#: Does the object exist? "E": it exists. "notE": it does not.
EXISTENCE = Frame("existence", ("E", "notE"))
#: What is the object? The four classes of the plan.
CLASS = Frame("class", ("car", "truck", "bicycle", "pedestrian"))


def _table(frame: Frame, masses) -> np.ndarray:
    table = np.asarray(masses, dtype=float)
    if table.ndim == 1:
        table = table[None, :]
    if table.ndim != 2 or table.shape[1] != frame.n_sets:
        raise ValueError("a table of mass functions on the frame %s needs %d columns" % (frame.name, frame.n_sets))
    return table


def vacuous(frame: Frame, n: int) -> np.ndarray:
    """n mass functions that say nothing: all the mass on the whole frame."""
    table = np.zeros((n, frame.n_sets))
    table[:, frame.omega] = 1.0
    return table


def simple_support(frame: Frame, subset: str | Iterable[str], weight) -> np.ndarray:
    """One mass function per weight: the weight on the given set, the rest on the whole frame.

    A weight of 0 gives the vacuous mass function, a weight of 1 a certainty.
    """
    weight = np.atleast_1d(np.asarray(weight, dtype=float))
    if weight.ndim != 1 or not np.isfinite(weight).all() or (weight < 0.0).any() or (weight > 1.0).any():
        raise ValueError("the weights must be numbers between 0 and 1")
    number = frame.index(subset)
    if number == 0:
        raise ValueError("a simple support needs a set that is not empty")
    table = np.zeros((len(weight), frame.n_sets))
    table[:, frame.omega] = 1.0 - weight
    table[:, number] += weight                      # += so that the whole frame as the set keeps a total of 1
    return table


def is_valid(frame: Frame, masses, tolerance: float = 1e-9) -> np.ndarray:
    """True for each row that is a mass function: no negative mass, and a total of 1."""
    table = _table(frame, masses)
    return np.isfinite(table).all(axis=1) & (table >= -tolerance).all(axis=1) & (np.abs(table.sum(axis=1) - 1.0) <= tolerance)


def conflict(frame: Frame, masses) -> np.ndarray:
    """The mass of the empty set of each row."""
    return _table(frame, masses)[:, 0].copy()


def belief(frame: Frame, masses, subset: str | Iterable[str]) -> np.ndarray:
    """Belief of a set, for each row: the masses of the sets inside it (the empty set left out)."""
    return _table(frame, masses) @ frame.inside[:, frame.index(subset)].astype(float)


def plausibility(frame: Frame, masses, subset: str | Iterable[str]) -> np.ndarray:
    """Plausibility of a set, for each row: the masses of the sets that share an answer with it."""
    return _table(frame, masses) @ frame.touches[:, frame.index(subset)].astype(float)


def pignistic(frame: Frame, masses) -> np.ndarray:
    """One probability per answer, for each row: shape (rows, answers).

    Each mass is shared equally between the answers of its set. The mass of the empty set is left out
    and the rest is rescaled to a total of 1. A row with all its mass on the empty set gives NaN.
    """
    table = _table(frame, masses)
    share = np.zeros((frame.n_sets, len(frame.elements)))
    for number in range(1, frame.n_sets):
        for position in range(len(frame.elements)):
            if number >> position & 1:
                share[number, position] = 1.0 / frame.sizes[number]
    kept = 1.0 - table[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        return (table @ share) / np.where(kept > 0.0, kept, np.nan)[:, None]
