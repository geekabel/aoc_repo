# The figures of the grid (task D5)

`aoc-grid-figures [--results results] [--out figures] [--only f05]` draws the four figures of the papers that come
from the core grid. Each one is also a script in `figures/` (`f02_alpha.py`, `f05_calibration.py`,
`f07_awareness.py`, `f08_stale.py`), run by `make figures`. They read the tables of D3 and D4 in the repository
and need no simulation data; every figure prints the numbers quoted in the text; without the tables the command
says so and stops without an error.

| Figure | Content | Data | The numbers printed |
| --- | --- | --- | --- |
| F2 `f02_alpha.pdf` | one panel per class (car, truck, pedestrian): the reliability curve alpha(age) of the note with the assumed q (dashed grey), the fitted curve (blue) and the measured hit rate of the calibration runs (circles, within 1.5 m); the dotted verticals mark the half-lives (alpha = 0.5) | `results/d3/qfit_curves.csv`, `qfit.csv` | q and the half-life, assumed and fitted; the hit rate, the fitted and the assumed alpha at 5 s |
| F5 `f05_calibration.pdf` | the reliability diagram at a hold of 5 s: the share of the fused objects really there against their belief (bins of 0.1), one curve per way B0 to B4, the diagonal of perfect calibration; under it the share of the objects per bin (log scale). B0 has one bin (the belief is 0.99 everywhere): a lone point | `results/d4/reliability.csv` | per way the calibration error pooled over the objects of the 360 evaluation runs (not the mean per run of `cells.csv`), the top bin: its share really there and its share of the objects |
| F7 `f07_awareness.pdf` | left, the awareness ratio within 200 m against the penetration; right, the calibration error; one line per way with the 95 % band over the evaluation runs of the level (90 runs) | `results/d4/runs.csv` | per way and penetration the mean of both |
| F8 `f08_stale.pdf` | left, the stale-confidence rate against the channel busy ratio of the cell (the mean over the received CPMs); right, the ghost rate; one marker per cell and way with its interval over the 10 seeds | `results/d4/cells.csv` | per way the mean, the lowest and the highest cell; the range of the channel busy ratio |

The style is the one of the other figures of the papers (serif, 8 pt, one column 3.45 in wide, two columns 6.9 in,
vector PDF with the fonts embedded). The colours of the ways are fixed in `gridfigures.COLORS` (B3 blue, B0 grey,
B1 orange, B2 green, B4 purple) and their markers in `MARKERS`, so that every figure of the papers names a way the
same way.

## What the figures of Nov 4, 2026 show

- F2: the measured hit rate follows the fitted curve in the three classes; the curve of the note (the assumed q)
  decays twice as fast. The half-life goes from 1.0 to 1.9 s for the car, 1.3 to 2.1 s for the truck, 1.7 to 2.4 s
  for the pedestrian. At 5 s the measured rate is still 0.09 (car), 0.12 (truck), 0.15 (pedestrian): the fitted
  curve is a little under it for the car (0.06).
- F5: B3 and B4 lie on the diagonal in the low bins and in the top bin (97 % there at a belief of 0.99) and about
  0.1 under it in the middle (over-confident: a belief of 0.65 for 55 % there); B2 is over the diagonal in the low
  bins (under-confident: 43 % there at 0.15, 75 % at 0.45) and B1 runs straight from 0.23 to 0.97 (nothing between
  0.2 and 0.9). B0 is one point at (0.99, 0.47). Pooled over the objects the calibration error is B3 0.033, B4
  0.037, B2 0.127, B1 0.160, B0 0.524.
- F7: the awareness is the same for every way (0.67 at 10 %, 0.97 at 100 %; the bands overlap); the calibration
  error grows with the penetration for every way (B3 0.020 to 0.035, B1 0.111 to 0.170, B0 0.30 to 0.57) and the
  order of the ways never changes.
- F8: the channel busy ratio of the cells runs from 0.02 to 0.20 and no way reacts to it: B0 at 0.99 everywhere,
  B3 between 0.21 and 0.30 (B4 the same), B2 between 0.05 and 0.11, B1 at 0. The ghost rate is the same picture.
  The channel never bites at these loads: the control grid (E2) must push it further.
