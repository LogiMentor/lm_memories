# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Summary of an AMD campaign (tools/campaign/run_amd.sh).

  python -m tools.campaign.summarize <campaign root>

Reads the step log (logs/steps.txt), the pilot report, the result files
of every step and the rerun of tool-errors (rerun/rerun.json,
tools/campaign/rerun.py), archives the driver logs and the pilot
(tools/eda_archive.py), and writes <campaign root>/summary.json and
summary.md. Exit code 0 only if the pilot and every step passed.

A step whose only non-passing cases were tool-errors passes when the rerun
of each of them passed; the summary lists every rerun with its first and
second result. The memory thresholds of the EDA lock in effect for the
campaign (logs/steps.txt) are part of the summary.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tools.eda_archive import archive_campaign


def _load(path: Path) -> dict | None:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return None


def _synthesis(root: Path, name: str, reruns: list[dict]) -> dict:
  """reruns: the items of rerun.json for this step (job, case, rerun_status)."""
  data = _load(root / name / "summary.json")
  if data is None:
    return {"ok": False, "detail": "no summary.json"}
  archive = (_load(root / name / "summary_public.json") or {}).get("archive")
  rerun_of = {(i["job"], i["case"]): i for i in reruns}
  counts: dict[str, int] = {}
  bad = []
  for r in data["results"]:
    status = r["status"]
    # Only a tool-error is run again; its rerun gives the result.
    item = rerun_of.get((r["job"], r["case"])) if status == "tool-error" else None
    if item is not None:
      status = item.get("rerun_status", "not-run")
    counts[status] = counts.get(status, 0) + 1
    if status != "pass":
      problems = item.get("rerun_problems", []) if item else r.get("problems", [])
      note = " (rerun of a tool-error)" if item else ""
      bad.append(f"{r['part']} {r['case']}: {status}{note} {'; '.join(problems)[:200]}")
  return {"ok": not bad and bool(data["results"]), "counts": counts, "total": len(data["results"]),
          "reruns": len(reruns), "failures": bad, "archive": archive}


def _postsynth(root: Path, reruns: list[dict]) -> dict:
  data = _load(root / "ps" / "results.json")
  if data is None:
    return {"ok": False, "detail": "no results.json"}
  rerun_parts = {i["part"]: i for i in reruns}
  runs, bad = 0, []
  for sim, res in data.get("rtl", {}).items():
    for name, r in res.get("results", {}).items():
      runs += 1
      if r["verdict"] != "PASS":
        bad.append(f"RTL {sim} {name}: {r['verdict']} {r.get('problems')}")
  for part, pd in data.get("parts", {}).items():
    item = rerun_parts.get(part)
    for key, s in pd["synth"].items():
      rerun_passed = bool(s.get("tool_error") and item and item.get("rerun_status") == "pass")
      if not s["ok"] and not rerun_passed:
        note = (f" (rerun: {item.get('rerun_status')} {item.get('rerun_problems')})"
                if item and s.get("tool_error") else "")
        bad.append(f"{part} synthesis {key}: {s.get('problems')}{note}")
    for sim, res in pd["sims"].items():
      if "error" in res:
        bad.append(f"{part} {sim}: {res['error']}")
      for name, r in res.get("results", {}).items():
        runs += 1
        if r["verdict"] != "PASS":
          bad.append(f"{part} {sim} {name}: {r['verdict']} {r.get('problems')}")
  return {"ok": not bad and runs > 0, "runs": runs, "reruns": len(reruns), "failures": bad,
          "archive": data.get("archive")}


def _vunit(root: Path, name: str) -> dict:
  data = _load(root / "vu" / name / "results.json")
  if data is None:
    return {"ok": False, "detail": "no results.json"}
  return {"ok": not data["problems"] and data["total"] > 0, "passed": data["passed"], "total": data["total"],
          "simulator": data["simulator"], "failures": data["problems"][:20], "archive": data.get("archive")}


def _mutation(root: Path) -> dict:
  data = _load(root / "mut" / "ghdl_6.0.0" / "results.json")
  if data is None:
    return {"ok": False, "detail": "no results.json"}
  detected = sum(m["verdict"] == "DETECTED" for m in data["mutations"])
  bad = [f"{m['name']}: {m['verdict']}" for m in data["mutations"] if m["verdict"] != "DETECTED"]
  ok = data["baseline"]["verdict"] == "PASS" and not bad and bool(data["mutations"])
  return {"ok": ok, "baseline": data["baseline"]["verdict"], "detected": detected, "total": len(data["mutations"]),
          "failures": bad, "archive": data.get("archive")}


def _cell(text: str) -> str:
  return text.replace("|", "\\|").replace("\n", " ")


def pilot_lines(pilot: dict) -> list[str]:
  """The pilot section: the result of each check, then each case with its attempts (tools/campaign/pilot.py)."""
  lines = []
  for name in ("launcher", "generic_1000_for_1024", "old_code"):
    entry = pilot.get(name)
    if entry is None:
      lines.append(f"- {name}: not run")
      continue
    lines.append(f"- {name}: {'ok' if entry['ok'] else 'FAILED'}")
    lines += [f"  - {p}" for p in entry["problems"]]
  cases = pilot.get("cases")
  if not cases:
    return lines
  delays = ", ".join(f"{d:g}" for d in pilot.get("retry_delays_s", [])) or "?"
  lines += ["", f"Each case that ended as tool-error ran again, at most {pilot.get('max_retries', '?')} times, "
            f"after a wait of {delays} s (tools/eda_retry.py); a fail stops the pilot. Tool runs: the attempts "
            "of the pilot and the retries of vendor_synth inside each attempt. Signatures: every transient "
            "failure met.", "",
            "| Check | Case | Result | Attempts | Waits (s) | Tool runs | Transient signatures | Problems |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
  for c in cases:
    signatures = "<br>".join(_cell(s[:160]) for s in c["signatures"]) or "-"
    problems = _cell("; ".join(c["problems"])[:200]) or "-"
    waits = ", ".join(f"{w:g}" for w in c.get("waits_s", [])) or "-"
    lines.append(f"| {c['check']} | {c['case']} | {c['status']} | {c['attempts']} | {waits} | {c['tool_runs']} | "
                 f"{signatures} | {problems} |")
  return lines


def main(argv: list[str] | None = None) -> int:
  argv = sys.argv[1:] if argv is None else argv
  root = Path(argv[0])
  steps_text = (root / "logs" / "steps.txt").read_text(encoding="utf-8") if (root / "logs" / "steps.txt").is_file() else ""
  exit_codes = {m.group(1): int(m.group(2)) for m in re.finditer(r"end (\S+) rc=(\d+)", steps_text)}
  commit = re.search(r"start commit (\S+)", steps_text)
  memory = re.search(r"memory thresholds: (.*)", steps_text)
  waits = re.search(r"retry waits: (.*)", steps_text)
  pilot = _load(root / "pilot" / "pilot.json") or {"ok": False, "detail": "no pilot.json"}
  rerun = _load(root / "rerun" / "rerun.json")
  reruns = (rerun or {}).get("items", [])
  summary = {"commit": commit.group(1) if commit else "unknown",
             "memory_thresholds": memory.group(1).strip() if memory else "not recorded",
             "retry_waits": waits.group(1).strip() if waits else "not recorded",
             "pilot": pilot, "exit_codes": exit_codes, "steps": {}, "rerun": rerun}
  if pilot.get("ok"):
    by_step = {name: [i for i in reruns if i["step"] == name]
               for name in ("vivado", "checks_vivado", "checks_quartus", "postsynth")}
    summary["steps"] = {
      "vivado": _synthesis(root, "vs", by_step["vivado"]),
      "checks_vivado": _synthesis(root, "fc", by_step["checks_vivado"]),
      "checks_quartus": _synthesis(root, "qc", by_step["checks_quartus"]),
      "postsynth": _postsynth(root, by_step["postsynth"]),
      "vunit_ghdl": _vunit(root, "ghdl_6.0.0"),
      "vunit_questa": _vunit(root, "questa_2021.1"),
      "mutation": _mutation(root),
    }
  # The driver logs and the pilot, archived like every campaign step.
  summary["driver_archive"] = archive_campaign("amd_campaign_driver", root / "logs", root / "pilot")
  def step_ok(name: str, s: dict) -> bool:
    # A step that exited non-zero for tool-errors passes only if they were
    # run again and passed (s["ok"] already counts the rerun results).
    if not s["ok"]:
      return False
    return exit_codes.get(name) == 0 or (s.get("reruns", 0) > 0 and exit_codes.get("rerun_tool_errors") == 0)

  summary["ok"] = bool(pilot.get("ok")) and bool(summary["steps"]) and all(
    step_ok(name, s) for name, s in summary["steps"].items())
  (root / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")

  lines = [f"# AMD campaign: {'PASS' if summary['ok'] else 'FAIL'}", "",
           f"Commit: `{summary['commit']}`", "",
           f"Memory thresholds of the EDA lock: {summary['memory_thresholds']}", "",
           f"Waits before a rerun of a transient tool failure: {summary['retry_waits']}", "", "## Pilot", ""]
  lines += pilot_lines(pilot)
  lines += ["", "## Steps", "", "| Step | Exit code | Result | Archive (SHA-256) |", "| --- | --- | --- | --- |"]
  for name, s in summary["steps"].items():
    detail = {k: s[k] for k in ("counts", "total", "runs", "reruns", "passed", "detected", "baseline") if k in s}
    archive = s.get("archive") or {}
    lines.append(f"| {name} | {exit_codes.get(name, '-')} | {'ok' if s['ok'] else 'FAILED'} {json.dumps(detail)} | "
                 f"{archive.get('archive', '-')} ({archive.get('sha256', '-')}) |")
  for name, s in summary["steps"].items():
    if s.get("failures"):
      lines += ["", f"### Failures: {name}", ""] + [f"- {f}" for f in s["failures"][:30]]
  lines += ["", "## Rerun of tool-errors", ""]
  if rerun is None:
    lines.append("Not run (no rerun/rerun.json).")
  elif not reruns:
    lines.append("No case ended as tool-error: nothing was run again.")
  else:
    lines += [f"Exit code: {exit_codes.get('rerun_tool_errors', '-')}. Each tool-error ran once more; "
              "a fail is never run again.", "",
              "| Step | Part | Job | Case | First result | Rerun |", "| --- | --- | --- | --- | --- | --- |"]
    for i in reruns:
      def result(status: str, problems: list[str]) -> str:
        return status + (f": {'; '.join(problems)[:120]}" if problems else "")

      first = result(i["first_status"], i["first_problems"])
      second = result(i.get("rerun_status", "not-run"), i.get("rerun_problems", []))
      lines.append(f"| {i['step']} | {i['part']} | {i['job']} | {i['case']} | {first} | {second} |")
  d = summary["driver_archive"]
  lines += ["", f"Driver logs and pilot: {d['archive']} ({d['sha256']}), {d['files']} files.", ""]
  (root / "summary.md").write_text("\n".join(lines), encoding="utf-8")
  print((root / "summary.md").read_text(encoding="utf-8"))
  return 0 if summary["ok"] else 1


if __name__ == "__main__":
  raise SystemExit(main())
