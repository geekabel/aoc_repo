"""The columns of the observations table, as a Markdown document.

Run:  python -m aoc.io.schema > docs/observations_schema.md

The list of columns lives in one place, ``aoc.io.export.OBS_COLUMNS``. This module only prints it.
"""
from .export import EXPORT_VERSION, OBS_COLUMNS

N_PLAN = 27          # the first 27 columns are the ones listed in the Plan


def markdown():
    lines = [
        "# The observations table",
        "",
        "<!-- Written by `python -m aoc.io.schema`. Do not edit by hand: change OBS_COLUMNS in src/aoc/io/export.py. -->",
        "",
        "One row of `observations.parquet` is one object that one vehicle gets at one time, either from its",
        f"own sensors or from a received CPM. Exporter version {EXPORT_VERSION}.",
        "",
        f"The first {N_PLAN} columns are the ones of the Plan, in the same order. The other {len(OBS_COLUMNS) - N_PLAN}",
        "were added during task A8.",
        "",
        "| No. | Column | Type | Meaning |",
        "| --- | --- | --- | --- |",
    ]
    for i, (name, kind, text) in enumerate(OBS_COLUMNS, start=1):
        lines.append(f"| {i} | `{name}` | {kind} | {text} |")
    lines += [
        "",
        "Positions and speeds use the axes of Artery: x grows to the east and y grows to the SOUTH.",
    ]
    return "\n".join(lines) + "\n"


def main():
    print(markdown(), end="")


if __name__ == "__main__":
    main()
