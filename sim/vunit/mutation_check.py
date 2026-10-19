#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Check that the VUnit suite detects deliberate defects in the RAM cores
and in the simulation architecture of lm_mem_ram_crw_crw.

The check never modifies the repository. It copies the sources (src/, sim/,
manifests/, tools/) into a temporary directory, applies each mutation to the
copy, runs the VUnit configurations that should catch it from the copy, and
puts the original file back into the copy before the next mutation. Each
VUnit run holds the machine-wide EDA lock (run.py).

Verdicts come from the structured VUnit results (--xunit-xml), not from the
console text:
- baseline: the same tests on the unmodified copy, with the same simulator,
  must all pass, and every pattern must select a test; otherwise no mutation
  is run and the check fails;
- DETECTED: every baseline test of that mutation ran, and at least one
  failed with an error of the harness or the scoreboard ("lm_mem_tb_pkg:" at
  severity error) in its output. A run without results (license, crash), with
  missing tests, or with failures that are not harness checks is an ERROR; a
  compile error is a COMPILE ERROR.

  python sim/vunit/mutation_check.py
  python sim/vunit/mutation_check.py --campaign ghdl   # keep and archive the results

It checks the sources as they are in the working tree, including
uncommitted changes.

Not covered by design: the data a model returns for a read/write collision
or beyond the depth, because the scoreboard excludes those reads.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from tools.eda_archive import archive_campaign  # noqa: E402
from tools.eda_provenance import commit  # noqa: E402
from tools.eda_workdir import work_root  # noqa: E402

# An error reported by the harness or the scoreboard (GHDL, Questa formats).
FUNCTIONAL_RE = re.compile(r"(?:\(report error\)|\*\* Error): lm_mem_tb_pkg: ")
COPIED = ("src", "sim", "manifests", "tools")
IGNORED = shutil.ignore_patterns("build", "__pycache__", "*.bak")

# Paths relative to the repository root (and to the copy).
SDP = Path("src") / "core" / "lm_mem_ram_sdp.vhd"
TDP1W = Path("src") / "core" / "lm_mem_ram_tdp1w.vhd"
CRW_CRW_SIM = Path("src") / "sim" / "lm_mem_ram_crw_crw_rtl.vhd"

# (description, file, original text, mutated text, VUnit test patterns)
MUTATIONS = [
  ("sdp: reversed sub-word order on read", SDP,
   "to_unsigned(i, C_RD_SEL)", "to_unsigned(C_RD_WORDS - 1 - i, C_RD_SEL)",
   ["*sdp.2clk.8to32.lat2*", "*r_w_ratio.8to32.lat2*"]),
  ("sdp: second output stage not gated by enb_i", SDP,
   "        end loop;\n        s_rd_dat_p <= s_rd_dat;\n      end if;",
   "        end loop;\n      end if;\n      s_rd_dat_p <= s_rd_dat;",
   ["*sdp.1clk.32x1024.lat2*"]),
  ("sdp: write ignores ena_i", SDP,
   "if (ena_i = '1') and (wen_i = '1') then", "if wen_i = '1' then",
   ["*sdp.1clk.32x1024.lat1*"]),
  ("sdp: latency 2 output taken from stage 1", SDP,
   "    rd_dat_o <= s_rd_dat_p;", "    rd_dat_o <= s_rd_dat;",
   ["*r_w.32x1024.lat2*"]),
  ("tdp1w: port A write-first instead of read-first", TDP1W,
   "        s_data_rd_a <= s_mem(to_integer(unsigned(addr_a_i)));",
   "        if wen_a_i = '1' then s_data_rd_a <= data_wr_a_i;"
   " else s_data_rd_a <= s_mem(to_integer(unsigned(addr_a_i))); end if;",
   ["*tdp1w.1clk.32x1024.lat11*"]),
  ("tdp1w: port B read ignores enb_i", TDP1W,
   "      if enb_i = '1' then\n        s_data_rd_b <= s_mem(",
   "      if true then\n        s_data_rd_b <= s_mem(",
   ["*crw_cr.1clk.32x1024.lat1*"]),
  ("crw_crw sim: port A write ignores ena_i", CRW_CRW_SIM,
   "v_wr_a_on := v_rd_a_on and (wen_a_i = '1');", "v_wr_a_on := v_edge_a and (wen_a_i = '1');",
   ["*crw_crw.2clk.32x1024.lat12*"]),
  ("crw_crw sim: port B write-first instead of read-first", CRW_CRW_SIM,
   "          v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := v_mem(v_c);",
   "          if v_wr_b_on then v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) :="
   " data_wr_b_i((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH);"
   " else v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := v_mem(v_c); end if;",
   ["*crw_crw.1clk.32x1024.lat11*"]),
  ("crw_crw sim: write/write collision leaves the cell defined", CRW_CRW_SIM,
   "          v_undef(v_c)  := v_hit;\n          v_wr_t_b(v_c) := now;",
   "          v_undef(v_c)  := false;\n          v_wr_t_b(v_c) := now;",
   ["*crw_crw.1clk.32x1024.lat11*"]),
  ("crw_crw sim: port B latency 2 output taken from stage 1", CRW_CRW_SIM,
   "    data_rd_b_o <= s_data_rd_b_p;", "    data_rd_b_o <= s_data_rd_b;",
   ["*crw_crw.2clk.32x1024.lat12*"]),
  ("crw_crw sim: reversed sub-word order on the wide port B read", CRW_CRW_SIM,
   "        v_c := v_base_b + i;\n        if v_c >= C_CELLS then\n          v_rd_b(",
   "        v_c := v_base_b + (C_B_WORDS - 1 - i);\n        if v_c >= C_CELLS then\n          v_rd_b(",
   ["*crw_crw.1clk.8to32.lat22*"]),
  ("crw_crw sim: port B write ignores wen_b_i", CRW_CRW_SIM,
   "v_wr_b_on := v_rd_b_on and (wen_b_i = '1');", "v_wr_b_on := v_rd_b_on;",
   ["*rw_rw.32x1024.lat1*"]),
  ("crw_crw sim: port A output defined (zero) before the first read", CRW_CRW_SIM,
   "  signal s_data_rd_a   : std_logic_vector(g_ram_a_data_w - 1 downto 0) := (others => 'X');",
   "  signal s_data_rd_a   : std_logic_vector(g_ram_a_data_w - 1 downto 0) := (others => '0');",
   ["*crw_crw.1clk.32x1024.lat11*"]),
  ("sdp: output defined (zero) before the first read", SDP,
   "  signal s_rd_dat   : std_logic_vector(g_rd_data_w - 1 downto 0);",
   "  signal s_rd_dat   : std_logic_vector(g_rd_data_w - 1 downto 0) := (others => '0');",
   ["*sdp.1clk.32x1024.lat1*"]),
]


def _src_digest() -> dict[str, str]:
  return {p.relative_to(ROOT_DIR).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in sorted((ROOT_DIR / "src").rglob("*")) if p.is_file()}


def _test_results(xunit: Path) -> dict[str, dict[str, str]]:
  """{test name: {"status": pass|fail|error|skipped, "output": text}} from a VUnit xunit file."""
  results = {}
  for case in ElementTree.parse(xunit).getroot().iter("testcase"):
    name = ".".join(filter(None, (case.get("classname"), case.get("name"))))
    if case.find("failure") is not None:
      status = "fail"
    elif case.find("error") is not None:
      status = "error"
    elif case.find("skipped") is not None:
      status = "skipped"
    else:
      status = "pass"
    out = case.find("system-out")
    results[name] = {"status": status, "output": (out.text or "") if out is not None else ""}
  return results


def _run_vunit(copy: Path, tests: list[str], tag: str, env: dict[str, str]) -> tuple[dict | None, str]:
  """Run the VUnit tests matching the patterns in the copy; (results by test, console output)."""
  xunit = copy / "results" / f"{tag}.xml"
  xunit.parent.mkdir(exist_ok=True)
  proc = subprocess.run([sys.executable, str(copy / "sim" / "vunit" / "run.py"), "-p", "4",
                         "--xunit-xml", str(xunit), *tests],
                        cwd=copy, env=env, capture_output=True, text=True)
  out = proc.stdout + proc.stderr
  (copy / "results" / f"{tag}.log").write_text(out, encoding="utf-8")
  if not xunit.is_file():
    return None, out
  return _test_results(xunit), out


def _selected(results: dict, patterns: list[str]) -> set[str]:
  return {name for name in results if any(fnmatch.fnmatch(name, p) for p in patterns)}


def _classify(results: dict | None, out: str, expected: set[str]) -> tuple[str, list[str]]:
  """Verdict of one mutation from structured VUnit results.

  DETECTED needs every test of the baseline to have run, and at least one of
  them to fail on a check of the harness or the scoreboard ("lm_mem_tb_pkg:"
  error in its output). A run without results, with missing tests, or where
  the only failures are simulator errors is an ERROR, never a detection.
  """
  if results is None:
    return ("COMPILE ERROR" if "Compile failed" in out or "compilation error" in out else "ERROR (no results)"), []
  ran = set(results)
  if not expected <= ran:
    return "ERROR (tests missing: " + ", ".join(sorted(expected - ran)) + ")", []
  failed = sorted(name for name in expected if results[name]["status"] == "fail")
  functional = [name for name in failed if FUNCTIONAL_RE.search(results[name]["output"])]
  if functional:
    return "DETECTED", functional
  if failed or any(results[name]["status"] != "pass" for name in expected):
    return "ERROR (failures without a harness check)", failed
  return "NOT DETECTED", []


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--campaign", metavar="NAME",
                      help="keep the VUnit results and logs of every run, archive them in $LM_EDA_ARCHIVE_DIR "
                           "(tools/eda_archive.py) and write results.json under $LM_EDA_WORKDIR/mut/NAME")
  args = parser.parse_args(argv)
  before = _src_digest()
  with tempfile.TemporaryDirectory(prefix="lm_mutation_") as tmp:
    copy = Path(tmp)
    for folder in COPIED:
      shutil.copytree(ROOT_DIR / folder, copy / folder, ignore=IGNORED)
    report = run_mutations(copy)
    if args.campaign:
      target = work_root("mut") / args.campaign
      if target.exists() and any(target.iterdir()):
        print(f"--campaign: {target} must be new or empty", file=sys.stderr)
        return 2
      shutil.copytree(copy / "results", target / "results")
      report["commit"] = commit()
      report["archive"] = archive_campaign(f"mutation_{args.campaign}", target)
      (target / "results.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
      print(f"wrote {target / 'results.json'}; archive {report['archive']['archive']}")
  print(f"baseline: {report['baseline']['verdict']} ({len(report['baseline']['tests'])} tests)")
  for m in report["mutations"]:
    print(f"{m['verdict']:14} | {m['name']} | {' '.join(m['patterns'])} | {' '.join(m['failed_tests'])}")
  if _src_digest() != before:
    print("ERROR: files under src/ changed during the mutation check", file=sys.stderr)
    return 1
  ok = report["baseline"]["verdict"] == "PASS" and all(m["verdict"] == "DETECTED" for m in report["mutations"])
  return 0 if ok else 1


def run_mutations(copy: Path) -> dict:
  # VUnit output stays inside the copy.
  env = {k: v for k, v in os.environ.items() if k != "LM_MEM_BUILD_DIR"}
  patterns = sorted({p for *_, tests in MUTATIONS for p in tests})
  # Baseline: the unmodified copy, same simulator and same tests. Every
  # pattern must select a test and every test must pass, otherwise a failure
  # of a mutant says nothing.
  results, out = _run_vunit(copy, patterns, "baseline", env)
  report = {"simulator": env.get("VUNIT_SIMULATOR", "ghdl"), "mutations": []}
  if results is None:
    report["baseline"] = {"verdict": "ERROR (no results)", "tests": []}
    return report
  empty = [p for p in patterns if not _selected(results, [p])]
  failing = sorted(n for n, r in results.items() if r["status"] != "pass")
  verdict = "PASS" if not empty and not failing else f"FAIL (no test for {empty}, failing {failing})"
  report["baseline"] = {"verdict": verdict, "tests": sorted(results)}
  if verdict != "PASS":
    return report
  for index, (name, rel, old, new, tests) in enumerate(MUTATIONS, start=1):
    original = (ROOT_DIR / rel).read_text(encoding="utf-8")
    target = copy / rel
    entry = {"name": name, "file": rel.as_posix(), "patterns": tests, "failed_tests": []}
    if old not in original:
      entry["verdict"] = "ANCHOR MISSING"
      report["mutations"].append(entry)
      continue
    try:
      target.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
      results, out = _run_vunit(copy, tests, f"mutation{index:02d}", env)
      expected = {n for n in report["baseline"]["tests"] if any(fnmatch.fnmatch(n, p) for p in tests)}
      entry["verdict"], entry["failed_tests"] = _classify(results, out, expected)
    finally:
      target.write_text(original, encoding="utf-8", newline="\n")
    report["mutations"].append(entry)
  return report


if __name__ == "__main__":
  raise SystemExit(main())
