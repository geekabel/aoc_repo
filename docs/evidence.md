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
belief.belief(EXISTENCE, evidence.existence, "E")         # 0.9 for each object
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
| radar | 0.9 |
| fov | 0.9 |
| see_through | 0.9 |
| cam_msg | 0.9 |
| anything else | 0.9 |

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

## Assumed values, to confirm or change

- **The score of 0.9 and the confidence of 0.9.** They are assumed, like the motion values of the theory note.
  In the simulator a detection is always a real object and its class is always right, so the data cannot give these values.
  They will be set with the sensor models (task C6).
- **One score for all origins.** The table has one line per origin, so each one can get its own value later.
- **No evidence against an object.** A sensor that does not see an object says nothing about it. An object loses
  support only through the discounting (its age), not through a missing detection.
- **Motorcycles and mopeds have no class of the frame.** The plan lists four classes. They get no class evidence.
- **The sender confidence of a CPM is not used here.** It is used in the discounting (B5), as the plan says.

## What the checks say

On the two runs of 60 s (0.9 and 1.1 million objects over all ticks), every mass function is valid.
All objects are passenger cars, so every class mass is on "car".

The readings (belief, plausibility, pignistic probability) are tested against their definitions on random mass functions,
and against the library `dst-py` of Godwin on 60 random mass functions: same values. This last test runs only where
`dst-py` is installed. The pipeline does not depend on it: `dst-py` handles one mass function at a time, and the pipeline
needs a million of them per run, so `aoc.fusion.belief` works on whole tables.

## Speed

The evidence of all objects of a run of 60 s is built in a few seconds. Most of the time of the check is the replay and the alignment.
