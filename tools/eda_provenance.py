# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Provenance of EDA artefacts: which sources, manifest and commit produced them.

A result kept on disk (synthesis log and reports, netlist) is reused or
evaluated again only if it was produced from the same inputs as now:

- sources_hash: SHA-256 over the compiled files in compile order (their
  paths relative to the repository and their contents) and any extra text
  that changes the run (a generated top, the tool script, the part);
- manifest_hash: SHA-256 of manifests/lm_memories.toml;
- commit: git HEAD, with "+dirty" when tracked files differ from it. It is
  recorded for reference only: equal inputs on another commit give the same
  sources_hash.

  from tools.eda_provenance import provenance
  record = provenance(sources, extra=[script_text])
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterable
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
MANIFEST = ROOT_DIR / "manifests" / "lm_memories.toml"


def _rel(path: Path) -> str:
  try:
    return path.resolve().relative_to(ROOT_DIR).as_posix()
  except ValueError:
    # Generated files outside the repository: their contents count, not where they are.
    return path.name


def _normalized(path: Path) -> bytes:
  # Line endings depend on the checkout (.gitattributes), the contents do not.
  return path.read_bytes().replace(b"\r\n", b"\n")


def sources_hash(sources: Iterable[Path], extra: Iterable[str] = ()) -> str:
  digest = hashlib.sha256()
  for path in sources:
    digest.update(_rel(Path(path)).encode() + b"\0")
    digest.update(_normalized(Path(path)) + b"\0")
  for text in extra:
    digest.update(b"extra\0" + text.replace("\r\n", "\n").encode() + b"\0")
  return digest.hexdigest()


def manifest_hash() -> str:
  return hashlib.sha256(_normalized(MANIFEST)).hexdigest()


def commit() -> str:
  try:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT_DIR, capture_output=True, text=True,
                          check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT_DIR,
                           capture_output=True, text=True, check=True).stdout.strip()
  except (OSError, subprocess.CalledProcessError):
    return "unknown"
  return head + ("+dirty" if dirty else "")


def provenance(sources: Iterable[Path], extra: Iterable[str] = ()) -> dict[str, str]:
  """{"sources_hash", "manifest_hash", "commit"} for a run on these inputs."""
  return {"sources_hash": sources_hash(sources, extra), "manifest_hash": manifest_hash(), "commit": commit()}


def same_inputs(recorded: dict | None, current: dict[str, str]) -> bool:
  """True if a recorded provenance has the same sources and manifest as now (the commit may differ)."""
  return bool(recorded) and all(recorded.get(k) == current[k] for k in ("sources_hash", "manifest_hash"))
