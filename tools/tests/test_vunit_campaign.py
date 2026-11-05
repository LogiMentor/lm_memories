# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""sim/scripts/run_vunit_campaign.py: verdict of a VUnit campaign from its xunit file."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("run_vunit_campaign", ROOT_DIR / "sim" / "scripts" / "run_vunit_campaign.py")
vc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vc)

COUNTERS = ("writes", "writes_masked", "writes_beyond_depth", "reads", "checked", "excluded_rw", "excluded_undefined",
            "excluded_beyond_depth", "collisions_rw", "collisions_ww", "init_reads", "old_data_reads", "hold_checks",
            "undefined_output_checks", "errors")
GOOD = " ".join(f"{k}={0 if k == 'errors' else 3}" for k in COUNTERS)
SIM = "ghdl_GHDL_6.0.0_(tarball)_Dunoon_edition_"


def xunit(lines_by_test: dict[str, str]) -> str:
  cases = "".join(
    f'<testcase classname="lib.tb_lm_mem_ram" name="{name}"><system-out>{text}</system-out></testcase>'
    for name, text in lines_by_test.items())
  return f'<?xml version="1.0"?><testsuite>{cases}</testsuite>'


def harness(counts: str, sim: str = SIM) -> str:
  return f"x.vhd:1:1:@1us:(report note): lm_mem_tb_harness lm_mem_ram_r_w [{sim}]: {counts}\n"


class VunitCampaignTest(unittest.TestCase):
  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    base = Path(self.tmp.name)
    self.env = mock.patch.dict(os.environ, {"LM_EDA_WORKDIR": str(base / "w"), "LM_EDA_ARCHIVE_DIR": str(base / "a"),
                                            "VUNIT_SIMULATOR": "ghdl"})
    self.env.start()

  def tearDown(self) -> None:
    self.env.stop()
    self.tmp.cleanup()

  def _campaign(self, lines_by_test: dict[str, str], name: str = "ghdl_6.0.0") -> tuple[int, dict]:
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
      if "--xunit-xml" not in cmd:
        return real_run(cmd, **kwargs)  # git, for the commit
      xml = Path(cmd[cmd.index("--xunit-xml") + 1])
      xml.write_text(xunit(lines_by_test), encoding="utf-8")
      return subprocess.CompletedProcess(cmd, 0)

    root = Path(os.environ["LM_EDA_WORKDIR"]) / "vu" / name
    if root.exists():
      import shutil
      shutil.rmtree(root)
    with mock.patch.object(vc.subprocess, "run", fake_run):
      code = vc.main([name])
    return code, json.loads((root / "results.json").read_text(encoding="utf-8"))

  def test_clean_campaign_passes(self) -> None:
    code, result = self._campaign({"r_w.32x1024.lat1.random": harness(GOOD)})
    self.assertEqual((code, result["problems"]), (0, []))

  def test_every_counter_is_always_required_and_errors_explicit(self) -> None:
    # Second review of PR #4: a harness line without counters, without
    # errors, with a malformed or repeated counter passed.
    cases = {
      "empty": "",
      "no errors": GOOD.replace(" errors=0", ""),
      "errors malformed": GOOD.replace("errors=0", "errors=x"),
      "counter missing": GOOD.replace("checked=3 ", ""),
      "repeated": GOOD + " errors=0",
      "errors not zero": GOOD.replace("errors=0", "errors=1"),
    }
    for label, counts in cases.items():
      code, result = self._campaign({"r_w.32x1024.lat1.random": harness(counts)})
      self.assertEqual(code, 1, label)
      self.assertTrue(any("r_w.32x1024.lat1.random" in p for p in result["problems"]), (label, result["problems"]))

  # The simulator: requested explicitly, and the one the harness reports
  # (second review of PR #4).

  def test_simulator_must_be_requested_explicitly(self) -> None:
    with mock.patch.dict(os.environ):
      del os.environ["VUNIT_SIMULATOR"]
      with self.assertRaises(SystemExit):
        self._campaign({"r_w.32x1024.lat1.random": harness(GOOD)})

  def test_effective_simulator_must_be_the_published_one(self) -> None:
    for label, sims in (("other simulator", ["modelsim_Questa_Sim-64_vsim_2021.1"]),
                        ("other version", ["ghdl_GHDL_5.0.1_(tarball)"]),
                        ("two simulators", [SIM, "ghdl_GHDL_5.0.1_(tarball)"])):
      tests = {f"t{k}.random": harness(GOOD, sim) for k, sim in enumerate(sims)}
      code, result = self._campaign(tests)
      self.assertEqual(code, 1, label)
      self.assertTrue(any("simulator" in p for p in result["problems"]), (label, result["problems"]))
    code, result = self._campaign({"r_w.random": harness(GOOD)})
    self.assertEqual((code, result["simulator"]), (0, SIM))

  def test_campaign_script_requests_ghdl_explicitly(self) -> None:
    script = (ROOT_DIR / "tools" / "campaign" / "run_amd.sh").read_text(encoding="utf-8")
    for step in ("vunit_ghdl", "mutation"):
      line = next(l for l in script.splitlines() if l.startswith(f"step {step} "))
      self.assertIn("VUNIT_SIMULATOR=ghdl ", line, step)


if __name__ == "__main__":
  unittest.main()
