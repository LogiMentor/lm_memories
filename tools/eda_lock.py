# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Machine-wide lock for heavy EDA runs (synthesis, large simulations).

Every lm_memories script that starts a heavy tool takes this lock first, so
sessions on the same machine queue up instead of running in parallel. The
lock is a file with a fixed name in the system temporary directory, not tied
to any repository, so other projects can use the same file to queue with us.

- The lock file holds JSON describing the holder (pid, host, user, purpose,
  command, start time); a waiting process prints it.
- A lock left by a process that no longer exists on this host is stale and
  is removed. No process is ever terminated.
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


class LockTimeout(TimeoutError):
  """Raised when the lock is still held by someone else after the timeout."""


def default_lock_path() -> Path:
  return Path(tempfile.gettempdir()) / LOCK_NAME


def _pid_alive(pid: int) -> bool:
  """True if a process with this pid exists on this host. Never signals it."""
  if pid <= 0:
    return False
  if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
      return ctypes.get_last_error() == error_access_denied
    try:
      code = wintypes.DWORD()
      if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
        return True
      return code.value == still_active
    finally:
      kernel32.CloseHandle(handle)
  try:
    os.kill(pid, 0)  # POSIX: signal 0 only checks existence.
  except ProcessLookupError:
    return False
  except PermissionError:
    return True
  return True


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
  while True:
    try:
      fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
      holder = _read_holder(path)
      if holder and holder.get("host") == info["host"] and not _pid_alive(int(holder.get("pid", -1))):
        log(f"[eda-lock] removing stale lock {path}: {describe(holder)} (process no longer exists)")
        try:
          path.unlink()
        except FileNotFoundError:
          pass
        continue
      if time.monotonic() >= deadline:
        raise LockTimeout(f"[eda-lock] gave up after {timeout_s:.0f} s waiting for {path}, held by {describe(holder)}")
      if not announced:
        log(f"[eda-lock] waiting for {path}, held by {describe(holder)}")
        announced = True
      time.sleep(min(poll_s, max(0.05, deadline - time.monotonic())))
      continue
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
      json.dump(info, handle)
    break

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
    holder = _read_holder(path)
    if holder and holder.get("token") == token:
      try:
        path.unlink()
      except FileNotFoundError:
        pass
    log(f"[eda-lock] released {path}")
