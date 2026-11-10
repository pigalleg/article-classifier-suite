#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_outputs.py
=================
Check that the analysis stage reproduces the committed results exactly.

Copies data/ to a temporary folder, runs src/analyze_citations.py there
(via SO_DATA_DIR, so the committed files are never touched), and compares
every file in results/stats/ and results/figures/ (.csv, .json, .tex, .png)
byte-for-byte against the committed versions.

The analysis is deterministic, so any difference means an input, the code
or a package version changed the results.

Usage:
    python scripts/verify_outputs.py
    python scripts/verify_outputs.py --keep     # keep the temp folder for inspection
"""

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("SO_DATA_DIR", ROOT / "data")).resolve()
CHECK_SUFFIXES = {".csv", ".json", ".tex", ".png"}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[3])
    p.add_argument("--keep", action="store_true", help="Keep the temporary run folder.")
    args = p.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="so_verify_"))
    run_data = tmp / "data"
    shutil.copytree(DATA, run_data, ignore=shutil.ignore_patterns("pdfs"))

    env = dict(os.environ, SO_DATA_DIR=str(run_data), MPLBACKEND="Agg")
    print(f"Running analyze_citations.py on a copy of {DATA} ...")
    proc = subprocess.run([sys.executable, str(ROOT / "src" / "analyze_citations.py")],
                          env=env, cwd=tmp, capture_output=True, text=True)
    (tmp / "analyze.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        print(proc.stdout[-2000:], proc.stderr[-4000:])
        print(f"[FAIL] analyze_citations.py exited with {proc.returncode} (log: {tmp/'analyze.log'})")
        sys.exit(1)

    checked, diffs, missing = 0, [], []
    for sub in ("results/stats", "results/figures"):
        for ref in sorted((DATA / sub).iterdir()):
            if ref.suffix not in CHECK_SUFFIXES:
                continue
            new = run_data / sub / ref.name
            checked += 1
            if not new.exists():
                missing.append(f"{sub}/{ref.name}")
            elif not filecmp.cmp(ref, new, shallow=False):
                diffs.append(f"{sub}/{ref.name}")

    print(f"Checked {checked} files: {len(diffs)} differ, {len(missing)} not regenerated")
    for d in diffs:
        print(f"  DIFF     {d}")
    for m in missing:
        print(f"  MISSING  {m}")

    if args.keep or diffs or missing:
        print(f"Run folder kept at {tmp}")
    else:
        shutil.rmtree(tmp)

    if diffs or missing:
        sys.exit(1)
    print("[OK] results reproduced exactly.")


if __name__ == "__main__":
    main()
