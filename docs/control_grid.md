# The control grid (task E2)

The control grid compares the AoC generation rule (E1) with the rules of ETSI at equal conditions, over a range of
channel loads. It gives the data of H4 (the target confidence reached with at least 20 % less channel load, or
more time above the target at equal load) and of figure F9 (the time above the target against the channel load,
one point per configuration, the AoC points expected to dominate). It is the configuration `control` of
`artery-ext/grid/omnetpp.ini`, run by `bash d1_grid.sh --control`.

## The design

The road, the events and the densities are those of the core grid (D1). Four factors, in the order of the run
numbers (`run = (((d * 2 + p) * 5 + r) * 3 + l) * 4 + seed`, `aoc.analysis.grid.CONTROL`):

| factor | levels | what it is |
| --- | --- | --- |
| density | low, high | 250 and 700 vehicles per hour on each of the north, east and west approaches (about 15 and 40 cars) |
| pen | 25, 100 | the share of the cars with V2X, in percent |
| rule | etsi, etsi_abs, aoc, aoc2, aocdev | the generation rule of the CP service (below) |
| load | 0, 12, 36 | the load stations around the junction (below) |
| seed | 0 to 3 | the repetition; every seed is an evaluation run (no calibration run here) |

2 x 2 x 5 x 3 x 4 = 240 runs of 330 s. The analysis leaves out the first 60 s, as in the core grid. The seed sets
the traffic (the departures and the routes) and the random choices of the simulation. The rules of the same cell
and seed see the same traffic, so the analysis pairs the rules by seed. The 60 runs of a seed cover the whole
design: `--seeds 0,1` runs a first pass over every cell (120 runs) before the seeds 2 and 3.

**A car has V2X or nothing.** In the core grid every car runs CA (CAMs) and a share runs CP (CPMs). A receiver
there knows every car fresh from its own CAMs whatever the CPM rule does, and the only objects that CPMs alone
cover are the three pedestrians of the events. The AoC rule changes the CPMs only, so its effect has to be
measured on the objects that only CPMs can cover. In the control grid the equipped cars are drawn in the route file
(`control_<density>_p<pen>.rou.xml`, written by `tools/make_grid_scenario.py`): on each approach two flows,
`eq_<leg>` at the share of the penetration and `bg_<leg>` at the rest (two Poisson flows together are a Poisson
flow at the density). `services-control.xml` gives CA and CP to the `eq_` cars and the actors of the events by
name. The `bg_` cars, the truck and the pedestrians have no station: they are the objects collective perception
serves, and the objects of the receiver metric (`aoc-rulecheck`, the objects without V2X). The busy ratio of the
control grid at a penetration is therefore not the one of the core grid at the same penetration (fewer CAMs). H4
compares the rules inside the control grid, at the same conditions.

**The rules.** `AocCpService` with `generationRule` and the parameters of `AocCpService.ned`:

| rule | what it does |
| --- | --- |
| etsi | `CpService` of Artery untouched: the rules of TS 103 324 as Artery applies them (the position change of an object taken relative to the sender; an empty CPM at every step) |
| etsi_abs | the rules of ETSI with the position change on the absolute position of the object, a CPM only when something is due |
| aoc | the AoC rule, n = 1: each object sent with the period (age limit - delay) / 1 of the curve of its report |
| aoc2 | the AoC rule with n = 2: twice as often, the receiver dips under the target only when two reports in a row are lost |
| aocdev | the AoC rule with n = 1 and the deviation trigger at half the tolerance: the object is also sent when the receiver's prediction is off by 0.75 m |

All with DCC (`withDccRestriction = true`), a generation step of 0.1 s and a CPM at least every second.

**The load stations.** The equipped cars cannot load the channel themselves. Their DCC holds each station to
about 16 packets per second, and the CAMs and the CPMs take most of it (asking 25 extra packets per second from
every car raised the busy ratio of the high/100 cell from 0.12 to 0.15 only; the gatekeeper dropped the rest).
The load comes from static stations around the junction (`load_<n>.xml`, Artery's `StaticNodeManager`, on a grid
of 80 m centred on the junction, the closest first). They run only the load source of the extension
(`AocLoadService`: 25 packets of 300 bytes per second, single hop, DP3) without DCC (`NoRateControlDccEntity`), so
they do not back off. They stand for the other applications and the other stations that load the channel, and
they hear each other: with 36 stations the load alone fills about half of the channel. What the levels give (60 s,
high density, 100 %, the rules of ETSI, in the sandbox of this session, `bash d1_grid.sh --control-load`):

| load stations | packets/s added | busy ratio (mean, p95) | CPM rate per car | time of the run against no load |
| --- | --- | --- | --- | --- |
| 0 | 0 | 0.12, 0.17 | 5.9 Hz | 1.0 |
| 12 | 300 | 0.24, 0.28 | 5.0 Hz | 1.6 |
| 24 | 600 | 0.37, 0.41 | 2.0 Hz | 2.0 |
| 36 | 900 | 0.52, 0.55 | 1.2 Hz | 3.3 |

With 12 stations DCC starts to act on the cars. With 36 the channel is near the DCC target (0.59) and the ETSI CPMs
are down to 1.2 Hz. The control grid takes 0, 12 and 36: a free channel, a loaded one, a saturated one. At the low
density and 25 % the stations give lower ratios (fewer cars): F9 gets a range of busy ratios from 0.02 to 0.55.

**The load side of H4 and F9.** With no load station the busy ratio is the one the stations make, so H4 reads it
as the load (the first verdict of `aoc-rulecheck --against`). With 12 or 36 stations the load stations set most of
the busy ratio, and a rule that sends fewer CPMs changes it little. There the load side of H4 is the CPM bytes per
second of the rule (the second verdict, without the empty CPMs), and F9 reads as the time above the target at
about the same busy ratio within a load level: the AoC points win when they stand higher, or as high with fewer
bytes. The busy ratio at a load level is still printed, since DCC acts on the cars through it.

## Running it

```
bash d1_grid.sh --control               the 240 runs that are not done yet (stop and start again at will)
bash d1_grid.sh --control --runs 3,17-20
bash d1_grid.sh --control --seeds 0,1     the runs of the seeds 0 and 1 of every cell (120 runs)
bash d1_grid.sh --control --check       index the results folder again
bash d1_grid.sh --control-load          the four load levels once (60 s each), with the table above
```

The runs go to `<root>/d1/results/control`, one folder `aoc-control-<n>` per run, exported as each run ends
(`--receivers auto:8`: the actors of the events and 8 drawn equipped cars; `cpm_rx` keeps every receiver). The
index (`aoc-grid ROOT --design control`) counts the runs per cell and lists the missing ones (`aoc-gridcheck` is
for the core grid, not for these runs). The heavy cells (high, 100, 36 stations) take three to four times the time
of a cell of the core grid: about 55 to 60 CPU hours in all, 10 to 14 hours with 4 to 6 jobs.

## What the analysis needs next (E3)

- The receiver's curve with the stopped state (the replay's discount and alignment: a report with a speed under
  0.5 m/s is a stopped object, aged with the parked curve and predicted still), so that both sides use the same
  curve; without it the receiver ages a stopped car as a moving one and the sender's saving is scored as a loss.
- The metric of F9 from the raw logs (`aoc-rulecheck`: `alpha(age)` of the latest report, the objects without V2X)
  and from the replay (B3's belief); the Pareto scatter of the time above the target against the busy ratio, one
  point per rule and load, and the verdict of H4 per pair.
