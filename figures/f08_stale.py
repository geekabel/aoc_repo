#!/usr/bin/env python3
"""Figure F8: see aoc.analysis.gridfigures. Reads results/ (the folder given on the command line, or the repository's),
writes f08_stale.pdf next to this script and prints the numbers quoted in the text. Without the results it says so and stops."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    results = argv[0] if argv else os.path.join(os.path.dirname(HERE), "results")
    from aoc.analysis import gridfigures
    return gridfigures.main(["--results", results, "--out", HERE, "--only", "f08"])


if __name__ == "__main__":
    sys.exit(main())
