# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Work directories of heavy EDA runs (synthesis, post-synthesis simulation).

- Root: $LM_EDA_WORKDIR; by default C:/lmeda on Windows and "lmeda" in the
  system temporary directory elsewhere. Not %TEMP% on Windows: the AMD
  campaign of 2026-11-04 failed three attempts of one case with "couldn't
  read file" on a file Vivado had just written under .Xil in %TEMP%, a
  pattern of a file scanner on new files there (PLAN 3.15). Keep the root
  short: Vivado builds paths under .Xil from the work directory and fails
  beyond 260 characters on Windows ([Common 17-680]).
- Each script has its own subdirectory: work_root("vs") for vendor_synth,
  work_root("ps") for the post-synthesis runner.
- After a successful run the scripts prune their work directories: logs
  (of every attempt), reports, run records and generated scripts stay, tool
  databases, projects, netlists and simulation libraries are removed. After
  a failed run nothing is removed. Campaign runs (--campaign) are never
  pruned; they are archived instead (tools/eda_archive.py).
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

WORKDIR_ENV = "LM_EDA_WORKDIR"
WINDOWS_DEFAULT = Path("C:/lmeda")


def default_base(os_name: str = os.name) -> Path:
  return WINDOWS_DEFAULT if os_name == "nt" else Path(tempfile.gettempdir()) / "lmeda"


def workdir_base() -> Path:
  value = os.environ.get(WORKDIR_ENV)
  return Path(value) if value else default_base()


def work_root(name: str) -> Path:
  return workdir_base() / name


def prune(directory: Path, keep: Iterable[str]) -> list[str]:
  """Remove every top-level entry of directory whose name matches none of keep (glob patterns).

  Returns the removed names. Entries that cannot be removed (for example a
  file still open by a tool) are left in place.
  """
  patterns = list(keep)
  removed = []
  if not directory.is_dir():
    return removed
  for entry in sorted(directory.iterdir()):
    if any(fnmatch.fnmatch(entry.name, pattern) for pattern in patterns):
      continue
    try:
      if entry.is_dir() and not entry.is_symlink():
        shutil.rmtree(entry)
      else:
        entry.unlink()
      removed.append(entry.name)
    except OSError:
      pass
  return removed
