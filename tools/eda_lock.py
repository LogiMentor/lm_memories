# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Machine-wide lock for heavy EDA runs (synthesis, large simulations).

Every lm_memories script that starts a heavy tool takes this lock first, so
sessions on the same machine queue up instead of running in parallel. The
lock is a file with a fixed name in the system temporary directory, not tied
to any repository, so other projects can use the same file to queue with us.

- The lock is an operating-system lock on that file (msvcrt.locking on
  Windows, flock elsewhere). The operating system releases it when the holder
  exits, also after a crash, so there is no stale-lock cleanup and no window
  where two waiters can both take it. No process is ever terminated.
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
  "synthesis" (Vivado, Quartus) LM_EDA_MIN_FREE_MB_SYNTH, default 8192 MB;
  "simulation" LM_EDA_MIN_FREE_MB_SIM, default 2048 MB. While it waits it
  keeps the lock, so the queue order is kept. Timeout: LM_EDA_MEMORY_TIMEOUT
  (seconds), default 4 hours. Where free memory cannot be read, it does not
  wait and says so.
- Child processes of the holder inherit LM_EDA_LOCK_TOKEN and do not wait
  for the lock their parent already holds.
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

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
  "synthesis": ("LM_EDA_MIN_FREE_MB_SYNTH", 8192.0),
  "simulation": ("LM_EDA_MIN_FREE_MB_SIM", 2048.0),
}
# While waiting for memory, repeat the status line this often.
MEMORY_REPORT_S = 600.0


class LockTimeout(TimeoutError):
  """Raised when the lock is still held by someone else after the timeout."""


class MemoryTimeout(TimeoutError):
  """Raised when the free memory stays below the threshold until the timeout."""


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
  return float(os.environ.get(env, default)), env


def _wait_for_memory(
  kind: str,
  poll_s: float,
  log: Callable[[str], None],
  free_mb: Callable[[], float | None],
) -> None:
  need, env = min_free_mb(kind)
  timeout_s = float(os.environ.get(MEMORY_TIMEOUT_ENV, DEFAULT_TIMEOUT_S))
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


def default_lock_path() -> Path:
  return Path(tempfile.gettempdir()) / LOCK_NAME


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
  min_free_mb(kind)  # reject an unknown kind before queuing
  path = lock_path or default_lock_path()
  inherited = os.environ.get(TOKEN_ENV)
  if inherited:
    holder = _read_holder(path)
    if holder and holder.get("token") == inherited:
      yield path
      return

  if timeout_s is None:
    timeout_s = float(os.environ.get(TIMEOUT_ENV, DEFAULT_TIMEOUT_S))
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
  ticket_fd = os.open(ticket, flags, 0o666)
  _try_lock(ticket_fd)  # a fresh, unique file: always succeeds
  fd = os.open(path, flags, 0o666)
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
