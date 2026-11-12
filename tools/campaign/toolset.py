# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tool versions of an AMD campaign, detected once (tools/campaign/run_amd.sh).

  python -m tools.campaign.toolset detect              print the toolset (JSON)
  python -m tools.campaign.toolset check <file>        the installed tools must be those of the file
  python -m tools.campaign.toolset name <file> <step>  campaign name of a step
  python -m tools.campaign.toolset preflight           the tools start in this environment

detect asks each tool of the campaign for its version, from the
installations in the environment (no default):

- Vivado: $LM_VIVADO_DIR/bin/vivado -version ("vivado v2024.1 (64-bit)");
- Quartus: $LM_QUARTUS_DIR/bin/quartus_sh --version ("Version 25.1std.0
  Build 1129 10/21/2025 SC Lite Edition");
- Questa: $VUNIT_MODELSIM_PATH/vsim -version ("Questa Altera Starter FPGA
  Edition-64 vsim 2025.2 ...", "Questa Sim-64 vsim 2021.1 ...");
- GHDL: $GHDL --version, else $VUNIT_GHDL_PATH/ghdl, else ghdl on PATH
  ("GHDL 6.0.0 (...)").

A version that cannot be read stops the campaign. run_amd.sh writes the
toolset to <campaign root>/logs/toolset.json; every later step (the names of
the campaigns, rerun.py, summarize.py, publish.py) reads it from there, so
no tool version is written in the code. A resumed campaign must find the
same versions installed (check).

preflight starts, before a campaign, the tools whose environment a version
query does not show: xvhdl (the xsim compiler of the post-synthesis step,
whose libraries may need the environment script of the installation; its
version must be that of Vivado) and vsim with a licence checkout (vsim -c,
quit at once). A failure names the tool and its output: load the
environment of the installation (library path, licence) first. These
environment scripts are specific to each machine, so the campaign does not
load them itself.

Campaign names (step -> name), the version in the name of each result file
of docs/results:

  vivado          vivado_<vivado>_amd             vivado_2023.1_amd
  checks_vivado   static_checks_vivado_<vivado>   static_checks_vivado_2023.1
  checks_quartus  static_checks_quartus_<x.y>     static_checks_quartus_23.1
  postsynth       postsynth_vivado_<vivado>_amd   postsynth_vivado_2023.1_amd
  vunit_ghdl      ghdl_<ghdl>                     ghdl_6.0.0
  vunit_questa    questa[_<edition>]_<questa>     questa_2021.1, questa_starter_2025.2
  mutation        ghdl_<ghdl>                     ghdl_6.0.0

The Questa edition is part of the name when it is not the full product
(Starter: questa_starter_2025.2).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

TOOLSET_FILE = "toolset.json"
STEPS = ("vivado", "checks_vivado", "checks_quartus", "postsynth", "vunit_ghdl", "vunit_questa", "mutation")

_VIVADO_RE = re.compile(r"\bvivado v(\d{4}\.\d+(?:\.\d+)?)", re.IGNORECASE)
_QUARTUS_RE = re.compile(r"\bVersion (\d+\.\d+\w*(?:\.\d+)?) Build (\d+)\b")
_QUARTUS_EDITION_RE = re.compile(r"\b(\w+) Edition\b")
_QUESTA_RE = re.compile(r"\bvsim (\d{4}\.\d+)\b")
_GHDL_RE = re.compile(r"\bGHDL (\d+\.\d+\.\d+)\b")


class ToolsetError(Exception):
  pass


def _first_line(text: str, pattern: re.Pattern[str]) -> str:
  return next((line.strip() for line in text.splitlines() if pattern.search(line)), "")


def parse_vivado(text: str) -> dict[str, str]:
  """{"version": "2024.1", "banner": ...} from the output of vivado -version."""
  found = _VIVADO_RE.search(text)
  if not found:
    raise ToolsetError(f"no Vivado version in: {text.strip()[:200]!r}")
  return {"version": found.group(1), "banner": _first_line(text, _VIVADO_RE)}


def parse_quartus(text: str) -> dict[str, str]:
  """Quartus version as its logs print it ("25.1std.0 Build 1129", the
  tool_version of vendor_synth), its release ("25.1std") and edition ("Lite")."""
  found = _QUARTUS_RE.search(text)
  if not found:
    raise ToolsetError(f"no Quartus version in: {text.strip()[:200]!r}")
  number = found.group(1)
  release = re.match(r"\d+\.\d+[A-Za-z]*", number).group(0)
  banner = _first_line(text, _QUARTUS_RE)
  edition = _QUARTUS_EDITION_RE.search(banner)
  return {"version": f"{number} Build {found.group(2)}", "release": release,
          "edition": edition.group(1) if edition else "", "banner": banner}


def parse_questa(text: str) -> dict[str, str]:
  """Questa version ("2025.2") and edition ("starter", or "" for the full product)."""
  found = _QUESTA_RE.search(text)
  if not found:
    raise ToolsetError(f"no Questa version in: {text.strip()[:200]!r}")
  banner = _first_line(text, _QUESTA_RE)
  return {"version": found.group(1), "edition": "starter" if re.search(r"\bstarter\b", banner, re.IGNORECASE) else "",
          "banner": banner}


def parse_ghdl(text: str) -> dict[str, str]:
  found = _GHDL_RE.search(text)
  if not found:
    raise ToolsetError(f"no GHDL version in: {text.strip()[:200]!r}")
  return {"version": found.group(1), "banner": _first_line(text, _GHDL_RE)}


def campaign_names(toolset: dict) -> dict[str, str]:
  """Campaign name of every step (module docstring)."""
  tools = toolset["tools"]
  vivado = tools["vivado"]["version"]
  quartus = re.match(r"\d+\.\d+", tools["quartus"]["release"]).group(0)
  ghdl = tools["ghdl"]["version"]
  questa = tools["questa"]
  edition = f"_{questa['edition']}" if questa.get("edition") else ""
  return {
    "vivado": f"vivado_{vivado}_amd",
    "checks_vivado": f"static_checks_vivado_{vivado}",
    "checks_quartus": f"static_checks_quartus_{quartus}",
    "postsynth": f"postsynth_vivado_{vivado}_amd",
    "vunit_ghdl": f"ghdl_{ghdl}",
    "vunit_questa": f"questa{edition}_{questa['version']}",
    "mutation": f"ghdl_{ghdl}",
  }


def _executable(directory: Path, names: list[str]) -> Path:
  for name in names:
    if (directory / name).is_file():
      return directory / name
  raise ToolsetError(f"none of {', '.join(names)} in {directory}")


def _command(executable: Path) -> list[str]:
  # A Windows batch file runs through cmd, as the launchers of vendor_synth do.
  return ["cmd", "/c", str(executable)] if executable.suffix.lower() == ".bat" else [str(executable)]


def _directory(variable: str) -> Path:
  value = os.environ.get(variable, "").strip()
  if not value:
    raise ToolsetError(f"{variable} is not set")
  return Path(value)


def tool_commands() -> dict[str, list[str]]:
  """The version command of every tool, from the installations in the environment."""
  vivado = _directory("LM_VIVADO_DIR") / "bin"
  quartus = _directory("LM_QUARTUS_DIR")
  questa = _directory("VUNIT_MODELSIM_PATH")
  ghdl = os.environ.get("GHDL", "").strip()
  if not ghdl and os.environ.get("VUNIT_GHDL_PATH", "").strip():
    ghdl = str(_executable(Path(os.environ["VUNIT_GHDL_PATH"].strip()), ["ghdl.exe", "ghdl"] if os.name == "nt"
                           else ["ghdl"]))
  ghdl = ghdl or shutil.which("ghdl")
  if not ghdl:
    raise ToolsetError("GHDL not found: set GHDL or VUNIT_GHDL_PATH, or put ghdl on PATH")
  windows = os.name == "nt"
  return {
    "vivado": _command(_executable(vivado, ["vivado.bat", "vivado"] if windows else ["vivado"])) + ["-version"],
    "quartus": _command(_executable(quartus, ["bin64/quartus_sh.exe", "bin/quartus_sh.exe"] if windows
                                    else ["bin/quartus_sh", "bin64/quartus_sh"])) + ["--version"],
    "questa": _command(_executable(questa, ["vsim.exe", "vsim"] if windows else ["vsim"])) + ["-version"],
    "ghdl": [ghdl, "--version"],
  }


PARSERS: dict[str, Callable[[str], dict[str, str]]] = {
  "vivado": parse_vivado, "quartus": parse_quartus, "questa": parse_questa, "ghdl": parse_ghdl,
}


def _run(command: list[str]) -> str:
  try:
    done = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=300)
  except (OSError, subprocess.SubprocessError) as exc:
    raise ToolsetError(f"{' '.join(command)}: {exc}") from exc
  return done.stdout + done.stderr


def detect(commands: dict[str, list[str]] | None = None, run: Callable[[list[str]], str] = _run) -> dict:
  """The toolset: every tool with its version, its banner and its command."""
  commands = tool_commands() if commands is None else commands
  tools = {}
  for tool, parse in PARSERS.items():
    try:
      tools[tool] = {**parse(run(commands[tool])), "command": commands[tool]}
    except ToolsetError as exc:
      raise ToolsetError(f"{tool}: {exc}") from exc
  toolset = {"tools": tools}
  toolset["campaigns"] = campaign_names(toolset)
  return toolset


def preflight_commands() -> dict[str, list[str]]:
  """The commands of preflight, from the installations in the environment."""
  windows = os.name == "nt"
  vivado = _directory("LM_VIVADO_DIR") / "bin"
  questa = _directory("VUNIT_MODELSIM_PATH")
  return {
    "xvhdl": _command(_executable(vivado, ["xvhdl.bat", "xvhdl"] if windows else ["xvhdl"])) + ["--version"],
    "vsim": _command(_executable(questa, ["vsim.exe", "vsim"] if windows else ["vsim"])) + ["-c", "-do", "quit -f"],
  }


def _run_with_code(command: list[str], cwd: Path) -> tuple[int | None, str]:
  try:
    done = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=300, cwd=cwd)
  except (OSError, subprocess.SubprocessError) as exc:
    return None, str(exc)
  return done.returncode, done.stdout + done.stderr


_VIVADO_SIMULATOR_RE = re.compile(r"\bVivado Simulator v(\d{4}\.\d+(?:\.\d+)?)")
_LICENCE_RE = re.compile(r"licen[cs]e", re.IGNORECASE)


def preflight(vivado_version: str, commands: dict[str, list[str]] | None = None,
              run: Callable[[list[str], Path], tuple[int | None, str]] = _run_with_code) -> list[str]:
  """Problems that would stop a step of the campaign after it started (empty: the tools start).

  Each command runs in a scratch directory (xvhdl and vsim write logs where they run)."""
  commands = preflight_commands() if commands is None else commands
  problems = []
  with tempfile.TemporaryDirectory(prefix="lm_preflight_") as tmp:
    for tool, command in commands.items():
      code, text = run(command, Path(tmp))
      last = "; ".join(line.strip() for line in text.splitlines() if line.strip())[-300:]
      if code != 0:
        hint = "a licence" if _LICENCE_RE.search(text) else "its libraries"
        problems.append(f"{tool} does not start (exit code {code}: {last}); load the environment of the "
                        f"installation ({hint}) before the campaign")
      elif tool == "xvhdl":
        found = _VIVADO_SIMULATOR_RE.search(text)
        if not found or found.group(1) != vivado_version:
          problems.append(f"xvhdl is not of Vivado {vivado_version}: {last}")
  return problems


def versions(toolset: dict) -> dict[str, dict[str, str]]:
  """What identifies the tools of a toolset (no banner, no command)."""
  return {tool: {k: v for k, v in entry.items() if k not in ("banner", "command")}
          for tool, entry in toolset["tools"].items()}


def load(root: Path) -> dict:
  """The toolset of a campaign root (<root>/logs/toolset.json)."""
  path = root / "logs" / TOOLSET_FILE
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError) as exc:
    raise ToolsetError(f"no readable {path}: the campaign did not record its tool versions ({exc})") from exc


def names(root: Path) -> dict[str, str]:
  """Campaign name of every step of a campaign root."""
  return campaign_names(load(root))


def main(argv: list[str] | None = None) -> int:
  argv = sys.argv[1:] if argv is None else argv
  try:
    if argv[:1] == ["detect"] and len(argv) == 1:
      print(json.dumps(detect(), indent=1))
      return 0
    if argv[:1] == ["check"] and len(argv) == 2:
      recorded = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
      installed = detect()
      if versions(installed) != versions(recorded):
        print(f"toolset: the installed tools are not those of {argv[1]}:\n  recorded:  "
              f"{json.dumps(versions(recorded))}\n  installed: {json.dumps(versions(installed))}", file=sys.stderr)
        return 1
      return 0
    if argv[:1] == ["preflight"] and len(argv) == 1:
      problems = preflight(parse_vivado(_run(tool_commands()["vivado"]))["version"])
      for problem in problems:
        print(f"toolset: {problem}", file=sys.stderr)
      return 1 if problems else 0
    if argv[:1] == ["name"] and len(argv) == 3 and argv[2] in STEPS:
      print(campaign_names(json.loads(Path(argv[1]).read_text(encoding="utf-8")))[argv[2]])
      return 0
  except (ToolsetError, OSError, json.JSONDecodeError, KeyError) as exc:
    print(f"toolset: {exc}", file=sys.stderr)
    return 2
  print(__doc__, file=sys.stderr)
  return 2


if __name__ == "__main__":
  raise SystemExit(main())
