#!/usr/bin/env python3
"""Figure F6: the belief of the ego in the hidden pedestrian over time, with V2X and with its own sensors only (C7).

Reads the exported runs of the occluded-VRU pilot (the folder given on the command line, or AOC_C5_RESULTS, or
~/aoc-sim/c5/latest), writes f06_money.pdf next to this script and the traces behind it (f06_money.csv), and prints
the numbers quoted in the text. The run drawn is the first one with two helpers, else the first run. When the
folder does not exist, it says so and stops without an error, so that ``make figures`` runs on a machine without
the results. A FAIL of the checks of ``aoc-pilot`` (exit code 1) still draws the figure and is not an error here;
a folder without any exported run (exit code 2) is.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = argv[0] if argv else os.environ.get("AOC_C5_RESULTS", os.path.expanduser("~/aoc-sim/c5/latest"))
    if not os.path.isdir(root):
        print("f06_money: no results in %s, nothing drawn" % root)
        return 0
    from aoc.analysis import pilot
    out = os.path.join(HERE, "f06_money.pdf")
    code = pilot.main([root, "--no-metrics", "--figure", out, "--traces", os.path.join(HERE, "f06_money.csv")])
    if os.path.isfile(out):
        print("written: %s" % out)
    else:
        print("f06_money: no figure written (matplotlib missing, or no run with a trace)")
    return 0 if code in (0, 1) else code


if __name__ == "__main__":
    sys.exit(main())
