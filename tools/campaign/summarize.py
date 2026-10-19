# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Summary of an AMD campaign (tools/campaign/run_amd.sh).

  python -m tools.campaign.summarize <campaign root>

Reads the step log (logs/steps.txt), the pilot report and the result files
of every step, archives the driver logs and the pilot (tools/eda_archive.py),
and writes <campaign root>/summary.json and summary.md. Exit code 0 only if
the pilot and every step passed.
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


def _synthesis(root: Path, name: str) -> dict:
  data = _load(root / name / "summary_public.json")
  if data is None:
    return {"ok": False, "detail": "no summary_public.json"}
  counts: dict[str, int] = {}
  for r in data["results"]:
    counts[r["status"]] = counts.get(r["status"], 0) + 1
  bad = [f"{r['part']} {r['case']}: {r['status']} {'; '.join(r.get('problems', []))[:200]}"
         for r in data["results"] if r["status"] != "pass"]
  return {"ok": not bad and bool(data["results"]), "counts": counts, "total": len(data["results"]),
          "failures": bad, "archive": data.get("archive")}


def _postsynth(root: Path) -> dict:
  data = _load(root / "ps" / "results.json")
  if data is None:
    return {"ok": False, "detail": "no results.json"}
  runs, bad = 0, []
  for sim, res in data.get("rtl", {}).items():
    for name, r in res.get("results", {}).items():
      runs += 1
      if r["verdict"] != "PASS":
        bad.append(f"RTL {sim} {name}: {r['verdict']} {r.get('problems')}")
  for part, pd in data.get("parts", {}).items():
    for key, s in pd["synth"].items():
      if not s["ok"]:
        bad.append(f"{part} synthesis {key}: {s.get('problems')}")
    for sim, res in pd["sims"].items():
      if "error" in res:
        bad.append(f"{part} {sim}: {res['error']}")
      for name, r in res.get("results", {}).items():
        runs += 1
        if r["verdict"] != "PASS":
          bad.append(f"{part} {sim} {name}: {r['verdict']} {r.get('problems')}")
  return {"ok": not bad and runs > 0, "runs": runs, "failures": bad, "archive": data.get("archive")}


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


def main(argv: list[str] | None = None) -> int:
  argv = sys.argv[1:] if argv is None else argv
  root = Path(argv[0])
  steps_text = (root / "logs" / "steps.txt").read_text(encoding="utf-8") if (root / "logs" / "steps.txt").is_file() else ""
  exit_codes = {m.group(1): int(m.group(2)) for m in re.finditer(r"end (\S+) rc=(\d+)", steps_text)}
  commit = re.search(r"start commit (\S+)", steps_text)
  pilot = _load(root / "pilot" / "pilot.json") or {"ok": False, "detail": "no pilot.json"}
  summary = {"commit": commit.group(1) if commit else "unknown", "pilot": pilot, "exit_codes": exit_codes, "steps": {}}
  if pilot.get("ok"):
    summary["steps"] = {
      "vivado": _synthesis(root, "vs"),
      "checks_vivado": _synthesis(root, "fc"),
      "checks_quartus": _synthesis(root, "qc"),
      "postsynth": _postsynth(root),
      "vunit_ghdl": _vunit(root, "ghdl_6.0.0"),
      "vunit_questa": _vunit(root, "questa_2021.1"),
      "mutation": _mutation(root),
    }
  # The driver logs and the pilot, archived like every campaign step.
  summary["driver_archive"] = archive_campaign("amd_campaign_driver", root / "logs", root / "pilot")
  summary["ok"] = bool(pilot.get("ok")) and bool(summary["steps"]) and all(
    s["ok"] and exit_codes.get(name) == 0 for name, s in summary["steps"].items())
  (root / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")

  lines = [f"# AMD campaign: {'PASS' if summary['ok'] else 'FAIL'}", "",
           f"Commit: `{summary['commit']}`", "", "## Pilot", ""]
  for name in ("launcher", "generic_1000_for_1024", "old_code"):
    entry = pilot.get(name)
    if entry is None:
      lines.append(f"- {name}: not run")
      continue
    lines.append(f"- {name}: {'ok' if entry['ok'] else 'FAILED'}")
    lines += [f"  - {p}" for p in entry["problems"]]
  lines += ["", "## Steps", "", "| Step | Exit code | Result | Archive (SHA-256) |", "| --- | --- | --- | --- |"]
  for name, s in summary["steps"].items():
    detail = {k: s[k] for k in ("counts", "total", "runs", "passed", "detected", "baseline") if k in s}
    archive = s.get("archive") or {}
    lines.append(f"| {name} | {exit_codes.get(name, '-')} | {'ok' if s['ok'] else 'FAILED'} {json.dumps(detail)} | "
                 f"{archive.get('archive', '-')} ({archive.get('sha256', '-')}) |")
  for name, s in summary["steps"].items():
    if s.get("failures"):
      lines += ["", f"### Failures: {name}", ""] + [f"- {f}" for f in s["failures"][:30]]
  d = summary["driver_archive"]
  lines += ["", f"Driver logs and pilot: {d['archive']} ({d['sha256']}), {d['files']} files.", ""]
  (root / "summary.md").write_text("\n".join(lines), encoding="utf-8")
  print((root / "summary.md").read_text(encoding="utf-8"))
  return 0 if summary["ok"] else 1


if __name__ == "__main__":
  raise SystemExit(main())
