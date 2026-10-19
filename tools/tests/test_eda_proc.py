# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for tools/eda_proc.py: a timeout stops the whole tool, not only its launcher."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tools.eda_proc import run_tool

# A launcher that starts the "tool" as its own child and waits for it, as
# cmd.exe does with vivado.bat. The tool writes its process id and sleeps.
LAUNCHER = """
import subprocess, sys
tool = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(120)"
subprocess.run([sys.executable, "-c", tool, sys.argv[1]])
"""


def alive(pid: int) -> bool:
  if os.name == "nt":
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                         capture_output=True, text=True).stdout
    return f'"{pid}"' in out
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return False
  # A zombie still answers to kill(0); it has stopped running.
  try:
    with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
      return handle.read().split()[2] != "Z"
  except OSError:
    return True


class RunToolTest(unittest.TestCase):
  def test_exit_code(self) -> None:
    self.assertEqual(run_tool([sys.executable, "-c", "raise SystemExit(3)"]), 3)

  def test_timeout_stops_the_tool_started_by_the_launcher(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      pid_file = Path(tmp) / "tool.pid"
      start = time.monotonic()
      with self.assertRaises(subprocess.TimeoutExpired):
        run_tool([sys.executable, "-c", LAUNCHER, str(pid_file)], timeout=5)
      self.assertLess(time.monotonic() - start, 60)
      self.assertTrue(pid_file.is_file(), "the tool did not start")
      pid = int(pid_file.read_text())
      deadline = time.monotonic() + 10
      while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
      self.assertFalse(alive(pid), f"tool process {pid} still running after the timeout")


if __name__ == "__main__":
  unittest.main()
