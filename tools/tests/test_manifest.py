# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for the lm_memories file manifest."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.manifest import DEFAULT_MANIFEST, ROOT_DIR, ManifestError, load
from tools.manifest.cli import main


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


if __name__ == "__main__":
  unittest.main()
