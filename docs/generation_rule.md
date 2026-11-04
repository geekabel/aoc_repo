# The AoC generation rule in the Artery CP service (task E1)

The receiver side of the contribution (B3) discounts a received object with the reliability `alpha(age)` of its
report. The sender side turns the same curve around: how often must a sender report an object so that the
reliability at the receivers stays above a target? `AocCpService` (`artery-ext/aoc/`) implements that rule in the
collective perception service of Artery, next to the rules of ETSI TS 103 324, so that the control grid (E2) can
compare the two at equal conditions (H4: the target reached with at least 20 % less channel load, or more time
above the target at equal load; figure F9).

## The rule

The theory note (Step 4, the time above a level) gives the rule. For an object of class `c` the reliability of a
report of age `Delta` at the receiver is

    alpha(Delta) = 1 - exp(-eps^2 / (2 sigma^2(Delta))),   sigma^2(Delta) = sigma_x^2 + sigma_v^2 Delta^2 + q_c Delta^3 / 3,

with `eps` the tolerance (1.5 m), `sigma_x` and `sigma_v` the position and speed errors of the sender's report
(0.3 m, 0.5 m/s) and `q_c` the motion value of the class. The age limit `Delta_l` is the age at which `alpha` falls
to the target `alpha*` (0.9). A report arrives with the delay `d`, so the receiver is above the target for
`Delta_l - d` after each report, and the sender sends the object with the period

    T = (Delta_l - d) / n,

`n` reports per limit (n = 1: the reliability dips under the target only when a report is lost; n = 2: when two in
a row are lost). The service evaluates the rule at every generation step (every `minInterval` of 0.1 s, or the DCC
interval): an object goes into the CPM of this step when its last report would be older than `T` at the next step
(so it is sent at the last step at or before `T`); a newly seen object goes in at once. The age counts from the
measurement of the report (`measurementDeltaTime`), not from its sending.

Three things the note's rule does not say, and the service adds:

- **A stopped object** (speed below `stoppedSpeed`, 0.5 m/s) takes the curve of a parked vehicle of the note
  (`q` 0.001, `sigma_x` 0.5 m, `sigma_v` 0.05 m/s): its age limit is 7 s instead of 1.1 s. The cars waiting at the
  light are where the rule saves most against ETSI (which sends them at least every second, and at every step
  when the sender moves, see below). The receiver's curve must know the same state (the replay's discount takes
  `q` by class only today; the control grid must give it the stopped state too, or the receiver ages a stopped
  car as a moving one and the sender's saving is scored as a loss).
- **A cap on the period**, `maxPeriod` (4.5 s): a receiver forgets an object after its memory hold (5 s in the
  evaluation of the plan), whatever the curve says.
- **A CPM only when something is due**: an object, or the sensor information container (every second), and at
  least every `maxInterval` (1 s). `CpService` sends a CPM at every generation step even when it has nothing to
  say (an empty CPM of about 40 bytes); `emptyCpm = true` keeps that behaviour.

An option the control grid can switch on: with `deviationFraction > 0` the sender also includes an object when
the receiver's prediction (the last sent position moved at the last sent velocity) is off by that fraction of the
tolerance. That is what the sender knows and the age alone does not say (a maneuver). It is off by default: the
rule of the papers is the age rule.

The parameters are NED parameters of `AocCpService` (`generationRule`, `targetAlpha`, `tolerance`, `delayEstimate`,
`repeats`, `sigmaX`, `sigmaV`, `qByClass`, `stoppedSpeed`, `qStopped`, `sigmaXStopped`, `sigmaVStopped`, `maxPeriod`,
`deviationFraction`, `emptyCpm`); the defaults are the values above, with the fitted `q` of D3 (`results/d3/qfit.csv`:
passenger_car 0.269, heavy_truck 0.118, pedestrian 0.0252; cyclist takes the note's 0.98, the other classes the
car's value). With the fitted `q`, `d` = 0.1 s and `n` = 1 the periods are: car 0.97 s (sent at 0.9 s), truck
1.06 s (1.0 s), pedestrian 1.14 s (1.1 s), cyclist 0.77 s (0.7 s), stopped 4.5 s (the cap; 6.9 s without it). The
service prints them at the start of a run (`EV_INFO`).

The service also knows the loss of C1 (`dropProbability`) and three generation rules:

| `generationRule` | What it does |
| --- | --- |
| `etsi` | `CpService` of Artery, untouched: the rules of TS 103 324 (new object; moved 4 m; speed changed 0.5 m/s; heading changed 4 degrees; 1 s since the last inclusion; the VRUs every 0.5 s together) |
| `etsi_abs` | the same rules with the position change taken on the absolute position of the object. `CpService` compares positions relative to the sender, so a sender that moves sends every stopped object it sees at every step (a car at 50 km/h moves 4 m in 0.3 s). The quick run below shows it: ETSI sends the stopped cars every 0.3 to 0.4 s instead of every second |
| `aoc` | the rule above |

## How it is built into Artery

`checkTriggeringConditions`, `sendCpm` and `checkPerceivedObjectTrigger` are private in `CpService`.
`artery-ext/aoc/cpservice-protected.patch` makes them protected and virtual (nine lines of `CpService.h`, no change
of behaviour); the scripts apply it once (`patch_cpservice`, the original kept as `CpService.h.aoc-backup`) and
build `libartery_envmod.so` again. `AocCpService` overrides the three: the first two only change how often a CPM
goes out, the third holds the rule. The ETSI rule (`generationRule = "etsi"`) calls the methods of `CpService`.

The sender has no covariance of its own tracks in Artery (the CPM carries no confidence), so `sigma_x` and
`sigma_v` are the nominal values of the note, the same ones the replay's noise model gives the reports. The
sender does not know which receivers got its reports: the rule works on the age of its own last report and the
assumed delay `d` (the median delay of the grid is 0.06 to 0.08 s, 0.18 s in the heaviest cell).

## The check (`aoc-rulecheck`)

`aoc-rulecheck RUN_DIR [--against OTHER_RUN_DIR] [--target 0.9 --delay 0.1 --repeats 1 --q ... --max-period 4.5]
[--warmup 10] [--range 200] [--hold 5] [--objects unequipped|all] [--out DIR]` (`aoc.analysis.rulecheck`) reads the raw
logs of an exported run (`cpm_tx`, `cpm_obj`, `cpm_rx`, `truth`, `nodes`) and prints three tables:

- `senders`: per sender the CPM rate, the bytes per second, the bytes and the objects per CPM;
- `inclusions`: per class and state the period of the rule, the intervals between two inclusions of the same
  object by the same sender (median, p10, p90), the share at the period (within one step), shorter (the object
  changed state, or a new object id) and longer (the object was out of view);
- `receivers`: per receiver the share of the time an object within range (200 m) is known with a reliability at
  or above the target (`time_above_target`: `alpha(age)` of the latest report received, with the curve of the
  rule and the state of the report, 0 when no report is younger than the hold), the share of the time it is known
  at all (`known`), the mean channel busy ratio seen by the receiver. The objects counted are the ones without
  V2X (not in `nodes`): the pedestrians, and the cars without a station. In the core grid every car runs CA, so
  only the pedestrians count; `--objects all` counts every object but the receiver (the stations are then known
  through their CPMs only, since these logs hold no CAM). The control grid must give the cars without CP no CA
  either, so that the objects collective perception is for exist.

With `--against` the same tables of a second run are printed next to the first, with the ratios of the load (bytes
per second, CPM rate, channel busy ratio) and of the time above the target, and the verdict of H4 on the pair
(within one point). The Python mirror of the rule (`Rule`, `period`, `sent_at`) is what the tables compare with;
`tests/test_rulecheck.py` checks it against the theory module (`aoc.theory.reliability`).

`bash d1_grid.sh --control-quick` runs the configuration `control_quick` of `artery-ext/grid/omnetpp.ini` (three runs
of 60 s at low density and 100 % penetration: `etsi`, `etsi_abs`, `aoc`, with `services-control-100.xml`), exports
them and prints the check of the `aoc` run against the two others (`--objects all`, after a warm-up of 10 s).

## The quick run of Nov 4, 2026 (in the sandbox of this session, Artery e0ddd8c)

Low density, 100 % penetration, 19 senders, 50 s after the warm-up:

| rule | CPM rate per sender | bytes/s in all | objects per CPM | CBR | time above target (all objects) | moving cars sent every | stopped cars sent every |
| --- | --- | --- | --- | --- | --- | --- | --- |
| etsi | 6.4 Hz | 8374 | 1.31 | 0.050 | 0.867 | 0.2 to 0.3 s | 0.3 to 0.4 s |
| etsi_abs | 6.4 Hz | 8436 | 1.32 | 0.050 | 0.869 | 0.2 to 0.4 s | 1.0 s |
| aoc | 2.5 Hz | 3199 | 1.12 | 0.032 | 0.863 | 0.9 s | 1.0 to 4.5 s |

The AoC rule sends the moving cars every 0.9 s (88 % of the intervals at the period; 6 % shorter, a new object id
or a state change; 7 % longer, the object out of view for a while) and the stopped cars every 4.5 s when they stay
stopped (half of the intervals; 29 % end with the car moving again, which sends it at once). It sends 62 % fewer
bytes than ETSI (40 % of the CPMs) for a time above the target 0.5 point lower: the verdict of H4 on this pair is
PASS within the tolerance of one point, but this run is not the test of H4 (the objects here are the equipped cars
themselves, which a CAM covers in the real grid). `CpService` sends the stopped cars every 0.3 to 0.4 s because it
takes the position change relative to the moving sender; `etsi_abs` sends them every second (rule 2e) and the
moving cars a little more often, for the same number of CPMs (a CPM goes out at every step in both) and the same
load. The real test of H4 is the control grid (E2), with the objects the metric is for.

## What E2 must add

- The control scenario: the cars without CP without CA either (the equipped set drawn once in the route files),
  so that the receiver metric has its objects; a channel load factor that bites (the core grid stays under a
  busy ratio of 0.2: more senders, or a load generator); the configurations `aoc` with n = 1 and 2, with and
  without the deviation trigger, against `etsi` and `etsi_abs`; `aoc` with the assumed q as a sensitivity.
- The receiver's curve with the stopped state (the replay's discount), so that both sides use the same curve.
- The metric of F9 from the replay too (B3's belief instead of `alpha(age)` of the raw report).
