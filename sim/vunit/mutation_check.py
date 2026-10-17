#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Check that the VUnit suite detects deliberate defects in the RAM cores.

Each mutation edits one core file, runs the VUnit configurations that should
catch it, and restores the file from a backup (also on error or Ctrl-C).
A mutation passes only if a test fails at simulation time; a compile error
does not count.

  python sim/vunit/mutation_check.py

Do not run it while other work on src/core/ is uncommitted.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SDP = ROOT_DIR / "src" / "core" / "lm_mem_ram_sdp.vhd"
TDP1W = ROOT_DIR / "src" / "core" / "lm_mem_ram_tdp1w.vhd"

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
]


def main() -> int:
  results = []
  for name, path, old, new, tests in MUTATIONS:
    backup = path.with_suffix(".vhd.bak")
    shutil.copy2(path, backup)
    try:
      text = path.read_text(encoding="utf-8")
      if old not in text:
        results.append((name, "ANCHOR MISSING", ""))
        continue
      path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
      proc = subprocess.run([sys.executable, str(ROOT_DIR / "sim" / "vunit" / "run.py"), "-p", "4", *tests],
                            cwd=ROOT_DIR, capture_output=True, text=True)
      out = proc.stdout + proc.stderr
      if "Compile failed" in out or "compilation error" in out:
        verdict = "COMPILE ERROR"
      elif proc.returncode != 0 and "fail" in out:
        verdict = "DETECTED"
      else:
        verdict = "NOT DETECTED"
      results.append((name, verdict, " ".join(tests)))
    finally:
      shutil.move(backup, path)

  for name, verdict, tests in results:
    print(f"{verdict:14} | {name} | {tests}")
  return 0 if all(verdict == "DETECTED" for _, verdict, _ in results) else 1


if __name__ == "__main__":
  raise SystemExit(main())
