# Notes about Artery

What we learned about Artery during tasks A7 and A8. Read this before you use the logs.
Artery version: commit e0ddd8c (2026-07-30). Scenario: `scenarios/artery`, configuration `envmod`, 60 s.

## The four findings of A8

**1. The position in a CPM is the exact true position.**
`CpService` takes the position of an object from the simulator at the last step, without noise.
It does so whatever the measurement time that it declares for the object.
So the position error is zero in the logs. With the sensors of the example, 63% of the CPM objects
also declare a measurement time (`t_meas`) that is older than the position itself (`t_pos`).

Decision of 2026-10-12: fix both in the replay, in Python. For each row, the replay takes the true state at `t_meas`,
adds random noise with a chosen sigma_x and sigma_v, and fills the covariance columns.
The noise can then change without a new network simulation, and it is repeatable with a seed.
Default values: sigma_x = 0.3 m and sigma_v = 0.5 m/s, as in the theory note. It is built with the replay loader (task B2).

**2. With the sensors of the example, most CPM objects come from CAM messages.**
The example gives each vehicle a "CAM sensor". A vehicle known from its CAMs enters the local model, and then the CPMs.
AoC runs use `artery-ext/config/sensors-aoc.xml` instead (radars only).

| Quantity | Example sensors | AoC sensors |
| --- | --- | --- |
| Objects per CPM, average | 3.9 | 0.8 |
| CPM objects by origin | 84% CAM, 14% radar, 2% see-through | 100% radar |
| CPM objects measured before the reference time | 63% | 6% |
| Age of a CPM object at reception | median 0.12 s, longest 0.52 s | median 0.08 s, longest 0.39 s |
| Rows in observations | 405888 | 82069 |

The example sensors stay useful as a test case for the data incest problem.

**3. The direction field of a CPM object is in the wrong unit.**
`CpService` writes `velocityDirection` in tenths of a radian. The standard asks for tenths of a degree.
A heading of 249.8 degrees is sent as 44. The exporter does not use this field: it takes the speed value from the CPM
and the heading from the matched true object. The issue draft for the Artery repository is in `artery_issue_cpm_direction.md`.

**4. A run with other recording options is not bit-identical.**
With the same options, two runs give identical files. With the logger on or off, or with other recording options,
or on another machine, the number of received CPMs moves by 0.1 to 0.3%. The sent CPMs stay identical.
The cause is not found. It does not hurt the log-and-replay design: all fusion variants replay the same logs.

## Conventions of Artery

- Positions: x grows to the east, y grows to the SOUTH (the frame of OMNeT++).
- Heading of a vehicle: degrees from north, clockwise.
- The position given by the vehicle data provider is the front bumper. The position of an object in a CPM is its geometric centre.
  The logs use the centre everywhere.
- Reference time of a CPM: milliseconds since 2004-01-01. The simulation starts at the date of the middleware parameter `datetime`.
  With "2013-06-01 12:35:00", simulation time 0 is 297174900 s.
- Position of an object in a CPM: offset to the sender in centimetres. `measurementDeltaTime` is the reference time
  minus the time of the last detection, limited to plus or minus 2 s. It can be negative.
- The confidence fields of a CPM object are "unavailable" and the perception quality is always 15.
  So the column `sender_conf` is always 1 today.

## Facts about the CP service

- Module `artery.envmod.service.CpService`, BTP port 2009.
- Signals `CpmSent` and `CpmReceived` carry the full CPM. The logger uses them.
- The optional result vectors (`cpmStationId`, `cpmReferenceTime`) are recorded only with `result-recording-modes=all`.
- A receiver also gets CPM objects that describe itself, when the sender detects it. The replay has to handle these rows.
  In the sample run `tests/data/run_aoc_sensors`, 230 of the 667 CPM rows describe the receiver itself.

## Options on the command line

Patterns that start with `**` work for recording options:

```
--**.vector-recording=false
--**.middleware.**.vector-recording=true
--**.result-recording-modes=all
```

Patterns that start with `*.node[*]` did not select any vector in our tests. They do work for module parameters:

```
--*.node[*].middleware.services=xmldoc("services-cp.xml")
--*.node[*].environmentModel.sensors=xmldoc("sensors-aoc.xml")
```

## Order of the listeners

OMNeT++ calls the listeners of a module before the listeners of its parent. The logger listens at the top of the network.
So when it gets `EnvironmentModel.refresh`, the local models of the vehicles are already up to date for this step.
