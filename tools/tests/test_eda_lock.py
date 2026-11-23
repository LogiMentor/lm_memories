# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the machine-wide EDA lock. They use a private lock path."""

from __future__ import annotations

import ast
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tools.eda_lock import (LOCK_DIR_ENV, LOCK_NAME, TOKEN_ENV, LockTimeout, MemoryTimeout, free_physical_mb,
                            heavy_tool_lock, min_free_mb)

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

  def test_default_thresholds_are_those_of_the_campaigns(self) -> None:
    # The AMD campaign and every check since ran with 4096 MB for synthesis;
    # the default of 8192 MB kept a synthesis waiting with 6.3 GB free.
    from unittest import mock
    with mock.patch.dict(os.environ):
      for env in ("LM_EDA_MIN_FREE_MB_SYNTH", "LM_EDA_MIN_FREE_MB_SIM"):
        os.environ.pop(env, None)
      self.assertEqual(min_free_mb("synthesis"), (4096.0, "LM_EDA_MIN_FREE_MB_SYNTH"))
      self.assertEqual(min_free_mb("simulation"), (2048.0, "LM_EDA_MIN_FREE_MB_SIM"))
      with self._lock(timeout_s=1, poll_s=0.01, kind="synthesis", free_mb=lambda: 6300.0):
        pass
    self.assertFalse(any("waiting for memory" in m for m in self.messages), self.messages)

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


SCRIPT = ROOT_DIR / "tools" / "eda_lock.py"
# The command of the command-line tests: exit 7 only if it runs with the lock
# held for it (the record says "running" and its token is the inherited one).
CHECK_HELD = r"""
import json, os, sys
record = json.loads(open(sys.argv[1], encoding="utf-8").read())
sys.exit(7 if record.get("state") == "running" and record.get("token") == os.environ.get("LM_EDA_LOCK_TOKEN") else 1)
"""
# Writes "ran" to argv[1].
MARK = "import sys; open(sys.argv[1], 'w').write('ran')"
# Prints "ready", then waits; on SIGTERM writes "term" to argv[1] and exits 5.
WAIT_FOR_TERM = r"""
import signal, sys, time
def stop(signum, frame):
  open(sys.argv[1], "w").write("term")
  sys.exit(5)
signal.signal(signal.SIGTERM, stop)
print("ready", flush=True)
time.sleep(60)
"""


class EdaLockCliTest(unittest.TestCase):
  """python3 tools/eda_lock.py [options] -- COMMAND: run as a script, as other repositories do."""

  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.dir = Path(self.tmp.name)
    self.lock = self.dir / LOCK_NAME
    self.marker = self.dir / "marker"

  def tearDown(self) -> None:
    self.tmp.cleanup()

  def _env(self, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != TOKEN_ENV}
    env.update({LOCK_DIR_ENV: str(self.dir), "LM_EDA_MIN_FREE_MB_SYNTH": "0", "LM_EDA_MIN_FREE_MB_SIM": "0"})
    env.update(extra)
    return env

  def _cli(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=self._env(**extra), capture_output=True,
                          text=True, timeout=60, cwd=self.dir)

  def _start(self, *args: str, **extra: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(SCRIPT), *args], env=self._env(**extra), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=self.dir)

  def _released(self) -> bool:
    return self.lock.read_text(encoding="utf-8") == ""

  def test_runs_the_command_with_the_lock_held_and_returns_its_exit_code(self) -> None:
    done = self._cli("--kind", "simulation", "--purpose", "cli test", "--", sys.executable, "-c", CHECK_HELD,
                     str(self.lock))
    self.assertEqual(done.returncode, 7, done.stderr)
    self.assertIn(f"acquired {self.lock} for 'cli test'", done.stderr)
    self.assertIn("released", done.stderr)
    self.assertEqual(done.stdout, "", "the messages of the lock go to stderr")
    self.assertTrue(self._released())

  def test_exit_codes_when_the_command_does_not_run_normally(self) -> None:
    done = self._cli("--", "lm-no-such-command")
    self.assertEqual(done.returncode, 127, done.stderr)
    self.assertIn("command not found", done.stderr)
    self.assertTrue(self._released())
    if os.name != "nt":
      script = self.dir / "not_executable.sh"
      script.write_text("#!/bin/sh\nexit 0\n")
      done = self._cli("--", str(script))
      self.assertEqual(done.returncode, 126, done.stderr)
      self.assertTrue(self._released())
      done = self._cli("--", sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
      self.assertEqual(done.returncode, 128 + signal.SIGKILL, done.stderr)
      self.assertTrue(self._released())

  def test_wrong_options_do_not_run_the_command(self) -> None:
    command = ["--", sys.executable, "-c", MARK, str(self.marker)]
    for args in ([sys.executable, "-c", MARK, str(self.marker)], ["--"], ["--kind", "place", *command],
                 ["--timeout", "-1", *command], ["--timeout", "x", *command], ["--other", *command]):
      with self.subTest(args=args[:3]):
        done = self._cli(*args)
        self.assertEqual(done.returncode, 125, done.stderr)
        self.assertIn("usage:", done.stderr)
        self.assertFalse(self.marker.exists())

  def test_lock_timeout_does_not_run_the_command(self) -> None:
    with heavy_tool_lock("holder", lock_path=self.lock, log=lambda m: None, free_mb=lambda: 1e9):
      done = self._cli("--timeout", "0.5", "--", sys.executable, "-c", MARK, str(self.marker))
    self.assertEqual(done.returncode, 125, done.stderr)
    self.assertIn("gave up", done.stderr)
    self.assertIn("purpose 'holder'", done.stderr)
    self.assertFalse(self.marker.exists())

  def test_memory_timeout_does_not_run_the_command(self) -> None:
    done = self._cli("--kind", "simulation", "--", sys.executable, "-c", MARK, str(self.marker),
                     LM_EDA_MIN_FREE_MB_SIM=str(10**12), LM_EDA_MEMORY_TIMEOUT="0.3")
    self.assertEqual(done.returncode, 125, done.stderr)
    self.assertIn("required for a simulation run", done.stderr)
    self.assertFalse(self.marker.exists())
    self.assertTrue(self._released())

  @unittest.skipIf(os.name == "nt", "POSIX signals")
  def test_signals_never_release_the_lock_before_the_command_exits(self) -> None:
    cli = self._start("--", sys.executable, "-c", WAIT_FOR_TERM, str(self.marker))
    try:
      self.assertEqual(cli.stdout.readline().strip(), "ready")
      # A SIGINT to the command line alone: ignored (a terminal sends it to the
      # command as well); the command runs on with the lock.
      cli.send_signal(signal.SIGINT)
      time.sleep(0.5)
      self.assertIsNone(cli.poll(), "the command line must wait for the command")
      self.assertEqual(json.loads(self.lock.read_text(encoding="utf-8"))["state"], "running")
      # SIGTERM: relayed to the command; the lock is released after it exits.
      cli.send_signal(signal.SIGTERM)
      self.assertEqual(cli.wait(timeout=30), 5, cli.stderr.read())
    finally:
      if cli.poll() is None:
        cli.kill()
      cli.communicate()
    self.assertEqual(self.marker.read_text(), "term")
    self.assertTrue(self._released())

  @unittest.skipIf(os.name == "nt", "POSIX signals")
  def test_a_signal_while_waiting_stops_the_wait(self) -> None:
    with heavy_tool_lock("holder", lock_path=self.lock, log=lambda m: None, free_mb=lambda: 1e9):
      cli = self._start("--timeout", "60", "--", sys.executable, "-c", MARK, str(self.marker))
      try:
        deadline = time.monotonic() + 30
        while not list(self.dir.glob(f"{LOCK_NAME}.q.*")) and time.monotonic() < deadline:
          time.sleep(0.05)  # the command line is queued (its ticket exists)
        cli.send_signal(signal.SIGTERM)
        self.assertEqual(cli.wait(timeout=30), 128 + signal.SIGTERM)
      finally:
        if cli.poll() is None:
          cli.kill()
        _, err = cli.communicate()
    self.assertIn("SIGTERM before the command started", err)
    self.assertFalse(self.marker.exists())
    self.assertEqual(list(self.dir.glob(f"{LOCK_NAME}.q.*")), [], "the ticket is removed")

  def test_the_file_needs_only_the_standard_library(self) -> None:
    # Other repositories keep an identical copy: no import of this repository.
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    modules = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    modules |= {node.module.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module and node.level == 0}
    self.assertEqual(sorted(modules - sys.stdlib_module_names - {"__future__"}), [])
    self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level],
                     "no relative import")
    # A copy alone in another directory, in isolated mode, runs.
    copy = self.dir / "elsewhere" / "eda_lock.py"
    copy.parent.mkdir()
    shutil.copy(SCRIPT, copy)
    done = subprocess.run([sys.executable, "-I", str(copy), "--", sys.executable, "-c", "raise SystemExit(3)"],
                          env=self._env(), capture_output=True, text=True, timeout=60, cwd=copy.parent)
    self.assertEqual(done.returncode, 3, done.stderr)


if __name__ == "__main__":
  unittest.main()
