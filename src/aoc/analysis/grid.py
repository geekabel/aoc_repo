"""The core grid of the plan (task D1, experiment E3): the cells, the run numbers, and the index of the exported runs.

The grid (``artery-ext/grid/omnetpp.ini``, configuration ``grid``):

    density   low, medium, high           250, 450, 700 vehicles per hour on each of the north, east and west approaches
    pen       10, 25, 50, 100             the share of cars that run the CA and CP services, in percent
    msg       etsi, 2hz, 10hz             the message configuration of the CP service
    seed      0 to 11                     0 to 9 are the evaluation runs of the plan, 10 and 11 the calibration runs

3 x 4 x 3 = 36 cells, 12 runs each: 432 runs of 330 s, 360 for the evaluation and 72 for the calibration of the motion
value q. OMNeT++ numbers the runs with the density outermost and the repetition innermost (the order of the
iteration lines of the configuration): run = ((d * 4 + p) * 3 + m) * 12 + seed.

    aoc-grid ROOT [--out index.csv] [--missing missing.txt]

reads the exported runs under ``ROOT`` (the folders ``aoc-grid-<run>`` with a ``parquet/export_summary.json``),
writes one row per run (the cell, the seed, the rows, the receivers, the sizes, the verdict of the exporter), checks
that the cell read from the run's variables is the one its run number says, counts the runs of each cell and lists
the missing run numbers, ready for ``d1_grid.sh --runs``. The exit code is 0 when the 432 runs are there and
exported, 1 otherwise.

The control grid of task E2 (configuration ``control``, ``aoc-grid ROOT --design control``) is a second design of the
same kind: density (low, high) x pen (25, 100) x rule (etsi, etsi_abs, aoc, aoc2, aocdev) x load (0, 12, 36 load stations
around the junction) x 4 seeds, 240 runs, every seed an evaluation run; run = (((d * 2 + p) * 5 + r) * 3 + l) * 4 + seed.
A ``Design`` holds the factors in the order of the run numbers; the functions take one (the core grid by default).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import pandas as pd

DENSITIES = ("low", "medium", "high")
PENETRATIONS = (10, 25, 50, 100)
MESSAGES = ("etsi", "2hz", "10hz")
REPETITIONS = 12
EVAL_SEEDS = tuple(range(10))
CAL_SEEDS = (10, 11)
N_RUNS = len(DENSITIES) * len(PENETRATIONS) * len(MESSAGES) * REPETITIONS
CONFIG = "grid"


class Design:
    """A full factorial design run by OMNeT++: the factors in the order of the iteration lines (the first outermost),
    the repetitions innermost, the name of the configuration, and the seeds that are calibration runs."""

    def __init__(self, config: str, factors: dict, repetitions: int, cal_seeds=()):
        self.config = config
        self.factors = {name: tuple(levels) for name, levels in factors.items()}
        self.names = tuple(self.factors)
        self.repetitions = int(repetitions)
        self.cal_seeds = tuple(cal_seeds)
        self.eval_seeds = tuple(s for s in range(self.repetitions) if s not in self.cal_seeds)
        self.n_cells = 1
        for levels in self.factors.values():
            self.n_cells *= len(levels)
        self.n_runs = self.n_cells * self.repetitions

    def run_number(self, seed: int, **levels) -> int:
        """The OMNeT++ run number of a cell (one level per factor, by name) and a seed."""
        if not 0 <= int(seed) < self.repetitions:
            raise ValueError("the seed must be between 0 and %d" % (self.repetitions - 1))
        number = 0
        for name in self.names:
            number = number * len(self.factors[name]) + self.factors[name].index(self._cast(name, levels[name]))
        return number * self.repetitions + int(seed)

    def cell_of_run(self, number: int) -> dict:
        """The cell and the seed of a run number, with the kind of the run (evaluation or calibration)."""
        if not 0 <= int(number) < self.n_runs:
            raise ValueError("the run number must be between 0 and %d" % (self.n_runs - 1))
        seed = int(number) % self.repetitions
        rest = int(number) // self.repetitions
        cell = {}
        for name in reversed(self.names):
            cell[name] = self.factors[name][rest % len(self.factors[name])]
            rest //= len(self.factors[name])
        out = {name: cell[name] for name in self.names}
        out.update(seed=seed, kind="calibration" if seed in self.cal_seeds else "evaluation")
        return out

    def _cast(self, name: str, value):
        return type(self.factors[name][0])(value)

    def cell_from_variables(self, variables: dict) -> dict | None:
        """The cell written by the exporter from the iteration variables of the run (None when one is missing or unknown)."""
        cell = {}
        for name in self.names:
            try:
                cell[name] = self._cast(name, variables[name])
            except (KeyError, ValueError, TypeError):
                return None
            if cell[name] not in self.factors[name]:
                return None
        return cell

    def cell_runs(self, **levels) -> list[int]:
        """The run numbers of a cell, one per seed."""
        return [self.run_number(seed, **levels) for seed in range(self.repetitions)]

    def seed_runs(self, seeds) -> list[int]:
        """The run numbers of the given seeds in every cell."""
        wanted = {int(s) for s in seeds}
        return [n for n in range(self.n_runs) if n % self.repetitions in wanted]

    def runs_table(self) -> pd.DataFrame:
        """Every run: run number, cell, seed, kind."""
        return pd.DataFrame([dict(run=n, **self.cell_of_run(n)) for n in range(self.n_runs)])

    def describe(self) -> str:
        return " x ".join("%s (%s)" % (name, ", ".join(str(v) for v in levels)) for name, levels in self.factors.items()) + \
            " x %d seeds: %d runs" % (self.repetitions, self.n_runs)


GRID = Design("grid", {"density": DENSITIES, "pen": PENETRATIONS, "msg": MESSAGES}, REPETITIONS, CAL_SEEDS)
CONTROL = Design("control", {"density": ("low", "high"), "pen": (25, 100), "rule": ("etsi", "etsi_abs", "aoc", "aoc2", "aocdev"), "load": (0, 12, 36)}, 4)
DESIGNS = {"grid": GRID, "control": CONTROL}


def run_number(density: str, pen: int, msg: str, seed: int) -> int:
    """The OMNeT++ run number of a cell and a seed of the core grid."""
    return GRID.run_number(seed, density=density, pen=pen, msg=msg)


def cell_of_run(number: int, design: Design = GRID) -> dict:
    """The cell and the seed of a run number."""
    return design.cell_of_run(number)


def runs_table(design: Design = GRID) -> pd.DataFrame:
    """Every run of the grid: run number, cell, seed, kind."""
    return design.runs_table()


def seed_runs(seeds, design: Design = GRID) -> list[int]:
    """The run numbers of the given seeds in every cell (for example the seeds 0 and 1 of every cell of the core grid: 72 runs)."""
    return design.seed_runs(seeds)


def cell_runs(density: str, pen: int, msg: str) -> list[int]:
    """The 12 run numbers of a cell of the core grid."""
    return GRID.cell_runs(density=density, pen=pen, msg=msg)


def cell_from_variables(variables: dict, design: Design = GRID) -> dict | None:
    """The cell written by the exporter from the iteration variables of the run (None when one is missing)."""
    return design.cell_from_variables(variables)


def _number_of_folder(name: str, design: Design = GRID) -> int | None:
    m = re.fullmatch(r"aoc-%s-(\d+)" % design.config, name)
    return int(m.group(1)) if m else None


def index_runs(root: str | os.PathLike, design: Design = GRID) -> pd.DataFrame:
    """One row per run folder ``aoc-<config>-<n>`` under ``root``: exported or not, with its cell and its numbers."""
    root = os.fspath(root)
    names = list(design.names)
    N_RUNS, cell_of_run, cell_from_variables = design.n_runs, design.cell_of_run, design.cell_from_variables
    exit_codes, export_codes = {}, {}
    codes_path = os.path.join(root, "exit-codes.txt")
    if os.path.isfile(codes_path):                       # one line per run of the script: "run simulation_code export_code", the last one counts
        with open(codes_path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].isdigit():
                    exit_codes[int(parts[0])] = int(parts[1]) if parts[1].lstrip("-").isdigit() else None
                    export_codes[int(parts[0])] = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    rows = []
    seen = set()
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        number = _number_of_folder(name, design)
        if number is None:
            continue
        seen.add(number)
        folder = os.path.join(root, name)
        expected = cell_of_run(number) if number < N_RUNS else {}
        row = dict(run=number, folder=folder, exported=False, **{k: expected.get(k) for k in names + ["seed", "kind"]},
                   exit_code=exit_codes.get(number), export_code=export_codes.get(number), run_id="", result="", t_last_s=None, steps=None,
                   receivers=None, rows_observations=None, rows_cpm_rx=None, rows_local=None, objects_true=None, parquet_mb=None, cell_matches=None,
                   has_variables=None)
        summary = os.path.join(folder, "parquet", "export_summary.json")
        if os.path.isfile(summary):
            with open(summary, encoding="utf-8") as fh:
                info = json.load(fh)
            row.update(exported=True, run_id=info.get("run_id", ""), result=info.get("result", ""), t_last_s=info.get("t_last_s"),
                       steps=info.get("steps"), receivers=None if info.get("receivers") is None else len(info["receivers"]),
                       rows_observations=info.get("rows", {}).get("observations"), rows_cpm_rx=info.get("rows", {}).get("cpm_rx"),
                       rows_local=info.get("rows", {}).get("local"), objects_true=info.get("objects_true"),
                       parquet_mb=round(sum(info.get("size_bytes", {}).values()) / 1e6, 1))
            cell = cell_from_variables(info.get("variables") or {})
            seed = info.get("seed")
            row["has_variables"] = cell is not None
            if expected:                                 # a run without the variables of the design is not a run of it
                row["cell_matches"] = bool(cell is not None and cell == {k: expected[k] for k in names} and seed == expected["seed"])
                if cell is not None and not row["cell_matches"]:
                    row.update(cell)
                    row["seed"] = seed
        rows.append(row)
    for number in range(N_RUNS):
        if number not in seen:
            rows.append(dict(run=number, folder="", exported=False, **cell_of_run(number), exit_code=exit_codes.get(number),
                             export_code=export_codes.get(number), run_id="", result="", t_last_s=None, steps=None, receivers=None,
                             rows_observations=None, rows_cpm_rx=None, rows_local=None, objects_true=None, parquet_mb=None, cell_matches=None,
                             has_variables=None))
    table = pd.DataFrame(rows).sort_values("run").reset_index(drop=True)
    int_factors = [n for n in names if isinstance(design.factors[n][0], int)]
    for col in ("exit_code", "export_code", "steps", "receivers", "rows_observations", "rows_cpm_rx", "rows_local", "objects_true", "seed", *int_factors):
        table[col] = table[col].astype("Int64")
    return table


def check_grid(table: pd.DataFrame, design: Design = GRID) -> dict:
    """What is done and what is missing: the good runs (exported, PASS, right cell), the missing or failed run numbers,
    the count per cell."""
    N_RUNS, names = design.n_runs, list(design.names)
    in_grid = table["run"] < N_RUNS
    passed = table["exported"] & (table["result"] == "PASS")
    good = table[in_grid & passed & (table["cell_matches"] == True)]  # noqa: E712
    done = set(good["run"])
    missing = sorted(set(range(N_RUNS)) - done)
    no_variables = sorted(table.loc[in_grid & table["exported"] & (table["has_variables"] == False), "run"])  # noqa: E712
    wrong_cell = sorted(table.loc[in_grid & (table["cell_matches"] == False) & (table["has_variables"] == True), "run"])  # noqa: E712
    failed_export = sorted(table.loc[in_grid & table["exported"] & (table["result"] != "PASS"), "run"])
    # a run whose script codes are not 0 and that is not exported with PASS (a run exported again by --check is fine)
    bad_code = (table["exit_code"].fillna(0) != 0) | (table["export_code"].fillna(0) != 0)
    failed_run = sorted(table.loc[in_grid & bad_code & ~passed, "run"])
    stray = sorted(table.loc[~in_grid, "run"])
    eval_seeds, cal_seeds = design.eval_seeds, design.cal_seeds
    per_cell = (good.groupby(names).agg(evaluation=("seed", lambda s: int(sum(1 for v in s if v in eval_seeds))),
                                        calibration=("seed", lambda s: int(sum(1 for v in s if v in cal_seeds))))
                .reindex(pd.MultiIndex.from_product([design.factors[n] for n in names], names=names), fill_value=0)
                .reset_index())
    return dict(done=len(done), missing=missing, wrong_cell=wrong_cell, no_variables=no_variables, failed_export=failed_export,
                failed_run=failed_run, stray=stray, per_cell=per_cell, complete=not missing and not wrong_cell and not no_variables)


def format_check(c: dict, design: Design = GRID) -> str:
    names = list(design.names)
    lines = ["runs done: %d of %d (%d evaluation, %d calibration)" % (
        c["done"], design.n_runs, int(c["per_cell"]["evaluation"].sum()), int(c["per_cell"]["calibration"].sum()))]
    per_cell = c["per_cell"]
    full = (per_cell["evaluation"] >= len(design.eval_seeds)) & (per_cell["calibration"] >= len(design.cal_seeds))
    started = (per_cell["evaluation"] + per_cell["calibration"]) > 0
    lines.append("cells complete: %d of %d, started: %d" % (int(full.sum()), len(per_cell), int(started.sum())))
    short = per_cell[started & ~full]
    if len(short):
        lines.append("cells started and not complete (evaluation+calibration runs): " + "; ".join(
            "/".join(str(getattr(r, n)) for n in names) + " %d+%d" % (r.evaluation, r.calibration) for r in short.itertuples()))
    if c["failed_run"]:
        lines.append("runs that ended with an error: " + compact(c["failed_run"]))
    if c["failed_export"]:
        lines.append("runs whose export did not pass: " + compact(c["failed_export"]))
    if c["wrong_cell"]:
        lines.append("runs whose variables are not those of their run number (the order of the runs is not the one assumed): " + compact(c["wrong_cell"]))
    if c["no_variables"]:
        lines.append("runs exported without the variables of the design (%s; not runs of the configuration %s?): %s" % (
            ", ".join(names), design.config, compact(c["no_variables"])))
    if c["stray"]:
        lines.append("run folders with a number outside the grid (ignored): " + compact(c["stray"]))
    if c["missing"]:
        lines.append("missing: %d run(s): %s" % (len(c["missing"]), compact(c["missing"])))
        option = "" if design.config == "grid" else " --%s" % design.config
        lines.append("to run them: bash d1_grid.sh%s --runs " % option + compact(c["missing"]) + "   (or bash d1_grid.sh%s alone: it runs what is missing)" % option)
    lines.append("grid: %s" % ("complete" if c["complete"] else "incomplete"))
    return "\n".join(lines)


def compact(numbers: list) -> str:
    """1,2,3,7 as 1-3,7."""
    out, start, prev = [], None, None
    for n in list(numbers) + [None]:
        if start is None:
            start = prev = n
        elif n is not None and n == prev + 1:
            prev = n
        else:
            out.append(str(start) if start == prev else "%d-%d" % (start, prev))
            start = prev = n
    return ",".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Index and check the exported runs of the core grid (task D1) or of the control grid (task E2).")
    ap.add_argument("root", help="the results folder of the grid (holds the run folders aoc-<config>-<n>)")
    ap.add_argument("--design", choices=sorted(DESIGNS), default="grid", help="the design: grid (default) or control")
    ap.add_argument("--out", default=None, help="write the index (one row per run) to this CSV file")
    ap.add_argument("--missing", default=None, help="write the missing run numbers, one per line, to this file")
    ap.add_argument("--list", action="store_true", help="print the table of the runs (run number, cell, seed) and stop")
    args = ap.parse_args(argv)
    design = DESIGNS[args.design]
    if args.list:
        print(design.runs_table().to_string(index=False))
        return 0
    table = index_runs(args.root, design)
    c = check_grid(table, design)
    print(format_check(c, design))
    if args.out:
        table.to_csv(args.out, index=False)
        print("index: %s" % args.out)
    if args.missing:
        with open(args.missing, "w", encoding="utf-8") as fh:
            fh.write("\n".join(str(n) for n in c["missing"]) + ("\n" if c["missing"] else ""))
        print("missing runs: %s" % args.missing)
    return 0 if c["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
