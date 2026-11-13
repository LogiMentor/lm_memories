# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the retry policy of tools/campaign/pilot.py (no tool is run)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.campaign import pilot, summarize

INSTALL = "C:/Xilinx/Vivado/2023.1"
_ENV = mock.patch.dict("os.environ", {"LM_VIVADO_DIR": INSTALL})


def setUpModule() -> None:
  _ENV.start()


def tearDownModule() -> None:
  _ENV.stop()

# The end of a run of the old code on 2026-11-04 (C:/lmeda/c, bad_r_w_infer_latency3).
TRANSIENT_LOG = f"""Starting synth_design
INFO: [Synth 8-7075] Helper process launched with PID 12144
couldn't read file "{INSTALL}/scripts/rt/fpga_tcl/rtSynthCleanup.tcl": no such file or directory
synth_design failed
ERROR: [Common 17-69] Command failed: Vivado Synthesis failed
"""
DESIGN_ERROR_LOG = TRANSIENT_LOG.replace(
  "synth_design failed", "ERROR: [Synth 8-63] RTL assertion: \"latency\"\nsynth_design failed")


def fake(*statuses: str):
  """An attempt function giving these results in turn; records the directories it was given."""
  calls: list[Path] = []

  def attempt(where: Path) -> dict:
    status = statuses[len(calls)]
    calls.append(where)
    transient = [f"unreadable-file: attempt {len(calls)}"] if status == "tool-error" else []
    return {"status": status, "problems": [] if status == "pass" else [status], "exit_code": 1,
            "tool_runs": 1, "transient": transient}
  attempt.calls = calls  # type: ignore[attr-defined]
  return attempt


class RunPilotTest(unittest.TestCase):
  def _run(self, plan) -> dict:
    self.waits: list[float] = []
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
      return pilot.run_pilot(Path(tmp), plan, delays=(30.0, 60.0), sleep=self.waits.append)

  def test_a_tool_error_runs_again_until_it_passes(self) -> None:
    attempt = fake("tool-error", "tool-error", "pass")
    report = self._run([("launcher", "j/c", lambda done: attempt)])
    case = report["cases"][0]
    self.assertTrue(report["ok"])
    self.assertEqual((case["status"], case["attempts"], case["tool_runs"]), ("pass", 3, 3))
    self.assertEqual(case["signatures"], ["unreadable-file: attempt 1", "unreadable-file: attempt 2"])
    # The waits of tools/eda_retry.py before each rerun, recorded.
    self.assertEqual(self.waits, [30.0, 60.0])
    self.assertEqual(case["waits_s"], [30.0, 60.0])
    self.assertEqual([a["waited_s"] for a in case["history"]], [0, 30.0, 60.0])
    self.assertEqual(report["retry_delays_s"], [30.0, 60.0])
    self.assertEqual([p.name for p in attempt.calls], ["attempt1", "attempt2", "attempt3"])
    self.assertEqual(attempt.calls[0].parent.name, "j_c")

  def test_a_persistent_tool_error_fails_the_pilot_after_two_reruns(self) -> None:
    stuck, after = fake("tool-error", "tool-error", "tool-error"), fake("pass")
    report = self._run([("launcher", "a", lambda done: stuck), ("old_code", "b", lambda done: after)])
    self.assertFalse(report["ok"])
    self.assertEqual([(c["status"], c["attempts"]) for c in report["cases"]], [("tool-error", 3), ("pass", 1)])
    self.assertEqual(report["launcher"]["problems"], ["a: tool-error after 3 attempts: tool-error"])
    self.assertTrue(report["old_code"]["ok"])

  def test_a_fail_is_not_run_again_and_stops_the_pilot(self) -> None:
    failing, never = fake("fail"), fake("pass")
    report = self._run([("launcher", "a", lambda done: failing), ("launcher", "b", lambda done: never),
                        ("old_code", "c", lambda done: never)])
    self.assertFalse(report["ok"])
    self.assertEqual(len(failing.calls), 1)
    self.assertEqual(never.calls, [])
    self.assertEqual(self.waits, [])
    self.assertEqual([c["status"] for c in report["cases"]], ["fail", "not-run", "not-run"])
    self.assertIn("not run: launcher a failed", report["cases"][1]["problems"])

  def test_a_broken_attempt_is_a_fail(self) -> None:
    def broken(where: Path) -> dict:
      raise RuntimeError("no summary.json")
    report = self._run([("launcher", "a", lambda done: broken)])
    self.assertEqual(report["cases"][0]["status"], "fail")
    self.assertEqual(report["cases"][0]["problems"], ["RuntimeError: no summary.json"])

  def test_a_case_whose_base_did_not_pass_is_not_run(self) -> None:
    def needs_a(done: list[dict]):
      if done[0]["status"] != "pass":
        raise pilot.PilotStop("a did not pass")
      return fake("pass")
    report = self._run([("launcher", "a", lambda done: fake("tool-error", "tool-error", "tool-error")),
                        ("generic_1000_for_1024", "b", needs_a)])
    self.assertEqual(report["cases"][1]["status"], "not-run")
    self.assertFalse(report["generic_1000_for_1024"]["ok"])

  def test_the_generic_1000_check_finds_the_launcher_run(self) -> None:
    plan = pilot.default_plan(Path("unused"))
    make = next(m for check, _, m in plan if check == "generic_1000_for_1024")
    with self.assertRaises(pilot.PilotStop):
      make([])
    done = [{"case": "pilot-artix7/r_w_32x1024", "status": "pass", "history": [{"work_dir": "w"}]}]
    self.assertTrue(callable(make(done)))
    self.assertEqual([c for _, c, _ in plan][:2], ["pilot-artix7/r_w_32x1024", "pilot-artix7/bad_ccd_resync_levels1"])


class OldCodeStatusTest(unittest.TestCase):
  def test_completed(self) -> None:
    self.assertEqual(pilot.old_code_status("synth_design completed successfully\n"), ("pass", None))

  def test_unreadable_installation_file_is_a_tool_error(self) -> None:
    status, reason = pilot.old_code_status(TRANSIENT_LOG)
    self.assertEqual(status, "tool-error")
    self.assertIn("unreadable-file: couldn't read file", reason)
    self.assertIn("rtSynthCleanup.tcl", reason)

  def test_an_error_of_the_design_is_a_fail(self) -> None:
    self.assertEqual(pilot.old_code_status(DESIGN_ERROR_LOG), ("fail", None))
    self.assertEqual(pilot.old_code_status("synth_design failed\nERROR: [Common 17-69] x\n")[0], "tool-error")
    self.assertEqual(pilot.old_code_status("ERROR: [Common 17-69] x\n"), ("fail", None))


class VendorSynthRecordTest(unittest.TestCase):
  def test_tool_runs(self) -> None:
    self.assertEqual(pilot._tool_runs({"retries": []}), 1)
    self.assertEqual(pilot._tool_runs({"retries": ["attempt 1: x", "attempt 2: x"]}), 3)
    # Persisted: the last run is recorded too.
    self.assertEqual(pilot._tool_runs({"retries": ["attempt 1: x", "attempt 2: x", "attempt 3: x"]}), 3)

  def test_signatures_are_not_repeated(self) -> None:
    last = pilot.transient_reason(TRANSIENT_LOG, [pilot.vivado_dir()])
    persisted = {"status": "tool-error",
                 "retries": ["attempt 1: rt-undefined: x; waited 30 s before attempt 2", f"attempt 2: {last}"]}
    self.assertEqual(pilot._signatures(persisted, TRANSIENT_LOG), ["rt-undefined: x", last])
    self.assertEqual(pilot._signatures({"status": "tool-error", "retries": []}, TRANSIENT_LOG), [last])
    self.assertEqual(pilot._signatures({"status": "pass", "retries": ["attempt 1: s"]}, TRANSIENT_LOG), ["s"])

  def _launcher(self, case: str, status: str, rc: int, retries: list[str]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
      work = Path(tmp) / "attempt1"

      def fake_vendor_synth(args, cwd=pilot.ROOT_DIR):
        case_dir = work / "pilot_artix7" / case
        case_dir.mkdir(parents=True)
        (case_dir / "run.json").write_text(json.dumps({"return_code": rc}))
        (case_dir / "run.log").write_text(TRANSIENT_LOG if status == "tool-error" else "")
        (work / "summary.json").write_text(json.dumps({"results": [{
          "status": status, "problems": [], "work_dir": str(case_dir), "log": str(case_dir / "run.log"),
          "retries": retries}]}))
        return 0

      with mock.patch.object(pilot, "vendor_synth", fake_vendor_synth):
        return pilot.launcher_attempt("pilot-artix7", case)(work)

  def test_launcher_exit_codes(self) -> None:
    self.assertEqual(self._launcher("r_w_32x1024", "pass", 0, [])["status"], "pass")
    self.assertEqual(self._launcher("r_w_32x1024", "pass", 1, [])["problems"], ["exit code 1, expected 0"])
    bad = self._launcher("bad_ccd_resync_levels1", "pass", 0, [])
    self.assertEqual((bad["status"], bad["problems"]), ("fail", ["exit code 0, expected non-zero"]))
    self.assertEqual(self._launcher("bad_ccd_resync_levels1", "pass", 1, ["attempt 1: s"])["tool_runs"], 2)

  def test_launcher_tool_error(self) -> None:
    retries = ["attempt 1: a", "attempt 2: b", "attempt 3: c"]
    a = self._launcher("bad_ccd_resync_levels1", "tool-error", 1, retries)
    self.assertEqual((a["status"], a["tool_runs"]), ("tool-error", 3))
    self.assertEqual(a["transient"][:3], ["a", "b", "c"])


class PilotSummaryTest(unittest.TestCase):
  def test_each_case_with_its_attempts_and_signatures(self) -> None:
    attempt = fake("tool-error", "pass")
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
      report = pilot.run_pilot(Path(tmp), [("old_code", "old-artix7/bad_r_w_infer_latency3", lambda d: attempt)],
                               delays=(30.0, 60.0), sleep=lambda s: None)
    text = "\n".join(summarize.pilot_lines(report))
    self.assertIn("| old_code | old-artix7/bad_r_w_infer_latency3 | pass | 2 | 30 | 2 | unreadable-file: attempt 1 | - |",
                  text)
    self.assertIn("at most 2 times, after a wait of 30, 60 s", text)

  def test_old_pilot_records_still_read(self) -> None:
    lines = summarize.pilot_lines({"ok": False, "launcher": {"ok": False, "problems": ["exit code 0"]}})
    self.assertEqual(lines[:2], ["- launcher: FAILED", "  - exit code 0"])



class OldCommitTest(unittest.TestCase):
  """The old_code check needs 5a5761a, which a clone of main does not have."""

  def _git(self, present_after_fetch: bool, fetch_code: int = 0):
    calls = []
    state = {"present": False}

    def run(cmd, cwd=None, **kwargs):
      calls.append(cmd)
      if cmd[:2] == ["git", "fetch"]:
        state["present"] = present_after_fetch
        return mock.Mock(returncode=fetch_code, stderr="fatal: could not read from remote repository\n")
      return mock.Mock(returncode=0 if state["present"] else 128)
    return run, calls, state

  def test_present_commit_is_not_fetched(self) -> None:
    run, calls, state = self._git(True)
    state["present"] = True
    self.assertFalse(pilot.ensure_old_commit(run=run))
    self.assertEqual([c[:2] for c in calls], [["git", "cat-file"]])

  def test_missing_commit_is_fetched_from_pull_request_4(self) -> None:
    run, calls, _ = self._git(True)
    self.assertTrue(pilot.ensure_old_commit(run=run))
    self.assertEqual(calls[1], ["git", "fetch", "origin", "refs/pull/4/head:refs/remotes/origin/pr/4"])
    self.assertEqual(calls[2][:2], ["git", "cat-file"])

  def test_failed_fetch_stops_with_the_command(self) -> None:
    run, _, _ = self._git(False, fetch_code=128)
    with self.assertRaises(pilot.OldCommitError) as raised:
      pilot.ensure_old_commit(run=run)
    message = str(raised.exception)
    self.assertIn("could not read from remote repository", message)
    self.assertIn("Run in the repository: git fetch origin refs/pull/4/head:refs/remotes/origin/pr/4", message)

  def test_failed_fetch_in_a_real_clone_without_origin(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      import subprocess
      subprocess.run(["git", "init", "-q", tmp], check=True)
      with self.assertRaisesRegex(pilot.OldCommitError, "is not in this clone and the fetch failed"):
        pilot.ensure_old_commit(Path(tmp))

  def test_pilot_stops_before_any_tool_runs(self) -> None:
    run, _, _ = self._git(False, fetch_code=1)
    with tempfile.TemporaryDirectory() as tmp, \
         mock.patch.object(pilot.subprocess, "run", side_effect=run), \
         mock.patch.object(pilot, "run_pilot", side_effect=AssertionError("a tool ran")), \
         contextlib.redirect_stderr(io.StringIO()) as err:
      self.assertEqual(pilot.main([str(Path(tmp) / "p")]), 2)
    self.assertIn("git fetch origin refs/pull/4/head", err.getvalue())


if __name__ == "__main__":
  unittest.main()
