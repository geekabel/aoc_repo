#!/usr/bin/env python3
"""Builds the self-contained shell scripts of artery-ext/scripts from their templates.

  a7_cp_demo.sh   template a7_cp_demo.template.sh  +  a7_cpm_logs.py
  a8_logger.sh    template a8_logger.template.sh   +  the AoC sources (artery-ext/aoc),
                  the two config files (artery-ext/config) and the exporter (src/aoc/io/export.py)
  c1_link.sh      template c1_link.template.sh     +  the AoC sources, the single-link scenario (artery-ext/link),
                  its services and sensors files, the exporter and the link check (src/aoc/analysis/link.py)
  c5_vru.sh       template c5_vru.template.sh      +  the AoC sources, the occluded-VRU scenario (artery-ext/vru),
                  its services and sensors files, the exporter and the event check (src/aoc/analysis/vru.py)

A script holds a copy of each file it needs, so one file is enough on the workstation.
The sources stay in one place in this repository. After a change of a source file, run

    python artery-ext/tools/build_scripts.py            writes the four scripts
    python artery-ext/tools/build_scripts.py --check    writes nothing, fails if a script is out of date

Needs only the Python standard library.
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "artery-ext" / "tools"
SCRIPTS = ROOT / "artery-ext" / "scripts"


def embed(path, end_marker):
    body = (ROOT / path).read_text(encoding="utf-8").rstrip("\n")
    if end_marker in body:
        raise SystemExit(f"{path} contains the heredoc marker {end_marker}")
    return body


def build_a7():
    template = (TOOLS / "a7_cp_demo.template.sh").read_text(encoding="utf-8")
    if template.count("@@A7_CPM_LOGS_PY@@") != 1:
        raise SystemExit("a7_cp_demo.template.sh: expected one placeholder @@A7_CPM_LOGS_PY@@")
    return template.replace("@@A7_CPM_LOGS_PY@@", embed("artery-ext/tools/a7_cpm_logs.py", "PYEOF"))


def build_a8():
    template = (TOOLS / "a8_logger.template.sh").read_text(encoding="utf-8")
    text, n = re.subn(r"@@FILE:([^@]+)@@", lambda m: embed(m.group(1), "A8_FILE_EOF"), template)
    if n != 11:
        raise SystemExit(f"a8_logger.template.sh: expected 11 placeholders, found {n}")
    return text


def build_c1():
    template = (TOOLS / "c1_link.template.sh").read_text(encoding="utf-8")
    text, n = re.subn(r"@@FILE:([^@]+)@@", lambda m: embed(m.group(1), "C1_FILE_EOF"), template)
    if n != 17:
        raise SystemExit(f"c1_link.template.sh: expected 17 placeholders, found {n}")
    return text


def build_c5():
    template = (TOOLS / "c5_vru.template.sh").read_text(encoding="utf-8")
    text, n = re.subn(r"@@FILE:([^@]+)@@", lambda m: embed(m.group(1), "C5_FILE_EOF"), template)
    if n != 21:
        raise SystemExit(f"c5_vru.template.sh: expected 21 placeholders, found {n}")
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the shell scripts of artery-ext/scripts from their templates.")
    ap.add_argument("--check", action="store_true", help="write nothing; exit with 1 if a script is out of date")
    args = ap.parse_args(argv)
    stale = []
    for name, build in (("a7_cp_demo.sh", build_a7), ("a8_logger.sh", build_a8), ("c1_link.sh", build_c1), ("c5_vru.sh", build_c5)):
        text = build()
        target = SCRIPTS / name
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if args.check:
            state = "up to date" if current == text else "OUT OF DATE"
            if current != text:
                stale.append(name)
        else:
            if current != text:
                target.write_text(text, encoding="utf-8")
                target.chmod(0o755)
            state = "unchanged" if current == text else "written"
        print(f"{name:16s} {len(text.splitlines()):5d} lines  {state}")
    if stale:
        print("Run: python artery-ext/tools/build_scripts.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
