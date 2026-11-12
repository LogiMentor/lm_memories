# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Static checks of generics in src/ (lm_mem_pkg f_static_check).

- Rule: no check in src/ is a plain assertion that stops a tool. Every
  assertion of severity error or failure is the one inside f_static_check;
  every C_ERROR_<CHECK> constant is a t_static_check from f_static_check,
  with its "-- ERROR:" comment on the same line. Assertions of severity
  note or warning (deprecation notes, run-time warnings of the simulation
  model and the FIFO) are allowed.
- Shape of the mechanism: a failed f_static_check returns 1, outside
  t_static_check (natural range 0 to 0), which is what stops Vivado without
  synth_design -assert; every C_ERROR_<CHECK> constant is declared in an
  architecture or a subprogram, never in a generate, block or process.
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
  ("r_w_ratio_wide_read_reset",
   "inst : entity work.lm_mem_ram_r_w_ratio\n"
   "  generic map(g_ram_a_data_w => 8, g_ram_a_depth => 16, g_ram_b_data_w => 16, g_ram_b_rst_en => true)\n"
   "  port map(clk_i => s_clk, wen_i => s_bit, wr_addr_i => s_nib, wr_dat_i => s_byte, rd_addr_i => s_nib(2 downto 0));",
   "lm_mem_ram_sdp: the read-output reset (g_rd_rst_en) is not supported with a read port wider than the write port"),
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

  def test_a_failed_check_is_out_of_range_of_its_constant(self) -> None:
    # Without synth_design -assert, Vivado skips the assertion and stops only
    # because the constant is out of range: [Synth 8-11323] "assigned value
    # '1' out of range" at the C_ERROR_ line (campaign pilot of 2026-10-19).
    # GHDL stops at the assertion first, so the GHDL cases below cannot show
    # that the range part works: this test keeps its shape.
    code = _code((ROOT_DIR / "src" / "common" / "lm_mem_pkg.vhd").read_text(encoding="utf-8"))
    self.assertRegex(code, r"subtype\s+t_static_check\s+is\s+natural\s+range\s+0\s+to\s+0\s*;")
    body = re.search(r"function\s+f_static_check\s*\([^)]*\)\s*return\s+natural\s+is\b(.*?)"
                     r"\bend\s+function\s+f_static_check\s*;", code,
                     re.IGNORECASE | re.DOTALL)
    self.assertIsNotNone(body)
    # Starts at 1 and becomes 0 only when ok holds: a failed check returns 1.
    self.assertRegex(body.group(1), r"variable\s+v_result\s*:\s*natural\s*:=\s*1\s*;")
    self.assertRegex(body.group(1), r"if\s+ok\s+then\s+v_result\s*:=\s*0\s*;\s*end\s+if\s*;")
    self.assertNotRegex(body.group(1), r"v_result\s*:=(?!\s*0\s*;)")

  def test_every_check_is_declared_where_it_is_always_elaborated(self) -> None:
    # A constant in an architecture is elaborated with the entity, one in a
    # subprogram with each call. One inside a generate, block or process
    # would be skipped when that branch is not elaborated.
    for path in sorted((ROOT_DIR / "src").rglob("*.vhd")):
      lines = _code(path.read_text(encoding="utf-8")).splitlines()
      scope = None
      for number, line in enumerate(lines, start=1):
        if re.match(r"\s*end\b", line, re.IGNORECASE):
          continue
        if re.search(r"\bgenerate\b", line, re.IGNORECASE):
          scope = "generate"
        elif re.search(r"\b(process|block)\b", line, re.IGNORECASE):
          scope = "process or block"
        elif re.match(r"\s*((pure|impure)\s+)?(function|procedure)\b", line, re.IGNORECASE):
          scope = "subprogram"
        elif re.match(r"\s*architecture\b", line, re.IGNORECASE):
          scope = "architecture"
        if re.match(r"\s*constant\s+C_ERROR_\w+\s*:", line):
          where = f"{path.relative_to(ROOT_DIR).as_posix()}:{number}"
          self.assertIn(scope, ("architecture", "subprogram"), where)


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


  def test_reset_of_a_disabled_port_warns_in_simulation(self) -> None:
    # Issue #7: with the read-output reset disabled (default), a low reset
    # input has no effect, and simulation warns about it; it does not stop.
    body = ("inst : entity work.lm_mem_ram_r_w generic map(g_ram_data_w => 8, g_ram_depth => 16)\n"
            "  port map(clk_i => s_clk, wen_i => s_bit, wr_addr_i => s_nib, wr_dat_i => s_byte, "
            "rd_addr_i => s_nib, rd_rst_n_i => s_bit);")
    top = self.work / "tb_static_disabled_reset.vhd"
    top.write_text(TOP.format(name="disabled_reset", body=body), encoding="utf-8")
    analyze = subprocess.run([GHDL, "-a", "--std=08", f"--workdir={self.work}", str(top)],
                             capture_output=True, text=True)
    self.assertEqual(analyze.returncode, 0, analyze.stderr)
    run = subprocess.run([GHDL, "--elab-run", "--std=08", f"--workdir={self.work}", "tb_static_disabled_reset",
                          "--stop-time=10ns", "--assert-level=error"], cwd=self.work, capture_output=True, text=True)
    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
    self.assertIn("lm_mem_ram_sdp: rd_rst_n_i is low, but the read-output reset is disabled (g_rd_rst_en = false)",
                  run.stdout + run.stderr)


if __name__ == "__main__":
  unittest.main()
