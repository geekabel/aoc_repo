"""The shell scripts of artery-ext/scripts hold copies of source files. This test fails when a copy is out of date."""
import importlib.util
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parents[1]
SCRIPTS = ["a6_install.sh", "a7_cp_demo.sh", "a8_logger.sh", "c1_link.sh"]


def test_built_scripts_are_up_to_date(capsys):
    spec = importlib.util.spec_from_file_location("build_scripts", ROOT / "artery-ext" / "tools" / "build_scripts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0, capsys.readouterr().out


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_has_valid_shell_syntax(name):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found")
    done = subprocess.run([bash, "-n", str(ROOT / "artery-ext" / "scripts" / name)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
