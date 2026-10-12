# Time alignment and association

At a fusion tick, a vehicle knows objects from several sources: each of its sensors, and each vehicle
that sends CPMs. Several of these objects are the same real object. Task B3 answers two questions:

1. Where is each object now? (`aoc.fusion.alignment`)
2. Which objects are the same real object? (`aoc.fusion.association`)

Both modules were written from scratch.

## Check a run

```bash
aoc-associate RUN_FOLDER          # the folder with observations.parquet and truth.parquet
```

The command aligns and associates every tick of every vehicle, compares the result with the ground truth,
and prints PASS or FAIL. Options: `--seed`, `--gate`, `--with-speed`, `--hold`, `--drop-unmatched`.

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

1. The sources are taken one after the other: the sensors of the vehicle first, then the senders, by name.
   The objects of the first source open one group each.
2. Each object of the next source is compared with every member of every group. Members that are too vague
   to pass the second test with any partner are skipped. Two tests decide if a match is allowed:
   - **The gate.** The distance between the two objects, counted in units of their joint uncertainty
     (the Mahalanobis distance), must be below a limit. With the default, a true match passes with a probability of 0.99.
   - **The coincidence test.** The match must be more likely than finding another object at this place by chance.
     The chance is set by a density: one object per 100 m2 by default. A vague object fails this test:
     it could match anything, so a match with it proves nothing.
3. Among the allowed matches, the best global choice is taken (the Hungarian method): each object goes to at most one group,
   each group takes at most one object of this source, and the total cost is the lowest.
   An object without a match opens a new group.

Rules that follow:

- Two objects of one source are never in one group. A source reports a real object once.
- An old object that fails the coincidence test stays alone in its group. It is not thrown away:
  the fusion will give it the reliability of its age. With the default settings a car object older than about 2.2 s is in this case.
- The identifiers given by the sources are not used to match. In particular, the track number of the local environment model
  of Artery is the same for all sensors of a vehicle. It comes from the simulator, and a real vehicle does not have it.
- Every object needs a source name. A sensor row without a sensor name, or a CPM row without a sender, stops with an error.
- An exact estimate (a covariance of zero) is given a very small uncertainty of 1 mm, so that it can still be compared.

`oracle_groups(aligned)` gives the perfect association from the ground truth. It is for evaluation and for ablations:
a fusion variant can be run with the perfect association to separate the effect of the trust handling from the effect of wrong matches.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `gate_probability` | 0.99 | a true match passes the gate with this probability |
| `use_velocity` | False | False: compare the positions. True: compare positions and speeds |
| `density_position` | 0.01 | chance objects per m2, for the coincidence test |
| `density_velocity` | 0.03 | the same for the speed, per (m/s)2; only with `use_velocity` |

Why the positions only by default. On the run of 60 s with the AoC sensors, I measured both:

| Setting | Wrong matches | Fresh pairs found | Pairs up to 1 s found |
| --- | --- | --- | --- |
| positions only (default) | 0 of 402,093 | 98.6% | 95.1% |
| positions and speeds | 4 of 418,827 | 97.6% | 94.3% |

A fresh pair is two objects of one real object, from two sources, both younger than 0.5 s.
The speed of an old object is the weakest part of its state: the object may have braked or accelerated since.
Using it removes true matches. The option stays, for scenarios where vehicles cross each other closely.

A lower density (a more tolerant coincidence test) finds almost no more fresh pairs, and it starts to join old objects
with the wrong vehicle: 1374 wrong matches at a density of 0.003 (one object per 333 m2), 9576 at 0.001 (one per 1000 m2).

## What the checks say

| Run | Wrong matches | Fresh pairs found | Pairs up to 1 s found |
| --- | --- | --- | --- |
| 60 s, AoC sensors, 20 vehicles | 0 of 402,093 | 190,089 of 192,810 (98.6%) | 308,956 of 324,776 (95.1%) |
| 60 s, example sensors, 20 vehicles | 0 of 7,987,986 | 5,059,092 of 5,076,614 (99.7%) | 7,597,974 of 7,619,959 (99.7%) |

The command passes when at least 99% of the matches are right, 95% of the fresh pairs are found,
and 90% of the pairs up to 1 s are found.

Where the missed fresh pairs come from (AoC sensors, 2721 pairs):

| Kind of pair | Pairs | Missed | Rate |
| --- | --- | --- | --- |
| two sensor rows | 7,956 | 66 | 0.8% |
| a sensor row and a CPM object | 39,012 | 415 | 1.1% |
| two CPM objects from two senders | 145,842 | 2,240 | 1.5% |

The gate alone lets 1% of the true matches out, by design. The rest comes from the older objects:
80% of the missed pairs have a member older than 0.3 s. The motion model does not cover a sustained acceleration,
and this is where it shows.

With the default `hold` (none), old CPM objects stay in the memory of the vehicle and each one forms its own group.
On the AoC run this gives 112 groups per tick for at most 19 real objects. With `--hold 1.0` there are 13 groups per tick,
and the quality numbers stay the same. The fusion variants choose their own hold time.

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

## Limits

- The two estimates of a pair are taken as independent. This is the usual simplification. In truth two predictions
  of one object share the same motion error.
- The order of the sources can change the result in rare cases, because the groups are built source after source.
  The order is fixed (sensors first, then senders by name), so a run always gives the same groups.
- No memory between ticks: the groups of one tick do not depend on the groups of the tick before, and a group has no
  identifier that lasts in time.

## Speed

About 5 ms per tick and vehicle for replay, alignment and association together (60 s run, 140 objects per tick).
