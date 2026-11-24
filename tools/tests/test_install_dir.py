# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""The tool installations come from the environment (LM_VIVADO_DIR,
LM_QUARTUS_DIR), never from a directory written in a checked file."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from tools.campaign import pilot
from tools.vendor_synth.cli import main as vendor_synth

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIGS = {
  "tools/vendor_synth/vivado_check.toml": "LM_VIVADO_DIR",
  "tools/vendor_synth/fallback_check.toml": "LM_VIVADO_DIR",
  "tools/vendor_synth/quartus_check.toml": "LM_QUARTUS_DIR",
  "tools/campaign/pilot.toml": "LM_VIVADO_DIR",
}
# A Windows (C:/...) or POSIX installation directory written as a default.
_DEFAULT_DIR_RE = re.compile(r"[A-Za-z]:/(?:Xilinx|intelFPGA|questasim|msys64)|/opt/|/c/msys64", re.IGNORECASE)


def _env_without(*names: str) -> dict[str, str]:
  return {k: v for k, v in os.environ.items() if k not in names}


class InstallDirEnvTest(unittest.TestCase):
  def _config(self, root: Path, **job: str) -> Path:
    config = root / "config.json"
    config.write_text(json.dumps({"work_root": str(root / "work"), "jobs": [{
      "name": "vivado_env", "enabled": True, "tool": "vivado", "vendor": "amd", "family": "artix7",
      "part": "xc7a35tfgg484-2", "cases": ["r_w_32x1024"], **job}]}), encoding="utf-8")
    return config

  def test_install_dir_env_names_the_installation(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      install = root / "Vivado" / "2024.1"
      (install / "bin").mkdir(parents=True)
      executable = install / "bin" / ("vivado.bat" if os.name == "nt" else "vivado")
      executable.write_text("", encoding="utf-8")
      config = self._config(root, install_dir_env="LM_TEST_VIVADO_DIR")
      with mock.patch.dict(os.environ, {"LM_TEST_VIVADO_DIR": str(install)}), \
           contextlib.redirect_stdout(io.StringIO()):
        self.assertEqual(vendor_synth(["--config", str(config), "--dry-run"]), 0)
      summary = json.loads((root / "work" / "summary.json").read_text(encoding="utf-8"))
      command = " ".join(summary["results"][0]["command"])
      launcher = Path(summary["results"][0]["command"][-1])
      self.assertIn(str(executable), command + launcher.read_text(encoding="utf-8"))

  def test_unset_variable_stops_the_run(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      config = self._config(Path(tmp), install_dir_env="LM_TEST_VIVADO_DIR")
      for value in (None, " "):
        env = _env_without("LM_TEST_VIVADO_DIR")
        if value is not None:
          env["LM_TEST_VIVADO_DIR"] = value
        with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(SystemExit) as stop:
          vendor_synth(["--config", str(config), "--dry-run"])
        self.assertIn("LM_TEST_VIVADO_DIR is not set", str(stop.exception))

  def test_install_dir_and_install_dir_env_together_are_refused(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      config = self._config(Path(tmp), install_dir=tmp, install_dir_env="LM_TEST_VIVADO_DIR")
      with mock.patch.dict(os.environ, {"LM_TEST_VIVADO_DIR": tmp}), self.assertRaises(SystemExit) as stop:
        vendor_synth(["--config", str(config), "--dry-run"])
      self.assertIn("not both", str(stop.exception))


class CheckedConfigurationTest(unittest.TestCase):
  def test_every_job_reads_its_installation_from_the_environment(self) -> None:
    for name, variable in CONFIGS.items():
      with (ROOT_DIR / name).open("rb") as handle:
        jobs = tomllib.load(handle)["jobs"]
      for job in jobs:
        with self.subTest(config=name, job=job["name"]):
          self.assertNotIn("install_dir", job)
          self.assertEqual(job.get("install_dir_env"), variable)

  def test_no_default_installation_in_the_campaign_scripts(self) -> None:
    for name in ("tools/campaign/run_amd.sh", "tools/campaign/pilot.py", "sim/scripts/run_postsynth.py",
                 *CONFIGS):
      text = (ROOT_DIR / name).read_text(encoding="utf-8")
      with self.subTest(file=name):
        self.assertEqual(_DEFAULT_DIR_RE.findall(text), [])


class PilotAndPostsynthTest(unittest.TestCase):
  def test_pilot_needs_lm_vivado_dir(self) -> None:
    with mock.patch.dict(os.environ, _env_without("LM_VIVADO_DIR"), clear=True), \
         self.assertRaises(SystemExit) as stop:
      pilot.vivado_dir()
    self.assertIn("LM_VIVADO_DIR", str(stop.exception))
    with mock.patch.dict(os.environ, {"LM_VIVADO_DIR": "/tools/Vivado/2024.1"}):
      self.assertEqual(pilot.vivado_dir(), Path("/tools/Vivado/2024.1"))

  def test_postsynth_needs_lm_vivado_dir(self) -> None:
    spec = importlib.util.spec_from_file_location("run_postsynth", ROOT_DIR / "sim" / "scripts" / "run_postsynth.py")
    ps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ps)
    with mock.patch.dict(os.environ, _env_without("LM_VIVADO_DIR"), clear=True), \
         contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as stop, \
         mock.patch.object(ps, "run_ghdl", side_effect=AssertionError("a tool ran")), \
         mock.patch.object(ps, "run_xsim", side_effect=AssertionError("a tool ran")):
      ps.main(["--work-root", str(ROOT_DIR / "nonexistent_lm_test_root")])
    self.assertEqual(stop.exception.code, 2)
    self.assertIn("LM_VIVADO_DIR", err.getvalue())


if __name__ == "__main__":
  unittest.main()
