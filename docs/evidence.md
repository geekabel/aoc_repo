# The evidence of each object

Task B4. Each object that a source reports is turned into evidence: how much it supports "this object exists",
and how much it supports each class. The evidence is written as mass functions (belief function theory).
Two modules do it: `aoc.fusion.belief` and `aoc.fusion.bba`.

This is step 3 of the pipeline of the plan. The next steps lower the evidence for the age of the object (B5)
and combine the evidence of the objects of one group (B6).

## Check a run

```bash
aoc-evidence RUN_FOLDER          # the folder with observations.parquet and truth.parquet
```

The command builds the evidence of every object of every tick and checks that every mass function is valid.
Options: `--score`, `--class-confidence`, `--drop-unmatched`.

## Use it in Python

```python
from aoc.fusion.alignment import align
from aoc.fusion.bba import build_evidence
from aoc.fusion import belief
from aoc.fusion.belief import EXISTENCE, CLASS

aligned = align(tick)
evidence = build_evidence(aligned.objects)
evidence.existence                                        # one row per object, 4 columns
evidence.classes                                          # one row per object, 16 columns
belief.belief(EXISTENCE, evidence.existence, "E")         # 0.99 for each object
belief.plausibility(EXISTENCE, evidence.existence, "E")   # 1.0
belief.pignistic(CLASS, evidence.classes)                 # one probability per class
```

## Mass functions in short

A mass function says how much support each set of answers gets. The masses are not negative and their total is 1.

- Mass on one answer: support for exactly this answer.
- Mass on a set of several answers: support for "one of these", without saying which one.
- Mass on the whole frame: "I do not know".

Three readings:

| Reading | Meaning | How it is computed |
| --- | --- | --- |
| belief of a set | the support that is certain | the masses of the sets inside it |
| plausibility of a set | the support that is possible | the masses of the sets that touch it |
| pignistic probability | one probability per answer, to decide | each mass is shared equally between the answers of its set |

The belief is never above the pignistic probability, which is never above the plausibility.

## The two frames

| Frame | Answers | Sets |
| --- | --- | --- |
| `EXISTENCE` | E (it exists), notE (it does not) | 4 |
| `CLASS` | car, truck, bicycle, pedestrian | 16 |

A table of mass functions has one row per object and one column per set. A set has a number: the answers have
the positions 0, 1, 2, 3, and the bits of the number tell which answers are in the set.
For the existence frame the columns are: empty set, {E}, {notE}, {E, notE}.

The empty set is kept. After a combination its mass is the conflict between the sources.
The pignistic probability leaves it out and rescales the rest.

## How an object becomes evidence

**Existence.** The source says "this object exists" with a score s between 0 and 1:

```
m({E}) = s        m({E, notE}) = 1 - s
```

A report never puts mass on "does not exist". It is support for the object, never against it.

The score comes from the source if it gives one (column `exist_score`). Artery gives none. So today the score comes
from a table, by the origin of the measurement (column `meas_src`):

| Origin | Score |
| --- | --- |
| radar | 0.99 |
| fov | 0.99 |
| see_through | 0.99 |
| cam_msg | 0.99 |
| anything else | 0.99 |

**Class.** The source names a class with a confidence c (0.9 by default):

```
m({class}) = c    m(whole frame) = 1 - c
```

| Class name in the observations | Class of the frame |
| --- | --- |
| passenger_car | car |
| light_truck, heavy_truck, bus, trailer, tram, special_vehicle | truck |
| cyclist | bicycle |
| pedestrian | pedestrian |
| anything else (motorcycle, moped, unknown, no class) | no information: all mass on the whole frame |

## The values and why (decisions of 2026-10-13)

**Existence score: 0.99.** In the simulator every fresh reported object is real. A score of 0.9 would say 0.90
where the truth is 1.00, for every method alike. The score is not 1, because a certainty can never be corrected later.
When the sensor models have false alarms (task C6), the score becomes 1 minus the false alarm rate.
The table has one line per origin, so each origin can get its own value.

**Class confidence: 0.9.** In the simulator the class is always right. The existence confidence does not use the class,
so this value has almost no effect on the AoC results. It will be set with the sensor models (task C6).

**The class is not lowered with age.** An object seen three seconds ago may have moved, but it has not changed its class.
The discounting of task B5 lowers the existence evidence only.

**No evidence against an object.** A source can say "there is an object". It never says "there is none".
So with age the confidence falls to "I do not know", never to "it is not there".

This decides how the confidence must be read:

- The belief counts only the support that is certain. It can fall to 0.
- The pignistic probability splits "I do not know" half and half. So it can never go below 0.5.

Measured on the run of 60 s with the AoC sensors (one source at a time, before any combination).
"Really there" means that the true object is within 1.5 m of the place where the report puts it now.

| Age of the object | Really there | Reliability alpha of the theory (car) |
| --- | --- | --- |
| up to 0.3 s | 100% | 1.00 |
| 0.3 to 0.6 s | 98% | 0.99 |
| 0.6 to 1 s | 91% | 0.71 |
| 1 to 2 s | 43% | 0.22 |
| 2 to 5 s | 11% | 0.03 |
| over 5 s | 1% | 0.00 |

Objects older than 1 s are 72% of what a vehicle has in memory, and they are really there in 6% of the cases.

| How the confidence is read | Confidence given to the objects older than 1 s | Calibration error over all objects |
| --- | --- | --- |
| full trust (no discount) | 0.95 to 1.00 | 0.59 to 0.69 |
| discount, pignistic probability | 0.51 | 0.33 |
| discount, belief | 0.02 | 0.04 |

So the confidence of the AoC work is read on the belief, and on the interval between belief and plausibility.
The plan was changed for H1, H3 and figure F5 on 2026-10-13. The pignistic probability is still computed and reported.

A later option, not built: let the sensors of the vehicle itself give evidence against an object, when they look at
its place and see nothing. It needs the field of view of each sensor and the occlusions.

**Motorcycles and mopeds have no class of the frame.** The plan lists four classes. They get no class evidence.
Our scenarios have none.

**The sender confidence of a CPM is not used here.** It is used in the discounting (B5), as the plan says.

One more thing the table shows: between 0.6 and 1 s the theory is too careful for cars (0.71 against 91%).
The assumed motion value of a car looks too high. This is for tasks C2 to C4.

## What the checks say

On the two runs of 60 s (0.9 and 1.1 million objects over all ticks), every mass function is valid.
All objects are passenger cars, so every class mass is on "car".

The readings (belief, plausibility, pignistic probability) are tested against their definitions on random mass functions,
and against the library `dst-py` of Godwin on 60 random mass functions: same values. This last test runs only where
`dst-py` is installed. The pipeline does not depend on it: `dst-py` handles one mass function at a time, and the pipeline
needs a million of them per run, so `aoc.fusion.belief` works on whole tables.

## Speed

The evidence of all objects of a run of 60 s is built in a few seconds. Most of the time of the check is the replay and the alignment.
