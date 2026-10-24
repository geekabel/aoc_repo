# The discount: how much each object is trusted

Task B5. The evidence of task B4 says "this object exists" with the score of the source. It does not look at
the age of the object. The discount lowers this evidence when the object is old: an old report tells less
about where the object is now. The module is `aoc.fusion.discounting`. The operations on mass functions are in
`aoc.fusion.belief`.

This is step 4 of the pipeline of the plan. The next step combines the evidence of the objects of one group (B6).

## Check a run

```bash
aoc-discount RUN_FOLDER          # the folder with observations.parquet and truth.parquet
```

The command discounts the evidence of every object of every tick and checks the result.
Then it measures how right the belief is for three ways to handle trust.
It takes about 40 s for a run of 60 s.

| Option | What it does |
| --- | --- |
| `--hold SECONDS` | forgets a received object when it is older than this value (default: it is kept until the end of the run) |
| `--fit` | also searches the motion value that fits this run best |
| `--motion` | also measures how the objects really move, on the ground truth of the run |
| `--q CLASS=VALUE` | sets the motion value of one class, for example `--q passenger_car=1.0` |
| `--epsilon` | tolerance on the place of an object in m (default 1.5) |
| `--cutoff` | age limit of the hard cutoff in s (default 1.0) |
| `--no-sender-confidence` | does not multiply by the confidence declared by the sender |
| `--drop-unmatched` | leaves out the rows without a true object or without a time |

The result is PASS when, for each of the three laws:

- every mass function is valid;
- no belief was raised by the discount;
- no object got evidence against it;
- the class evidence did not change;
- full trust changed nothing, and the cutoff dropped the received objects older than the limit and no other.

These conditions check that the discount works as designed. The measures (calibration error, Brier score)
are results: they are printed, and they are not a pass condition.

## Use it in Python

```python
from aoc.fusion.alignment import align
from aoc.fusion.bba import build_evidence
from aoc.fusion.discounting import discount_evidence, TrustModel
from aoc.fusion import belief
from aoc.fusion.belief import EXISTENCE

aligned = align(tick)
evidence = build_evidence(aligned.objects)
trusted = discount_evidence(evidence, aligned.objects)             # the proposed law
trusted.reliability                                                # the alpha of each object, between 0 and 1
belief.belief(EXISTENCE, trusted.existence, "E")                   # score of the source times alpha
belief.plausibility(EXISTENCE, trusted.existence, "E")             # 1.0: nothing speaks against the object

cut = discount_evidence(evidence, aligned.objects, TrustModel(law="cutoff"))                 # baseline B1
mine = discount_evidence(evidence, aligned.objects, TrustModel(q_by_class={"passenger_car": 1.0}))
```

## What the discount does

Each object has a reliability alpha between 0 and 1. Its existence evidence becomes:

- m({E}) = alpha * s
- m({E, notE}) = 1 - alpha * s

With alpha = 1 the evidence is kept. With alpha = 0 the object says nothing: "I do not know".
The discount never says "the object is not there".

The class evidence is not lowered. An old object may have moved, but it has not changed its class
(decision of 2026-10-13).

## The three laws

The plan compares ways to handle trust. They differ only in how alpha is found.

| Law | Name in the plan | alpha |
| --- | --- | --- |
| `aoc` | B3, the proposed one | the reliability curve of the theory note, read at the age of the object |
| `full_trust` | B0 | 1 for every object |
| `cutoff` | B1, today's practice | 0 for a received object older than 1 s, 1 for every other object |

A dropped object (cutoff) is kept in the table with alpha = 0. So the three laws can be judged on the same objects.

## The law "aoc"

alpha is the product of two parts.

**1. The age.** The probability that the true place of the object is within the tolerance (1.5 m) of the place
where its report puts it now. It is Equation (2) of the theory note:

    variance of the place now = variance of the report + age^2 * variance of the speed + q * age^3 / 3
    alpha = 1 - exp(- tolerance^2 / (2 * variance of the place now))

The variances come from the object itself (columns P11 to Pv22). The motion value q comes from the class of the
object. When the uncertainty is not the same in every direction, the largest variance over all directions is
used (the largest eigenvalue of the covariance). Task A2 checked that alpha is then a safe lower bound
(`aoc.theory.reliability`, part 5).

The motion values of the discount are a setting of their own (`TrustModel.q_by_class`). By default they are the
assumed values of the theory note, the ones that the alignment uses. Changing them here does not change the alignment.

**2. What the source declares.** For a received object: the confidence that the sender declared (column
`sender_conf`, between 0 and 1). An empty value counts as 1. Artery always declares the highest quality, so this
factor is 1 in today's runs. For an object of an on-board sensor: the reliability of the sensor
(`TrustModel.sensor_reliability`). It is 1 today. The table by weather and range of the plan comes with the
sensor models (task C6).

## Classical or contextual discounting?

`aoc.fusion.belief` has both operations.

- `discount`: the classical discounting of Shafer, the one of the theory note (its Equation 4). One reliability for the source.
- `contextual_discount`: the contextual discounting of Mercier, Quost and Denoeux (Information Fusion, 2008).
  One reliability per context. A context is a set of answers: "how reliable is the source when the truth is in this set?"

Contexts can be single answers, a partition of the frame, or sets that overlap. For sets that overlap, the code
follows the corrigendum of Mercier, Pichon and Lefevre (IJAR, 2016), which corrects the result of the article of 2012.

For our existence evidence the two operations give the same result. The evidence supports E only. Then the
contextual discounting with the contexts {E} and {notE} depends on one number only: the reliability in the
context "the object is not there". The reliability in the context "the object is there" has no effect.
A test checks this. So the pipeline uses the classical operation, with alpha in this role.

One point of wording for the papers. The plan says "contextual discounting" because alpha depends on the
context of the object: its age, later the weather and the range. In the belief function literature, the words
mean the operation of Mercier et al., where a context is a set of answers. A reviewer of this field may ask.
The operation of Mercier et al. will make a real difference in two cases: when a sensor is more reliable for
some classes than for others (class frame), and if on-board sensors give evidence against an object one day.

## How right is the belief? What is measured

An object is "really there" when its true object is within 1.5 m of the place where its report puts it now.
An object whose true object has left the simulation is not there. Two measures compare the belief in
"the object exists" with this truth. For both, 0 is perfect.

- **Calibration error** (10 bins). The objects are grouped by their belief. In each group, the mean belief is
  compared with the share of objects that are really there. It is the measure of H1 in the plan.
- **Brier score.** The mean of (belief - truth)^2 over the objects, with truth 1 or 0.

The calibration error has one limit. It looks at groups, not at each object. If every object gets the belief
0.99 and 99% of them are there, the error is 0, even though the belief cannot tell which objects are missing.
The Brier score does not have this limit. So both are printed.

Every object counts, one source at a time (before any combination). A dropped object counts with belief 0.

**The memory of the receiver matters.** By default the replay keeps every received object until the end of the
run. An old report is then counted again at every tick. On the first run below, a report is counted 16 times on
average, the median age of a received object is 6.2 s and the oldest is 51.7 s. So the numbers depend on how long
the receiver keeps an object. `--hold` sets this limit. A real receiver has such a limit.

## Measures on the run with the AoC sensors

One run of 60 s, 20 cars, one seed. Each cell gives the calibration error, then the Brier score.

| Memory of the receiver | Received objects older than 1 s | Full trust (B0) | Cutoff at 1 s (B1) | Discount (B3), assumed value 4.0 | Discount (B3), value fitted on this run |
| --- | --- | --- | --- | --- | --- |
| no limit | 74.1% | 0.678, 0.674 | 0.046, 0.049 | 0.042, 0.043 | 0.009, 0.036 (value 1.11) |
| 10 s | 59.3% | 0.509, 0.509 | 0.071, 0.075 | 0.065, 0.066 | 0.014, 0.055 (value 1.11) |
| 5 s | 42.8% | 0.333, 0.336 | 0.090, 0.096 | 0.083, 0.084 | 0.014, 0.069 (value 1.12) |
| 2 s | 17.4% | 0.107, 0.115 | 0.084, 0.092 | 0.084, 0.077 | 0.008, 0.059 (value 1.20) |

What it shows:

- Hypothesis H1 asks for a calibration error of B3 at least 30% below B0 and B1. With the assumed motion value,
  B3 is between 0% and 9% below B1, for every memory limit. This is not enough.
- With the fitted value, the calibration error of B3 is 80% to 90% below B1, and its Brier score 26% to 36% below.
- The fitted value is about the same for every memory limit: 1.1 to 1.2.
- The error of full trust depends very much on the memory limit.

The fitted value was found on the run that it is measured on. This is too kind to it. For a result, the value
must come from other runs.

## Measures on the run with the example sensors

Same traffic, other sensors on the vehicles. Almost every object is fresh: 2.7% of the received objects are
older than 1 s, and none is older than 2.5 s. So the memory limit changes nothing here.

| | Calibration error | Brier score |
| --- | --- | --- |
| Full trust (B0) | 0.004 | 0.006 |
| Cutoff at 1 s (B1) | 0.029 | 0.026 |
| Discount (B3), assumed value 4.0 | 0.060 | 0.023 |
| Discount (B3), value fitted on this run (0.11) | 0.009 | 0.005 |

- When the network keeps every object fresh, full trust is already right and a discount has little to win.
  The calibration error of full trust is 0.004 because every object gets the same belief (the limit above).
  The Brier score puts the fitted discount at the level of full trust.
- This run cannot give the motion value: it has too few old objects. Every value below about 0.14 gives
  almost the same error. With the value of the first run (1.11), the calibration error is 0.022.

## Why the assumed motion value is too careful (analysis of 2026-10-13)

The cause was searched on the ground truth of the run (`--motion`): the true state of a car is moved forward
at constant velocity and compared with its true state later. No sensor and no noise is involved.

| Prediction at constant velocity | 0.5 s ahead | 1 s ahead | 2 s ahead |
| --- | --- | --- | --- |
| Size of the error per axis (root of the mean squared error) | 0.38 m | 1.27 m | 4.22 m |
| Motion value that gives this size | 3.4 | 4.8 | 6.7 |
| Median error | 0.07 m | 0.41 m | 1.98 m |
| Within 1.5 m of the true place | 96.1% | 83.9% | 46.4% |
| Within 1.5 m, if the error were Gaussian with this size | 100.0% | 50.2% | 6.1% |

What it shows:

- The assumed value 4.0 has the right size for the motion of these cars up to 1 s (3.4 to 4.8).
  The alignment and the association use this size, so 4.0 is right for them.
- The error is not Gaussian. Most of the time a car goes straight and the prediction is almost exact:
  the median error after 1 s is 0.41 m, three times below the size of the error. A few cars give large errors,
  because they turn or change speed. The cars have a turn rate above 5 degrees per second 13% of the time.
- The curve assumes a Gaussian error. With the same size, a Gaussian error puts far fewer objects within
  1.5 m than the truth does. So the curve is too careful, at every age.

This is the open point of the plan "a sustained maneuver is a bias that the model does not cover"
(status of the Month 1 checks).

A smaller motion value in the curve makes up for it. With the value 1.11, the belief follows the share of
objects that are really there (received objects of the first run, no memory limit):

| Age | Really there | Belief, value 4.0 | Belief, value 1.11 |
| --- | --- | --- | --- |
| up to 0.5 s | 99.4% | 0.99 | 0.99 |
| 0.5 to 0.8 s | 92.9% | 0.80 | 0.95 |
| 0.8 to 1 s | 86.3% | 0.53 | 0.82 |
| 1 to 1.5 s | 53.4% | 0.30 | 0.59 |
| 1.5 to 2 s | 30.0% | 0.12 | 0.31 |
| 2 to 3 s | 16.6% | 0.05 | 0.14 |
| 3 to 5 s | 7.3% | 0.01 | 0.04 |

One thing is lost with a fitted value. With the assumed value the belief is below the truth at every age: it
is on the safe side. With the fitted value it is right on average, and it is sometimes a little above the truth
(by 0.02 between 0.5 and 0.8 s, by 0.05 between 1 and 1.5 s).

## Limits of this first look

- 20 cars, 60 s, one seed, one traffic. The numbers show a direction. They are not a result.
- The ages are on a grid of 0.1 s, and 12 486 received objects of the first run are exactly 1.0 s old.
  The cutoff keeps them ("older than 1 s" is dropped). If it dropped them, its calibration error would be
  0.056 in place of 0.046.
- On the objects that the cutoff keeps, the calibration errors are: cutoff 0.020, discount with the assumed
  value 0.061, discount with the fitted value 0.004.

## Decisions of Oct 24, 2026

The four choices below were put to Godwin after the measures above. He said yes to all four, and the Plan holds them.

1. **The motion value of the curve** is fitted on calibration runs, kept apart from the runs of the evaluation.
   The alignment and the association keep the assumed value. Until the calibration runs exist (phase C), the code
   keeps the assumed value by default: a value fitted on the run it is measured on is too kind to it.
2. **The memory limit of the evaluation** is 5 s for every baseline, with 2 s and 10 s as a check (`--hold`).
   The code keeps no limit by default, so that the check of a run stays comparable with the earlier numbers.
3. **The measure of H1** is the calibration error, with the Brier score reported next to it.
4. **H1 must hold** where objects get old (low rate, loss, low penetration). Where every object is fresh,
   the discount must not be worse than full trust.

## What the tests check

- The examples of the article of 2008 (Examples 1, 2 and 3) give the printed values.
- The contextual discounting is compared with its definition: every piece of knowledge is put on the product
  space (answer, state of the source) with plain Python sets, combined and projected. Same result for
  partitions and for contexts that overlap.
- The closed form of Proposition 10 of the article holds for random mass functions.
- The reliability of an object is the curve of the theory note, for each class.
- The calibration error, the Brier score and the fit are tested on simulated data where the answer is known.
- Every number that the check of a run prints is counted again in the tests, object by object, with plain formulas.
- A discount that is broken on purpose makes the check fail.
- The classical discounting and the disjunctive rule give the same values as the library `dst-py` of Godwin.
  This test runs only where `dst-py` is installed.

The contextual discounting of `dst-py` was not used as a referee. On Example 2 of the article (case 1) it
gives {a: 0.3, r: 0.5, a|r: 0.2}. The article gives {a: 0.5, r: 0.3, a|r: 0.2}. On the other cases of
Example 2 and on Example 3 it stops with an error, because it finds the frame from the focal sets.
