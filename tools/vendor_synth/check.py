# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Result extraction and comparison with expected results for vendor_synth.

A synthesis case passes only when the extracted results match the expected
results for that case and part; the tool exit code alone never makes a pass.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from tools.eda_retry import project_error

EXPECTED_DIR = Path(__file__).resolve().parent / "expected"
CASES_FILE = Path(__file__).resolve().parent / "cases.toml"

VENDOR_FILES = {
  "amd": "amd", "xilinx": "amd",
  "intel": "intel", "altera": "intel",
  "lattice": "lattice",
  "microchip": "microchip", "microsemi": "microchip",
}

# Printed by the Vivado scripts of vendor_synth and run_postsynth.py when
# synth_design returns an error; they then exit with 1 (vivado_synth_command).
SYNTH_FAILED_MARKER = "lm_vendor_synth: synth_design failed:"

# Status file written by the Vivado scripts: one JSON line per event of each
# synth_design ("started", then "ok" or "failed"), with the run id of the
# attempt (environment variable RUN_ID_ENV, set by the caller), the error
# message of a failure and a UTC timestamp. It is the evidence of the outcome
# that does not depend on the exit code of the process (PLAN 3.15).
STATUS_FILE = "synth_status.jsonl"
RUN_ID_ENV = "LM_RUN_ID"

_STATUS_TCL = r"""proc lm_json_string {text} {
  set text [string map [list "\\" "\\\\" "\"" "\\\"" "\n" "\\n" "\r" "\\r" "\t" "\\t"] $text]
  return [regsub -all {[\x01-\x1f]} $text { }]
}
proc lm_synth_status {outcome message} {
  set run_id [expr {[info exists ::env(@RUN_ID_ENV@)] ? $::env(@RUN_ID_ENV@) : ""}]
  set time [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%SZ} -gmt 1]
  set handle [open {@STATUS@} a]
  puts $handle "{\"run_id\": \"[lm_json_string $run_id]\", \"step\": \"synth_design\", \"outcome\": \"$outcome\", \"time\": \"$time\", \"message\": \"[lm_json_string $message]\"}"
  close $handle
}
lm_synth_status started {}
if {[catch {@COMMAND@} result]} {
  lm_synth_status failed $result
  puts "@MARKER@ $result"
  exit 1
}
lm_synth_status ok {}"""


def vivado_synth_command(command: str, status_path: str) -> str:
  """synth_design in a Vivado batch script, with its status file.

  status_path: path of the status file, as a Tcl path (forward slashes).
  The outcome goes to the status file (STATUS_FILE); a failure also prints
  the marker and ends the script with "exit 1". Vivado 2023.1 exits with 1
  after a failed synth_design anyway; the exit code reaches the caller only
  through a launcher that keeps it (PLAN 3.15), so the status file is the
  evidence and the exit code a second one where it is reliable.
  """
  return (_STATUS_TCL.replace("@RUN_ID_ENV@", RUN_ID_ENV).replace("@STATUS@", status_path)
          .replace("@MARKER@", SYNTH_FAILED_MARKER).replace("@COMMAND@", command))


def read_synth_status(path: Path, run_id: str | None) -> tuple[dict[str, Any] | None, list[str]]:
  """Outcome of the last synth_design of the attempt run_id in the status file.

  Returns (record, problems). record is the last "ok" or "failed" event of
  that run id, or the "started" event when synth_design never returned (the
  tool crashed or was stopped); None when the file has no event of this run.
  Events of other run ids (an earlier attempt or run) are ignored.
  """
  if not run_id:
    return None, ["no run id for this attempt, so the status file cannot be matched to it"]
  if not path.is_file():
    return None, [f"no status file {path.name}: the script did not record the outcome of synth_design"]
  problems, events = [], []
  for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
    if not line.strip():
      continue
    try:
      event = json.loads(line)
    except json.JSONDecodeError:
      problems.append(f"{path.name}:{number}: not a JSON line")
      continue
    if isinstance(event, dict) and event.get("run_id") == run_id and event.get("step") == "synth_design":
      events.append(event)
  if not events:
    problems.append(f"{path.name}: no event of run {run_id}")
    return None, problems
  finished = [event for event in events if event.get("outcome") in ("ok", "failed")]
  return (finished[-1] if finished else events[-1]), problems


def synth_died(path: Path, run_id: str | None, log_text: str = "") -> str | None:
  """Why the tool died inside synth_design, or None.

  The script writes "started" before synth_design and "ok" or "failed" after
  it returns. A run of this run id that ends with "started" never returned
  from synth_design: the tool died (2026-11-04: Vivado printed the stop of
  the check, "synth_design failed", then "called Tcl_Close on channel with
  refCount > 0" and exited with 3). Whatever the log says, such a run has no
  result. A missing file or run id is not a death (read_synth_status reports
  it).
  """
  record, _ = read_synth_status(path, run_id)
  if record is None or record.get("outcome") != "started":
    return None
  lines = [line.strip() for line in log_text.splitlines() if line.strip()]
  last = f"; last log line: {lines[-1][:120]}" if lines else ""
  return f"synth_design started at {record.get('time', '?')} and never returned (status file){last}"


def vivado_exit_code_reliable(os_name: str = os.name) -> bool:
  """True where the exit code of a Vivado run is measured to reach vendor_synth.

  Measured with Vivado 2023.1 on Windows (PLAN 3.15): through launch_tool.bat
  the exit code of Vivado arrives; through the former launch_tool.cmd it was
  always 0. Not measured on Linux (bash launcher with exec), so there it is
  recorded but not required.
  """
  return os_name == "nt"



_PARAM_RE = re.compile(r"^\s*Parameter\s+(\S+)\s+bound to:\s*(.*?)\s+-\s+type:\s*(\S+)")
_MODULE_RE = re.compile(r"synthesizing module '([^']+)'")
_LIMIT_RE = re.compile(r"\[Common 17-14\] Message '([^']+)' appears 100 times")
_VERSION_RE = re.compile(r"\bVivado v(\d{4}\.\d+(?:\.\d+)?)")
# Quartus: "Info: Version 23.1std.0 Build 991 11/28/2023 SC Lite Edition" (each module prints it).
_QUARTUS_VERSION_RE = re.compile(r"\bInfo: Version (\S+ Build \d+)\b")


def quartus_version(text: str) -> str:
  """Version of Quartus in its log ("23.1std.0 Build 991"); "unknown" if absent, "mixed: ..." if two differ."""
  versions = sorted(set(_QUARTUS_VERSION_RE.findall(text)))
  if not versions:
    return "unknown"
  return versions[0] if len(versions) == 1 else "mixed: " + ", ".join(versions)


def load_cases(path: Path = CASES_FILE) -> list[dict[str, Any]]:
  with path.open("rb") as handle:
    return list(tomllib.load(handle).get("cases", []))


class ExpectedError(Exception):
  """No expected results can be chosen for a run: a fail of the run, never a fallback to another version."""


def expected_key(vendor: str, version: str) -> str:
  """Name of the expected-results file of a tool version (without .toml):
  Vivado "2024.1" -> "2024.1"; Quartus "25.1std.0 Build 1129" -> "25.1std"
  (the release: the expected results do not change with the build)."""
  if VENDOR_FILES.get(vendor.lower()) == "intel":
    release = re.match(r"\d+\.\d+[A-Za-z]*", version)
    return release.group(0) if release else version
  return version


def expected_path(vendor: str, version: str) -> Path | None:
  """expected/<vendor>/<key>.toml, or None for a vendor without expected results."""
  name = VENDOR_FILES.get(vendor.lower())
  return EXPECTED_DIR / name / f"{expected_key(vendor, version)}.toml" if name else None


def load_expected(vendor: str, version: str | None) -> dict[str, Any]:
  """Expected results of a vendor for one tool version (expected/<vendor>/<key>.toml).

  A vendor without a directory of expected results has none (its cases are
  "unchecked"). For a vendor with expected results, an unknown version, a
  version without its own file, or a file whose [meta] tool_version is
  another version raises ExpectedError: a run is never compared with the
  results of another version.
  """
  name = VENDOR_FILES.get(vendor.lower())
  if name is None or not (EXPECTED_DIR / name).is_dir():
    return {"defaults": {}, "expect": []}
  if not version or version == "unknown" or version.startswith("mixed"):
    raise ExpectedError(f"tool version {version or 'unknown'}: no expected results can be chosen")
  key = expected_key(vendor, version)
  path = expected_path(vendor, version)
  if not path.is_file():
    raise ExpectedError(f"no expected results for {name} {key} (tool version {version}): "
                        f"tools/vendor_synth/expected/{name}/{key}.toml does not exist")
  with path.open("rb") as handle:
    data = tomllib.load(handle)
  declared = data.get("meta", {}).get("tool_version")
  if declared != key:
    raise ExpectedError(f"tools/vendor_synth/expected/{name}/{key}.toml: [meta] tool_version is {declared!r}, "
                        f"not {key!r}")
  return {"defaults": data.get("defaults", {}), "expect": data.get("expect", []), "tool_version": key}


def find_expectation(expected: dict[str, Any], case: str, part: str) -> dict[str, Any] | None:
  for entry in expected["expect"]:
    if entry.get("case") == case and part.lower() in [p.lower() for p in entry.get("parts", [])]:
      return {**expected["defaults"], **entry}
  return None


def normalize_generic(value: Any) -> str:
  if isinstance(value, bool):
    return "TRUE" if value else "FALSE"
  if isinstance(value, str) and value == "":
    return "(null)"
  return str(value)


def _same_generic(requested: str, reported: str, vhdl_type: str) -> bool:
  if requested == reported:
    return True
  if requested == "(null)" and reported == "":
    return True  # an empty string, printed as nothing
  if vhdl_type.lower() in ("bool", "boolean"):
    truthy = {"TRUE", "1"}
    return (requested.upper() in truthy) == (reported.upper() in truthy)
  return False


def parse_vivado_log(text: str, top: str) -> dict[str, Any]:
  """Facts from a Vivado batch log. Echoed script lines ('# ...') are ignored."""
  lines = [line for line in text.splitlines() if not line.startswith("#")]
  synth_start = next((i for i, line in enumerate(lines) if line.startswith("Command: synth_design")), None)
  synth_lines = lines[synth_start:] if synth_start is not None else []

  # Generics of each module, as bound at its first elaboration.
  module_generics: dict[str, dict[str, tuple[str, str]]] = {}
  for i, line in enumerate(lines):
    match = _MODULE_RE.search(line)
    if match and match.group(1).lower() not in module_generics:
      params: dict[str, tuple[str, str]] = {}
      for follow in lines[i + 1:]:
        param = _PARAM_RE.match(follow)
        if not param:
          break
        params[param.group(1)] = (param.group(2), param.group(3))
      module_generics[match.group(1).lower()] = params

  version = next((m.group(1) for m in map(_VERSION_RE.search, lines) if m), "unknown")
  return {
    "version": version,
    "synth_ran": synth_start is not None,
    # Positive evidence of the end of synth_design, either way.
    "completed": any(line.startswith("synth_design completed successfully") for line in synth_lines),
    "stopped": any(line.startswith(("synth_design failed", SYNTH_FAILED_MARKER)) for line in lines),
    "critical_warnings": sum(1 for line in synth_lines if line.startswith("CRITICAL WARNING:")),
    "critical_warnings_outside_synth": sum(
      1 for line in lines[:synth_start or 0] if line.startswith("CRITICAL WARNING:")
    ),
    "message_ids": _message_ids(synth_lines),
    "limited_messages": sorted({m.group(1) for m in map(_LIMIT_RE.search, lines) if m}),
    "generics": module_generics.get(top.lower(), {}),
    "module_generics": module_generics,
    "errors": [line for line in lines if line.startswith("ERROR:")][:10],
    "project_error": project_error(text),
    "couldnt_read_file": any("couldn't read file" in line for line in lines),
  }


def _message_ids(lines: list[str]) -> dict[str, int]:
  counts: dict[str, int] = {}
  for line in lines:
    match = re.match(r"^(?:CRITICAL WARNING|WARNING|ERROR|INFO): \[([^\]]+)\]", line)
    if match:
      counts[match.group(1)] = counts.get(match.group(1), 0) + 1
  return counts


def _util_value(text: str, names: tuple[str, ...]) -> float | None:
  for name in names:
    match = re.search(r"^\|\s*" + re.escape(name) + r"\s*\|\s*([0-9.]+)\s*\|", text, re.MULTILINE)
    if match:
      return float(match.group(1))
  return None


def parse_vivado_utilization(text: str) -> dict[str, float | None]:
  """Resource counts from report_utilization (7-series and UltraScale+ names).

  A row that is not in the report gives None, never 0: the report may be
  incomplete. uram is None on families without URAM (see uram_applicable).
  """
  return {
    "luts": _util_value(text, ("Slice LUTs*", "Slice LUTs", "CLB LUTs*", "CLB LUTs")),
    "luts_logic": _util_value(text, ("LUT as Logic",)),
    "lutram": _util_value(text, ("LUT as Memory",)),
    "registers": _util_value(text, ("Slice Registers", "CLB Registers")),
    "bram_tiles": _util_value(text, ("Block RAM Tile",)),
    "ramb36": _util_value(text, ("RAMB36/FIFO*", "RAMB36/FIFO")),
    "ramb18": _util_value(text, ("RAMB18",)),
    "uram": _util_value(text, ("URAM",)),
  }


# Rows every complete utilization report has, on every AMD family.
REQUIRED_METRICS = ("luts", "luts_logic", "lutram", "registers", "bram_tiles")


def uram_applicable(part: str) -> bool:
  """False for AMD families without URAM (7-series parts xc7...), whose report has no URAM row."""
  return not part.lower().startswith("xc7")


def check_generics(requested: dict[str, Any], reported: dict[str, tuple[str, str]]) -> list[str]:
  problems = []
  for name, value in requested.items():
    want = normalize_generic(value)
    if name not in reported:
      problems.append(f"generic {name}: requested {want}, not found in the log")
      continue
    got, vhdl_type = reported[name]
    if not _same_generic(want, got, vhdl_type):
      problems.append(f"generic {name}: requested {want}, synthesized with {got}")
  return problems


def compare(metrics: dict[str, Any], log: dict[str, Any], expectation: dict[str, Any], part: str = "") -> list[str]:
  """Mismatches between results and expectation (empty list = pass)."""
  problems = []
  missing = [key for key in REQUIRED_METRICS if metrics.get(key) is None]
  if missing:
    problems.append("utilization report incomplete: no " + ", ".join(missing))
  exact = (("bram_tiles", "block RAM tiles"), ("ramb36", "RAMB36"), ("ramb18", "RAMB18"),
           ("lutram", "LUTs as memory"), ("uram", "URAM blocks"))
  for key, label in exact:
    if key in expectation:
      got = metrics.get(key)
      if key == "uram" and got is None and not uram_applicable(part):
        got = 0.0  # no URAM on this family: the row is absent, not missing
      elif key == "uram" and got is None:
        problems.append(f"{label}: expected {expectation[key]}, but the report has no URAM row")
        continue
      if got is None or abs(float(got) - float(expectation[key])) > 1e-9:
        problems.append(f"{label}: expected {expectation[key]}, got {got}")
  ceilings = (("max_registers", "registers"), ("max_luts", "luts_logic"))
  for key, metric in ceilings:
    if key in expectation:
      got = metrics.get(metric)
      if got is None or float(got) > float(expectation[key]):
        problems.append(f"{metric}: expected at most {expectation[key]}, got {got}")
  max_cw = int(expectation.get("max_critical_warnings", 0))
  if log["critical_warnings"] > max_cw:
    problems.append(f"CRITICAL WARNINGs: expected at most {max_cw}, got {log['critical_warnings']}")
  for msg_id in expectation.get("forbidden_messages", []):
    count = log["message_ids"].get(msg_id, 0)
    if count:
      problems.append(f"forbidden message [{msg_id}] reported {count} times")
  if log["limited_messages"]:
    problems.append("message limit reached for " + ", ".join(log["limited_messages"]) + "; counts are not complete")
  return problems


_VIVADO_RANGE_RE = re.compile(r"Synth 8-11323\].*\[(?P<path>[^\]]+):(?P<line>\d+)\]")
_SYNTH_MODULE_RE = re.compile(r"\[Synth 8-638\] synthesizing module '[^']+' \[(?P<path>[^\]]+):\d+\]")
_QUARTUS_ERROR_RE = re.compile(r"^\s*Error \(\d+\)")
_QUARTUS_AT_RE = re.compile(r" at (?P<path>[^\s()]+)\((?P<line>\d+)\)")


def locate_constant(name: str, sources: list[Path]) -> list[tuple[Path, int, int]]:
  """Declarations of a VHDL constant: (file, first line, last line), 1-based."""
  found = []
  decl = re.compile(r"^\s*constant\s+" + re.escape(name) + r"\s*:", re.IGNORECASE)
  for path in sources:
    try:
      lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
      continue
    for i, line in enumerate(lines):
      if decl.match(line):
        last = i
        while ";" not in lines[last].split("--")[0] and last + 1 < len(lines):
          last += 1
        found.append((path, i + 1, last + 1))
  return found


def _same_file(reported: str, path: Path) -> bool:
  a = reported.replace("\\", "/").lower()
  b = path.as_posix().lower()
  return a.endswith(b) or b.endswith(a)


def _at_place(path: str, line: int, places: list[tuple[Path, int, int]]) -> bool:
  return any(_same_file(path, p) and first <= line <= last for p, first, last in places)


def _quartus(tool: str) -> bool:
  return tool in ("quartus", "intel", "altera")


def tool_completed(tool: str, text: str) -> bool:
  """Positive evidence in the log that synthesis completed."""
  lines = text.splitlines()
  if _quartus(tool):
    return any("Analysis & Synthesis was successful" in line for line in lines)
  return any(line.startswith("synth_design completed successfully") for line in lines)


def check_expected_failure(
  tool: str,
  log_text: str,
  expectation: dict[str, Any],
  sources: list[Path] | None = None,
  assert_mode: bool = False,
  return_code: int | None = None,
  status: tuple[dict[str, Any] | None, list[str]] | None = None,
  exit_code_required: bool = True,
) -> tuple[list[str], dict[str, Any]]:
  """Expected result "synthesis stops at this check".

  Passes only with positive evidence of each step:
  - the tool elaborated the design file that holds the check (Vivado:
    "synthesizing module" in the file of the constant; Quartus: an error at
    the check, which it reports during elaboration);
  - an error message of the tool names the check:
    - Vivado without -assert: "ERROR: [Synth 8-11323] ... out of range" at
      the file and lines of the constant expect_failure_constant;
    - Vivado with -assert (assert_mode): "ERROR: [Synth 8-63] RTL assertion"
      with one of the texts in expect_failure;
    - Quartus: an "Error (<id>)" line with one of the texts in expect_failure
      (assertion, Error 10652), or at the file and line of the constant
      (Error 10657 failed call of f_static_check, Error 10405 value out of
      range);
    Any other line (an INFO or WARNING, or an error elsewhere) does not count.
  - the tool reports the stop ("synth_design failed", "Analysis & Synthesis
    was unsuccessful") and does not report completion;
  - Vivado: the status file of this attempt records synth_design as
    "failed" (status: the result of read_synth_status);
  - the exit code is not 0 where it is reliable (exit_code_required:
    vivado_exit_code_reliable for Vivado; Quartus keeps the requirement).
    Where it is not required, it is only recorded in the facts.
  Returns (problems, facts); facts holds the tool's actual messages.
  """
  raw = expectation.get("expect_failure", [])
  texts = [str(raw)] if isinstance(raw, str) else [str(item) for item in raw]
  constant = expectation.get("expect_failure_constant")
  places = locate_constant(constant, sources or []) if constant else []
  lines = [line for line in log_text.splitlines() if not line.startswith("#")]
  completed = tool_completed(tool, log_text)
  if _quartus(tool):
    errors = [line.strip() for line in lines if _QUARTUS_ERROR_RE.match(line)]
    stopped = any("Analysis & Synthesis was unsuccessful" in line for line in lines)

    def names_the_check(line: str) -> bool:
      if any(text in line for text in texts):
        return True
      # An error at the declaration of the constant (Error 10405 value out of
      # range, Error 10657 failed call of f_static_check).
      at = _QUARTUS_AT_RE.search(line)
      return bool(at) and _at_place(at["path"], int(at["line"]), places)

    accepted = [line for line in errors if names_the_check(line)]
    # Quartus reports these checks while elaborating the design.
    elaborated = bool(accepted) or any("Elaborating entity" in line for line in lines)
    expected = (" or ".join(repr(t) for t in texts) + (f", or {constant} at its declaration" if constant else ""))
  else:
    errors = [line.strip() for line in lines if line.startswith("ERROR:")]
    stopped = any(line.startswith(("synth_design failed", SYNTH_FAILED_MARKER)) for line in lines)
    if assert_mode:
      accepted = [line for line in errors if "[Synth 8-63]" in line and any(text in line for text in texts)]
      expected = "[Synth 8-63] with " + " or ".join(repr(t) for t in texts)
    else:
      accepted = []
      for line in errors:
        match = _VIVADO_RANGE_RE.search(line)
        if match and _at_place(match["path"], int(match["line"]), places):
          accepted.append(line)
      where = ", ".join(f"{p.as_posix()}:{a}" for p, a, _ in places) or "the constant"
      expected = f"[Synth 8-11323] at {where}"
    files = {p for p, _, _ in places}
    elaborated = any(match and any(_same_file(match["path"], p) for p in files)
                     for match in map(_SYNTH_MODULE_RE.search, lines))

  problems = []
  if constant and not places:
    problems.append(f"constant {constant} not found in the compiled sources")
  if completed:
    problems.append("synthesis completed, but it was expected to stop")
  elif not stopped:
    problems.append("the tool did not report a stop of synthesis")
  outcome = None
  if not _quartus(tool):
    record, status_problems = status if status is not None else (None, ["no status file was read"])
    outcome = record.get("outcome") if record else None
    if outcome != "failed":
      problems.append(f"status file: synth_design of this run is {outcome or 'not recorded'}, expected failed"
                      + (f" ({'; '.join(status_problems)})" if status_problems else ""))
  if exit_code_required and return_code in (0, None):
    problems.append(f"tool exit code {return_code}, but synthesis was expected to stop")
  if not accepted:
    problems.append(f"no error of the tool names the failing check (expected: {expected})")
  if not elaborated:
    problems.append("no evidence that the tool elaborated the file of the check")
  facts = {"stopped": stopped and not completed, "elaborated": elaborated, "fallback_messages": accepted[:3],
           "errors": errors[:5], "check_lines": [f"{p.as_posix()}:{a}" for p, a, _ in places],
           "mode": "quartus" if _quartus(tool) else ("vivado -assert" if assert_mode else "vivado"),
           "status_outcome": outcome, "exit_code": return_code, "exit_code_required": exit_code_required}
  return problems, facts
