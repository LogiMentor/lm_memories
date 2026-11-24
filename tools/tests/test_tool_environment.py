# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""The campaign finds its Python and stops early when a tool does not start
(tools/campaign/run_amd.sh, tools/campaign/toolset.py preflight)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.campaign import toolset

ROOT_DIR = Path(__file__).resolve().parents[2]
COMMANDS = {"xvhdl": ["xvhdl", "--version"], "vsim": ["vsim", "-c", "-do", "quit -f"]}
XVHDL_OK = (0, "Vivado Simulator v2024.1\nCopyright 1986-2024 Xilinx, Inc. All Rights Reserved.\n")
VSIM_OK = (0, "Reading pref.tcl\n\n# 2025.2\n\n# quit -f\n")
# As measured on Linux without the environment scripts of the installations.
XVHDL_NO_LIBRARY = (127, ".../bin/unwrapped/lnx64.o/xvhdl: error while loading shared libraries: libtinfo.so.5: "
                         "cannot open shared object file: No such file or directory\n")
VSIM_NO_LICENCE = (4, "Unable to find the license file.  It appears that your license file environment variable "
                      "(SALT_LICENSE_SERVER) is not set correctly.\nUnable to checkout a license.  Vsim is closing.\n"
                      "** Error: Invalid license environment. Application closing.\n")


def fake(results: dict[str, tuple[int, str]]):
  seen = []

  def run(command: list[str], cwd: Path) -> tuple[int, str]:
    seen.append((command[0], cwd))
    return results[command[0]]
  return run, seen


class PreflightTest(unittest.TestCase):
  def test_tools_that_start_pass(self) -> None:
    run, seen = fake({"xvhdl": XVHDL_OK, "vsim": VSIM_OK})
    self.assertEqual(toolset.preflight("2024.1", COMMANDS, run), [])
    # Both run in a scratch directory, not in the repository.
    self.assertTrue(all(cwd != ROOT_DIR and cwd.name.startswith("lm_preflight_") for _, cwd in seen))

  def test_missing_library_and_licence_stop(self) -> None:
    run, _ = fake({"xvhdl": XVHDL_NO_LIBRARY, "vsim": VSIM_NO_LICENCE})
    problems = toolset.preflight("2024.1", COMMANDS, run)
    self.assertEqual(len(problems), 2, problems)
    self.assertIn("xvhdl does not start (exit code 127", problems[0])
    self.assertIn("libtinfo.so.5", problems[0])
    self.assertIn("(its libraries)", problems[0])
    self.assertIn("vsim does not start (exit code 4", problems[1])
    self.assertIn("(a licence)", problems[1])

  def test_xvhdl_of_another_vivado_stops(self) -> None:
    run, _ = fake({"xvhdl": (0, "Vivado Simulator v2023.1\n"), "vsim": VSIM_OK})
    self.assertEqual(toolset.preflight("2024.1", COMMANDS, run),
                     ["xvhdl is not of Vivado 2024.1: Vivado Simulator v2023.1"])

  def test_the_campaign_runs_the_preflight(self) -> None:
    script = (ROOT_DIR / "tools" / "campaign" / "run_amd.sh").read_text(encoding="utf-8")
    preflight = script.index("tools.campaign.toolset preflight")
    self.assertLess(preflight, script.index("tools.campaign.toolset detect"))
    self.assertLess(preflight, script.index("LM_CAMPAIGN_RESUME: refused"))


@unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "POSIX shell test")
class CampaignPythonTest(unittest.TestCase):
  def _run(self, tools: dict[str, str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """run_amd.sh with only these executables (name -> shell body) and dirname on PATH."""
    with tempfile.TemporaryDirectory() as tmp:
      bin_dir = Path(tmp)
      os.symlink(shutil.which("dirname"), bin_dir / "dirname")
      for name, body in tools.items():
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (bin_dir / name).chmod(0o755)
      return subprocess.run([shutil.which("bash"), str(ROOT_DIR / "tools" / "campaign" / "run_amd.sh")],
                            env={"PATH": str(bin_dir), "HOME": tmp, **(env or {})}, capture_output=True, text=True,
                            timeout=60)

  # A Python that passes the version check and has no VUnit: the script stops there and names it.
  NO_VUNIT = 'case "$2" in *vunit*) exit 1;; *) exit 0;; esac'

  def test_python3_when_there_is_no_python(self) -> None:
    done = self._run({"python3": self.NO_VUNIT})
    self.assertEqual(done.returncode, 2, done.stderr)
    self.assertIn("VUnit is not installed for python3", done.stderr)

  def test_python_first_and_python_variable_wins(self) -> None:
    done = self._run({"python": self.NO_VUNIT, "python3": "exit 1"})
    self.assertIn("VUnit is not installed for python ", done.stderr)
    done = self._run({"python": "exit 1", "mypython": self.NO_VUNIT}, env={"PYTHON": "mypython"})
    self.assertIn("VUnit is not installed for mypython", done.stderr)


if __name__ == "__main__":
  unittest.main()
