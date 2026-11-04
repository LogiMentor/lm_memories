# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the verdicts and the netlist reuse of sim/scripts/run_postsynth.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("run_postsynth", ROOT_DIR / "sim" / "scripts" / "run_postsynth.py")
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

COUNTS = " ".join(f"{k}={0 if k == 'errors' else 5}" for k in ps.REQUIRED_COUNTS)
HARNESS = f"lm_mem_tb_harness lm_mem_ram_crw_crw [GHDL_6.0.0]: {COUNTS}\n"
PASS = "tb_lm_mem_ram_plain: RESULT PASS errors=0\n"
FAIL = "tb_lm_mem_ram_plain: RESULT FAIL errors=1\n"


class ParseResultTest(unittest.TestCase):
  def test_clean_run_passes(self) -> None:
    result = ps.parse_result(HARNESS + PASS, 0)
    self.assertEqual(result["verdict"], "PASS", result["problems"])
    self.assertEqual(result["counts"]["checked"], 5)

  def test_pass_followed_by_fail_fails(self) -> None:
    # Review of PR #4: the first verdict was taken, with empty counters.
    result = ps.parse_result(PASS + FAIL, 0)
    self.assertEqual(result["verdict"], "FAIL")

  def test_exit_code_is_required(self) -> None:
    self.assertEqual(ps.parse_result(HARNESS + PASS, 1)["verdict"], "FAIL")
    self.assertEqual(ps.parse_result(HARNESS + PASS, ps.TIMEOUT_RC)["verdict"], "TIMEOUT")

  def test_counters_are_required(self) -> None:
    self.assertEqual(ps.parse_result(PASS, 0)["verdict"], "FAIL")
    partial = HARNESS.replace(" hold_checks=5", "")
    result = ps.parse_result(partial + PASS, 0)
    self.assertEqual(result["verdict"], "FAIL")
    self.assertIn("hold_checks", result["problems"][0])
    self.assertEqual(ps.parse_result(HARNESS + HARNESS + PASS, 0)["verdict"], "FAIL")

  def test_fatal_diagnostic_after_the_result_fails(self) -> None:
    for fatal in ("x.vhd:10:5:@1us:(report failure): late\n", "ERROR: [XSIM 43-3316] Signal SIGSEGV received.\n",
                  "x.vhd:1:1:@0ms:(assertion failure): bound check failure\n"):
      self.assertEqual(ps.parse_result(HARNESS + PASS + fatal, 0)["verdict"], "FAIL", fatal)


UNISIM_PATH = (r"::tb_lm_mem_ram_plain:inst_harness:inst_dut:gen_crw_crw_16x64_16_l11:inst_dut:"
               r"\inst/gen_crw_crw.inst_dut/sv_mem_reg\:tdp:ramb18e1_tdp_inst:")
RAMB = {r"\inst/gen_crw_crw.inst_dut/sv_mem_reg" + "\\": {"primitive": "RAMB18E1", "READ_WIDTH_A": 18,
                                                         "READ_WIDTH_B": 18, "WRITE_WIDTH_A": 18,
                                                         "WRITE_WIDTH_B": 18}}
COLL = {"instances": RAMB, "a_w": 16, "b_w": 16}


def model_ww(t_ns: int, addr: str, sim: str = "ghdl") -> str:
  # The shape of the real logs of crw_crw.1clk.16x64 on xc7a35tfgg484-2 (2026-11-04).
  head = (f"Memory Collision Error on RAMB18E1 {UNISIM_PATH} at simulation time {t_ns} ns.\n"
          f" A write was requested to the same address simultaneously at both port A and port B of the RAM. "
          f"The contents written to the RAM at address location {addr} (hex) of port A and address location "
          f"{addr} (hex) of port B are unknown. \n")
  if sim == "ghdl":
    return f"RAMB18E1.vhd:1424:11:@{t_ns}ns:(assertion error):  " + head
  return "Error:  " + head + f"Time: {t_ns} ns  Iteration: 3  Process: /x/prcs_clk  File: RAMB18E1.vhd\n"


def model_rw(t_ns: int, addr: str, read_port: str = "B") -> str:
  write_port = "A" if read_port == "B" else "B"
  return (f"RAMB18E1.vhd:1471:13:@{t_ns}ns:(assertion error):  Memory Collision Error on RAMB18E1 {UNISIM_PATH} "
          f"at simulation time {t_ns} ns.\n A read was performed on address {addr} (hex) of port {read_port} while "
          f"a write was requested to the same address on port {write_port}.  The write will be successful however "
          "the read value on port B is unknown until the next CLKB cycle. \n")


def harness_ww(cell: int, t_ns: int) -> str:
  return (f"lm_mem_tb_pkg.vhd:428:9:@{t_ns}ns:(report warning): lm_mem_tb_pkg: write/write collision on cells "
          f"{cell} to {cell} at {t_ns * 1000} ps; the cells of both writes become undefined\n")


def harness_rw(port: str, cell: int, t_rd: int, t_wr: int) -> str:
  return (f"lm_mem_tb_pkg.vhd:556:15:@{t_rd + 5}ns:(report warning): lm_mem_tb_pkg: read/write collision on port "
          f"{port}           cell {cell} (read at {t_rd * 1000} ps, write at {t_wr * 1000} ps); read excluded\n")


def harness_line(ww: int, rw: int) -> str:
  counts = " ".join(f"{k}={0 if k == 'errors' else 5}" for k in ps.REQUIRED_COUNTS
                    if k not in ("collisions_ww", "collisions_rw"))
  return f"lm_mem_tb_harness lm_mem_ram_crw_crw [GHDL_6.0.0]: {counts} collisions_rw={rw} collisions_ww={ww}\n"


class CollisionPairingTest(unittest.TestCase):
  # Netlist simulations: a collision error of a UNISIM block RAM model is
  # accepted only when paired with a collision of the harness (2026-11-04:
  # 13 "Memory Collision Error on RAMB18E1" = collisions_ww=13 failed the run).
  def _run(self, model: str, harness: str, ww: int, rw: int = 0) -> dict:
    return ps.parse_result(harness + model + harness_line(ww, rw) + PASS, 0, COLL)

  def test_each_model_collision_paired_passes(self) -> None:
    for sim in ("ghdl", "xsim"):
      model = model_ww(1460, "0050", sim) + model_ww(2750, "00d0", sim)
      result = self._run(model, harness_ww(5, 1460) + harness_ww(13, 2750), ww=2)
      self.assertEqual(result["verdict"], "PASS", (sim, result["problems"]))
      self.assertEqual(result["collisions"]["accepted"], 2)

  def test_one_more_in_the_model_fails(self) -> None:
    model = model_ww(1460, "0050") + model_ww(2750, "00d0") + model_ww(3840, "0030")
    result = self._run(model, harness_ww(5, 1460) + harness_ww(13, 2750), ww=2)
    self.assertEqual(result["verdict"], "FAIL")
    self.assertTrue(any("no matching collision in the harness" in p for p in result["problems"]), result)
    self.assertTrue(any("1 fatal diagnostics" in p for p in result["problems"]), result)

  def test_one_less_in_the_model_fails(self) -> None:
    result = self._run(model_ww(1460, "0050"), harness_ww(5, 1460) + harness_ww(13, 2750), ww=2)
    self.assertEqual(result["verdict"], "FAIL")
    self.assertTrue(any("1 write/write collisions of the model paired, 2 in the harness" in p
                        for p in result["problems"]), result["problems"])

  def test_other_instant_or_address_does_not_pair(self) -> None:
    for model in (model_ww(1470, "0050"), model_ww(1460, "0060")):
      result = self._run(model, harness_ww(5, 1460), ww=1)
      self.assertEqual(result["verdict"], "FAIL", model)

  def test_read_write_pairs_with_the_read_or_the_write_instant(self) -> None:
    # Harness read/write collisions without a model message are allowed
    # (one clock, READ_FIRST: old data, no message from the model).
    harness = harness_rw("B", 5, 1460, 1460) + harness_rw("A", 8, 585, 585)
    result = self._run(model_rw(1460, "0050"), harness, ww=0, rw=2)
    self.assertEqual(result["verdict"], "PASS", result["problems"])
    # Another cell, another port or another instant: not paired.
    for model in (model_rw(1460, "0060"), model_rw(1460, "0050", read_port="A"), model_rw(1500, "0050")):
      self.assertEqual(self._run(model, harness, ww=0, rw=2)["verdict"], "FAIL", model)

  def test_other_unisim_diagnostics_stay_fatal(self) -> None:
    overlap = ("RAMB18E1.vhd:1385:11:@1460ns:(assertion error):  Address Overlap Error on RAMB18E1 "
               f"{UNISIM_PATH} at simulation time 1460 ns.\n A write was requested to the overlapped address\n")
    result = self._run(model_ww(1460, "0050") + overlap, harness_ww(5, 1460), ww=1)
    self.assertEqual(result["verdict"], "FAIL")
    self.assertTrue(any("Address Overlap Error" in p for p in result["problems"]), result["problems"])

  def test_messages_without_address_need_the_exact_count(self) -> None:
    # 'X' in the address: no pairing by address, so the count per kind and
    # instance must equal the harness count, neither more nor less.
    harness = harness_ww(5, 1460) + harness_ww(13, 2750)
    exact = self._run(model_ww(1460, "00X0") + model_ww(2750, "00X0"), harness, ww=2)
    self.assertEqual(exact["verdict"], "PASS", exact["problems"])
    for model in (model_ww(1460, "00X0"), model_ww(1460, "00X0") * 2 + model_ww(2750, "00X0")):
      self.assertEqual(self._run(model, harness, ww=2)["verdict"], "FAIL", model)

  def test_harness_messages_must_cover_its_counters(self) -> None:
    # A harness collision without its message (no instant) cannot be paired.
    result = self._run(model_ww(1460, "0050"), harness_ww(5, 1460), ww=2)
    self.assertTrue(any("2 write/write collisions counted, 1 reported" in p for p in result["problems"]),
                    result["problems"])

  def test_rtl_simulation_keeps_every_fatal_diagnostic(self) -> None:
    result = ps.parse_result(harness_ww(5, 1460) + model_ww(1460, "0050") + harness_line(1, 0) + PASS, 0)
    self.assertEqual(result["verdict"], "FAIL")

  def test_netlist_instances_and_ultrascale_messages(self) -> None:
    netlist = ("\\inst/x/sv_mem_reg\\: unisim.vcomponents.RAMB36E2\n    generic map(\n      READ_WIDTH_A => 36,\n"
               "      WRITE_WIDTH_A => 36,\n      READ_WIDTH_B => 9,\n      WRITE_WIDTH_B => 9\n    )\n"
               "        port map (\n")
    self.assertEqual(ps.ramb_instances(netlist)["\\inst/x/sv_mem_reg\\"],
                     {"primitive": "RAMB36E2", "READ_WIDTH_A": 36, "READ_WIDTH_B": 9, "WRITE_WIDTH_A": 36,
                      "WRITE_WIDTH_B": 9})
    log = ("Error : [Unisim RAMB36E2-1]  : instance :tb:\\inst/x/sv_mem_reg\\:ramb : Memory Collision at time "
           "1460 ns.\nA simultaneous WRITE occured on port A (addr:0140 data:00000000) and port B (addr:0140 "
           "data:00).\nMemory contents at those locations have been corrupted\n")
    event = ps.unisim_collisions(log)[0]
    self.assertEqual((event["primitive"], event["t_ps"], event["kind"]), ("RAMB36E2", 1460000, "ww"))
    self.assertEqual(event["accesses"], [("WRITE", "A", 0x140), ("WRITE", "B", 0x140)])


class ReuseTest(unittest.TestCase):
  def test_netlist_reused_only_for_the_same_inputs(self) -> None:
    g = dict(ps.RAM_CONFIGS)["crw_crw.1clk.16x64"]
    with tempfile.TemporaryDirectory() as tmp:
      work = Path(tmp)
      inputs = ps.synth_inputs("xc7a35tfgg484-2", "k", g, work)
      self.assertEqual(ps.reusable(work, inputs)[1], "no netlist")
      inputs["netlist"].write_text("netlist")
      self.assertEqual(ps.reusable(work, inputs)[1], "no synth.json")
      (work / "synth.json").write_text(json.dumps({"ok": True, "provenance": inputs["provenance"]}))
      self.assertEqual(ps.reusable(work, inputs)[1], "same inputs")
      changed = {**inputs["provenance"], "sources_hash": "0" * 64}
      (work / "synth.json").write_text(json.dumps({"ok": True, "provenance": changed}))
      self.assertIsNone(ps.reusable(work, inputs)[0])
      other_part = ps.synth_inputs("xcku3p-ffva676-2-e", "k", g, work)
      (work / "synth.json").write_text(json.dumps({"ok": True, "provenance": inputs["provenance"]}))
      self.assertIsNone(ps.reusable(work, other_part)[0])


class ScriptTest(unittest.TestCase):
  def test_failed_synthesis_exits_with_1(self) -> None:
    # The script records the outcome and exits with 1 itself; Vivado 2023.1
    # also exits with 1 after a failed synth_design (PLAN 3.15).
    g = dict(ps.RAM_CONFIGS)["crw_crw.1clk.16x64"]
    script = ps.synth_inputs("xc7a35tfgg484-2", "k", g, Path(tempfile.gettempdir()))["script_text"]
    self.assertIn("if {[catch {synth_design -mode out_of_context -top lm_ps_k", script)
    self.assertIn("  exit 1\n}", script)
    # The outcome of synth_design goes to the status file of the work directory.
    self.assertRegex(script, r"set handle \[open \{[^}]*/synth_status\.jsonl\} a\]")
    self.assertIn("synth_status.jsonl", ps.KEEP_SYNTH)

  def test_vivado_log_is_not_the_console_log(self) -> None:
    # 2026-11-04: Vivado wrote its default vivado.log in the working directory,
    # the file run_logged appends the console output to; the lines interleaved
    # and "synth_design completed successfully" was lost.
    g = dict(ps.RAM_CONFIGS)["crw_crw.1clk.16x64"]
    commands = []
    with tempfile.TemporaryDirectory() as tmp, \
         mock.patch.object(ps, "heavy_tool_lock", lambda *a, **k: contextlib.nullcontext()), \
         mock.patch.object(ps, "run_logged", lambda cmd, cwd, log, **k: commands.append((cmd, log)) or 1):
      ps.synthesize(Path("C:/Xilinx/Vivado/2023.1"), "xc7a35tfgg484-2", "k", g, Path(tmp))
    cmd, console = commands[0]
    tool_log = Path(cmd[cmd.index("-log") + 1])
    self.assertEqual(tool_log.name, ps.TOOL_LOG)
    self.assertNotEqual(tool_log.name, console.name)
    self.assertEqual(console.name, "vivado.log")


class SelectionTest(unittest.TestCase):
  def test_selection_without_netlist_comparison_is_an_error(self) -> None:
    # Review of PR #4: 36x4096 on Artix-7 ran the RTL only and exited 0.
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
      code = ps.main(["--part", "xc7a35tfgg484-2", "--config", "36x4096", "--work-root", tmp])
    self.assertEqual(code, 2)


if __name__ == "__main__":
  unittest.main()
