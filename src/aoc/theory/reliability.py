#!/usr/bin/env python3
"""
A2 of the AoC plan: the discount law alpha(Delta) and the half-life per class.

What the script does
  1. Closed forms: sigma^2(Delta), alpha(Delta), half-life, tau.
  2. Monte Carlo check of steps 1 and 2 (simulated motion, no formula reused).
  3. Half-life table per class, with the delay offset d0.
  4. Sensitivity: epsilon in {1, 1.5, 2} m, q x0.5 and x2.
  5. Non-isotropic error: bounds with the smallest and largest eigenvalue.
  6. Error of the exponential surrogate exp(-Delta/tau).
  7. Maneuver check: Singer acceleration model against the white-noise model.

Run:  python -m aoc.theory.reliability            (about 1 minute)
      python -m aoc.theory.reliability --plot     (also writes a2_alpha_per_class.png)
Needs numpy and scipy. matplotlib only for --plot.
The run writes a2_results.json in the current folder (see --json).

Notation
  Delta   age of the object: now minus measurement time at the sender [s]
  alpha   reliability in [0, 1]. The discount RATE of Shafer is 1 - alpha.
  eps     task tolerance on the position error [m]
  sx, sv  std of position [m] and velocity [m/s] at Delta = 0, per axis
  sxv     position-velocity covariance at Delta = 0, per axis [m^2/s]
  q       intensity of the white acceleration noise [m^2/s^3]
"""
import argparse
import json
import math

import numpy as np
from scipy import integrate, optimize, special

LN2 = math.log(2.0)

# ----------------------------------------------------------------------------
# Working values per class. They are ILLUSTRATIVE, not calibrated.
# q follows q = 2 * sigma_a^2 * tau_m (sigma_a: std of the maneuver
# acceleration, tau_m: its correlation time). Replace with fitted values.
# ----------------------------------------------------------------------------
def q_from_maneuver(sigma_a, tau_m):
    """White-noise intensity that matches a Singer maneuver at long horizons."""
    return 2.0 * sigma_a**2 * tau_m


CLASSES = {
    #  name               sx    sv    sxv   q                            sigma_a  tau_m
    "pedestrian":        (0.30, 0.50, 0.0, q_from_maneuver(0.5, 1.0),    0.5,     1.0),
    "bicycle":           (0.30, 0.50, 0.0, q_from_maneuver(0.7, 1.0),    0.7,     1.0),
    "truck (moving)":    (0.30, 0.50, 0.0, q_from_maneuver(0.6, 2.0),    0.6,     2.0),
    "car (moving)":      (0.30, 0.50, 0.0, q_from_maneuver(1.0, 2.0),    1.0,     2.0),
    "vehicle (parked)":  (0.50, 0.05, 0.0, 0.001,                        None,    None),
}
EPS = 1.5          # m, half a lane
D0 = 0.13          # s, age at reception (Volk et al. 2021: 119 to 133 ms end to end)


# ----------------------------------------------------------------------------
# Closed forms (Plan, Methodology 2, steps 1 and 2)
# ----------------------------------------------------------------------------
def sigma2(delta, sx, sv, sxv, q):
    """Per-axis variance of the predicted position after age delta."""
    delta = np.asarray(delta, dtype=float)
    return sx**2 + 2.0 * delta * sxv + delta**2 * sv**2 + q * delta**3 / 3.0


def alpha(delta, sx, sv, sxv, q, eps=EPS):
    """P(position error <= eps) for a 2D isotropic Gaussian error."""
    return 1.0 - np.exp(-eps**2 / (2.0 * sigma2(delta, sx, sv, sxv, q)))


def half_life(sx, sv, sxv, q, eps=EPS):
    """Age at which alpha = 0.5. Returns nan if alpha(0) <= 0.5."""
    target = eps**2 / (2.0 * LN2)
    if sx**2 >= target:
        return float("nan")
    f = lambda d: float(sigma2(d, sx, sv, sxv, q)) - target
    hi = 1.0
    while f(hi) < 0.0:
        hi *= 2.0
    return optimize.brentq(f, 0.0, hi, xtol=1e-12)


def tau_from_half_life(d_half):
    return d_half / LN2


# ----------------------------------------------------------------------------
# Monte Carlo: simulate the motion, do not reuse the formulas
# ----------------------------------------------------------------------------
def monte_carlo_alpha(sx, sv, sxv, q, eps, ages, n=200_000, dt=0.01, seed=1):
    """
    Truth: 2D nearly-constant-velocity motion, dv = sqrt(q) dW, dx = v dt.
    Receiver: holds an estimate with error covariance [[sx^2, sxv], [sxv, sv^2]]
    per axis at age 0 and predicts with constant velocity.
    Returns the empirical P(|error| <= eps) at each age.
    """
    rng = np.random.default_rng(seed)
    cov0 = np.array([[sx**2, sxv], [sxv, sv**2]])
    chol = np.linalg.cholesky(cov0 + 1e-15 * np.eye(2))
    ages = np.asarray(ages, dtype=float)
    steps = np.round(ages / dt).astype(int)
    out = np.empty(len(ages))
    # error state per axis: e_x (position error), e_v (velocity error)
    z = rng.standard_normal((2, n, 2)) @ chol.T          # axis, sample, (x, v)
    ex = z[:, :, 0].copy()
    ev = z[:, :, 1].copy()
    k = 0
    for i, s in enumerate(steps):
        while k < s:
            dw = rng.standard_normal((2, n)) * math.sqrt(q * dt)
            ex += ev * dt + 0.5 * dw * dt     # semi-implicit step, exact in the mean
            ev += dw
            k += 1
        out[i] = np.mean(ex[0] ** 2 + ex[1] ** 2 <= eps**2)
    return out


def monte_carlo_anisotropic(l1, l2, eps, n=2_000_000, seed=2):
    rng = np.random.default_rng(seed)
    e = rng.standard_normal((n, 2)) * np.sqrt([l1, l2])
    return float(np.mean(np.sum(e**2, axis=1) <= eps**2))


# ----------------------------------------------------------------------------
# Surrogate error
# ----------------------------------------------------------------------------
def surrogate_error(sx, sv, sxv, q, eps=EPS):
    d_half = half_life(sx, sv, sxv, q, eps)
    tau = tau_from_half_life(d_half)
    grid = np.linspace(0.0, 12.0 * d_half, 60001)
    diff = np.exp(-grid / tau) - alpha(grid, sx, sv, sxv, q, eps)
    i_lo, i_hi = int(np.argmin(diff)), int(np.argmax(diff))
    area = integrate.quad(lambda d: float(alpha(d, sx, sv, sxv, q, eps)), 0, np.inf, limit=400)[0]
    return {
        "max_under": float(diff[i_lo]), "at_under": float(grid[i_lo] / d_half),
        "max_over": float(diff[i_hi]), "at_over": float(grid[i_hi] / d_half),
        "tau_half": tau, "tau_area": area,
    }


def expected_alpha_poisson(mu, sx, sv, sxv, q, eps=EPS, d0=0.0):
    """E[alpha(d0 + A)] for an exponential age A with rate mu (exact, numerical)."""
    f = lambda a: mu * math.exp(-mu * a) * float(alpha(d0 + a, sx, sv, sxv, q, eps))
    return integrate.quad(f, 0, np.inf, limit=400)[0]


def rate_for_target(target, sx, sv, sxv, q, eps=EPS, d0=0.0):
    """Smallest update rate mu with E[alpha] >= target (exact law, numerical)."""
    if float(alpha(d0, sx, sv, sxv, q, eps)) <= target:
        return float("inf")
    g = lambda mu: expected_alpha_poisson(mu, sx, sv, sxv, q, eps, d0) - target
    lo, hi = 1e-4, 1.0
    while g(hi) < 0:
        hi *= 2.0
    return optimize.brentq(g, lo, hi, xtol=1e-9)


def age_at_level(level, sx, sv, sxv, q, eps=EPS):
    """Age at which alpha falls to the given level."""
    target = eps**2 / (2.0 * math.log(1.0 / (1.0 - level)))
    f = lambda d: float(sigma2(d, sx, sv, sxv, q)) - target
    if f(0.0) >= 0.0:
        return 0.0
    hi = 1.0
    while f(hi) < 0.0:
        hi *= 2.0
    return optimize.brentq(f, 0.0, hi, xtol=1e-12)


def plateau_surrogate(sx, sv, sxv, q, eps=EPS):
    """
    Two-parameter surrogate: alpha = 1 up to age D0, then exp(-(age - D0) / tau2).
    Fitted on two points of the exact law: alpha = 0.9 and alpha = 0.5.
    Its Poisson average is closed form: E = 1 - exp(-mu D0) / (mu tau2 + 1).
    """
    d9 = age_at_level(0.9, sx, sv, sxv, q, eps)
    d5 = age_at_level(0.5, sx, sv, sxv, q, eps)
    tau2 = (d5 - d9) / (LN2 - math.log(1.0 / 0.9))
    dd0 = max(0.0, d9 - math.log(1.0 / 0.9) * tau2)
    return dd0, tau2


def plateau_alpha(delta, dd0, tau2):
    delta = np.asarray(delta, dtype=float)
    return np.where(delta <= dd0, 1.0, np.exp(-(delta - dd0) / tau2))


def plateau_rate_closed_form(target, dd0, tau2):
    """Inverse of E = 1 - exp(-mu D0) / (mu tau2 + 1), with the Lambert W function."""
    if dd0 <= 0.0:
        return target / (1.0 - target) / tau2
    x = dd0 / tau2
    u = special.lambertw(x * math.exp(x) / (1.0 - target)).real / x
    return (u - 1.0) / tau2


def plateau_rate_for_target(target, dd0, tau2):
    g = lambda mu: 1.0 - math.exp(-mu * dd0) / (mu * tau2 + 1.0) - target
    hi = 1.0
    while g(hi) < 0:
        hi *= 2.0
    return optimize.brentq(g, 1e-6, hi, xtol=1e-9)


# ----------------------------------------------------------------------------
# Singer maneuver model (exact position variance)
# ----------------------------------------------------------------------------
def singer_pos_var(t, sigma_a, tau_m):
    """Variance of the position after t caused by a Singer acceleration."""
    b = 1.0 / tau_m
    bt = b * t
    return sigma_a**2 / b**4 * (1 - math.exp(-2 * bt) + 2 * bt + 2 * bt**3 / 3
                                 - 2 * bt**2 - 4 * bt * math.exp(-bt))


def half_life_singer(sx, sv, sigma_a, tau_m, eps=EPS):
    target = eps**2 / (2.0 * LN2)
    f = lambda d: sx**2 + d**2 * sv**2 + singer_pos_var(d, sigma_a, tau_m) - target
    hi = 1.0
    while f(hi) < 0:
        hi *= 2.0
    return optimize.brentq(f, 1e-9, hi)


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plot", action="store_true", help="write a2_alpha_per_class.png")
    ap.add_argument("--json", default="a2_results.json")
    args = ap.parse_args()
    res = {"eps": EPS, "d0": D0, "classes": {}}

    print("=" * 78)
    print("1. Plan example: pedestrian, sv = 0.5 m/s, q = 0.5, eps = 1.5 m, sx = 0")
    d = half_life(0.0, 0.5, 0.0, 0.5)
    t = tau_from_half_life(d)
    lam = 0.9 / (1 - 0.9) / ((1 - 0.1) * t)
    print(f"   half-life = {d:.3f} s, tau = {t:.3f} s, lambda_min(0.9, p=0.1) = {lam:.2f} Hz")
    print("   Plan says: about 1.7 s, about 2.5 s, 4.0 Hz")
    res["plan_example"] = {"half_life": d, "tau": t, "lambda_min": lam}

    print("=" * 78)
    print("2. Monte Carlo check of alpha(Delta) (200 000 simulated objects per class)")
    worst = 0.0
    mc_store = {}
    for name, (sx, sv, sxv, q, _, _) in CLASSES.items():
        d_half = half_life(sx, sv, sxv, q)
        ages = np.round(np.array([0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]) * d_half, 2)
        th = alpha(ages, sx, sv, sxv, q)
        mc = monte_carlo_alpha(sx, sv, sxv, q, EPS, ages)
        err = float(np.max(np.abs(th - mc)))
        worst = max(worst, err)
        mc_store[name] = {"ages": ages.tolist(), "theory": th.tolist(), "mc": mc.tolist()}
        print(f"   {name:18s} max |theory - simulation| = {err:.4f}")
    # with a non-zero position-velocity covariance
    for sxv in (0.10, -0.10):
        ages = np.array([0.5, 1.0, 1.5, 2.5])
        th = alpha(ages, 0.3, 0.5, sxv, 0.5)
        mc = monte_carlo_alpha(0.3, 0.5, sxv, 0.5, EPS, ages, seed=5)
        err = float(np.max(np.abs(th - mc)))
        worst = max(worst, err)
        print(f"   pedestrian, sxv = {sxv:+.2f}   max |theory - simulation| = {err:.4f}")
    print(f"   worst gap over all cases: {worst:.4f} (Monte Carlo noise is about 0.002)")
    res["mc_worst_gap"] = worst
    res["mc"] = mc_store

    print("=" * 78)
    print(f"3. Half-life per class (eps = {EPS} m, d0 = {D0} s)")
    print(f"   {'class':18s} {'sx':>5} {'sv':>5} {'q':>6} {'alpha(0)':>9} {'half-life':>10} {'tau':>7}"
          f" {'after rx':>9} {'regime':>10}")
    for name, (sx, sv, sxv, q, sa, tm) in CLASSES.items():
        d_half = half_life(sx, sv, sxv, q)
        tau = tau_from_half_life(d_half)
        a0 = float(alpha(0.0, sx, sv, sxv, q))
        vel = d_half**2 * sv**2
        acc = q * d_half**3 / 3.0
        regime = "velocity" if vel > 2 * acc else ("accel." if acc > 2 * vel else "mixed")
        print(f"   {name:18s} {sx:5.2f} {sv:5.2f} {q:6.3f} {a0:9.4f} {d_half:9.2f}s {tau:6.2f}s"
              f" {d_half - D0:8.2f}s {regime:>10}")
        res["classes"][name] = {"sx": sx, "sv": sv, "sxv": sxv, "q": q, "alpha0": a0,
                                "half_life": d_half, "tau": tau,
                                "half_life_after_rx": d_half - D0, "regime": regime}

    print("=" * 78)
    print("4. Sensitivity of the half-life [s]")
    print(f"   {'class':18s} {'eps=1.0':>8} {'eps=1.5':>8} {'eps=2.0':>8} | {'q x0.5':>8} {'q x1':>8} {'q x2':>8}")
    for name, (sx, sv, sxv, q, _, _) in CLASSES.items():
        row_e = [half_life(sx, sv, sxv, q, e) for e in (1.0, 1.5, 2.0)]
        row_q = [half_life(sx, sv, sxv, q * m) for m in (0.5, 1.0, 2.0)]
        print(f"   {name:18s} " + " ".join(f"{v:8.2f}" for v in row_e) + " | "
              + " ".join(f"{v:8.2f}" for v in row_q))
        res["classes"][name]["half_life_eps"] = dict(zip(["1.0", "1.5", "2.0"], row_e))
        res["classes"][name]["half_life_q"] = dict(zip(["x0.5", "x1", "x2"], row_q))

    print("=" * 78)
    print("5. Non-isotropic error: bounds with the eigenvalues of the covariance")
    print("   lower bound uses the largest eigenvalue, upper bound the smallest")
    res["anisotropic"] = []
    for l1, l2 in ((1.0, 0.25), (2.0, 0.5), (4.0, 0.25), (1.0, 1.0)):
        lo = 1 - math.exp(-EPS**2 / (2 * l1))
        hi = 1 - math.exp(-EPS**2 / (2 * l2))
        mc = monte_carlo_anisotropic(l1, l2, EPS)
        ok = lo - 0.002 <= mc <= hi + 0.002
        print(f"   variances ({l1:.2f}, {l2:.2f}) m^2: {lo:.4f} <= simulated {mc:.4f} <= {hi:.4f}   {'OK' if ok else 'VIOLATED'}")
        res["anisotropic"].append({"l1": l1, "l2": l2, "lower": lo, "mc": mc, "upper": hi, "ok": ok})

    print("=" * 78)
    print("6. Exponential surrogate exp(-Delta/tau), tau = half-life / ln 2")
    print(f"   {'class':18s} {'most pessimistic':>22} {'most optimistic':>22} {'tau':>7} {'area':>7}")
    for name, (sx, sv, sxv, q, _, _) in CLASSES.items():
        s = surrogate_error(sx, sv, sxv, q)
        print(f"   {name:18s} {s['max_under']:+7.3f} at {s['at_under']:4.2f} half-l."
              f" {s['max_over']:+7.3f} at {s['at_over']:4.2f} half-l. {s['tau_half']:6.2f}s {s['tau_area']:6.2f}s")
        res["classes"][name]["surrogate"] = s
    print("   Effect on the Poisson average E[alpha] and on the rate for a 0.9 target (p = 0):")
    print(f"   {'class':18s} {'rate, surrogate':>16} {'rate, exact law':>16} {'ratio':>6} {'exact E[alpha] at surrogate rate':>34}")
    for name, (sx, sv, sxv, q, _, _) in CLASSES.items():
        tau = tau_from_half_life(half_life(sx, sv, sxv, q))
        mu_s = 9.0 / tau
        mu_e = rate_for_target(0.9, sx, sv, sxv, q)
        ea = expected_alpha_poisson(mu_s, sx, sv, sxv, q)
        print(f"   {name:18s} {mu_s:13.2f} Hz {mu_e:13.2f} Hz {mu_s / mu_e:6.2f} {ea:34.3f}")
        res["classes"][name]["rate_target_0.9"] = {"surrogate": mu_s, "exact": mu_e, "exact_Ealpha_at_surrogate_rate": ea}

    print("   Option: plateau surrogate, alpha = 1 up to D0, then exp(-(age - D0) / tau2)")
    print(f"   {'class':18s} {'D0':>7} {'tau2':>7} {'max |error|':>12} {'rate for 0.9':>13} {'ratio to exact':>15}")
    for name, (sx, sv, sxv, q, _, _) in CLASSES.items():
        dd0, tau2 = plateau_surrogate(sx, sv, sxv, q)
        d_half = half_life(sx, sv, sxv, q)
        grid = np.linspace(0.0, 12.0 * d_half, 60001)
        err = float(np.max(np.abs(plateau_alpha(grid, dd0, tau2) - alpha(grid, sx, sv, sxv, q))))
        mu_p = plateau_rate_for_target(0.9, dd0, tau2)
        mu_e = res["classes"][name]["rate_target_0.9"]["exact"]
        mu_w = plateau_rate_closed_form(0.9, dd0, tau2)
        assert abs(mu_w - mu_p) < 1e-6 * max(1.0, mu_p), "Lambert W inverse does not match the root search"
        print(f"   {name:18s} {dd0:6.2f}s {tau2:6.2f}s {err:12.3f} {mu_p:10.2f} Hz {mu_p / mu_e:15.2f}")
        res["classes"][name]["plateau"] = {"D0": dd0, "tau2": tau2, "max_abs_error": err,
                                           "rate_target_0.9": mu_p, "ratio_to_exact": mu_p / mu_e}

    print("   The Lambert W inverse of the plateau formula matches the root search for all classes.")

    print("=" * 78)
    print("7. Maneuver check: Singer model against white noise with q = 2 sigma_a^2 tau_m")
    print(f"   {'class':18s} {'half-life white':>16} {'half-life Singer':>17}")
    for name, (sx, sv, sxv, q, sa, tm) in CLASSES.items():
        if sa is None:
            continue
        hw = half_life(sx, sv, 0.0, q_from_maneuver(sa, tm))
        hs = half_life_singer(sx, sv, sa, tm)
        print(f"   {name:18s} {hw:15.2f}s {hs:16.2f}s   white-noise model is {'conservative' if hw <= hs else 'OPTIMISTIC'}")
        res["classes"][name]["singer"] = {"half_life_white": hw, "half_life_singer": hs}
    a_max = 2.0
    print(f"   Deterministic maneuver (constant {a_max} m/s^2, for example a turn): error = a t^2 / 2,")
    print(f"   it reaches eps = {EPS} m after {math.sqrt(2 * EPS / a_max):.2f} s. No zero-mean model covers that.")

    with open(args.json, "w") as fh:
        json.dump(res, fh, indent=1)
    print("=" * 78)
    print(f"Results written to {args.json}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        colors = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7"]   # Okabe-Ito
        fig, ax = plt.subplots(figsize=(7.0, 4.0))
        for (name, (sx, sv, sxv, q, _, _)), c in zip(CLASSES.items(), colors):
            d_half = half_life(sx, sv, sxv, q)
            grid = np.linspace(0.0, 6.0, 601)
            ax.plot(grid, alpha(grid, sx, sv, sxv, q), color=c, lw=2, label=name)
            ax.plot(grid, np.exp(-grid * LN2 / d_half), color=c, lw=1, ls="--")
            m = res["mc"][name]
            keep = [i for i, a in enumerate(m["ages"]) if a <= 6.0]
            ax.plot([m["ages"][i] for i in keep], [m["mc"][i] for i in keep], "o", color=c, ms=4)
            if d_half <= 6.0:
                ax.plot([d_half], [0.5], "s", color=c, ms=6, mfc="white")
        ax.axhline(0.5, color="0.6", lw=0.8)
        ax.set_xlabel("age Delta (s)")
        ax.set_ylabel("alpha(Delta)")
        ax.set_xlim(0, 6)
        ax.set_ylim(0, 1.02)
        ax.set_title("Discount law per class: exact (solid), surrogate (dashed), simulation (dots)", fontsize=9)
        ax.legend(frameon=False, fontsize=8)
        fig.tight_layout()
        fig.savefig("a2_alpha_per_class.png", dpi=200)
        print("Figure written to a2_alpha_per_class.png")


if __name__ == "__main__":
    main()
