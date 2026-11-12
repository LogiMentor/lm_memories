# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the read-output reset checks of tools/vendor_synth (issue #7):
the pin report of the memory cells, the reset pin check, the exact LUT and
register counts, and the generated top of the "component_default" cases."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from tools.vendor_synth import check, cli  # noqa: E402

# Pin report of r_w_32x1024_lat2_rst on xc7a35tfgg484-2 (Vivado 2023.1), with
# the cell attributes of the report.
LAT2 = "\n".join([
  "cell\tname=inst_mem/s_mem_reg\tref=RAMB36E1\tDOA_REG=0\tDOB_REG=1\tIS_RSTRAMARSTRAM_INVERTED=\t"
  "IS_RSTRAMB_INVERTED=\tIS_RSTREGARSTREG_INVERTED=\tIS_RSTREGB_INVERTED=\tRSTREG_PRIORITY_A=RSTREG\t"
  "RSTREG_PRIORITY_B=RSTREG",
  "pin\tcell=inst_mem/s_mem_reg\tpin=ENARDEN\tports=ena_i\tdriver=-",
  "pin\tcell=inst_mem/s_mem_reg\tpin=ENBWREN\tports=enb_i\tdriver=-",
  "pin\tcell=inst_mem/s_mem_reg\tpin=REGCEAREGCE\tports=-\tdriver=GND",
  "pin\tcell=inst_mem/s_mem_reg\tpin=REGCEB\tports=enb_i\tdriver=-",
  "pin\tcell=inst_mem/s_mem_reg\tpin=RSTRAMARSTRAM\tports=-\tdriver=GND",
  "pin\tcell=inst_mem/s_mem_reg\tpin=RSTRAMB\tports=-\tdriver=LUT2\tinit=4'h2\tI0=enb_i\tI1=rd_rst_n_i",
  "pin\tcell=inst_mem/s_mem_reg\tpin=RSTREGARSTREG\tports=-\tdriver=GND",
  "pin\tcell=inst_mem/s_mem_reg\tpin=RSTREGB\tports=-\tdriver=LUT2\tinit=4'h2\tI0=enb_i\tI1=rd_rst_n_i",
  "end",
])
RESETS = {"rd_rst_n_i": "enb_i"}


def report(text: str = LAT2) -> dict:
  return check.parse_ram_reset_pins(text)


class ResetPinCheckTest(unittest.TestCase):
  def test_measured_report_passes(self) -> None:
    self.assertEqual(check.check_reset_pins(report(), RESETS, ["RSTRAM", "RSTREG"]), [])

  def test_lut_inputs_in_the_other_order(self) -> None:
    # I0 = reset, I1 = enable: "enable AND NOT reset" is INIT 4'h4.
    text = LAT2.replace("init=4'h2\tI0=enb_i\tI1=rd_rst_n_i", "init=4'h4\tI0=rd_rst_n_i\tI1=enb_i")
    self.assertEqual(check.check_reset_pins(report(text), RESETS, ["RSTRAM", "RSTREG"]), [])

  def test_missing_report_or_cells(self) -> None:
    self.assertTrue(check.check_reset_pins(None, RESETS, ["RSTRAM"]))
    self.assertTrue(check.check_reset_pins(report(LAT2.replace("\nend", "")), RESETS, ["RSTRAM"]))
    self.assertIn("no URAM cell", check.check_reset_pins(report(), RESETS, ["RST_"], "URAM")[0])

  def test_latency_2_needs_rstreg(self) -> None:
    text = LAT2.replace("pin=RSTREGB\tports=-\tdriver=LUT2\tinit=4'h2\tI0=enb_i\tI1=rd_rst_n_i",
                        "pin=RSTREGB\tports=-\tdriver=GND")
    problems = check.check_reset_pins(report(text), RESETS, ["RSTRAM", "RSTREG"])
    self.assertEqual(len(problems), 1)
    self.assertIn("no RSTREG pin", problems[0])

  def test_reset_not_gated_by_the_enable(self) -> None:
    # A LUT1 inverter only (reset acts with the enable low): not the agreed semantics.
    text = LAT2.replace("init=4'h2\tI0=enb_i\tI1=rd_rst_n_i", "init=2'h1\tI0=rd_rst_n_i")
    self.assertTrue(check.check_reset_pins(report(text), RESETS, ["RSTRAM"]))

  def test_wrong_polarity(self) -> None:
    # enable AND reset: the reset would be active high.
    text = LAT2.replace("init=4'h2", "init=4'h8")
    self.assertTrue(check.check_reset_pins(report(text), RESETS, ["RSTRAM"]))

  def test_reset_on_the_enable_path(self) -> None:
    text = LAT2.replace("pin=ENBWREN\tports=enb_i\tdriver=-",
                        "pin=ENBWREN\tports=-\tdriver=LUT2\tinit=4'hE\tI0=rd_rst_n_i\tI1=enb_i")
    problems = check.check_reset_pins(report(text), RESETS, ["RSTRAM", "RSTREG"])
    self.assertEqual(problems, ["inst_mem/s_mem_reg (RAMB36E1): enable pin ENBWREN depends on rd_rst_n_i"])

  def test_inverted_reset_pin(self) -> None:
    text = LAT2.replace("IS_RSTRAMB_INVERTED=\t", "IS_RSTRAMB_INVERTED=1'b1\t")
    problems = check.check_reset_pins(report(text), RESETS, ["RSTRAM"])
    self.assertEqual(len(problems), 1)
    self.assertIn("IS_RSTRAMB_INVERTED", problems[0])

  def test_every_block_ram_needs_the_reset(self) -> None:
    second = "\n".join(line.replace("inst_mem/s_mem_reg", "inst_mem/s_mem_reg_1")
                       .replace("driver=LUT2\tinit=4'h2\tI0=enb_i\tI1=rd_rst_n_i", "driver=GND")
                       for line in LAT2.split("\n")[:-1])
    problems = check.check_reset_pins(report(LAT2.replace("end", second + "\nend")), RESETS, ["RSTRAM"])
    self.assertEqual(len(problems), 1)
    self.assertIn("inst_mem/s_mem_reg_1", problems[0])

  def test_flip_flops_of_distributed_ram(self) -> None:
    text = "\n".join([
      "cell\tname=inst_mem/s_rd_dat_reg[0]\tref=FDRE\tIS_R_INVERTED=1'b0",
      "pin\tcell=inst_mem/s_rd_dat_reg[0]\tpin=CE\tports=enb_i\tdriver=-",
      "pin\tcell=inst_mem/s_rd_dat_reg[0]\tpin=R\tports=-\tdriver=LUT2\tinit=4'h2\tI0=enb_i\tI1=rd_rst_n_i",
      "end"])
    self.assertEqual(check.check_reset_pins(report(text), RESETS, ["R"], "FD"), [])

  def test_summary_names_the_reset_pins(self) -> None:
    summary = check.reset_pin_summary(report())
    self.assertEqual(list(summary), ["rd_rst_n_i"])
    self.assertEqual(len(summary["rd_rst_n_i"]), 2)

  def test_exact_lut_and_register_counts(self) -> None:
    metrics = {"luts": 1.0, "luts_logic": 1.0, "lutram": 0.0, "registers": 0.0, "bram_tiles": 1.0}
    log = {"critical_warnings": 0, "message_ids": {}, "limited_messages": []}
    self.assertEqual(check.compare(metrics, log, {"luts_logic": 1, "registers": 0}, "xc7a35tfgg484-2"), [])
    self.assertTrue(check.compare({**metrics, "luts_logic": 0.0}, log, {"luts_logic": 1}, "xc7a35tfgg484-2"))
    self.assertTrue(check.compare({**metrics, "luts_logic": 2.0}, log, {"luts_logic": 1}, "xc7a35tfgg484-2"))


class ComponentDefaultTopTest(unittest.TestCase):
  JOB = {"name": "vivado-artix7", "tool": "vivado", "vendor": "amd", "part": "xc7a35tfgg484-2"}

  def test_component_without_the_reset_port(self) -> None:
    case = {"name": "r_w_32x1024", "kind": "component_default", "entity": "lm_mem_ram_r_w",
            "omit_ports": ["rd_rst_n_i"], "generics": {"g_ram_data_w": 32, "g_ram_depth": 1024}}
    top = cli._top_name(self.JOB, case)
    text = cli._render_top(self.JOB, case, top)
    self.assertIn("component lm_mem_ram_r_w is", text)
    self.assertIn("inst_mem : lm_mem_ram_r_w", text)
    code = "\n".join(line for line in text.splitlines() if not line.startswith("--"))
    self.assertNotIn("rd_rst_n_i", code)
    self.assertIn("rd_dat_o : out std_logic_vector(g_ram_data_w - 1 downto 0)", text)
    self.assertEqual(cli._generics_module(case, top), "lm_mem_ram_r_w")

  def test_unknown_or_mandatory_port_is_refused(self) -> None:
    case = {"name": "x", "kind": "component_default", "entity": "lm_mem_ram_r_w", "omit_ports": ["clk_i"]}
    with self.assertRaises(SystemExit):
      cli._render_top(self.JOB, case, "top")
    case["omit_ports"] = ["no_such_port"]
    with self.assertRaises(SystemExit):
      cli._render_top(self.JOB, case, "top")

  def test_v020_component_without_the_reset_generic_and_port(self) -> None:
    case = {"name": "crw_cr", "kind": "component_default", "entity": "lm_mem_ram_crw_cr",
            "omit_generics": ["g_ram_a_rst_en", "g_ram_b_rst_en"], "omit_ports": ["rst_a_n_i", "rst_b_n_i"],
            "generics": {"g_ram_data_w": 32}}
    code = "\n".join(line for line in cli._render_top(self.JOB, case, "top").splitlines() if not line.startswith("--"))
    self.assertNotIn("rst", code)
    case["generics"] = {"g_ram_a_rst_en": True}
    with self.assertRaises(SystemExit):
      cli._render_top(self.JOB, case, "top")

  def test_cases(self) -> None:
    cases = {c["name"]: c for c in check.load_cases()}
    # The cases of v0.2.0 are entity cases again: the reset is disabled by default.
    for name in ("r_w_32x1024", "cr_cw_32x1024", "r_w_ratio_32to8", "cr_cw_ratio_8to32", "crw_cr_32x1024",
                 "r_w_36x4096"):
      self.assertEqual(cases[name]["kind"], "entity", name)
      self.assertFalse(any(k.endswith("_rst_en") for k in cases[name]["generics"]), name)
    # Every reset case enables the reset; none has a read port wider than the write port.
    for name, case in cases.items():
      if name.endswith("_rst") and not name.startswith("bad_"):
        self.assertTrue(any(k.endswith("_rst_en") and v for k, v in case["generics"].items()), name)
        g = case["generics"]
        self.assertLessEqual(g.get("g_ram_b_data_w", 0), g.get("g_ram_a_data_w", 1 << 30), name)
    self.assertEqual(cases["r_w_32x1024_v020_component"]["kind"], "component_default")


if __name__ == "__main__":
  unittest.main()
