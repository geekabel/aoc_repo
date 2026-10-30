# The sensor models and the weather tables

Task C6. The plan asks for sensor defaults (camera, radar, lidar), a detection probability per weather, and a
reliability of each on-board sensor from a (weather, range bin) table for the contextual discounting. This
page says where each part lives and what the assumed values are. The code is in `aoc.fusion.sensors`; the
replay (`aoc.fusion.replay`) applies the detection, the discount (`aoc.fusion.discounting`) the reliability.

## Where each part lives

| Part | Where | What |
| --- | --- | --- |
| The cones | Artery, `artery-ext/config/sensors-vru.xml` and `artery-ext/vru/omnetpp.ini` | a range, an opening angle, a mounting point, a line-of-sight check against the buildings and the other objects. Deterministic: no miss, no false alarm, no weather |
| The detection probability | Python, at the replay (`load_run`) | the sensor rows that a sensor misses in the weather of the run are left out; the CPM rows whose sender missed the object with them |
| The reliability alpha_i | Python, in the discount (`TrustModel`) | multiplied into the reliability of an on-board object, by weather and range bin |

The two Python parts follow the A8 decision for the noise: on the logs, changeable without a new simulation,
repeatable with a seed. The misses are tied to the measurement: the same seed gives the same misses, whatever the
order of the rows, and a subset of a run gives the same misses as the full run (the keys of the noise model,
another stream).

## The tables

The detection values are assumed. They are not fitted to data. The plan fits them on hold-out runs later
(E3); `SensorTables.to_json` and `from_json` carry a fitted table, and `aoc-sensors` prints or writes the
defaults. The reliability is 1 by decision (below).

| Sensor | Range | Bins (m) | Detection, clear | Detection, rain | Reliability (clear and rain) |
| --- | --- | --- | --- | --- | --- |
| Camera | 80 m, 120 degrees | 0-30-60-80 | 0.98, 0.95, 0.85 | 0.85, 0.70, 0.45 | 1, 1, 1 |
| Radar | 150 m, 120 degrees | 0-50-100-150 | 0.97, 0.95, 0.90 | 0.95, 0.92, 0.85 | 1, 1, 1 |
| Lidar | 100 m, 360 degrees | 0-40-70-100 | 0.99, 0.97, 0.90 | 0.90, 0.75, 0.55 | 1, 1, 1 |

The reasons for the detection values, one line each: a camera falls first in rain and at long range; a radar hardly minds the rain and
keeps its range; a lidar is the best in the clear and loses the most in rain (the beam scatters on the drops).
A bin holds the distances up to its upper edge; an object beyond the last bin, or whose distance is unknown,
takes the values of the last bin. The distance is the true distance between the station that made the
measurement and the object, at the measurement time (`range_m` in the rows). It is ground truth that the fusion
reads on purpose: it stands for the range the sensor measured, which the discount of the plan uses by bin, and
the noise of the replay (0.3 m) would not move an object across a bin of 20 to 50 m.

What the two tables stand for: the detection probability is the chance that the sensor sees the object at all
(a missed detection); the reliability alpha_i is the trust in a report the sensor did produce, and stands for
what Artery does not simulate (wrong sizes, wrong classes, ghosts near the edge of the range). They are not the
same thing and both are in the plan.

**The reliability is 1 in every bin** (decision of task C7, Oct 30, 2026). Artery makes no ghost and no wrong
class: every report is true, so any reliability below 1 is pessimistic on its logs. The first guesses of C6
(camera 0.97, 0.95, 0.90 in the clear and 0.90, 0.80, 0.70 in rain; radar 0.95, 0.93, 0.90 and 0.93, 0.90,
0.85; lidar 0.98, 0.96, 0.92 and 0.90, 0.82, 0.72) made the proposed law under-confident on the pilot run of C5:
calibration error 0.040 against 0.010 for the Bayesian way in the clear, 0.094 in rain, and a time to
confidence of 5.7 s against 3.3 s, because a report of the radar alone (the only sensor beyond 100 m) got
0.99 x 0.90 = 0.89, under the target of 0.9, before any age discount. With the reliability at 1 the same run
gives 0.016 for the proposed law against 0.010 and 0.017 for the Bayesian way and the cutoff. The plan calibrates
this table on hold-out runs; on Artery logs that calibration gives 1. The mechanism stays, for a sensor model
with false alarms or for real data; the guesses are kept in `GUESSED_RELIABILITY` and `with_reliability` for the
tests and as an example of a table. Two things follow for the papers: the reliability table is the identity in
every reported result, and the rain subset acts through the detection table only (fewer rows, more missed
objects); and the 0.89 against 0.9 of the guesses is also a matter of the target (`confident_belief` = 0.9 in the
metrics), not of the idea of a table.

A sensor that the tables do not name (the four radars of the Artery example, the `radar` of the synthetic
streams) is never missed and has the reliability `sensor_default` of the trust model (1).

## The detection at the replay

`load_run(folder, sensors=True, weather=None)`:

- the weather is the one the exporter wrote (`aoc-export --weather rain`, column `weather` and
  `export_summary.json`), else clear; `weather=` forces it; `aoc-replay --weather rain` and the same option on
  `aoc-discount`, `aoc-combine` and `aoc-evaluate`;
- a sensor row is kept with the probability of detection of its sensor at its distance, one draw per measurement
  (every copy of a measurement gets the same draw);
- a CPM row is kept when its sender kept at least one sensor row about the object at the measurement time
  (within 1 ms: a CPM declares that time in whole milliseconds, and the CAM sensor of the Artery example logs
  its rows a fraction of a millisecond away): the sender cannot send what it did not see. When the sender has
  no sensor row about the object at that time, the row is kept with the probability that one of the sender's
  own sensors of the table in range sees it; a sender whose sensors are not in the table is never missed; a
  sender without any sensor row is taken to carry every sensor of the table;
- `run.detection` holds the counts: sensor rows and CPM rows dropped, CPM rows without a sensor row of the sender.

`sensors=None` keeps every logged row; `SensorTables(seed=1)` gives other draws; `SensorTables.from_json(path)`
loads a fitted table. The synthetic streams take `sensor_name="Camera"` and `weather="rain"` to use the tables.

## The reliability in the discount

`TrustModel(sensor_tables=DEFAULT_TABLES, weather=None)`: the reliability of an on-board object is multiplied by
alpha_i of its sensor in the weather at its `range_m`. `TrustModel.for_run(run)` takes from the run what was not
set by hand: the weather when none was set, and the tables when the model still has the default ones (a run
loaded with `sensors=None` then gets no table reliability either: the misses and the reliability go together).
The evaluation, the discount check and the combination check call it. `TrustModel.sensor_reliability` (a
constant per sensor) wins over the tables; `sensor_tables=None` switches them off; `sensor_default` serves a
sensor that nothing names. On the command line, `--weather` and `--no-sensor-tables` on `aoc-replay`,
`aoc-discount`, `aoc-combine` and `aoc-evaluate`.

On a convoy of synthetic cars 30 m apart with a camera of 80 m, in rain, the sensor keeps 85% of the rows about
the next car and 70% about the one after, the CPM rows about a car are gone exactly when its sender missed the
car, and with the guessed reliability table the belief of B3 in a locally seen car at 30 m is
0.99 x alpha(age) x 0.90 instead of 0.99 x alpha(age).

## What is not here

- Weather in Artery (the radio, the sensors): nothing changes in the simulator; a rain run is a clear run replayed
  with `--weather rain`. So the cars of a rain run brake and send as in the clear. The plan's rain subset (E5) is
  a replay subset.
- A false alarm (a ghost object): none, since Artery makes none; the reliability table stands for it.
- The fit of the tables on hold-out runs: E3.

## Tests

`tests/test_sensors.py`: the tables of the plan and their shape (worse with the distance, worse in rain, the
bins), a bad table refused (and the same weathers for every sensor, a read-only table), the JSON round trip and
the command, the distance between the station and the object, the kept share per bin against the table on a
synthetic convoy, the CPM rows against the sender's kept rows, the misses tied to the measurement (same seed,
other seed, a subset), a sensor the tables do not name, a CPM without a sensor row of its sender (every sensor
of the table, the sender's own sensors, a sender with no sensor of the table), a sensor row a fraction of a
millisecond away, the two sample runs of Artery untouched, the draws uniform and independent of the noise,
`load_run` with the weather of the export and with a forced weather, the reliability of a sensor row through
`align` and the discount (the tables, a constant, no tables, no distance, the weather of the run), the
reliability lower in rain and the evaluation under both weathers, `for_run` with the tables of the run, the
tables with the noise off.
