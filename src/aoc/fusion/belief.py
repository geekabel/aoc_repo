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

Weakening a mass function when its source is not fully reliable (task B5)
  discount             classical discounting (Shafer): one reliability for the source
  contextual_discount  contextual discounting (Mercier, Quost and Denoeux): one reliability per context,
                       where a context is a set of answers ("how reliable is the source when the truth is in this set?")
  disjunctive          the disjunctive rule. Both discountings are a disjunctive combination with a small mass function.

Combining the mass functions of several sources about one object (task B6)
  conjunctive          the conjunctive rule of the Transferable Belief Model: Dempster's rule without the
                       normalisation. The conflict stays as the mass of the empty set. For sources that are distinct.
  dempster             Dempster's rule: the conjunctive rule, then the conflict is removed and the rest rescaled.
  cautious             the cautious rule of Denoeux (2008), for sources that may share their information.
                       Combining a mass function with itself gives it back: shared information cannot be counted twice.
  commonality, weights, from_weights   the functions behind the cautious rule.
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


# ----------------------------------------------------------------------------
# Discounting (task B5)
# ----------------------------------------------------------------------------
def _per_row(values, n: int, what: str) -> np.ndarray:
    """One number between 0 and 1 per row. A single number is used for every row."""
    values = np.asarray(values, dtype=float)
    if values.ndim == 0:
        values = np.full(n, float(values))
    if values.shape != (n,):
        raise ValueError("%s: one number is needed, or one per mass function (%d)" % (what, n))
    if not np.isfinite(values).all() or (values < 0.0).any() or (values > 1.0).any():
        raise ValueError("%s must be numbers between 0 and 1" % what)
    return values


def disjunctive(frame: Frame, first, second) -> np.ndarray:
    """The disjunctive rule, row by row: each product m1(B) m2(C) goes to the union of B and C.

    It is the rule for two sources of which at least one is reliable, without knowing which one.
    One of the two tables can hold a single row: it is then used for every row of the other.
    """
    a, b = _table(frame, first), _table(frame, second)
    if len(a) != len(b):
        if len(a) == 1:
            a = np.repeat(a, len(b), axis=0)
        elif len(b) == 1:
            b = np.repeat(b, len(a), axis=0)
        else:
            raise ValueError("the two tables need the same number of rows (or one of them a single row)")
    out = np.zeros_like(a)
    for left in range(frame.n_sets):
        if not a[:, left].any():
            continue
        for right in range(frame.n_sets):
            if b[:, right].any():
                out[:, left | right] += a[:, left] * b[:, right]
    return out


def discount(frame: Frame, masses, reliability) -> np.ndarray:
    """Classical discounting (Shafer 1976), row by row.

    ``reliability`` is the degree of belief that the source is reliable: 1 keeps the mass function,
    0 gives "I do not know". Each mass keeps the share ``reliability``. The rest goes to the whole frame.
    (The books often write the discount RATE, which is 1 - reliability.)
    One number is used for every row. A list gives one number per row.
    """
    table = _table(frame, masses)
    r = _per_row(reliability, len(table), "the reliabilities")
    out = table * r[:, None]
    out[:, frame.omega] += (1.0 - r) * table.sum(axis=1)             # the total of a mass function is 1
    return out


def discount_kernel(frame: Frame, contexts, reliability, n: int = 1) -> np.ndarray:
    """The mass function that carries a contextual discounting: discounting is the disjunctive rule with it.

    Take any group G of contexts. Its weight is the product of the reliabilities of the contexts of G
    and of (1 - reliability) of the other contexts. This weight goes to the set of the answers that are
    in no context of G. Returns ``n`` rows (the reliabilities can differ from row to row).
    """
    try:
        numbers = [frame.index(context) for context in contexts]
    except TypeError:
        raise ValueError("the contexts must be sets of answers of the frame %s" % frame.name) from None
    if not numbers or 0 in numbers or len(set(numbers)) != len(numbers):
        raise ValueError("the contexts must be different sets of answers, none of them empty, and at least one is needed")
    if len(numbers) > 12:
        raise ValueError("at most 12 contexts")
    r = np.asarray(reliability, dtype=float)
    if r.ndim == 0:
        r = np.full(len(numbers), float(r))
    if r.ndim == 1:
        r = np.repeat(r[None, :], n, axis=0) if len(r) == len(numbers) else r
    if r.shape != (n, len(numbers)):
        raise ValueError("one reliability per context is needed (%d), or a table with one row per mass function" % len(numbers))
    if not np.isfinite(r).all() or (r < 0.0).any() or (r > 1.0).any():
        raise ValueError("the reliabilities must be numbers between 0 and 1")
    kernel = np.zeros((n, frame.n_sets))
    for group in range(1 << len(numbers)):
        weight = np.ones(n)
        covered = 0
        for position, number in enumerate(numbers):
            if group >> position & 1:
                weight = weight * r[:, position]
                covered |= number
            else:
                weight = weight * (1.0 - r[:, position])
        kernel[:, frame.omega & ~covered] += weight
    return kernel


def contextual_discount(frame: Frame, masses, contexts, reliability) -> np.ndarray:
    """Contextual discounting, row by row.

    ``contexts`` is a list of sets of answers, for example ``["E", "notE"]`` or ``[("car", "truck"), "pedestrian"]``.
    The reliability of a context is the degree of belief that the source is reliable when the true answer
    is in this set.

    ``reliability`` holds one number per context (a list is always read this way). To give other numbers
    to each mass function, pass a table with one row per mass function and one column per context.

    With the contexts {answer 1}, {answer 2}, ... it is the contextual discounting of Mercier, Quost and
    Denoeux (Information Fusion, 2008). With a partition of the frame it is their discounting on a coarsening.
    With the single context "whole frame" (``[frame.elements]``) it is the classical discounting.
    For contexts that overlap, the result is the one of the corrigendum of Mercier, Pichon and Lefevre (IJAR, 2016).
    An answer that is in no context counts as a context with reliability 0.
    """
    table = _table(frame, masses)
    return disjunctive(frame, table, discount_kernel(frame, contexts, reliability, len(table)))


# ----------------------------------------------------------------------------
# Combination (task B6)
# ----------------------------------------------------------------------------
def _pair(frame: Frame, first, second) -> tuple[np.ndarray, np.ndarray]:
    """Two tables with the same number of rows. A table of one row is repeated to match the other."""
    a, b = _table(frame, first), _table(frame, second)
    if len(a) != len(b):
        if len(a) == 1:
            a = np.repeat(a, len(b), axis=0)
        elif len(b) == 1:
            b = np.repeat(b, len(a), axis=0)
        else:
            raise ValueError("the two tables need the same number of rows (or one of them a single row)")
    return a, b


def conjunctive(frame: Frame, first, second) -> np.ndarray:
    """The conjunctive rule, row by row: each product m1(B) m2(C) goes to the intersection of B and C.

    It is Dempster's rule without the normalisation: the products that fall on the empty set stay there,
    as the conflict between the two sources. It is for two sources that are distinct (they do not share their
    information). One of the two tables can hold a single row: it is then used for every row of the other.
    """
    a, b = _pair(frame, first, second)
    out = np.zeros_like(a)
    for left in range(frame.n_sets):
        if not a[:, left].any():
            continue
        for right in range(frame.n_sets):
            if b[:, right].any():
                out[:, left & right] += a[:, left] * b[:, right]
    return out


def normalize(frame: Frame, masses) -> np.ndarray:
    """Removes the mass of the empty set and rescales the rest to a total of 1 (the closed-world reading).

    A row with all its mass on the empty set cannot be rescaled: it is returned as it is.
    """
    table = _table(frame, masses).copy()
    kept = table[:, 1:].sum(axis=1)
    can = kept > 0.0
    table[can, 0] = 0.0
    table[can] /= kept[can, None]
    return table


def dempster(frame: Frame, first, second) -> np.ndarray:
    """Dempster's rule, row by row: the conjunctive rule, then the conflict is removed and the rest rescaled."""
    return normalize(frame, conjunctive(frame, first, second))


def commonality(frame: Frame, masses) -> np.ndarray:
    """The commonality of each set, for each row: the masses of the sets that contain it (itself included)."""
    table = _table(frame, masses)
    sets = np.arange(frame.n_sets)
    contains = (sets[:, None] & sets[None, :]) == sets[None, :]           # contains[b, a]: set b contains set a
    return table @ contains.astype(float)


def _mobius_sign(frame: Frame) -> np.ndarray:
    """sign[a, b] = (-1)^(|b| - |a|) when set b contains set a, else 0."""
    sets = np.arange(frame.n_sets)
    contains = (sets[:, None] & sets[None, :]) == sets[:, None]           # contains[a, b]: b contains a
    return np.where(contains, (-1.0) ** (frame.sizes[None, :] - frame.sizes[:, None]), 0.0)


def weights(frame: Frame, masses) -> np.ndarray:
    """The conjunctive weights of each row (Smets 1995, Denoeux 2008), one column per set except the whole frame.

    A mass function is the conjunctive combination of one generalized simple mass function per set A, with
    1 - w(A) on A and w(A) on the whole frame. The weights are the w(A). A weight below 1 is support for A.
    A weight above 1 is the opposite: 1 - w(A) is then negative, and the piece is not a mass function on its
    own (that is why it is called generalized). The last column (the whole frame) is 1 and is kept so that the
    columns line up with the sets. The mass functions must not be dogmatic: some mass on the whole frame is needed.
    """
    table = _table(frame, masses)
    if (table[:, frame.omega] <= 0.0).any():
        raise ValueError("the weights need mass functions with some mass on the whole frame (not dogmatic)")
    if (table < -1e-9).any():
        raise ValueError("the weights need mass functions without negative masses")
    q = commonality(frame, table)
    log_q = np.log(q)                                                     # q >= m(whole frame) > 0 for every set
    # ln w(A) = sum over the sets B that contain A of (-1)^(|B| - |A| + 1) ln q(B)
    log_w = -(log_q @ _mobius_sign(frame).T)
    log_w[:, frame.omega] = 0.0
    return np.exp(log_w)


def from_weights(frame: Frame, w) -> np.ndarray:
    """The mass function of each row of conjunctive weights (the inverse of ``weights``).

    The last column (the whole frame) must be 1, as ``weights`` gives it.
    """
    w = np.asarray(w, dtype=float)
    if w.ndim == 1:
        w = w[None, :]
    if w.ndim != 2 or w.shape[1] != frame.n_sets or not (w > 0.0).all():
        raise ValueError("a table of weights needs %d positive columns" % frame.n_sets)
    if not np.allclose(w[:, frame.omega], 1.0):
        raise ValueError("the weight of the whole frame must be 1")
    sets = np.arange(frame.n_sets)
    # q(C) = product over the sets B (other than the whole frame) that do not contain C of w(B)
    not_contains = ((sets[:, None] & sets[None, :]) != sets[None, :]).astype(float)       # [b, c]: b does not contain c
    not_contains[frame.omega, :] = 0.0
    log_q = np.log(w) @ not_contains
    # m(A) = sum over the sets B that contain A of (-1)^(|B| - |A|) q(B)
    return np.exp(log_q) @ _mobius_sign(frame).T


def cautious(frame: Frame, first, second) -> np.ndarray:
    """The cautious rule of Denoeux (2008), row by row: the weights of the two mass functions are combined by the minimum.

    It is for two sources that may share their information. The combination of a mass function with itself
    gives it back, so a piece of information that reaches the vehicle twice counts once. For two simple
    mass functions on the same set, the result keeps the larger support. The mass functions must not be dogmatic.
    The conflict stays as the mass of the empty set; ``normalize`` removes it.
    """
    a, b = _pair(frame, first, second)
    return from_weights(frame, np.minimum(weights(frame, a), weights(frame, b)))


def combine(frame: Frame, tables, rule=conjunctive) -> np.ndarray:
    """Combines several tables of mass functions, row by row, with a rule (``conjunctive``, ``dempster`` or ``cautious``).

    With no table the result is "I do not know" for one row. With one table the result is this table.
    """
    tables = list(tables)
    if not tables:
        return vacuous(frame, 1)
    out = _table(frame, tables[0])
    for table in tables[1:]:
        out = rule(frame, out, table)
    return out
