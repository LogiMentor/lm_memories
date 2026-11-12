# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Pilot of the AMD campaign: corrections that only the real tools can show.

  python -m tools.campaign.pilot <pilot directory> [--toolset FILE]

--toolset: the toolset of the campaign (logs/toolset.json,
tools/campaign/toolset.py), passed to vendor_synth: the log of every run of
the current code must be of that tool version, and is compared with the
expected results of that version.

Writes <pilot directory>/pilot.json and exits with 1 if any check fails:

1. launcher: tools/campaign/pilot.toml with vendor_synth, one case at a
   time. r_w_32x1024 passes with exit code 0 through the Windows launcher;
   every wrong generic stops with a non-zero exit code, with and without
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

Retry policy, the same as the campaign (tools/eda_retry.py): each pilot case
ends as pass, fail or tool-error. A tool-error (the tool failed with a
transient signature of eda_retry and no error of the project) is run again,
at most MAX_RETRIES times, after the wait of eda_retry (30 s, then 60 s;
$LM_EDA_RETRY_DELAYS), each attempt in its own directory
(<check>/<case>/attempt<n>), kept and archived with the pilot. A fail stops
the pilot at once: the remaining cases are not run. The pilot fails if a
case failed or is still a tool-error after its reruns. Every case records
its result, its attempts, the tool runs of each attempt (vendor_synth also
retries inside one attempt) and the transient signatures met.

Every tool run holds the machine-wide EDA lock.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from tools.eda_lock import heavy_tool_lock
from tools.eda_proc import run_tool
from tools.eda_retry import MAX_RETRIES, record_reason, retry_delay, retry_delays, retry_reason, transient_reason
from tools.vendor_synth.check import RUN_ID_ENV

ROOT_DIR = Path(__file__).resolve().parents[2]
PILOT_CONFIG = ROOT_DIR / "tools" / "campaign" / "pilot.toml"
LAUNCHER_CASES = [  # (job, case) of pilot.toml, in this order
  ("pilot-artix7", "r_w_32x1024"),
  ("pilot-artix7", "bad_ccd_resync_levels1"),
  ("pilot-artix7", "bad_r_w_infer_no_vendor"),
  ("pilot-artix7", "bad_fifo_almost_empty_15"),
  ("pilot-artix7-assert", "bad_ccd_resync_levels1"),
  ("pilot-artix7-assert", "bad_r_w_infer_no_vendor"),
  ("pilot-artix7-assert", "bad_fifo_almost_empty_15"),
]
OLD_COMMIT = "5a5761a"
OLD_JOB = "old-artix7"
OLD_CASES = [
  {"name": "bad_ccd_resync_levels1", "kind": "entity", "entity": "lm_mem_ccd_resync",
   "generics": {"g_meta_levels": 1}},
  {"name": "bad_fifo_almost_full_0", "kind": "entity", "entity": "lm_mem_fifo_sync",
   "generics": {"g_data_w": 32, "g_depth": 16, "g_almost_full_limit": 0}},
  {"name": "bad_r_w_infer_latency3", "kind": "entity", "entity": "lm_mem_ram_r_w_infer",
   "generics": {"g_ram_data_w": 32, "g_ram_depth": 1024, "g_ram_latency": 3}},
]
COMPLETED = "synth_design completed successfully"
CHECKS = ("launcher", "generic_1000_for_1024", "old_code")


def vivado_dir() -> Path:
  """The Vivado installation of the pilot: $LM_VIVADO_DIR, the same one as
  pilot.toml (install_dir_env). There is no default installation."""
  value = os.environ.get("LM_VIVADO_DIR", "").strip()
  if not value:
    raise SystemExit("pilot: set LM_VIVADO_DIR to the Vivado installation directory")
  return Path(value)


class PilotStop(Exception):
  """A case failed: the pilot stops at once."""


# An attempt: {"status": "pass" | "fail" | "tool-error", "problems": [...],
# "dir": str, "exit_code": int | None, "tool_runs": int,
# "transient": ["<signature>: <log line>", ...]}.
Attempt = dict


def run_case(check: str, case: str, attempt: Callable[[Path], Attempt], base: Path,
             max_retries: int = MAX_RETRIES, delays: tuple[float, ...] | None = None,
             sleep: Callable[[float], None] = time.sleep) -> dict:
  """Run one pilot case, and again while it ends as tool-error (at most max_retries times).

  Before each rerun it waits as tools/eda_retry.py does (retry_delay).
  """
  delays = retry_delays() if delays is None else delays
  history: list[Attempt] = []
  waits: list[float] = []
  for n in range(1, max_retries + 2):
    if n > 1:
      waits.append(retry_delay(n - 1, delays))
      print(f"pilot {check} {case}: waiting {waits[-1]:g} s before attempt {n}", flush=True)
      sleep(waits[-1])
    where = base / check / re.sub(r"[^A-Za-z0-9_.-]+", "_", case) / f"attempt{n}"
    try:
      a = attempt(where)
    except Exception as exc:  # a broken attempt is a failed case, with its reason
      a = {"status": "fail", "problems": [f"{type(exc).__name__}: {exc}"], "exit_code": None,
           "tool_runs": 0, "transient": []}
    a.setdefault("dir", str(where))
    a["waited_s"] = waits[-1] if n > 1 else 0
    history.append(a)
    print(f"pilot {check} {case} attempt {n}: {a['status']}"
          + (f" ({'; '.join(a['transient'])[:200]})" if a["transient"] else ""), flush=True)
    if a["status"] != "tool-error":
      break
  last = history[-1]
  signatures = [s for a in history for s in a["transient"]]
  return {"check": check, "case": case, "status": last["status"], "attempts": len(history),
          "tool_runs": sum(a["tool_runs"] for a in history), "waits_s": waits, "signatures": signatures,
          "problems": last["problems"], "dir": last["dir"], "history": history}


def vendor_synth(args: list[str], cwd: Path = ROOT_DIR) -> int:
  return subprocess.run([sys.executable, "-m", "tools.vendor_synth", *args], cwd=cwd).returncode


def single_result(work: Path) -> dict:
  results = json.loads((work / "summary.json").read_text(encoding="utf-8"))["results"]
  if len(results) != 1:
    raise RuntimeError(f"{work}: {len(results)} results, expected 1")
  return results[0]


def _log_text(path: Path) -> str:
  return path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""


def _signatures(r: dict, log_text: str) -> list[str]:
  """Transient signatures of a vendor_synth result: its retried runs, and its last run if transient."""
  found = [record_reason(entry) for entry in r.get("retries", [])]
  if r["status"] == "tool-error":
    last = transient_reason(log_text, [vivado_dir()])
    if last is not None and last not in found[-1:]:
      found.append(last)
  return found


def _tool_runs(r: dict) -> int:
  """Tool runs of one vendor_synth case: one record per retried run, the last run too if it persisted."""
  retried = len(r.get("retries", []))
  return retried if retried > MAX_RETRIES else retried + 1


def _toolset_args(toolset: Path | None) -> list[str]:
  return ["--toolset", str(toolset)] if toolset else []


def launcher_attempt(job: str, case: str, toolset: Path | None = None) -> Callable[[Path], Attempt]:
  def attempt(work: Path) -> Attempt:
    vendor_synth(["--config", str(PILOT_CONFIG), "--job", job, "--case", case, "--keep-work",
                  "--work-root", str(work), *_toolset_args(toolset)])
    r = single_result(work)
    record = json.loads((Path(r["work_dir"]) / "run.json").read_text(encoding="utf-8"))
    rc = record.get("return_code")
    problems = list(r.get("problems", []))
    status = r["status"] if r["status"] in ("pass", "tool-error") else "fail"
    if status == "pass":
      if case == "r_w_32x1024" and rc != 0:
        status, problems = "fail", [f"exit code {rc}, expected 0"]
      elif case.startswith("bad_") and rc in (0, None):
        status, problems = "fail", [f"exit code {rc}, expected non-zero"]
    elif status == "fail" and not problems:
      problems = [f"{r['status']}: {r.get('message', '')}"]
    return {"status": status, "problems": problems, "exit_code": rc, "work_dir": r["work_dir"],
            "tool_runs": _tool_runs(r), "transient": _signatures(r, _log_text(Path(r["log"])))}
  return attempt


def generic_1000_attempt(source: Path, toolset: Path | None = None) -> Callable[[Path], Attempt]:
  """source: the case directory of the passing r_w_32x1024 run."""
  def attempt(work: Path) -> Attempt:
    case_dir = work / "pilot_artix7" / "r_w_32x1024"
    shutil.copytree(source, case_dir, ignore=shutil.ignore_patterns(".Xil", "*.attempt*"))
    script = case_dir / "run_vivado.tcl"
    text = script.read_text(encoding="utf-8")
    if text.count("-generic {g_ram_depth=1024}") != 1:
      raise RuntimeError("the script of r_w_32x1024 has no -generic {g_ram_depth=1024}")
    # The copied script names the directory of the original run.
    for old_dir in {source.resolve().as_posix(), source.as_posix()}:
      text = text.replace(old_dir, case_dir.resolve().as_posix())
    if source.resolve().as_posix() in text:
      raise RuntimeError("the copied script still names the original directory")
    script.write_text(text.replace("-generic {g_ram_depth=1024}", "-generic {g_ram_depth=1000}"), encoding="utf-8")
    for old in (case_dir / "reports").glob("*"):
      old.unlink()
    record_file = case_dir / "run.json"
    record = json.loads(record_file.read_text(encoding="utf-8"))
    record.update(started_at=dt.datetime.now(dt.timezone.utc).isoformat(), retries=[], persisted=False)
    # The script records the outcome of synth_design under this run id.
    record["run_id"] = uuid.uuid4().hex
    env = {**os.environ, RUN_ID_ENV: record["run_id"]}
    vivado = vivado_dir() / "bin" / ("vivado.bat" if sys.platform == "win32" else "vivado")
    cmd = (["cmd", "/c", str(vivado)] if sys.platform == "win32" else [str(vivado)]) + [
      "-mode", "batch", "-nojournal", "-source", script.resolve().as_posix()]
    with (case_dir / "run.log").open("w", encoding="utf-8") as handle, \
         heavy_tool_lock("pilot: r_w_32x1024 with g_ram_depth=1000 in the script", kind="synthesis"):
      handle.write("command: " + " ".join(cmd) + "\n")
      handle.flush()
      record["return_code"] = run_tool(cmd, cwd=case_dir, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                       timeout=1800)
    record["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    record_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    vendor_synth(["--config", str(PILOT_CONFIG), "--job", "pilot-artix7", "--case", "r_w_32x1024",
                  "--evaluate-only", "--work-root", str(work), *_toolset_args(toolset)])
    r = single_result(work)
    base = {"exit_code": record["return_code"], "tool_runs": 1, "work_dir": str(case_dir),
            "transient": _signatures(r, _log_text(case_dir / "run.log")),
            "bram_tiles": r["metrics"].get("bram_tiles"), "reported": r["problems"]}
    if r["status"] == "tool-error":
      return {**base, "status": "tool-error", "problems": r["problems"]}
    problems = []
    if r["status"] != "fail":
      problems.append(f"status {r['status']}, expected fail")
    if not any("g_ram_depth: requested 1024, synthesized with 1000" in p for p in r["problems"]):
      problems.append("no generic mismatch reported: " + "; ".join(r["problems"]))
    if r["metrics"].get("bram_tiles") != 1.0:
      problems.append(f"bram_tiles {r['metrics'].get('bram_tiles')}, expected 1 (same occupancy)")
    return {**base, "status": "fail" if problems else "pass", "problems": problems}
  return attempt


def old_code_status(log_text: str) -> tuple[str, str | None]:
  """Result of a run of the old code, by the rules of tools/eda_retry.py.

  pass: synthesis completed; tool-error: a transient signature and no error
  of the project (the reason is returned); fail: anything else.
  """
  if COMPLETED in log_text:
    return "pass", None
  reason = retry_reason(log_text, completed=False, roots=[vivado_dir()])
  return ("tool-error", reason) if reason else ("fail", None)


def prepare_old_code(base: Path) -> tuple[Path, Path]:
  """The source tree of OLD_COMMIT and a vendor_synth configuration of OLD_CASES."""
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
    "name": OLD_JOB, "enabled": True, "tool": "vivado", "vendor": "amd", "family": "artix7",
    "part": "xc7a35tfgg484-2", "install_dir": vivado_dir().as_posix(), "manifest_flow": "synth-unsupported",
    "threads": 2, "timeout_seconds": 1800, "cases": [c["name"] for c in OLD_CASES]}]}), encoding="utf-8")
  return old_tree, config


def old_code_attempt(old_tree: Path, config: Path, case: str) -> Callable[[Path], Attempt]:
  def attempt(work: Path) -> Attempt:
    vendor_synth(["--config", str(config), "--job", OLD_JOB, "--case", case, "--keep-work",
                  "--work-root", str(work)], cwd=old_tree)
    r = single_result(work)
    text = _log_text(Path(r["log"]))
    status, reason = old_code_status(text)
    # The old vendor_synth retries with its own (older) rules: its records
    # are signatures of this attempt; the result is decided above.
    transient = _signatures({**r, "status": status}, text)
    if status == "pass":
      problems = []
    elif status == "tool-error":
      problems = [f"transient tool failure: {reason}"]
    else:
      problems = [f"the old code did not complete synthesis, without a transient signature (status {r['status']}): "
                  + "; ".join(r.get("problems", []))[:300]]
    return {"status": status, "problems": problems, "exit_code": r.get("return_code"), "work_dir": r["work_dir"],
            "tool_runs": _tool_runs(r), "transient": transient}
  return attempt


MakeAttempt = Callable[[list[dict]], Callable[[Path], Attempt]]


def run_pilot(base: Path, plan: list[tuple[str, str, MakeAttempt]], max_retries: int = MAX_RETRIES,
              delays: tuple[float, ...] | None = None, sleep: Callable[[float], None] = time.sleep) -> dict:
  """Run the cases of plan ((check, case, make_attempt)) in order; a fail stops the rest.

  make_attempt(cases done so far) returns the attempt function of the case,
  or raises PilotStop when a case it needs did not pass.
  """
  delays = retry_delays() if delays is None else delays
  cases: list[dict] = []
  stopped = None

  def not_run(check: str, case: str, why: str) -> dict:
    return {"check": check, "case": case, "status": "not-run", "attempts": 0, "tool_runs": 0, "waits_s": [],
            "signatures": [], "problems": [f"not run: {why}"], "history": []}

  for check, case, make_attempt in plan:
    if stopped is not None:
      cases.append(not_run(check, case, f"{stopped} failed"))
      continue
    try:
      attempt = make_attempt(cases)
    except PilotStop as exc:
      cases.append(not_run(check, case, str(exc)))
      continue
    entry = run_case(check, case, attempt, base, max_retries, delays, sleep)
    cases.append(entry)
    if entry["status"] == "fail":
      stopped = f"{check} {case}"
  report: dict = {"max_retries": max_retries, "retry_delays_s": list(delays), "cases": cases}
  for check in CHECKS:
    mine = [c for c in cases if c["check"] == check]
    problems = [f"{c['case']}: {c['status']}"
                + (f" after {c['attempts']} attempts" if c["status"] == "tool-error" else "")
                + (f": {'; '.join(c['problems'])}" if c["problems"] else "")
                for c in mine if c["status"] != "pass"]
    report[check] = {"ok": bool(mine) and not problems, "problems": problems}
  report["ok"] = bool(cases) and all(c["status"] == "pass" for c in cases)
  return report


def default_plan(base: Path, toolset: Path | None = None) -> list[tuple[str, str, MakeAttempt]]:
  def launcher(job: str, case: str) -> MakeAttempt:
    return lambda done: launcher_attempt(job, case, toolset)

  def generic_1000(done: list[dict]) -> Callable[[Path], Attempt]:
    # The tampered run starts from the passing r_w_32x1024 run of the launcher.
    entry = next((c for c in done if c["case"] == "pilot-artix7/r_w_32x1024"), None)
    if entry is None or entry["status"] != "pass":
      raise PilotStop("launcher pilot-artix7/r_w_32x1024 did not pass")
    return generic_1000_attempt(Path(entry["history"][-1]["work_dir"]), toolset)

  old: dict[str, Path] = {}

  def old_code(case: str) -> MakeAttempt:
    def make(done: list[dict]) -> Callable[[Path], Attempt]:
      if not old:
        old["tree"], old["config"] = prepare_old_code(base)
      return old_code_attempt(old["tree"], old["config"], case)
    return make

  plan = [("launcher", f"{job}/{case}", launcher(job, case)) for job, case in LAUNCHER_CASES]
  plan.append(("generic_1000_for_1024", "pilot-artix7/r_w_32x1024 g_ram_depth=1000", generic_1000))
  plan += [("old_code", f"{OLD_JOB}/{c['name']}", old_code(c["name"])) for c in OLD_CASES]
  return plan


def main(argv: list[str] | None = None) -> int:
  argv = sys.argv[1:] if argv is None else argv
  parser = argparse.ArgumentParser(prog="python -m tools.campaign.pilot", description="Pilot of the AMD campaign.")
  parser.add_argument("base", type=Path, help="pilot directory")
  parser.add_argument("--toolset", type=Path, help="toolset of the campaign (logs/toolset.json)")
  args = parser.parse_args(argv)
  base, toolset = args.base, (args.toolset.resolve() if args.toolset else None)
  base.mkdir(parents=True, exist_ok=True)
  report = run_pilot(base, default_plan(base, toolset))
  report["commit_of_old_code"] = OLD_COMMIT
  (base / "pilot.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
  for check in CHECKS:
    print(f"pilot {check}: {'ok' if report[check]['ok'] else 'FAILED'}", flush=True)
    for problem in report[check]["problems"]:
      print(f"  {problem}", flush=True)
  return 0 if report["ok"] else 1


if __name__ == "__main__":
  raise SystemExit(main())
