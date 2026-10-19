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

from tools.campaign import summarize


class SummaryTest(unittest.TestCase):
  def _root(self, tmp: str, pilot_ok: bool) -> Path:
    root = Path(tmp) / "c"
    (root / "logs").mkdir(parents=True)
    (root / "pilot").mkdir()
    (root / "logs" / "steps.txt").write_text("t start commit abc\nt end pilot rc=%d\n" % (0 if pilot_ok else 1))
    pilot = {"ok": pilot_ok, "launcher": {"ok": pilot_ok, "problems": [] if pilot_ok else ["exit code 0"]}}
    (root / "pilot" / "pilot.json").write_text(json.dumps(pilot))
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


if __name__ == "__main__":
  unittest.main()
