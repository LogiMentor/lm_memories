# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for tools/eda_archive.py and tools/eda_provenance.py."""

from __future__ import annotations

import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.eda_archive import ARCHIVE_ENV, archive_campaign, archive_dir, sha256_file
from tools.eda_provenance import ROOT_DIR, provenance, same_inputs, sources_hash


class ArchiveTest(unittest.TestCase):
  def test_archive_keeps_logs_and_skips_libraries(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      work = Path(tmp) / "work"
      (work / "case" / ".Xil").mkdir(parents=True)
      (work / "case" / "run.log").write_text("log")
      (work / "case" / "run.attempt1.log").write_text("first attempt")
      (work / "case" / ".Xil" / "junk.tcl").write_text("x")
      (work / "case" / "lib.o").write_text("x")
      with mock.patch.dict(os.environ, {ARCHIVE_ENV: str(Path(tmp) / "archive")}):
        record = archive_campaign("test", work)
      target = Path(tmp) / "archive" / record["archive"]
      self.assertEqual(record["sha256"], sha256_file(target))
      self.assertEqual(record["files"], 2)
      with tarfile.open(target) as tar:
        self.assertEqual(sorted(tar.getnames()), ["work/case/run.attempt1.log", "work/case/run.log"])

  def test_archive_outside_the_repository_only(self) -> None:
    with mock.patch.dict(os.environ, {ARCHIVE_ENV: str(ROOT_DIR / "archive")}):
      with self.assertRaises(SystemExit):
        archive_dir()


class ProvenanceTest(unittest.TestCase):
  def test_hash_follows_contents_not_line_endings(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      a = Path(tmp) / "a.vhd"
      a.write_bytes(b"entity e is\nend;\n")
      first = sources_hash([a], ["case"])
      a.write_bytes(b"entity e is\r\nend;\r\n")
      self.assertEqual(sources_hash([a], ["case"]), first)
      a.write_bytes(b"entity f is\nend;\n")
      self.assertNotEqual(sources_hash([a], ["case"]), first)
      self.assertNotEqual(sources_hash([a], ["other case"]), sources_hash([a], ["case"]))

  def test_same_inputs_ignores_the_commit(self) -> None:
    current = provenance([ROOT_DIR / "manifests" / "lm_memories.toml"])
    self.assertTrue(same_inputs({**current, "commit": "other"}, current))
    self.assertFalse(same_inputs({**current, "sources_hash": "0"}, current))
    self.assertFalse(same_inputs(None, current))


if __name__ == "__main__":
  unittest.main()
