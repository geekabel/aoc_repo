#!/usr/bin/env python3
"""
Figures and table values of the theory note (task A5, second version).

Run:  python theory/note/code/a5_figures.py      (or: make figures)
Writes the seven figures of the note in ../figs and prints the numbers quoted
in the text and in the tables. Needs the aoc package (pip install -e ".[figures]").

Notation of the note against the notation of the scripts and of the plan:
  delay d = d0 (0.13 s), plateau length L = D0, decay time tau = tau2,
  target alpha* = alpha-bar, age limit Delta* = age where alpha equals the target.
"""
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Rectangle

from aoc.theory import averages as a3
from aoc.theory import rates as a4
from aoc.theory import reliability as a2

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "figs")
BLUE, ORANGE, INK, GREY = "#2a78d6", "#eb6834", "#1a1a1a", "#8a8a86"
LIGHT, PALE = "#d9d9d6", "#efefec"            # neutral fills
DELAY = a2.D0                                # age at reception, 0.13 s
EPS = a2.EPS                                 # tolerance, 1.5 m
WIDTH = 3.45                                 # one IEEE column, in inches
WIDE = 7.1                                   # two IEEE columns, in inches

plt.rcParams.update({
    "font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "legend.fontsize": 7.5, "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42,
})

PAR = {name: v[:4] for name, v in a2.CLASSES.items()}
PED = PAR["pedestrian"]
L_PED, TAU_PED = a2.plateau_surrogate(*PED)


def sigma(age, par=PED):
    return math.sqrt(a2.sigma2(age, *par))


def save(fig, name):
    fig.savefig(os.path.join(OUT, name))
    plt.close(fig)


# ---------------------------------------------------------------------------
# Fig. 1: the situation
# ---------------------------------------------------------------------------
def car(ax, x, y, color, heading):
    """Top view of a car of 4.5 m by 1.9 m centred on (x, y); heading is +1 (right) or -1 (left)."""
    ax.add_patch(FancyBboxPatch((x - 2.25, y - 0.95), 4.5, 1.9, boxstyle="round,pad=0,rounding_size=0.5",
                                fc=color, ec="white", lw=0.6, zorder=4))
    wx = x + heading * 0.55
    ax.add_patch(Rectangle((wx - 0.35, y - 0.7), 0.7, 1.4, fc="white", ec="none", alpha=0.75, zorder=5))


def fig_scenario():
    fig = plt.figure(figsize=(WIDTH, 2.62))
    ax = fig.add_axes([0.0, 0.33, 1.0, 0.67])
    ax.set_xlim(0, 40); ax.set_ylim(-6.6, 11.2); ax.set_aspect("equal"); ax.axis("off")
    ax.add_patch(Rectangle((0, -5.6), 40, 3.0, fc=PALE, ec="none"))                 # sidewalk
    ax.add_patch(Rectangle((0, -2.6), 40, 9.6, fc=LIGHT, ec="none"))                # parking strip and two lanes
    ax.plot([0, 40], [0, 0], color="white", lw=0.8, ls=(0, (1, 2)))
    ax.plot([0, 40], [3.5, 3.5], color="white", lw=1.0, ls=(0, (6, 5)))
    # receiver, truck, pedestrian, sender
    car(ax, 5.2, 1.75, INK, +1)
    ax.add_patch(FancyBboxPatch((13.0, -2.35), 8.0, 2.1, boxstyle="round,pad=0,rounding_size=0.3",
                                fc=GREY, ec="white", lw=0.6, zorder=4))
    ax.add_patch(Rectangle((19.2, -2.35), 0.12, 2.1, fc="white", ec="none", zorder=5))
    ped = (23.6, -3.2)
    car(ax, 33.2, 5.25, BLUE, -1)
    # lines of sight
    ax.plot([7.45, 14.6], [1.75, -0.25], color=INK, lw=0.8, ls=(0, (3, 2)), zorder=3)
    ax.plot([14.6], [-0.25], marker="x", color=INK, ms=4.5, mew=1.1, zorder=6)
    ax.plot([30.95, ped[0] + 0.5], [5.0, ped[1] + 0.55], color=BLUE, lw=0.9, zorder=3)
    ax.add_patch(Circle(ped, 0.62, fc=ORANGE, ec="white", lw=0.7, zorder=6))
    ax.add_patch(FancyArrowPatch((ped[0], ped[1] + 0.8), (ped[0], ped[1] + 3.0), arrowstyle="-|>", mutation_scale=6,
                                 color=ORANGE, lw=1.0, zorder=6))
    # the message
    ax.add_patch(FancyArrowPatch((31.2, 6.6), (6.2, 3.2), connectionstyle="arc3,rad=0.28", arrowstyle="-|>",
                                 mutation_scale=7, color=BLUE, lw=1.1, ls=(0, (1, 1.2)), zorder=3))
    ax.text(19.0, 10.25, "message: “a pedestrian is there”", ha="center", va="center", fontsize=7.5, color=INK)
    ax.text(5.2, -0.15, "receiver", ha="center", va="top", fontsize=7.5, color=INK, zorder=7)
    ax.text(33.2, 3.75, "sender", ha="center", va="top", fontsize=7.5, color=INK, zorder=7)
    ax.text(16.1, -1.3, "parked truck", ha="center", va="center", fontsize=7.2, color="white", zorder=7)
    ax.text(ped[0] + 1.1, ped[1] - 0.2, "pedestrian", ha="left", va="center", fontsize=7.5, color=INK)
    ax.text(9.2, -3.6, "hidden from\nthe receiver", ha="center", va="center", fontsize=7.2, color=INK, linespacing=1.05)
    ax.text(26.9, 1.75, "seen by\nthe sender", ha="right", va="center", fontsize=7.2, color=INK, linespacing=1.05)
    # time line of one object
    tl = fig.add_axes([0.04, 0.0, 0.92, 0.31])
    tl.set_xlim(0, 100); tl.set_ylim(0, 10); tl.axis("off")
    tl.add_patch(FancyArrowPatch((3, 6.4), (98, 6.4), arrowstyle="-|>", mutation_scale=7, color=INK, lw=0.9))
    tl.text(3, 9.2, "Age of the pedestrian's position, seen from the receiver", ha="left", va="center", fontsize=7.5,
            color=INK)
    for x, top, bottom in ((6, "0", "measured\nby the sender"), (36, "0.13 s", "received"),
                           (82, "1.13 s", "still in use if the\nnext message is lost")):
        tl.plot([x, x], [5.5, 7.3], color=INK, lw=0.9)
        tl.text(x, 4.7, top, ha="center", va="top", fontsize=7.5, color=INK)
        tl.text(x, 2.95, bottom, ha="center", va="top", fontsize=7.0, color=INK, linespacing=1.05)
    save(fig, "fig_scenario.pdf")


# ---------------------------------------------------------------------------
# Fig. 2: the four steps (two columns wide)
# ---------------------------------------------------------------------------
def fig_roadmap():
    fig, axes = plt.subplots(1, 4, figsize=(WIDE, 1.55))
    age = np.linspace(0.0, 3.0, 301)
    # Step 1: error against age
    ax = axes[0]
    ax.plot(age, [sigma(a) for a in age], color=BLUE, lw=1.6)
    ax.axhline(EPS, color=GREY, lw=0.6, ls="--")
    ax.text(0.06, EPS + 0.1, "tolerance", fontsize=7.2, color=INK)
    ax.set_xlim(0, 3); ax.set_ylim(0, 2.8); ax.set_xticks([0, 1, 2, 3]); ax.set_yticks([0, 1, 2])
    ax.set_xlabel("age (s)"); ax.set_ylabel("position error (m)")
    ax.set_title("Step 1. The position error\ngrows with the age", fontsize=7.8, loc="left", pad=5)
    # Step 2: reliability against age
    ax = axes[1]
    ax.plot(age, a2.alpha(age, *PED), color=BLUE, lw=1.6)
    ax.set_xlim(0, 3); ax.set_ylim(0, 1.05); ax.set_xticks([0, 1, 2, 3]); ax.set_yticks([0, 0.5, 1])
    ax.set_xlabel("age (s)"); ax.set_ylabel("reliability")
    ax.set_title("Step 2. The object becomes\nless reliable", fontsize=7.8, loc="left", pad=5)
    # Step 3: reliability in time with regular updates
    ax = axes[2]
    T = 1.5
    t = np.linspace(0.0, 6.0, 1201)
    rel = a2.alpha(DELAY + np.mod(t, T), *PED)
    avg = a3.e_exact_periodic(T, 0.0, PED, DELAY)
    ax.plot(t, rel, color=BLUE, lw=1.4)
    ax.axhline(avg, color=INK, lw=0.7, ls="--")
    ax.plot([2.25, 2.25], [0.53, avg], color=GREY, lw=0.5)
    ax.text(2.25, 0.41, "average", fontsize=7.2, color=INK, ha="center")
    for k in range(1, 4):
        ax.annotate("", xy=(k * T, 0.35), xytext=(k * T, 0.17), arrowprops=dict(arrowstyle="-|>", color=INK, lw=0.7,
                    mutation_scale=5))
    ax.text(3.0, 0.04, "an update arrives", fontsize=7.2, color=INK, ha="center")
    ax.set_xlim(0, 6); ax.set_ylim(0, 1.05); ax.set_xticks([0, 2, 4, 6]); ax.set_yticks([0, 0.5, 1])
    ax.set_xlabel("time (s)"); ax.set_ylabel("reliability")
    ax.set_title("Step 3. Each update brings\nthe reliability back up", fontsize=7.8, loc="left", pad=5)
    # Step 4: average against rate, read backwards
    ax = axes[3]
    rates = np.linspace(0.25, 3.0, 111)
    avgs = [a3.e_exact_periodic(1.0 / r, 0.0, PED, DELAY) for r in rates]
    need = 1.0 / a4.t_max_periodic_exact(0.9, 0.0, PED, DELAY)
    ax.plot(rates, avgs, color=BLUE, lw=1.6)
    ax.plot([0, need], [0.9, 0.9], color=INK, lw=0.7, ls="--")
    ax.annotate("", xy=(need, 0.0), xytext=(need, 0.9), arrowprops=dict(arrowstyle="-|>", color=INK, lw=0.7,
                mutation_scale=5, ls="--"))
    ax.text(0.08, 0.93, "target", fontsize=7.2, color=INK)
    ax.text(need + 0.1, 0.1, "rate needed", fontsize=7.2, color=INK)
    ax.set_xlim(0, 3); ax.set_ylim(0, 1.05); ax.set_xticks([0, 1, 2, 3]); ax.set_yticks([0, 0.5, 1])
    ax.set_xlabel("updates per second"); ax.set_ylabel("average reliability")
    ax.set_title("Step 4. A target reliability\nsets the update rate", fontsize=7.8, loc="left", pad=5)
    fig.tight_layout(pad=0.3, w_pad=1.3)
    save(fig, "fig_roadmap.pdf")
    print(f"Fig. 2: step 3 drawn with T = {T} s (average {avg:.2f}), step 4 rate needed without loss {need:.2f} Hz")


# ---------------------------------------------------------------------------
# Fig. 3: clouds of possible positions and the reliability curve
# ---------------------------------------------------------------------------
def cloud(age, n, target_inside):
    """n possible position errors at the given age; the seed is chosen so that the share inside matches alpha."""
    s = sigma(age)
    for seed in range(2000):
        pts = np.random.default_rng(seed).normal(0.0, s, size=(n, 2))
        if int(np.sum(np.hypot(pts[:, 0], pts[:, 1]) <= EPS)) == target_inside and np.abs(pts).max() < 3.9:
            return pts
    raise RuntimeError("no seed found")


def fig_reliability():
    ages = (DELAY, 1.0, 2.0)
    n = 100
    fig = plt.figure(figsize=(WIDTH, 3.35))
    gs = fig.add_gridspec(2, 3, height_ratios=[1.0, 1.55], left=0.125, right=0.985, top=0.94, bottom=0.115,
                          hspace=0.3, wspace=0.06)
    for i, age in enumerate(ages):
        ax = fig.add_subplot(gs[0, i])
        rel = float(a2.alpha(age, *PED))
        pts = cloud(age, n, round(rel * n))
        inside = np.hypot(pts[:, 0], pts[:, 1]) <= EPS
        ax.scatter(pts[inside, 0], pts[inside, 1], s=3.0, color=BLUE, lw=0, zorder=3)
        ax.scatter(pts[~inside, 0], pts[~inside, 1], s=4.2, color=ORANGE, lw=0, zorder=3, marker="D")
        ax.add_patch(Circle((0, 0), EPS, fc="none", ec=INK, lw=0.8, ls=(0, (3, 2)), zorder=4))
        ax.set_xlim(-4.6, 4.6); ax.set_ylim(-5.0, 4.2); ax.set_aspect("equal")
        ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(True); sp.set_linewidth(0.4); sp.set_color(GREY)
        label = f"age {age:.2f} s" if age < 1 else f"age {age:.0f} s"
        ax.set_title(label, fontsize=7.8, pad=3)
        ax.text(0, -4.75, f"{round(rel * 100)} of 100 inside", ha="center", va="bottom", fontsize=7.2, color=INK,
                zorder=6, bbox=dict(fc="white", ec="none", pad=0.8, alpha=0.9))
        if i == 0:
            ax.annotate("tolerance\ncircle, 1.5 m", xy=(EPS * 0.69, EPS * 0.72), xytext=(0.2, 2.3), fontsize=6.8,
                        color=INK, ha="center", va="bottom", linespacing=1.0,
                        arrowprops=dict(arrowstyle="-", color=GREY, lw=0.5, shrinkA=0, shrinkB=0))
    d_half = a2.half_life(*PED)
    tau_exp = a2.tau_from_half_life(d_half)
    x = np.linspace(0.0, 4.0, 801)
    ax = fig.add_subplot(gs[1, :])
    ax.plot(x, np.exp(-x / tau_exp), color=GREY, lw=1.1, ls=":", label="exponential without plateau")
    ax.plot(x, a2.plateau_alpha(x, L_PED, TAU_PED), color=INK, lw=1.0, ls="--", label="simple curve, Equation (3)")
    ax.plot(x, a2.alpha(x, *PED), color=BLUE, lw=1.8, label="exact curve, Equation (2)")
    ax.plot([d_half, d_half], [0, 0.5], color=GREY, lw=0.6)
    ax.plot([0, d_half], [0.5, 0.5], color=GREY, lw=0.6)
    for age in ages:
        ax.plot([age], [float(a2.alpha(age, *PED))], "o", color=BLUE, ms=4.2, mec="white", mew=0.7, zorder=5)
    ax.text(1.0 + 0.09, float(a2.alpha(1.0, *PED)) + 0.015, "0.89", fontsize=7.5, color=INK, va="bottom")
    ax.text(2.0, 0.28, "0.37", fontsize=7.5, color=INK, va="top", ha="center")
    ax.text(d_half - 0.06, 0.03, "half-life\n1.7 s", fontsize=7.2, color=INK, linespacing=1.0, ha="right")
    ax.annotate("", xy=(0.02, 1.075), xytext=(L_PED, 1.075), annotation_clip=False,
                arrowprops=dict(arrowstyle="<->", color=INK, lw=0.6, mutation_scale=5, shrinkA=0, shrinkB=0))
    ax.text(L_PED / 2, 1.1, "plateau", fontsize=7.2, color=INK, ha="center", va="bottom")
    ax.set_xlim(0, 4); ax.set_ylim(0, 1.04)
    ax.set_xlabel("age of the object (s)"); ax.set_ylabel("reliability")
    ax.set_xticks([0, 1, 2, 3, 4]); ax.set_yticks([0, 0.5, 1])
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], labels[::-1], frameon=False, loc="upper right", handlelength=2.4, borderaxespad=0.0,
              bbox_to_anchor=(1.0, 0.99))
    save(fig, "fig_reliability.pdf")
    grid = np.linspace(0, 12 * d_half, 60001)
    exact = a2.alpha(grid, *PED)
    below = float(np.max(exact - np.exp(-grid / tau_exp)))
    above = float(np.max(np.exp(-grid / tau_exp) - exact))
    pla = float(np.max(np.abs(a2.plateau_alpha(grid, L_PED, TAU_PED) - exact)))
    print(f"Fig. 3 pedestrian: half-life {d_half:.2f} s, L {L_PED:.2f} s, tau {TAU_PED:.2f} s; exponential up to "
          f"{below:.2f} below and {above:.2f} above the exact curve; simple curve within {pla:.3f}")
    worst = 0.0
    for par in PAR.values():
        ll, tt = a2.plateau_surrogate(*par)
        g = np.linspace(0, 12 * a2.half_life(*par), 60001)
        worst = max(worst, float(np.max(np.abs(a2.plateau_alpha(g, ll, tt) - a2.alpha(g, *par)))))
    print(f"        simple curve, largest gap over the five objects: {worst:.3f}")


# ---------------------------------------------------------------------------
# Fig. 4: what discounting does to a message
# ---------------------------------------------------------------------------
def fig_discount():
    said = 0.9                                         # mass that the sender gives to "a pedestrian is there"
    rows = [("message of the sender", None)] + [(None, a) for a in (DELAY, 1.0, a2.half_life(*PED), 3.0)]
    fig, ax = plt.subplots(figsize=(WIDTH, 1.64))
    y = len(rows) - 1
    for name, age in rows:
        rel = 1.0 if age is None else float(a2.alpha(age, *PED))
        there = said * rel
        gap = 0.006
        ax.barh(y, there - gap, left=0, height=0.62, color=BLUE, lw=0)
        ax.barh(y, 1 - there - gap, left=there + gap, height=0.62, color=LIGHT, lw=0)
        ax.text(there / 2, y, f"{there:.2f}", ha="center", va="center", fontsize=7.5, color="white")
        ax.text(there + (1 - there) / 2, y, f"{1 - there:.2f}", ha="center", va="center", fontsize=7.5, color=INK)
        if name is None:
            age_txt = f"{age:.2f} s" if age < 1 else f"{age:.1f} s".replace(".0 s", " s")
            name = f"age {age_txt}, " + r"$\alpha$" + f" = {rel:.2f}"
        ax.text(-0.03, y, name, ha="right", va="center", fontsize=7.5, color=INK)
        y -= 1
    ax.axhline(len(rows) - 1.5, color=GREY, lw=0.5, xmin=-0.9, clip_on=False)
    ax.set_xlim(0, 1); ax.set_ylim(-0.5, len(rows) - 0.5)
    ax.set_yticks([]); ax.set_xticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    handles = [Rectangle((0, 0), 1, 1, fc=BLUE), Rectangle((0, 0), 1, 1, fc=LIGHT)]
    ax.legend(handles, ["“a pedestrian is there”", "“I do not know”"], frameon=False, ncol=2,
              loc="lower right", bbox_to_anchor=(1.0, 1.0), borderaxespad=0.1, handlelength=1.1, columnspacing=1.2,
              handletextpad=0.5)
    fig.subplots_adjust(left=0.415, right=0.985, top=0.87, bottom=0.02)
    save(fig, "fig_discount.pdf")
    print("Fig. 4 masses kept for 'a pedestrian is there': " + ", ".join(
        f"age {a:.2f} s: {said * float(a2.alpha(a, *PED)):.2f}" for a in (DELAY, 1.0, a2.half_life(*PED), 3.0)))


# ---------------------------------------------------------------------------
# Fig. 5: age and reliability in time
# ---------------------------------------------------------------------------
def fig_sawtooth():
    target = 0.9
    limit = a2.age_at_level(target, *PED)             # age limit: age where the reliability equals the target
    T = limit - DELAY                                 # Equation (6) with n = 1
    t = np.linspace(0.0, 6.0, 6001)
    recv = [k * T for k in range(0, 9) if k != 4]     # the update sent at 4T is lost
    age = np.array([DELAY + (ti - max(r for r in recv if r <= ti)) for ti in t])
    rel = a2.alpha(age, *PED)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(WIDTH, 2.45), sharex=True, gridspec_kw={"height_ratios": [1, 1.15]})
    ax1.plot(t, age, color=INK, lw=1.0)
    ax1.axhline(limit, color=GREY, lw=0.6, ls="--")
    ax1.text(5.78, limit + 0.07, r"age limit $\Delta^{\star}$", color=INK, fontsize=7.5, ha="right")
    ax1.plot([4 * T], [DELAY], marker="x", color=ORANGE, ms=5, mew=1.3, clip_on=False, zorder=5)
    ax1.annotate("lost update", xy=(4 * T, DELAY + 0.05), xytext=(4 * T - 0.95, 1.86), color=INK, fontsize=7.5,
                 arrowprops=dict(arrowstyle="-", color=GREY, lw=0.6))
    ax1.annotate("update received", xy=(T, 0.62), xytext=(T + 0.12, 1.48), color=INK, fontsize=7.5,
                 ha="center", arrowprops=dict(arrowstyle="-", color=GREY, lw=0.6))
    ax1.set_ylabel("age (s)"); ax1.set_ylim(0, 2.1); ax1.set_yticks([0, 1, 2])
    ax2.plot(t, rel, color=BLUE, lw=1.6)
    ax2.axhline(target, color=GREY, lw=0.6, ls="--")
    ax2.fill_between(t, rel, target, where=rel < target, color=ORANGE, alpha=0.35, lw=0)
    ax2.text(0.05, target - 0.085, r"target $\alpha^{\star}=0.9$", color=INK, fontsize=7.5)
    ax2.text(5 * T + 0.1, 0.66, "below\nthe target", color=INK, fontsize=7.2, ha="left", va="center", linespacing=1.0)
    ax2.plot([4 * T + 0.62, 5 * T + 0.06], [0.72, 0.68], color=GREY, lw=0.6)
    ax2.set_ylabel("reliability"); ax2.set_ylim(0.4, 1.03); ax2.set_yticks([0.5, 0.75, 1.0])
    ax2.set_xlabel("time (s)"); ax2.set_xlim(0, 5.8)
    fig.align_ylabels([ax1, ax2])
    fig.tight_layout(pad=0.3, h_pad=1.5)
    save(fig, "fig_sawtooth.pdf")
    print(f"Fig. 5 pedestrian: age limit {limit:.2f} s, period T {T:.2f} s, lowest reliability after the loss {rel.min():.2f}")


# ---------------------------------------------------------------------------
# Fig. 6: average reliability against the update rate
# ---------------------------------------------------------------------------
def fig_expected():
    p = 0.3
    rates = np.logspace(math.log10(0.2), 1.0, 200)
    marks = np.array([0.2, 0.35, 0.5, 0.7, 1.0, 1.4, 2.0, 3.0, 5.0, 10.0])
    per = [a3.e_plateau_periodic(1 / r, p, L_PED, TAU_PED, DELAY) for r in rates]
    poi = [a3.e_plateau_poisson(r, p, L_PED, TAU_PED, DELAY) for r in rates]
    per_x = [a3.e_exact_periodic(1 / r, p, PED, DELAY) for r in marks]
    poi_x = [a3.e_exact_poisson(r, p, PED, DELAY) for r in marks]
    r_per = 1.0 / a4.t_max_periodic_exact(0.9, p, PED, DELAY)
    r_poi = a4.lam_min_poisson_exact(0.9, p, PED, DELAY)
    fig, ax = plt.subplots(figsize=(WIDTH, 2.0))
    ax.axhline(0.9, color=GREY, lw=0.6, ls="--")
    ax.plot(rates, per, color=BLUE, lw=1.4)
    ax.plot(rates, poi, color=ORANGE, lw=1.4)
    ax.plot(marks, per_x, "o", color=BLUE, ms=3.6, mec="white", mew=0.5, label="regular updates")
    ax.plot(marks, poi_x, "s", color=ORANGE, ms=3.4, mec="white", mew=0.5, label="random updates")
    for r, c in ((r_per, BLUE), (r_poi, ORANGE)):
        ax.plot([r, r], [0, 0.9], color=c, lw=0.6, ls=":")
    ax.text(r_per * 0.93, 0.05, f"{r_per:.1f} Hz", color=INK, fontsize=7.5, ha="right")
    ax.text(r_poi * 1.07, 0.05, f"{r_poi:.1f} Hz", color=INK, fontsize=7.5, ha="left")
    ax.text(0.21, 0.925, r"target $\alpha^{\star}=0.9$", color=INK, fontsize=7.5)
    ax.set_xscale("log"); ax.set_xlim(0.2, 10); ax.set_ylim(0, 1.03)
    ax.set_xticks([0.2, 0.5, 1, 2, 5, 10]); ax.set_xticklabels(["0.2", "0.5", "1", "2", "5", "10"])
    ax.set_yticks([0, 0.5, 1]); ax.minorticks_off()
    ax.set_xlabel("update rate (updates per second, Hz)"); ax.set_ylabel("average reliability")
    ax.legend(frameon=False, loc="lower right", bbox_to_anchor=(1.0, 0.2), borderaxespad=0.3, handletextpad=0.3)
    fig.tight_layout(pad=0.3)
    save(fig, "fig_expected.pdf")
    gap = max(max(abs(a3.e_plateau_periodic(1 / r, p, L_PED, TAU_PED, DELAY) - e) for r, e in zip(marks, per_x)),
              max(abs(a3.e_plateau_poisson(r, p, L_PED, TAU_PED, DELAY) - e) for r, e in zip(marks, poi_x)))
    print(f"Fig. 6 pedestrian, p = {p}: rate for 0.9 regular {r_per:.2f} Hz, random {r_poi:.2f} Hz, largest gap line to marker {gap:.4f}")


# ---------------------------------------------------------------------------
# Fig. 7: rate needed against rate sent by the current rules
# ---------------------------------------------------------------------------
# Rate at which the generation rules of ETSI TS 103 324 include each object when the channel is free
# (CPM generation every 0.1 s): a vehicle at 50 km/h moves 4 m in 0.29 s and is included every 0.3 s;
# pedestrians and cyclists are included every 0.5 s; an object that does not change is included every 1 s.
SENT = {"car (moving)": 1 / 0.3, "truck (moving)": 1 / 0.3, "bicycle": 2.0, "pedestrian": 2.0, "vehicle (parked)": 1.0}
NAMES = {"car (moving)": "Car, moving", "truck (moving)": "Truck, moving", "bicycle": "Bicycle",
         "pedestrian": "Pedestrian", "vehicle (parked)": "Vehicle, parked"}
ORDER = ["car (moving)", "truck (moving)", "bicycle", "pedestrian", "vehicle (parked)"]


def fig_rates():
    need = {k: 1.0 / a4.t_max_periodic_exact(0.9, 0.1, PAR[k], DELAY) for k in ORDER}
    fig, ax = plt.subplots(figsize=(WIDTH, 1.95))
    h = 0.34
    for i, k in enumerate(ORDER):
        y = len(ORDER) - 1 - i
        ax.barh(y + h / 2 + 0.02, need[k], height=h, color=BLUE, lw=0, label="needed for an average of 0.9" if i == 0 else None)
        ax.barh(y - h / 2 - 0.02, SENT[k], height=h, color=LIGHT, lw=0,
                label="sent by the current rules" if i == 0 else None)
        ax.text(need[k] + 0.05, y + h / 2 + 0.02, (f"{need[k]:.1f}" if need[k] >= 0.2 else f"{need[k]:.2f}"), va="center", fontsize=7.5, color=INK)
        ax.text(SENT[k] + 0.05, y - h / 2 - 0.02, f"{SENT[k]:.1f}", va="center", fontsize=7.5, color=INK)
    ax.set_yticks(range(len(ORDER))); ax.set_yticklabels([NAMES[k] for k in ORDER[::-1]])
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, 3.8); ax.set_xticks([0, 1, 2, 3]); ax.set_ylim(-0.6, len(ORDER) - 0.4)
    ax.set_xlabel("updates per second (Hz)")
    ax.legend(frameon=False, loc="lower right", borderaxespad=0.0, handlelength=1.1, handletextpad=0.5,
              labelspacing=0.35)
    fig.tight_layout(pad=0.3)
    save(fig, "fig_rates.pdf")
    print("Fig. 7 needed (regular, 10% loss, average target 0.9) and sent: " + ", ".join(
        f"{NAMES[k]} {need[k]:.2f} / {SENT[k]:.2f}" for k in ORDER))


# ---------------------------------------------------------------------------
# Numbers quoted in the text and in the tables
# ---------------------------------------------------------------------------
def numbers():
    sx, sv, _, q = PED
    print("Running example (pedestrian): sigma_x 0.30 m, sigma_v 0.50 m/s, q 0.50")
    for age in (0.5, 1.0, 2.0):
        parts = (sx ** 2, (age * sv) ** 2, q * age ** 3 / 3)
        print(f"  age {age:.1f} s: squares {parts[0]:.2f} + {parts[1]:.2f} + {parts[2]:.2f} = {sum(parts):.2f} m2, "
              f"sigma {sigma(age):.2f} m, reliability {float(a2.alpha(age, *PED)):.3f}")
    print(f"  reliability when sigma equals the tolerance: {1 - math.exp(-0.5):.2f}")
    for T in (1.0, 2.0):
        row = [a3.e_plateau_periodic(T, p, L_PED, TAU_PED, DELAY) for p in (0.0, 0.1, 0.3)]
        fall = TAU_PED * (1 - math.exp(-(T - (L_PED - DELAY)) / TAU_PED))
        print(f"  regular, T = {T:.0f} s: plateau time {L_PED - DELAY:.2f} s, falling area {fall:.2f}; average for a loss of "
              f"0, 0.1, 0.3: " + ", ".join(f"{v:.2f}" for v in row)
              + f"; random updates, loss 0.1: {a3.e_plateau_poisson(1 / T, 0.1, L_PED, TAU_PED, DELAY):.2f}")
    T09 = a4.t_max_periodic_exact(0.9, 0.1, PED, DELAY)
    print(f"  average target 0.9, loss 0.1: period {T09:.2f} s, time above 0.9: "
          f"{a4.time_above_target(0.9, PED, 'periodic', 1 / T09, 0.1, DELAY):.2f}")
    print("Table II: object, sigma_a, tau_m, q, half-life, L, tau")
    for name, (sx, sv, sxv, q, sa, tm) in a2.CLASSES.items():
        ll, tt = a2.plateau_surrogate(sx, sv, sxv, q)
        print(f"  {name:18s} {sa} {tm} {q:.3f} {a2.half_life(sx, sv, sxv, q):.2f} {ll:.2f} {tt:.2f}")
    print("Table III: object, regular with a loss of 0, 0.1, 0.3, random with a loss of 0.1, Equation (6) with n = 1 "
          "(Hz, exact curve, target 0.9), then the age limit (s)")
    for name in PAR:
        row = [1.0 / a4.t_max_periodic_exact(0.9, p, PAR[name], DELAY) for p in (0.0, 0.1, 0.3)]
        row.append(a4.lam_min_poisson_exact(0.9, 0.1, PAR[name], DELAY))
        row.append(1.0 / a4.t_floor(0.9, PAR[name], DELAY))
        print(f"  {name:18s} " + " ".join(f"{v:.2f}" for v in row) + f"   {a2.age_at_level(0.9, *PAR[name]):.2f}")
    print("Regular updates with a loss of 0.1, average reliability at 2 Hz and 1 Hz (exact curve):")
    for name in PAR:
        print(f"  {name:18s} " + " ".join(f"{a3.e_exact_periodic(1 / r, 0.1, PAR[name], DELAY):.3f}" for r in (2.0, 1.0)))


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    fig_scenario(); fig_roadmap(); fig_reliability(); fig_discount(); fig_sawtooth(); fig_expected(); fig_rates()
    numbers()
