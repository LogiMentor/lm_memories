# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Dependencies of each step of an AMD campaign, for a resume (run_amd.sh).

  python -m tools.campaign.deps record <campaign root> [STEP ...]
  python -m tools.campaign.deps check <campaign root> STEP ...

A resume (LM_CAMPAIGN_RESUME) runs some steps again on a later commit and
keeps the results of the others. A kept result is valid only if what
determines it is the same on the commit of the resume. For each step four
SHA-256, over the files (path and contents, line endings normalized):

- sources: src/ (the RTL that is synthesized or simulated);
- manifest: manifests/lm_memories.toml (the compile order of each flow);
- harness: the VUnit harness, testbenches and reference model (sim/vunit
  VHDL, sim/ref); only for the simulation steps;
- configuration: what selects and judges the cases of the step: the
  vendor_synth configuration, cases and expected results; the post-synthesis
  script with its configurations; the VUnit runner and configurations; the
  mutations; the pilot configuration.

record writes <root>/logs/deps.json: every step (or the given ones) with
the commit and its four hashes, from the working tree (run_amd.sh requires
a clean tree, so it is the commit). check compares, for every step that is
NOT resumed, the recorded hashes with the current ones; on a difference it
prints the steps to run again and which dependency changed, and exits
with 1. A root without deps.json (campaigns before this check) gets its
record derived from git: each step at the commit of logs/steps.txt that last
ran it (the start, or a resume that named it).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
STEPS = ("pilot", "vivado", "checks_vivado", "checks_quartus", "postsynth", "vunit_ghdl", "vunit_questa", "mutation")
_COMMON = {"sources": ["src/**"], "manifest": ["manifests/lm_memories.toml"]}
_HARNESS = ["sim/vunit/*.vhd", "sim/ref/**"]
_SYNTH = ["tools/vendor_synth/cases.toml"]
_VUNIT = ["sim/vunit/run.py", "sim/vunit/configs.py"]
CONFIGURATION = {
  "pilot": ["tools/campaign/pilot.toml", *_SYNTH],
  "vivado": ["tools/vendor_synth/vivado_check.toml", "tools/vendor_synth/expected/amd.toml", *_SYNTH],
  "checks_vivado": ["tools/vendor_synth/fallback_check.toml", "tools/vendor_synth/expected/amd.toml", *_SYNTH],
  "checks_quartus": ["tools/vendor_synth/quartus_check.toml", "tools/vendor_synth/expected/intel.toml", *_SYNTH],
  "postsynth": ["sim/scripts/run_postsynth.py", "sim/vunit/configs.py"],
  "vunit_ghdl": _VUNIT,
  "vunit_questa": _VUNIT,
  "mutation": ["sim/vunit/mutation_check.py", *_VUNIT],
}
SIMULATION = ("postsynth", "vunit_ghdl", "vunit_questa", "mutation")
DEPS_FILE = "deps.json"

# A reader returns {path: contents} for the files of the tree that match the patterns.
Reader = Callable[[list[str]], dict[str, bytes]]


def _matches(path: str, patterns: list[str]) -> bool:
  return any(fnmatch.fnmatchcase(path, p) or (p.endswith("/**") and path.startswith(p[:-2])) for p in patterns)


def tree_reader(repo: Path = ROOT_DIR) -> Reader:
  """Files of the working tree (tracked files only, as git lists them)."""
  listed = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True).stdout.split("\n")

  def read(patterns: list[str]) -> dict[str, bytes]:
    return {p: (repo / p).read_bytes() for p in listed if p and _matches(p, patterns) and (repo / p).is_file()}
  return read


def commit_reader(commit: str, repo: Path = ROOT_DIR) -> Reader:
  """Files of a commit, from git."""
  listed = subprocess.run(["git", "ls-tree", "-r", "--name-only", commit], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.split("\n")

  def read(patterns: list[str]) -> dict[str, bytes]:
    paths = [p for p in listed if p and _matches(p, patterns)]
    if not paths:
      return {}
    # One git process for every file: "<sha> blob <size>\n<contents>\n" each.
    out = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, check=True, capture_output=True,
                         input="".join(f"{commit}:{p}\n" for p in paths).encode()).stdout
    files, pos = {}, 0
    for path in paths:
      header_end = out.index(b"\n", pos)
      size = int(out[pos:header_end].split()[2])
      files[path] = out[header_end + 1:header_end + 1 + size]
      pos = header_end + 1 + size + 1
    return files
  return read


def _hash(files: dict[str, bytes]) -> str:
  digest = hashlib.sha256()
  for path in sorted(files):
    digest.update(path.encode() + b"\0" + files[path].replace(b"\r\n", b"\n") + b"\0")
  return digest.hexdigest()


def step_deps(step: str, read: Reader) -> dict[str, str]:
  """The four hashes of one step ("harness" is "-" for a synthesis step)."""
  deps = {group: _hash(read(patterns)) for group, patterns in _COMMON.items()}
  deps["harness"] = _hash(read(_HARNESS)) if step in SIMULATION else "-"
  deps["configuration"] = _hash(read(CONFIGURATION[step]))
  return deps


def _head(repo: Path = ROOT_DIR) -> str:
  return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
                        check=True).stdout.strip()


def _load(root: Path) -> dict:
  try:
    return json.loads((root / "logs" / DEPS_FILE).read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return {}


def _save(root: Path, data: dict) -> None:
  (root / "logs").mkdir(parents=True, exist_ok=True)
  (root / "logs" / DEPS_FILE).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def step_commits(steps_text: str) -> dict[str, str]:
  """Commit that last ran each step, from logs/steps.txt."""
  start = re.search(r"start commit (\S+)", steps_text)
  if not start:
    return {}
  commits = {step: start[1] for step in STEPS}
  for commit, steps in re.findall(r"resume commit (\S+) steps: (.*)", steps_text):
    for step in steps.split():
      commits[step] = commit
  return commits


def recorded(root: Path, reader_at: Callable[[str], Reader] = commit_reader) -> dict:
  """deps.json of the root; derived from git and logs/steps.txt when missing."""
  data = _load(root)
  if data.get("steps"):
    return data
  try:
    commits = step_commits((root / "logs" / "steps.txt").read_text(encoding="utf-8"))
  except OSError:
    commits = {}
  readers: dict[str, Reader] = {}
  steps = {}
  for step, commit in commits.items():
    readers.setdefault(commit, reader_at(commit))
    steps[step] = {"commit": commit, "deps": step_deps(step, readers[commit])}
  data = {"steps": steps, "derived": "from git, at the commits of logs/steps.txt"}
  if steps:
    _save(root, data)
  return data


def record(root: Path, steps: list[str] | None = None, read: Reader | None = None, commit: str | None = None) -> dict:
  """Record the dependencies of these steps (all by default) on the current tree."""
  read = read or tree_reader()
  commit = commit or _head()
  data = _load(root) if steps else {}
  data.setdefault("steps", {})
  for step in steps or STEPS:
    data["steps"][step] = {"commit": commit, "deps": step_deps(step, read)}
  _save(root, data)
  return data


def check(root: Path, resumed: list[str], read: Reader | None = None,
          reader_at: Callable[[str], Reader] = commit_reader) -> list[str]:
  """Problems of a resume of these steps: kept steps whose dependencies changed."""
  read = read or tree_reader()
  data = recorded(root, reader_at)
  if not data.get("steps"):
    return [f"no record of the dependencies of the steps of {root} (logs/{DEPS_FILE} or logs/steps.txt)"]
  problems = []
  for step in STEPS:
    if step in resumed:
      continue
    entry = data["steps"].get(step)
    if entry is None:
      problems.append(f"{step}: no recorded dependencies")
      continue
    now = step_deps(step, read)
    changed = [group for group in now if entry["deps"].get(group) != now[group]]
    if changed:
      problems.append(f"{step}: {', '.join(changed)} changed since commit {entry['commit'][:7]}")
  return problems


def main(argv: list[str] | None = None) -> int:
  argv = sys.argv[1:] if argv is None else argv
  if len(argv) < 2 or argv[0] not in ("record", "check"):
    raise SystemExit(__doc__)
  root, steps = Path(argv[1]), argv[2:]
  unknown = [s for s in steps if s not in STEPS]
  if unknown:
    raise SystemExit(f"deps: unknown step(s) {unknown} ({', '.join(STEPS)})")
  if argv[0] == "record":
    record(root, steps or None)
    print(f"deps: recorded {', '.join(steps or STEPS)} in {root / 'logs' / DEPS_FILE}")
    return 0
  problems = check(root, steps)
  if problems:
    redo = sorted({p.split(":")[0] for p in problems if not p.startswith("no record")}, key=STEPS.index)
    print("deps: the resume would keep results whose dependencies changed:", file=sys.stderr)
    for problem in problems:
      print(f"  {problem}", file=sys.stderr)
    if redo:
      print(f"deps: run these steps again too: LM_CAMPAIGN_RESUME=\"{' '.join([*steps, *redo])}\" "
            "(pilot, vunit and mutation cannot be resumed: start a new campaign)", file=sys.stderr)
    return 1
  print(f"deps: every kept step has the dependencies it ran with ({', '.join(s for s in STEPS if s not in steps)})")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
