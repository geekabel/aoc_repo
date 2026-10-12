# The log files

The logger writes CSV files. The exporter (`aoc-export`) writes one parquet file per CSV file,
plus `observations.parquet` and `export_summary.json`.

| File (CSV and parquet) | One row is | Main columns |
| --- | --- | --- |
| `truth` | one object at one step of 0.1 s | t, obj_id (SUMO id), x, y, speed, heading_deg, length, width, station_type |
| `local` | one detection: a vehicle, one of its sensors, an object | t, node_id, sensor, category, obj_id, track_id, t_first, t_last |
| `cpm_tx` | one CPM sent | t_tx, tx_id, station_id, reference_time_ms, t_ref, ego_x, ego_y, n_objects, size_bytes |
| `cpm_obj` | one object of one CPM sent | station_id, reference_time_ms, object_id, meas_delta_ms, x_cm, y_cm, speed_cms, class_label, sensor_ids |
| `cpm_rx` | one CPM received | t_rx, rx_id, station_id, reference_time_ms, rx_x, rx_y |
| `cbr` | one channel load value of one vehicle | t, node_id, cbr |
| `nodes` | one vehicle with a middleware | node, node_id (SUMO id), station_id |
| `run` (CSV only) | one property of the run | key, value |
| `observations` (parquet only) | one object that a vehicle gets, from a sensor or from a CPM | see `observations_schema.md` |

## Conventions

- Times are simulation times in seconds.
- Positions are in metres, x to the east and y to the south (the frame of Artery). Headings are in degrees from north, clockwise.
- x and y are the centre of the object. This is the point that `CpService` puts in the CPM.
- A CPM is identified by the pair (`station_id`, `reference_time_ms`). The objects of a received CPM are not copied
  for each receiver: join `cpm_rx` and `cpm_obj` on this pair.
- The parquet versions add computed columns: absolute position of each CPM object, measurement time,
  true object behind it, delay, distance between sender and receiver.
- `export_summary.json` holds the counts and the checks of the export. The exporter ends with PASS or FAIL.

## The observations table

A sensor gives one row per step in which it detects the object. A CPM gives one row per object at the time of reception.
The columns are listed in `observations_schema.md`. Their description is also stored inside the parquet file.

One thing differs from the plan, on purpose: the values of `src`. The plan writes cam, rad, lid or v2x.
Artery has no camera or lidar model by default. So `src` holds the sensor category of Artery
(radar, fov, see_through, cam_msg) or v2x, and the column `sensor` holds the name given in the sensors file.
The value cam_msg means an object known from its CAM messages, not a camera.

Columns that stay empty today: the covariances (P11, P12, P22, Pv11, Pv22) wait for the noise model,
and `exist_score` waits for the evidence model (task B4).

## Read the files in Python

```python
import pandas as pd

run = "/tmp/run_aoc"          # after: aoc-export tests/data/run_aoc_sensors --out /tmp/run_aoc
obs = pd.read_parquet(run + "/observations.parquet")
truth = pd.read_parquet(run + "/truth.parquet")

# what one vehicle gets from CPMs, in time order
one = obs[(obs.rx_station == "flow0.0") & (obs.src == "v2x")]
print(one[["t_rx", "tx_station", "gt_match_id", "x", "y", "t_meas", "age"]].head())

# error of each row against the true state at the time of its position
err = obs.merge(truth, left_on=["t_pos", "gt_match_id"], right_on=["t", "obj_id"], suffixes=("", "_true"))
print(((err.x - err.x_true) ** 2 + (err.y - err.y_true) ** 2).pow(0.5).describe())
```
