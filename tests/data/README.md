# Sample logs

Two short runs of the Artery example scenario (`scenarios/artery`, configuration `envmod`, network `AocWorld`),
14 s of simulated time each. They are the CSV files of the logger, as written by Artery.

| Folder | Sensors | CPMs sent | CPMs received | Objects in CPMs |
| --- | --- | --- | --- | --- |
| `run_example_sensors` | sensors of the Artery example | 390 | 935 | 318 |
| `run_aoc_sensors` | `artery-ext/config/sensors-aoc.xml` | 390 | 943 | 232 |

The tests export both runs and check the counts above. Made on 2026-10-12 with Artery commit e0ddd8c and logger version 1.0.
