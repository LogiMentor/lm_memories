# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Expected results per tool version (tools/vendor_synth/expected/<vendor>/<version>.toml)."""

from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from tools.campaign import pilot, rerun, resume
from tools.tests.test_campaign_summary import _campaign
from tools.vendor_synth import check


class LoaderTest(unittest.TestCase):
  def test_every_file_names_its_version(self) -> None:
    files = sorted(check.EXPECTED_DIR.glob("*/*.toml"))
    self.assertEqual([f"{p.parent.name}/{p.stem}" for p in files],
                     ["amd/2023.1", "amd/2024.1", "intel/23.1std", "intel/25.1std"])
    self.assertEqual(list(check.EXPECTED_DIR.glob("*.toml")), [], "no expected results outside a version")
    for path in files:
      with path.open("rb") as handle:
        self.assertEqual(tomllib.load(handle)["meta"]["tool_version"], path.stem, path)

  def test_key_of_a_version(self) -> None:
    self.assertEqual(check.expected_key("amd", "2024.1"), "2024.1")
    self.assertEqual(check.expected_key("intel", "25.1std.0 Build 1129"), "25.1std")
    self.assertEqual(check.expected_key("altera", "23.1std.0 Build 991"), "23.1std")

  def test_each_version_loads_its_own_file(self) -> None:
    self.assertEqual(check.load_expected("amd", "2023.1")["tool_version"], "2023.1")
    self.assertEqual(check.load_expected("xilinx", "2024.1")["tool_version"], "2024.1")
    self.assertEqual(check.load_expected("intel", "25.1std.0 Build 1129")["tool_version"], "25.1std")

  def test_no_fallback_to_another_version(self) -> None:
    for vendor, version, message in (("amd", "2025.2", "expected/amd/2025.2.toml does not exist"),
                                     ("intel", "24.1std.0 Build 1077", "expected/intel/24.1std.toml does not exist"),
                                     ("amd", "unknown", "tool version unknown"),
                                     ("amd", None, "tool version unknown"),
                                     ("intel", "mixed: 23.1std.0 Build 991, 25.1std.0 Build 1129", "mixed")):
      with self.subTest(vendor=vendor, version=version), self.assertRaisesRegex(check.ExpectedError, message):
        check.load_expected(vendor, version)

  def test_a_file_of_another_version_is_refused(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      (Path(tmp) / "amd").mkdir()
      (Path(tmp) / "amd" / "2024.2.toml").write_text('[meta]\ntool_version = "2024.1"\n', encoding="utf-8")
      with mock.patch.object(check, "EXPECTED_DIR", Path(tmp)), \
           self.assertRaisesRegex(check.ExpectedError, r"\[meta\] tool_version is '2024.1', not '2024.2'"):
        check.load_expected("amd", "2024.2")

  def test_a_vendor_without_expected_results_has_none(self) -> None:
    self.assertEqual(check.load_expected("lattice", None), {"defaults": {}, "expect": []})


class CampaignToolsetTest(unittest.TestCase):
  def test_rerun_and_resume_pass_the_toolset(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["tool-error"])
      command = rerun.tool_errors(root)[0]["command"]
      self.assertEqual(Path(command[command.index("--toolset") + 1]), root / "logs" / "toolset.json")
      seen = []

      def fake_run(cmd, cwd):
        seen.append(cmd)
        return mock.Mock(returncode=0)
      resume.reevaluate(root, "vivado", run=fake_run)
      self.assertEqual(Path(seen[0][seen[0].index("--toolset") + 1]), root / "logs" / "toolset.json")

  def test_pilot_passes_the_toolset_to_vendor_synth(self) -> None:
    seen = []
    with mock.patch.object(pilot, "vendor_synth", side_effect=lambda args, cwd=None: seen.append(args) or 0), \
         mock.patch.object(pilot, "single_result", side_effect=RuntimeError("stop")):
      with self.assertRaises(RuntimeError):
        pilot.launcher_attempt("pilot-artix7", "r_w_32x1024", Path("/c/logs/toolset.json"))(Path("unused"))
    self.assertEqual(seen[0][-2:], ["--toolset", str(Path("/c/logs/toolset.json"))])


if __name__ == "__main__":
  unittest.main()
