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
- Timeout: argument, or LM_EDA_LOCK_TIMEOUT (seconds), default 4 hours.
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


class LockTimeout(TimeoutError):
  """Raised when the lock is still held by someone else after the timeout."""


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
  return (f"pid {holder.get('pid')} on {holder.get('host')} (user {holder.get('user')}), "
          f"purpose '{holder.get('purpose')}', since {holder.get('started')}, "
          f"command: {holder.get('command')}")


@contextmanager
def heavy_tool_lock(
  purpose: str,
  timeout_s: float | None = None,
  poll_s: float = 5.0,
  lock_path: Path | None = None,
  log: Callable[[str], None] = print,
) -> Iterator[Path]:
  """Hold the machine-wide lock for the duration of the block."""
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
  fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o666)
  try:
    while not _try_lock(fd):
      holder = _read_holder(path)
      if time.monotonic() >= deadline:
        raise LockTimeout(f"[eda-lock] gave up after {timeout_s:.0f} s waiting for {path}, held by {describe(holder)}")
      if not announced:
        log(f"[eda-lock] waiting for {path}, held by {describe(holder)}")
        announced = True
      time.sleep(min(poll_s, max(0.05, deadline - time.monotonic())))
    _write_record(fd, info)

    log(f"[eda-lock] acquired {path} for '{purpose}'")
    previous = os.environ.get(TOKEN_ENV)
    os.environ[TOKEN_ENV] = token
    try:
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
