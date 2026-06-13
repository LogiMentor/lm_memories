#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Run lm_memories GHDL simulations."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
BUILD_DIR = ROOT_DIR / "sim" / "build" / "ghdl"
GHDL = os.environ.get("GHDL", "ghdl")

SOURCES = [
  "src/common/lm_mem_pkg.vhd",
  "src/common/lm_mem_ccd_resync.vhd",
  "src/generic/lm_mem_ram_crw_crw.vhd",
  "src/generic/lm_mem_ram_rw_rw.vhd",
  "src/generic/lm_mem_ram_cr_cw.vhd",
  "src/generic/lm_mem_ram_cr_cw_ratio.vhd",
  "src/generic/lm_mem_ram_crw_cr.vhd",
  "src/generic/lm_mem_ram_crw_cw.vhd",
  "src/generic/lm_mem_ram_r_w.vhd",
  "src/generic/lm_mem_ram_r_w_ratio.vhd",
  "src/inference/lm_mem_ram_r_w_infer.vhd",
  "src/fifo/lm_mem_fifo.vhd",
  "src/fifo/lm_mem_fifo_sync.vhd",
  "src/fifo/lm_mem_fifo_async.vhd",
  "src/primitives/xilinx/lm_mem_xilinx_ram_r_w_prim.vhd",
  "src/primitives/intel/lm_mem_intel_ram_r_w_prim.vhd",
  "src/primitives/lattice/lm_mem_lattice_ram_r_w_prim.vhd",
  "src/primitives/microchip/lm_mem_microchip_ram_r_w_prim.vhd",
]

TESTBENCHES = [
  "sim/tb/tb_lm_mem_ram_rw_rw.vhd",
  "sim/tb/tb_lm_mem_ram_r_w_infer.vhd",
  "sim/tb/tb_lm_mem_ram_r_w_primitives.vhd",
  "sim/tb/tb_lm_mem_fifo_sync.vhd",
  "sim/tb/tb_lm_mem_fifo_async.vhd",
]

TESTS = [
  "tb_lm_mem_ram_rw_rw",
  "tb_lm_mem_ram_r_w_infer",
  "tb_lm_mem_ram_r_w_primitives",
  "tb_lm_mem_fifo_sync",
  "tb_lm_mem_fifo_async",
]


def run_ghdl(args: list[str]) -> None:
  subprocess.run([GHDL, *args], cwd=ROOT_DIR, check=True)


def main() -> int:
  if BUILD_DIR.exists():
    shutil.rmtree(BUILD_DIR)
  BUILD_DIR.mkdir(parents=True)

  flags = ["--std=08", f"--workdir={BUILD_DIR}"]

  for source in SOURCES:
    run_ghdl(["-a", *flags, str(ROOT_DIR / source)])

  for testbench in TESTBENCHES:
    run_ghdl(["-a", *flags, str(ROOT_DIR / testbench)])

  for test in TESTS:
    run_ghdl(["-e", *flags, test])
    run_ghdl(["-r", *flags, test, "--assert-level=error"])

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
