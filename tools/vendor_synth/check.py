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

  generics: dict[str, tuple[str, str]] = {}
  for i, line in enumerate(lines):
    match = _MODULE_RE.search(line)
    if match and match.group(1).lower() == top.lower():
      for follow in lines[i + 1:]:
        param = _PARAM_RE.match(follow)
        if not param:
          break
        generics[param.group(1)] = (param.group(2), param.group(3))
      break

  version = next((m.group(1) for m in map(_VERSION_RE.search, lines) if m), "unknown")
  return {
    "version": version,
    "synth_ran": synth_start is not None,
    "critical_warnings": sum(1 for line in synth_lines if line.startswith("CRITICAL WARNING:")),
    "critical_warnings_outside_synth": sum(
      1 for line in lines[:synth_start or 0] if line.startswith("CRITICAL WARNING:")
    ),
    "message_ids": _message_ids(synth_lines),
    "limited_messages": sorted({m.group(1) for m in map(_LIMIT_RE.search, lines) if m}),
    "generics": generics,
    "errors": [line for line in lines if line.startswith("ERROR:")][:10],
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
  """Resource counts from report_utilization (7-series and UltraScale+ names)."""
  return {
    "luts": _util_value(text, ("Slice LUTs*", "Slice LUTs", "CLB LUTs*", "CLB LUTs")),
    "luts_logic": _util_value(text, ("LUT as Logic",)),
    "lutram": _util_value(text, ("LUT as Memory",)),
    "registers": _util_value(text, ("Slice Registers", "CLB Registers")),
    "bram_tiles": _util_value(text, ("Block RAM Tile",)),
    "uram": _util_value(text, ("URAM",)) or 0.0,
  }


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


def compare(metrics: dict[str, Any], log: dict[str, Any], expectation: dict[str, Any]) -> list[str]:
  """Mismatches between results and expectation (empty list = pass)."""
  problems = []
  exact = (("bram_tiles", "block RAM tiles"), ("lutram", "LUTs as memory"), ("uram", "URAM blocks"))
  for key, label in exact:
    if key in expectation:
      got = metrics.get(key)
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
