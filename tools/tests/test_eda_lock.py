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

from tools.eda_lock import (LOCK_DIR_ENV, LOCK_NAME, MEMORY_TIMEOUT_ENV, TIMEOUT_ENV, TOKEN_ENV, LockConfigError,
                            LockTimeout, MemoryTimeout, free_physical_mb, heavy_tool_lock, min_free_mb)

import tools.eda_lock as eda_lock

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
# Writes "overlap" to argv[2] if process argv[1] still runs, "after" otherwise.
CHECK_GONE = r"""
import subprocess, sys
stat = subprocess.run(["ps", "-o", "stat=", "-p", sys.argv[1]], capture_output=True, text=True).stdout.strip()
open(sys.argv[2], "w").write("overlap" if stat and not stat.startswith("Z") else "after")
"""


def _running(pid: int) -> bool:
  """True if process pid exists and is not a zombie (POSIX, through ps)."""
  stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
  return bool(stat) and not stat.startswith("Z")


def _running_with(args: str) -> list[int]:
  """Process ids of the running processes whose command line is args (POSIX, through ps)."""
  listing = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "stat=", "-o", "args="], capture_output=True,
                           text=True).stdout
  return [int(fields[0]) for fields in (line.split(None, 2) for line in listing.splitlines())
          if len(fields) == 3 and fields[2].strip() == args and not fields[1].startswith("Z")]


def _descendant(root: int, args: str, timeout_s: float = 30.0) -> int:
  """Process id of the descendant of root whose command line is args, once it runs (POSIX, through ps)."""
  deadline = time.monotonic() + timeout_s
  while time.monotonic() < deadline:
    listing = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "args="], capture_output=True,
                             text=True).stdout
    parent, command = {}, {}
    for line in listing.splitlines():
      fields = line.split(None, 2)
      if len(fields) == 3:
        parent[int(fields[0])], command[int(fields[0])] = int(fields[1]), fields[2].strip()
    for pid, line in command.items():
      ancestor = parent.get(pid)
      while ancestor not in (None, 0, 1, root):
        ancestor = parent.get(ancestor)
      if line == args and ancestor == root:
        return pid
    time.sleep(0.05)
  raise AssertionError(f"no descendant '{args}' of process {root}")


class EdaLockSettingsTest(unittest.TestCase):
  """Review of PR #11: malformed settings are refused before the run queues."""

  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.dir = Path(self.tmp.name)

  def tearDown(self) -> None:
    self.tmp.cleanup()

  def _enter(self, **kwargs) -> None:
    with heavy_tool_lock("settings", lock_path=self.dir / LOCK_NAME, log=lambda m: None, free_mb=lambda: 1e9,
                         **kwargs):
      self.fail("the block must not run")

  def test_malformed_environment(self) -> None:
    from unittest import mock
    for env, kind in ((TIMEOUT_ENV, "synthesis"), (MEMORY_TIMEOUT_ENV, "synthesis"),
                      ("LM_EDA_MIN_FREE_MB_SYNTH", "synthesis"), ("LM_EDA_MIN_FREE_MB_SIM", "simulation")):
      for value in ("abc", "nan", "inf", "-1"):
        with self.subTest(env=env, value=value), mock.patch.dict(os.environ, {env: value}):
          with self.assertRaises(LockConfigError) as raised:
            self._enter(kind=kind)
          self.assertIn(f"{env}={value!r} is not valid", str(raised.exception))
          self.assertEqual(list(self.dir.iterdir()), [], "refused before queuing: no lock or ticket file")

  def test_non_finite_timeout_argument(self) -> None:
    for value in (float("nan"), float("inf"), -1.0):
      with self.subTest(value=value), self.assertRaises(LockConfigError):
        self._enter(timeout_s=value)

  def test_lock_directory_that_is_a_file(self) -> None:
    from unittest import mock
    a_file = self.dir / "not_a_directory"
    a_file.write_text("")
    with mock.patch.dict(os.environ, {LOCK_DIR_ENV: str(a_file)}):
      with self.assertRaises(LockConfigError) as raised:
        with heavy_tool_lock("settings", log=lambda m: None, free_mb=lambda: 1e9):
          self.fail("the block must not run")
    self.assertIn(f"{LOCK_DIR_ENV}=", str(raised.exception))

  def test_valid_values_are_accepted(self) -> None:
    from unittest import mock
    with mock.patch.dict(os.environ, {TIMEOUT_ENV: "1.5", MEMORY_TIMEOUT_ENV: "0", "LM_EDA_MIN_FREE_MB_SYNTH": "0"}):
      with heavy_tool_lock("settings", lock_path=self.dir / LOCK_NAME, log=lambda m: None, free_mb=lambda: 1e9):
        pass


def _write_protection_is_enforced() -> bool:
  """False where a directory without write permission can still be written (root, Windows)."""
  return os.name != "nt" and os.geteuid() != 0


class EdaLockSetupErrorTest(unittest.TestCase):
  """Review of PR #11: a lock directory or file that cannot be created or opened raises LockSetupError."""

  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.dir = Path(self.tmp.name)

  def tearDown(self) -> None:
    for path in self.dir.rglob("*"):
      if path.is_dir():
        path.chmod(0o755)
    self.tmp.cleanup()

  def _enter(self, lock: Path) -> str:
    with self.assertRaises(eda_lock.LockSetupError) as raised:
      with heavy_tool_lock("setup", lock_path=lock, log=lambda m: None, free_mb=lambda: 1e9):
        self.fail("the block must not run")
    self.assertIsInstance(raised.exception, OSError)
    return str(raised.exception)

  def test_directory_below_a_file(self) -> None:
    a_file = self.dir / "a_file"
    a_file.write_text("")
    message = self._enter(a_file / "sub" / LOCK_NAME)
    self.assertIn(f"cannot create the lock directory {a_file / 'sub'}", message)
    self.assertEqual(a_file.read_text(), "")

  @unittest.skipUnless(_write_protection_is_enforced(), "POSIX permissions, not as root")
  def test_directory_without_write_permission(self) -> None:
    locked = self.dir / "read_only"
    locked.mkdir()
    locked.chmod(0o555)
    message = self._enter(locked / LOCK_NAME)
    self.assertIn(f"cannot create the ticket {locked / LOCK_NAME}.q.", message)
    self.assertIn("Permission denied", message)
    self.assertEqual(list(locked.iterdir()), [])

  def test_lock_file_that_cannot_be_opened_leaves_no_ticket(self) -> None:
    lock = self.dir / LOCK_NAME
    lock.mkdir()  # a directory where the lock file should be
    message = self._enter(lock)
    self.assertIn(f"cannot open the lock file {lock}", message)
    self.assertEqual(list(self.dir.glob(LOCK_NAME + ".q.*")), [], "the ticket is released")


LINUX = sys.platform.startswith("linux")


@unittest.skipUnless(LINUX, "Linux /proc, pidfd and child subreaper")
class EdaLockProcessTreeTest(unittest.TestCase):
  """Review of PR #11: the process tree of the command, on Linux."""

  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.lock = Path(self.tmp.name) / LOCK_NAME

  def tearDown(self) -> None:
    self.tmp.cleanup()

  def test_process_table(self) -> None:
    me = os.getpid()
    table = eda_lock._process_table()
    self.assertEqual(table[me][0], os.getppid())
    self.assertTrue(table[me][1], "start time")
    self.assertNotEqual(table[me][2], "Z")
    self.assertIn(me, eda_lock._descendants([os.getppid()], table))

  def _signal_only_the_same_process(self) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
      start = eda_lock._stat(proc.pid)[1]
      self.assertFalse(eda_lock._send_signal(proc.pid, start + "1", signal.SIGTERM), "another start time")
      time.sleep(0.2)
      self.assertIsNone(proc.poll())
      self.assertTrue(eda_lock._send_signal(proc.pid, start, signal.SIGTERM))
      self.assertEqual(proc.wait(timeout=10), -signal.SIGTERM)
      self.assertFalse(eda_lock._send_signal(proc.pid, start, signal.SIGTERM), "an exited process")
    finally:
      if proc.poll() is None:
        proc.kill()
        proc.wait()

  def test_signals_reach_only_the_same_process(self) -> None:
    self._signal_only_the_same_process()

  def test_signals_without_pidfd(self) -> None:
    import errno
    from unittest import mock
    with mock.patch.object(os, "pidfd_open", side_effect=OSError(errno.ENOSYS, "not implemented"), create=True):
      self._signal_only_the_same_process()

  def test_orphans_never_take_the_exit_status_of_the_command(self) -> None:
    # The adopted orphans are reaped by their own process id: the exit status of
    # the command, and of another child of this process, stay with their owners.
    me = os.getpid()
    zombies = {pid for pid, (ppid, _, st) in eda_lock._process_table().items() if ppid == me and st == "Z"}
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.4); raise SystemExit(7)"])
    try:
      messages: list[str] = []
      code = eda_lock.run(["sh", "-c", "(sleep 0.2 &); (sleep 0.1 &); sleep 0.8; exit 3"], lock_path=self.lock,
                          log=messages.append, free_mb=lambda: 1e9)
      self.assertEqual(code, 3, messages)
      self.assertEqual(other.wait(timeout=30), 7, "another child of this process keeps its exit status")
      left = {pid for pid, (ppid, _, st) in eda_lock._process_table().items() if ppid == me and st == "Z"}
      self.assertEqual(left - zombies, set(), "the adopted orphans are reaped")
      self.assertEqual(eda_lock._child_subreaper(0), 0, "the child-subreaper attribute is restored")
    finally:
      if other.poll() is None:
        other.kill()
        other.wait()


class EdaLockCliTest(unittest.TestCase):
  """python3 tools/eda_lock.py [options] -- COMMAND: run as a script, as other repositories do."""

  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.dir = Path(self.tmp.name)
    self.lock = self.dir / LOCK_NAME
    self.marker = self.dir / "marker"

  def tearDown(self) -> None:
    for path in self.dir.rglob("*"):
      if path.is_dir():
        path.chmod(0o755)
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
                 ["--timeout", "-1", *command], ["--timeout", "x", *command], ["--other", *command],
                 ["--kill-after", "-1", *command], ["--kill-after=nan", *command], ["--kill-after=inf", *command]):
      with self.subTest(args=args[:3]):
        done = self._cli(*args)
        self.assertEqual(done.returncode, 125, done.stderr)
        self.assertIn("usage:", done.stderr)
        self.assertFalse(self.marker.exists())

  def test_non_finite_timeout_is_a_wrong_option(self) -> None:
    # Review of PR #11: --timeout NaN gave a deadline that is never reached.
    for value in ("nan", "NaN", "inf", "-inf"):
      with self.subTest(value=value):
        done = self._cli(f"--timeout={value}", "--", sys.executable, "-c", MARK, str(self.marker))
        self.assertEqual(done.returncode, 125, done.stderr)
        self.assertIn("usage:", done.stderr)
        self.assertIn("a finite number of seconds, not negative, is required", done.stderr)
        self.assertFalse(self.marker.exists())

  def test_malformed_settings_in_the_environment_do_not_run_the_command(self) -> None:
    # Review of PR #11: LM_EDA_MEMORY_TIMEOUT=abc stopped with a traceback and exit code 1.
    a_file = self.dir / "not_a_directory"
    a_file.write_text("")
    cases = [(kind, env, value) for kind, env in (("synthesis", TIMEOUT_ENV), ("synthesis", MEMORY_TIMEOUT_ENV),
                                                  ("synthesis", "LM_EDA_MIN_FREE_MB_SYNTH"),
                                                  ("simulation", "LM_EDA_MIN_FREE_MB_SIM"))
             for value in ("abc", "nan", "inf", "-1")]
    cases.append(("synthesis", LOCK_DIR_ENV, str(a_file)))
    for kind, env, value in cases:
      with self.subTest(env=env, value=value):
        done = self._cli("--kind", kind, "--", sys.executable, "-c", MARK, str(self.marker), **{env: value})
        self.assertEqual(done.returncode, 125, done.stderr)
        self.assertIn(f"{env}={value!r} is not valid", done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(list(self.dir.glob(LOCK_NAME + "*")), [], "refused before queuing")

  def test_lock_setup_errors_do_not_run_the_command(self) -> None:
    # Review of PR #11: an OSError while preparing the lock stopped with a traceback and exit code 1.
    a_file = self.dir / "a_file"
    a_file.write_text("")
    cases = [(a_file / "sub", f"cannot create the lock directory {a_file / 'sub'}")]
    if _write_protection_is_enforced():
      locked = self.dir / "read_only"
      locked.mkdir()
      locked.chmod(0o555)
      cases.append((locked, f"cannot create the ticket {locked / LOCK_NAME}.q."))
    for directory, expected in cases:
      with self.subTest(directory=directory.name):
        done = self._cli("--", sys.executable, "-c", MARK, str(self.marker), **{LOCK_DIR_ENV: str(directory)})
        self.assertEqual(done.returncode, 125, done.stderr)
        self.assertIn(expected, done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertFalse(self.marker.exists())

  def test_release_errors_keep_the_exit_code_of_the_command(self) -> None:
    # Review of PR #11: once the command has started, its exit code stands.
    from unittest import mock
    for command, expected in (([sys.executable, "-c", "raise SystemExit(4)"], 4),
                              (["lm-no-such-command"], eda_lock.EXIT_NOT_FOUND)):
      with self.subTest(command=command[-1]):
        messages: list[str] = []
        with mock.patch.object(eda_lock, "_unlock", side_effect=OSError(5, "Input/output error")):
          code = eda_lock.run(command, lock_path=self.lock, log=messages.append, free_mb=lambda: 1e9)
        self.assertEqual(code, expected, messages)
        self.assertTrue(any("exit code of the command kept" in m for m in messages), messages)

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

  def _stop(self, *procs: subprocess.Popen | None, pid: int | None = None) -> None:
    for proc in procs:
      if proc is not None:
        if proc.poll() is None:
          proc.kill()
        proc.communicate()
    if pid is not None and _running(pid):
      os.kill(pid, signal.SIGKILL)  # a descendant of a command started by this test

  @unittest.skipUnless(LINUX, "Linux process tree")
  def test_sigterm_stops_the_descendants_before_the_lock_is_released(self) -> None:
    # Review of PR #11: SIGTERM reached only the shell; its sleep went on after the lock was released.
    cli = self._start("--", "sh", "-c", "sleep 47.3; :")
    second = sleep_pid = None
    try:
      sleep_pid = _descendant(cli.pid, "sleep 47.3")
      second = self._start("--timeout", "60", "--", sys.executable, "-c", CHECK_GONE, str(sleep_pid),
                           str(self.marker))
      deadline = time.monotonic() + 30
      while not list(self.dir.glob(f"{LOCK_NAME}.q.*")) and time.monotonic() < deadline:
        time.sleep(0.05)  # the second command line is queued (its ticket exists)
      cli.send_signal(signal.SIGTERM)
      self.assertEqual(cli.wait(timeout=30), 128 + signal.SIGTERM)
      self.assertFalse(_running(sleep_pid), "a descendant of the command outlived the command line")
      self.assertEqual(second.wait(timeout=30), 0)
      self.assertEqual(self.marker.read_text(), "after", "the second run took the lock while the descendant ran")
    finally:
      self._stop(cli, second, pid=sleep_pid)

  @unittest.skipUnless(LINUX, "Linux process tree")
  def test_processes_started_while_the_command_stops_are_stopped_too(self) -> None:
    # Review of PR #11: a process started by a SIGTERM trap, whose parent then
    # exited, was adopted by init and ran on after the lock was released.
    cli = self._start("--", "sh", "-c", 'trap "sleep 47.5 & exit 0" TERM; sleep 47.6 & wait')
    try:
      _descendant(cli.pid, "sleep 47.6")
      cli.send_signal(signal.SIGTERM)
      self.assertEqual(cli.wait(timeout=30), 0, "the exit code of the trap")
      self.assertEqual(_running_with("sleep 47.5"), [], "a process of the command outlived the command line")
      self.assertTrue(self._released())
    finally:
      self._stop(cli)
      for pid in _running_with("sleep 47.5") + _running_with("sleep 47.6"):
        os.kill(pid, signal.SIGKILL)  # started by this test

  @unittest.skipUnless(LINUX, "Linux process tree")
  def test_descendants_that_ignore_sigterm_receive_sigkill(self) -> None:
    cli = self._start("--kill-after", "1", "--", "sh", "-c", 'trap "" TERM; sleep 47.4; :')
    sleep_pid = None
    try:
      sleep_pid = _descendant(cli.pid, "sleep 47.4")
      started = time.monotonic()
      cli.send_signal(signal.SIGTERM)
      self.assertEqual(cli.wait(timeout=30), 128 + signal.SIGTERM, "the exit code tells the relayed signal")
      self.assertGreaterEqual(time.monotonic() - started, 1.0)
      _, err = cli.communicate()
      self.assertIn("SIGKILL to the processes still running 1 s after SIGTERM", err)
      self.assertFalse(_running(sleep_pid))
      self.assertTrue(self._released())
    finally:
      self._stop(cli, pid=sleep_pid)

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
