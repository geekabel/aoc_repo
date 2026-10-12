# The observations table

<!-- Written by `python -m aoc.io.schema`. Do not edit by hand: change OBS_COLUMNS in src/aoc/io/export.py. -->

One row of `observations.parquet` is one object that one vehicle gets at one time, either from its
own sensors or from a received CPM. Exporter version 1.1.

The first 27 columns are the ones of the Plan, in the same order. The other 6
were added during task A8.

| No. | Column | Type | Meaning |
| --- | --- | --- | --- |
| 1 | `run_id` | string | identifier of the simulation run (OMNeT++ run id) |
| 2 | `seed` | int32 | seed set of the run |
| 3 | `t_sim` | float64 | time of the row in s: the step for a sensor row, the reception time for a CPM row |
| 4 | `rx_station` | string | vehicle that gets the object (SUMO id) |
| 5 | `src` | string | source: v2x for a CPM, else the sensor category (radar, fov, see_through, cam_msg) |
| 6 | `tx_station` | string | sender of the CPM (SUMO id); empty for a sensor row |
| 7 | `obj_id` | string | identifier given by the source: CPM object id, or track id of the local environment model |
| 8 | `class` | string | class of the object as given by the source |
| 9 | `x` | float64 | position east in m |
| 10 | `y` | float64 | position south in m |
| 11 | `vx` | float64 | speed east in m/s |
| 12 | `vy` | float64 | speed south in m/s |
| 13 | `P11` | float64 | position covariance, xx in m2; empty until the noise model is defined |
| 14 | `P12` | float64 | position covariance, xy in m2; empty until the noise model is defined |
| 15 | `P22` | float64 | position covariance, yy in m2; empty until the noise model is defined |
| 16 | `Pv11` | float64 | speed covariance, xx; empty until the noise model is defined |
| 17 | `Pv22` | float64 | speed covariance, yy; empty until the noise model is defined |
| 18 | `exist_score` | float64 | existence score of the source; empty until the evidence model is defined |
| 19 | `sender_conf` | float64 | confidence declared by the sender, 0 to 1: CPM object perception quality / 15 |
| 20 | `t_meas` | float64 | time of the measurement in s, as declared by the source |
| 21 | `t_rx` | float64 | time at which the receiver gets the row in s |
| 22 | `age` | float64 | t_rx minus t_meas in s |
| 23 | `cbr` | float64 | last channel busy ratio measured by the receiver, 0 to 1 |
| 24 | `dcc_state` | string | state deduced from cbr with the limits of ETSI TS 102 687 Table A.1 |
| 25 | `pdr_window` | float64 | share of the CPMs of this sender received by this receiver in the last window |
| 26 | `weather` | string | weather of the run (option --weather) |
| 27 | `gt_match_id` | string | true object behind the row (SUMO id) |
| 28 | `sensor` | string | name of the sensor for a sensor row |
| 29 | `gt_dist` | float64 | distance in m between the row and the true object at t_pos |
| 30 | `t_pos` | float64 | time of the true state that the position comes from in s |
| 31 | `tx_station_id` | int64 | ETSI station id of the sender of the CPM |
| 32 | `reference_time_ms` | int64 | reference time of the CPM, ms since 2004-01-01 |
| 33 | `meas_src` | string | where the sender got the measurement of a CPM object from: an on-board sensor category, or cam_msg |

Positions and speeds use the axes of Artery: x grows to the east and y grows to the SOUTH.
