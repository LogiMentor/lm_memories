# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Machine-wide lock for heavy EDA runs (synthesis, large simulations).

Every script that starts a heavy tool takes this lock first, so sessions on
the same machine queue up instead of running in parallel. The lock is a file
with a fixed name, not tied to any repository, so every project that uses it
queues with the others. This file depends only on the Python standard
library (3.11 or newer): other repositories keep an identical copy.

From Python: "with heavy_tool_lock(purpose, kind=...):". From a shell, a
Tcl flow or CI, the command line runs a command holding the lock:

  python3 eda_lock.py [--kind synthesis|simulation] [--timeout SECONDS]
                      [--purpose TEXT] -- COMMAND [ARG...]

It waits for the lock and then for free memory (--kind, default synthesis),
runs COMMAND, and releases the lock when COMMAND exits. Its exit code is
that of COMMAND (128 + N if signal N stopped it); 125 if COMMAND did not run
(wrong options, a malformed setting in the environment, lock or memory
timeout), 126 if it cannot be started, 127 if it does not exist. Messages go to stderr. The lock is released only after
COMMAND has exited: the signals a terminal sends to its whole foreground
process group (SIGINT, SIGQUIT; SIGBREAK on Windows) reach COMMAND directly
and the command line ignores them; SIGTERM and SIGHUP are relayed to COMMAND.
A signal before COMMAND starts stops the wait, and COMMAND does not run. A
process killed with SIGKILL cannot do any of this: the operating system
releases the lock and COMMAND goes on without it.

- The lock is an operating-system lock on that file (msvcrt.locking on
  Windows, flock elsewhere). The operating system releases it when the holder
  exits, also after a crash, so there is no stale-lock cleanup and no window
  where two waiters can both take it. The lock never terminates a process
  (the command line only relays the signals it receives itself).
- The file holds JSON describing the holder (pid, host, user, purpose,
  command, start time); a waiting process prints it. The file itself is never
  deleted.
- Waiters are served in order of arrival. Each waiter creates a ticket file
  next to the lock (<lock>.q.<time>-<pid>-<id>) and keeps an operating-system
  lock on it while it waits; it takes the lock only when no older live ticket
  exists. A ticket that can be locked by someone else belongs to a process
  that has exited and is removed. Without this, a process that releases and
  takes the lock again in a loop (one lock per synthesis case) would starve
  the others. Mutual exclusion depends only on the main lock.
- Timeout: argument, or LM_EDA_LOCK_TIMEOUT (seconds), default 4 hours.
- Memory: once it holds the lock, a run waits until the free physical memory
  reaches a threshold before it starts its tool, so that it does not start
  while other programs (for example tools of other projects, which do not
  use this lock) fill the memory. The threshold depends on the kind of run:
  "synthesis" (Vivado, Quartus) LM_EDA_MIN_FREE_MB_SYNTH, default 4096 MB;
  "simulation" LM_EDA_MIN_FREE_MB_SIM, default 2048 MB. While it waits it
  keeps the lock, so the queue order is kept. Timeout: LM_EDA_MEMORY_TIMEOUT
  (seconds), default 4 hours. Where free memory cannot be read, it does not
  wait and says so.
- Timeouts and thresholds, from an argument or from the environment, must be
  finite numbers, not negative; LM_EDA_LOCK_DIR must not name an existing
  file that is not a directory. Anything else raises LockConfigError (a
  ValueError) before the run queues; the command line then exits with 125.
- Child processes of the holder inherit LM_EDA_LOCK_TOKEN and do not wait
  for the lock their parent already holds.
- Directory: LM_EDA_LOCK_DIR, else the system temporary directory. On
  Windows that directory is per user (%TEMP%); users who share a machine set
  LM_EDA_LOCK_DIR to one directory they can all write (for example
  C:/ProgramData/lm_eda). On POSIX systems the lock and ticket files are
  made readable and writable by everyone (mode 0666, whatever the umask), so
  that other users can queue on them in a shared /tmp.
"""

from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

LOCK_NAME = "lm_eda_heavy_tools.lock"
TOKEN_ENV = "LM_EDA_LOCK_TOKEN"
TIMEOUT_ENV = "LM_EDA_LOCK_TIMEOUT"
DEFAULT_TIMEOUT_S = 4 * 3600.0
# Windows locks are mandatory: lock one byte far past the holder record, so
# waiters can still read the record.
_WIN_LOCK_OFFSET = 1 << 30
# A ticket younger than this is never judged stale (its owner creates it
# first and locks it right after).
TICKET_GRACE_S = 2.0

MEMORY_TIMEOUT_ENV = "LM_EDA_MEMORY_TIMEOUT"
# kind of run -> (environment variable, default threshold in MB)
MIN_FREE_MB = {
  "synthesis": ("LM_EDA_MIN_FREE_MB_SYNTH", 4096.0),
  "simulation": ("LM_EDA_MIN_FREE_MB_SIM", 2048.0),
}
# While waiting for memory, repeat the status line this often.
MEMORY_REPORT_S = 600.0


class LockTimeout(TimeoutError):
  """Raised when the lock is still held by someone else after the timeout."""


class MemoryTimeout(TimeoutError):
  """Raised when the free memory stays below the threshold until the timeout."""


class LockConfigError(ValueError):
  """Raised when a setting of the lock (argument or environment) is malformed."""


def _number(value: object, name: str, unit: str) -> float:
  """value as a finite, non-negative number; LockConfigError naming name otherwise."""
  try:
    number = float(value)  # type: ignore[arg-type]
  except (TypeError, ValueError):
    number = math.nan
  if not math.isfinite(number) or number < 0:
    raise LockConfigError(f"[eda-lock] {name}={value!r} is not valid: a finite number of {unit}, not negative, "
                          "is required")
  return number


def _env_number(env: str, default: float, unit: str) -> float:
  value = os.environ.get(env)
  return default if value is None else _number(value, env, unit)


def lock_timeout_s(timeout_s: float | None = None) -> float:
  """Timeout for the lock in seconds: the argument, else LM_EDA_LOCK_TIMEOUT, else the default."""
  if timeout_s is not None:
    return _number(timeout_s, "timeout", "seconds")
  return _env_number(TIMEOUT_ENV, DEFAULT_TIMEOUT_S, "seconds")


def memory_timeout_s() -> float:
  """Timeout for free memory in seconds: LM_EDA_MEMORY_TIMEOUT, else the default."""
  return _env_number(MEMORY_TIMEOUT_ENV, DEFAULT_TIMEOUT_S, "seconds")


def free_physical_mb() -> float | None:
  """Free physical memory in MB, or None where it cannot be read."""
  if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class MemoryStatusEx(ctypes.Structure):
      _fields_ = [
        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
      ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
      return None
    return status.ullAvailPhys / 2**20
  try:
    with open("/proc/meminfo", encoding="ascii") as handle:
      for line in handle:
        if line.startswith("MemAvailable:"):
          return int(line.split()[1]) / 1024.0
  except OSError:
    pass
  return None


def min_free_mb(kind: str) -> tuple[float, str]:
  """Threshold in MB for a kind of run, and the variable that sets it."""
  if kind not in MIN_FREE_MB:
    raise ValueError(f"unknown kind of run '{kind}' (known: {', '.join(MIN_FREE_MB)})")
  env, default = MIN_FREE_MB[kind]
  return _env_number(env, default, "MB"), env


def _wait_for_memory(
  kind: str,
  poll_s: float,
  log: Callable[[str], None],
  free_mb: Callable[[], float | None],
) -> None:
  need, env = min_free_mb(kind)
  timeout_s = memory_timeout_s()
  start = time.monotonic()
  last_report = None
  while True:
    free = free_mb()
    if free is None:
      log(f"[eda-lock] free memory cannot be read on this system; starting without the {need:.0f} MB check")
      return
    if free >= need:
      if last_report is not None:
        log(f"[eda-lock] {free:.0f} MB free, starting the {kind} run")
      return
    waited = time.monotonic() - start
    if waited >= timeout_s:
      raise MemoryTimeout(f"[eda-lock] gave up after {timeout_s:.0f} s: {free:.0f} MB of physical memory free, "
                          f"{need:.0f} MB required for a {kind} run ({env}, timeout {MEMORY_TIMEOUT_ENV})")
    if last_report is None or time.monotonic() - last_report >= MEMORY_REPORT_S:
      log(f"[eda-lock] holding the lock, waiting for memory: {free:.0f} MB free, {need:.0f} MB required "
          f"for a {kind} run ({env}); waited {waited:.0f} s of {timeout_s:.0f} s ({MEMORY_TIMEOUT_ENV})")
      last_report = time.monotonic()
    time.sleep(min(poll_s, max(0.05, timeout_s - waited)))


LOCK_DIR_ENV = "LM_EDA_LOCK_DIR"


def default_lock_path() -> Path:
  directory = os.environ.get(LOCK_DIR_ENV)
  if directory and os.path.exists(directory) and not os.path.isdir(directory):
    raise LockConfigError(f"[eda-lock] {LOCK_DIR_ENV}={directory!r} is not valid: it exists and is not a directory")
  return Path(directory or tempfile.gettempdir()) / LOCK_NAME


def _open_shared(path: Path, flags: int) -> int:
  """Open (or create) a lock or ticket file that every user may lock."""
  fd = os.open(path, flags, 0o666)
  if sys.platform != "win32":
    try:
      os.fchmod(fd, 0o666)  # the umask would otherwise keep other users out
    except OSError:
      pass  # created by another user, who set the mode already
  return fd


def _try_lock(fd: int) -> bool:
  if sys.platform == "win32":
    import msvcrt

    os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
    try:
      msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
      return False
    return True
  import fcntl

  try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
  except BlockingIOError:
    return False
  return True


def _unlock(fd: int) -> None:
  if sys.platform == "win32":
    import msvcrt

    os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
  else:
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def _ticket_glob(path: Path) -> str:
  return f"{path.name}.q.*"


def _older_live_ticket(path: Path, mine: Path) -> Path | None:
  """The oldest ticket older than ours whose owner is still waiting."""
  for ticket in sorted(path.parent.glob(_ticket_glob(path))):
    if ticket.name >= mine.name:
      return None
    try:
      if time.time() - ticket.stat().st_mtime < TICKET_GRACE_S:
        return ticket  # just created: its owner may not have locked it yet
      fd = os.open(ticket, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError:
      continue  # already gone
    try:
      locked = _try_lock(fd)
      if locked:
        _unlock(fd)
    finally:
      os.close(fd)
    if not locked:
      return ticket
    try:
      ticket.unlink()  # its owner has exited
    except OSError:
      pass
  return None


def _write_record(fd: int, record: dict | None) -> None:
  os.ftruncate(fd, 0)
  if record is not None:
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps(record).encode("utf-8"))


def _read_holder(path: Path) -> dict | None:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return None


def describe(holder: dict | None) -> str:
  if not holder:
    return "unknown holder (lock file unreadable or being written)"
  state = f", {holder['state']}" if holder.get("state") else ""
  return (f"pid {holder.get('pid')} on {holder.get('host')} (user {holder.get('user')}), "
          f"purpose '{holder.get('purpose')}'{state}, since {holder.get('started')}, "
          f"command: {holder.get('command')}")


@contextmanager
def heavy_tool_lock(
  purpose: str,
  timeout_s: float | None = None,
  poll_s: float = 5.0,
  lock_path: Path | None = None,
  log: Callable[[str], None] = print,
  kind: str = "synthesis",
  free_mb: Callable[[], float | None] = free_physical_mb,
) -> Iterator[Path]:
  """Hold the machine-wide lock for the duration of the block.

  kind ("synthesis" or "simulation") selects the free-memory threshold that
  must be reached, with the lock held, before the block starts.
  """
  # Every setting is checked before queuing: a malformed one raises
  # LockConfigError here, not after the wait.
  min_free_mb(kind)
  memory_timeout_s()
  timeout_s = lock_timeout_s(timeout_s)
  path = lock_path or default_lock_path()
  inherited = os.environ.get(TOKEN_ENV)
  if inherited:
    holder = _read_holder(path)
    if holder and holder.get("token") == inherited:
      yield path
      return

  token = uuid.uuid4().hex
  info = {
    "token": token,
    "pid": os.getpid(),
    "host": socket.gethostname(),
    "user": getpass.getuser(),
    "purpose": purpose,
    "command": " ".join(sys.argv),
    "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
  }
  deadline = time.monotonic() + timeout_s
  announced = False
  flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
  ticket = path.parent / f"{path.name}.q.{time.time_ns():020d}-{os.getpid()}-{token[:8]}"
  path.parent.mkdir(parents=True, exist_ok=True)
  ticket_fd = _open_shared(ticket, flags)
  _try_lock(ticket_fd)  # a fresh, unique file: always succeeds
  fd = _open_shared(path, flags)
  try:
    while True:
      ahead = _older_live_ticket(path, ticket)
      if ahead is None and _try_lock(fd):
        break
      holder = _read_holder(path)
      if time.monotonic() >= deadline:
        raise LockTimeout(f"[eda-lock] gave up after {timeout_s:.0f} s waiting for {path}, held by {describe(holder)}")
      if not announced:
        log(f"[eda-lock] waiting for {path}, held by {describe(holder)}"
            + (f"; queued behind {ahead.name}" if ahead is not None else ""))
        announced = True
      time.sleep(min(poll_s, max(0.05, deadline - time.monotonic())))
    _release_ticket(ticket, ticket_fd)
    ticket_fd = None
    _write_record(fd, {**info, "state": "waiting for free memory"})

    log(f"[eda-lock] acquired {path} for '{purpose}'")
    previous = os.environ.get(TOKEN_ENV)
    os.environ[TOKEN_ENV] = token
    try:
      _wait_for_memory(kind, poll_s, log, free_mb)
      _write_record(fd, {**info, "state": "running"})
      yield path
    finally:
      if previous is None:
        os.environ.pop(TOKEN_ENV, None)
      else:
        os.environ[TOKEN_ENV] = previous
      _write_record(fd, None)
      _unlock(fd)
      log(f"[eda-lock] released {path}")
  finally:
    os.close(fd)
    if ticket_fd is not None:
      _release_ticket(ticket, ticket_fd)


def _release_ticket(ticket: Path, fd: int) -> None:
  try:
    _unlock(fd)
  except OSError:
    pass
  os.close(fd)
  try:
    ticket.unlink()
  except OSError:
    pass


# Command line ----------------------------------------------------------------

# Exit codes of the command line besides the exit code of the command, as GNU
# env and timeout use them.
EXIT_LOCK_FAILED = 125  # wrong options or settings, lock or memory timeout: the command did not run
EXIT_CANNOT_RUN = 126  # the command exists but cannot be started
EXIT_NOT_FOUND = 127  # no such command

# Signals that a terminal sends to its whole foreground process group: the
# command receives them itself, so the command line only ignores them while
# the command runs. The others are relayed to the command.
_TERMINAL_SIGNALS = ("SIGINT", "SIGQUIT", "SIGBREAK")
_RELAYED_SIGNALS = ("SIGTERM", "SIGHUP")


class _Interrupted(Exception):
  """A signal arrived before the command started."""

  def __init__(self, signum: int) -> None:
    super().__init__(signum)
    self.signum = signum


def exit_code(returncode: int) -> int:
  """Exit code of a command as a shell reports it: 128 + N when signal N stopped it."""
  return 128 - returncode if returncode < 0 else returncode


def run(
  command: list[str],
  kind: str = "synthesis",
  timeout_s: float | None = None,
  purpose: str | None = None,
  lock_path: Path | None = None,
  log: Callable[[str], None] = print,
  free_mb: Callable[[], float | None] = free_physical_mb,
) -> int:
  """Run a command holding the lock (heavy_tool_lock) and return its exit code.

  The lock is released only after the command has exited, whatever happens:
  an error, a signal, an interruption. A signal before the command starts
  stops the wait and returns 128 + N; the command is then not started."""
  signal_names = {getattr(signal, name): name for name in _TERMINAL_SIGNALS + _RELAYED_SIGNALS if hasattr(signal, name)}
  relayed = {getattr(signal, name) for name in _RELAYED_SIGNALS if hasattr(signal, name)}
  state: dict = {"child": None, "starting": False, "pending": []}

  def handler(signum: int, frame: object) -> None:
    child = state["child"]
    if child is not None:
      if signum in relayed:
        child.send_signal(signum)
      return
    if state["starting"]:
      state["pending"].append(signum)  # sent to the command once it exists
      return
    raise _Interrupted(signum)

  previous = {}
  for signum in signal_names:
    if signal.getsignal(signum) is signal.SIG_IGN:
      continue  # an ignored signal stays ignored (nohup), for the command too
    previous[signum] = signal.signal(signum, handler)
  try:
    with heavy_tool_lock(purpose or Path(command[0]).name, timeout_s=timeout_s, lock_path=lock_path, log=log,
                         kind=kind, free_mb=free_mb):
      state["starting"] = True
      try:
        child = subprocess.Popen(command)
      except FileNotFoundError as exc:
        log(f"[eda-lock] command not found: {command[0]} ({exc})")
        return EXIT_NOT_FOUND
      except OSError as exc:
        log(f"[eda-lock] cannot run {command[0]}: {exc}")
        return EXIT_CANNOT_RUN
      state["child"] = child
      for signum in state["pending"]:
        child.send_signal(signum)
      returncode = child.wait()
    return exit_code(returncode)
  except _Interrupted as exc:
    log(f"[eda-lock] {signal_names.get(exc.signum, exc.signum)} before the command started: not started")
    return 128 + exc.signum
  except (LockTimeout, MemoryTimeout, LockConfigError) as exc:
    log(str(exc))
    return EXIT_LOCK_FAILED
  finally:
    for signum, old in previous.items():
      signal.signal(signum, old)


class _UsageError(Exception):
  pass


class _Parser(argparse.ArgumentParser):
  def error(self, message: str) -> NoReturn:
    raise _UsageError(message)


def main(argv: list[str] | None = None) -> int:
  """python3 eda_lock.py [--kind KIND] [--timeout SECONDS] [--purpose TEXT] -- COMMAND [ARG...]"""
  argv = sys.argv[1:] if argv is None else argv
  parser = _Parser(
    prog="eda_lock.py",
    usage="%(prog)s [--kind {" + ",".join(MIN_FREE_MB) + "}] [--timeout SECONDS] [--purpose TEXT] -- COMMAND [ARG...]",
    description="Run COMMAND holding the machine-wide EDA lock: wait for the lock, then for free memory, "
                "run the command, release the lock when it exits. Exit code: that of the command (128 + N "
                f"if signal N stopped it); {EXIT_LOCK_FAILED} if the command did not run (wrong options, a "
                "malformed setting in the environment, lock or memory timeout), "
                f"{EXIT_CANNOT_RUN} if it cannot be started, {EXIT_NOT_FOUND} if it does not exist. Lock "
                f"directory: LM_EDA_LOCK_DIR. Timeouts and thresholds ({TIMEOUT_ENV}, {MEMORY_TIMEOUT_ENV}, "
                + ", ".join(env for env, _ in MIN_FREE_MB.values()) + ") must be finite numbers, not negative.")
  parser.add_argument("--kind", choices=list(MIN_FREE_MB), default="synthesis",
                      help="kind of run, selects the free-memory threshold (default: synthesis)")
  parser.add_argument("--timeout", type=float, metavar="SECONDS",
                      help=f"maximum wait for the lock (default: {TIMEOUT_ENV}, else {DEFAULT_TIMEOUT_S:.0f} s)")
  parser.add_argument("--purpose", metavar="TEXT", help="shown to waiting runs (default: the command name)")
  split = argv.index("--") if "--" in argv else len(argv)
  options, command = argv[:split], argv[split + 1:]

  def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)

  try:
    args = parser.parse_args(options)
    if split == len(argv) or not command:
      raise _UsageError("give the command after --")
    if args.timeout is not None and not (math.isfinite(args.timeout) and args.timeout >= 0):
      raise _UsageError(f"--timeout {args.timeout}: a finite number of seconds, not negative, is required")
  except _UsageError as exc:
    parser.print_usage(sys.stderr)
    log(f"eda_lock.py: {exc}")
    return EXIT_LOCK_FAILED
  return run(command, kind=args.kind, timeout_s=args.timeout, purpose=args.purpose, log=log)


if __name__ == "__main__":
  sys.exit(main())
