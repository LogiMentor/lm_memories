# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the shared retry policy of heavy EDA runs."""

from __future__ import annotations

import unittest

from tools.eda_retry import MAX_RETRIES, run_with_retries, transient_reason

UNREADABLE = ('couldn\'t read file "C:/Xilinx/Vivado/2023.1/scripts/rt/data/lib_core.tcl": no such file or directory\n'
              "ERROR: [Common 17-69] Command failed: Vivado Synthesis failed\n")
RT_UNDEFINED = ('invalid command name "rt-undefined"\n'
                "ERROR: [Common 17-69] Command failed: Vivado Synthesis failed\n")
SILENT = ("Finished Technology Mapping\n"
          "16 Infos, 4 Warnings, 0 Critical Warnings and 1 Errors encountered.\n"
          "synth_design failed\n"
          "ERROR: [Common 17-69] Command failed: Vivado Synthesis failed\n")
DESIGN_ERROR = ("ERROR: [Synth 8-63] RTL assertion: \"lm_mem_ram_sdp: read latency must be 1 or 2\"\n"
                "synth_design failed\n"
                "ERROR: [Common 17-69] Command failed: Vivado Synthesis failed\n")
OK = "synth_design completed successfully\n"


class EdaRetryTest(unittest.TestCase):
  def test_each_signature_is_recognized(self) -> None:
    self.assertTrue(transient_reason(UNREADABLE).startswith("unreadable-file: couldn't read file"))
    self.assertTrue(transient_reason(RT_UNDEFINED).startswith("rt-undefined:"))
    self.assertEqual(transient_reason(SILENT), "silent-stop: synth_design failed without an error message")

  def test_design_errors_and_success_are_not_transient(self) -> None:
    self.assertIsNone(transient_reason(DESIGN_ERROR))
    self.assertIsNone(transient_reason(OK))

  def test_retry_until_clean_and_record_every_attempt(self) -> None:
    logs = iter([UNREADABLE, SILENT, OK])
    text, attempts, persisted = run_with_retries(lambda attempt: next(logs))
    self.assertEqual(text, OK)
    self.assertFalse(persisted)
    self.assertEqual(len(attempts), 2)
    self.assertTrue(attempts[0].startswith("attempt 1: unreadable-file"))
    self.assertTrue(attempts[1].startswith("attempt 2: silent-stop"))

  def test_persistent_failure_is_reported_after_the_cap(self) -> None:
    calls = []
    text, attempts, persisted = run_with_retries(lambda attempt: calls.append(attempt) or RT_UNDEFINED)
    self.assertTrue(persisted)
    self.assertEqual(calls, list(range(MAX_RETRIES + 1)))
    self.assertEqual(len(attempts), MAX_RETRIES + 1)

  def test_design_error_is_not_retried(self) -> None:
    calls = []
    text, attempts, persisted = run_with_retries(lambda attempt: calls.append(attempt) or DESIGN_ERROR)
    self.assertEqual(calls, [0])
    self.assertEqual(attempts, [])
    self.assertFalse(persisted)


if __name__ == "__main__":
  unittest.main()
