#!/usr/bin/env python3
"""Figure F3: the age at the receiver on the single-link runs (C1), measured against the law of the note (C2).

Reads the exported runs of C1 (the folder given on the command line, or AOC_C1_RESULTS, or ~/aoc-sim/c1/latest),
writes f03_ages.pdf next to this script, and prints the numbers quoted in the text: the largest gap of the mean
age and the largest Kolmogorov distance over the runs. When the folder does not exist, it says so and stops
without an error, so that ``make figures`` runs on a machine without the simulation results.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = argv[0] if argv else os.environ.get("AOC_C1_RESULTS", os.path.expanduser("~/aoc-sim/c1/latest"))
    if not os.path.isdir(root):
        print("f03_ages: no results in %s, nothing drawn" % root)
        return 0
    from aoc.analysis import ages
    out = os.path.join(HERE, "f03_ages.pdf")
    code = ages.main([root, "--out", out, "--table", os.path.join(HERE, "f03_ages.csv")])
    print("written: %s" % out)
    return 0 if code in (0, 3) else code


if __name__ == "__main__":
    sys.exit(main())
