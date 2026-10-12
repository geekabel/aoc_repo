"""Tests of the theory package (tasks A2 to A4).

The reference numbers are the ones printed in the theory note (theory/note), Tables II and III.
If a test of this file fails after a change, the note no longer agrees with the code.
"""
import math

import numpy as np
import pytest

from aoc.theory import averages, rates, reliability

DELAY = reliability.D0                       # age at reception, 0.13 s
PAR = {name: values[:4] for name, values in reliability.CLASSES.items()}
PED = PAR["pedestrian"]

# Table II of the note: half-life, plateau length L and decay time tau, in seconds
TABLE_II = {
    "car (moving)": (0.99, 0.54, 0.64),
    "truck (moving)": (1.32, 0.70, 0.89),
    "bicycle": (1.45, 0.76, 1.00),
    "pedestrian": (1.70, 0.85, 1.22),
    "vehicle (parked)": (13.88, 5.79, 11.67),
}

# Table III of the note: updates per second needed for an average reliability of 0.9.
# Regular updates with a loss of 0, 0.1 and 0.3; random updates with a loss of 0.1; the rule with n = 1.
TABLE_III = {
    "car (moving)": (1.32, 1.63, 2.67, 3.36, 2.08),
    "truck (moving)": (0.96, 1.18, 1.93, 2.43, 1.50),
    "bicycle": (0.86, 1.07, 1.74, 2.19, 1.36),
    "pedestrian": (0.74, 0.91, 1.49, 1.87, 1.17),
    "vehicle (parked)": (0.09, 0.11, 0.19, 0.23, 0.15),
}


# ----------------------------------------------------------------------------
# The reliability curve
# ----------------------------------------------------------------------------
def test_variance_formula():
    # sigma^2(age) = sx^2 + (age * sv)^2 + q * age^3 / 3, without position-speed correlation
    assert float(reliability.sigma2(2.0, 0.3, 0.5, 0.0, 0.5)) == pytest.approx(0.09 + 1.0 + 0.5 * 8.0 / 3.0)


@pytest.mark.parametrize("name", list(PAR))
def test_curve_starts_high_and_falls(name):
    ages = np.linspace(0.0, 30.0, 601)
    curve = reliability.alpha(ages, *PAR[name])
    assert curve[0] > 0.98
    assert np.all(np.diff(curve) < 0.0)
    assert curve[-1] < 0.5


@pytest.mark.parametrize("name", list(PAR))
def test_half_life_is_where_the_curve_crosses_one_half(name):
    half = reliability.half_life(*PAR[name])
    assert float(reliability.alpha(half, *PAR[name])) == pytest.approx(0.5, abs=1e-9)
    assert reliability.age_at_level(0.5, *PAR[name]) == pytest.approx(half, abs=1e-9)


@pytest.mark.parametrize("name", list(PAR))
def test_table_ii_of_the_note(name):
    half, plateau, decay = TABLE_II[name]
    length, tau = reliability.plateau_surrogate(*PAR[name])
    assert reliability.half_life(*PAR[name]) == pytest.approx(half, abs=0.005)
    assert length == pytest.approx(plateau, abs=0.005)
    assert tau == pytest.approx(decay, abs=0.005)


def test_pedestrian_age_limit_for_a_target_of_0_9():
    assert reliability.age_at_level(0.9, *PED) == pytest.approx(0.98, abs=0.005)


@pytest.mark.parametrize("name", list(PAR))
def test_simple_curve_stays_close_to_the_exact_curve(name):
    length, tau = reliability.plateau_surrogate(*PAR[name])
    # the simple curve goes through the two points where the exact curve equals 0.9 and 0.5
    for level in (0.9, 0.5):
        age = reliability.age_at_level(level, *PAR[name])
        assert float(reliability.plateau_alpha(age, length, tau)) == pytest.approx(level, abs=1e-9)
    ages = np.linspace(0.0, 60.0, 12001)
    gap = np.abs(reliability.alpha(ages, *PAR[name]) - reliability.plateau_alpha(ages, length, tau))
    assert float(gap.max()) < 0.06


def test_curve_against_simulated_motion():
    ages = [0.5, 1.0, 2.0]
    simulated = reliability.monte_carlo_alpha(*PED, reliability.EPS, ages, n=20_000)
    assert np.allclose(simulated, reliability.alpha(np.array(ages), *PED), atol=0.015)


# ----------------------------------------------------------------------------
# The average reliability
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("kind,rate,loss", [("poisson", 2.0, 0.1), ("poisson", 1.0, 0.3),
                                            ("periodic", 2.0, 0.1), ("periodic", 1.0, 0.3)])
def test_average_formulas_against_a_simulated_update_process(kind, rate, loss):
    length, tau = reliability.plateau_surrogate(*PED)
    waits = averages.simulate_ages(kind, rate, loss, n_updates=40_000, n_obs=100_000)
    simulated = float(np.mean(reliability.plateau_alpha(DELAY + waits, length, tau)))
    if kind == "poisson":
        formula = averages.e_plateau_poisson(rate, loss, length, tau, DELAY)
        mean_wait = averages.mean_age_poisson(rate, loss)
    else:
        formula = averages.e_plateau_periodic(1.0 / rate, loss, length, tau, DELAY)
        mean_wait = averages.mean_age_periodic(1.0 / rate, loss)
    assert simulated == pytest.approx(formula, abs=0.005)
    assert float(waits.mean()) == pytest.approx(mean_wait, rel=0.02)


@pytest.mark.parametrize("name", list(PAR))
def test_average_formulas_against_the_exact_curve(name):
    # grid of experiment E2: 1 to 10 updates per second, loss of 0 to 0.5
    length, tau = reliability.plateau_surrogate(*PAR[name])
    for rate in averages.RATES:
        for loss in averages.LOSSES:
            random_gap = abs(averages.e_plateau_poisson(rate, loss, length, tau, DELAY)
                             - averages.e_exact_poisson(rate, loss, PAR[name], DELAY))
            regular_gap = abs(averages.e_plateau_periodic(1.0 / rate, loss, length, tau, DELAY)
                              - averages.e_exact_periodic(1.0 / rate, loss, PAR[name], DELAY))
            assert random_gap < 0.02 and regular_gap < 0.02


def test_regular_updates_without_loss_reduce_to_the_short_formula():
    # alpha_avg = [(L - d) + tau * (1 - exp(-(T - (L - d)) / tau))] / T, for a period T longer than L - d
    length, tau = reliability.plateau_surrogate(*PED)
    period = 2.0
    left = length - DELAY
    short = (left + tau * (1.0 - math.exp(-(period - left) / tau))) / period
    assert averages.e_plateau_periodic(period, 0.0, length, tau, DELAY) == pytest.approx(short, abs=1e-12)


# ----------------------------------------------------------------------------
# The needed rates
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", list(PAR))
def test_table_iii_of_the_note(name):
    par = PAR[name]
    got = [1.0 / rates.t_max_periodic_exact(0.9, loss, par, DELAY) for loss in (0.0, 0.1, 0.3)]
    got.append(rates.lam_min_poisson_exact(0.9, 0.1, par, DELAY))
    got.append(1.0 / rates.t_floor(0.9, par, DELAY))
    assert got == pytest.approx(TABLE_III[name], abs=0.006)


def test_regular_updates_need_about_half_the_rate_of_random_updates():
    # ratio of the needed rates, pedestrian, target 0.9: 0.44 without loss, 0.49 at 10 %, 0.62 at 30 %
    for loss, expected in ((0.0, 0.44), (0.1, 0.49), (0.3, 0.62)):
        regular = 1.0 / rates.t_max_periodic_exact(0.9, loss, PED, DELAY)
        random = rates.lam_min_poisson_exact(0.9, loss, PED, DELAY)
        assert regular / random == pytest.approx(expected, abs=0.005)


@pytest.mark.parametrize("target", rates.TARGETS)
def test_needed_rate_put_back_into_the_average_gives_the_target(target):
    length, tau = reliability.plateau_surrogate(*PED)
    mu = rates.mu_min_poisson(target, length, tau, DELAY)
    assert averages.e_plateau_poisson(mu, 0.0, length, tau, DELAY) == pytest.approx(target, abs=1e-9)
    period = rates.t_max_periodic_no_loss(target, length, tau, DELAY)
    assert averages.e_plateau_periodic(period, 0.0, length, tau, DELAY) == pytest.approx(target, abs=1e-9)
    assert rates.t_max_periodic(target, 0.0, length, tau, DELAY) == pytest.approx(period, abs=1e-6)
    for loss in (0.1, 0.3):
        period = rates.t_max_periodic(target, loss, length, tau, DELAY)
        assert averages.e_plateau_periodic(period, loss, length, tau, DELAY) == pytest.approx(target, abs=1e-6)


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("loss", [0.1, 0.3])
def test_rule_keeps_the_reliability_above_the_target_for_a_known_share_of_time(n, loss):
    # period T = (age limit - delay) / n  ->  the reliability is above the target for a share 1 - loss^n of the time
    period = (reliability.age_at_level(0.9, *PED) - DELAY) / n
    share = rates.time_above_target(0.9, PED, "periodic", 1.0 / period, loss, DELAY)
    assert share == pytest.approx(1.0 - loss**n, abs=1e-9)


def test_dcc_table_is_ordered():
    limits = [rate for _, rate in rates.DCC_STATES]
    assert limits == sorted(limits, reverse=True)
    assert limits[0] == 10.0 and limits[-1] == 1.0
