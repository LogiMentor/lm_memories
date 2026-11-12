# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""tools/campaign/toolset.py: tool versions detected once, campaign names derived from them."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import harness_counts
from tools.campaign import publish, rerun, summarize, toolset
from tools.tests import toolsets
from tools.tests.test_campaign_summary import _campaign
from tools.tests.test_publish import make_campaign

ROOT_DIR = Path(__file__).resolve().parents[2]
LAPTOP_NAMES = {
  "vivado": "vivado_2023.1_amd", "checks_vivado": "static_checks_vivado_2023.1",
  "checks_quartus": "static_checks_quartus_23.1", "postsynth": "postsynth_vivado_2023.1_amd",
  "vunit_ghdl": "ghdl_6.0.0", "vunit_questa": "questa_2021.1", "mutation": "ghdl_6.0.0",
}


class NamesTest(unittest.TestCase):
  def test_laptop_versions_give_the_names_of_today(self) -> None:
    self.assertEqual(toolsets.LAPTOP["campaigns"], LAPTOP_NAMES)
    # The published files of the laptop campaign carry these names.
    for step in ("vivado", "checks_vivado", "checks_quartus", "postsynth"):
      self.assertTrue((ROOT_DIR / "docs" / "results" / f"{LAPTOP_NAMES[step]}.json").is_file(), step)
    for step in ("vunit_ghdl", "vunit_questa"):
      self.assertTrue((ROOT_DIR / "docs" / "results" / f"vunit_{LAPTOP_NAMES[step]}.json").is_file(), step)

  def test_pcl01_versions_and_questa_edition(self) -> None:
    self.assertEqual(toolsets.PCL01["campaigns"], {
      "vivado": "vivado_2024.1_amd", "checks_vivado": "static_checks_vivado_2024.1",
      "checks_quartus": "static_checks_quartus_25.1", "postsynth": "postsynth_vivado_2024.1_amd",
      "vunit_ghdl": "ghdl_6.0.0", "vunit_questa": "questa_starter_2025.2", "mutation": "ghdl_6.0.0",
    })
    tools = toolsets.PCL01["tools"]
    self.assertEqual(tools["quartus"]["version"], "25.1std.0 Build 1129")
    self.assertEqual((tools["quartus"]["release"], tools["quartus"]["edition"]), ("25.1std", "Lite"))
    self.assertEqual((tools["questa"]["version"], tools["questa"]["edition"]), ("2025.2", "starter"))
    self.assertEqual(toolsets.LAPTOP["tools"]["questa"]["edition"], "")

  def test_a_version_that_cannot_be_read_stops(self) -> None:
    banners = {**toolsets.PCL01_BANNERS, "vivado": "ERROR: license\n"}
    with self.assertRaisesRegex(toolset.ToolsetError, "vivado: no Vivado version"):
      toolsets.make(banners)

  def test_detect_needs_the_installations_in_the_environment(self) -> None:
    env = {k: v for k, v in os.environ.items() if k != "LM_QUARTUS_DIR"}
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {**env, "LM_VIVADO_DIR": tmp}, clear=True):
      (Path(tmp) / "bin").mkdir()
      (Path(tmp) / "bin" / ("vivado.bat" if os.name == "nt" else "vivado")).write_text("")
      with self.assertRaisesRegex(toolset.ToolsetError, "LM_QUARTUS_DIR is not set"):
        toolset.tool_commands()

  def test_check_refuses_other_installed_versions(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      recorded = Path(tmp) / "toolset.json"
      recorded.write_text(json.dumps(toolsets.LAPTOP))
      with mock.patch.object(toolset, "detect", return_value=toolsets.PCL01), \
           contextlib.redirect_stderr(io.StringIO()) as err:
        self.assertEqual(toolset.main(["check", str(recorded)]), 1)
      self.assertIn("not those of", err.getvalue())
      with mock.patch.object(toolset, "detect", return_value=toolsets.make(toolsets.LAPTOP_BANNERS)):
        self.assertEqual(toolset.main(["check", str(recorded)]), 0)

  def test_name_of_a_step(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "toolset.json"
      path.write_text(json.dumps(toolsets.PCL01))
      with contextlib.redirect_stdout(io.StringIO()) as out:
        self.assertEqual(toolset.main(["name", str(path), "vunit_questa"]), 0)
      self.assertEqual(out.getvalue().strip(), "questa_starter_2025.2")
      with contextlib.redirect_stderr(io.StringIO()):
        self.assertEqual(toolset.main(["name", str(path), "nothing"]), 2)


class CampaignStepsTest(unittest.TestCase):
  def test_rerun_takes_the_campaign_names_of_the_toolset(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["tool-error"], ps_tool_error=True)
      toolsets.write(root, toolsets.PCL01)
      todo = rerun.tool_errors(root)
      campaigns = [i["command"][i["command"].index("--campaign") + 1] for i in todo]
      self.assertEqual(campaigns, ["vivado_2024.1_amd_rerun", "postsynth_vivado_2024.1_amd_rerun"])

  def test_rerun_without_toolset_stops(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["tool-error"])
      (root / "logs" / "toolset.json").unlink()
      with contextlib.redirect_stderr(io.StringIO()) as err:
        self.assertEqual(rerun.main([str(root)]), 2)
      self.assertIn("toolset.json", err.getvalue())

  def _summary(self, root: Path) -> tuple[int, dict]:
    with mock.patch.dict(os.environ, {"LM_EDA_ARCHIVE_DIR": str(root.parent / "archive")}), \
         contextlib.redirect_stdout(io.StringIO()):
      code = summarize.main([str(root)])
    return code, json.loads((root / "summary.json").read_text(encoding="utf-8"))

  def test_summary_reads_the_simulation_steps_of_the_toolset(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["pass"], rerun_items=[])
      toolsets.write(root, toolsets.PCL01)
      code, summary = self._summary(root)
      # The results are those of the laptop: other directories and versions.
      self.assertEqual(code, 1)
      self.assertEqual(summary["steps"]["vunit_questa"]["detail"], "no results.json")
      self.assertIn("tool version 2023.1, the campaign toolset has 2024.1", summary["steps"]["vivado"]["failures"][0])
      self.assertIn("tool version 23.1std.0 Build 991, the campaign toolset has 25.1std.0 Build 1129",
                    summary["steps"]["checks_quartus"]["failures"][0])
      self.assertEqual(summary["campaigns"]["vunit_questa"], "questa_starter_2025.2")
      self.assertEqual(summary["toolset"]["questa"], {"version": "2025.2", "edition": "starter"})

  def test_postsynth_with_another_vivado_fails(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["pass"], rerun_items=[])
      data = json.loads((root / "ps" / "results.json").read_text())
      data["parts"]["xc7a35tfgg484-2"]["synth"]["crw_crw_16x64_16_l11"]["version"] = "2024.1"
      (root / "ps" / "results.json").write_text(json.dumps(data))
      code, summary = self._summary(root)
      self.assertEqual(code, 1)
      self.assertIn("tool version 2024.1, the campaign toolset has 2023.1", summary["steps"]["postsynth"]["failures"][0])

  def test_a_pass_without_tool_version_fails(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["pass"], rerun_items=[])
      data = json.loads((root / "vs" / "summary.json").read_text())
      data["results"][0]["tool_version"] = "unknown"
      (root / "vs" / "summary.json").write_text(json.dumps(data))
      code, summary = self._summary(root)
      self.assertEqual(code, 1)
      self.assertIn("pass without a tool version", summary["steps"]["vivado"]["failures"][0])

  def test_summary_without_toolset_fails(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = _campaign(tmp, ["pass"], rerun_items=[])
      (root / "logs" / "toolset.json").unlink()
      code, summary = self._summary(root)
      self.assertEqual(code, 1)
      self.assertFalse(summary["ok"])
      self.assertIn("Tools: not recorded", (root / "summary.md").read_text(encoding="utf-8"))

  def test_publish_names_the_files_after_the_toolset(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      c = make_campaign(Path(tmp))
      toolsets.write(c.root, toolsets.PCL01)
      (c.root / "vu" / "ghdl_6.0.0").rename(c.root / "vu" / "questa_starter_2025.2")
      files = publish.collect(c.root, c.archives)
      self.assertIn("vivado_2024.1_amd.json", files)
      self.assertIn("static_checks_quartus_25.1.json", files)
      self.assertEqual(files["postsynth_vivado_2024.1_amd.json"]["campaign"], "postsynth_vivado_2024.1_amd")
      questa = files["vunit_questa_starter_2025.2.json"]
      self.assertEqual((questa["tool"], questa["edition"], questa["tool_version"]), ("questa", "starter", "2025.2"))
      self.assertIn("| questa starter 2025.2 |", publish.table(files))
      self.assertNotIn("vivado_2023.1_amd.json", files)


class HarnessEditionTest(unittest.TestCase):
  STARTER = "modelsim_Questa_Altera_Starter_FPGA_Edition-64_vsim_2025.2_Simulator_2025.05_May_31_2025"
  FULL = "modelsim_Questa_Sim-64_vsim_2021.1_Simulator_2021.01_Jan_19_2021"

  def test_campaign_parts(self) -> None:
    self.assertEqual(harness_counts.campaign_parts("questa_starter_2025.2"), ("questa", "starter", "2025.2"))
    self.assertEqual(harness_counts.campaign_parts("questa_2021.1"), ("questa", "", "2021.1"))
    self.assertEqual(harness_counts.campaign_simulator("questa_starter_2025.2"), ("modelsim", "2025.2"))
    with self.assertRaises(SystemExit):
      harness_counts.campaign_parts("questa_pro_2025.2")

  def test_the_harness_must_run_on_the_edition_of_the_name(self) -> None:
    check = harness_counts.simulator_problems
    self.assertEqual(check([self.STARTER], "modelsim", "2025.2", "questa_starter_2025.2"), [])
    self.assertEqual(check([self.FULL], "modelsim", "2021.1", "questa_2021.1"), [])
    self.assertIn("an edition (starter)", check([self.STARTER], "modelsim", "2025.2", "questa_2025.2")[0])
    full_2025 = self.FULL.replace("2021.1", "2025.2")
    self.assertIn("not on the starter edition", check([full_2025], "modelsim", "2025.2", "questa_starter_2025.2")[0])


if __name__ == "__main__":
  unittest.main()
