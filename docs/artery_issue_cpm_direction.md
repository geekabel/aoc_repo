<!-- Draft of an issue for https://github.com/riebl/artery/issues  (prepared on 2026-10-12)
     Title to use: CpService: velocityDirection of perceived objects is written in tenths of a radian -->

**Summary**

In `CpService`, the field `velocityDirection.value` of a perceived object is written in tenths of a radian. `CartesianAngleValue` is defined in units of 0.1 degree (ETSI TS 102 894-2 V2.2.1). A heading of 249.8 degrees is sent as 44 instead of 2498.

**Where**

Commit e0ddd8c (master of 2026-07-30). The same lines are in master today.

`src/artery/envmod/service/CpService.cc`

```cpp
// line 458: heading is a vanetza::units::Angle, so value() is in radians
snap.headingDeg = vdpSnapshot.heading.value();

// line 1040: tenths of a radian go into the CPM
polar.velocityDirection.value = static_cast<long>(std::round(snap.headingDeg * 10.0));
```

**How to reproduce**

Scenario `scenarios/artery`, configuration `envmod`, with `artery.envmod.service.CpService` (port 2009) in place of the mock service, 60 s of simulated time. I logged every sent CPM from the `CpmSent` signal and compared the field with `VehicleDataProvider::heading()` of the same vehicle at the same step.

- 24257 perceived objects carry a velocity.
- The field equals `round(heading in radians * 10)` for 24257 of 24257 objects.
- The field equals `round(heading in degrees * 10)` for 0 of 24257 objects.
- All values are between 16 and 47. The valid range is 0 to 3599.

| True heading (deg) | Expected field (0.1 deg) | Field in the CPM |
| --- | --- | --- |
| 234.73 | 2347 | 41 |
| 249.84 | 2498 | 44 |

**A side effect to keep in mind for the fix**

`headingDeg` also feeds the orientation rule (rule 2d) and the utility function. These work today because the slips cancel each other:

- line 640 stores the history as `Angle(headingDeg * M_PI / 180.0)`,
- line 577 computes `std::abs(objectOrientation - history.orientation.degree())`, which gives back a difference in radians,
- line 609 compares it with `mMinGroundVelocityOrientationChangeThreshold.value()`, which is also in radians (the threshold is built from `par(...) * vanetza::units::degree`). The same holds for the priority thresholds in `calculateUtilityFunction` (lines 666 to 669).

If only line 458 is changed to degrees, the difference becomes degrees while the thresholds stay in radians. Rule 2d would then fire for a heading change of 0.07 degree.

**Suggested fix**

1. Line 458: store degrees, for example `snap.headingDeg = vdpSnapshot.heading.value() * 180.0 / M_PI;`
2. Compare in degrees at line 609 and in `calculateUtilityFunction`: convert the three orientation thresholds to degrees once in `initialize()`.
3. Line 1040 can stay as it is.

**Two related points**

ETSI TS 103 324 V2.1.1, clause 7.1.8.1, asks for an East-North-Up coordinate system centred on the reference position, and for polar angles "with positive values turning around the z-axis using the right-hand rule, starting from the x-axis".

- `velocityDirection` is filled from the heading, which is clockwise from north. With the rule above, it should be counter-clockwise from east.
- `yCoordinate` is `objectPos.y - egoPos.y` in OMNeT++ coordinates, where y grows to the south. An object north of the sender gets a negative `yCoordinate`. I checked the axis direction with the logged positions and headings: the displacement between two steps matches `vy = -speed * cos(heading)`.
