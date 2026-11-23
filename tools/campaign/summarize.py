# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Summary of an AMD campaign (tools/campaign/run_amd.sh).

  python -m tools.campaign.summarize <campaign root>

Reads the step log (logs/steps.txt), the pilot report, the result files
of every step, the tool versions (logs/toolset.json,
tools/campaign/toolset.py) and the rerun of tool-errors (rerun/rerun.json,
tools/campaign/rerun.py), archives the driver logs and the pilot
(tools/eda_archive.py), and writes <campaign root>/summary.json and
summary.md. Exit code 0 only if the pilot and every step passed.

A vendor_synth result compared with expected results not yet measured
([meta] basis) is "pass-provisional": it does not fail its step, but the
campaign is not a pass (summary "ok" false, exit code 1); the summary says
so at the top ("# AMD campaign: PROVISIONAL" when nothing else failed).

A step whose only non-passing cases were tool-errors passes when the rerun
of each of them passed; the summary lists every rerun with its first and
second result. The memory thresholds of the EDA lock in effect for the
campaign (logs/steps.txt) are part of the summary. A vendor_synth or
post-synthesis result with another tool version than the toolset fails its
step: for post-synthesis, its Vivado synthesis and its simulator (the GHDL
of the toolset, the xsim of its Vivado; a simulation with results must
record its version). Reruns included: a rerun is judged by its own result
file (rerun/<step>/..., tools/campaign/rerun.py), and a passed rerun
without it fails. Without a toolset the campaign fails.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tools.campaign import toolset
from tools.campaign.rerun import _safe
from tools.eda_archive import archive_campaign


# Status of a vendor_synth result compared with expected results not yet
# measured ([meta] basis, tools/vendor_synth/cli.py _evaluate).
PROVISIONAL = "pass-provisional"


def _load(path: Path) -> dict | None:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return None


def _version_problem(found: str | None, expected: str, what: str) -> str | None:
  """A tool version of a result that is not the one of the campaign toolset (logs/toolset.json)."""
  if found and found != "unknown" and found != expected:
    return f"{what}: tool version {found}, the campaign toolset has {expected}"
  return None


def _synthesis(root: Path, name: str, reruns: list[dict], version: str) -> dict:
  """reruns: the items of rerun.json for this step (job, case, rerun_status).
  version: the tool version of the campaign toolset; every result that
  records a tool version must have this one, and a pass must record it."""
  data = _load(root / name / "summary.json")
  if data is None:
    return {"ok": False, "detail": "no summary.json"}
  archive = (_load(root / name / "summary_public.json") or {}).get("archive")
  rerun_of = {(i["job"], i["case"]): i for i in reruns}
  counts: dict[str, int] = {}
  bad = []
  provisional_files: set[str] = set()
  for r in data["results"]:
    status, result = r["status"], r
    # Only a tool-error is run again; its rerun gives the result, read from
    # the result file of the rerun (rerun.py), with its own tool version.
    item = rerun_of.get((r["job"], r["case"])) if status == "tool-error" else None
    if item is not None:
      path = root / "rerun" / name / _safe(r["job"]) / _safe(r["case"]) / "summary.json"
      found_results = (_load(path) or {}).get("results") or []
      result = found_results[0] if found_results else None
      status = result["status"] if result else item.get("rerun_status", "not-run")
      if result is None:
        bad.append(f"{r['part']} {r['case']}: no result of the rerun in {path}")
    counts[status] = counts.get(status, 0) + 1
    if status == PROVISIONAL:
      # Compared with expected results not yet measured: a measurement, not a
      # failure of the step and not a pass of the campaign (main).
      provisional_files.add(((result or {}).get("expected") or {}).get("file") or "?")
    elif status != "pass":
      problems = (result or {}).get("problems", []) if item is None or result else item.get("rerun_problems", [])
      note = " (rerun of a tool-error)" if item else ""
      bad.append(f"{r['part']} {r['case']}: {status}{note} {'; '.join(problems)[:200]}")
    if result is not None:
      found = "" if result.get("tool_version") in (None, "", "unknown") else result["tool_version"]
      what = f"{r['part']} {r['case']}" + (" (rerun)" if item else "")
      wrong = _version_problem(found, version, what)
      if wrong is None and status in ("pass", PROVISIONAL) and not found:
        wrong = f"{what}: pass without a tool version"
      if wrong:
        bad.append(wrong)
  return {"ok": not bad and bool(data["results"]), "counts": counts, "total": len(data["results"]),
          "reruns": len(reruns), "provisional": counts.get(PROVISIONAL, 0),
          "provisional_files": sorted(provisional_files), "failures": bad, "archive": archive}


def _postsynth(root: Path, reruns: list[dict], versions: dict[str, str]) -> dict:
  """versions: tool -> version of the campaign toolset. Every synthesis must
  be of its Vivado, every simulation with results of its GHDL or of the xsim
  of its Vivado (toolset.simulator_problem), reruns included."""
  data = _load(root / "ps" / "results.json")
  if data is None:
    return {"ok": False, "detail": "no results.json"}
  vivado = versions["vivado"]
  rerun_parts = {i["part"]: i for i in reruns}
  runs, bad = 0, []

  def simulators(sims: dict, what: str) -> None:
    for sim, res in sims.items():
      problem = toolset.simulator_problem(sim, res.get("sim"), versions) if res.get("results") else None
      if problem:
        bad.append(f"{what} {sim}: {problem}")

  simulators(data.get("rtl", {}), "RTL")
  for part, pd in data.get("parts", {}).items():
    for key, s in pd.get("synth", {}).items():
      wrong = _version_problem(s.get("version"), vivado, f"{part} synthesis {key}")
      if wrong:
        bad.append(wrong)
    simulators(pd.get("sims", {}), part)
  # The syntheses and simulations of the reruns, with their own tool versions.
  for part, item in rerun_parts.items():
    path = root / "rerun" / "ps" / _safe(part) / "results.json"
    rerun_data = _load(path)
    if rerun_data is None:
      if item.get("rerun_status") == "pass":
        bad.append(f"{part}: no result of the rerun in {path}")
      continue
    rerun_part = rerun_data.get("parts", {}).get(part, {})
    for key, s in rerun_part.get("synth", {}).items():
      wrong = _version_problem(s.get("version"), vivado, f"{part} synthesis {key} (rerun)")
      if wrong:
        bad.append(wrong)
    simulators(rerun_part.get("sims", {}), f"{part} (rerun)")
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


def _mutation(root: Path, name: str) -> dict:
  data = _load(root / "mut" / name / "results.json")
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
  # A resumed campaign appends its own lines: the last ones are in effect.
  memory = ([None] + list(re.finditer(r"memory thresholds: (.*)", steps_text)))[-1]
  waits = ([None] + list(re.finditer(r"retry waits: (.*)", steps_text)))[-1]
  resumes = [{"commit": m.group(1), "steps": m.group(2).split()}
             for m in re.finditer(r"resume commit (\S+) steps: (.*)", steps_text)]
  pilot = _load(root / "pilot" / "pilot.json") or {"ok": False, "detail": "no pilot.json"}
  rerun = _load(root / "rerun" / "rerun.json")
  reruns = (rerun or {}).get("items", [])
  try:
    tools = toolset.load(root)
    names = toolset.campaign_names(tools)
  except (toolset.ToolsetError, KeyError, TypeError) as exc:
    tools, names, no_toolset = None, None, f"not recorded, the campaign cannot pass ({exc})"
  summary = {"commit": commit.group(1) if commit else "unknown",
             "memory_thresholds": memory.group(1).strip() if memory else "not recorded",
             "retry_waits": waits.group(1).strip() if waits else "not recorded", "resumes": resumes,
             "toolset": toolset.versions(tools) if tools else None, "campaigns": names,
             "pilot": pilot, "exit_codes": exit_codes, "steps": {}, "rerun": rerun}
  if pilot.get("ok") and tools:
    by_step = {name: [i for i in reruns if i["step"] == name]
               for name in ("vivado", "checks_vivado", "checks_quartus", "postsynth")}
    vivado, quartus = tools["tools"]["vivado"]["version"], tools["tools"]["quartus"]["version"]
    summary["steps"] = {
      "vivado": _synthesis(root, "vs", by_step["vivado"], vivado),
      "checks_vivado": _synthesis(root, "fc", by_step["checks_vivado"], vivado),
      "checks_quartus": _synthesis(root, "qc", by_step["checks_quartus"], quartus),
      "postsynth": _postsynth(root, by_step["postsynth"],
                              {tool: entry["version"] for tool, entry in tools["tools"].items()}),
      "vunit_ghdl": _vunit(root, names["vunit_ghdl"]),
      "vunit_questa": _vunit(root, names["vunit_questa"]),
      "mutation": _mutation(root, names["mutation"]),
    }
  # The driver logs and the pilot, archived like every campaign step.
  summary["driver_archive"] = archive_campaign("amd_campaign_driver", root / "logs", root / "pilot")
  def step_ok(name: str, s: dict) -> bool:
    # A step that exited non-zero for tool-errors passes only if they were
    # run again and passed (s["ok"] already counts the rerun results). A
    # step with provisional results exits non-zero for them alone.
    if not s["ok"]:
      return False
    return (exit_codes.get(name) == 0 or s.get("provisional", 0) > 0
            or (s.get("reruns", 0) > 0 and exit_codes.get("rerun_tool_errors") == 0))

  provisional = {name: s["provisional"] for name, s in summary["steps"].items() if s.get("provisional")}
  provisional_files = sorted({f for s in summary["steps"].values() for f in s.get("provisional_files", [])})
  summary["provisional"] = provisional
  passed = bool(pilot.get("ok")) and bool(summary["steps"]) and all(
    step_ok(name, s) for name, s in summary["steps"].items())
  # Provisional results never make a pass of the campaign.
  summary["ok"] = passed and not provisional
  verdict = "PASS" if summary["ok"] else "PROVISIONAL" if passed else "FAIL"
  (root / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")

  lines = [f"# AMD campaign: {verdict}", ""]
  if provisional:
    count = sum(provisional.values())
    lines += [f"**Not a pass:** {count} result{'s' if count != 1 else ''} "
              f"({', '.join(f'{name} {n}' for name, n in provisional.items())}) "
              "compared with expected results not yet measured ([meta] basis of "
              f"{', '.join(f'`{f}`' for f in provisional_files)}). They are measurements: write the measured values "
              "in that file, remove [meta] basis and evaluate the logs again (LM_CAMPAIGN_RESUME).", ""]
  lines += [f"Commit: `{summary['commit']}`", ""]
  for r in resumes:
    lines += [f"Resumed on commit `{r['commit']}`: {', '.join(r['steps'])} (results evaluated again or run "
              "again; the other steps are those of the first commit).", ""]
  if tools:
    lines += ["Tools: " + "; ".join(f"{tool} {entry['version']}" + (f" ({entry['edition']})" if entry.get("edition") else "")
                                    for tool, entry in summary["toolset"].items()), ""]
  else:
    lines += [f"Tools: {no_toolset}", ""]
  lines += [
           f"Memory thresholds of the EDA lock: {summary['memory_thresholds']}", "",
           f"Waits before a rerun of a transient tool failure: {summary['retry_waits']}", "", "## Pilot", ""]
  lines += pilot_lines(pilot)
  lines += ["", "## Steps", "", "| Step | Exit code | Result | Archive (SHA-256) |", "| --- | --- | --- | --- |"]
  for name, s in summary["steps"].items():
    detail = {k: s[k] for k in ("counts", "total", "runs", "reruns", "passed", "detected", "baseline") if k in s}
    archive = s.get("archive") or {}
    state = "FAILED" if not s["ok"] else "provisional" if s.get("provisional") else "ok"
    lines.append(f"| {name} | {exit_codes.get(name, '-')} | {state} {json.dumps(detail)} | "
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
