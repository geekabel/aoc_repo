# Synthetic streams: runs whose right answer is known

Task B9. The module is `aoc.io.synthetic`. The tests are in `tests/test_synthetic.py`. The gate M2 of the plan asks
that "pipeline v1 passes unit tests on synthetic streams": this is them.

## Why

A run from Artery is realistic, but nobody knows the right fusion result on it. The checks of B2 to B8 count
things again with the modules themselves, or compare ways with each other. A synthetic stream is the opposite:
a few cars on straight lines, an on-board sensor with a known range, messages with a known delay and a known
loss, a car that leaves at a known time. On such a stream the pipeline must give the numbers that the formulas
of the theory note give, to the last digit where the stream allows it.

## Make a stream

```bash
aoc-synthetic OUT_FOLDER                              # the crossroads scenario, 20 s
aoc-synthetic OUT_FOLDER --scenario lossy --duration 30 --delay 0.1 1.5 --loss 0.3 --seed 4
aoc-evaluate OUT_FOLDER                               # every command of the pipeline runs on it
```

```python
from aoc.io.synthetic import Car, Scenario, make_run, write_run

cars = (Car("a", vx=10.0), Car("b", x=40.0, vx=10.0), Car("c", x=80.0, vx=10.0, t_out=8.0))
run = make_run(Scenario(cars, duration=15.0, delay=(0.1, 1.0), loss=0.2))     # a Run, as load_run gives one
write_run(Scenario(cars), "runs/synthetic")                                 # observations.parquet, truth.parquet, export_summary.json
```

| Setting | Meaning | Default |
| --- | --- | --- |
| `Car(name, x, y, vx, vy)` | a true object, which is also a vehicle with a sensor and a radio | |
| `t_in`, `t_out` | when the car enters and leaves the simulation | 0, never |
| `cls` | class of the observations table | passenger_car |
| `turn` | turn rate in degrees per second | 0 |
| `wobble` | intensity q of a random acceleration, in m^2/s^3 (the motion model of the alignment) | 0 |
| `sends` | whether the car sends CPMs | True |
| `duration`, `step` | end of the run and step of the truth and of the sensors, in s | 20, 0.1 |
| `sensor_range` | the on-board sensor sees every other car within this distance, in m | 60 |
| `sensor_name` | the name of the sensor in the rows; a name of the sensor tables (Camera, Radar, Lidar) gets their misses and their reliability | radar |
| `weather` | the weather written in the rows and used by the sensor tables (`sensors.md`) | none (clear) |
| `cpm_period`, `cpm_range` | one CPM per car every so many seconds, to the cars within this distance | 0.1, 400 |
| `delay` | age at reception in s: one value, or a pair (low, high) drawn uniformly | 0.1 |
| `loss` | probability that one copy of a CPM is lost | 0 |
| `noise`, `seed` | the noise model of the replay; the seed of the delays, the losses and the wobble | sigma 0.3 m and 0.5 m/s; 0 |

A sender reports what its sensor sees at the time of sending, the receiver included (the replay drops the rows
about the receiver itself, as with Artery). The table on disk holds exact states, like an export: `make_run` and
`load_run` add the noise. A copy that would arrive after the end of the run, or after its receiver left, is
never seen. The channel busy ratio is a constant (0.05). The delivery ratio of a row is the share of the last
10 messages of its sender to this receiver that arrived (the exporter counts over a window of 1 s instead; the
two agree at 10 Hz). `t_in` and `t_out` must be multiples of the step.

One change in the replay (B2) came with this task: a vehicle that leaves the simulation is replayed until its
last true step, not until the end of the run. Before, its later ticks had no vehicle state and the replay check
failed on such a run.

Named scenarios: `crossroads` (six cars on two roads that cross), `convoy` (five cars 25 m apart, same speed),
`leaver` (the convoy, one car leaves at 8 s), `lossy` (the convoy with 2 Hz messages, delays of 0.1 to 1.5 s,
30% loss).

## What the tests check

The stream itself:

- The tables follow the schema of the exporter, column by column and type by type. On the convoy the number of
  sensor rows and of CPM rows is counted by hand (the cars see 2, 3, 4, 3, 2 others).
- A car that enters late and leaves early has no row before or after, measured or received, and its replay stops
  when it leaves; the replay check of B2 passes on the four scenarios (20 s); the same seed gives the same stream.

The pipeline, formula by formula:

- **Relay.** Two senders report one car to a receiver that cannot see it. At every tick the two reports form one
  group and the fused belief is, to 1e-9: the curve itself for the AoC law with the cautious rule
  (0.99 alpha(age), with `alpha` of `aoc.theory.reliability`); 1 - (1 - 0.99 alpha)^2 with Dempster;
  1 - 0.01^2 with full trust; 0 with the cutoff when the delay is above 1 s. The ages are exactly the delay: a
  message measured at t - delay arrives at t. The fused state of two equal covariances is the mean of the two
  states (covariance intersection). Through the whole evaluation (`evaluate_run`) the calibration errors of
  B1, B3 and B4 are the same numbers, and the one of B0 is at most 0.01^2.
- **Leaver.** One sender; a car leaves at 8 s. From 9.1 s on its reports are stale. Full trust gives them 0.99,
  the cutoff 0, the AoC law exactly the mean of the curve over the ages 1.1 to 8 s (to 1e-4). With every car
  sending, the old reports are mostly too vague to be grouped (the coincidence test of the association) and
  count one by one; the stale belief of B3 stays between the smallest and the largest value of the curve, and
  below half of the one of full trust (H3).
- **Age law (task A3).** Periodic messages at 2 Hz with 30% loss and a fixed delay, 150 s: the mean age of the
  newest known report, over all ticks, matches delay + (T - step) / 2 + T p / (1 - p) within 4%. The step correction
  is there because the ticks sample the phase at 0, 0.1, ..., 0.4 s. The delivery ratio read on the delivered
  rows is (1 + 9 (1 - p)) / 10, not 1 - p: a delivered message always has one success in its window.
- **Convoy.** Fresh reports, cars that never leave: every way has an awareness above 99%, no ghost, no wrong
  group, a calibration error of 0.01 at most (1 - 0.99), and the Kalman way (B2) beats covariance intersection
  on the position by more than 20%: the measurement noises are independent, which is its case.
- **Calibration when the motion follows the model.** Five cars with a random acceleration of intensity q equal
  to the motion value of the class, messages every 0.5 s with delays of 0 to 2 s and 20% loss. The curve is
  exact for this stream: B3 has a calibration error below 0.03 and full trust above 0.5. On straight lines
  (q_true = 0) the curve is too careful: coverage 100%, calibration error above 0.1 and worse than full trust.
  This is the risk of H1 found on the Artery runs (B5), reproduced on a stream where the cause is known.

The command: it writes a run that `load_run`, `aoc-replay` and the others read; its errors return 2.

Four mutants were tried against these tests: the curve with q age^3 / 2 instead of / 3, a loss never applied,
B3 evaluated with Dempster instead of the cautious rule, the stale limit ignored. Each one fails at least one test.

## Limits

The cars move on straight lines or with a random acceleration; no real traffic, no occlusion, no sensor
failure. Every reported object is real, as in Artery. The streams are small (five or six cars, 10 to 150 s), so
the whole file runs in one to two minutes.
