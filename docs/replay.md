# The replay

A simulation run is made once in Artery and exported once. The fusion variants then play the same run
again in Python, as often as needed. This page describes the two modules that do it (task B2):
`aoc.fusion.noise` and `aoc.fusion.replay`.

## Check a run

```bash
aoc-replay RUN_FOLDER          # the folder with observations.parquet and truth.parquet
```

The command loads the run, applies the noise model, replays every vehicle and prints PASS or FAIL.
Options: `--sigma-x`, `--sigma-v`, `--seed`, `--period`, `--drop-unmatched`.

What it checks:

- every row of one measurement carries the same values;
- the error against the truth table has a mean of 0 and the spread of the model, and the errors on x, y, vx, vy are not linked.
  The true state is looked up again in the truth table by another path than the one of the noise model;
- every row of the table is delivered exactly once by the replay;
- an object is never twice in the known objects of a vehicle.

## Use it in Python

```python
from aoc.fusion.replay import load_run
from aoc.fusion.noise import NoiseModel

run = load_run("RUN_FOLDER", noise=NoiseModel(sigma_x=0.3, sigma_v=0.5, seed=1))
print(run.egos)                                   # the vehicles of the run

for tick in run.replay("flow0.0", period=0.1):    # one tick every 0.1 s
    tick.t            # time of the tick in s
    tick.ego_state    # true state of the vehicle itself: x, y, vx, vy, speed, heading_deg
    tick.local        # what its sensors measured at this tick
    tick.v2x_new      # the CPM objects received since the last tick
    tick.v2x          # the newest state known from each sender about each object
    tick.truth        # the true objects at this tick, for evaluation only
```

`tick.local`, `tick.v2x_new` and `tick.v2x` have the columns of the observations table (see `observations_schema.md`), plus:

| Column | Meaning |
| --- | --- |
| `age_now` | time of the tick minus the measurement time, in s. This is the age that the reliability uses |
| `gt_x`, `gt_y`, `gt_vx`, `gt_vy` | true state of the object at the measurement time (only with a noise model) |
| `gt_gap` | 0, or the distance in s to the nearest logged true state (only with a noise model) |
| `range_m` | true distance in m between the station that made the measurement (the receiver for a sensor row, the sender for a CPM row) and the object, at the measurement time (only with the sensor tables). The one column of ground truth a fusion may read: it stands for the range the sensor measured (`sensors.md`) |

`tick.truth` has the columns of the truth table. The vehicle itself is not in it: its state is in `tick.ego_state`,
which is None when the vehicle is not in the simulation at this tick.

Rule: a fusion may read every column except those whose name starts with `gt_`. They are the ground truth.
(`range_m` is true too, but stands for a measured range: the discount reads it by bin.)

Each table of a tick is a copy. You can change it freely: the next ticks are not affected.
Text columns hold Python strings, and None where a value is missing.

## The sensor tables

`load_run(folder, sensors=True, weather=None)` also applies the sensor tables of task C6 (`sensors.md`): the
sensor rows that a sensor misses in the weather of the run are left out (a detection probability by sensor,
weather and range bin), the CPM rows whose sender missed the object at the measurement time with them, and every
row gets its `range_m`. The weather is the one the exporter wrote (`aoc-export --weather rain`), else clear;
`weather="rain"` forces it. `run.weather`, `run.sensors` and `run.detection` (the counts) say what was done.
`sensors=None` keeps every logged row (and the discount then uses no table either, through `TrustModel.for_run`).
A sensor that the tables do not name (the four radars of the Artery example) is never missed, and neither are
the CPM rows of a sender whose sensors are not in the tables. The misses are tied to the measurement like the
noise: the same seed gives the same misses, and a subset of a run gives the same misses as the full run.
`run.dropped` counts the rows left out at loading only; the misses are in `run.detection`.

A vehicle that leaves the simulation is replayed up to its last true step. A CPM it received after that step, in
the last fraction of a second of its life, is never replayed; `check_run` counts these rows
(`v2x_rows_after_the_receiver_left`) and does not report them as missing. The occluded-VRU runs, where cars drive
off the map, are the first with such rows.

## The noise model

Why: in the Artery logs, the position of an object is exact, and it can be newer than the measurement
time that the source declares (finding 1 of A8). Decision of 2026-10-12: fix it in the replay.

For each row, the noise model:

1. takes the true state of the object at the declared measurement time `t_meas` (linear interpolation between two steps);
2. adds Gaussian noise: sigma_x on each position axis, sigma_v on each speed axis;
3. fills the covariance columns: P11 = P22 = sigma_x squared, P12 = 0, Pv11 = Pv22 = sigma_v squared.

Default values: sigma_x = 0.3 m and sigma_v = 0.5 m/s, the values of the theory note.
`NoiseModel(overrides={"cam_msg": (0.5, 0.2)})` gives other values to one origin of measurement (column `meas_src`).

**The noise belongs to the measurement, not to the row.** One measurement can appear in many rows:
a CPM object is received by many vehicles, and a sender can repeat one measurement in two CPMs.
All these rows get the same noisy values. The noise of a measurement depends only on the seed and on four things:
who measured, with what, which object, and when.

| Row | Who | With what | Object | When |
| --- | --- | --- | --- | --- |
| sensor row | the vehicle | the name of the sensor | its track id | `t_meas` |
| CPM row | the sender | "cpm" | the object id in the CPM | `t_meas` |

Consequences:

- The same seed gives the same values on any machine, up to the last digit (about 1e-15).
  Compare two runs with a tolerance, not with `==`.
- A part of a run gives the same values as the full run. The order of the rows does not matter.
- Another seed gives another, independent set of values.

Assumptions. Each one can be changed later, and each one is a line to write in the papers:

- The noise is white: two measurements of one object at two times get independent errors. A real tracker gives errors that are linked in time.
- The declared covariance is the true covariance of the noise. The source is honest.
- The error has the same size in all directions, with no link between position and speed.
- A CPM object is one measurement made by its sender. It is independent of the sensor rows of this sender.

One limit to keep in mind for the data incest study. With the sensors of the Artery example, one CAM is relayed
by several senders in their CPMs. Each sender then gets its own independent error. The shared origin of these copies
is not in the noise, so a fusion of them looks better than it should. The AoC runs use radars only and are not concerned.

Rows that cannot get noise. A row without a true object, or without a time, stops the loading with an error.
`load_run(folder, missing="drop")` leaves these rows out and counts them in `run.dropped`.

## The replay

`run.replay(ego)` gives one `Tick` per fusion time for one vehicle.

- A sensor row belongs to the first tick at or after its time `t_sim`.
- A CPM row belongs to the first tick at or after its reception time `t_rx`. It appears once in `v2x_new`.
- `tick.v2x` is the memory of the vehicle: for each pair (sender, object id), the row with the newest measurement time
  among all the CPMs received so far. Between two receptions the row stays, and its `age_now` grows.
  A late CPM with an older measurement does not replace a newer one.
  When a sender repeats one measurement in two CPMs, the last one received is kept.
- CPM objects that describe the vehicle itself are left out (`drop_self=True`). The loader finds them with the ground truth.
  A real vehicle would compare them with its own position.

Options of `replay`:

| Option | Default | Meaning |
| --- | --- | --- |
| `period` | 0.1 | time between two ticks in s. It must be a multiple of the step of the truth table (0.1 s) |
| `t_start` | first tick where the vehicle has a row | first tick. CPMs received before it count as known |
| `t_end` | last step of the run | last tick |
| `hold` | None | an object leaves `tick.v2x` when its age is above this value. None: it stays until the end |
| `drop_self` | True | leave out the CPM objects that describe the vehicle itself |

About `hold`. The object id in a CPM is the track id of the sender. When the sender loses an object and finds it again,
it uses a new id. The old id is never updated again, and it stays in `tick.v2x` with a growing age.
This is what a real receiver sees. The fusion decides what such an old object is worth: the baseline with a hard cutoff
uses `hold=1.0`, and the AoC variant gives it a reliability that falls with its age.

## Speed

On a run of 60 s with 20 vehicles (82,000 rows): load and noise 0.1 s, replay of all vehicles 3.3 s
(6270 ticks, about 0.5 ms per tick).
