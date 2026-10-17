# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the machine-wide EDA lock. They use a private lock path."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.eda_lock import TOKEN_ENV, LockTimeout, _pid_alive, heavy_tool_lock


class EdaLockTest(unittest.TestCase):
  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.path = Path(self.tmp.name) / "test.lock"
    self.messages: list[str] = []

  def tearDown(self) -> None:
    self.tmp.cleanup()

  def _lock(self, **kwargs):
    return heavy_tool_lock("unit test", lock_path=self.path, log=self.messages.append, **kwargs)

  def _fake_holder(self, pid: int) -> None:
    self.path.write_text(json.dumps({
      "token": "other", "pid": pid, "host": socket.gethostname(), "user": "someone",
      "purpose": "other run", "command": "other command", "started": "earlier",
    }), encoding="utf-8")

  def test_acquire_and_release(self) -> None:
    with self._lock(timeout_s=1):
      holder = json.loads(self.path.read_text(encoding="utf-8"))
      self.assertEqual(holder["pid"], os.getpid())
      self.assertEqual(os.environ[TOKEN_ENV], holder["token"])
    self.assertFalse(self.path.exists())
    self.assertNotIn(TOKEN_ENV, os.environ)

  def test_live_holder_times_out_with_its_description(self) -> None:
    self._fake_holder(os.getpid())  # this process is certainly alive
    with self.assertRaises(LockTimeout) as ctx:
      with self._lock(timeout_s=0.3, poll_s=0.05):
        pass
    self.assertIn("other run", str(ctx.exception))
    self.assertTrue(any("waiting for" in m for m in self.messages))
    self.assertTrue(self.path.exists(), "a live holder's lock must not be removed")

  def test_stale_lock_is_removed(self) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    self.assertFalse(_pid_alive(proc.pid))
    self._fake_holder(proc.pid)
    with self._lock(timeout_s=1):
      self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["pid"], os.getpid())
    self.assertTrue(any("stale" in m for m in self.messages))

  def test_child_with_token_does_not_wait(self) -> None:
    with self._lock(timeout_s=1):
      with self._lock(timeout_s=0.2, poll_s=0.05):
        self.assertTrue(self.path.exists())
      self.assertTrue(self.path.exists(), "the nested block must not release the parent's lock")
    self.assertFalse(self.path.exists())

  def test_current_process_is_alive(self) -> None:
    self.assertTrue(_pid_alive(os.getpid()))


if __name__ == "__main__":
  unittest.main()
