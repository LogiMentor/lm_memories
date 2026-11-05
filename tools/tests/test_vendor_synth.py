# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.eda_lock

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
      # The script records the outcome of synth_design in the status file of
      # the attempt and exits with 1 when it fails (PLAN 3.15).
      self.assertIn("if {[catch {synth_design -mode out_of_context", script)
      self.assertIn("  exit 1\n}", script)
      self.assertRegex(script, r"set handle \[open \{[^}]*/infer_r_w_bram_32x1024/reports/synth_status\.jsonl\} a\]")
      self.assertIn("lm_synth_status failed $result", script)
      self.assertIn("::env(LM_RUN_ID)", script)

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
        # .bat, not .cmd: in a .cmd script "set" resets ERRORLEVEL (PLAN 3.15).
        launcher = Path(summary["results"][0]["work_dir"]) / "launch_tool.bat"
        text = launcher.read_text(encoding="utf-8")
        self.assertEqual(command[-1], str(launcher))
        self.assertIn(str(executable), text)
        self.assertNotIn("settings64", text)
        # The exit code of a .bat tool reaches the caller only through "call".
        self.assertIn(f'call "{executable}"', text)
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
      launcher = work_dir / ("launch_tool.bat" if os.name == "nt" else "launch_tool.sh")
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

      # Private lock: a unit test must not queue behind real EDA runs.
      with mock.patch.object(tools.eda_lock, "default_lock_path", lambda: root / "test.lock"), \
           mock.patch.dict(os.environ, {"LM_EDA_MIN_FREE_MB_SYNTH": "0"}):
        result = main(["--config", str(config)])

      summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
      self.assertEqual(result, 1)
      self.assertEqual(summary["results"][0]["status"], "tool-error")
      self.assertIn("Vivado Synthesis failed", summary["results"][0]["message"])


@unittest.skipUnless(os.name == "nt", "cmd.exe only")
class WindowsLauncherExitCodeTest(unittest.TestCase):
  # Campaign of 2026-11-04: every Vivado run came back with 0 through
  # launch_tool.cmd. loader.bat (called by vivado.bat) runs "set" after the
  # tool exits, and in a script started as .cmd, "set" resets ERRORLEVEL.
  # The fake tool ends like loader.bat.
  TOOL = "@echo off\r\nsetlocal\r\ncmd /c exit 3\r\nendlocal\r\nset LM_FAKE_LOADER=\r\nexit /b %ERRORLEVEL%\r\n"

  def test_exit_code_of_a_bat_tool_reaches_the_caller(self) -> None:
    from tools.eda_proc import run_tool
    from tools.vendor_synth.cli import _launcher_command
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      tool = root / "vivado.bat"
      tool.write_text(self.TOOL, encoding="utf-8")
      command = _launcher_command({"tool": "vivado"}, [str(tool)], root)
      self.assertTrue(command[-1].endswith("launch_tool.bat"), command)
      self.assertEqual(run_tool(command, cwd=root), 3)
      # The same launcher as .cmd loses it: the trap this test guards against.
      as_cmd = root / "launch_tool.cmd"
      as_cmd.write_text(Path(command[-1]).read_text(encoding="utf-8"), encoding="utf-8")
      self.assertEqual(run_tool(["cmd", "/c", str(as_cmd)], cwd=root), 0)


if __name__ == "__main__":
  unittest.main()
