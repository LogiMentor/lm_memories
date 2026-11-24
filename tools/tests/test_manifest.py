# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the lm_memories file manifest."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tools.manifest import DEFAULT_MANIFEST, ROOT_DIR, ManifestError, cli, load
from tools.manifest.cli import main


def which_311_windows(cmd: str, mode: int = os.F_OK | os.X_OK, path: str | None = None) -> str | None:
  """shutil.which of Python 3.11 on Windows (Lib/shutil.py), PATHEXT reduced
  to .exe: a cmd with a directory part is checked as it is, without PATHEXT;
  a search tries the name with each extension of PATHEXT."""
  if os.path.dirname(cmd):
    return cmd if os.path.isfile(cmd) else None
  names = [cmd] if cmd.lower().endswith(".exe") else [cmd + ".exe"]
  for folder in (os.environ.get("PATH", "") if path is None else path).split(os.pathsep):
    for name in names:
      if folder and os.path.isfile(os.path.join(folder, name)):
        return os.path.join(folder, name)
  return None


class GhdlFoundTest(unittest.TestCase):
  def test_a_windows_path_without_exe_is_found_with_python_3_11(self) -> None:
    # Review of PR #9: GHDL=C:/msys64/ucrt64/bin/ghdl (tools/vendor_synth/
    # local.example.toml) is a valid installation, ghdl.exe starts, but
    # shutil.which of Python 3.11 does not find the path without .exe, so
    # "python -m tools.manifest check" stopped with "GHDL not found".
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(shutil, "which", which_311_windows), \
         mock.patch.object(cli, "check", return_value=0):
      (Path(tmp) / "ghdl.exe").write_text("")
      for ghdl in (str(Path(tmp) / "ghdl"), str(Path(tmp) / "ghdl.exe")):
        with self.subTest(ghdl=ghdl), mock.patch.object(cli, "GHDL", ghdl), redirect_stderr(io.StringIO()) as err:
          self.assertEqual(main(["check", "--flow", "sim"]), 0, err.getvalue())
      with mock.patch.dict(os.environ, {"PATH": tmp}):
        self.assertTrue(cli.ghdl_found("ghdl"))
      self.assertFalse(cli.ghdl_found(str(Path(tmp) / "other" / "ghdl")))

  @unittest.skipUnless(os.name == "nt", "Windows: PATHEXT of the real shutil.which")
  def test_a_path_without_exe_is_found_on_windows(self) -> None:
    # The same with the shutil.which of the running Python (CI: 3.11 and 3.12).
    with tempfile.TemporaryDirectory() as tmp:
      (Path(tmp) / "ghdl.exe").write_bytes(b"")
      self.assertTrue(cli.ghdl_found(str(Path(tmp) / "ghdl")))
      self.assertTrue(cli.ghdl_found(Path(tmp).as_posix() + "/ghdl"))
      self.assertFalse(cli.ghdl_found(str(Path(tmp) / "other")))

  @unittest.skipIf(os.name == "nt", "POSIX execute permission")
  def test_a_path_must_be_an_executable(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      ghdl = Path(tmp) / "ghdl"
      ghdl.write_text("")
      self.assertFalse(cli.ghdl_found(str(ghdl)))
      ghdl.chmod(0o755)
      self.assertTrue(cli.ghdl_found(str(ghdl)))


class ManifestTest(unittest.TestCase):
  def setUp(self) -> None:
    self.manifest = load()

  def test_every_vhdl_source_is_listed(self) -> None:
    listed = {
      (ROOT_DIR / rel).resolve()
      for fileset in self.manifest.filesets.values()
      for rel in fileset["files"]
    }
    on_disk = {
      path.resolve()
      for folder in ("src", "sim")
      for path in (ROOT_DIR / folder).rglob("*.vhd")
      if "build" not in path.relative_to(ROOT_DIR).parts
    }
    self.assertEqual(sorted(on_disk - listed), [], "VHDL files missing from the manifest")

  def test_synthesis_flows_contain_only_library_sources(self) -> None:
    for flow in self.manifest.flow_names():
      if not flow.startswith("synth-"):
        continue
      for source in self.manifest.files(flow):
        rel = source.path.relative_to(ROOT_DIR).as_posix()
        self.assertTrue(rel.startswith("src/"), f"{flow}: {rel} is not a library source")

  def test_rtl_directories_use_the_strict_check(self) -> None:
    for name, fileset in self.manifest.filesets.items():
      for rel in fileset["files"]:
        if rel.startswith(("src/core/", "src/generic/", "src/sim/")):
          self.assertEqual(fileset["check"], "ghdl-93-08", f"{name}: {rel}")

  def test_strict_file_sets_are_checked_with_strict_vhdl_93(self) -> None:
    # Issue #12: --std=93c accepted a loop bound that VHDL-93 does not allow.
    calls: list[tuple[list[str], set[str]]] = []

    def analyze(files: list, flags: list[str], workdir: Path) -> list[str]:
      calls.append((list(flags), {f.check for f in files}))
      return []

    with mock.patch.object(cli, "_analyze", analyze), redirect_stdout(io.StringIO()):
      self.assertEqual(cli.check(["sim"], DEFAULT_MANIFEST), 0)
    strict = [flags for flags, checks in calls if checks == {"ghdl-93-08"} and "--std=08" not in flags]
    self.assertEqual(strict, [["--std=93"]])
    self.assertFalse([flags for flags, _ in calls if "--std=93c" in flags])

  def test_package_comes_first(self) -> None:
    for flow in self.manifest.flow_names():
      first = self.manifest.files(flow)[0].path.relative_to(ROOT_DIR).as_posix()
      self.assertEqual(first, "src/common/lm_mem_pkg.vhd", flow)

  def test_list_command(self) -> None:
    out = io.StringIO()
    with redirect_stdout(out):
      self.assertEqual(main(["list", "--flow", "synth-amd"]), 0)
    lines = out.getvalue().splitlines()
    self.assertTrue(lines[0].endswith("src/common/lm_mem_pkg.vhd"))
    self.assertTrue(any(line.endswith("src/core/lm_mem_ram_sdp.vhd") for line in lines))

  def test_unknown_flow(self) -> None:
    with self.assertRaises(ManifestError):
      self.manifest.files("no-such-flow")

  def test_bad_check_value_is_rejected(self) -> None:
    text = DEFAULT_MANIFEST.read_text(encoding="utf-8").replace('check = "ghdl-08"', 'check = "maybe"', 1)
    with tempfile.TemporaryDirectory() as tmp:
      fake = Path(tmp) / "manifests" / "lm_memories.toml"
      fake.parent.mkdir()
      fake.write_text(text, encoding="utf-8")
      with self.assertRaises(ManifestError):
        load(fake)


@unittest.skipUnless(cli.ghdl_found(cli.GHDL), "GHDL not installed")
class StrictVhdl93Test(unittest.TestCase):
  def test_strict_file_sets_analyse_with_strict_vhdl_93(self) -> None:
    # Issue #12: lm_mem_pkg.vhd stopped at a loop bound, and every source
    # after it.
    manifest = load()
    files: list = []
    for flow in manifest.flow_names():
      files += [f for f in manifest.files(flow) if f.check == "ghdl-93-08" and f not in files]
    self.assertIn("src/common/lm_mem_pkg.vhd", [f.path.relative_to(ROOT_DIR).as_posix() for f in files])
    with tempfile.TemporaryDirectory() as tmp:
      for f in files:
        result = subprocess.run([cli.GHDL, "-a", "--std=93", f"--workdir={tmp}", str(f.path)],
                                cwd=tmp, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"{f.path.as_posix()}:\n{result.stdout}{result.stderr}")


if __name__ == "__main__":
  unittest.main()
