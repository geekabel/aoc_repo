# The kinematic fusion: one place and one speed per real object

Task B7. After the association (B3), the objects of a group are one real object. Each of them has a state at the
time of the tick (x, y, vx, vy) and a covariance, from the alignment. This module gives one state and one
covariance per group. The module is `aoc.fusion.kinematics`.

This is step 6 of the pipeline of the plan: the kinematic layer. It knows nothing about the existence and the
class (the evidential layer, B4 to B6). The two layers stay separate, as the plan asks. `with_states` puts the
two side by side in one table when a user needs both.

## Check a run

```bash
aoc-kinematics RUN_FOLDER          # the folder with observations.parquet and truth.parquet
```

The command aligns and associates every tick, fuses the states of every group with five methods, and measures
them against the truth. Only the groups of two members or more are measured: the others are the same for every
method. It takes 6 to 10 minutes for a run of 60 s with 20 vehicles, depending on the machine and on the memory limit.

| Option | What it does |
| --- | --- |
| `--hold SECONDS` | forgets a received object when it is older than this value (default: it is kept until the end of the run) |
| `--drop-unmatched` | leaves out the rows without a true object or without a time |

The result is PASS when each method keeps what it promises about the size of the fused covariance (see below),
and when covariance intersection is not more sure than its inputs: its NEES on the position must not be above the
one of the newest member alone by more than 0.1. (The NEES of the inputs is a matter of the alignment, B3, not of B7.)

## Use it in Python

```python
from aoc.fusion.alignment import align
from aoc.fusion.association import associate
from aoc.fusion.kinematics import fuse_states, with_states, KinematicsModel

aligned = align(tick)
groups = associate(aligned)
states = fuse_states(aligned, groups)                   # covariance intersection, determinant criterion
states.state                                            # one line per group: x, y, vx, vy
states.cov                                              # one 4 x 4 covariance per group
states.members                                          # the line of each object of the tick

fused = fuse(aligned.objects, groups, trusted)          # the evidential layer, task B6
table = with_states(fused.objects, states)              # adds x, y, vx, vy, sigma_xy, n_states to its table
kalman = fuse_states(aligned, groups, KinematicsModel("independent"))
```

## Why covariance intersection

Two reports of one object may share a part of their error. Two neighbours may have seen it from the same place.
Two reports moved forward to the time of the tick share the unknown motion of the object since their measurement.
The Kalman way of fusing (the inverse covariances add up) assumes independent errors. With shared errors it gives
a covariance that is too small: the fused object looks more sure than it is.

Covariance intersection (Julier and Uhlmann, 1997) takes a convex combination of the inverse covariances instead:

    P^-1 = omega Pa^-1 + (1 - omega) Pb^-1
    x = P (omega Pa^-1 xa + (1 - omega) Pb^-1 xb)

For any omega between 0 and 1, the fused covariance is safe whatever the correlation of the two errors: it is at
least the real error covariance, when the two inputs are. The weight omega is free. It is chosen to make the fused
covariance smallest, by its determinant (default) or its trace. The price of the safety: when the errors are in
fact independent, the covariance is larger than needed.

## The methods

| Method | What it does | What it promises |
| --- | --- | --- |
| `ci` (default) | covariance intersection, two members at a time, omega by the smallest determinant | the determinant of the fused covariance is at most the smallest one of the members |
| `ci` with `criterion="trace"` | the same, omega by the smallest trace | the same with the trace |
| `fast_ci` | covariance intersection with fixed weights (Niehsen, 2002), one pass for any number of members. The weight of a member is the inverse of the trace of its covariance, rescaled to a total of 1 | the determinant is at most the largest one of the members |
| `independent` | the Kalman way: the inverse covariances add up | the determinant is at most the smallest one of the members. Right only when the errors are independent |
| `newest` | the newest member alone (what task B6 uses before this task) | nothing |

With three members or more, `ci` and `independent` take the members two at a time, in the order of the rows: the
first with the second, the result with the third, and so on. Each step is safe, so the whole is safe. For `ci` the
result depends on the order. The check of a run measures it: the groups of three members or more are fused again
in the reverse order, and the shift of the fused place is given in units of its uncertainty (see the measures below).
The first rows are the on-board sensors, then the received objects.

The weight omega is found by a golden-section search on [0, 1] (40 steps, within 1e-8). The size of the fused
covariance has a single minimum in omega. When the two covariances are the same, every omega gives the same
covariance and the search keeps one half, so the two states are averaged.

## What the measures say

The measures are on the groups of two members or more, against the true place and speed at the time of the tick.
RMSE is the root mean square of the position error per axis (the two axes pooled). NEES is the squared error in
units of the covariance: 2 on average when the covariance is right (two axes), below 2 when it is larger than
needed (safe), above 2 when it is too sure.

Run with the AoC sensors (60 s, 20 cars, one seed), memory of 5 s. 77 659 groups of two members or more
(largest: 8 members); the memory limit does not change them, since old lone reports join no group.

| Method | RMSE (m) | Within 1.5 m | NEES position | NEES speed |
| --- | --- | --- | --- | --- |
| newest member alone | 0.845 | 97.1% | 2.18 | 1.67 |
| independent (Kalman) | 0.806 | 97.2% | 2.64 | 3.81 |
| fast covariance intersection | 0.807 | 97.2% | 1.21 | 1.31 |
| covariance intersection, trace | 0.816 | 97.1% | 1.86 | 1.50 |
| covariance intersection, determinant | 0.810 | 97.2% | 1.75 | 1.46 |

What it shows:

- **Covariance intersection is the safe one.** Its NEES is 1.75 on the position: the covariance it gives is a
  little larger than the real error. The Kalman way is too sure: 2.64 on the position and 3.81 on the speed. The
  errors of the members are not independent, although their measurement noises are: the members are moved forward
  to the tick with the same unknown motion of the car.
- **The gain in precision is small here.** The Kalman way gains 5% of RMSE over the newest member, covariance
  intersection 4%. Measured on three vehicles: two thirds of the steps of covariance intersection end at an edge,
  where the fused state is one of the two members (one report is better than the other in every way). The median
  weight is one half: two reports as good as each other, and the two are averaged.
- **The order of the members matters little here.** 63 299 groups have three members or more. Fused in the reverse
  order, their place moves by 0.07 of its uncertainty on average. On random covariances of the same size the shift
  can reach one uncertainty, so the measure stays in the check.
- **Fast covariance intersection is too careful** (NEES 1.21). Its fixed weights give the old reports more than
  they deserve, and the covariance grows with them.

Run with the example sensors (same traffic, other sensors). Every report is fresh, and 79 411 groups have two
members or more (largest: 22 members, one per neighbour).

| Method | RMSE (m) | Within 1.5 m | NEES position | NEES speed |
| --- | --- | --- | --- | --- |
| newest member alone | 0.309 | 100.0% | 1.94 | 1.23 |
| independent (Kalman) | 0.111 | 100.0% | 2.00 | 6.50 |
| fast covariance intersection | 0.124 | 100.0% | 0.25 | 0.47 |
| covariance intersection, trace | 0.272 | 100.0% | 1.53 | 1.03 |
| covariance intersection, determinant | 0.262 | 100.0% | 1.43 | 0.98 |

What it shows:

- **Here the Kalman way wins on the position.** With many fresh reports whose measurement noises are independent
  (the replay draws one noise per sender and per report), it divides the position error by three, and its position
  covariance is right (NEES 2.00). Covariance intersection keeps its covariance safe (1.43) but gives up most
  of the gain: 0.262 m against 0.309 m. This is the known price of covariance intersection when the errors are
  in fact independent.
- **On the speed the Kalman way is too sure** (NEES 6.50). The speed errors of the reports are not independent:
  the reports are moved forward with the same unknown motion of the car.
- The choice between the two is a choice of the plan: safety under unknown correlation (covariance intersection,
  the choice of the plan) against precision when the errors are known to be independent (the Kalman way). Methods
  in between exist (split covariance intersection, inverse covariance intersection); they are not built.

### Limits

One run, one seed, one traffic for each table. The replay draws independent measurement noises for the senders,
so the correlation between the reports comes only from the common motion of the object. In real traffic the
reports of two neighbours can share more (a relayed report, a shared tracker). A direction, not a result.

## What the tests check

- Covariance intersection by hand on a 2 by 2 example, against the formulas.
- The weight omega against a brute-force search on 200 001 values, for both criteria and frames of 2 and 4.
- The promises: the determinant (or the trace) of the fused covariance is at most the smallest one of the members;
  the order of the two members does not matter; omega 0 and 1 give back a member.
- The safety: two estimates with a shared error. The Kalman way gets a NEES far above 2, covariance intersection
  stays at or below 2. With independent errors, the Kalman way is right and covariance intersection is larger than needed.
- The fusion of a tick against a plain loop over the groups, for all methods, with groups of any size.
- The check of a run is counted again group by group, and a fusion that is too sure makes it fail.
