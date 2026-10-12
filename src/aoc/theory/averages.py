#!/usr/bin/env python3
"""
A3 of the AoC plan: expected reliability E[alpha] under Poisson and periodic
updates with i.i.d. loss, with the plateau surrogate chosen after A2.

What the script does
  1. Age laws at the receiver: Poisson and periodic generation, loss p, delay d0.
     Check of the two CDFs against a simulated update process.
  2. Closed forms of E[alpha] with the plateau surrogate (and with the
     exponential surrogate of the Plan, for comparison).
     Check of the algebra against the simulated process.
  3. Model error: closed forms against the exact law (numerical integral),
     on the E2 grid: rate in {1, 2, 5, 10} Hz, loss in {0, 0.1, 0.3, 0.5}.

Run:  python -m aoc.theory.averages        (about 1 minute)
Needs numpy and scipy. The run writes a3_results.json in the current folder.

Notation
  lam   generation rate of updates about one object [Hz]; T = 1/lam (periodic)
  p     loss probability of one update, independent between updates
  mu    rate of received updates, mu = (1 - p) lam
  d0    age of the object at reception [s]
  A     time since the last received update [s]; the age is Delta = d0 + A
  D0, tau2  plateau length and decay time of the surrogate (from A2) [s]
"""
import json
import math

import numpy as np
from scipy import integrate

from . import reliability as a2

EPS = a2.EPS
D0_DELAY = a2.D0            # 0.13 s
RATES = (1.0, 2.0, 5.0, 10.0)
LOSSES = (0.0, 0.1, 0.3, 0.5)


# ----------------------------------------------------------------------------
# Age laws (time since the last received update, seen at a random time)
# ----------------------------------------------------------------------------
def age_cdf_poisson(a, lam, p):
    mu = (1.0 - p) * lam
    return 1.0 - np.exp(-mu * np.asarray(a, dtype=float))


def age_cdf_periodic(a, T, p):
    a = np.asarray(a, dtype=float)
    k = np.floor(a / T)
    r = a - k * T
    return 1.0 - p**k + (1.0 - p) * p**k * r / T


def mean_age_poisson(lam, p):
    return 1.0 / ((1.0 - p) * lam)


def mean_age_periodic(T, p):
    return T / 2.0 + T * p / (1.0 - p)


# ----------------------------------------------------------------------------
# Closed forms of E[alpha]
# ----------------------------------------------------------------------------
def e_plateau_poisson(lam, p, D0, tau2, d0=0.0):
    mu = (1.0 - p) * lam
    if d0 <= D0:
        return 1.0 - math.exp(-mu * (D0 - d0)) / (mu * tau2 + 1.0)
    return math.exp(-(d0 - D0) / tau2) * mu * tau2 / (mu * tau2 + 1.0)


def e_plateau_periodic(T, p, D0, tau2, d0=0.0):
    x = math.exp(-T / tau2)
    if d0 > D0:
        return math.exp(-(d0 - D0) / tau2) * (1.0 - p) * tau2 * (1.0 - x) / (T * (1.0 - p * x))
    Dp = D0 - d0                      # plateau left after the reception delay
    m = math.floor(Dp / T)            # whole periods inside the plateau
    r = Dp - m * T
    inner = r / T + (tau2 / T) * (1.0 - (1.0 - p) * math.exp(-(T - r) / tau2) / (1.0 - p * x))
    return 1.0 - p**m + (1.0 - p) * p**m * inner


def e_expo_poisson(lam, p, tau, d0=0.0):
    mu = (1.0 - p) * lam
    return math.exp(-d0 / tau) * mu * tau / (mu * tau + 1.0)


def e_expo_periodic(T, p, tau, d0=0.0):
    x = math.exp(-T / tau)
    return math.exp(-d0 / tau) * (1.0 - p) * tau * (1.0 - x) / (T * (1.0 - p * x))


# ----------------------------------------------------------------------------
# Exact law, numerical integration over the age law
# ----------------------------------------------------------------------------
def e_exact_poisson(lam, p, par, d0=0.0):
    mu = (1.0 - p) * lam
    f = lambda a: mu * math.exp(-mu * a) * float(a2.alpha(d0 + a, *par, EPS))
    return integrate.quad(f, 0.0, np.inf, limit=400)[0]


def e_exact_periodic(T, p, par, d0=0.0):
    total, k = 0.0, 0
    while True:
        w = (1.0 - p) * p**k / T
        if w < 1e-12 / T or k > 400:
            break
        part = integrate.quad(lambda a: float(a2.alpha(d0 + a, *par, EPS)), k * T, (k + 1) * T, limit=200)[0]
        total += w * part
        k += 1
        if p == 0.0:
            break
    return total


# ----------------------------------------------------------------------------
# Simulated update process (no formula reused)
# ----------------------------------------------------------------------------
def simulate_ages(kind, lam, p, n_updates=400_000, n_obs=1_000_000, seed=3):
    """Ages A seen at random times, for a simulated generation and loss process."""
    rng = np.random.default_rng(seed)
    if kind == "poisson":
        gen = np.cumsum(rng.exponential(1.0 / lam, n_updates))
    else:
        gen = (np.arange(n_updates) + rng.uniform()) / lam
    rx = gen[rng.uniform(size=n_updates) >= p]
    t = rng.uniform(rx[10], rx[-10], n_obs)
    idx = np.searchsorted(rx, t, side="right") - 1
    return t - rx[idx]


def main():
    res = {"d0": D0_DELAY, "classes": {}}
    par = {name: v[:4] for name, v in a2.CLASSES.items()}
    sur = {name: a2.plateau_surrogate(*par[name]) for name in par}
    tau = {name: a2.tau_from_half_life(a2.half_life(*par[name])) for name in par}

    print("=" * 86)
    print("1. Age laws: formula against a simulated update process (largest CDF gap)")
    grid = np.linspace(0.0, 6.0, 601)
    worst = 0.0
    for lam, p in ((2.0, 0.3), (10.0, 0.5), (1.0, 0.1)):
        for kind in ("poisson", "periodic"):
            ages = simulate_ages(kind, lam, p)
            emp = np.searchsorted(np.sort(ages), grid, side="right") / len(ages)
            th = age_cdf_poisson(grid, lam, p) if kind == "poisson" else age_cdf_periodic(grid, 1.0 / lam, p)
            mean_th = mean_age_poisson(lam, p) if kind == "poisson" else mean_age_periodic(1.0 / lam, p)
            gap = float(np.max(np.abs(emp - th)))
            worst = max(worst, gap)
            print(f"   {kind:8s} rate {lam:4.1f} Hz, p = {p:.1f}: CDF gap {gap:.4f}, mean age {ages.mean():.4f} s (formula {mean_th:.4f} s)")
    res["age_cdf_worst_gap"] = worst

    print("=" * 86)
    print("2. Algebra check: closed form of the plateau surrogate against the simulated process")
    worst = 0.0
    for name in ("pedestrian", "car (moving)", "vehicle (parked)"):
        D0, tau2 = sur[name]
        for lam, p in ((1.0, 0.5), (2.0, 0.3), (5.0, 0.1), (10.0, 0.0)):
            for kind in ("poisson", "periodic"):
                ages = simulate_ages(kind, lam, p, seed=7)
                sim = float(np.mean(a2.plateau_alpha(D0_DELAY + ages, D0, tau2)))
                cf = (e_plateau_poisson(lam, p, D0, tau2, D0_DELAY) if kind == "poisson"
                      else e_plateau_periodic(1.0 / lam, p, D0, tau2, D0_DELAY))
                worst = max(worst, abs(sim - cf))
    print(f"   largest gap over 24 cases: {worst:.4f} (simulation noise is about 0.001)")
    res["algebra_worst_gap"] = worst
    # a case where the delay is longer than the plateau, and the Plan limit D0 = 0
    assert abs(e_plateau_periodic(0.5, 0.3, 0.0, 2.0) - e_expo_periodic(0.5, 0.3, 2.0)) < 1e-12
    assert abs(e_plateau_poisson(2.0, 0.3, 0.0, 2.0) - e_expo_poisson(2.0, 0.3, 2.0)) < 1e-12
    ages = simulate_ages("periodic", 2.0, 0.3, seed=9)
    sim = float(np.mean(a2.plateau_alpha(0.9 + ages, 0.5, 1.0)))
    assert abs(sim - e_plateau_periodic(0.5, 0.3, 0.5, 1.0, 0.9)) < 0.003
    print("   limits checked: D0 = 0 gives the Plan formulas; delay longer than the plateau is handled")

    print("=" * 86)
    print(f"3. Model error on the E2 grid (rates {RATES} Hz, losses {LOSSES}), d0 = {D0_DELAY} s")
    print("   relative error of the closed form against the exact law, worst case over the grid")
    print(f"   {'class':18s} | {'Poisson':^31s} | {'periodic':^31s}")
    print(f"   {'':18s} | {'plateau':>14s} {'exponential':>16s} | {'plateau':>14s} {'exponential':>16s}")
    for name in par:
        D0, tau2 = sur[name]
        rows = []
        worst_e = {"pp": (0, None), "ep": (0, None), "pt": (0, None), "et": (0, None)}
        for lam in RATES:
            for p in LOSSES:
                ex_p = e_exact_poisson(lam, p, par[name], D0_DELAY)
                ex_t = e_exact_periodic(1.0 / lam, p, par[name], D0_DELAY)
                v = {
                    "pp": e_plateau_poisson(lam, p, D0, tau2, D0_DELAY),
                    "ep": e_expo_poisson(lam, p, tau[name], D0_DELAY),
                    "pt": e_plateau_periodic(1.0 / lam, p, D0, tau2, D0_DELAY),
                    "et": e_expo_periodic(1.0 / lam, p, tau[name], D0_DELAY),
                }
                rows.append({"rate": lam, "p": p, "exact_poisson": ex_p, "exact_periodic": ex_t, **v})
                for key, ex in (("pp", ex_p), ("ep", ex_p), ("pt", ex_t), ("et", ex_t)):
                    rel = abs(v[key] - ex) / ex
                    if rel > worst_e[key][0]:
                        worst_e[key] = (rel, (lam, p))
        res["classes"][name] = {"D0": D0, "tau2": tau2, "tau": tau[name], "grid": rows,
                                "worst": {k: {"rel": w[0], "at": w[1]} for k, w in worst_e.items()}}
        fmt = lambda w: f"{100 * w[0]:5.1f}% at {w[1][0]:g} Hz, p={w[1][1]:g}"
        print(f"   {name:18s} | {fmt(worst_e['pp']):>14s} {fmt(worst_e['ep']):>16s} | {fmt(worst_e['pt']):>14s} {fmt(worst_e['et']):>16s}")

    print("=" * 86)
    print("4. Values of E[alpha] for the pedestrian (exact law / plateau closed form)")
    for kind in ("poisson", "periodic"):
        print(f"   {kind}")
        print("   rate    " + "".join(f"      p = {p:.1f}      " for p in LOSSES))
        for lam in RATES:
            cells = []
            for p in LOSSES:
                row = next(r for r in res["classes"]["pedestrian"]["grid"] if r["rate"] == lam and r["p"] == p)
                ex = row["exact_poisson"] if kind == "poisson" else row["exact_periodic"]
                cf = row["pp"] if kind == "poisson" else row["pt"]
                cells.append(f"  {ex:.3f} / {cf:.3f}  ")
            print(f"   {lam:4.0f} Hz " + "".join(cells))

    with open("a3_results.json", "w") as fh:
        json.dump(res, fh, indent=1)
    print("=" * 86)
    print("Results written to a3_results.json")


if __name__ == "__main__":
    main()
