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

BUILD_ROOT = Path(os.environ["LM_MEM_BUILD_DIR"]) if os.environ.get("LM_MEM_BUILD_DIR") else ROOT_DIR / "sim" / "build"

FAST = 10000  # ps
SLOW = 7000   # ps, deliberately not a divisor of FAST

# Port roles: simple dual port = A writes, B reads; tdp1w = A reads and writes, B reads.
SDP = dict(g_a_writes=True, g_a_reads=False, g_b_writes=False, g_b_reads=True)
TDP1W = dict(g_a_writes=True, g_a_reads=True, g_b_writes=False, g_b_reads=True)


def ram(name, dut, roles, *, a_w=32, b_w=None, depth=1024, a_lat=1, b_lat=1,
        same_clock=True, a_per=FAST, b_per=FAST, cycles=2000, hot=8, seed=1, min_checked=1000):
  """One RAM configuration. Common-clock simple dual port reads old data across ports.

  min_checked: the test fails if fewer reads than this are compared after
  the collision and beyond-depth exclusions.
  """
  b_w = a_w if b_w is None else b_w
  cross_rdw_valid = same_clock and roles is SDP
  generics = dict(
    g_dut=dut, g_a_data_w=a_w, g_b_data_w=b_w, g_a_depth=depth,
    g_a_latency=a_lat, g_b_latency=b_lat, g_same_clock=same_clock,
    g_a_period_ps=a_per, g_b_period_ps=b_per if not same_clock else a_per,
    g_cross_rdw_valid=cross_rdw_valid, g_n_cycles=cycles, g_hot_addrs=hot, g_seed=seed,
    g_min_checked=min_checked,
    **roles,
  )
  return name, generics


RAM_CONFIGS = [
  # Core: simple dual port.
  ram("sdp.1clk.32x1024.lat1", "lm_mem_ram_sdp", SDP),
  ram("sdp.1clk.32x1024.lat2", "lm_mem_ram_sdp", SDP, b_lat=2, seed=2),
  ram("sdp.2clk.32x1024.lat1", "lm_mem_ram_sdp", SDP, same_clock=False, b_per=SLOW, seed=3),
  ram("sdp.2clk.32x1024.lat2", "lm_mem_ram_sdp", SDP, same_clock=False, a_per=SLOW, b_lat=2, seed=4),
  ram("sdp.1clk.16x64", "lm_mem_ram_sdp", SDP, a_w=16, depth=64, seed=5),
  ram("sdp.1clk.1x16", "lm_mem_ram_sdp", SDP, a_w=1, depth=16, hot=4, seed=6),
  ram("sdp.1clk.32to8", "lm_mem_ram_sdp", SDP, b_w=8, depth=256, seed=7),
  ram("sdp.2clk.8to32.lat2", "lm_mem_ram_sdp", SDP, a_w=8, b_w=32, depth=1024, b_lat=2,
      same_clock=False, b_per=SLOW, hot=16, seed=8),
  # Core: one writer, two readers.
  ram("tdp1w.1clk.32x1024.lat11", "lm_mem_ram_tdp1w", TDP1W, seed=11),
  ram("tdp1w.1clk.32x1024.lat21", "lm_mem_ram_tdp1w", TDP1W, a_lat=2, seed=12),
  ram("tdp1w.2clk.32x1024.lat12", "lm_mem_ram_tdp1w", TDP1W, b_lat=2, same_clock=False, b_per=SLOW, seed=13),
  ram("tdp1w.2clk.16x64.lat22", "lm_mem_ram_tdp1w", TDP1W, a_w=16, depth=64, a_lat=2, b_lat=2,
      same_clock=False, a_per=SLOW, seed=14),
  # Public wrappers.
  ram("r_w.32x1024.lat1", "lm_mem_ram_r_w", SDP, seed=21),
  ram("r_w.32x1024.lat2", "lm_mem_ram_r_w", SDP, b_lat=2, seed=22),
  ram("r_w.16x64", "lm_mem_ram_r_w", SDP, a_w=16, depth=64, seed=23),
  ram("cr_cw.32x1024.lat1", "lm_mem_ram_cr_cw", SDP, same_clock=False, b_per=SLOW, seed=24),
  ram("cr_cw.32x1024.lat2", "lm_mem_ram_cr_cw", SDP, b_lat=2, same_clock=False, a_per=SLOW, seed=25),
  ram("r_w_ratio.32to8", "lm_mem_ram_r_w_ratio", SDP, b_w=8, depth=256, seed=26),
  ram("r_w_ratio.8to32.lat2", "lm_mem_ram_r_w_ratio", SDP, a_w=8, b_w=32, b_lat=2, hot=16, seed=27),
  ram("r_w_ratio.32to16.lat2", "lm_mem_ram_r_w_ratio", SDP, b_w=16, depth=512, a_lat=2, b_lat=2, seed=28),
  ram("cr_cw_ratio.32to8", "lm_mem_ram_cr_cw_ratio", SDP, b_w=8, depth=256, same_clock=False, b_per=SLOW, seed=29),
  ram("cr_cw_ratio.8to32.lat2", "lm_mem_ram_cr_cw_ratio", SDP, a_w=8, b_w=32, b_lat=2, hot=16,
      same_clock=False, a_per=SLOW, seed=30),
  ram("crw_cr.1clk.32x1024.lat1", "lm_mem_ram_crw_cr", TDP1W, seed=31),
  ram("crw_cr.2clk.32x1024.lat2", "lm_mem_ram_crw_cr", TDP1W, a_lat=2, b_lat=2, same_clock=False, b_per=SLOW, seed=32),
  # Depths that are not a power of two: accesses beyond the depth are generated,
  # checked to be harmless and excluded from the comparison.
  ram("sdp.1clk.32x48", "lm_mem_ram_sdp", SDP, depth=48, seed=51),
  ram("sdp.2clk.32to8.depth48", "lm_mem_ram_sdp", SDP, b_w=8, depth=48, same_clock=False, b_per=SLOW, seed=52),
  ram("tdp1w.2clk.16x40.lat21", "lm_mem_ram_tdp1w", TDP1W, a_w=16, depth=40, a_lat=2,
      same_clock=False, a_per=SLOW, seed=53),
  ram("r_w_ratio.8to32.depth48", "lm_mem_ram_r_w_ratio", SDP, a_w=8, b_w=32, depth=48, hot=16, seed=54),
]


def fifo(name, *, dual=True, wr_w=16, rd_w=None, depth=16, lat=1, wr_per=FAST, rd_per=SLOW, n=400, seed=1):
  rd_w = wr_w if rd_w is None else rd_w
  return name, dict(
    g_dual_clock=dual, g_wr_data_w=wr_w, g_rd_data_w=rd_w, g_wr_depth=depth, g_rd_latency=lat,
    g_wr_period_ps=wr_per, g_rd_period_ps=rd_per if dual else wr_per, g_n_writes=n, g_seed=seed,
  )


FIFO_CONFIGS = [
  fifo("sync.16x16.lat1", dual=False, seed=41),
  fifo("sync.16x16.lat2", dual=False, lat=2, seed=42),
  fifo("async.16x16.lat1", seed=43),
  fifo("async.16x16.lat2.rdfast", lat=2, wr_per=SLOW, rd_per=FAST, seed=44),
  fifo("async.32x1024", wr_w=32, depth=1024, n=3000, seed=45),
  fifo("async.32to8", wr_w=32, rd_w=8, depth=16, seed=46),
  fifo("async.8to32", wr_w=8, rd_w=32, depth=64, seed=47),
  fifo("sync.32to8", dual=False, wr_w=32, rd_w=8, depth=16, seed=48),
]


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
    with heavy_tool_lock(f"VUnit {os.environ['VUNIT_SIMULATOR']}: {' '.join(argv) or 'all tests'}"):
      vu.main()


if __name__ == "__main__":
  main()
