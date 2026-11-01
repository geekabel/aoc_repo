# The core grid (task D1, experiment E3)

The core grid is the set of runs that the papers are built on: the occluded-VRU intersection of C5 with
background traffic, under the three factors of the plan, 12 seeds per cell. This document says what a run is,
how the runs are numbered, how the script runs them on the workstation, what it exports, and how to see what is
done and what is missing. The analysis of the runs comes in D3 (the replay of the five ways on every run) and D4
(the results database).

## The cells

| Factor | Levels | How it is set |
| --- | --- | --- |
| density | low, medium, high | 250, 450, 700 vehicles per hour on each of the north, east and west approaches: about 15, 25 and 40 cars on the map after the warm-up. One SUMO route file per level (`artery-ext/grid/grid_<density>.rou.xml`) |
| pen (penetration) | 10, 25, 50, 100 % | the share of cars that run the CP service (`artery-ext/config/services-grid-<pen>.xml`): every car runs CA (Day-1 equipment, as in C5), and a car runs CP with the probability pen, drawn once with the random numbers of the car (Artery's `<penetration>` filter). The egos and the helpers of the events always run CP (a name filter, `or` with the draw); the truck and the background cars take part in the draw, so at 100 % the truck of each event sends CPMs about the pedestrian it stands next to, and at 10 % it does so in one event out of ten. C5 ran the truck with CA only; the grid cannot (Artery's filters cannot say "the egos, or a draw among the others but the truck"), and the analysis can tell the truck's reports apart by their sender |
| msg (message configuration) | etsi, 2hz, 10hz | the CP service of Artery: `etsi` is the generation rule of ETSI TS 103 324 with the DCC restriction; `2hz` and `10hz` send every perceived object in every CPM, every 0.5 s or every 0.1 s, without the DCC restriction |
| seed | 0 to 11 | OMNeT++ seed set and SUMO seed. Seeds 0 to 9 are the evaluation runs of the plan (360), seeds 10 and 11 the calibration runs for the motion value q (72), kept apart in every analysis |

3 x 4 x 3 = 36 cells, 432 runs of 330 s: a warm-up of 60 s (the background traffic fills the map in about 25 s),
then three light cycles of 90 s with one event each. The analysis leaves out the first 60 s.

The run number of OMNeT++ follows the order of the iteration lines of `artery-ext/grid/omnetpp.ini`: the density
outermost, then the penetration, then the message configuration, the repetition innermost:

    run = ((d * 4 + p) * 3 + m) * 12 + seed        d, p, m: the index of the level in the lists above

`aoc.analysis.grid` holds the formula both ways (`run_number`, `cell_of_run`, `cell_runs`) and the table of
the 432 runs (`aoc-grid x --list`). The script computes the same formula in bash for `--cell`. The index checks
on every exported run that the variables written by the logger (`density`, `pen`, `msg`, `seed`) are those of its
run number, so a change of the order in the configuration cannot go unnoticed.

The message configuration is written as three parallel iteration variables (`${incl=1,0,0 ! msg}` and so on):
they follow `msg` and add no run. The logger receives `msg` through its parameter `label`, so the run's variables
hold the name of the configuration and not only its four parameters.

## The event

Each run holds the event of C5 three times. An event keeps the timing of C5 and starts at the start of a light
cycle like C5 did (the cycle is 90 s, the north-south direction has the green from 0 to 42 s): at 90, 180 and
270 s. So the pedestrian comes out of the alley at 106.6, 196.6 and 286.6 s. The truck comes and stops on the
right lane before the zebra crossing for 50 s, the pedestrian comes out of the alley and crosses behind it, the
ego comes on the left lane and loses the pedestrian behind the truck, the three helpers see it from the other
side. The actors of event k are `truck_k`, `ped_k`, `ego_k`, `helper_a_k`, `helper_b_k`, `helper_c_k`, with the
departure times of C5 shifted to the start of the event (the ego departs 13.1 s before the pedestrian is out).

Two choices keep the event the same in every run, both checked with SUMO alone over the three densities and the
seeds 0 to 11:

- The south approach carries the event vehicles only. With background cars on it as well, cars ahead of the ego
  stop at the zebra crossing for the pedestrian and the ego queues behind them: 8 to 10 s late in some events at
  150 vehicles per hour already, and no arrival at all at 600. With the south approach free, the ego reaches the
  crossing 5.2 s after the pedestrian is out in every event (it stops there for the pedestrian for a few seconds,
  then enters the junction during the green). The road of the grid is also built without the turnaround
  connections at the dead ends (`netconvert --no-turnarounds`): with them, a southbound car arriving at the end
  of the south leg counted as a car ahead of the ego being inserted, and the insertion was late by up to 1.2 s.
- The events start at the start of a cycle. At another phase (a first draft had the pedestrian out at 95, 185
  and 265 s), the helpers coming from the north stood at the red north of the junction while the pedestrian
  crossed, and missed it in the third event; now helper_a and helper_c pass the crossing 14 to 16 s after the
  pedestrian is out, as in C5, while it crosses the carriageway (2 to 13 s after it is out).

What the density still changes about the event: the helpers a and c are inserted at 40 m on the north approach,
inside the background flow, so their insertion waits for a free spot (up to 3 s at the high density), and the
southbound background cars stop beside them at the crossing while the pedestrian crosses, so their view of the
pedestrian is sometimes blocked. The ego's approach is the same in every run; the density acts through the
channel load, the number of witnesses (the cars that leave the junction southbound pass the pedestrian like the
helpers do) and the helpers' arrival and view. The counts of cars are stable over the run up to 700 vehicles per
hour; at 800 and above the junction saturates and the queues grow to the end of the run.

`artery-ext/tools/make_grid_scenario.py` writes the route files and the SUMO configurations;
`python3 artery-ext/tools/make_grid_scenario.py --check` fails when the committed copies are out of date
(`tests/test_grid.py` runs it). The road itself is the one of C5 (`artery-ext/vru/vru.nod.xml`, `vru.edg.xml`,
`vru.con.xml`, `vru.poly.geo.xml`), built by netconvert (without turnarounds) and polyconvert on the workstation.

## The script

`artery-ext/scripts/d1_grid.sh` runs the grid on the workstation. It needs the installation of A6 and one run of
`a8_logger.sh` (the Python environment). It installs the AoC sources, writes the scenario in
`~/aoc-sim/artery/scenarios/aoc-grid`, builds the road, then runs the runs several at a time (one Artery process
per run number). Each run is exported as soon as it ends, and its CSV logs are packed with gzip. The results of
the grid live in one fixed folder, `~/aoc-sim/d1/results/grid`, with one folder `aoc-grid-<run>` per run.

```bash
bash d1_grid.sh --quick                  # one run of 60 s (low, 100, etsi): checks the chain, writes a dated folder
bash d1_grid.sh                          # the 432 runs that are not done yet; stop (Ctrl-C) and start again at will
bash d1_grid.sh --cell low 100 etsi      # the 12 runs of one cell
bash d1_grid.sh --runs 3,17-20,41        # these run numbers
bash d1_grid.sh --check                  # index the results folder again, export what is not exported, list what is missing
```

A run is done when its `parquet/export_summary.json` says `PASS` (the exporter writes a summary on FAIL too); a run
that is started again is deleted first. The runs of a sitting are listed in `exit-codes.txt` (`run
simulation_code export_code`) and `progress.txt`. A failed run gives a FAIL report with the hint, after the
index; `bash d1_grid.sh` again runs the failed runs only. One script at a time runs on the grid folder (a lock);
`--check` can run beside it: it exports only the runs whose simulation has ended. Options:

| Variable | Default | Meaning |
| --- | --- | --- |
| `D1_JOBS` | from the CPU count and the RAM (1.9 GB per job) | runs in parallel |
| `D1_RECEIVERS` | 8 | other equipped cars whose observations are exported, beside the actors of the events (see below) |
| `D1_SIM_TIME` | 330s | simulated time of a run of the grid |
| `D1_ROOT` | `~/aoc-sim` | the install folder of A6 |
| `D1_NTFY_TOPIC`, `D1_NTFY_SERVER` | empty | the relay of the report to the chat, as in the other scripts |

The exports run one at a time (a lock), because the exporter of a dense run at 10 Hz holds several GB in
memory; the simulations go on meanwhile.

## What is exported

The exporter writes every table of `docs/logs.md` for every vehicle (`cpm_tx`, `cpm_obj`, `cpm_rx`, `local`,
`cbr`, `truth`, `nodes`), so the channel load, the message rates, the ages at every receiver and the sensor
detections of every car are there for every run. The observations table (one row per receiver, per object and
per report) is the exception: at high density, 100 % and 10 Hz, every receiver gets about 400 CPMs per second of
about 10 objects each, and the table of all 40 receivers would hold about 50 million rows per run. The exporter
option `--receivers auto:N` keeps the observations of the actors of the events (every vehicle whose id is `ego`,
`helper_a`, `helper_b`, `helper_c` or `truck`, with or without `_<k>`) and of N other senders of CPMs drawn at
random with the seed of the run (the same draw every time the run is exported; when fewer than N cars sent a CPM,
the draw is among all the cars with a middleware); the summary lists them under `receivers`, and the checks
`local_rows_of_receivers` and `local_rows_all` say what share of the sensor rows the table holds. `local.parquet`
itself keeps every vehicle, so the timeline of an event (who sees the pedestrian when) can be read for every car.
The replay of D3 runs on the receivers of the observations table; the other tables answer the questions about
the channel.

Rough sizes, scaled from the pilot of C5 (60 s, 25 vehicles, 80 000 observation rows, 1.4 MB of parquet): a run
of the low density holds a few hundred thousand observation rows and some tens of MB of parquet; a run of the
heaviest cell (high, 100, 10hz: 40 senders at 10 Hz with about 10 objects per CPM, 39 receivers each) about
5 million `cpm_rx` rows, 15 to 20 million observation rows for the 8 drawn receivers and the 15 actors (each
present for about 40 s), a few hundred MB of parquet, and CSV logs of about 1 GB before gzip. The whole grid is of the order of 10 to 20 GB of parquet plus the packed logs.
`D1_RECEIVERS=4` halves the observations if the disk is short. The time of a run grows with the number of
transmissions times the number of receivers: minutes for the low cells, tens of minutes for the heaviest ones;
the whole grid takes from several hours to a day with 4 to 6 jobs. Run one heavy cell first
(`bash d1_grid.sh --cell high 100 10hz`) to measure the time and the disk on the workstation before the rest.

## The index

`aoc-grid ROOT` (the same code as the checker the script carries) writes one row per run, done or not: the cell,
the seed, the kind (evaluation or calibration), the exit codes of the script, the result of the exporter, the
simulated time, the rows of the main tables, the receivers, the size of the parquet files and whether the
variables of the run match its run number. It prints what is done and what is missing, cell by cell, and the
`--runs` list to run the missing runs. The exit code is 0 when the 432 runs are there and exported.

```
runs done: 9 of 432 (9 evaluation, 0 calibration)
cells complete: 0 of 36, started: 2
cells started and not complete (evaluation+calibration runs): low/10/etsi 8+0; medium/10/etsi 1+0
missing: 423 run(s): 8-143,145-431
to run them: bash d1_grid.sh --runs 8-143,145-431   (or bash d1_grid.sh alone: it runs what is missing)
grid: incomplete
```

The script writes the index to `d1_index.csv` and the missing run numbers to `d1_missing.txt` in the results
folder after every sitting.

## The quality check (task D2)

`aoc-gridcheck ROOT [--out runs.csv] [--events events.csv] [--cells cells.csv] [--jobs N] [--runs 0-11,36]` reads
every exported run once (the summary, the nodes, the truth, the sent and received CPMs, and the rows about the
pedestrians of the local and observations tables) and writes one row per run, one row per event (three per run)
and one row per cell.

The checks of a run, PASS when all hold: the export passed and its variables are those of its run number; the
run covers 329.5 s or more; the seven tables are there; three pedestrians (`ped_1` to `ped_3`); the 15 actors of
the events (the truck, the ego and the three helpers of each) have a middleware; the three egos and the nine
helpers sent CPMs; the share of the background cars present at least 2 s that sent at least one CPM is within
three binomial standard deviations plus 0.02 of the penetration (at least 0.97 at 100 %; with about 70 background
cars at the low density the tolerance at 10 % is 0.13, enough to tell 10 % from 25 %); the CPM rate of the
senders present at least 20 s matches the message configuration (2hz: 1.9 to 2.1 Hz and 10hz: 9.5 to 10.5 Hz for
the median and the 10th and 90th percentiles of the senders, whose generation is strictly periodic; etsi: a
median between 0.8 and 10.5 Hz, the rule of ETSI going from 1 Hz under the DCC to 10 Hz, so a run of another
configuration labelled etsi would pass this check); the mean number of cars after the warm-up is between half and
twice the expected count of the density (15, 25, 40).

The events are reported and counted, not checked: for each event, whether the ego saw the pedestrian with its own
sensors, then lost it (the first gap of at least 1.5 s, the rule of C5; without such a gap the longest one is
written down), the distances when lost and seen again, whether that is an occlusion in the sense of C5 (a gap of
1.5 to 6 s, lost within 100 m, seen again within 40 m), the CPM rows about the pedestrian the ego received during
the gap, from the actors of the event and from the other cars, the age of the first one, and the cars that saw
the pedestrian during four steps out of five of the gap (the witnesses). How many events of a cell have an
occlusion and a CPM in the gap is a result of the grid (it depends on the penetration and on the traffic), not a
defect. The table per cell gives the cars, the penetration as drawn, the CPM rate, the channel busy ratio (a mean
over the CPMs received after the warm-up), the received CPM rows, and the counts of events; the counts of CPMs in
the gap, the first age and the witnesses are over the events with an occlusion only.

The exit code is 0 when every run read passes. A run that cannot be read is reported with the error and counted
as failed; the others are still read.

## What D1 does not do

- It does not analyse the runs: the replay of B0 to B4 on every run, with the warm-up left out and the two kinds
  of seeds kept apart, is D3; the results database is D4.
- It does not check the event in every run the way `aoc-vru` does for C5. `aoc-vru` expects one event per run
  and the actors of C5 by name (`ego`, `helper_a`); the three events of the grid, with the actors `ego_k` and
  `helper_a_k`, will be read by the analysis of D3 with the same timeline code.
- It does not rerun a run that passed but looks odd. The index gives the numbers to look at (the simulated time,
  the rows per table); a run that ended early shows a `t_last_s` below 330.
