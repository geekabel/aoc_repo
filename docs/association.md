# Time alignment and association

At a fusion tick, a vehicle knows objects from several sources: each of its sensors, and each vehicle
that sends CPMs. Several of these objects are the same real object. Task B3 answers two questions:

1. Where is each object now? (`aoc.fusion.alignment`)
2. Which objects are the same real object? (`aoc.fusion.association`)

The association uses the adapted greedy algorithm of Wolf et al. (FUSION 2023), with a likelihood distance
(the Mahalanobis distance plus a term for the size of the uncertainty).

## Check a run

```bash
aoc-associate RUN_FOLDER          # the folder with observations.parquet and truth.parquet
```

The command aligns and associates every tick of every vehicle, compares the result with the ground truth,
and prints PASS or FAIL. Options: `--method`, `--distance`, `--no-coincidence-test`, `--euclidean-limit`,
`--gate`, `--with-speed`, `--hold`, `--seed`, `--drop-unmatched`.

## Use it in Python

```python
from aoc.fusion.replay import load_run
from aoc.fusion.alignment import align
from aoc.fusion.association import associate

run = load_run("RUN_FOLDER")
for tick in run.replay("flow0.0"):
    aligned = align(tick)             # every object moved to the time of the tick
    groups = associate(aligned)       # one group number per object
    table = aligned.objects.assign(group=groups)
```

`aligned.objects` has one row per object: the sensor rows of the tick first, then the known CPM objects.
If a sensor gives several rows for one of its tracks in a tick (a tick period longer than the sensor step),
only the newest row is kept.
`aligned.state` holds x, y, vx, vy at the time of the tick, and `aligned.cov` the 4 x 4 covariance of each object.
New columns of `aligned.objects`:

| Column | Meaning |
| --- | --- |
| `source` | who gave the object: the name of the sensor, or `v2x:` plus the sender |
| `x_t`, `y_t`, `vx_t`, `vy_t` | state at the time of the tick |
| `sigma_t` | position uncertainty at the time of the tick, in m |
| `q` | the intensity of the unknown acceleration used for this object |

## Step 1: time alignment

The motion model is the one of the theory note: constant velocity, plus an unknown acceleration of intensity q.
For an object of age a (time of the tick minus its measurement time):

```
position at the tick   = position + a * speed
position variance      = position variance + a^2 * speed variance + q * a^3 / 3
speed variance         = speed variance + q * a
position-speed link    = a * speed variance + q * a^2 / 2
```

The position variance is Equation (1) of the theory note. So an old object is not only moved: its uncertainty grows,
and the next steps see it.

The value of q comes from the class of the object (`Q_BY_CLASS`): 4.0 for a car, 1.44 for a truck or a bus,
0.98 for a cyclist, 0.5 for a pedestrian. These are the assumed values of the theory note. A class that is not in
the table gets the largest value.

## Step 2: association

This is a track-to-track association: it decides which objects of the different sources are the same real object.
The default method is the **adapted greedy algorithm** of the paper

> L. M. Wolf, S. Steuernagel, K. Thormann, M. Baum, "Track-to-track Association based on Stochastic Optimization",
> FUSION 2023, Algorithm 1.

It is the greedy algorithm of Houenou et al. (IV 2012) with one more step, the merging of two groups.

1. Every object starts alone.
2. The distance of every pair of objects is computed. A pair is removed from the list when the two objects come
   from the same source, or when they are too far apart.
3. The pairs are taken in the order of their distance, the closest first:
   - both objects are alone: they form a new group;
   - one is alone: it joins the group of the other, unless this group already has an object of its source;
   - both are in a group: the two groups are merged, unless they share a source. This is the step added by the paper.
4. After a pair is taken, the pairs that it rules out are removed from the list.

The code follows the pseudo-code of the paper line by line. I compared it with the authors' own program
(github.com/Fusion-Goettingen/TAES_2025_Wolf_T2TA) on 2882 random scenes with the Euclidean distance: the groups are
the same in every scene. Their program is not copied here: it has no licence file.

### The distance

The paper uses the Euclidean distance. Godwin asked for the Mahalanobis distance: the distance counted in units
of the joint uncertainty of the two objects. After the measurements below, the default is the **likelihood distance**
(decision of 2026-10-13): the Mahalanobis distance plus a term that grows with the size of the uncertainty.
"Too far apart" means two tests:

- **The gate.** The Mahalanobis distance must be below a limit. With the default, a true match passes with a probability of 0.99.
- **The coincidence test.** The match must be more likely than finding another object at this place by chance.
  The chance is set by a density: one object per 100 m2 by default.

Why the second test is needed. An old object is vague. Counted in units of its own uncertainty, a vague object
is "close" to everything. So the gate alone lets it match any object around it. The paper does not have this
problem: all its tracks have the same uncertainty. Here the objects have very different ages.
An old object that fails the coincidence test stays alone in its group. It is not thrown away: the fusion will give it
the reliability of its age. A car object older than about 2.2 s is in this case.

Three distances can be chosen (`AssociationConfig.distance`, option `--distance`):

| Distance | Order of the pairs | Too far apart |
| --- | --- | --- |
| `likelihood` (default) | Mahalanobis distance plus a term that grows with the size of the uncertainty | gate, and coincidence test |
| `mahalanobis` | Mahalanobis distance | gate, and coincidence test |
| `euclidean` | distance in metres, as in the conference paper | more than `euclidean_limit` (2 m) |

The `likelihood` distance is the negative logarithm of the likelihood of the pair. The same authors replace the Euclidean
distance by such a likelihood in the journal version of their paper (arXiv:2510.21278), and report that the greedy algorithm
gains from it.

### What I measured

Run of 60 s with the AoC sensors, 20 vehicles. A fresh pair is two objects of one real object, from two sources,
both younger than 0.5 s.

Old CPM objects kept in memory (the default, `hold` = none):

| Method | Wrong matches | Fresh pairs found | Pairs up to 1 s found |
| --- | --- | --- | --- |
| greedy, Mahalanobis, gate only | 1,837,477 of 2,898,938 | 76.0% | 73.4% |
| greedy, Mahalanobis, gate and coincidence test | 0 of 404,427 | 95.1% | 92.7% |
| greedy, likelihood, gate and coincidence test (default) | 0 of 407,827 | 99.3% | 95.8% |
| greedy, Euclidean, 2 m | 12,641 of 518,009 | 98.0% | 93.7% |
| sensorwise | 0 of 402,093 | 98.6% | 95.1% |

CPM objects older than 1 s forgotten (`--hold 1.0`):

| Method | Wrong matches | Fresh pairs found | Pairs up to 1 s found |
| --- | --- | --- | --- |
| greedy, Mahalanobis, gate only | 0 of 314,077 | 98.6% | 96.2% |
| greedy, Mahalanobis, gate and coincidence test | 0 of 311,658 | 98.4% | 95.5% |
| greedy, likelihood, gate and coincidence test (default) | 0 of 312,267 | 99.2% | 95.7% |
| greedy, Euclidean, 2 m | 0 of 310,607 | 99.1% | 95.2% |
| sensorwise | 0 of 310,242 | 98.6% | 95.1% |

Run of 60 s with the example sensors (all objects are fresh): every greedy variant finds 100.0% of the fresh pairs
with no wrong match. The sensorwise method finds 99.7%.

What the numbers say:

- The greedy algorithm of the paper is better than the sensorwise method that I wrote first, when the objects are fresh.
- With the Mahalanobis distance and the gate alone, old objects ruin the result: two matches out of three are wrong.
  The coincidence test repairs this completely.
- With the Mahalanobis distance, the greedy algorithm takes the pairs with an old object first, because they look
  the closest. This costs fresh pairs. The typical case: a sender lost an object and found it again, so it reports it twice,
  once old and once fresh. A group takes one object per source. The old one joins first, and the fresh one is left out.
  With the `likelihood` distance the fresh one joins first. This is the difference between 95.1% and 99.3%.
- With a short memory (`hold` of 1 s) the three distances are close to each other.

Decision of 2026-10-13: the greedy algorithm with the `likelihood` distance is the default. It is still the
Mahalanobis distance, with one more term for the size of the uncertainty.

### The other method

`method="sensorwise"` is the method that I wrote first. The sources are taken one after the other: the sensors of
the vehicle, then the senders by name. Each object of a new source is compared with every member of every group,
and the best global choice is taken with the Hungarian method. It stays in the code as a point of comparison.

### Rules that hold for both methods

- Two objects of one source are never in one group. A source reports a real object once.
- The identifiers given by the sources are not used to match. In particular, the track number of the local environment model
  of Artery is the same for all sensors of a vehicle. It comes from the simulator, and a real vehicle does not have it.
- Every object needs a source name. A sensor row without a sensor name, or a CPM row without a sender, stops with an error.
- An exact estimate (a covariance of zero) is given a very small uncertainty of 1 mm, so that it can still be compared.
- The groups are numbered from 0, in the order in which they first appear in the list of objects.

`oracle_groups(aligned)` gives the perfect association from the ground truth. It is for evaluation and for ablations:
a fusion variant can be run with the perfect association to separate the effect of the trust handling from the effect of wrong matches.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `method` | "greedy" | "greedy": the adapted greedy algorithm of the paper. "sensorwise": the other method |
| `distance` | "likelihood" | "likelihood", "mahalanobis" or "euclidean" (greedy method) |
| `gate_probability` | 0.99 | a true match passes the gate with this probability |
| `coincidence_test` | True | a match must also be more likely than a chance meeting |
| `euclidean_limit` | 2.0 | largest distance in m of a match with the Euclidean distance |
| `use_velocity` | False | False: compare the positions. True: compare positions and speeds |
| `density_position` | 0.01 | chance objects per m2, for the coincidence test |
| `density_velocity` | 0.03 | the same for the speed, per (m/s)2; only with `use_velocity` |

Positions only by default. The speed of an old object is the weakest part of its state: the object may have braked
or accelerated since. With the sensorwise method, using the speeds gave 4 wrong matches and fewer fresh pairs (97.6% against 98.6%).
The option stays, for scenarios where vehicles cross each other closely.

A lower density (a more tolerant coincidence test) finds almost no more fresh pairs, and it starts to join old objects
with the wrong vehicle (measured with the sensorwise method: 1374 wrong matches at a density of 0.003, 9576 at 0.001).

## What the checks say

The command `aoc-associate` passes when at least 99% of the matches are right, 95% of the fresh pairs are found,
and 90% of the pairs up to 1 s are found. With the default settings the three numbers are 100%, 99.3% and 95.8% on the AoC run.
With `--distance mahalanobis` and old objects kept in memory, the second number is at the limit (95.1%).

Most of the missed fresh pairs have a member older than 0.3 s. The motion model does not cover a sustained acceleration,
and this is where it shows.

How well the predicted uncertainty fits the real error (sample run, squared position error in units of the predicted
covariance, 2.0 means a perfect fit):

| Age of the object | Value |
| --- | --- |
| 0 s (sensor rows) | 2.0 |
| up to 0.3 s | 1.7 |
| 0.3 to 1 s | 2.7 |
| 1 to 3 s | 5.8 |

So the model is right for fresh objects, a little too confident up to 1 s, and too confident after 1 s in this scenario
(vehicles that start and accelerate). This is the known limit of the model, already listed in the plan.

With the default `hold` (none), old CPM objects stay in the memory of the vehicle and each one forms its own group.
On the AoC run this gives 112 groups per tick for at most 19 real objects. With `--hold 1.0` there are 13 groups per tick.
The fusion variants choose their own hold time.

## Limits

- The two estimates of a pair are taken as independent. This is the usual simplification. In truth two predictions
  of one object share the same motion error.
- The greedy algorithm, as published, can depend on the order of the objects in rare cases. After a pair is taken,
  it removes other pairs from the list, also when the pair could not be matched. Which pairs are removed depends on the
  position of the objects in the list. On the AoC run, shuffling the objects changed the groups in 10 ticks out of 817.
  The authors' program behaves the same. In our pipeline the order is fixed (sensor rows first, then the CPM objects),
  so a run always gives the same groups. Leaving this removal step out makes the result independent of the order,
  and it does not change the quality (measured on random scenes and on the AoC run).
- The greedy algorithm joins groups through their closest pair. Two real objects that are very close to each other
  can be chained into one group when no source sees both.
- No memory between ticks: the groups of one tick do not depend on the groups of the tick before, and a group has no
  identifier that lasts in time.

## Speed

About 7 ms per tick and vehicle for replay, alignment, association and the check together (AoC run, 140 objects per tick).
The greedy method compares every pair of objects, so it gets slower in dense scenes: about 17 ms per tick on the run
with the example sensors (180 fresh objects per tick).
