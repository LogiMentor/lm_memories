#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Run the existing lm_memories GHDL testbenches.

Sources come from the "test-legacy" flow of manifests/lm_memories.toml. GHDL
runs inside a dedicated work directory, so no object or executable files are
written to the repository root. The directory is sim/build/ghdl, or
$LM_MEM_BUILD_DIR/ghdl when LM_MEM_BUILD_DIR is set.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from tools.manifest import load  # noqa: E402

BUILD_ROOT = Path(os.environ["LM_MEM_BUILD_DIR"]) if os.environ.get("LM_MEM_BUILD_DIR") else ROOT_DIR / "sim" / "build"
BUILD_DIR = BUILD_ROOT / "ghdl"
GHDL = os.environ.get("GHDL", "ghdl")

TESTS = [
  "tb_lm_mem_ram_rw_rw",
  "tb_lm_mem_ram_r_w_infer",
  "tb_lm_mem_ram_r_w_primitives",
  "tb_lm_mem_fifo_sync",
  "tb_lm_mem_fifo_async",
]


def run_ghdl(args: list[str]) -> None:
  subprocess.run([GHDL, *args], cwd=BUILD_DIR, check=True)


def main() -> int:
  if BUILD_DIR.exists():
    shutil.rmtree(BUILD_DIR)
  BUILD_DIR.mkdir(parents=True)

  flags = ["--std=08", f"--workdir={BUILD_DIR}"]

  for source in load().files("test-legacy"):
    run_ghdl(["-a", *flags, str(source.path)])

  for test in TESTS:
    run_ghdl(["-e", *flags, test])
    run_ghdl(["-r", *flags, test, "--assert-level=error"])

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
