# The age at the receiver against the law of the note

Task C2, figure F3. The module is `aoc.analysis.ages`, the command `aoc-ages`, the figure script
`figures/f03_ages.py`. It takes the exported single-link runs of C1 and compares the age at the receiver with the
age law of step 3 of the theory note (task A3).

## What is compared

On a single link the sender sends a CPM every T seconds and each copy is lost with probability p, independently.
Seen at a random time, the time A since the last received update follows, for periodic generation:

    P(A <= a) = 1 - p^k + (1 - p) p^k r / T        with a = k T + r, 0 <= r < T
    E[A] = T / 2 + T p / (1 - p)

The age of the object at the receiver is Delta = d0 + A, where d0 is the age at reception: the radio delay plus
the time between the sender's last sensor step and its CPM. The module measures A and d0; the law is about A.

## How A is measured

Not by sampling. Over a window of length L with receptions at r_1 < r_2 < ... the share of time during which
the age is at most a is exact:

    F(a) = (1 / L) * sum over the gaps g of min(g, a)        mean age = sum of g^2 / (2 L)

The gaps are the times between two receptions, and from the last reception to the end of the run. The window
opens at the first reception after the warm-up (10 s by default), so the time before any knowledge does not
count. The receptions are the received rows about objects the receiver never saw itself (the two links of the
scenario), pooled.

## The noise of one run

A run of 330 s has few receptions when T = 1 s and p = 0.5 (about 165), and the mean age weighs the long gaps
(g^2), so one run cannot show the law to 5%. The module draws 300 runs of the same window from the law itself
(gaps of k periods, k geometric) and reports what they show: the standard deviation of their mean age, and the
99th percentile of the distance between their distribution function and the law. A run passes when the relative
gap of its mean age is within 5% or within three times that standard deviation, and when its Kolmogorov distance
is within 0.05 or within that 99th percentile. On a long run the 5% of the plan decides; on a short one, the noise.

A plain bootstrap of the measured gaps was tried first and underestimates the noise by a third: a run with fewer
long gaps than average resamples fewer long gaps. The noise under the law itself is the right reference.

## Run it

```bash
aoc-ages ~/aoc-sim/c1/latest --out f3_ages.pdf --table ages.csv
python figures/f03_ages.py [RESULT_FOLDER]        # the same, written next to the script as f03_ages.pdf
```

One row per run: T, p, links, receptions, window, mean age measured and from the law, their relative gap and
the noise, the Kolmogorov distance and its noise, d0 (median and 95th percentile). Then PASS or FAIL.

| Option | What it does |
| --- | --- |
| `--warmup S` | seconds left out at the start of each run (default 10) |
| `--max-rel X` | largest relative gap of the mean age for a PASS (default 0.05) |
| `--max-ks X` | largest Kolmogorov distance for a PASS (default 0.05) |
| `--draws N` | runs drawn from the law to measure the noise (default 300) |
| `--out FILE`, `--table FILE` | figure F3 (.pdf or .png) and the table (.csv or .parquet) |

## On a synthetic grid

The 16 cells of the plan as synthetic streams (`aoc.io.synthetic`, the same three cars, delay 0.02 s, 330 s):
every run passes. With no loss the measured and the law coincide (gap 0.0%, distance 0.000). With loss the gaps
of the mean are within 3% on 13 runs and reach 6 to 10% on the three with the fewest receptions (T = 1 s with
p = 0.3 or 0.5, T = 0.2 s with p = 0.5), each within the noise of a run of this length; over 20 seeds of the worst
cell the gaps average 0.0% with a standard deviation of 5%. The Kolmogorov distance stays at or below 0.045. The
age at reception is 0.020 s, the delay of the stream.

## Figure F3

One panel per loss p, the law as lines and the runs as dots, one colour per period T. Accept when the dots sit on
the lines.

## Limits

The law and the measure both take the receptions as a renewal process with independent losses, which is what
`AocCpService` does. A radio with bursts of losses would not follow the law; the module would show it. One seed
per cell: the noise columns say what one run can show.
