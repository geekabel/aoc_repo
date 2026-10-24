# The combination: one piece of evidence per real object

Task B6. After the association (B3), the objects of a tick are in groups: the objects of one group are one real
object. After the evidence (B4) and the discount (B5), each object carries two mass functions, existence and
class. The combination merges the mass functions of each group into one. The module is `aoc.fusion.combination`.
The rules on tables of mass functions are in `aoc.fusion.belief`.

This is step 5 of the pipeline of the plan. The next step fuses the places and the speeds (B7).

## Check a run

```bash
aoc-combine RUN_FOLDER          # the folder with observations.parquet and truth.parquet
```

The command runs the whole evidential chain on every tick of every vehicle: align, associate, evidence, discount,
fuse. It checks the fused mass functions and measures how right the fused belief is, for four ways of the plan.
It takes about 2 minutes for a run of 60 s (the discount and the fusion run four times, once per way).

| Option | What it does |
| --- | --- |
| `--hold SECONDS` | forgets a received object when it is older than this value (default: it is kept until the end of the run) |
| `--q CLASS=VALUE` | sets the motion value of the reliability curve for one class |
| `--drop-unmatched` | leaves out the rows without a true object or without a time |

The result is PASS when, for each of the four ways: every fused mass function is valid; no belief is above its
plausibility; no fused object has conflict on the existence frame (no source speaks against an object); and, for
the cautious rule, a fused object made of received evidence only never has more belief than its best source.
The measures are printed and are not a pass condition.

## Use it in Python

```python
from aoc.fusion.alignment import align
from aoc.fusion.association import associate
from aoc.fusion.bba import build_evidence
from aoc.fusion.discounting import discount_evidence
from aoc.fusion.combination import fuse, CombinationModel

aligned = align(tick)
groups = associate(aligned)
trusted = discount_evidence(build_evidence(aligned.objects), aligned.objects)
fused = fuse(aligned.objects, groups, trusted, t=tick.t, ego=tick.ego)
fused.objects                 # one row per real object
fused.existence               # its existence mass function, 4 columns
fused.classes                 # its class mass function, 16 columns
fused.members                 # the row of fused.objects of each object of the tick

ablation = fuse(aligned.objects, groups, trusted, CombinationModel(cpm_rule="conjunctive"))      # Dempster only
```

Columns of `fused.objects`:

| Column | Meaning |
| --- | --- |
| `group` | the group number given by the association |
| `n_sources`, `n_local`, `n_received` | how many objects took part, from the on-board sensors, from messages |
| `n_senders` | how many different vehicles sent the received objects |
| `newest` | the row of `aligned.objects` of the member with the newest measurement |
| `class` | the class name given most often by the members |
| `bel_exists`, `pl_exists` | belief and plausibility that the object exists |
| `conflict_class` | the mass of the empty set of the class mass function |
| `p_car`, `p_truck`, `p_bicycle`, `p_pedestrian` | the pignistic probability of each class |

The place and the speed of a fused object are not here. They come from the kinematic fusion (B7).

## The rules

| Pieces of evidence | Rule | Why |
| --- | --- | --- |
| the objects of the on-board sensors of one group | conjunctive rule | the radar and the camera see the object on their own: distinct evidence |
| the received objects of one group | cautious rule | several neighbours may report the same object, and they may have got it from the same place |
| the on-board result and the received result | conjunctive rule | distinct |

**The conjunctive rule** is Dempster's rule without the normalisation. Each product m1(B) m2(C) goes to the
intersection of B and C. What falls on the empty set is the conflict between the two sources. It stays there.
For two supports s1 and s2 of "the object exists", the fused support is 1 - (1 - s1)(1 - s2): the supports add up.

**The cautious rule** (Denoeux, Artificial Intelligence, 2008) is for sources that may share their information.
Every mass function with some mass on the whole frame is a conjunctive combination of generalized simple mass
functions, one per set A, with a weight w(A) (Smets, 1995). A weight below 1 is support for A. A weight above 1 is
the opposite, and the piece is then not a mass function on its own: that is why it is called generalized. The cautious rule takes, for each set, the smaller of the two
weights. The combination of a mass function with itself gives it back. For two supports s1 and s2 of "the object
exists", the fused support is the larger of the two. A report that reaches the vehicle through two neighbours
counts once.

**Dempster's rule** is also in the code (`belief.dempster`): the conjunctive rule, then the conflict is removed
and the rest rescaled. `belief.normalize` does the second part on its own.

## Why the conflict is kept

The plan asks for the conflict as a diagnostic. So the fused mass functions keep it as the mass of the empty set
(the open-world reading of the Transferable Belief Model), and `conflict_class` reports it. Belief and
plausibility are read on the mass function as it is. The pignistic probability leaves the conflict out and rescales.

With the existence evidence of task B4 there is never any conflict on the existence frame: every source supports
"the object exists" or says nothing. The check fails if some appears. Conflict appears on the class frame when two
sources name different classes. Our runs hold cars only, so it is 0 today.

## Two settings

- `CombinationModel(cpm_rule="conjunctive")`: Dempster only for the received objects. It is the ablation B4 of
  the plan. The check of a run measures it next to the cautious rule.
- `CombinationModel(min_ignorance=1e-9)`: the cautious rule needs some mass on the whole frame. A received
  object with a certainty (score 1 and reliability 1) gets this much, taken from its other masses in proportion.
  With the scores of task B4 (0.99 at most) it never happens.

## What the measures say

The fused belief that the object exists is compared with the truth. A fused object is "really there" when two
things hold: all its members name the same true object, and this object is within 1.5 m of the place of the newest
member. Before B7, this place stands for the place of the fused object. An object with members that name
different true objects is a wrong association: it counts as not there. Calibration error and Brier score as in `docs/discount.md`.

Four ways of the plan are measured on the same groups:

| Way | Trust | Rule for the received objects |
| --- | --- | --- |
| B0 full trust | none | conjunctive |
| B1 cutoff at 1 s | dropped above 1 s | conjunctive |
| B3 the proposed one | discount by age | cautious |
| B4 ablation | discount by age | conjunctive |

### The run with the AoC sensors (60 s, 20 cars, one seed)

Each cell gives the calibration error, then the Brier score. The motion value of the curve is the assumed one (4.0).

| Memory of the receiver | Fused objects | With several senders | B0 full trust | B1 cutoff | B3 cautious | B4 Dempster only |
| --- | --- | --- | --- | --- | --- | --- |
| no limit | 698 895 | 10.8% | 0.852, 0.844 | 0.030, 0.030 | 0.018, 0.027 | 0.020, 0.027 |
| 5 s | 220 611 | 34.2% | 0.568, 0.564 | 0.079, 0.079 | 0.044, 0.071 | 0.050, 0.070 |

- No wrong group: the association never put two true objects together.
- On the fused objects, the discount with the assumed value is 40% to 44% below the cutoff on the calibration
  error, and 10% below on the Brier score. One object at a time (B5), it was 0% to 9% below. The reason was measured
  with a memory of 5 s on four vehicles. The fresh reports of an object absorb its old ones. So 62% of the fused
  objects are a lone report older than 1 s, and these are really there in 10% of the cases. The discount gives them a belief
  of 0.04 on average, the cutoff 0. The fused objects with a fresh member (37%) are really there in 96% of the
  cases; the discount gives them 0.96, the cutoff 1.00.
- The cautious rule and Dempster only differ little here: on the objects with several senders, Dempster only
  gives 0.025 more belief on average (at most 0.43). The reason: the received reports of one object are mostly
  old, so their supports are small, and the sum of small supports is close to their maximum. The gap will be
  larger with fresh reports from many neighbours.

### The run with the example sensors

Every object is fresh. 99.3% of the fused objects have several senders. The four ways give a calibration error
of 0.001 to 0.005 and a Brier score of 0.001: nothing to tell apart, as expected where nothing gets old.

### Limits

One run, one seed, one traffic, and the fused objects are judged at the place of their newest member (B7 will
give the fused place). The numbers show a direction, not a result.

## What the tests check

- Tables 2 and 3 of Denoeux (2008): the weights, the cautious combination, the normalised cautious combination
  and the conjunctive combination give the printed values.
- The conjunctive rule is compared with a brute-force sum over all pairs of sets. Commonalities multiply.
- The weights of a conjunctive combination are the product of the weights. A simple mass function has one weight
  below 1, on its own set. The weights of random mass functions go back to the mass functions.
- The cautious rule is commutative, associative and idempotent, and its weights are the minimum of the weights.
  Three copies of one report give the report back. For two supports of the same set it keeps the larger one.
- The fusion of a group is checked by hand on small tables: on-board only, received only, both, several groups,
  classes in conflict, the ablation.
- The check of a run is counted again object by object with plain formulas, and a rule broken on purpose makes it fail.
- The conjunctive rule and Dempster's rule give the same values as the library `dst-py` of Godwin (where it is
  installed). Its cautious rule was not used as a referee. On Table 2 of Denoeux (2008) it returns an empty mass
  function. On two simple supports of {E} it returns 0.5 on E and 0.5 on notE.
