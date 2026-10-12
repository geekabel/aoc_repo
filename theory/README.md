# Theory note

`note/aoc_theory_note.pdf` is the note of task A5 (version 2 of October 12, 2026): 8 pages, IEEE conference format.

| File | Content |
| --- | --- |
| `note/aoc_theory_note.tex` | Source. Compile with `make note` (pdflatex, twice) |
| `note/figs/` | The seven figures (PDF, vector) |
| `note/code/a5_figures.py` | Draws every figure and prints every number quoted in the text and the tables |

`make figures` runs the script. It needs the `aoc` package, because the numbers come from `aoc.theory`.
Another version of matplotlib can move the margins of a figure by a few pixels. The content stays the same.

Things to know before a change:

- The class file is IEEEtran from TeX Live, the class of the official IEEE conference template.
- The numerical values are assumed, not fitted to data.
- The last page is balanced by a manual `\newpage` in Appendix D. Remove it if the text changes.
- `tests/test_theory.py` holds the values of Tables II and III. Update the test and the note together.
