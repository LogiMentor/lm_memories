# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the machine-wide EDA lock. They use a private lock path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.eda_lock import TOKEN_ENV, LockTimeout, heavy_tool_lock

ROOT_DIR = Path(__file__).resolve().parents[2]

# Holder in another process. argv: lock path, mode, log path.
#   wait  - print "ready", hold until stdin closes, release normally;
#   crash - exit without releasing (os._exit inside the block);
#   log   - append "start"/"end" lines to the log file around a short hold.
HOLDER = r"""
import os, sys, time
from pathlib import Path
from tools.eda_lock import heavy_tool_lock
path, mode = Path(sys.argv[1]), sys.argv[2]
with heavy_tool_lock("other run", lock_path=path, poll_s=0.02, log=lambda m: None):
  if mode == "wait":
    print("ready", flush=True)
    sys.stdin.read()
  elif mode == "crash":
    os._exit(3)
  else:
    with open(sys.argv[3], "a") as f:
      f.write(f"start {os.getpid()}\n")
    time.sleep(0.2)
    with open(sys.argv[3], "a") as f:
      f.write(f"end {os.getpid()}\n")
"""


class EdaLockTest(unittest.TestCase):
  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.path = Path(self.tmp.name) / "test.lock"
    self.messages: list[str] = []

  def tearDown(self) -> None:
    self.tmp.cleanup()

  def _lock(self, **kwargs):
    return heavy_tool_lock("unit test", lock_path=self.path, log=self.messages.append, **kwargs)

  def _holder(self, *args: str, stdin=None) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k != TOKEN_ENV}
    env["PYTHONPATH"] = str(ROOT_DIR)
    return subprocess.Popen([sys.executable, "-c", HOLDER, str(self.path), *args],
                            cwd=ROOT_DIR, env=env, stdin=stdin, text=True,
                            stdout=subprocess.PIPE if stdin else subprocess.DEVNULL)

  def test_acquire_and_release(self) -> None:
    with self._lock(timeout_s=1):
      holder = json.loads(self.path.read_text(encoding="utf-8"))
      self.assertEqual(holder["pid"], os.getpid())
      self.assertEqual(os.environ[TOKEN_ENV], holder["token"])
    self.assertEqual(self.path.read_text(encoding="utf-8"), "", "the holder record is cleared on release")
    self.assertNotIn(TOKEN_ENV, os.environ)

  def test_live_holder_times_out_with_its_description(self) -> None:
    proc = self._holder("wait", stdin=subprocess.PIPE)
    try:
      self.assertEqual(proc.stdout.readline().strip(), "ready")
      with self.assertRaises(LockTimeout) as ctx:
        with self._lock(timeout_s=0.3, poll_s=0.05):
          pass
      self.assertIn("other run", str(ctx.exception))
      self.assertIn(str(proc.pid), str(ctx.exception))
      self.assertTrue(any("waiting for" in m for m in self.messages))
    finally:
      proc.stdin.close()
      proc.wait(timeout=10)
      proc.stdout.close()
    with self._lock(timeout_s=5, poll_s=0.05):
      pass

  def test_crashed_holder_releases_the_lock(self) -> None:
    proc = self._holder("crash")
    self.assertEqual(proc.wait(timeout=10), 3)
    self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["pid"], proc.pid)
    with self._lock(timeout_s=1, poll_s=0.05):
      self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["pid"], os.getpid())
    self.assertFalse(any("waiting for" in m for m in self.messages))

  def test_contenders_never_overlap(self) -> None:
    log = Path(self.tmp.name) / "holders.log"
    procs = [self._holder("log", str(log)) for _ in range(4)]
    for proc in procs:
      self.assertEqual(proc.wait(timeout=60), 0)
    lines = log.read_text().split()
    events = list(zip(lines[0::2], lines[1::2]))
    self.assertEqual(len(events), 8)
    for i in range(0, 8, 2):
      self.assertEqual(events[i][0], "start")
      self.assertEqual(events[i + 1], ("end", events[i][1]), f"overlapping holders: {events}")

  def test_child_with_token_does_not_wait(self) -> None:
    with self._lock(timeout_s=1):
      with self._lock(timeout_s=0.2, poll_s=0.05):
        pass
      holder = json.loads(self.path.read_text(encoding="utf-8"))
      self.assertEqual(holder["token"], os.environ[TOKEN_ENV], "the nested block must not release the parent's lock")
    self.assertEqual(self.path.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
  unittest.main()
