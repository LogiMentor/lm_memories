# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Write docs/results/ from a finished AMD campaign (tools/campaign/run_amd.sh).

  python -m tools.campaign.publish <campaign root> [--out docs/results]

Reads the result files of every step of the campaign root and of the rerun
of tool-errors, and writes one JSON file per step to the output directory,
plus amd_campaign.json (the campaign: commits, steps, every archive) and
the generated table of docs/results/README.md (between the markers
<!-- publish:begin --> and <!-- publish:end -->).

Every result records the commit it ran on, the tool and its version, the
device, and the archive of its complete logs with the SHA-256. Each archive
is checked first: the file must be in the archive directory
(tools/eda_archive.py) and its SHA-256 must match the record, otherwise
nothing is written. A case that ended as tool-error and passed when run
again (tools/campaign/rerun.py) is published with the rerun, its archive and
its commit; its first run is kept under "first_run". A step evaluated again
on a later commit (LM_CAMPAIGN_RESUME, tools/campaign/resume.py) records
both commits: "commit" (the tool run) and "evaluated_on".

Local paths are removed: the repository (paths become relative), the
campaign root (<campaign>), the tool installations (<vivado>, <quartus>) and
the host name in Vivado's .Xil directories. A path that is still there stops
the script.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from tools.campaign.rerun import POSTSYNTH_CAMPAIGN, ROOT_DIR, SYNTH_STEPS, _safe
from tools.eda_archive import archive_dir, sha256_file

# Output file of each vendor_synth step; device column of the README.
SYNTH_FILES = {
  "vivado": "vivado_2023.1_amd.json",
  "checks_vivado": "static_checks_vivado_2023.1.json",
  "checks_quartus": "static_checks_quartus_23.1.json",
}
POSTSYNTH_FILE = "postsynth_vivado_2023.1_amd.json"
CAMPAIGN_FILE = "amd_campaign.json"
BEGIN, END = "<!-- publish:begin -->", "<!-- publish:end -->"
_QUARTUS_VERSION_RE = re.compile(r"Info: Version (\S+ Build \d+) .*Edition")
_LOCAL_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|[\\/]Users[\\/]|/home/")


class PublishError(Exception):
  pass


def _load(path: Path) -> Any:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return None


def _path_re(path: Path | str) -> str:
  parts = re.split(r"[\\/]+", str(path).rstrip("\\/"))
  return r"[\\/]+".join(re.escape(p) for p in parts)


def scrubber(campaign_root: Path):
  """Function that removes the local paths of this machine from a string."""
  rules = [
    (re.compile(_path_re(ROOT_DIR) + r"[\\/]+", re.IGNORECASE), ""),
    (re.compile(_path_re(ROOT_DIR), re.IGNORECASE), "."),
    (re.compile(_path_re(campaign_root.resolve()), re.IGNORECASE), "<campaign>"),
    (re.compile(_path_re(campaign_root), re.IGNORECASE), "<campaign>"),
    (re.compile(r"[A-Za-z]:[\\/]+Xilinx[\\/]+Vivado[\\/]+[\d.]+", re.IGNORECASE), "<vivado>"),
    (re.compile(r"[A-Za-z]:[\\/]+intelFPGA[^\\/\s\"']*[\\/]+[\d.]+", re.IGNORECASE), "<quartus>"),
    (re.compile(r"\bVivado-\d+-[^\\/\s\"']+"), "Vivado-<pid>-<host>"),
  ]

  def scrub(value: Any) -> Any:
    if isinstance(value, str):
      for pattern, repl in rules:
        value = pattern.sub(repl, value)
      return value
    if isinstance(value, list):
      return [scrub(v) for v in value]
    if isinstance(value, dict):
      return {k: scrub(v) for k, v in value.items()}
    return value
  return scrub


def local_paths(value: Any, where: str = "") -> list[str]:
  """Strings that still hold a local path (where: the JSON path)."""
  if isinstance(value, str):
    return [f"{where}: {value[:160]}"] if _LOCAL_RE.search(value) else []
  if isinstance(value, list):
    return [p for i, v in enumerate(value) for p in local_paths(v, f"{where}[{i}]")]
  if isinstance(value, dict):
    return [p for k, v in value.items() for p in local_paths(v, f"{where}.{k}")]
  return []


def verify_archive(record: dict | None, directory: Path) -> dict:
  """The archive record, after its file and SHA-256 were checked."""
  if not record or not record.get("archive"):
    raise PublishError("a result has no archive record")
  path = directory / record["archive"]
  if not path.is_file():
    raise PublishError(f"archive {record['archive']} not found in {directory}")
  digest = sha256_file(path)
  if digest != record.get("sha256"):
    raise PublishError(f"archive {record['archive']}: SHA-256 {digest}, the record says {record.get('sha256')}")
  return {"archive": record["archive"], "sha256": digest, "files": record.get("files")}


def _before_commits(work: Path) -> dict[tuple[str, str], str]:
  """Commit of the tool run of each case of a step evaluated again (its first summary)."""
  befores = sorted(work.glob("summary.before_*.json"))
  if not befores:
    return {}
  data = _load(befores[0]) or {}
  return {(r["job"], r["case"]): r.get("provenance", {}).get("commit", "")
          for r in data.get("results", [])}


def _quartus_version(work: Path) -> str:
  versions = set()
  for log in work.rglob("run.log"):
    versions.update(_QUARTUS_VERSION_RE.findall(log.read_text(encoding="utf-8", errors="ignore")))
  if len(versions) != 1:
    raise PublishError(f"Quartus version in the logs of {work}: {sorted(versions) or 'none'}")
  return versions.pop()


def _synth_entry(job: str, public: dict, archive: dict, evaluated_on: str | None, run_commit: str | None,
                 version: str) -> dict:
  entry = {"job": job, **public}
  if version and not entry.get("tool_version"):
    # vendor_synth does not record the Quartus version: read from run.log
    # ("Info: Version 23.1std.0 Build 991 ... Lite Edition", quartus_map).
    entry["tool_version"] = version
  if run_commit and run_commit != entry.get("commit"):
    entry["evaluated_on"] = entry["commit"]
    entry["commit"] = run_commit
  elif evaluated_on:
    entry["evaluated_on"] = evaluated_on
  entry["archive"] = archive
  return entry


def synth_step(root: Path, step: str, reruns: list[dict], directory: Path) -> dict:
  """Published results of one vendor_synth step, the passed reruns in place of the tool-errors."""
  sub, _, _ = SYNTH_STEPS[step]
  work = root / sub
  full = _load(work / "summary.json")
  public = _load(work / "summary_public.json")
  if not full or not public:
    raise PublishError(f"{step}: no summary.json or summary_public.json in {work}")
  archive = verify_archive(public.get("archive"), directory)
  before = _before_commits(work)
  version = _quartus_version(work) if step == "checks_quartus" else ""
  rerun_of = {(i["job"], i["case"]): i for i in reruns if i["step"] == step}
  results = []
  for r, p in zip(full["results"], public["results"], strict=True):
    key = (r["job"], r["case"])
    entry = _synth_entry(r["job"], p, archive, None, before.get(key), version)
    item = rerun_of.get(key)
    if r["status"] == "tool-error" and item is not None:
      rerun_work = root / "rerun" / sub / _safe(r["job"]) / _safe(r["case"])
      rerun_public = _load(rerun_work / "summary_public.json")
      if not rerun_public or not rerun_public.get("results"):
        raise PublishError(f"{step} {r['job']} {r['case']}: no rerun result in {rerun_work}")
      first = {"status": entry["status"], "problems": entry["problems"], "commit": entry["commit"],
               "archive": archive}
      entry = _synth_entry(r["job"], rerun_public["results"][0],
                           verify_archive(rerun_public.get("archive"), directory), None, None, version)
      entry["first_run"] = first
    results.append(entry)
  counts: dict[str, int] = {}
  for e in results:
    counts[e["status"]] = counts.get(e["status"], 0) + 1
  return {"step": step, "counts": counts, "total": len(results), "results": results}


def _config_of_key(configs: list[str]) -> dict[str, str]:
  sys.path.insert(0, str(ROOT_DIR / "sim" / "scripts"))
  sys.path.insert(0, str(ROOT_DIR / "sim" / "vunit"))
  from configs import RAM_CONFIGS  # noqa: E402
  import run_postsynth  # noqa: E402
  return {run_postsynth.netlist_key(name, g): name for name, g in RAM_CONFIGS if name in configs}


def _ps_runs(data: dict, archive: dict, configs_only: set[str] | None = None, parts_only: set[str] | None = None,
             rtl: bool = True) -> list[dict]:
  """Flat results of a run_postsynth results.json: RTL runs, then netlist runs per part."""
  key_of = {v: k for k, v in _config_of_key(data["configs"]).items()}
  out = []

  def sim_entries(sims: dict, part: str | None, synth: dict | None):
    for sim, res in sims.items():
      for config, r in res.get("results", {}).items():
        if configs_only is not None and config not in configs_only:
          continue
        entry = {"config": config, "model": "netlist" if part else "rtl", "part": part, "simulator": sim,
                 "simulator_version": res.get("sim", ""), "verdict": r["verdict"], "counts": r.get("counts", {}),
                 "problems": r.get("problems", [])}
        if part:
          s = (synth or {}).get(key_of[config], {})
          prov = s.get("provenance", {})
          entry["collisions"] = {k: v for k, v in r.get("collisions", {}).items() if k != "block_rams"}
          entry["synthesis"] = {"tool": "vivado", "tool_version": s.get("version", ""), "key": key_of[config],
                                "metrics": s.get("metrics", {}), "critical_warnings": s.get("critical_warnings"),
                                "retries": len(s.get("retries", []))}
          entry["commit"] = prov.get("commit", data.get("commit", ""))
          entry["sources_hash"] = prov.get("sources_hash", "")
        else:
          entry["commit"] = data.get("commit", "")
        entry["manifest_hash"] = data.get("manifest_hash", "")
        entry["archive"] = archive
        out.append(entry)
      if "error" in res:
        out.append({"model": "netlist" if part else "rtl", "part": part, "simulator": sim, "verdict": "ERROR",
                    "problems": [res["error"]], "commit": data.get("commit", ""), "archive": archive})

  if rtl:
    sim_entries(data.get("rtl", {}), None, None)
  for part, pd in data.get("parts", {}).items():
    if parts_only is None or part in parts_only:
      sim_entries(pd.get("sims", {}), part, pd.get("synth", {}))
  return out


def postsynth_step(root: Path, reruns: list[dict], directory: Path) -> dict:
  data = _load(root / "ps" / "results.json")
  if not data:
    raise PublishError(f"postsynth: no results.json in {root / 'ps'}")
  archive = verify_archive(data.get("archive"), directory)
  results = _ps_runs(data, archive)
  config_of = _config_of_key(data["configs"])
  first_runs = []
  for part, pd in data.get("parts", {}).items():
    failed = {k: s for k, s in pd.get("synth", {}).items() if not s.get("ok")}
    for key, s in failed.items():
      config = config_of[key]
      first = {"part": part, "config": config, "status": "tool-error" if s.get("tool_error") else "fail",
               "problems": s.get("problems", []), "commit": s.get("provenance", {}).get("commit", data.get("commit")),
               "archive": archive}
      item = next((i for i in reruns if i["step"] == "postsynth" and i["part"] == part
                   and config in i["case"].split(",")), None)
      if not s.get("tool_error") or item is None:
        results.append({"config": config, "model": "netlist", "part": part, "verdict": "NOT SYNTHESIZED",
                        "problems": first["problems"], "commit": first["commit"], "archive": archive})
        continue
      rerun_data = _load(root / "rerun" / "ps" / _safe(part) / "results.json")
      if not rerun_data:
        raise PublishError(f"postsynth {part} {config}: no rerun result")
      rerun_archive = verify_archive(rerun_data.get("archive"), directory)
      for e in _ps_runs(rerun_data, rerun_archive, {config}, {part}, rtl=False):
        e["first_run"] = first
        results.append(e)
      first_runs.append(first)
  verdicts: dict[str, int] = {}
  for e in results:
    verdicts[e["verdict"]] = verdicts.get(e["verdict"], 0) + 1
  return {"step": "postsynth", "campaign": POSTSYNTH_CAMPAIGN, "configs": data["configs"],
          "verdicts": verdicts, "total": len(results), "results": results}


def sim_step(root: Path, step: str, work: Path, directory: Path) -> dict:
  """VUnit or mutation results: one simulator, no device."""
  data = _load(work / "results.json")
  if not data:
    raise PublishError(f"{step}: no results.json in {work}")
  tool, _, version = work.name.partition("_")
  return {"step": step, "tool": tool, "tool_version": version, "part": None, **data,
          "archive": verify_archive(data.get("archive"), directory)}


def _sim_dirs(root: Path) -> list[tuple[str, Path, str]]:
  out = [(f"vunit_{d.name.split('_')[0]}", d, f"vunit_{d.name}.json") for d in sorted((root / "vu").glob("*_*"))]
  out += [("mutation", d, f"mutation_{d.name}.json") for d in sorted((root / "mut").glob("*_*"))]
  return out


def _postsynth_counts(step: dict, published: dict) -> dict:
  """The counts of the post-synthesis step, each with its meaning (summary.json says only "runs" and "reruns")."""
  rerun_runs = sum(1 for r in published["results"] if r.get("first_run"))
  out = {k: v for k, v in step.items() if k not in ("runs", "reruns")}
  out.update(main_step_runs=step.get("runs"), reruns=step.get("reruns"), rerun_netlist_runs=rerun_runs,
             consolidated_results=published["total"])
  out["meaning"] = ("main_step_runs: simulation runs of the post-synthesis step itself, RTL and netlist, on both "
                    "simulators (a synthesis that ended as tool-error has no netlist run there); reruns: syntheses "
                    "that ended as tool-error and ran again (tools/campaign/rerun.py); rerun_netlist_runs: the "
                    "netlist runs of those reruns; consolidated_results: the results published in "
                    f"{POSTSYNTH_FILE} = main_step_runs + rerun_netlist_runs")
  if step.get("runs") is not None and step["runs"] + rerun_runs != published["total"]:
    raise PublishError(f"postsynth: {step['runs']} runs of the step + {rerun_runs} of its reruns are not the "
                       f"{published['total']} published results")
  return out


def collect(root: Path, directory: Path | None = None) -> dict[str, dict]:
  """Every published file: name -> content, local paths removed and checked."""
  directory = directory or archive_dir()
  summary = _load(root / "summary.json")
  if not summary:
    raise PublishError(f"no summary.json in {root}: run tools/campaign/summarize.py first")
  if not summary.get("ok"):
    raise PublishError(f"the campaign in {root} did not pass (summary.json)")
  reruns = (_load(root / "rerun" / "rerun.json") or {}).get("items", [])
  files = {name: synth_step(root, step, reruns, directory) for step, name in SYNTH_FILES.items()}
  files[POSTSYNTH_FILE] = postsynth_step(root, reruns, directory)
  for step, work, name in _sim_dirs(root):
    files[name] = sim_step(root, step, work, directory)
  driver = verify_archive(summary.get("driver_archive"), directory)
  archives = {}
  for name, content in files.items():
    for record in _archives(content):
      archives.setdefault(record["archive"], {**record, "files_published": []})["files_published"].append(name)
  superseded = []
  for old in sorted(root.glob("ps.before_*")) + sorted((root / "rerun" / "ps").glob("*.before_*")):
    record = (_load(old / "results.json") or {}).get("archive")
    if record:
      superseded.append({**verify_archive(record, directory), "commit": (_load(old / "results.json") or {}).get("commit"),
                         "reason": "post-synthesis run again on the commit of the resume"})
  pilot = summary.get("pilot") or {}
  files[CAMPAIGN_FILE] = {
    "commit": summary["commit"], "resumes": summary.get("resumes", []), "ok": summary["ok"],
    "memory_thresholds": summary.get("memory_thresholds"), "retry_waits": summary.get("retry_waits"),
    "pilot": {"ok": pilot.get("ok"), "commit_of_old_code": pilot.get("commit_of_old_code"),
              "cases": [{k: c.get(k) for k in ("check", "case", "status", "attempts", "tool_runs", "signatures")}
                        for c in pilot.get("cases", [])]},
    "exit_codes": summary.get("exit_codes", {}),
    "steps": {k: {kk: vv for kk, vv in v.items() if kk not in ("archive", "failures")}
              for k, v in summary.get("steps", {}).items()},
    "archives": sorted(archives.values(), key=lambda a: a["archive"]),
    "superseded_archives": superseded, "driver_archive": driver,
  }
  steps = files[CAMPAIGN_FILE]["steps"]
  if "postsynth" in steps:
    steps["postsynth"] = _postsynth_counts(steps["postsynth"], files[POSTSYNTH_FILE])
  scrub = scrubber(root)
  files = {name: scrub(content) for name, content in files.items()}
  leftover = [p for name, content in files.items() for p in local_paths(content, name)]
  if leftover:
    raise PublishError("local paths left after scrubbing:\n  " + "\n  ".join(leftover[:10]))
  return files


def _archives(value: Any) -> list[dict]:
  if isinstance(value, dict):
    if set(value) >= {"archive", "sha256"} and isinstance(value["archive"], str):
      return [{k: value[k] for k in ("archive", "sha256", "files") if k in value}]
    return [a for v in value.values() for a in _archives(v)]
  if isinstance(value, list):
    return [a for v in value for a in _archives(v)]
  return []


def _short(commit: str | None) -> str:
  return f"`{commit[:7]}`" if commit else "-"


def table(files: dict[str, dict]) -> str:
  """Markdown table of docs/results/README.md: one row per published file."""
  rows = ["| File | Result | Commit | Tool and version | Device | Archives (SHA-256) |",
          "| --- | --- | --- | --- | --- | --- |"]
  for name, content in files.items():
    if name == CAMPAIGN_FILE:
      continue
    results = content.get("results")
    if isinstance(results, list):
      if "verdicts" in content:
        result = ", ".join(f"{n} {v}" for v, n in sorted(content["verdicts"].items())) + f" of {content['total']}"
        tools = sorted({f"vivado {(e.get('synthesis') or {}).get('tool_version', '')}".strip() for e in results
                        if e.get("synthesis")}
                       | {e.get("simulator_version") or e.get("simulator", "") for e in results if e.get("simulator")})
        parts = sorted({e["part"] for e in results if e.get("part")}) + ["RTL (no device)"]
      else:
        result = ", ".join(f"{n} {s}" for s, n in sorted(content["counts"].items())) + f" of {content['total']}"
        tools = sorted({f"{e['tool']} {e.get('tool_version', '')}".strip() for e in results})
        parts = sorted({e["part"] for e in results})
      commits = sorted({e.get("commit", "") for e in results})
      evaluated = sorted({e["evaluated_on"] for e in results if e.get("evaluated_on")})
      commit = ", ".join(_short(c) for c in commits)
      if evaluated:
        commit += "; evaluated on " + ", ".join(_short(c) for c in evaluated)
    elif "tests" in content:
      result = f"{content['passed']} of {content['total']} pass"
      tools, parts, commit = [f"{content['tool']} {content['tool_version']}"], ["- (simulation)"], _short(content["commit"])
    else:
      baseline = (content.get("baseline") or {}).get("verdict")
      muts = content.get("mutations", [])
      detected = sum(1 for m in muts if m.get("verdict") == "DETECTED")
      result = f"{detected} of {len(muts)} mutations detected, baseline {baseline}"
      tools, parts, commit = [f"{content['tool']} {content['tool_version']}"], ["- (simulation)"], _short(content["commit"])
    archives = "<br>".join(f"`{a['archive']}` (`{a['sha256']}`)" for a in _unique(_archives(content)))
    rows.append(f"| [`{name}`]({name}) | {result} | {commit} | {', '.join(tools)} | {', '.join(f'`{p}`' if p[0] != '-' and ' ' not in p else p for p in parts)} | {archives} |")
  return "\n".join(rows)


def _unique(records: list[dict]) -> list[dict]:
  """One record per archive: the run of the step first, then its reruns by time."""
  by_name = {r["archive"]: r for r in records}
  return [by_name[n] for n in sorted(by_name, key=lambda n: ("_rerun_" in n, n))]


def write(files: dict[str, dict], out: Path) -> None:
  out.mkdir(parents=True, exist_ok=True)
  for name, content in files.items():
    (out / name).write_bytes((json.dumps(content, indent=1) + "\n").encode("utf-8"))
  readme = out / "README.md"
  text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
  generated = f"{BEGIN}\n{table(files)}\n{END}"
  if BEGIN in text and END in text:
    text = text[:text.index(BEGIN)] + generated + text[text.index(END) + len(END):]
  else:
    text = text.rstrip("\n") + "\n\n" + generated + "\n"
  readme.write_bytes(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
  argv = sys.argv[1:] if argv is None else argv
  if not argv:
    raise SystemExit(__doc__)
  root = Path(argv[0])
  out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else ROOT_DIR / "docs" / "results"
  try:
    files = collect(root)
  except PublishError as error:
    print(f"publish: {error}", file=sys.stderr)
    return 1
  write(files, out)
  for name in files:
    print(f"wrote {out / name}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
