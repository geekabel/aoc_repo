#!/usr/bin/env python3
"""
A4 of the AoC plan: the inverse rate law and the DCC feasibility check.

What the script does
  1. Inverse laws with the plateau surrogate:
     Poisson   smallest generation rate for a target (a formula, Lambert W)
     periodic  longest period for a target (a formula without loss,
               bisection with loss)
     floor     longest period that keeps alpha above the target at all times
  2. Check of the inverses: put the result back into the forward formulas,
     and compare with the inverse of the exact law (numerical).
  3. Required rates per class and target.
  4. DCC feasibility: required rate against the rate allowed in each DCC state.

Run:  python -m aoc.theory.rates        (about 1 minute)
Needs numpy and scipy. The run writes a4_results.json in the current folder.
"""
import json
import math

from scipy import optimize, special

from . import averages as a3
from . import reliability as a2

D0_DELAY = a2.D0                     # age at reception, 0.13 s
TARGETS = (0.8, 0.9, 0.95)
LOSSES = (0.0, 0.1, 0.3)

# ETSI TS 102 687, Table A.1 (informative, one station): channel busy ratio -> highest message rate
DCC_STATES = (("below 30%", 10.0), ("30 to 39%", 5.0), ("40 to 49%", 2.5), ("50 to 60%", 2.0), ("above 60%", 1.0))
# Limits of the CPM generation rules (also the defaults of the Artery CpService): 0.1 s to 1 s per object
T_MIN_ETSI, T_MAX_ETSI = 0.1, 1.0


# ----------------------------------------------------------------------------
# Inverse laws, plateau surrogate
# ----------------------------------------------------------------------------
def mu_min_poisson(target, D0, tau2, d0=0.0):
    """Smallest rate of RECEIVED updates with E >= target. The generation rate is mu / (1 - p)."""
    Dp = D0 - d0
    if Dp <= 0.0:
        x = target * math.exp(-Dp / tau2)
        return float("inf") if x >= 1.0 else x / (1.0 - x) / tau2
    z = Dp / tau2
    u = special.lambertw(z * math.exp(z) / (1.0 - target)).real / z
    return (u - 1.0) / tau2


def t_max_periodic_no_loss(target, D0, tau2, d0=0.0):
    """Longest period with E >= target when no update is lost (a formula, case d0 <= D0)."""
    Dp = D0 - d0
    d = Dp * (1.0 - target) + tau2
    w = special.lambertw(-(1.0 / target) * math.exp(-d / (target * tau2)), k=0).real
    return Dp + d / target + tau2 * w


def t_max_periodic(target, p, D0, tau2, d0=0.0):
    """Longest period with E >= target, loss p (bisection on the forward formula)."""
    g = lambda T: a3.e_plateau_periodic(T, p, D0, tau2, d0) - target
    lo = 1e-4
    if g(lo) < 0.0:
        return 0.0                      # the target cannot be reached
    hi = 1.0
    while g(hi) > 0.0:
        hi *= 2.0
    return optimize.brentq(g, lo, hi, xtol=1e-10)


def t_floor(target, par, d0=0.0):
    """Longest period for which alpha stays above the target between two received updates (exact law, no loss)."""
    return a2.age_at_level(target, *par, a2.EPS) - d0


def time_above_target(target, par, kind, rate, p, d0=0.0):
    """Fraction of time with alpha >= target (exact law): the age CDF at the age where alpha = target."""
    a_max = a2.age_at_level(target, *par, a2.EPS) - d0
    if a_max <= 0.0:
        return 0.0
    if kind == "poisson":
        return float(a3.age_cdf_poisson(a_max, rate, p))
    return float(a3.age_cdf_periodic(a_max, 1.0 / rate, p))


# ----------------------------------------------------------------------------
# Inverse of the exact law (numerical reference)
# ----------------------------------------------------------------------------
def lam_min_poisson_exact(target, p, par, d0=0.0):
    if float(a2.alpha(d0, *par, a2.EPS)) <= target:
        return float("inf")
    g = lambda lam: a3.e_exact_poisson(lam, p, par, d0) - target
    hi = 1.0
    while g(hi) < 0.0:
        hi *= 2.0
    lo = hi / 2.0
    while g(lo) > 0.0:
        lo /= 2.0
    return optimize.brentq(g, lo, hi, xtol=1e-8)


def t_max_periodic_exact(target, p, par, d0=0.0):
    if float(a2.alpha(d0, *par, a2.EPS)) <= target:
        return 0.0
    g = lambda T: a3.e_exact_periodic(T, p, par, d0) - target
    lo, hi = 1e-3, 1.0
    if g(lo) < 0.0:
        return 0.0
    while g(hi) > 0.0:
        hi *= 2.0
    return optimize.brentq(g, lo, hi, xtol=1e-8)


def main():
    par = {name: v[:4] for name, v in a2.CLASSES.items()}
    sur = {name: a2.plateau_surrogate(*par[name]) for name in par}
    res = {"d0": D0_DELAY, "classes": {}}

    print("=" * 92)
    print("1. Checks of the inverse laws")
    worst_back, worst_closed, mono = 0.0, 0.0, True
    for name in par:
        D0, tau2 = sur[name]
        for tg in TARGETS:
            for p in LOSSES:
                mu = mu_min_poisson(tg, D0, tau2, D0_DELAY)
                back = a3.e_plateau_poisson(mu / (1 - p), p, D0, tau2, D0_DELAY)
                worst_back = max(worst_back, abs(back - tg))
                T = t_max_periodic(tg, p, D0, tau2, D0_DELAY)
                if T > 0:
                    worst_back = max(worst_back, abs(a3.e_plateau_periodic(T, p, D0, tau2, D0_DELAY) - tg))
            worst_closed = max(worst_closed, abs(t_max_periodic_no_loss(tg, D0, tau2, D0_DELAY)
                                                 - t_max_periodic(tg, 0.0, D0, tau2, D0_DELAY)))
        # E must fall when the period grows, otherwise the bisection is not valid
        for p in (0.0, 0.1, 0.3, 0.5):
            prev = 2.0
            for i in range(1, 2001):
                T = 0.01 * i * max(1.0, D0)
                e = a3.e_plateau_periodic(T, p, D0, tau2, D0_DELAY)
                if e > prev + 1e-12:
                    mono = False
                prev = e
    print(f"   forward formula at the inverse gives the target back: largest gap {worst_back:.2e}")
    print(f"   the formula without loss against the bisection: largest gap {worst_closed:.2e} s")
    print(f"   E[alpha] falls when the period grows (all classes, 4 losses): {mono}")
    res["checks"] = {"back": worst_back, "closed_no_loss": worst_closed, "monotone": mono}

    print("=" * 92)
    print(f"2. Required generation rate per class [Hz], d0 = {D0_DELAY} s")
    print("   P = Poisson, T = periodic. 'exact' is the inverse of the exact law. 'Plan' is the exponential formula.")
    for tg in TARGETS:
        for p in LOSSES:
            print(f"   target {tg}, loss {p}")
            print(f"   {'class':18s} {'P plateau':>10} {'P exact':>9} {'ratio':>6} | {'T plateau':>10} {'T exact':>9} {'ratio':>6} | {'P Plan':>8} {'floor':>7}")
            for name in par:
                D0, tau2 = sur[name]
                tau = a2.tau_from_half_life(a2.half_life(*par[name]))
                lp = mu_min_poisson(tg, D0, tau2, D0_DELAY) / (1 - p)
                lpe = lam_min_poisson_exact(tg, p, par[name], D0_DELAY)
                T = t_max_periodic(tg, p, D0, tau2, D0_DELAY)
                Te = t_max_periodic_exact(tg, p, par[name], D0_DELAY)
                lt = 1.0 / T if T > 0 else float("inf")
                lte = 1.0 / Te if Te > 0 else float("inf")
                plan = tg / (1 - tg) / ((1 - p) * tau)
                fl = t_floor(tg, par[name], D0_DELAY)
                lfl = 1.0 / fl if fl > 0 else float("inf")
                print(f"   {name:18s} {lp:10.2f} {lpe:9.2f} {lp / lpe:6.2f} | {lt:10.2f} {lte:9.2f} {lt / lte:6.2f} | {plan:8.2f} {lfl:7.2f}")
                res["classes"].setdefault(name, {})[f"{tg}|{p}"] = {
                    "poisson_plateau": lp, "poisson_exact": lpe, "periodic_plateau": lt,
                    "periodic_exact": lte, "plan": plan, "floor_rate": lfl, "T_max": T, "T_floor": fl}

    print("=" * 92)
    print("3. DCC feasibility: highest target reached with periodic updates at the rate of each DCC state")
    print("   (exact law, loss 0.1). 'ok' marks a 0.9 target reached.")
    print(f"   {'class':18s} " + " ".join(f"{s[0] + ' / ' + format(s[1], 'g') + ' Hz':>20s}" for s in DCC_STATES))
    for name in par:
        cells = []
        for label, rate in DCC_STATES:
            e = a3.e_exact_periodic(1.0 / rate, 0.1, par[name], D0_DELAY)
            cells.append(f"{e:14.3f} {'ok' if e >= 0.9 else '--':>5s}")
            res["classes"][name][f"dcc|{rate}"] = e
        print(f"   {name:18s} " + " ".join(cells))

    print("=" * 92)
    print("4. Required rate against the limits of the CPM rules (0.1 s to 1 s per object), target 0.9, periodic")
    print(f"   {'class':18s} {'loss 0':>8} {'loss 0.1':>9} {'loss 0.3':>9} {'loss 0.5':>9}   highest loss tolerated at 10 Hz")
    for name in par:
        D0, tau2 = sur[name]
        row = []
        for p in (0.0, 0.1, 0.3, 0.5):
            T = t_max_periodic(0.9, p, D0, tau2, D0_DELAY)
            row.append(1.0 / T if T > 0 else float("inf"))
        g = lambda p: a3.e_plateau_periodic(T_MIN_ETSI, p, D0, tau2, D0_DELAY) - 0.9
        pmax = optimize.brentq(g, 0.0, 0.999) if g(0.0) > 0 else 0.0
        print(f"   {name:18s} " + " ".join(f"{v:8.2f} " for v in row) + f"  {pmax:.2f}")
        res["classes"][name]["rates_0.9"] = row
        res["classes"][name]["pmax_10Hz"] = pmax

    print("=" * 92)
    print("5. Fraction of time above the 0.9 target (exact law, loss 0.1)")
    import numpy as np
    ages = a3.simulate_ages("periodic", 2.0, 0.1, seed=11)
    sim = float(np.mean(a2.alpha(D0_DELAY + ages, *par["pedestrian"], a2.EPS) >= 0.9))
    cf = time_above_target(0.9, par["pedestrian"], "periodic", 2.0, 0.1, D0_DELAY)
    print(f"   check, pedestrian, periodic 2 Hz: formula {cf:.4f}, simulated {sim:.4f}")
    res["checks"]["time_above"] = [cf, sim]
    print(f"   {'class':18s} {'periodic at the':>16} {'periodic at the':>16} {'Poisson at the':>16}")
    print(f"   {'':18s} {'average rate':>16} {'floor rate':>16} {'average rate':>16}")
    for name in par:
        r_avg = res["classes"][name]["0.9|0.1"]["periodic_exact"]
        r_floor = res["classes"][name]["0.9|0.1"]["floor_rate"]
        r_poi = res["classes"][name]["0.9|0.1"]["poisson_exact"]
        v = (time_above_target(0.9, par[name], "periodic", r_avg, 0.1, D0_DELAY),
             time_above_target(0.9, par[name], "periodic", r_floor, 0.1, D0_DELAY),
             time_above_target(0.9, par[name], "poisson", r_poi, 0.1, D0_DELAY))
        print(f"   {name:18s} {v[0]:16.3f} {v[1]:16.3f} {v[2]:16.3f}")
        res["classes"][name]["time_above_0.9"] = v

    with open("a4_results.json", "w") as fh:
        json.dump(res, fh, indent=1)
    print("=" * 92)
    print("Results written to a4_results.json")


if __name__ == "__main__":
    main()
