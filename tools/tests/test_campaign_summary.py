# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for tools/campaign/summarize.py."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.campaign import rerun, summarize, toolset
from tools.tests import toolsets


class SummaryTest(unittest.TestCase):
  def _root(self, tmp: str, pilot_ok: bool) -> Path:
    root = Path(tmp) / "c"
    (root / "logs").mkdir(parents=True)
    (root / "pilot").mkdir()
    (root / "logs" / "steps.txt").write_text("t start commit abc\nt end pilot rc=%d\n" % (0 if pilot_ok else 1))
    pilot = {"ok": pilot_ok, "launcher": {"ok": pilot_ok, "problems": [] if pilot_ok else ["exit code 0"]}}
    (root / "pilot" / "pilot.json").write_text(json.dumps(pilot))
    toolsets.write(root)
    return root

  def _run(self, root: Path) -> int:
    with mock.patch.dict(os.environ, {"LM_EDA_ARCHIVE_DIR": str(root.parent / "archive")}), \
         contextlib.redirect_stdout(io.StringIO()):
      return summarize.main([str(root)])

  def test_failed_pilot_gives_a_failed_summary(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = self._root(tmp, pilot_ok=False)
      self.assertEqual(self._run(root), 1)
      text = (root / "summary.md").read_text(encoding="utf-8")
      self.assertIn("# AMD campaign: FAIL", text)
      self.assertIn("exit code 0", text)
      summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
      self.assertEqual(summary["commit"], "abc")
      self.assertTrue(summary["driver_archive"]["sha256"])

  def test_missing_step_results_fail(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = self._root(tmp, pilot_ok=True)
      self.assertEqual(self._run(root), 1)
      summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
      self.assertFalse(summary["steps"]["vivado"]["ok"])
      self.assertEqual(summary["memory_thresholds"], "not recorded")
      self.assertEqual(summary["retry_waits"], "not recorded")


def _campaign(tmp: str, vivado_statuses: list[str], ps_tool_error: bool = False,
              rerun_items: list[dict] | None = None, rc: dict | None = None) -> Path:
  """A campaign root where every step passed, except what the arguments say."""
  root = Path(tmp) / "c"
  for sub in ("logs", "pilot", "vs", "fc", "qc", "ps", "vu/ghdl_6.0.0", "vu/questa_2021.1", "mut/ghdl_6.0.0"):
    (root / sub).mkdir(parents=True)
  codes = {"pilot": 0, "vivado": 0, "checks_vivado": 0, "checks_quartus": 0, "postsynth": 0, "vunit_ghdl": 0,
           "vunit_questa": 0, "mutation": 0, "rerun_tool_errors": 0, **(rc or {})}
  (root / "logs" / "steps.txt").write_text(
    "t start commit abc\nmemory thresholds: synthesis 4096 MB (LM_EDA_MIN_FREE_MB_SYNTH); simulation 2048 MB "
    "(LM_EDA_MIN_FREE_MB_SIM)\nretry waits: 30, 60 s (LM_EDA_RETRY_DELAYS)\n" + "".join(f"t end {k} rc={v}\n" for k, v in codes.items()))
  (root / "pilot" / "pilot.json").write_text(json.dumps({"ok": True, "launcher": {"ok": True, "problems": []}}))
  results = [{"job": "artix7", "part": "xc7a35tfgg484-2", "case": f"case{i}", "status": s, "tool_version": "2023.1",
              "problems": [] if s == "pass" else [f"{s} of case{i}"]} for i, s in enumerate(vivado_statuses)]
  (root / "vs" / "summary.json").write_text(json.dumps({"results": results}))
  for sub, version in (("fc", "2023.1"), ("qc", "23.1std.0 Build 991")):
    (root / sub / "summary.json").write_text(json.dumps({"results": [
      {"job": "j", "part": "p", "case": "x", "status": "pass", "tool_version": version, "problems": []}]}))
  synth = {"ok": not ps_tool_error, "tool_error": ps_tool_error, "problems": ["transient"] if ps_tool_error else []}
  sims = {} if ps_tool_error else {"ghdl": {"results": {"crw_crw.1clk.16x64": {"verdict": "PASS"}}}}
  (root / "ps" / "results.json").write_text(json.dumps({"configs": ["crw_crw.1clk.16x64"], "rtl": {}, "parts": {
    "xc7a35tfgg484-2": {"synth": {"crw_crw_16x64_16_l11": synth}, "sims": sims}}}))
  for name in ("ghdl_6.0.0", "questa_2021.1"):
    (root / "vu" / name / "results.json").write_text(json.dumps({"problems": [], "total": 1, "passed": 1,
                                                                 "simulator": name}))
  (root / "mut" / "ghdl_6.0.0" / "results.json").write_text(json.dumps({
    "baseline": {"verdict": "PASS"}, "mutations": [{"name": "m", "verdict": "DETECTED"}]}))
  if rerun_items is not None:
    (root / "rerun").mkdir()
    (root / "rerun" / "rerun.json").write_text(json.dumps({"ok": True, "items": rerun_items}))
  toolsets.write(root)
  return root


class RerunTest(unittest.TestCase):
  def _summary(self, root: Path) -> tuple[int, dict, str]:
    code = SummaryTest._run(self, root)  # type: ignore[arg-type]
    return (code, json.loads((root / "summary.json").read_text(encoding="utf-8")),
            (root / "summary.md").read_text(encoding="utf-8"))

  def test_memory_thresholds_are_in_the_summary(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      code, summary, text = self._summary(_campaign(tmp, ["pass"], rerun_items=[]))
      self.assertEqual(code, 0, text)
      self.assertIn("synthesis 4096 MB (LM_EDA_MIN_FREE_MB_SYNTH)", summary["memory_thresholds"])
      self.assertIn("Memory thresholds of the EDA lock: synthesis 4096 MB", text)
      self.assertEqual(summary["retry_waits"], "30, 60 s (LM_EDA_RETRY_DELAYS)")
      self.assertIn("Waits before a rerun of a transient tool failure: 30, 60 s", text)
      self.assertIn("nothing was run again", text)

  def test_only_tool_errors_run_again(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["pass", "tool-error", "fail"], ps_tool_error=True)
      todo = rerun.tool_errors(root)
      self.assertEqual([(i["step"], i["case"]) for i in todo],
                       [("vivado", "case1"), ("postsynth", "crw_crw.1clk.16x64")])
      cmd = todo[0]["command"]
      self.assertEqual(cmd[cmd.index("--config") + 1], "tools/vendor_synth/vivado_check.toml")
      self.assertEqual((cmd[cmd.index("--job") + 1], cmd[cmd.index("--case") + 1]), ("artix7", "case1"))
      self.assertEqual(cmd[cmd.index("--campaign") + 1], "vivado_2023.1_amd_rerun")
      ps_cmd = todo[1]["command"]
      self.assertEqual(ps_cmd[ps_cmd.index("--config") + 1], r"^(?:crw_crw\.1clk\.16x64)$")
      self.assertEqual(ps_cmd[ps_cmd.index("--part") + 1], "xc7a35tfgg484-2")

  def test_rerun_writes_its_record_and_runs_each_case_once(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["tool-error"])
      calls = []

      def fake_run(cmd, cwd):  # the rerun passes
        work = Path(cmd[cmd.index("--work-root") + 1])
        work.mkdir(parents=True)
        (work / "summary.json").write_text(json.dumps({"results": [{"status": "pass", "problems": []}]}))
        calls.append(cmd)
        return mock.Mock(returncode=0)

      with mock.patch.object(rerun.subprocess, "run", fake_run), contextlib.redirect_stdout(io.StringIO()):
        self.assertEqual(rerun.main([str(root)]), 0)
      self.assertEqual(len(calls), 1)
      record = json.loads((root / "rerun" / "rerun.json").read_text(encoding="utf-8"))
      self.assertEqual([(i["case"], i["first_status"], i["rerun_status"]) for i in record["items"]],
                       [("case0", "tool-error", "pass")])

  def test_a_passed_rerun_passes_the_step_and_is_listed(self) -> None:
    item = {"step": "vivado", "job": "artix7", "part": "xc7a35tfgg484-2", "case": "case1",
            "first_status": "tool-error", "first_problems": ["couldn't read file"], "rerun_status": "pass",
            "rerun_problems": []}
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["pass", "tool-error"], rerun_items=[item], rc={"vivado": 1})
      code, summary, text = self._summary(root)
      self.assertEqual(code, 0, text)
      self.assertEqual(summary["steps"]["vivado"]["counts"], {"pass": 2})
      self.assertIn("| vivado | xc7a35tfgg484-2 | artix7 | case1 | tool-error: couldn't read file | pass |", text)
    with tempfile.TemporaryDirectory() as tmp:
      failed = {**item, "rerun_status": "tool-error", "rerun_problems": ["couldn't read file again"]}
      root = _campaign(tmp, ["pass", "tool-error"], rerun_items=[failed], rc={"vivado": 1, "rerun_tool_errors": 1})
      code, summary, text = self._summary(root)
      self.assertEqual(code, 1)
      self.assertIn("case1: tool-error (rerun of a tool-error) couldn't read file again", text)

  def test_a_fail_stays_a_fail(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      code, summary, _ = self._summary(_campaign(tmp, ["pass", "fail"], rerun_items=[], rc={"vivado": 1}))
      self.assertEqual(code, 1)
      self.assertEqual(summary["steps"]["vivado"]["counts"], {"pass": 1, "fail": 1})

  def test_resumed_steps_rerun_only_what_is_new(self) -> None:
    # Resume with checks_vivado and postsynth: the rerun of the vivado step is
    # kept as it is; a checks_vivado rerun that passed is kept and not run
    # again; postsynth ran from scratch, so its tool-errors all run again.
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["tool-error"], ps_tool_error=True)
      (root / "fc" / "summary.json").write_text(json.dumps({"results": [
        {"job": "j", "part": "p", "case": "old", "status": "tool-error", "problems": []},
        {"job": "j", "part": "p", "case": "died", "status": "tool-error", "problems": []}]}))
      earlier = [
        {"step": "vivado", "job": "artix7", "part": "xc7a35tfgg484-2", "case": "case0", "rerun_status": "pass"},
        {"step": "checks_vivado", "job": "j", "part": "p", "case": "old", "rerun_status": "pass"},
        {"step": "postsynth", "job": "postsynth", "part": "xc7a35tfgg484-2", "case": "crw_crw.1clk.16x64",
         "rerun_status": "pass"}]
      (root / "rerun").mkdir()
      (root / "rerun" / "rerun.json").write_text(json.dumps({"ok": True, "items": earlier}))
      stale = root / "rerun" / "fc" / "j" / "died"
      stale.mkdir(parents=True)
      (stale / "summary.json").write_text("{}")
      calls = []

      def fake_run(cmd, cwd):
        work = Path(cmd[cmd.index("--work-root") + 1])
        work.mkdir(parents=True)
        if "run_postsynth.py" in cmd[1]:
          (work / "results.json").write_text(json.dumps({"parts": {"xc7a35tfgg484-2": {"synth": {}, "sims": {
            "ghdl": {"results": {"crw_crw.1clk.16x64": {"verdict": "PASS"}}}}}}}))
        else:
          (work / "summary.json").write_text(json.dumps({"results": [{"status": "pass", "problems": []}]}))
        calls.append(cmd)
        return mock.Mock(returncode=0)

      with mock.patch.object(rerun.subprocess, "run", fake_run), contextlib.redirect_stdout(io.StringIO()):
        code = rerun.main([str(root), "--steps", "checks_vivado", "postsynth", "--fresh", "postsynth"])
      self.assertEqual(code, 0)
      ran = sorted((c[c.index("--case") + 1] if "--case" in c else "ps") for c in calls)
      self.assertEqual(ran, ["died", "ps"])
      record = json.loads((root / "rerun" / "rerun.json").read_text(encoding="utf-8"))
      self.assertEqual(sorted((i["step"], i["case"]) for i in record["items"]),
                       [("checks_vivado", "died"), ("checks_vivado", "old"), ("postsynth", "crw_crw.1clk.16x64"),
                        ("vivado", "case0")])
      # The earlier rerun of the same case is kept beside, not deleted.
      self.assertEqual(len(list(stale.parent.glob("died.before_*"))), 1)

  def test_resume_is_in_the_summary(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["pass"], rerun_items=[])
      with (root / "logs" / "steps.txt").open("a", encoding="utf-8") as handle:
        handle.write("t resume commit def steps: checks_vivado postsynth\n"
                     "memory thresholds: synthesis 6000 MB (LM_EDA_MIN_FREE_MB_SYNTH)\n")
      code, summary, text = self._summary(root)
      self.assertEqual(code, 0, text)
      self.assertEqual(summary["resumes"], [{"commit": "def", "steps": ["checks_vivado", "postsynth"]}])
      self.assertIn("Resumed on commit `def`: checks_vivado, postsynth", text)
      self.assertIn("synthesis 6000 MB", summary["memory_thresholds"])

  def test_reevaluation_keeps_the_archive_and_lists_the_changes(self) -> None:
    from tools.campaign import resume
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      (root / "fc").mkdir()
      before = {"results": [{"job": "j", "case": "died", "status": "fail"}, {"job": "j", "case": "ok", "status": "pass"}]}
      (root / "fc" / "summary.json").write_text(json.dumps(before))
      (root / "fc" / "summary_public.json").write_text(json.dumps({"archive": {"archive": "a.tar.xz"}, "results": []}))
      seen = []

      def fake_run(cmd, cwd):  # vendor_synth --evaluate-only rewrites both summaries
        seen.append(cmd)
        after = {"results": [{"job": "j", "case": "died", "status": "tool-error"}, {"job": "j", "case": "ok",
                                                                                   "status": "pass"}]}
        (root / "fc" / "summary.json").write_text(json.dumps(after))
        (root / "fc" / "summary_public.json").write_text(json.dumps({"results": []}))
        return mock.Mock(returncode=1)

      code, changed = resume.reevaluate(root, "checks_vivado", run=fake_run)
      self.assertEqual(code, 1)
      self.assertIn("--evaluate-only", seen[0])
      self.assertEqual(changed, [{"job": "j", "case": "died", "before": "fail", "after": "tool-error"}])
      self.assertEqual(json.loads((root / "fc" / "summary_public.json").read_text())["archive"],
                       {"archive": "a.tar.xz"})
      self.assertEqual(len(list((root / "fc").glob("summary.before_*.json"))), 1)

  def test_steps_match_the_campaign_script(self) -> None:
    script = (Path(rerun.ROOT_DIR) / "tools" / "campaign" / "run_amd.sh").read_text(encoding="utf-8")
    variables = {"vivado": "N_VIVADO", "checks_vivado": "N_CHECKS_VIVADO", "checks_quartus": "N_CHECKS_QUARTUS"}
    for step, (sub, config) in rerun.SYNTH_STEPS.items():
      line = next(l for l in script.splitlines() if l.startswith(f"step {step} "))
      following = script[script.index(line):].split("\n", 2)
      text = following[0] + following[1]
      self.assertIn(config, text, step)
      self.assertIn(f'--campaign "${variables[step]}"', text, step)
      self.assertIn(f"{variables[step]}=$(name {step})", script, step)
      self.assertIn(f'"$ROOT/{sub}"', text, step)
    self.assertIn('--campaign "$N_POSTSYNTH"', script)
    self.assertIn("step rerun_tool_errors", script)
    # No campaign name, and so no tool version, is written in the script.
    code = "\n".join(l for l in script.splitlines() if not l.lstrip().startswith("#"))
    self.assertNotRegex(code, r"--campaign [a-z]|run_vunit_campaign\.py [a-z]")
    self.assertIn("tools.campaign.toolset detect", script)
    self.assertIn('tools.campaign.toolset check "$TOOLSET"', script)


if __name__ == "__main__":
  unittest.main()
