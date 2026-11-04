# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the shared retry policy of heavy EDA runs."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.eda_retry import (MAX_RETRIES, RETRY_DELAYS, RETRY_DELAYS_ENV, attempt_log, project_error, record_reason,
                             retry_delay, retry_delays, retry_reason, run_with_retries, transient_reason)

ROOTS = ["C:/Xilinx/Vivado/2023.1"]
UNREADABLE = ('couldn\'t read file "C:/Xilinx/Vivado/2023.1/scripts/rt/data/lib_core.tcl": no such file or directory\n'
              "ERROR: [Common 17-69] Command failed: Vivado Synthesis failed\n")
GENERATED = ('couldn\'t read file "C:/Users/x/AppData/Local/Temp/lmeda/vs/a/b/.Xil/Vivado-1-x/rt.tcl": '
             "no such file or directory\n"
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
# A project file that cannot be read, together with a design error.
PROJECT_FILE = ('couldn\'t read file "C:/work/project/user_constraints.xdc": no such file or directory\n'
                "ERROR: [Synth 8-439] module 'top' not found\n"
                "synth_design failed\n")
OK = "synth_design completed successfully\n"
# A completed run whose log also has a harmless "couldn't read file" line.
OK_WITH_NOISE = ('couldn\'t read file "C:/Xilinx/Vivado/2023.1/scripts/init.tcl": no such file or directory\n' + OK)


def done(text: str) -> bool:
  return "synth_design completed successfully" in text


class EdaRetryTest(unittest.TestCase):
  def test_each_signature_is_recognized(self) -> None:
    self.assertTrue(transient_reason(UNREADABLE, ROOTS).startswith("unreadable-file: couldn't read file"))
    self.assertTrue(transient_reason(GENERATED, ROOTS).startswith("unreadable-file:"))
    self.assertTrue(transient_reason(RT_UNDEFINED, ROOTS).startswith("rt-undefined:"))
    self.assertEqual(transient_reason(SILENT, ROOTS), "silent-stop: synth_design failed without an error message")

  def test_unreadable_file_counts_only_for_tool_files(self) -> None:
    self.assertIsNone(transient_reason(PROJECT_FILE, ROOTS))
    # Without the installation root, an installation file is not recognized.
    self.assertIsNone(transient_reason(UNREADABLE, []))

  def test_project_error_takes_precedence(self) -> None:
    self.assertEqual(project_error(PROJECT_FILE), "ERROR: [Synth 8-439] module 'top' not found")
    self.assertIsNone(project_error(UNREADABLE))
    self.assertIsNone(retry_reason(PROJECT_FILE + UNREADABLE, completed=False, roots=ROOTS))
    self.assertIsNone(retry_reason(DESIGN_ERROR, completed=False, roots=ROOTS))

  def test_completed_run_is_never_retried(self) -> None:
    calls = []
    waits = []
    text, attempts, persisted = run_with_retries(lambda attempt: calls.append(attempt) or OK_WITH_NOISE,
                                                 completed=done, roots=ROOTS, sleep=waits.append)
    self.assertEqual(calls, [0])
    self.assertEqual((attempts, persisted, waits), ([], False, []))

  def test_retry_until_clean_and_keep_every_log(self) -> None:
    logs = iter([UNREADABLE, SILENT, OK])
    with tempfile.TemporaryDirectory() as tmp:
      log = Path(tmp) / "run.log"

      def run_once(attempt: int) -> str:
        text = next(logs)
        log.write_text(text)
        return text

      waits = []
      text, attempts, persisted = run_with_retries(run_once, completed=done, roots=ROOTS, log_path=log,
                                                   delays=(30.0, 60.0), sleep=waits.append)
      self.assertEqual(text, OK)
      self.assertEqual(waits, [30.0, 60.0])
      self.assertTrue(attempts[0].endswith("; waited 30 s before attempt 2"), attempts[0])
      self.assertTrue(attempts[1].endswith("; waited 60 s before attempt 3"), attempts[1])
      self.assertFalse(persisted)
      self.assertEqual(len(attempts), 2)
      self.assertTrue(attempts[0].startswith("attempt 1: unreadable-file"))
      self.assertTrue(attempts[1].startswith("attempt 2: silent-stop"))
      self.assertEqual(attempt_log(log, 1).read_text(), UNREADABLE)
      self.assertEqual(attempt_log(log, 2).read_text(), SILENT)
      self.assertEqual(log.read_text(), OK)

  def test_persistent_failure_is_reported_after_the_cap(self) -> None:
    calls = []
    waits = []
    text, attempts, persisted = run_with_retries(lambda attempt: calls.append(attempt) or RT_UNDEFINED,
                                                 completed=done, roots=ROOTS, delays=(30.0, 60.0),
                                                 sleep=waits.append)
    self.assertTrue(persisted)
    self.assertEqual(calls, list(range(MAX_RETRIES + 1)))
    self.assertEqual(len(attempts), MAX_RETRIES + 1)
    # No wait after the last attempt: nothing runs after it.
    self.assertEqual(waits, [30.0, 60.0])
    self.assertNotIn("waited", attempts[-1])
    self.assertEqual(record_reason(attempts[0]), record_reason(attempts[-1]))

  def test_design_error_is_not_retried(self) -> None:
    calls = []
    waits = []
    text, attempts, persisted = run_with_retries(lambda attempt: calls.append(attempt) or DESIGN_ERROR,
                                                 completed=done, roots=ROOTS, sleep=waits.append)
    self.assertEqual(calls, [0])
    self.assertEqual((attempts, waits), ([], []))
    self.assertFalse(persisted)


class ToolDiedTest(unittest.TestCase):
  # The stop of a static check printed, then the tool died (2026-11-04).
  DIED = ('ERROR: [Synth 8-63] RTL assertion: "lm_mem_fifo: g_almost_full_limit must be in 1 to g_wr_depth"\n'
          "synth_design failed\ncalled Tcl_Close on channel with refCount > 0\n")

  def test_a_died_tool_is_retried_whatever_its_log(self) -> None:
    self.assertIsNotNone(project_error(self.DIED))
    self.assertIsNone(retry_reason(self.DIED, completed=False, roots=ROOTS))
    self.assertEqual(retry_reason(self.DIED, completed=False, roots=ROOTS, died="started, never returned"),
                     "tool-died: started, never returned")
    # A completed run is a result, even if the caller thinks the tool died.
    self.assertIsNone(retry_reason(OK, completed=True, roots=ROOTS, died="x"))

  def test_run_with_retries_asks_the_caller(self) -> None:
    deaths = iter(["started, never returned", None])
    logs = iter([self.DIED, DESIGN_ERROR])
    text, attempts, persisted = run_with_retries(lambda attempt: next(logs), completed=done, roots=ROOTS,
                                                 died=lambda text: next(deaths), delays=(0.0,), sleep=lambda s: None)
    self.assertEqual(text, DESIGN_ERROR)
    self.assertEqual(attempts, ["attempt 1: tool-died: started, never returned; waited 0 s before attempt 2"])
    self.assertFalse(persisted)


class RetryDelayTest(unittest.TestCase):
  def test_default_waits_are_30_then_60_seconds(self) -> None:
    with mock.patch.dict(os.environ, {RETRY_DELAYS_ENV: ""}):
      self.assertEqual(retry_delays(), RETRY_DELAYS)
      self.assertEqual((retry_delay(1), retry_delay(2)), (30.0, 60.0))
    # The last value also serves later reruns.
    self.assertEqual(retry_delay(5, (30.0, 60.0)), 60.0)

  def test_waits_from_the_environment(self) -> None:
    with mock.patch.dict(os.environ, {RETRY_DELAYS_ENV: "5, 7.5"}):
      self.assertEqual(retry_delays(), (5.0, 7.5))
      waits = []
      run_with_retries(lambda attempt: RT_UNDEFINED, completed=done, roots=ROOTS, sleep=waits.append)
      self.assertEqual(waits, [5.0, 7.5])
    with mock.patch.dict(os.environ, {RETRY_DELAYS_ENV: "0"}):
      waits = []
      _, attempts, _ = run_with_retries(lambda attempt: RT_UNDEFINED, completed=done, roots=ROOTS,
                                        sleep=waits.append)
      self.assertEqual(waits, [0.0, 0.0])
      self.assertTrue(attempts[0].endswith("; waited 0 s before attempt 2"))

  def test_a_wrong_value_stops_with_its_reason(self) -> None:
    for value in ("30;60", "-1", "x", ","):
      with mock.patch.dict(os.environ, {RETRY_DELAYS_ENV: value}), \
           self.assertRaisesRegex(ValueError, RETRY_DELAYS_ENV):
        retry_delays()

  def test_record_reason(self) -> None:
    self.assertEqual(record_reason("attempt 1: rt-undefined: x; waited 30 s before attempt 2"), "rt-undefined: x")
    self.assertEqual(record_reason("attempt 3: rt-undefined: x"), "rt-undefined: x")


if __name__ == "__main__":
  unittest.main()
