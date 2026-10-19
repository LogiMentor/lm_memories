# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Result extraction and comparison with expected results for vendor_synth.

A synthesis case passes only when the extracted results match the expected
results for that case and part; the tool exit code alone never makes a pass.
"""

from __future__ import annotations

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

_PARAM_RE = re.compile(r"^\s*Parameter\s+(\S+)\s+bound to:\s*(.*?)\s+-\s+type:\s*(\S+)")
_MODULE_RE = re.compile(r"synthesizing module '([^']+)'")
_LIMIT_RE = re.compile(r"\[Common 17-14\] Message '([^']+)' appears 100 times")
_VERSION_RE = re.compile(r"\bVivado v(\d{4}\.\d+(?:\.\d+)?)")


def load_cases(path: Path = CASES_FILE) -> list[dict[str, Any]]:
  with path.open("rb") as handle:
    return list(tomllib.load(handle).get("cases", []))


def load_expected(vendor: str) -> dict[str, Any]:
  name = VENDOR_FILES.get(vendor.lower())
  path = EXPECTED_DIR / f"{name}.toml" if name else None
  if path is None or not path.is_file():
    return {"defaults": {}, "expect": []}
  with path.open("rb") as handle:
    data = tomllib.load(handle)
  return {"defaults": data.get("defaults", {}), "expect": data.get("expect", [])}


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
    "stopped": any(line.startswith("synth_design failed") for line in lines),
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
    was unsuccessful"), does not report completion, and, when the exit code
    is known, it is not 0.
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
    stopped = any(line.startswith("synth_design failed") for line in lines)
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
  if return_code == 0:
    problems.append("tool exit code 0, but synthesis was expected to stop")
  if not accepted:
    problems.append(f"no error of the tool names the failing check (expected: {expected})")
  if not elaborated:
    problems.append("no evidence that the tool elaborated the file of the check")
  facts = {"stopped": stopped and not completed, "elaborated": elaborated, "fallback_messages": accepted[:3],
           "errors": errors[:5], "check_lines": [f"{p.as_posix()}:{a}" for p, a, _ in places],
           "mode": "quartus" if _quartus(tool) else ("vivado -assert" if assert_mode else "vivado")}
  return problems, facts
