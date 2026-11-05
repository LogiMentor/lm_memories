#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""VUnit campaign for docs/results: the whole suite, with its logs archived.

Runs sim/vunit/run.py with the simulator of VUNIT_SIMULATOR, which must be
set explicitly and match the campaign name (<tool>_<version>: ghdl_6.0.0 ->
ghdl, questa_2021.1 -> modelsim), into a new work directory ($LM_EDA_WORKDIR/vu/<name>), reads the
structured results (VUnit xunit file) and the final harness line of each
RAM test (counters), writes results.json there, and archives the whole
directory (tools/eda_archive.py). The campaign passes only if every test
passed and every RAM test printed exactly one harness line with every
counter and errors=0 written explicitly (tools/harness_counts.py), and
every harness line names the requested simulator of that version: the
published simulator is the one that ran.

  VUNIT_SIMULATOR=ghdl python sim/scripts/run_vunit_campaign.py ghdl_6.0.0
  VUNIT_SIMULATOR=modelsim VUNIT_MODELSIM_PATH=C:/questasim64_2021.1/win64 \\
    python sim/scripts/run_vunit_campaign.py questa_2021.1 -p 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from tools.eda_archive import archive_campaign  # noqa: E402
from tools.eda_provenance import commit, manifest_hash  # noqa: E402
from tools.eda_workdir import work_root  # noqa: E402
from tools.harness_counts import campaign_simulator, parse_counts, simulator_problems  # noqa: E402

HARNESS_RE = re.compile(r"lm_mem_tb_harness (\S+) \[([^\]]*)\]: (.*)")


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("name", help="campaign name, for example ghdl_6.0.0")
  parser.add_argument("-p", "--parallel", type=int, default=2, help="VUnit parallel jobs (Questa 2021.1: 1)")
  args = parser.parse_args(argv)
  expected, version = campaign_simulator(args.name)
  requested = os.environ.get("VUNIT_SIMULATOR")
  if requested != expected:
    raise SystemExit(f"VUNIT_SIMULATOR={requested or '(not set)'}: campaign {args.name} needs VUNIT_SIMULATOR={expected} "
                     "set explicitly")
  root = work_root("vu") / args.name
  if root.exists() and any(root.iterdir()):
    print(f"{root} must be new or empty", file=sys.stderr)
    return 2
  root.mkdir(parents=True, exist_ok=True)
  xunit = root / "vunit_results.xml"
  env = {k: v for k, v in os.environ.items() if k != "LM_MEM_BUILD_DIR"}
  cmd = [sys.executable, str(ROOT_DIR / "sim" / "vunit" / "run.py"), "-p", str(args.parallel), "--no-color",
         "--output-path", str(root / "vunit_out"), "--xunit-xml", str(xunit)]
  with (root / "console.log").open("w", encoding="utf-8") as handle:
    code = subprocess.run(cmd, cwd=ROOT_DIR, env=env, stdout=handle, stderr=subprocess.STDOUT).returncode
  result = {"campaign": args.name, **evaluate(xunit, code, requested, version), "commit": commit(),
            "manifest_hash": manifest_hash()}
  (root / "results.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
  result["archive"] = archive_campaign(f"vunit_{args.name}", root)
  (root / "results.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
  print(f"{result['passed']} of {result['total']} pass; {len(result['problems'])} problems; "
        f"results {root / 'results.json'}; archive {result['archive']['archive']}")
  for problem in result["problems"][:20]:
    print("  " + problem)
  return 0 if not result["problems"] else 1


def evaluate(xunit: Path, code: int | None, requested: str | None = None, version: str | None = None) -> dict:
  """Verdict of a VUnit run from its xunit file and the exit code of run.py.

  Every test must pass; every RAM test must print exactly one harness line
  with every counter and errors=0 written explicitly; with requested, every
  harness line must name that simulator (and version). Returns simulator
  (as the harness printed it), exit_code, passed, total, problems and tests.
  """
  tests = []
  simulators: list[str] = []
  if xunit.is_file():
    for case in ElementTree.parse(xunit).getroot().iter("testcase"):
      name = ".".join(filter(None, (case.get("classname"), case.get("name"))))
      status = ("fail" if case.find("failure") is not None else "error" if case.find("error") is not None
                else "skipped" if case.find("skipped") is not None else "pass")
      out = case.find("system-out")
      lines = HARNESS_RE.findall((out.text or "") if out is not None else "")
      entry: dict = {"test": name, "status": status}
      if ".tb_lm_mem_ram." in name:
        entry["harness_lines"] = len(lines)
        if len(lines) == 1:
          simulators.append(lines[0][1])
          entry["counts"], count_problems = parse_counts(lines[0][2])
          if count_problems:
            entry["count_problems"] = count_problems
      tests.append(entry)
  problems = []
  if code != 0:
    problems.append(f"run.py exit code {code}")
  if not tests:
    problems.append("no test results")
  problems += [f"{t['test']}: {t['status']}" for t in tests if t["status"] != "pass"]
  problems += [f"{t['test']}: {t['harness_lines']} harness lines" for t in tests
               if "harness_lines" in t and t["harness_lines"] != 1]
  problems += [f"{t['test']}: {p}" for t in tests for p in t.get("count_problems", [])]
  if requested:
    problems += simulator_problems(simulators, requested, version)
  return {"simulator": ", ".join(sorted(set(simulators))), "simulator_requested": requested, "exit_code": code, "passed": sum(t["status"] == "pass" for t in tests),
          "total": len(tests), "problems": problems, "tests": tests}


if __name__ == "__main__":
  raise SystemExit(main())
