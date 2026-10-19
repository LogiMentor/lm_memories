# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Run a heavy EDA tool as a child process, with a timeout that stops the whole tool.

On Windows the tools start through cmd.exe (vivado.bat, xsim.bat, a launcher
script). The timeout of subprocess.run terminates only that cmd.exe: the tool
keeps running, and a caller that holds the EDA lock (tools/eda_lock.py)
releases it while the tool still uses the machine. run_tool terminates the
process tree of the child it started and returns only after the child has
exited, so a caller releases the lock after the tool has stopped.

Only processes started here are terminated: the tree of our own child, while
that child is still running (its handle is open, so its process id cannot have
been reused). On Windows: taskkill /T /F on the child's process id. Elsewhere
the child starts in a new session and its process group is killed.

  from tools.eda_proc import run_tool
  code = run_tool(cmd, cwd=work, stdout=handle, stderr=subprocess.STDOUT, timeout=3600)
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any


def run_tool(
  cmd: Sequence[str],
  *,
  cwd: str | Path | None = None,
  env: dict[str, str] | None = None,
  stdout: int | IO[Any] | None = None,
  stderr: int | IO[Any] | None = None,
  timeout: float | None = None,
) -> int:
  """Run cmd and return its exit code.

  Raises subprocess.TimeoutExpired after the timeout, once the process tree
  of the child has been terminated. An interrupt (Ctrl+C) also terminates
  the tree before it propagates.
  """
  extra: dict[str, Any] = {} if os.name == "nt" else {"start_new_session": True}
  proc = subprocess.Popen(list(cmd), cwd=cwd, env=env, stdout=stdout, stderr=stderr, **extra)
  try:
    return proc.wait(timeout=timeout)
  except BaseException:
    terminate_tree(proc)
    raise


def terminate_tree(proc: subprocess.Popen) -> None:
  """Terminate the child proc and its descendants, and wait for the child to exit."""
  if proc.poll() is None:
    if os.name == "nt":
      subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
      try:
        os.killpg(proc.pid, signal.SIGKILL)
      except ProcessLookupError:
        pass
  proc.wait()
