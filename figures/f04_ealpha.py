#!/usr/bin/env python3
"""Figure F4: E[alpha] against the update rate on the single-link runs (C1), measured against the formula of the note (C3).

Reads the exported runs of C1 (the folder given on the command line, or AOC_C1_RESULTS, or ~/aoc-sim/c1/latest),
writes f04_ealpha.pdf next to this script, and prints the numbers quoted in the text. When the folder does not
exist, it says so and stops without an error, so that ``make figures`` runs on a machine without the results.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = argv[0] if argv else os.environ.get("AOC_C1_RESULTS", os.path.expanduser("~/aoc-sim/c1/latest"))
    if not os.path.isdir(root):
        print("f04_ealpha: no results in %s, nothing drawn" % root)
        return 0
    from aoc.analysis import ealpha
    out = os.path.join(HERE, "f04_ealpha.pdf")
    code = ealpha.main([root, "--out", out, "--table", os.path.join(HERE, "f04_ealpha.csv")])
    if os.path.isfile(out):
        print("written: %s" % out)
    else:
        print("f04_ealpha: no figure written (matplotlib missing, or no run with a value)")
    return 0 if code in (0, 3) else code


if __name__ == "__main__":
    sys.exit(main())
