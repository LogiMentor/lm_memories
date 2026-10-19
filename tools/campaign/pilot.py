# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Pilot of the AMD campaign: corrections that only the real tools can show.

  python -m tools.campaign.pilot <pilot directory>

Writes <pilot directory>/pilot.json and exits with 1 if any check fails:

1. launcher: tools/campaign/pilot.toml with vendor_synth. r_w_32x1024 passes
   with exit code 0 through the Windows launcher; every wrong generic stops
   with a non-zero exit code (launcher with "call"), with and without
   synth_design -assert; the empty vendor string reaches Vivado as an empty
   string (bad_r_w_infer_no_vendor stops at its check).
2. generic_1000_for_1024: the Vivado script of the passing r_w_32x1024 run
   is changed to g_ram_depth=1000 while the case still asks for 1024, and run
   again; --evaluate-only must fail on the generic although the block RAM
   count is the same (1 tile).
3. old_code: the RTL of 5a5761a (before the corrections), with its own
   vendor_synth and without -assert: lm_mem_ccd_resync with one level, a FIFO
   with almost-full limit 0 and lm_mem_ram_r_w_infer with latency 3 must
   complete synthesis, showing what the new static checks stop.

Every tool run holds the machine-wide EDA lock.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from tools.eda_lock import heavy_tool_lock
from tools.eda_proc import run_tool

ROOT_DIR = Path(__file__).resolve().parents[2]
PILOT_CONFIG = ROOT_DIR / "tools" / "campaign" / "pilot.toml"
VIVADO_DIR = Path(os.environ.get("LM_VIVADO_DIR", "C:/Xilinx/Vivado/2023.1"))
OLD_COMMIT = "5a5761a"
OLD_CASES = [
  {"name": "bad_ccd_resync_levels1", "kind": "entity", "entity": "lm_mem_ccd_resync",
   "generics": {"g_meta_levels": 1}},
  {"name": "bad_fifo_almost_full_0", "kind": "entity", "entity": "lm_mem_fifo_sync",
   "generics": {"g_data_w": 32, "g_depth": 16, "g_almost_full_limit": 0}},
  {"name": "bad_r_w_infer_latency3", "kind": "entity", "entity": "lm_mem_ram_r_w_infer",
   "generics": {"g_ram_data_w": 32, "g_ram_depth": 1024, "g_ram_latency": 3}},
]


def vendor_synth(args: list[str], cwd: Path = ROOT_DIR) -> int:
  return subprocess.run([sys.executable, "-m", "tools.vendor_synth", *args], cwd=cwd).returncode


def results(work: Path) -> list[dict]:
  return json.loads((work / "summary.json").read_text(encoding="utf-8"))["results"]


def check_launcher(base: Path) -> dict:
  work = base / "launcher"
  vendor_synth(["--config", str(PILOT_CONFIG), "--keep-going", "--keep-work", "--work-root", str(work)])
  problems = []
  for r in results(work):
    record = json.loads((Path(r["work_dir"]) / "run.json").read_text(encoding="utf-8"))
    rc = record.get("return_code")
    where = f"{r['job']} {r['case']}"
    if r["status"] != "pass":
      problems.append(f"{where}: {r['status']}: {r.get('message', '')}")
    if r["case"] == "r_w_32x1024" and rc != 0:
      problems.append(f"{where}: exit code {rc}, expected 0")
    if r["case"].startswith("bad_") and rc in (0, None):
      problems.append(f"{where}: exit code {rc}, expected non-zero")
  return {"ok": not problems, "problems": problems,
          "runs": [{"job": r["job"], "case": r["case"], "status": r["status"]} for r in results(work)]}


def check_generic_1000(base: Path) -> dict:
  source = base / "launcher" / "pilot_artix7" / "r_w_32x1024"
  work = base / "tamper"
  case_dir = work / "pilot_artix7" / "r_w_32x1024"
  shutil.copytree(source, case_dir)
  script = case_dir / "run_vivado.tcl"
  text = script.read_text(encoding="utf-8")
  if text.count("-generic {g_ram_depth=1024}") != 1:
    return {"ok": False, "problems": ["the script of r_w_32x1024 has no -generic {g_ram_depth=1024}"]}
  # The copied script names the directory of the original run.
  for old_dir in {source.resolve().as_posix(), source.as_posix()}:
    text = text.replace(old_dir, case_dir.resolve().as_posix())
  if source.resolve().as_posix() in text:
    return {"ok": False, "problems": ["the copied script still names the original directory"]}
  script.write_text(text.replace("-generic {g_ram_depth=1024}", "-generic {g_ram_depth=1000}"), encoding="utf-8")
  for old in (case_dir / "reports").glob("*"):
    old.unlink()
  record_file = case_dir / "run.json"
  record = json.loads(record_file.read_text(encoding="utf-8"))
  record["started_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
  vivado = VIVADO_DIR / "bin" / ("vivado.bat" if sys.platform == "win32" else "vivado")
  cmd = (["cmd", "/c", str(vivado)] if sys.platform == "win32" else [str(vivado)]) + [
    "-mode", "batch", "-nojournal", "-source", script.resolve().as_posix()]
  with (case_dir / "run.log").open("w", encoding="utf-8") as handle, \
       heavy_tool_lock("pilot: r_w_32x1024 with g_ram_depth=1000 in the script", kind="synthesis"):
    handle.write("command: " + " ".join(cmd) + "\n")
    handle.flush()
    record["return_code"] = run_tool(cmd, cwd=case_dir, stdout=handle, stderr=subprocess.STDOUT, timeout=1800)
  record["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
  record_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
  vendor_synth(["--config", str(PILOT_CONFIG), "--job", "pilot-artix7", "--case", "r_w_32x1024",
                "--evaluate-only", "--work-root", str(work)])
  r = results(work)[0]
  problems = []
  if r["status"] != "fail":
    problems.append(f"status {r['status']}, expected fail")
  if not any("g_ram_depth: requested 1024, synthesized with 1000" in p for p in r["problems"]):
    problems.append("no generic mismatch reported: " + "; ".join(r["problems"]))
  if r["metrics"].get("bram_tiles") != 1.0:
    problems.append(f"bram_tiles {r['metrics'].get('bram_tiles')}, expected 1 (same occupancy)")
  return {"ok": not problems, "problems": problems, "status": r["status"], "reported": r["problems"],
          "bram_tiles": r["metrics"].get("bram_tiles"), "exit_code": record["return_code"]}


def check_old_code(base: Path) -> dict:
  old_tree = base / "old_src"
  old_tree.mkdir(parents=True)
  archive = base / "old.tar"
  with archive.open("wb") as handle:
    subprocess.run(["git", "archive", OLD_COMMIT], cwd=ROOT_DIR, stdout=handle, check=True)
  with tarfile.open(archive) as tar:
    tar.extractall(old_tree, filter="data")
  archive.unlink()
  config = base / "old_static.json"
  config.write_text(json.dumps({"cases": OLD_CASES, "jobs": [{
    "name": "old-artix7", "enabled": True, "tool": "vivado", "vendor": "amd", "family": "artix7",
    "part": "xc7a35tfgg484-2", "install_dir": "C:/Xilinx/Vivado/2023.1", "manifest_flow": "synth-unsupported",
    "threads": 2, "timeout_seconds": 1800, "cases": [c["name"] for c in OLD_CASES]}]}), encoding="utf-8")
  work = base / "old"
  vendor_synth(["--config", str(config), "--keep-going", "--keep-work", "--work-root", str(work)], cwd=old_tree)
  problems, runs = [], []
  for case in OLD_CASES:
    log = work / "old_artix7" / case["name"] / "run.log"
    text = log.read_text(encoding="utf-8", errors="ignore") if log.is_file() else ""
    completed = "synth_design completed successfully" in text
    runs.append({"case": case["name"], "synthesis_completed_on_old_code": completed})
    if not completed:
      problems.append(f"{case['name']}: the old code did not complete synthesis")
  return {"ok": not problems, "problems": problems, "commit": OLD_COMMIT, "runs": runs}


def main(argv: list[str] | None = None) -> int:
  argv = sys.argv[1:] if argv is None else argv
  base = Path(argv[0])
  base.mkdir(parents=True, exist_ok=True)
  report = {}
  for name, check in (("launcher", check_launcher), ("generic_1000_for_1024", check_generic_1000),
                      ("old_code", check_old_code)):
    try:
      report[name] = check(base)
    except Exception as exc:  # a broken step is a failed pilot, with its reason
      report[name] = {"ok": False, "problems": [f"{type(exc).__name__}: {exc}"]}
    print(f"pilot {name}: {'ok' if report[name]['ok'] else 'FAILED'}", flush=True)
    for problem in report[name]["problems"]:
      print(f"  {problem}", flush=True)
    if name == "launcher" and not report[name]["ok"]:
      break  # the next check needs the passing run
  report["ok"] = all(v["ok"] for v in report.values() if isinstance(v, dict))
  (base / "pilot.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
  return 0 if report["ok"] else 1


if __name__ == "__main__":
  raise SystemExit(main())
