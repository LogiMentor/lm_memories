# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the work directories of heavy EDA runs."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.eda_workdir import WORKDIR_ENV, prune, work_root


class EdaWorkdirTest(unittest.TestCase):
  def test_root_from_environment_or_short_default(self) -> None:
    with mock.patch.dict(os.environ, {WORKDIR_ENV: "C:/eda"}):
      self.assertEqual(work_root("vs"), Path("C:/eda") / "vs")
    env = {k: v for k, v in os.environ.items() if k != WORKDIR_ENV}
    with mock.patch.dict(os.environ, env, clear=True):
      self.assertEqual(work_root("ps"), Path(tempfile.gettempdir()) / "lmeda" / "ps")

  def test_prune_keeps_only_matching_entries(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      d = Path(tmp)
      for name in ("run.log", "run_vivado.tcl", "a.rpt"):
        (d / name).write_text("x")
      for name in ("reports", ".Xil", "project"):
        (d / name).mkdir()
        (d / name / "f").write_text("x")
      removed = prune(d, ("run.log", "reports", "*.tcl", "*.rpt"))
      self.assertEqual(sorted(removed), [".Xil", "project"])
      self.assertEqual(sorted(p.name for p in d.iterdir()), ["a.rpt", "reports", "run.log", "run_vivado.tcl"])
      self.assertTrue((d / "reports" / "f").is_file())

  def test_prune_of_a_missing_directory_does_nothing(self) -> None:
    self.assertEqual(prune(Path(tempfile.gettempdir()) / "lm_no_such_dir_x1", ("*",)), [])


if __name__ == "__main__":
  unittest.main()
