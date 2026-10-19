# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Reader for the lm_memories file manifest (manifests/lm_memories.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT_DIR / "manifests" / "lm_memories.toml"

CHECKS = ("ghdl-93-08", "ghdl-08", "ghdl-relaxed", "target-tool", "vunit")


class ManifestError(ValueError):
  """Raised when the manifest is malformed or a name is unknown."""


@dataclass(frozen=True)
class SourceFile:
  path: Path
  fileset: str
  check: str
  tools: tuple[str, ...]

  def allowed_for(self, tool: str | None) -> bool:
    return tool is None or not self.tools or tool in self.tools


@dataclass(frozen=True)
class Manifest:
  root: Path
  filesets: dict[str, dict]
  flows: dict[str, list[str]]

  def flow_names(self) -> list[str]:
    return list(self.flows)

  def files(self, flow: str, tool: str | None = None) -> list[SourceFile]:
    """Ordered source files of a flow, optionally restricted to one tool."""
    if flow not in self.flows:
      raise ManifestError(f"unknown flow '{flow}' (known: {', '.join(self.flows)})")
    result: list[SourceFile] = []
    for name in self.flows[flow]:
      fileset = self.filesets[name]
      tools = tuple(fileset.get("tools", ()))
      if tool is not None and tools and tool not in tools:
        raise ManifestError(f"file set '{name}' of flow '{flow}' is restricted to {tools}, not '{tool}'")
      for rel in fileset["files"]:
        result.append(SourceFile(self.root / rel, name, fileset["check"], tools))
    return result


def load(path: Path = DEFAULT_MANIFEST) -> Manifest:
  with path.open("rb") as handle:
    data = tomllib.load(handle)
  if data.get("version") != 1:
    raise ManifestError(f"{path}: unsupported manifest version {data.get('version')!r}")
  filesets = data.get("filesets", {})
  flows = {name: list(flow.get("filesets", [])) for name, flow in data.get("flows", {}).items()}
  root = path.resolve().parents[1]
  for name, fileset in filesets.items():
    if fileset.get("check") not in CHECKS:
      raise ManifestError(f"file set '{name}': check must be one of {CHECKS}")
    if not fileset.get("files"):
      raise ManifestError(f"file set '{name}' has no files")
    for rel in fileset["files"]:
      if not (root / rel).is_file():
        raise ManifestError(f"file set '{name}': missing file {rel}")
  for flow, names in flows.items():
    for name in names:
      if name not in filesets:
        raise ManifestError(f"flow '{flow}' uses unknown file set '{name}'")
  return Manifest(root, filesets, flows)
