# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Last step of the AMD campaign: one more run of the cases that ended as tool-error.

  python -m tools.campaign.rerun <campaign root>

A tool-error is a run that says nothing about the design: the tool stopped
before a result (for example "couldn't read file" of Vivado's own Tcl in
every attempt, PLAN 3.15). Each such case runs again once, in its own work
root under <campaign root>/rerun, with --campaign so it is archived like the
first run. A case that failed (status fail, a wrong result) never runs again.

- vendor_synth steps (vs, fc, qc): every result with status "tool-error",
  one run per job and case, with the configuration of its step.

The campaign name of each rerun is that of its step with "_rerun", from the
toolset of the campaign root (logs/toolset.json, tools/campaign/toolset.py).
- post-synthesis (ps): every synthesis recorded with tool_error, one run per
  part with those configurations.

Writes <campaign root>/rerun/rerun.json: for each case the first status and
problems, the command, and the status of the rerun. Exit code 0 only if every
rerun passed (also when there was nothing to run again).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

from tools.campaign import toolset

ROOT_DIR = Path(__file__).resolve().parents[2]
# Work root of each vendor_synth step in tools/campaign/run_amd.sh and its
# configuration. The campaign names come from the toolset of the campaign
# root (tools/campaign/toolset.py).
SYNTH_STEPS = {
  "vivado": ("vs", "tools/vendor_synth/vivado_check.toml"),
  "checks_vivado": ("fc", "tools/vendor_synth/fallback_check.toml"),
  "checks_quartus": ("qc", "tools/vendor_synth/quartus_check.toml"),
}


def _load(path: Path) -> dict | None:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return None


def _safe(text: str) -> str:
  return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


def tool_errors(root: Path) -> list[dict]:
  """Cases of the campaign that ended as tool-error, with what reruns them."""
  todo = []
  campaigns = toolset.names(root)
  for step, (sub, config) in SYNTH_STEPS.items():
    campaign = campaigns[step]
    data = _load(root / sub / "summary.json")
    for r in (data or {}).get("results", []):
      if r.get("status") != "tool-error":
        continue
      work = root / "rerun" / sub / _safe(r["job"]) / _safe(r["case"])
      todo.append({"step": step, "job": r["job"], "part": r.get("part", ""), "case": r["case"],
                   "first_status": r["status"], "first_problems": r.get("problems", []), "work_root": str(work),
                   "command": [sys.executable, "-m", "tools.vendor_synth", "--config", config, "--job", r["job"],
                               "--case", r["case"], "--campaign", f"{campaign}_rerun", "--work-root", str(work),
                               "--toolset", str(root / "logs" / toolset.TOOLSET_FILE)]})
  data = _load(root / "ps" / "results.json") or {}
  for part, pd in data.get("parts", {}).items():
    names = [name for name, s in pd.get("synth", {}).items() if s.get("tool_error")]
    if not names:
      continue
    configs = _postsynth_configs(data, part, names)
    work = root / "rerun" / "ps" / _safe(part)
    pattern = "^(?:" + "|".join(re.escape(c) for c in configs) + ")$"
    todo.append({"step": "postsynth", "job": "postsynth", "part": part, "case": ",".join(configs),
                 "first_status": "tool-error", "first_problems": [p for n in names
                                                                  for p in pd["synth"][n].get("problems", [])],
                 "work_root": str(work),
                 "command": [sys.executable, "sim/scripts/run_postsynth.py", "--part", part, "--config", pattern,
                             "--campaign", f"{campaigns['postsynth']}_rerun", "--work-root", str(work)]})
  return todo


def _postsynth_configs(data: dict, part: str, keys: list[str]) -> list[str]:
  """Configuration names (sim/vunit/configs.py) of the synthesis keys of a part."""
  sys.path.insert(0, str(ROOT_DIR / "sim" / "scripts"))
  sys.path.insert(0, str(ROOT_DIR / "sim" / "vunit"))
  from configs import RAM_CONFIGS  # noqa: E402
  import run_postsynth  # noqa: E402
  wanted = set(keys)
  return [name for name, g in RAM_CONFIGS if run_postsynth.netlist_key(name, g) in wanted
          and name in data.get("configs", [name])]


def rerun_status(item: dict) -> tuple[str, list[str]]:
  """Status of the rerun of one item, from the result files it wrote."""
  work = Path(item["work_root"])
  if item["step"] == "postsynth":
    data = _load(work / "results.json")
    if data is None:
      return "not-run", ["no results.json"]
    problems = []
    pd = data.get("parts", {}).get(item["part"], {})
    for key, s in pd.get("synth", {}).items():
      if not s.get("ok"):
        problems.append(f"synthesis {key}: {s.get('problems')}")
    for sim, res in pd.get("sims", {}).items():
      if "error" in res:
        problems.append(f"{sim}: {res['error']}")
      for name, r in res.get("results", {}).items():
        if r["verdict"] != "PASS":
          problems.append(f"{sim} {name}: {r['verdict']} {r.get('problems')}")
    if not pd:
      problems.append("no result for this part")
    return ("pass" if not problems else "fail"), problems
  data = _load(work / "summary.json")
  if data is None or not data.get("results"):
    return "not-run", ["no summary.json"]
  r = data["results"][0]
  return r["status"], r.get("problems", [])


def _key(item: dict) -> tuple:
  return (item["step"], item["job"], item["part"], item["case"])


def main(argv: list[str] | None = None) -> int:
  """python -m tools.campaign.rerun <root> [--steps STEP ...] [--fresh STEP ...]

  --steps (a resumed campaign, run_amd.sh with LM_CAMPAIGN_RESUME): only the
  tool-errors of these steps; the items of the other steps in rerun.json are
  kept. An earlier rerun that passed is kept and not run again, unless its
  step ran again from scratch (--fresh): then every tool-error of it runs.
  """
  argv = sys.argv[1:] if argv is None else argv
  root = Path(argv[0])
  options: dict[str, list[str]] = {"--steps": [], "--fresh": []}
  current = None
  for arg in argv[1:]:
    if arg in options:
      current = arg
    elif current is None:
      raise SystemExit(f"rerun: unexpected argument {arg}")
    else:
      options[current].append(arg)
  steps, fresh = options["--steps"], set(options["--fresh"])
  try:
    todo = tool_errors(root)
  except toolset.ToolsetError as exc:
    print(f"rerun: {exc}", file=sys.stderr)
    return 2
  kept: list[dict] = []
  if steps:
    earlier = (_load(root / "rerun" / "rerun.json") or {}).get("items", [])
    kept = [i for i in earlier if i["step"] not in steps]
    passed = {_key(i): i for i in earlier if i["step"] in steps and i["step"] not in fresh
              and i.get("rerun_status") == "pass"}
    todo = [i for i in todo if i["step"] in steps]
    kept += [passed[_key(i)] for i in todo if _key(i) in passed]
    todo = [i for i in todo if _key(i) not in passed]
    for i in kept:
      print(f"kept {i['step']} {i['job']} {i['case']}: rerun {i.get('rerun_status')} earlier", flush=True)
  stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
  for item in todo:
    work = Path(item["work_root"])
    if work.exists() and any(work.iterdir()):
      # An earlier rerun of a resumed campaign: kept beside, never deleted.
      work.rename(work.with_name(f"{work.name}.before_{stamp}"))
    print(f"rerun {item['step']} {item['job']} {item['case']} (first: {item['first_status']})", flush=True)
    item["exit_code"] = subprocess.run(item["command"], cwd=ROOT_DIR).returncode
    item["rerun_status"], item["rerun_problems"] = rerun_status(item)
    print(f"  -> {item['rerun_status']}", flush=True)
  out = root / "rerun"
  out.mkdir(parents=True, exist_ok=True)
  items = kept + todo
  ok = all(item.get("rerun_status") == "pass" for item in items)
  (out / "rerun.json").write_text(json.dumps({"ok": ok, "items": items}, indent=1) + "\n", encoding="utf-8")
  print(f"{len(todo)} tool-error case(s) run again; {'all passed' if ok else 'not all passed'}")
  return 0 if ok else 1


if __name__ == "__main__":
  raise SystemExit(main())
