# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""tools/campaign/deps.py: a resume keeps a step only if its dependencies are unchanged."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from tools.campaign import deps

TREE = {
  "src/core/lm_mem_ram_sdp.vhd": b"architecture a of sdp\r\n",
  "manifests/lm_memories.toml": b"[flows]\n",
  "sim/vunit/lm_mem_tb_harness.vhd": b"harness\n",
  "sim/vunit/configs.py": b"RAM_CONFIGS = []\n",
  "sim/vunit/run.py": b"run\n",
  "sim/vunit/mutation_check.py": b"mutations\n",
  "sim/ref/lm_mem_ref_ram_crw_crw.vhd": b"ref\n",
  "sim/scripts/run_postsynth.py": b"postsynth\n",
  "tools/vendor_synth/cases.toml": b"cases\n",
  "tools/vendor_synth/vivado_check.toml": b"vivado\n",
  "tools/vendor_synth/fallback_check.toml": b"fallback\n",
  "tools/vendor_synth/quartus_check.toml": b"quartus\n",
  "tools/vendor_synth/expected/amd.toml": b"amd\n",
  "tools/vendor_synth/expected/intel.toml": b"intel\n",
  "tools/campaign/pilot.toml": b"pilot\n",
}


def reader(tree: dict[str, bytes]) -> deps.Reader:
  return lambda patterns: {p: c for p, c in tree.items() if deps._matches(p, patterns)}


class DepsTest(unittest.TestCase):
  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.root = Path(self.tmp.name)

  def tearDown(self) -> None:
    self.tmp.cleanup()

  def test_unchanged_dependencies_allow_the_resume(self) -> None:
    deps.record(self.root, read=reader(TREE), commit="c1")
    # Line endings of a checkout do not count.
    crlf = {**TREE, "manifests/lm_memories.toml": b"[flows]\r\n"}
    self.assertEqual(deps.check(self.root, ["postsynth"], read=reader(crlf)), [])

  def test_changed_dependency_of_a_kept_step_refuses_the_resume(self) -> None:
    deps.record(self.root, read=reader(TREE), commit="c1")
    for path, kept in (("sim/vunit/lm_mem_tb_harness.vhd", ["vunit_ghdl", "vunit_questa", "mutation"]),
                       ("tools/vendor_synth/expected/amd.toml", ["vivado"]),
                       ("src/core/lm_mem_ram_sdp.vhd", ["pilot", "vivado", "checks_quartus", "vunit_ghdl"])):
      problems = deps.check(self.root, ["checks_vivado", "postsynth"], read=reader({**TREE, path: b"changed\n"}))
      steps = {p.split(":")[0] for p in problems}
      self.assertTrue(set(kept) <= steps, (path, problems))
      self.assertNotIn("checks_vivado", steps)  # resumed, not kept
    # The harness does not count for a synthesis step.
    problems = deps.check(self.root, [], read=reader({**TREE, "sim/vunit/lm_mem_tb_harness.vhd": b"x\n"}))
    self.assertEqual({p.split(":")[0] for p in problems}, {"postsynth", "vunit_ghdl", "vunit_questa", "mutation"})
    self.assertIn("harness changed since commit c1", problems[0])

  def test_main_names_the_steps_to_run_again(self) -> None:
    deps.record(self.root, read=reader(TREE), commit="c1")
    changed = reader({**TREE, "tools/vendor_synth/vivado_check.toml": b"other\n"})
    err = io.StringIO()
    with mock.patch.object(deps, "tree_reader", lambda: changed), redirect_stderr(err):
      code = deps.main(["check", str(self.root), "postsynth"])
    self.assertEqual(code, 1)
    self.assertIn("vivado: configuration changed since commit c1", err.getvalue())
    self.assertIn('LM_CAMPAIGN_RESUME="postsynth vivado"', err.getvalue())

  def test_root_without_record_is_derived_from_the_commits_of_its_steps(self) -> None:
    (self.root / "logs").mkdir()
    (self.root / "logs" / "steps.txt").write_text(
      "2026-11-04T13:20:53Z start commit c1\n2026-11-05T07:23:14Z resume commit c2 steps: checks_vivado postsynth\n",
      encoding="utf-8")
    trees = {"c1": TREE, "c2": {**TREE, "sim/scripts/run_postsynth.py": b"postsynth 2\n"}}
    data = deps.recorded(self.root, reader_at=lambda c: reader(trees[c]))
    self.assertEqual(data["steps"]["vivado"]["commit"], "c1")
    self.assertEqual(data["steps"]["postsynth"]["commit"], "c2")
    self.assertTrue((self.root / "logs" / "deps.json").is_file())
    # A resume on c2 of checks_vivado only keeps postsynth, which ran on c2: allowed.
    self.assertEqual(deps.check(self.root, ["checks_vivado"], read=reader(trees["c2"])), [])

  def test_campaign_script_checks_before_and_records_after(self) -> None:
    script = (deps.ROOT_DIR / "tools" / "campaign" / "run_amd.sh").read_text(encoding="utf-8")
    check = script.index("tools.campaign.deps check")
    self.assertLess(check, script.index('echo "$(stamp) resume commit'))  # before steps.txt names the resume
    self.assertIn('tools.campaign.deps record "$ROOT" > /dev/null', script)
    self.assertLess(script.index("tools.campaign.rerun \"$ROOT\" --steps"),
                    script.index('tools.campaign.deps record "$ROOT" $RESUME'))

  def test_real_history_of_the_campaign_of_4d1380c(self) -> None:
    # From 4d1380c to f971122 only checks, tools and run_postsynth.py changed:
    # a resume of checks_vivado and postsynth keeps every other step.
    have = subprocess.run(["git", "cat-file", "-e", "4d1380c^{commit}"], cwd=deps.ROOT_DIR,
                          capture_output=True).returncode == 0
    if not have:
      self.skipTest("shallow clone without the campaign commits")
    (self.root / "logs").mkdir()
    (self.root / "logs" / "steps.txt").write_text("x start commit 4d1380c\n", encoding="utf-8")
    at_f971122 = deps.commit_reader("f971122")
    self.assertEqual(deps.check(self.root, ["checks_vivado", "postsynth"], read=at_f971122), [])
    problems = deps.check(self.root, ["checks_vivado"], read=at_f971122)
    self.assertEqual(problems, ["postsynth: configuration changed since commit 4d1380c"])


if __name__ == "__main__":
  unittest.main()
