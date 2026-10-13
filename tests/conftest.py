"""Shared test data: the two sample runs of tests/data, exported once per test session."""
import pathlib
import subprocess
import sys

import pytest

DATA = pathlib.Path(__file__).parent / "data"
SAMPLE_RUNS = ("run_example_sensors", "run_aoc_sensors")


@pytest.fixture(scope="session")
def exported_runs(tmp_path_factory):
    """Maps the name of each sample run to (folder with the parquet files, finished exporter process)."""
    out = {}
    for name in SAMPLE_RUNS:
        folder = tmp_path_factory.mktemp(name)
        done = subprocess.run([sys.executable, "-m", "aoc.io.export", str(DATA / name), "--out", str(folder)],
                              capture_output=True, text=True)
        out[name] = (folder, done)
    return out
