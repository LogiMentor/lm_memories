# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Command line for the lm_memories file manifest.

  python -m tools.manifest list  --flow synth-amd [--format f|tcl|json]
  python -m tools.manifest check --flow sim [--flow test-vunit ...]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import DEFAULT_MANIFEST, ManifestError, SourceFile, load

GHDL = os.environ.get("GHDL", "ghdl")


def _print_list(files: list[SourceFile], fmt: str) -> None:
  if fmt == "json":
    print(json.dumps([
      {"path": f.path.as_posix(), "fileset": f.fileset, "check": f.check, "tools": list(f.tools)}
      for f in files
    ], indent=2))
  elif fmt == "tcl":
    print("set lm_memories_files [list \\")
    for f in files:
      print(f"  {{{f.path.as_posix()}}} \\")
    print("]")
  else:
    for f in files:
      print(f.path.as_posix())


def _analyze(files: list[SourceFile], flags: list[str], workdir: Path) -> list[str]:
  failures: list[str] = []
  for f in files:
    cmd = [GHDL, "-a", *flags, f"--workdir={workdir}", str(f.path)]
    result = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if result.returncode != 0:
      failures.append(f"{f.path.as_posix()} ({' '.join(flags)}):\n{result.stdout}{result.stderr}")
  return failures


def check(flow_names: list[str], manifest_path: Path) -> int:
  manifest = load(manifest_path)
  failures: list[str] = []
  for flow in flow_names:
    files = manifest.files(flow)
    std93 = [f for f in files if f.check == "ghdl-93-08"]
    std08 = [f for f in files if f.check in ("ghdl-93-08", "ghdl-08", "ghdl-relaxed")]
    skipped = sorted({f"{f.fileset} ({f.check})" for f in files if f.check in ("target-tool", "vunit")})
    with tempfile.TemporaryDirectory(prefix="lm_manifest_") as tmp:
      w93 = Path(tmp) / "std93"
      w08 = Path(tmp) / "std08"
      w93.mkdir()
      w08.mkdir()
      failures += _analyze(std93, ["--std=93c"], w93)
      for f in std08:
        flags = ["--std=08", "-frelaxed"] if f.check == "ghdl-relaxed" else ["--std=08"]
        failures += _analyze([f], flags, w08)
    print(f"flow {flow}: {len(std93)} files checked as 93c, {len(std08)} as 08"
          + (f"; not checked by GHDL: {', '.join(skipped)}" if skipped else ""))
  for failure in failures:
    print(f"FAIL {failure}", file=sys.stderr)
  return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="python -m tools.manifest", description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
  sub = parser.add_subparsers(dest="command", required=True)
  p_list = sub.add_parser("list", help="print the ordered file list of a flow")
  p_list.add_argument("--flow", required=True)
  p_list.add_argument("--tool", help="fail if a file set of the flow is restricted to another tool")
  p_list.add_argument("--format", choices=("f", "tcl", "json"), default="f")
  p_check = sub.add_parser("check", help="run the GHDL checks declared for each file set")
  p_check.add_argument("--flow", action="append", required=True)
  args = parser.parse_args(argv)

  try:
    if args.command == "list":
      _print_list(load(args.manifest).files(args.flow, args.tool), args.format)
      return 0
    if shutil.which(GHDL) is None:
      print(f"GHDL not found ({GHDL})", file=sys.stderr)
      return 2
    return check(args.flow, args.manifest)
  except ManifestError as exc:
    print(f"manifest error: {exc}", file=sys.stderr)
    return 2
