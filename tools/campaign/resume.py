# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Evaluate again a vendor_synth step of an AMD campaign with the current checks.

  python -m tools.campaign.resume <campaign root> <step>

Used by tools/campaign/run_amd.sh with LM_CAMPAIGN_RESUME. Runs vendor_synth
--evaluate-only on every case of the step (vivado, checks_vivado or
checks_quartus): no tool runs, the logs and records of the campaign are read
again with the checks of the current commit (vendor_synth refuses a case
whose sources or manifest changed). The summary of the step before is kept
as summary.before_<UTC time>.json, and the archive record of the first run
stays in summary_public.json. A case that becomes a tool-error then runs
again in the rerun step (tools/campaign/rerun.py); a fail stays a fail.

Prints, and writes to <campaign root>/logs/reevaluate_<step>.json, every case
whose status changed. Exit code: that of vendor_synth (0 only if every case
passes).
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

from tools.campaign.rerun import ROOT_DIR, SYNTH_STEPS


def _load(path: Path) -> dict:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return {}


def _statuses(path: Path) -> dict[tuple[str, str], str]:
  return {(r["job"], r["case"]): r["status"] for r in _load(path).get("results", [])}


def reevaluate(root: Path, step: str, run=subprocess.run) -> tuple[int, list[dict]]:
  """Evaluate the step again; returns the exit code of vendor_synth and the changed cases."""
  sub, config = SYNTH_STEPS[step]
  work = root / sub
  if not (work / "summary.json").is_file():
    raise SystemExit(f"resume: no {work / 'summary.json'}: the step never ran in this campaign")
  stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
  before_file = work / f"summary.before_{stamp}.json"
  shutil.copyfile(work / "summary.json", before_file)
  archive = _load(work / "summary_public.json").get("archive")
  code = run([sys.executable, "-m", "tools.vendor_synth", "--config", config, "--keep-going", "--keep-work",
              "--evaluate-only", "--work-root", str(work)], cwd=ROOT_DIR).returncode
  if archive is not None:
    # --evaluate-only writes the summary without the archive of the run it read.
    public = _load(work / "summary_public.json")
    (work / "summary_public.json").write_text(json.dumps({"archive": archive, **public}, indent=2) + "\n",
                                              encoding="utf-8")
  before, after = _statuses(before_file), _statuses(work / "summary.json")
  changed = [{"job": job, "case": case, "before": status, "after": after.get((job, case), "missing")}
             for (job, case), status in before.items() if after.get((job, case)) != status]
  return code, changed


def main(argv: list[str] | None = None) -> int:
  argv = sys.argv[1:] if argv is None else argv
  root, step = Path(argv[0]), argv[1]
  if step not in SYNTH_STEPS:
    raise SystemExit(f"resume: {step} is not a vendor_synth step ({', '.join(SYNTH_STEPS)})")
  code, changed = reevaluate(root, step)
  (root / "logs").mkdir(parents=True, exist_ok=True)
  (root / "logs" / f"reevaluate_{step}.json").write_text(
    json.dumps({"step": step, "exit_code": code, "changed": changed}, indent=1) + "\n", encoding="utf-8")
  for c in changed:
    print(f"reevaluate {step}: {c['job']} {c['case']}: {c['before']} -> {c['after']}")
  print(f"reevaluate {step}: {len(changed)} case(s) changed status")
  return code


if __name__ == "__main__":
  raise SystemExit(main())
