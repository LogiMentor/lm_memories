# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the verdicts of sim/vunit/mutation_check.py."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("mutation_check", ROOT_DIR / "sim" / "vunit" / "mutation_check.py")
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)

TEST = "lib.tb_lm_mem_ram.sdp.1clk.32x1024.lat1.random"
HARNESS_ERROR = "x.vhd:602:7:@15ns:(report error): lm_mem_tb_pkg: port B read at 15 ns cell 0: DUT 1 expected 0\n"
QUESTA_ERROR = "# ** Error: lm_mem_tb_pkg: port B output changed while its enable was low\n"


class ClassifyTest(unittest.TestCase):
  def test_infrastructure_failure_is_not_a_detection(self) -> None:
    # Review of PR #4: exit code 1 with "License checkout failed" was DETECTED.
    verdict, _ = mc._classify(None, "Error: License checkout failed\n", {TEST})
    self.assertEqual(verdict, "ERROR (no results)")

  def test_failure_without_a_harness_check_is_not_a_detection(self) -> None:
    results = {TEST: {"status": "fail", "output": "ghdl: cannot open file\n"}}
    self.assertEqual(mc._classify(results, "", {TEST})[0], "ERROR (failures without a harness check)")
    # Harness warnings are not errors.
    results = {TEST: {"status": "fail", "output": "(report warning): lm_mem_tb_pkg: read/write collision\n"}}
    self.assertEqual(mc._classify(results, "", {TEST})[0], "ERROR (failures without a harness check)")

  def test_missing_test_is_an_error(self) -> None:
    self.assertTrue(mc._classify({}, "", {TEST})[0].startswith("ERROR (tests missing"))

  def test_harness_error_is_a_detection(self) -> None:
    for output in (HARNESS_ERROR, QUESTA_ERROR):
      verdict, failed = mc._classify({TEST: {"status": "fail", "output": output}}, "", {TEST})
      self.assertEqual((verdict, failed), ("DETECTED", [TEST]))

  def test_all_passing_is_not_detected(self) -> None:
    self.assertEqual(mc._classify({TEST: {"status": "pass", "output": ""}}, "", {TEST})[0], "NOT DETECTED")

  def test_xunit_results_are_read(self) -> None:
    xml = ('<testsuite><testcase classname="lib.tb_lm_mem_ram.sdp.1clk.32x1024.lat1" name="random">'
           f'<system-out>{HARNESS_ERROR}</system-out><failure message="Failed"/></testcase></testsuite>')
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "r.xml"
      path.write_text(xml, encoding="utf-8")
      results = mc._test_results(path)
    self.assertEqual(results[TEST]["status"], "fail")
    self.assertIn("lm_mem_tb_pkg", results[TEST]["output"])

  def test_every_mutation_anchor_exists(self) -> None:
    for name, rel, old, _new, _tests in mc.MUTATIONS:
      self.assertIn(old, (ROOT_DIR / rel).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
  unittest.main()
