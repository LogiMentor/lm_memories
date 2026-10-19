# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Static checks of generics in src/ (lm_mem_pkg f_static_check).

- Rule: no check in src/ is a plain assertion that stops a tool. Every
  assertion of severity error or failure is the one inside f_static_check;
  every C_ERROR_<CHECK> constant is a t_static_check from f_static_check,
  with its "-- ERROR:" comment on the same line. Assertions of severity
  note or warning (deprecation notes, run-time warnings of the simulation
  model and the FIFO) are allowed.
- Negative cases with GHDL (skipped where GHDL is not installed): each
  check added for the reviews of PR #4 stops elaboration with its message.
  The synthesis tools check the same cases in tools/vendor_synth
  (fallback_check.toml, quartus_check.toml).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.manifest import ROOT_DIR, load

GHDL = os.environ.get("GHDL") or shutil.which("ghdl")
ASSERT_RE = re.compile(r"\bassert\b(.*?);", re.IGNORECASE | re.DOTALL)
SEVERITY_RE = re.compile(r"\bseverity\s+(\w+)", re.IGNORECASE)


def _code(text: str) -> str:
  """VHDL text without comments and string literals."""
  text = re.sub(r'"[^"\n]*"', '""', text)
  return re.sub(r"--[^\n]*", "", text)


# (name, VHDL architecture body using the library, expected message)
NEGATIVE = [
  ("ccd_resync_levels1",
   "inst : entity work.lm_mem_ccd_resync generic map(g_meta_levels => 1)\n"
   "  port map(clk_i => s_clk, ccd_din_i => s_bit, ccd_din_o => open);",
   "lm_mem_ccd_resync: g_meta_levels must be at least 2"),
  ("fifo_almost_full_0",
   "inst : entity work.lm_mem_fifo_sync generic map(g_data_w => 8, g_depth => 16, g_almost_full_limit => 0)\n"
   "  port map(clk_i => s_clk, rst_n_i => s_bit, din_i => s_byte, wen_i => s_bit, ren_i => s_bit);",
   "lm_mem_fifo: g_almost_full_limit must be in 1 to g_wr_depth"),
  ("fifo_almost_empty_15",
   "inst : entity work.lm_mem_fifo_sync generic map(g_data_w => 8, g_depth => 16, g_almost_empty_limit => 15)\n"
   "  port map(clk_i => s_clk, rst_n_i => s_bit, din_i => s_byte, wen_i => s_bit, ren_i => s_bit);",
   "lm_mem_fifo: g_almost_empty_limit must be in 0 to read depth - 2"),
  ("r_w_infer_latency3",
   "inst : entity work.lm_mem_ram_r_w_infer generic map(g_ram_latency => 3, g_ram_data_w => 8, g_ram_depth => 16)\n"
   "  port map(clk_i => s_clk, wen_i => s_bit, wr_addr_i => s_nib, wr_dat_i => s_byte, rd_addr_i => s_nib);",
   "lm_mem_ram_r_w_infer: latency must be 1 or 2"),
  ("r_w_infer_no_vendor",
   "inst : entity work.lm_mem_ram_r_w_infer generic map(g_ram_data_w => 8, g_ram_depth => 16, g_vendor => \"\")\n"
   "  port map(clk_i => s_clk, wen_i => s_bit, wr_addr_i => s_nib, wr_dat_i => s_byte, rd_addr_i => s_nib);",
   "lm_mem_ram_r_w_infer: vendor, family, and tool strings must be set"),
  ("xilinx_prim_no_family",
   "inst : entity work.lm_mem_xilinx_ram_r_w_prim generic map(g_ram_data_w => 8, g_ram_depth => 16, g_family => \"\")\n"
   "  port map(clk_i => s_clk, wen_i => s_bit, wr_addr_i => s_nib, wr_dat_i => s_byte, rd_addr_i => s_nib);",
   "lm_mem_xilinx_ram_r_w_prim: family and primitive must be set"),
  ("bin2gray_1bit", "s_one <= f_bin2gray(s_bitv);", "lm_mem_pkg.f_bin2gray: input length must be greater than 1"),
  ("gray2bin_1bit", "s_one <= f_gray2bin(s_bitv);", "lm_mem_pkg.f_gray2bin: input length must be greater than 1"),
]

TOP = """library ieee;
use ieee.std_logic_1164.all;
use work.lm_mem_pkg.all;
entity tb_static_{name} is
end entity;
architecture a_tb of tb_static_{name} is
  signal s_clk  : std_logic := '0';
  signal s_bit  : std_logic := '0';
  signal s_bitv : std_logic_vector(0 downto 0) := "0";
  signal s_one  : std_logic_vector(0 downto 0);
  signal s_nib  : std_logic_vector(3 downto 0) := (others => '0');
  signal s_byte : std_logic_vector(7 downto 0) := (others => '0');
begin
  {body}
end architecture;
"""


class StaticCheckRuleTest(unittest.TestCase):
  def test_no_plain_assertion_stops_a_tool(self) -> None:
    offenders = []
    for path in sorted((ROOT_DIR / "src").rglob("*.vhd")):
      for match in ASSERT_RE.finditer(_code(path.read_text(encoding="utf-8"))):
        severity = SEVERITY_RE.search(match.group(1))
        level = severity.group(1).lower() if severity else "error"  # VHDL default
        if level in ("error", "failure"):
          offenders.append(f"{path.relative_to(ROOT_DIR).as_posix()}: assert {match.group(1).strip()[:60]}")
    # The single allowed one: the assertion inside f_static_check.
    self.assertEqual(offenders, ["src/common/lm_mem_pkg.vhd: assert ok report msg severity failure"])

  def test_every_error_constant_is_a_static_check(self) -> None:
    for path in sorted((ROOT_DIR / "src").rglob("*.vhd")):
      for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if re.match(r"\s*constant\s+C_ERROR_\w+\s*:", line):
          where = f"{path.relative_to(ROOT_DIR).as_posix()}:{number}"
          self.assertIn(": t_static_check := f_static_check(", line, where)
          self.assertIn("-- ERROR:", line, where)


@unittest.skipUnless(GHDL, "GHDL not installed")
class StaticCheckGhdlTest(unittest.TestCase):
  @classmethod
  def setUpClass(cls) -> None:
    cls.tmp = tempfile.TemporaryDirectory()
    cls.work = Path(cls.tmp.name)
    for source in load().files("sim"):
      subprocess.run([GHDL, "-a", "--std=08", f"--workdir={cls.work}", str(source.path)], check=True,
                     capture_output=True, text=True)

  @classmethod
  def tearDownClass(cls) -> None:
    cls.tmp.cleanup()

  def test_each_check_stops_elaboration_with_its_message(self) -> None:
    for name, body, message in NEGATIVE:
      with self.subTest(name):
        top = self.work / f"tb_static_{name}.vhd"
        top.write_text(TOP.format(name=name, body=body), encoding="utf-8")
        analyze = subprocess.run([GHDL, "-a", "--std=08", f"--workdir={self.work}", str(top)],
                                 capture_output=True, text=True)
        self.assertEqual(analyze.returncode, 0, analyze.stderr)
        run = subprocess.run([GHDL, "--elab-run", "--std=08", f"--workdir={self.work}", f"tb_static_{name}",
                              "--stop-time=10ns"], cwd=self.work, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0, f"{name}: elaboration did not stop")
        self.assertIn(message, run.stdout + run.stderr)


if __name__ == "__main__":
  unittest.main()
