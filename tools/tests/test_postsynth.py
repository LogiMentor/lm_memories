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


class SelectionTest(unittest.TestCase):
  def test_selection_without_netlist_comparison_is_an_error(self) -> None:
    # Review of PR #4: 36x4096 on Artix-7 ran the RTL only and exited 0.
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
      code = ps.main(["--part", "xc7a35tfgg484-2", "--config", "36x4096", "--work-root", tmp])
    self.assertEqual(code, 2)


if __name__ == "__main__":
  unittest.main()
