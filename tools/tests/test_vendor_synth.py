# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from tools.vendor_synth.cli import main


class VendorSynthTests(unittest.TestCase):
  def test_dry_run_generates_vivado_project(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      config = root / "config.json"
      work = root / "work"
      config.write_text(
        json.dumps(
          {
            "work_root": str(work),
            "jobs": [
              {
                "name": "vivado_test",
                "enabled": True,
                "tool": "vivado",
                "vendor": "xilinx",
                "family": "artix7",
                "part": "xc7a35tcsg324-1",
                "executable": "vivado",
                "cases": ["infer_r_w_bram_32x1024"],
              }
            ],
          }
        ),
        encoding="utf-8",
      )

      result = main(["--config", str(config), "--dry-run"])

      self.assertEqual(result, 0)
      summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
      self.assertEqual(summary["results"][0]["status"], "dry-run")
      script = Path(summary["results"][0]["script"]).read_text(encoding="utf-8")
      top = Path(summary["results"][0]["work_dir"]) / (summary["results"][0]["top"] + ".vhd")
      self.assertIn("read_vhdl -vhdl2008", script)
      self.assertIn("lm_mem_ram_r_w_infer", top.read_text(encoding="utf-8"))

  def test_vivado_threads_one_uses_default_thread_setting(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      config = root / "config.json"
      work = root / "work"
      config.write_text(
        json.dumps(
          {
            "work_root": str(work),
            "jobs": [
              {
                "name": "vivado_threads_one",
                "enabled": True,
                "tool": "vivado",
                "vendor": "xilinx",
                "family": "artix7",
                "part": "xc7a35tcsg324-1",
                "executable": "vivado",
                "threads": 1,
                "cases": ["infer_r_w_bram_32x1024"],
              }
            ],
          }
        ),
        encoding="utf-8",
      )

      result = main(["--config", str(config), "--dry-run"])

      self.assertEqual(result, 0)
      summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
      script = Path(summary["results"][0]["script"]).read_text(encoding="utf-8")
      self.assertNotIn("set_param general.maxThreads 1", script)
      self.assertNotIn("set_param general.maxThreads", script)

  def test_vivado_threads_two_emits_thread_setting(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      config = root / "config.json"
      work = root / "work"
      config.write_text(
        json.dumps(
          {
            "work_root": str(work),
            "jobs": [
              {
                "name": "vivado_threads_two",
                "enabled": True,
                "tool": "vivado",
                "vendor": "xilinx",
                "family": "artix7",
                "part": "xc7a35tcsg324-1",
                "executable": "vivado",
                "threads": 2,
                "cases": ["infer_r_w_bram_32x1024"],
              }
            ],
          }
        ),
        encoding="utf-8",
      )

      result = main(["--config", str(config), "--dry-run"])

      self.assertEqual(result, 0)
      summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
      script = Path(summary["results"][0]["script"]).read_text(encoding="utf-8")
      self.assertIn("set_param general.maxThreads 2", script)

  def test_vivado_source_argument_uses_tcl_path(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      config = root / "config.json"
      work = root / "work"
      config.write_text(
        json.dumps(
          {
            "work_root": str(work),
            "jobs": [
              {
                "name": "vivado_source_path",
                "enabled": True,
                "tool": "vivado",
                "vendor": "xilinx",
                "family": "artix7",
                "part": "xc7a35tcsg324-1",
                "executable": "vivado",
                "cases": ["infer_r_w_bram_32x1024"],
              }
            ],
          }
        ),
        encoding="utf-8",
      )

      result = main(["--config", str(config), "--dry-run"])

      self.assertEqual(result, 0)
      summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
      command = summary["results"][0]["command"]
      source_arg = command[command.index("-source") + 1]
      self.assertEqual(source_arg, source_arg.replace("\\", "/"))

  @unittest.skipIf(sys.version_info < (3, 11), "TOML config support requires Python 3.11")
  def test_toml_target_config(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      config = root / "local.toml"
      work = root / "work"
      config.write_text(
        f"""
[run]
work_root = "{work.as_posix()}"

[[targets]]
name = "vivado_toml"
enabled = true
tool = "vivado"
vendor = "xilinx"
family = "zynq7"
part = "xc7z010clg400-1"
executable = "vivado"
cases = ["infer_r_w_bram_32x1024"]
""",
        encoding="utf-8",
      )

      result = main(["--config", str(config), "--dry-run"])

      self.assertEqual(result, 0)
      summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
      self.assertEqual(summary["results"][0]["job"], "vivado_toml")

  def test_windows_vivado_bat_does_not_require_setup_script(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      executable = root / "Vivado" / "2022.2" / "bin" / "vivado.bat"
      executable.parent.mkdir(parents=True)
      executable.write_text("@echo off\n", encoding="utf-8")

      config = root / "config.json"
      work = root / "work"
      config.write_text(
        json.dumps(
          {
            "work_root": str(work),
            "jobs": [
              {
                "name": "vivado_bat",
                "enabled": True,
                "tool": "vivado",
                "vendor": "xilinx",
                "family": "zynq7",
                "part": "xc7z010clg400-1",
                "executable": str(executable),
                "cases": ["infer_r_w_bram_32x1024"],
              }
            ],
          }
        ),
        encoding="utf-8",
      )

      result = main(["--config", str(config), "--dry-run"])

      self.assertEqual(result, 0)
      summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
      command = summary["results"][0]["command"]
      if os.name == "nt":
        launcher = Path(summary["results"][0]["work_dir"]) / "launch_tool.cmd"
        text = launcher.read_text(encoding="utf-8")
        self.assertEqual(command[-1], str(launcher))
        self.assertIn(str(executable), text)
        self.assertNotIn("settings64", text)
      else:
        self.assertEqual(command[0], str(executable))

  def test_install_dir_and_auto_setup_generate_launcher(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      install = root / "Vivado" / "2024.2"
      bin_dir = install / "bin"
      bin_dir.mkdir(parents=True)

      if os.name == "nt":
        (bin_dir / "vivado.bat").write_text("@echo off\n", encoding="utf-8")
        (install / "settings64.bat").write_text("@echo off\n", encoding="utf-8")
      else:
        (bin_dir / "vivado").write_text("#!/usr/bin/env sh\n", encoding="utf-8")
        (install / "settings64.sh").write_text("true\n", encoding="utf-8")

      config = root / "config.json"
      work = root / "work"
      config.write_text(
        json.dumps(
          {
            "work_root": str(work),
            "jobs": [
              {
                "name": "vivado_install",
                "enabled": True,
                "tool": "vivado",
                "vendor": "xilinx",
                "family": "artix7",
                "part": "xc7a35tcsg324-1",
                "install_dir": str(install),
                "setup_script": "auto",
                "cases": ["infer_r_w_bram_32x1024"],
              }
            ],
          }
        ),
        encoding="utf-8",
      )

      result = main(["--config", str(config), "--dry-run"])

      self.assertEqual(result, 0)
      summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
      command = summary["results"][0]["command"]
      work_dir = Path(summary["results"][0]["work_dir"])
      launcher = work_dir / ("launch_tool.cmd" if os.name == "nt" else "launch_tool.sh")
      self.assertTrue(launcher.exists())
      self.assertEqual(command[-1], str(launcher))

  def test_disabled_jobs_are_skipped(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      config = root / "config.json"
      config.write_text(
        json.dumps(
          {
            "jobs": [
              {
                "name": "vivado_disabled",
                "enabled": False,
                "tool": "vivado",
                "vendor": "xilinx",
                "family": "artix7",
                "part": "xc7a35tcsg324-1",
                "executable": "vivado",
              }
            ]
          }
        ),
        encoding="utf-8",
      )

      with self.assertRaises(SystemExit):
        main(["--config", str(config), "--dry-run"])

  def test_vivado_error_text_marks_failure_even_with_zero_return_code(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      if os.name == "nt":
        tool = root / "vivado_fake.bat"
        tool.write_text(
          "@echo off\n"
          "echo ERROR: [Common 17-69] Command failed: Vivado Synthesis failed\n"
          "exit /b 0\n",
          encoding="utf-8",
        )
      else:
        tool = root / "vivado_fake.sh"
        tool.write_text(
          "#!/usr/bin/env sh\n"
          "echo 'ERROR: [Common 17-69] Command failed: Vivado Synthesis failed'\n"
          "exit 0\n",
          encoding="utf-8",
        )
        tool.chmod(0o755)
      config = root / "config.json"
      work = root / "work"
      config.write_text(
        json.dumps(
          {
            "work_root": str(work),
            "jobs": [
              {
                "name": "vivado_error",
                "enabled": True,
                "tool": "vivado",
                "vendor": "xilinx",
                "family": "artix7",
                "part": "xc7a35tcsg324-1",
                "executable": str(tool),
                "cases": ["infer_r_w_bram_32x1024"],
              }
            ],
          }
        ),
        encoding="utf-8",
      )

      result = main(["--config", str(config)])

      summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
      self.assertEqual(result, 1)
      self.assertEqual(summary["results"][0]["status"], "fail")
      self.assertIn("Vivado Synthesis failed", summary["results"][0]["message"])


if __name__ == "__main__":
  unittest.main()
