#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""VUnit runner for the lm_memories RAM and FIFO testbenches.

Sources come from the "test-vunit" flow of manifests/lm_memories.toml.
Output goes to sim/build/vunit, or $LM_MEM_BUILD_DIR/vunit when set.
The simulator is GHDL (the CI reference) unless VUNIT_SIMULATOR is set, e.g.
VUNIT_SIMULATOR=modelsim for Questa/ModelSim. VUnit would otherwise pick the
first simulator found on PATH. With Questa 2021.1 run one test at a time
(-p 1): parallel runs race for the library lock of the implicit vopt step
(vsim-1962).

A run holds the machine-wide EDA lock (tools/eda_lock.py), so runs from
different sessions on the same machine queue up. Listing does not lock.
Each test reports the simulator name and version in its final line.

  python sim/vunit/run.py              # all configurations
  python sim/vunit/run.py "*cr_cw*"    # a subset
  python sim/vunit/run.py --list
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("VUNIT_SIMULATOR", "ghdl")

from vunit import VUnit  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from tools.eda_lock import heavy_tool_lock  # noqa: E402
from tools.manifest import load  # noqa: E402

from configs import FIFO_CONFIGS, RAM_CONFIGS  # noqa: E402

BUILD_ROOT = Path(os.environ["LM_MEM_BUILD_DIR"]) if os.environ.get("LM_MEM_BUILD_DIR") else ROOT_DIR / "sim" / "build"


def simulator_info() -> str:
  """Simulator name and version, made safe for a VHDL string generic."""
  name = os.environ["VUNIT_SIMULATOR"]
  if name == "ghdl":
    cmd = [os.environ.get("GHDL", shutil.which("ghdl") or "ghdl"), "--version"]
  elif name == "modelsim":
    folder = os.environ.get("VUNIT_MODELSIM_PATH")
    cmd = [str(Path(folder) / "vsim") if folder else (shutil.which("vsim") or "vsim"), "-version"]
  else:
    return name
  try:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
  except (OSError, subprocess.SubprocessError):
    return f"{name} (version unknown)"
  first = next((line.strip() for line in out.splitlines() if line.strip()), "version unknown")
  return re.sub(r"[^A-Za-z0-9._()-]+", "_", f"{name}: {first}")


def main() -> None:
  argv = sys.argv[1:]
  if not any(arg.startswith("--output-path") or arg == "-o" for arg in argv):
    argv += ["--output-path", str(BUILD_ROOT / "vunit")]
  vu = VUnit.from_argv(argv=argv, compile_builtins=False)
  vu.add_vhdl_builtins()

  lib = vu.add_library("lib")
  for source in load().files("test-vunit"):
    lib.add_source_file(str(source.path), vhdl_standard="2008")

  listing = any(arg in ("--list", "-l", "--files", "--compile") for arg in argv)
  sim_info = "listing" if listing else simulator_info()
  if not listing:
    print(f"Simulator: {sim_info}")

  tb_ram = lib.test_bench("tb_lm_mem_ram")
  for name, generics in RAM_CONFIGS:
    tb_ram.add_config(name=name, generics={**generics, "g_sim_info": sim_info})

  tb_fifo = lib.test_bench("tb_lm_mem_fifo")
  for name, generics in FIFO_CONFIGS:
    tb_fifo.add_config(name=name, generics={**generics, "g_sim_info": sim_info})

  if listing:
    vu.main()
  else:
    with heavy_tool_lock(f"VUnit {os.environ['VUNIT_SIMULATOR']}: {' '.join(argv) or 'all tests'}", kind="simulation"):
      vu.main()


if __name__ == "__main__":
  main()
