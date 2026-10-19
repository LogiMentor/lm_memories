#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""VUnit campaign for docs/results: the whole suite, with its logs archived.

Runs sim/vunit/run.py with the simulator of VUNIT_SIMULATOR (GHDL unless
set) into a new work directory ($LM_EDA_WORKDIR/vu/<name>), reads the
structured results (VUnit xunit file) and the final harness line of each
RAM test (counters), writes results.json there, and archives the whole
directory (tools/eda_archive.py). The campaign passes only if every test
passed and every RAM test printed exactly one harness line with errors=0.

  python sim/scripts/run_vunit_campaign.py ghdl_6.0.0
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

HARNESS_RE = re.compile(r"lm_mem_tb_harness (\S+) \[([^\]]*)\]: (.*)")


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("name", help="campaign name, for example ghdl_6.0.0")
  parser.add_argument("-p", "--parallel", type=int, default=2, help="VUnit parallel jobs (Questa 2021.1: 1)")
  args = parser.parse_args(argv)
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

  tests = []
  simulator = ""
  if xunit.is_file():
    for case in ElementTree.parse(xunit).getroot().iter("testcase"):
      name = ".".join(filter(None, (case.get("classname"), case.get("name"))))
      status = ("fail" if case.find("failure") is not None else "error" if case.find("error") is not None
                else "skipped" if case.find("skipped") is not None else "pass")
      out = case.find("system-out")
      lines = HARNESS_RE.findall((out.text or "") if out is not None else "")
      entry = {"test": name, "status": status}
      if ".tb_lm_mem_ram." in name:
        entry["harness_lines"] = len(lines)
        if len(lines) == 1:
          simulator = lines[0][1]
          entry["counts"] = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", lines[0][2])}
      tests.append(entry)
  problems = []
  if code != 0:
    problems.append(f"run.py exit code {code}")
  if not tests:
    problems.append("no test results")
  problems += [f"{t['test']}: {t['status']}" for t in tests if t["status"] != "pass"]
  problems += [f"{t['test']}: {t['harness_lines']} harness lines" for t in tests
               if "harness_lines" in t and t["harness_lines"] != 1]
  problems += [f"{t['test']}: errors={t['counts']['errors']}" for t in tests if t.get("counts", {}).get("errors")]
  result = {"campaign": args.name, "simulator": simulator or env.get("VUNIT_SIMULATOR", "ghdl"), "commit": commit(),
            "manifest_hash": manifest_hash(), "passed": sum(t["status"] == "pass" for t in tests),
            "total": len(tests), "problems": problems, "tests": tests}
  (root / "results.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
  result["archive"] = archive_campaign(f"vunit_{args.name}", root)
  (root / "results.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
  print(f"{result['passed']} of {result['total']} pass; {len(problems)} problems; results {root / 'results.json'}; "
        f"archive {result['archive']['archive']}")
  for problem in problems[:20]:
    print("  " + problem)
  return 0 if not problems else 1


if __name__ == "__main__":
  raise SystemExit(main())
