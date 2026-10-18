#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Check that the VUnit suite detects deliberate defects in the RAM cores
and in the simulation architecture of lm_mem_ram_crw_crw.

The check never modifies the repository. It copies the sources (src/, sim/,
manifests/, tools/) into a temporary directory, applies each mutation to the
copy, runs the VUnit configurations that should catch it from the copy, and
puts the original file back into the copy before the next mutation. A
mutation passes only if a test fails at simulation time; a compile error does
not count. Each VUnit run holds the machine-wide EDA lock (run.py).

  python sim/vunit/mutation_check.py

It checks the sources as they are in the working tree, including
uncommitted changes.

Not covered by design: the data a model returns for a read/write collision
or beyond the depth, because the scoreboard excludes those reads.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
COPIED = ("src", "sim", "manifests", "tools")
IGNORED = shutil.ignore_patterns("build", "__pycache__", "*.bak")

# Paths relative to the repository root (and to the copy).
SDP = Path("src") / "core" / "lm_mem_ram_sdp.vhd"
TDP1W = Path("src") / "core" / "lm_mem_ram_tdp1w.vhd"
CRW_CRW_SIM = Path("src") / "sim" / "lm_mem_ram_crw_crw_rtl.vhd"

# (description, file, original text, mutated text, VUnit test patterns)
MUTATIONS = [
  ("sdp: reversed sub-word order on read", SDP,
   "to_unsigned(i, C_RD_SEL)", "to_unsigned(C_RD_WORDS - 1 - i, C_RD_SEL)",
   ["*sdp.2clk.8to32.lat2*", "*r_w_ratio.8to32.lat2*"]),
  ("sdp: second output stage not gated by enb_i", SDP,
   "        end loop;\n        s_rd_dat_p <= s_rd_dat;\n      end if;",
   "        end loop;\n      end if;\n      s_rd_dat_p <= s_rd_dat;",
   ["*sdp.1clk.32x1024.lat2*"]),
  ("sdp: write ignores ena_i", SDP,
   "if (ena_i = '1') and (wen_i = '1') then", "if wen_i = '1' then",
   ["*sdp.1clk.32x1024.lat1*"]),
  ("sdp: latency 2 output taken from stage 1", SDP,
   "    rd_dat_o <= s_rd_dat_p;", "    rd_dat_o <= s_rd_dat;",
   ["*r_w.32x1024.lat2*"]),
  ("tdp1w: port A write-first instead of read-first", TDP1W,
   "        s_data_rd_a <= s_mem(to_integer(unsigned(addr_a_i)));",
   "        if wen_a_i = '1' then s_data_rd_a <= data_wr_a_i;"
   " else s_data_rd_a <= s_mem(to_integer(unsigned(addr_a_i))); end if;",
   ["*tdp1w.1clk.32x1024.lat11*"]),
  ("tdp1w: port B read ignores enb_i", TDP1W,
   "      if enb_i = '1' then\n        s_data_rd_b <= s_mem(",
   "      if true then\n        s_data_rd_b <= s_mem(",
   ["*crw_cr.1clk.32x1024.lat1*"]),
  ("crw_crw sim: port A write ignores ena_i", CRW_CRW_SIM,
   "v_wr_a_on := v_rd_a_on and (wen_a_i = '1');", "v_wr_a_on := v_edge_a and (wen_a_i = '1');",
   ["*crw_crw.2clk.32x1024.lat12*"]),
  ("crw_crw sim: port B write-first instead of read-first", CRW_CRW_SIM,
   "          v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := v_mem(v_c);",
   "          if v_wr_b_on then v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) :="
   " data_wr_b_i((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH);"
   " else v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := v_mem(v_c); end if;",
   ["*crw_crw.1clk.32x1024.lat11*"]),
  ("crw_crw sim: write/write collision leaves the cell defined", CRW_CRW_SIM,
   "          v_undef(v_c)  := v_hit;\n          v_wr_t_b(v_c) := now;",
   "          v_undef(v_c)  := false;\n          v_wr_t_b(v_c) := now;",
   ["*crw_crw.1clk.32x1024.lat11*"]),
  ("crw_crw sim: port B latency 2 output taken from stage 1", CRW_CRW_SIM,
   "    data_rd_b_o <= s_data_rd_b_p;", "    data_rd_b_o <= s_data_rd_b;",
   ["*crw_crw.2clk.32x1024.lat12*"]),
  ("crw_crw sim: reversed sub-word order on the wide port B read", CRW_CRW_SIM,
   "        v_c := v_base_b + i;\n        if v_c >= C_CELLS then\n          v_rd_b(",
   "        v_c := v_base_b + (C_B_WORDS - 1 - i);\n        if v_c >= C_CELLS then\n          v_rd_b(",
   ["*crw_crw.1clk.8to32.lat22*"]),
  ("crw_crw sim: port B write ignores wen_b_i", CRW_CRW_SIM,
   "v_wr_b_on := v_rd_b_on and (wen_b_i = '1');", "v_wr_b_on := v_rd_b_on;",
   ["*rw_rw.32x1024.lat1*"]),
]


def _src_digest() -> dict[str, str]:
  return {p.relative_to(ROOT_DIR).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in sorted((ROOT_DIR / "src").rglob("*")) if p.is_file()}


def main() -> int:
  before = _src_digest()
  with tempfile.TemporaryDirectory(prefix="lm_mutation_") as tmp:
    copy = Path(tmp)
    for folder in COPIED:
      shutil.copytree(ROOT_DIR / folder, copy / folder, ignore=IGNORED)
    results = run_mutations(copy)
  for name, verdict, tests in results:
    print(f"{verdict:14} | {name} | {tests}")
  if _src_digest() != before:
    print("ERROR: files under src/ changed during the mutation check", file=sys.stderr)
    return 1
  return 0 if all(verdict == "DETECTED" for _, verdict, _ in results) else 1


def run_mutations(copy: Path) -> list[tuple[str, str, str]]:
  # VUnit output stays inside the copy.
  env = {k: v for k, v in os.environ.items() if k != "LM_MEM_BUILD_DIR"}
  results = []
  for name, rel, old, new, tests in MUTATIONS:
    original = (ROOT_DIR / rel).read_text(encoding="utf-8")
    target = copy / rel
    if old not in original:
      results.append((name, "ANCHOR MISSING", ""))
      continue
    try:
      target.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
      proc = subprocess.run([sys.executable, str(copy / "sim" / "vunit" / "run.py"), "-p", "4", *tests],
                            cwd=copy, env=env, capture_output=True, text=True)
      out = proc.stdout + proc.stderr
      if "Compile failed" in out or "compilation error" in out:
        verdict = "COMPILE ERROR"
      elif proc.returncode != 0 and "fail" in out:
        verdict = "DETECTED"
      else:
        verdict = "NOT DETECTED"
      results.append((name, verdict, " ".join(tests)))
    finally:
      target.write_text(original, encoding="utf-8", newline="\n")
  return results


if __name__ == "__main__":
  raise SystemExit(main())
