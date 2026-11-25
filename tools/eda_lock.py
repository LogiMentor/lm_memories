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
                      [--kill-after SECONDS] [--purpose TEXT] -- COMMAND [ARG...]

It waits for the lock and then for free memory (--kind, default synthesis),
runs COMMAND, and releases the lock when COMMAND exits. Its exit code is
that of COMMAND (128 + N if signal N stopped it); 125 if COMMAND did not run
(wrong options, a malformed setting in the environment, a lock directory or
file that cannot be created or opened, lock or memory timeout), 126 if it
cannot be started, 127 if it does not exist; an error while releasing the
lock after COMMAND does not change its exit code. Messages go to stderr.
The lock is released only after COMMAND has exited: the signals a terminal
sends to its whole foreground process group (SIGINT, SIGQUIT; SIGBREAK on
Windows) reach COMMAND directly and the command line ignores them. SIGTERM
and SIGHUP are relayed to COMMAND and, on POSIX systems, to all its
descendants: the command line takes the process tree of COMMAND when the
signal arrives (from /proc, or from ps where there is no /proc), sends the
signal to every process in it, follows the processes they start meanwhile,
and sends SIGKILL to those still running --kill-after seconds later (default
30). It releases the lock only when none of them is left (after SIGKILL it
waits at most 10 s more, then names the survivors). The exit code stays
that of COMMAND, 128 + N if it stopped only at the SIGKILL that followed
signal N. A process is recognised by its id and start time, so a process
that reuses an id is never signalled. A signal before COMMAND starts stops
the wait, and COMMAND does not run. Limits:
- when COMMAND exits by itself, the processes it leaves in the background
  are not waited for: the lock is released when COMMAND exits. For example
  the Quartus programming tools start the JTAG server jtagd, which keeps
  running after them;
- a process that leaves the tree (setsid and a double fork, or a parent that
  exits before the signal arrives) is no longer a descendant and does not
  receive the signal;
- on Windows the signals reach COMMAND only;
- a command line killed with SIGKILL cannot do any of this: the operating
  system releases the lock and COMMAND goes on without it.

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
- A lock directory, lock file or ticket that cannot be created, opened,
  locked or written raises LockSetupError (an OSError) naming the path and
  the error; the command line then exits with 125 without starting the
  command. An error while releasing the lock after the command has run does
  not change the exit code of the command.
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


class LockSetupError(OSError):
  """Raised when the lock directory, the lock file or a ticket cannot be created, opened, locked or written
  before the block starts."""


@contextmanager
def _setup_step(action: str, target: Path) -> Iterator[None]:
  """Turn an OSError of one step of the lock setup into LockSetupError naming the step, the path and the error."""
  try:
    yield
  except OSError as exc:
    detail = exc.strerror or str(exc)
    if exc.filename is not None and os.fspath(exc.filename) != os.fspath(target):
      detail += f" ({os.fspath(exc.filename)})"
    raise LockSetupError(f"[eda-lock] cannot {action} {target}: {detail}") from exc


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
  with _setup_step("create the lock directory", path.parent):
    path.parent.mkdir(parents=True, exist_ok=True)
  with _setup_step("create the ticket", ticket):
    ticket_fd = _open_shared(ticket, flags)
  fd = None
  try:
    with _setup_step("lock the ticket", ticket):
      _try_lock(ticket_fd)  # a fresh, unique file: always succeeds
    with _setup_step("open the lock file", path):
      fd = _open_shared(path, flags)
    while True:
      ahead = _older_live_ticket(path, ticket)
      with _setup_step("lock", path):
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
    with _setup_step("write the holder record to", path):
      _write_record(fd, {**info, "state": "waiting for free memory"})

    log(f"[eda-lock] acquired {path} for '{purpose}'")
    previous = os.environ.get(TOKEN_ENV)
    os.environ[TOKEN_ENV] = token
    try:
      _wait_for_memory(kind, poll_s, log, free_mb)
      with _setup_step("write the holder record to", path):
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
    if fd is not None:
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
EXIT_LOCK_FAILED = 125  # wrong options or settings, lock error or timeout, memory timeout: the command did not run
EXIT_CANNOT_RUN = 126  # the command exists but cannot be started
EXIT_NOT_FOUND = 127  # no such command

# Signals that a terminal sends to its whole foreground process group: the
# command receives them itself, so the command line only ignores them while
# the command runs. The others are relayed to the command.
_TERMINAL_SIGNALS = ("SIGINT", "SIGQUIT", "SIGBREAK")
_RELAYED_SIGNALS = ("SIGTERM", "SIGHUP")


# After SIGTERM or SIGHUP, the time the command and its descendants have to
# exit before the survivors receive SIGKILL (--kill-after), and the time the
# command line then waits for them to disappear before it releases the lock
# anyway, reporting them.
DEFAULT_KILL_AFTER_S = 30.0
_KILL_WAIT_S = 10.0
_STOP_POLL_S = 0.1


def _process_table(proc: bool | None = None) -> dict[int, tuple[int, str, str]]:
  """Process id -> (parent process id, start time, state) of every process (POSIX).

  From /proc where it exists (Linux), from ps elsewhere; empty if neither can
  be read. The start time tells a process from a later one that reuses its id."""
  table: dict[int, tuple[int, str, str]] = {}
  if proc is None:
    proc = os.path.isdir("/proc/self")
  if proc:
    for entry in os.scandir("/proc"):
      if not entry.name.isdigit():
        continue
      try:
        with open(f"/proc/{entry.name}/stat", "rb") as handle:
          stat = handle.read().decode("ascii", "replace")
        fields = stat[stat.rindex(")") + 2:].split()  # the name, in brackets, may contain spaces
        table[int(entry.name)] = (int(fields[1]), fields[19], fields[0])
      except (OSError, ValueError, IndexError):
        continue  # exited meanwhile
    return table
  try:
    listing = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "stat=", "-o", "lstart="],
                             capture_output=True, text=True, timeout=10).stdout
  except (OSError, subprocess.SubprocessError):
    return table
  for line in listing.splitlines():
    fields = line.split(None, 3)
    if len(fields) == 4 and fields[0].isdigit() and fields[1].isdigit():
      table[int(fields[0])] = (int(fields[1]), fields[3].strip(), fields[2][:1])
  return table


def _alive(pid: int, start: str, table: dict[int, tuple[int, str, str]]) -> bool:
  """True if the process pid that started at start still runs (not a zombie, id not reused)."""
  entry = table.get(pid)
  return entry is not None and entry[1] == start and entry[2] != "Z"


def _descendants(roots: list[int], table: dict[int, tuple[int, str, str]]) -> dict[int, str]:
  """Process id -> start time of every descendant of roots in table."""
  children: dict[int, list[int]] = {}
  for pid, (ppid, _, _) in table.items():
    children.setdefault(ppid, []).append(pid)
  found: dict[int, str] = {}
  todo = list(roots)
  while todo:
    for pid in children.get(todo.pop(), ()):
      if pid not in found:
        found[pid] = table[pid][1]
        todo.append(pid)
  return found


class _Relayed(Exception):
  """The first SIGTERM or SIGHUP while the command runs: stop waiting for it and stop its process tree."""


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
  kill_after_s: float = DEFAULT_KILL_AFTER_S,
) -> int:
  """Run a command holding the lock (heavy_tool_lock) and return its exit code.

  The lock is released only after the command has exited, whatever happens:
  an error, a signal, an interruption. A signal before the command starts
  stops the wait and returns 128 + N; the command is then not started.
  SIGTERM or SIGHUP while the command runs goes to the command and, on POSIX
  systems, to all its descendants; those still running kill_after_s seconds
  later receive SIGKILL, and only then is the lock released."""
  signal_names = {getattr(signal, name): name for name in _TERMINAL_SIGNALS + _RELAYED_SIGNALS if hasattr(signal, name)}
  relayed = {getattr(signal, name) for name in _RELAYED_SIGNALS if hasattr(signal, name)}
  # tree: process id -> start time of the descendants of the command, found
  # when a relayed signal arrives and while they stop (POSIX).
  state: dict = {"child": None, "starting": False, "pending": [], "signal": None, "tree": {}}

  def relay(signum: int) -> bool:
    """Send signum to the command and its descendants; True for the first relayed signal."""
    child = state["child"]
    if child.returncode is not None:
      return False  # the command has exited: its process id may be reused
    if os.name != "nt":
      table = _process_table()
      state["tree"].update(_descendants([child.pid], table))  # before the signal: the tree is still linked
      for pid, start in list(state["tree"].items()):
        if _alive(pid, start, table):
          try:
            os.kill(pid, signum)
          except OSError:
            pass  # exited meanwhile
    child.send_signal(signum)
    first = state["signal"] is None
    if first:
      state["signal"] = signum
      log(f"[eda-lock] {signal_names.get(signum, signum)} relayed to {command[0]}"
          + (f" and {len(state['tree'])} descendant process(es)" if state["tree"] else ""))
    return first

  def handler(signum: int, frame: object) -> None:
    child = state["child"]
    if child is not None:
      if signum in relayed and relay(signum):
        raise _Relayed()
      return
    if state["starting"]:
      state["pending"].append(signum)  # sent to the command once it exists
      return
    raise _Interrupted(signum)

  def stop_tree() -> int:
    """After a relayed signal: wait for the command and its descendants, SIGKILL the survivors; exit code."""
    child = state["child"]
    tree = state["tree"]
    killed = False
    deadline = time.monotonic() + kill_after_s
    while True:
      table = _process_table() if os.name != "nt" else {}
      running = child.poll() is None
      alive = [pid for pid, start in tree.items() if _alive(pid, start, table)]
      if table:
        tree.update(_descendants(([child.pid] if running else []) + alive, table))  # started meanwhile
        alive = [pid for pid, start in tree.items() if _alive(pid, start, table)]
      if not running and not alive:
        break
      if killed and time.monotonic() >= deadline:
        log(f"[eda-lock] still present {_KILL_WAIT_S:.0f} s after SIGKILL, lock released anyway: "
            + ", ".join(str(pid) for pid in alive + ([child.pid] if running else [])))
        break
      if not killed and time.monotonic() >= deadline:
        log(f"[eda-lock] SIGKILL to the processes still running {kill_after_s:g} s after "
            f"{signal_names.get(state['signal'], state['signal'])}: "
            + ", ".join(str(pid) for pid in ([child.pid] if running else []) + alive))
        for pid in alive:
          try:
            os.kill(pid, signal.SIGKILL)
          except OSError:
            pass
        if running:
          child.kill()
        killed = True
        deadline = time.monotonic() + _KILL_WAIT_S
      time.sleep(_STOP_POLL_S)
    returncode = child.wait() if child.returncode is None else child.returncode
    if killed and returncode == -signal.SIGKILL:
      return 128 + state["signal"]  # stopped because of the relayed signal, not of our SIGKILL
    return exit_code(returncode)

  outcome: int | None = None  # exit code, once the command has been started (or could not be)
  previous = {}
  for signum in signal_names:
    if signal.getsignal(signum) is signal.SIG_IGN:
      continue  # an ignored signal stays ignored (nohup), for the command too
    previous[signum] = signal.signal(signum, handler)
  try:
    kill_after_s = _number(kill_after_s, "kill_after", "seconds")
    with heavy_tool_lock(purpose or Path(command[0]).name, timeout_s=timeout_s, lock_path=lock_path, log=log,
                         kind=kind, free_mb=free_mb):
      state["starting"] = True
      try:
        child = subprocess.Popen(command)
      except FileNotFoundError as exc:
        log(f"[eda-lock] command not found: {command[0]} ({exc})")
        outcome = EXIT_NOT_FOUND
      except OSError as exc:
        log(f"[eda-lock] cannot run {command[0]}: {exc}")
        outcome = EXIT_CANNOT_RUN
      else:
        try:
          state["child"] = child  # from here a relayed signal may raise _Relayed
          for signum in state["pending"]:
            if signum in relayed:
              relay(signum)
            else:
              child.send_signal(signum)
          if state["signal"] is None:
            returncode = child.wait()
        except _Relayed:
          pass
        outcome = stop_tree() if state["signal"] is not None else exit_code(returncode)
    return outcome
  except _Interrupted as exc:
    log(f"[eda-lock] {signal_names.get(exc.signum, exc.signum)} before the command started: not started")
    return 128 + exc.signum
  except (LockTimeout, MemoryTimeout, LockConfigError) as exc:
    log(str(exc))
    return EXIT_LOCK_FAILED
  except OSError as exc:
    if outcome is not None:
      # Releasing the lock failed after the command: its exit code stands.
      log(f"[eda-lock] error while releasing the lock, exit code of the command kept: {exc}")
      return outcome
    log(str(exc) if isinstance(exc, LockSetupError) else f"[eda-lock] lock failed, command not started: {exc}")
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
  """python3 eda_lock.py [--kind KIND] [--timeout SECONDS] [--kill-after SECONDS] [--purpose TEXT] -- COMMAND ..."""
  argv = sys.argv[1:] if argv is None else argv
  parser = _Parser(
    prog="eda_lock.py",
    usage="%(prog)s [--kind {" + ",".join(MIN_FREE_MB) + "}] [--timeout SECONDS] [--kill-after SECONDS] "
          "[--purpose TEXT] -- COMMAND [ARG...]",
    description="Run COMMAND holding the machine-wide EDA lock: wait for the lock, then for free memory, "
                "run the command, release the lock when it exits. Exit code: that of the command (128 + N "
                f"if signal N stopped it); {EXIT_LOCK_FAILED} if the command did not run (wrong options, a "
                "malformed setting in the environment, a lock directory or file that cannot be created or "
                "opened, lock or memory timeout), "
                f"{EXIT_CANNOT_RUN} if it cannot be started, {EXIT_NOT_FOUND} if it does not exist. Lock "
                f"directory: LM_EDA_LOCK_DIR. Timeouts and thresholds ({TIMEOUT_ENV}, {MEMORY_TIMEOUT_ENV}, "
                + ", ".join(env for env, _ in MIN_FREE_MB.values()) + ") must be finite numbers, not negative.")
  parser.add_argument("--kind", choices=list(MIN_FREE_MB), default="synthesis",
                      help="kind of run, selects the free-memory threshold (default: synthesis)")
  parser.add_argument("--timeout", type=float, metavar="SECONDS",
                      help=f"maximum wait for the lock (default: {TIMEOUT_ENV}, else {DEFAULT_TIMEOUT_S:.0f} s)")
  parser.add_argument("--kill-after", type=float, metavar="SECONDS", default=DEFAULT_KILL_AFTER_S,
                      help="after SIGTERM or SIGHUP, time the command and its descendants have to exit before "
                           f"the survivors receive SIGKILL (default: {DEFAULT_KILL_AFTER_S:.0f} s)")
  parser.add_argument("--purpose", metavar="TEXT", help="shown to waiting runs (default: the command name)")
  split = argv.index("--") if "--" in argv else len(argv)
  options, command = argv[:split], argv[split + 1:]

  def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)

  try:
    args = parser.parse_args(options)
    if split == len(argv) or not command:
      raise _UsageError("give the command after --")
    for option, value in (("--timeout", args.timeout), ("--kill-after", args.kill_after)):
      if value is not None and not (math.isfinite(value) and value >= 0):
        raise _UsageError(f"{option} {value}: a finite number of seconds, not negative, is required")
  except _UsageError as exc:
    parser.print_usage(sys.stderr)
    log(f"eda_lock.py: {exc}")
    return EXIT_LOCK_FAILED
  return run(command, kind=args.kind, timeout_s=args.timeout, purpose=args.purpose, log=log,
             kill_after_s=args.kill_after)


if __name__ == "__main__":
  sys.exit(main())
