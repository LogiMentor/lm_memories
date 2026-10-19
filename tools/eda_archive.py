# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Archives of the complete logs of campaigns that feed docs/results/.

A campaign run (--campaign NAME in vendor_synth, run_postsynth, the VUnit
campaign script and the mutation check) is never pruned: once it ends, its
work directory is packed into a compressed archive outside the repository,
and the result JSON records the archive name, its SHA-256 and the number of
files. Runs outside a campaign are pruned as before (tools/eda_workdir.py).

- Directory: $LM_EDA_ARCHIVE_DIR, or lm_eda_archive in the home directory.
  It must not be inside the repository.
- Name: <campaign>_<UTC time>.tar.xz.
- Contents: every file of the work directory except compiled simulation
  libraries and tool caches (ARCHIVE_SKIP): logs of every attempt, reports,
  scripts, generated tops and adapters, netlists, result files.

  from tools.eda_archive import archive_campaign
  record = archive_campaign("vivado_2023.1_amd", work_root)
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import os
import tarfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
ARCHIVE_ENV = "LM_EDA_ARCHIVE_DIR"
# Directory names and file patterns left out: compiled libraries and caches.
ARCHIVE_SKIP_DIRS = (".Xil", "xsim.dir", "unisim", "vunit_lib", "libraries", "__pycache__")
ARCHIVE_SKIP_FILES = ("*.o", "*.cf", "*.exe", "*.dcp", "*.pb", "*.wdb", "*.lock")


def archive_dir() -> Path:
  value = os.environ.get(ARCHIVE_ENV)
  path = Path(value) if value else Path.home() / "lm_eda_archive"
  resolved = path.resolve()
  if resolved == ROOT_DIR or ROOT_DIR in resolved.parents:
    raise SystemExit(f"{ARCHIVE_ENV}={path}: the campaign archive must be outside the repository")
  return path


def _members(root: Path) -> list[Path]:
  files = []
  for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = sorted(d for d in dirnames if d not in ARCHIVE_SKIP_DIRS)
    for name in sorted(filenames):
      if not any(fnmatch.fnmatch(name, pattern) for pattern in ARCHIVE_SKIP_FILES):
        files.append(Path(dirpath) / name)
  return files


def sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for block in iter(lambda: handle.read(1 << 20), b""):
      digest.update(block)
  return digest.hexdigest()


def archive_campaign(campaign: str, *roots: Path) -> dict[str, object]:
  """Pack the work directories of a campaign; returns {"archive", "sha256", "files"}."""
  target_dir = archive_dir()
  target_dir.mkdir(parents=True, exist_ok=True)
  stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
  target = target_dir / f"{campaign}_{stamp}.tar.xz"
  count = 0
  with tarfile.open(target, "w:xz") as tar:
    for root in roots:
      for path in _members(root):
        tar.add(path, arcname=(Path(root.name) / path.relative_to(root)).as_posix(), recursive=False)
        count += 1
  return {"archive": target.name, "sha256": sha256_file(target), "files": count}
