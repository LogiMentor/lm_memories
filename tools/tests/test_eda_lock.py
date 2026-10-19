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

from tools.eda_lock import TOKEN_ENV, LockTimeout, MemoryTimeout, free_physical_mb, heavy_tool_lock

ROOT_DIR = Path(__file__).resolve().parents[2]

# Holder in another process. argv: lock path, mode, log path.
#   wait  - print "ready", hold until stdin closes, release normally;
#   crash - exit without releasing (os._exit inside the block);
#   log   - append "start"/"end" lines to the log file around a short hold;
#   loop  - take and release the lock 8 times, appending "<i>" to the log file
#           while holding it, printing "ready" after the first.
HOLDER = r"""
import os, sys, time
from pathlib import Path
from tools.eda_lock import heavy_tool_lock
path, mode = Path(sys.argv[1]), sys.argv[2]
with heavy_tool_lock("other run", lock_path=path, poll_s=0.02, log=lambda m: None, free_mb=lambda: 1e9):
  if mode == "wait":
    print("ready", flush=True)
    sys.stdin.read()
  elif mode == "crash":
    os._exit(3)
  elif mode == "loop":
    with open(sys.argv[3], "a") as f:
      f.write("0\n")
    print("ready", flush=True)
    time.sleep(0.3)
  else:
    with open(sys.argv[3], "a") as f:
      f.write(f"start {os.getpid()}\n")
    time.sleep(0.2)
    with open(sys.argv[3], "a") as f:
      f.write(f"end {os.getpid()}\n")
if mode == "loop":
  for i in range(1, 8):
    with heavy_tool_lock("other run", lock_path=path, poll_s=0.02, log=lambda m: None, free_mb=lambda: 1e9):
      with open(sys.argv[3], "a") as f:
        f.write(f"{i}\n")
      time.sleep(0.3)
"""


class EdaLockTest(unittest.TestCase):
  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.path = Path(self.tmp.name) / "test.lock"
    self.messages: list[str] = []

  def tearDown(self) -> None:
    self.tmp.cleanup()

  def _lock(self, **kwargs):
    kwargs.setdefault("free_mb", lambda: 1e9)  # plenty of memory unless a test says otherwise
    return heavy_tool_lock("unit test", lock_path=self.path, log=self.messages.append, **kwargs)

  def _holder(self, *args: str, stdin=None, stdout_pipe: bool = False) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k != TOKEN_ENV}
    env["PYTHONPATH"] = str(ROOT_DIR)
    return subprocess.Popen([sys.executable, "-c", HOLDER, str(self.path), *args],
                            cwd=ROOT_DIR, env=env, stdin=stdin, text=True,
                            stdout=subprocess.PIPE if (stdin == subprocess.PIPE or stdout_pipe) else subprocess.DEVNULL)

  def test_acquire_and_release(self) -> None:
    with self._lock(timeout_s=1):
      holder = json.loads(self.path.read_text(encoding="utf-8"))
      self.assertEqual(holder["pid"], os.getpid())
      self.assertEqual(os.environ[TOKEN_ENV], holder["token"])
    self.assertEqual(self.path.read_text(encoding="utf-8"), "", "the holder record is cleared on release")
    self.assertNotIn(TOKEN_ENV, os.environ)

  def test_lock_directory_from_the_environment(self) -> None:
    from unittest import mock

    from tools.eda_lock import LOCK_DIR_ENV, LOCK_NAME, default_lock_path
    shared = Path(self.tmp.name) / "shared"
    with mock.patch.dict(os.environ, {LOCK_DIR_ENV: str(shared)}):
      self.assertEqual(default_lock_path(), shared / LOCK_NAME)
      with heavy_tool_lock("unit test", log=self.messages.append, free_mb=lambda: 1e9, timeout_s=1):
        self.assertTrue((shared / LOCK_NAME).is_file(), "the directory is created")

  @unittest.skipIf(sys.platform == "win32", "POSIX file modes")
  def test_lock_file_is_shared_whatever_the_umask(self) -> None:
    old = os.umask(0o077)
    try:
      with self._lock(timeout_s=1):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o666)
    finally:
      os.umask(old)

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

  def test_waiters_are_served_in_order(self) -> None:
    log = Path(self.tmp.name) / "loop.log"
    proc = self._holder("loop", str(log), stdin=subprocess.DEVNULL, stdout_pipe=True)
    try:
      self.assertEqual(proc.stdout.readline().strip(), "ready")
      with self._lock(timeout_s=10, poll_s=0.02):
        done = log.read_text().split()
      self.assertLess(len(done), 4, f"a looping holder must not starve a waiter: {done}")
    finally:
      proc.wait(timeout=30)
      proc.stdout.close()

  def test_stale_ticket_is_skipped(self) -> None:
    stale = self.path.parent / f"{self.path.name}.q.{0:020d}-1-deadbeef"
    stale.write_text("")
    os.utime(stale, (1, 1))
    with self._lock(timeout_s=1, poll_s=0.02):
      pass
    self.assertFalse(stale.exists())
    self.assertEqual(list(self.path.parent.glob(f"{self.path.name}.q.*")), [])

  def _env(self, **values: str):
    from unittest import mock
    return mock.patch.dict(os.environ, values)

  def test_waits_for_memory_with_the_lock_held(self) -> None:
    readings = iter([1000.0, 3000.0, 9000.0])
    seen_while_waiting = []

    def free() -> float:
      if self.path.read_text(encoding="utf-8"):
        seen_while_waiting.append(json.loads(self.path.read_text(encoding="utf-8")).get("state"))
      return next(readings)

    with self._env(LM_EDA_MIN_FREE_MB_SYNTH="8192"):
      with self._lock(timeout_s=1, poll_s=0.01, kind="synthesis", free_mb=free):
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["state"], "running")
    waiting = [m for m in self.messages if "waiting for memory" in m]
    self.assertEqual(len(waiting), 1, self.messages)
    self.assertIn("1000 MB free, 8192 MB required for a synthesis run (LM_EDA_MIN_FREE_MB_SYNTH)", waiting[0])
    self.assertEqual(seen_while_waiting[0], "waiting for free memory")

  def test_memory_timeout_releases_the_lock(self) -> None:
    with self._env(LM_EDA_MIN_FREE_MB_SIM="2048", LM_EDA_MEMORY_TIMEOUT="0.2"):
      with self.assertRaises(MemoryTimeout) as ctx:
        with self._lock(timeout_s=1, poll_s=0.02, kind="simulation", free_mb=lambda: 500.0):
          self.fail("the block must not run without enough memory")
    self.assertIn("500 MB of physical memory free, 2048 MB required for a simulation run", str(ctx.exception))
    self.assertNotIn(TOKEN_ENV, os.environ)
    with self._lock(timeout_s=1, poll_s=0.02):
      pass

  def test_kind_selects_the_threshold(self) -> None:
    with self._env(LM_EDA_MIN_FREE_MB_SYNTH="8192", LM_EDA_MIN_FREE_MB_SIM="2048"):
      with self._lock(timeout_s=1, poll_s=0.01, kind="simulation", free_mb=lambda: 4000.0):
        pass
    self.assertFalse(any("waiting for memory" in m for m in self.messages))
    with self.assertRaises(ValueError):
      with self._lock(kind="place and route"):
        pass

  def test_unreadable_memory_does_not_block(self) -> None:
    with self._lock(timeout_s=1, poll_s=0.01, free_mb=lambda: None):
      pass
    self.assertTrue(any("cannot be read" in m for m in self.messages))

  def test_free_memory_is_readable_here(self) -> None:
    free = free_physical_mb()
    if sys.platform == "win32" or sys.platform.startswith("linux"):
      self.assertIsNotNone(free)
      self.assertGreater(free, 0)

  def test_child_with_token_does_not_wait(self) -> None:
    with self._lock(timeout_s=1):
      with self._lock(timeout_s=0.2, poll_s=0.05):
        pass
      holder = json.loads(self.path.read_text(encoding="utf-8"))
      self.assertEqual(holder["token"], os.environ[TOKEN_ENV], "the nested block must not release the parent's lock")
    self.assertEqual(self.path.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
  unittest.main()
