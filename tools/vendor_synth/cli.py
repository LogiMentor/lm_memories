# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Run local vendor synthesis checks for lm_memories.

Sources come from the manifest flow of the job's vendor (synth-amd, ...).
A case passes only when its results match tools/vendor_synth/expected/;
the tool exit code alone never makes a pass. Runs are sequential and hold
the machine-wide EDA lock. Artefacts go outside the repository by default.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.eda_archive import archive_campaign
from tools.eda_lock import heavy_tool_lock
from tools.eda_proc import run_tool
from tools.eda_provenance import provenance, same_inputs
from tools.eda_retry import retry_reason, run_with_retries
from tools.eda_workdir import prune, work_root as work_root_for
from tools.manifest import load as load_manifest

from . import check

try:
  import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
  tomllib = None


ROOT_DIR = Path(__file__).resolve().parents[2]
MANIFEST_FLOWS = {
  "amd": "synth-amd", "xilinx": "synth-amd",
  "intel": "synth-intel", "altera": "synth-intel",
  "lattice": "synth-lattice",
  "microchip": "synth-microchip", "microsemi": "synth-microchip",
}
MANIFEST_TOOLS = {
  "vivado": "vivado", "xilinx": "vivado",
  "quartus": "quartus", "intel": "quartus", "altera": "quartus",
  "diamond": "diamond", "radiant": "radiant",
  "libero": "libero", "microchip": "libero", "microsemi": "libero",
}
# Message IDs whose limit is always raised, so their counts are real.
ALWAYS_UNLIMITED = ("Synth 8-6859", "Synth 8-5787", "Synth 8-4767", "Synth 8-3848", "Synth 8-3917")


def default_work_root() -> Path:
  """Short build directory outside the repository: $LM_EDA_WORKDIR/vs (tools/eda_workdir.py)."""
  return work_root_for("vs")


# Kept in a case directory after a passing run (prune_case_dir); the rest
# (projects, tool databases, .Xil) is removed. Enough for --evaluate-only.
KEEP_AFTER_PASS = ("*.log", "run.json", "reports", "*.tcl", "*.rpt", "*.summary", "*.vhd")
# Record of a real run next to its log: provenance, times, exit code, retries.
# --evaluate-only needs it and refuses a run made from other inputs.
RUN_RECORD = "run.json"
# Generated tops whose generics are read back from the inner library entity.
GENERICS_MODULE = {"crw_cr_common_clock": "lm_mem_ram_crw_cr"}
MEMORY_KEYWORDS = re.compile(
  r"(ram|bram|uram|m20k|mlab|m10k|ebr|dpram|block memory|distributed)",
  re.IGNORECASE,
)
FAILURE_PATTERNS = (
  re.compile(r"couldn't read file .*", re.IGNORECASE),
  re.compile(r"^ERROR:.*", re.IGNORECASE),
  re.compile(r"^Error \(.*", re.IGNORECASE),
  re.compile(r".*synth_design failed.*", re.IGNORECASE),
  re.compile(r".*Command failed.*", re.IGNORECASE),
)

PRIMITIVE_ENTITIES = {
  "amd": "lm_mem_xilinx_ram_r_w_prim",
  "xilinx": "lm_mem_xilinx_ram_r_w_prim",
  "intel": "lm_mem_intel_ram_r_w_prim",
  "altera": "lm_mem_intel_ram_r_w_prim",
  "lattice": "lm_mem_lattice_ram_r_w_prim",
  "microchip": "lm_mem_microchip_ram_r_w_prim",
  "microsemi": "lm_mem_microchip_ram_r_w_prim",
}
DEFAULT_PRIMITIVES = {
  "amd": "bram",
  "xilinx": "bram",
  "intel": "m20k",
  "altera": "m20k",
  "lattice": "ebr",
  "microchip": "lsram",
  "microsemi": "lsram",
}

BUILTIN_CASES: tuple[dict[str, Any], ...] = (
  {
    "name": "generic_r_w_32x1024",
    "kind": "generic_r_w",
    "description": "Common-clock one-write one-read RAM wrapper.",
    "vendors": ["all"],
    "width": 32,
    "depth": 1024,
    "latency": 1,
  },
  {
    "name": "generic_rw_rw_32x1024",
    "kind": "generic_rw_rw",
    "description": "Common-clock true dual-port RAM wrapper.",
    "vendors": ["all"],
    "width": 32,
    "depth": 1024,
    "latency": 1,
  },
  {
    "name": "generic_crw_crw_32x1024",
    "kind": "generic_crw_crw",
    "description": "Dual-clock true dual-port RAM wrapper.",
    "vendors": ["all"],
    "width": 32,
    "depth": 1024,
    "latency": 1,
  },
  {
    "name": "infer_r_w_bram_32x1024",
    "kind": "infer_r_w",
    "description": "Vendor-aware inferred block RAM candidate.",
    "vendors": ["all"],
    "width": 32,
    "depth": 1024,
    "latency": 1,
    "style": "block",
  },
  {
    "name": "infer_r_w_distributed_16x64",
    "kind": "infer_r_w",
    "description": "Vendor-aware inferred distributed RAM candidate.",
    "vendors": ["all"],
    "width": 16,
    "depth": 64,
    "latency": 1,
    "style": "distributed",
  },
  {
    "name": "primitive_r_w_block_32x1024",
    "kind": "primitive_r_w",
    "description": "Vendor primitive-ready one-write one-read RAM wrapper.",
    "vendors": ["amd", "xilinx", "intel", "altera", "lattice", "microchip", "microsemi"],
    "width": 32,
    "depth": 1024,
    "latency": 1,
  },
)


@dataclass(frozen=True)
class RunItem:
  job: dict[str, Any]
  case: dict[str, Any]


def _load_config(path: Path) -> dict[str, Any]:
  if path.suffix.lower() == ".toml":
    if tomllib is None:
      raise SystemExit("TOML config files require Python 3.11 or newer")
    with path.open("rb") as handle:
      data = tomllib.load(handle)
  else:
    try:
      data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
      raise SystemExit(f"{path}: invalid JSON: {exc}") from exc

  if not isinstance(data, dict):
    raise SystemExit(f"{path}: top-level value must be an object")
  return data


def _case_map(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
  cases = {case["name"]: dict(case) for case in BUILTIN_CASES}
  for case in check.load_cases():
    cases[case["name"]] = dict(case)
  for case in config.get("cases", []):
    if not isinstance(case, dict) or not case.get("name"):
      raise SystemExit("each custom case must be an object with a name")
    cases[case["name"]] = case
  return cases


def _job_name(job: dict[str, Any]) -> str:
  name = job.get("name")
  if not name:
    raise SystemExit("each job must have a name")
  return str(name)


def _vendor(job: dict[str, Any]) -> str:
  return str(job.get("vendor", job.get("tool", "generic"))).lower()


def _tool(job: dict[str, Any]) -> str:
  return str(job.get("tool", "")).lower()


def _family(job: dict[str, Any]) -> str:
  return str(job.get("family", "generic"))


def _is_case_supported(job: dict[str, Any], case: dict[str, Any]) -> bool:
  vendors = [str(v).lower() for v in case.get("vendors", ["all"])]
  return "all" in vendors or _vendor(job) in vendors


def _selected_jobs(
  config: dict[str, Any],
  selected: set[str],
  include_disabled: bool,
) -> list[dict[str, Any]]:
  jobs = config.get("jobs", config.get("targets", []))
  if not isinstance(jobs, list):
    raise SystemExit("config field jobs must be a list")

  selected_jobs = []
  for job in jobs:
    if not isinstance(job, dict):
      raise SystemExit("each job must be an object")
    name = _job_name(job)
    if selected and name not in selected:
      continue
    if not include_disabled and not bool(job.get("enabled", False)):
      continue
    selected_jobs.append(job)
  return selected_jobs


def _selected_cases(cases: dict[str, dict[str, Any]], selected: set[str]) -> list[dict[str, Any]]:
  if not selected:
    return list(cases.values())

  missing = selected.difference(cases)
  if missing:
    raise SystemExit("unknown case(s): " + ", ".join(sorted(missing)))
  return [cases[name] for name in sorted(selected)]


def _sanitize(value: str) -> str:
  text = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
  return text or "item"


def _tcl_path(path: Path) -> str:
  return path.resolve().as_posix()


def _command_list(value: Any, default: str, field: str = "command") -> list[str]:
  if value is None:
    return [default]
  if isinstance(value, str):
    return [value]
  if isinstance(value, list) and all(isinstance(item, str) for item in value):
    return value
  raise SystemExit(f"{field} must be a string or a list of strings")


def _job_path(job: dict[str, Any], key: str) -> Path | None:
  value = job.get(key)
  if not value:
    return None
  path = Path(str(value))
  if path.is_absolute():
    return path
  return ROOT_DIR / path


def _existing_or_first(candidates: list[Path]) -> Path:
  for candidate in candidates:
    if candidate.exists():
      return candidate
  return candidates[0]


def _platform_candidates(windows: list[Path], posix: list[Path]) -> list[Path]:
  if os.name == "nt":
    return windows + posix
  return posix + windows


def _install_command(job: dict[str, Any], default: str) -> list[str] | None:
  install_dir = _job_path(job, "install_dir")
  if install_dir is None:
    return None

  tool = _tool(job)
  if tool in ("vivado", "xilinx"):
    candidates = _platform_candidates(
      [install_dir / "bin" / "vivado.bat", install_dir / "bin" / "vivado.exe"],
      [install_dir / "bin" / "vivado"],
    )
  elif tool in ("quartus", "intel", "altera"):
    candidates = _platform_candidates(
      [install_dir / "bin64" / "quartus_sh.exe", install_dir / "bin" / "quartus_sh.exe"],
      [install_dir / "bin" / "quartus_sh", install_dir / "bin64" / "quartus_sh"],
    )
  elif tool == "diamond":
    candidates = _platform_candidates(
      [install_dir / "bin" / "nt64" / "diamondc.exe", install_dir / "bin" / "nt64" / "diamondc.bat"],
      [install_dir / "bin" / "lin64" / "diamondc", install_dir / "bin" / "diamondc"],
    )
  elif tool == "radiant":
    candidates = _platform_candidates(
      [install_dir / "bin" / "nt64" / "radiantc.exe", install_dir / "bin" / "nt64" / "radiantc.bat"],
      [install_dir / "bin" / "lin64" / "radiantc", install_dir / "bin" / "radiantc"],
    )
  elif tool in ("libero", "microchip", "microsemi"):
    candidates = _platform_candidates(
      [install_dir / "Libero" / "bin" / "libero.exe", install_dir / "bin" / "libero.exe"],
      [install_dir / "Libero" / "bin" / "libero", install_dir / "bin" / "libero", install_dir / "libero"],
    )
  else:
    candidates = [install_dir / default]

  return [str(_existing_or_first(candidates))]


def _configured_command(job: dict[str, Any], default: str) -> list[str]:
  if job.get("executable") is not None and job.get("command") is not None:
    raise SystemExit(f"{_job_name(job)}: use executable or command, not both")

  if job.get("executable") is not None:
    return _command_list(job.get("executable"), default, "executable")

  if "command" in job and job.get("command") is not None:
    return _command_list(job.get("command"), default, "command")

  install_command = _install_command(job, default)
  if install_command is not None:
    return install_command

  return [default]


def _auto_setup_script(job: dict[str, Any]) -> Path | None:
  install_dir = _job_path(job, "install_dir")
  if install_dir is None:
    return None

  tool = _tool(job)
  if tool in ("vivado", "xilinx"):
    candidates = _platform_candidates(
      [install_dir / "settings64.bat", install_dir.parent / "settings64.bat"],
      [install_dir / "settings64.sh", install_dir.parent / "settings64.sh"],
    )
  elif tool in ("quartus", "intel", "altera"):
    candidates = _platform_candidates(
      [install_dir / "adm" / "qenv.bat", install_dir / "qenv.bat"],
      [install_dir / "adm" / "qenv.sh", install_dir / "qenv.sh"],
    )
  elif tool == "diamond":
    candidates = _platform_candidates(
      [install_dir / "bin" / "nt64" / "diamond_env.bat"],
      [install_dir / "bin" / "lin64" / "diamond_env"],
    )
  elif tool == "radiant":
    candidates = _platform_candidates(
      [install_dir / "bin" / "nt64" / "radiant_env.bat"],
      [install_dir / "bin" / "lin64" / "radiant_env"],
    )
  elif tool in ("libero", "microchip", "microsemi"):
    candidates = _platform_candidates(
      [install_dir / "bin" / "setup.bat"],
      [install_dir / "bin" / "setup.sh"],
    )
  else:
    return None

  return _existing_or_first(candidates)


def _setup_script(job: dict[str, Any]) -> Path | None:
  value = job.get("setup_script")
  if not value:
    return None
  if str(value).lower() == "auto":
    path = _auto_setup_script(job)
    if path is None:
      raise SystemExit(f"{_job_name(job)}: setup_script auto requires install_dir")
    return path
  path = Path(str(value))
  if path.is_absolute():
    return path
  return ROOT_DIR / path


def _cmd_quote(value: str) -> str:
  return '"' + value.replace('"', '""') + '"'


def _uses_windows_batch(command: list[str]) -> bool:
  return bool(command) and Path(command[0]).suffix.lower() in (".bat", ".cmd")


def _launcher_command(job: dict[str, Any], command: list[str], work_dir: Path) -> list[str]:
  setup_script = _setup_script(job)
  use_cmd = os.name == "nt" and (setup_script is not None or _uses_windows_batch(command))
  if setup_script is None and not use_cmd:
    return command

  if os.name == "nt":
    launcher = work_dir / "launch_tool.cmd"
    lines = ["@echo off"]
    if setup_script is not None:
      lines.append(f"call {_cmd_quote(str(setup_script))}")
      lines.append("if errorlevel 1 exit /b %ERRORLEVEL%")
    quoted = " ".join(_cmd_quote(arg) for arg in command)
    # "call": without it cmd.exe hands control to a .bat tool (vivado.bat)
    # and never returns, so its exit code is not the tool's (measured with
    # Vivado 2023.1: 0 or 123 after a failed synth_design, 1 with call).
    lines.append(("call " if _uses_windows_batch(command) else "") + quoted)
    lines.append("exit /b %ERRORLEVEL%")
    launcher.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ["cmd", "/c", str(launcher)]

  launcher = work_dir / "launch_tool.sh"
  lines = ["#!/usr/bin/env bash", "set -e"]
  if setup_script is not None:
    lines.append("source " + shlex.quote(str(setup_script)))
  lines.append("exec " + " ".join(shlex.quote(arg) for arg in command))
  launcher.write_text("\n".join(lines) + "\n", encoding="utf-8")
  launcher.chmod(0o755)
  return ["bash", str(launcher)]


def _tool_command(job: dict[str, Any], default: str, args: list[str], work_dir: Path) -> list[str]:
  return _launcher_command(job, _configured_command(job, default) + args, work_dir)


def _top_name(job: dict[str, Any], case: dict[str, Any]) -> str:
  if case.get("kind") == "entity":
    return str(case["entity"])
  return "lm_vs_" + _sanitize(_job_name(job)) + "_" + _sanitize(case["name"])


def _addr_range(case: dict[str, Any]) -> str:
  return f"f_ceil_log2({int(case['depth'])}) - 1 downto 0"


def _case_width(case: dict[str, Any]) -> int:
  return int(case.get("width", 32))


def _case_depth(case: dict[str, Any]) -> int:
  return int(case.get("depth", 1024))


def _case_latency(case: dict[str, Any]) -> int:
  return int(case.get("latency", 1))


def _render_r_w_top(entity: str, instance: str, generic_map: str, case: dict[str, Any]) -> str:
  width = _case_width(case)
  addr = _addr_range(case)
  return f"""-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : {entity}
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Local synthesis top for {case["name"]}.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity {entity} is
  port(
    clk_i     : in  std_logic;
    ena_i     : in  std_logic;
    enb_i     : in  std_logic;
    wen_i     : in  std_logic;
    wr_addr_i : in  std_logic_vector({addr});
    wr_dat_i  : in  std_logic_vector({width} - 1 downto 0);
    rd_addr_i : in  std_logic_vector({addr});
    rd_dat_o  : out std_logic_vector({width} - 1 downto 0)
  );
end entity {entity};

architecture a_rtl of {entity} is
begin
  inst_dut : entity work.{instance}
    generic map(
{generic_map}
    )
    port map(
      clk_i     => clk_i,
      ena_i     => ena_i,
      enb_i     => enb_i,
      wen_i     => wen_i,
      wr_addr_i => wr_addr_i,
      wr_dat_i  => wr_dat_i,
      rd_addr_i => rd_addr_i,
      rd_dat_o  => rd_dat_o
    );
end architecture a_rtl;
"""


def _render_rw_rw_top(entity: str, case: dict[str, Any]) -> str:
  width = _case_width(case)
  addr = _addr_range(case)
  return f"""-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : {entity}
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Local synthesis top for {case["name"]}.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity {entity} is
  port(
    clk_i      : in  std_logic;
    ena_i      : in  std_logic;
    enb_i      : in  std_logic;
    wen_a_i    : in  std_logic;
    wen_b_i    : in  std_logic;
    wr_dat_a_i : in  std_logic_vector({width} - 1 downto 0);
    wr_dat_b_i : in  std_logic_vector({width} - 1 downto 0);
    addr_a_i   : in  std_logic_vector({addr});
    addr_b_i   : in  std_logic_vector({addr});
    rd_dat_a_o : out std_logic_vector({width} - 1 downto 0);
    rd_dat_b_o : out std_logic_vector({width} - 1 downto 0)
  );
end entity {entity};

architecture a_rtl of {entity} is
begin
  inst_dut : entity work.lm_mem_ram_rw_rw
    generic map(
      g_ram_latency => {_case_latency(case)},
      g_ram_data_w  => {width},
      g_ram_depth   => {_case_depth(case)},
      g_init_file   => "",
      g_simulation  => 0
    )
    port map(
      clk_i      => clk_i,
      ena_i      => ena_i,
      enb_i      => enb_i,
      wen_a_i    => wen_a_i,
      wen_b_i    => wen_b_i,
      wr_dat_a_i => wr_dat_a_i,
      wr_dat_b_i => wr_dat_b_i,
      addr_a_i   => addr_a_i,
      addr_b_i   => addr_b_i,
      rd_dat_a_o => rd_dat_a_o,
      rd_dat_b_o => rd_dat_b_o
    );
end architecture a_rtl;
"""


def _render_crw_crw_top(entity: str, case: dict[str, Any]) -> str:
  width = _case_width(case)
  addr = _addr_range(case)
  return f"""-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : {entity}
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Local synthesis top for {case["name"]}.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity {entity} is
  port(
    clk_a_i     : in  std_logic;
    clk_b_i     : in  std_logic;
    ena_i       : in  std_logic;
    enb_i       : in  std_logic;
    wen_a_i     : in  std_logic;
    wen_b_i     : in  std_logic;
    data_wr_a_i : in  std_logic_vector({width} - 1 downto 0);
    data_wr_b_i : in  std_logic_vector({width} - 1 downto 0);
    addr_a_i    : in  std_logic_vector({addr});
    addr_b_i    : in  std_logic_vector({addr});
    data_rd_a_o : out std_logic_vector({width} - 1 downto 0);
    data_rd_b_o : out std_logic_vector({width} - 1 downto 0)
  );
end entity {entity};

architecture a_rtl of {entity} is
begin
  inst_dut : entity work.lm_mem_ram_crw_crw
    generic map(
      g_ram_a_latency => {_case_latency(case)},
      g_ram_a_data_w  => {width},
      g_ram_a_depth   => {_case_depth(case)},
      g_ram_b_latency => {_case_latency(case)},
      g_ram_b_data_w  => {width},
      g_init_file     => "",
      g_simulation    => 0
    )
    port map(
      clk_a_i     => clk_a_i,
      clk_b_i     => clk_b_i,
      ena_i       => ena_i,
      enb_i       => enb_i,
      wen_a_i     => wen_a_i,
      wen_b_i     => wen_b_i,
      data_wr_a_i => data_wr_a_i,
      data_wr_b_i => data_wr_b_i,
      addr_a_i    => addr_a_i,
      addr_b_i    => addr_b_i,
      data_rd_a_o => data_rd_a_o,
      data_rd_b_o => data_rd_b_o
    );
end architecture a_rtl;
"""


def _render_top(job: dict[str, Any], case: dict[str, Any], entity: str) -> str:
  width = _case_width(case)
  depth = _case_depth(case)
  latency = _case_latency(case)
  kind = str(case["kind"])

  if kind == "generic_r_w":
    generic_map = f"""      g_ram_latency => {latency},
      g_ram_data_w  => {width},
      g_ram_depth   => {depth},
      g_init_file   => "",
      g_simulation  => 0"""
    return _render_r_w_top(entity, "lm_mem_ram_r_w", generic_map, case)

  if kind == "infer_r_w":
    style = str(case.get("style", "auto"))
    generic_map = "\n".join(
      (
        f"      g_ram_latency => {latency},",
        f"      g_ram_data_w  => {width},",
        f"      g_ram_depth   => {depth},",
        f"      g_vendor      => \"{_vendor(job)}\",",
        f"      g_family      => \"{_family(job)}\",",
        f"      g_tool        => \"{_tool(job)}\",",
        f"      g_style       => \"{style}\"",
      )
    )
    return _render_r_w_top(entity, "lm_mem_ram_r_w_infer", generic_map, case)

  if kind == "primitive_r_w":
    vendor = _vendor(job)
    instance = PRIMITIVE_ENTITIES.get(vendor)
    if instance is None:
      raise SystemExit(f"{case['name']}: unsupported primitive vendor {vendor}")
    primitive = str(case.get("primitive", DEFAULT_PRIMITIVES.get(vendor, "block")))
    generic_map = "\n".join(
      (
        f"      g_ram_latency => {latency},",
        f"      g_ram_data_w  => {width},",
        f"      g_ram_depth   => {depth},",
        f"      g_family      => \"{_family(job)}\",",
        f"      g_primitive   => \"{primitive}\"",
      )
    )
    return _render_r_w_top(entity, instance, generic_map, case)

  if kind == "generic_rw_rw":
    return _render_rw_rw_top(entity, case)

  if kind == "generic_crw_crw":
    return _render_crw_crw_top(entity, case)

  if kind == "crw_cr_common_clock":
    return _render_crw_cr_common_clock_top(entity, case)

  raise SystemExit(f"{case['name']}: unsupported case kind {kind}")


def _render_crw_cr_common_clock_top(top: str, case: dict[str, Any]) -> str:
  """lm_mem_ram_crw_cr with both clock ports on one clock (a URAM has a single clock)."""
  g = case["generics"]
  width, depth, latency = int(g["g_ram_data_w"]), int(g["g_ram_depth"]), int(g.get("g_ram_latency", 1))
  addr = max(1, (depth - 1).bit_length())
  return f"""-- Generated by tools/vendor_synth: {case['name']}, lm_mem_ram_crw_cr on one clock.
library ieee;
use ieee.std_logic_1164.all;

entity {top} is
  port(
    clk_i       : in  std_logic;
    ena_i       : in  std_logic;
    enb_i       : in  std_logic;
    wen_a_i     : in  std_logic;
    data_wr_a_i : in  std_logic_vector({width - 1} downto 0);
    addr_a_i    : in  std_logic_vector({addr - 1} downto 0);
    addr_b_i    : in  std_logic_vector({addr - 1} downto 0);
    data_rd_a_o : out std_logic_vector({width - 1} downto 0);
    data_rd_b_o : out std_logic_vector({width - 1} downto 0)
  );
end entity {top};

architecture a_rtl of {top} is
begin
  inst_mem : entity work.lm_mem_ram_crw_cr
    generic map(
      g_ram_latency => {latency},
      g_ram_data_w  => {width},
      g_ram_depth   => {depth}
    )
    port map(
      clk_a_i     => clk_i,
      clk_b_i     => clk_i,
      ena_i       => ena_i,
      enb_i       => enb_i,
      wen_a_i     => wen_a_i,
      data_wr_a_i => data_wr_a_i,
      addr_a_i    => addr_a_i,
      addr_b_i    => addr_b_i,
      data_rd_a_o => data_rd_a_o,
      data_rd_b_o => data_rd_b_o
    );
end architecture a_rtl;
"""


def _source_files_for(job: dict[str, Any], case: dict[str, Any]) -> list[Path]:
  """Library sources from the manifest flow of the job's vendor."""
  vendor = _vendor(job)
  # manifest_flow: e.g. "synth-unsupported", to check the fallback with any tool.
  flow = job.get("manifest_flow") or MANIFEST_FLOWS.get(vendor)
  if flow is None:
    raise SystemExit(f"{_job_name(job)}: no manifest flow for vendor {vendor}")
  tool = MANIFEST_TOOLS.get(_tool(job))
  return [source.path for source in load_manifest().files(flow, tool)]


def _vivado_script(
  job: dict[str, Any],
  top: str,
  sources: list[Path],
  script: Path,
  reports: Path,
  generics: dict[str, Any] | None = None,
  unlimited_ids: tuple[str, ...] = ALWAYS_UNLIMITED,
) -> list[str]:
  part = job.get("part")
  if not part:
    raise SystemExit(f"{_job_name(job)}: Vivado jobs require part")
  threads = int(job.get("threads", 0))
  script_dir = _tcl_path(script.parent)
  source_lines = "\n".join(f"read_vhdl -vhdl2008 {{{_tcl_path(path)}}}" for path in sources)
  thread_line = f"set_param general.maxThreads {threads}\n" if threads >= 2 else ""
  limit_lines = "\n".join(f"set_msg_config -id {{{msg_id}}} -limit 1000000" for msg_id in unlimited_ids)
  # Generics go into the script, never through a command line (cmd.exe splits
  # arguments on '='), and are read back from the log afterwards.
  # assert = true: Vivado evaluates VHDL assertions (synth_design -assert).
  assert_arg = " -assert" if job.get("assert") else ""
  generic_args = "".join(
    f" -generic {{{name}={_tcl_generic(value)}}}" for name, value in (generics or {}).items()
  )
  synth_line = check.vivado_synth_command(
    f"synth_design -mode out_of_context -top {top} -part {{{part}}}{generic_args}{assert_arg}")
  text = f"""# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
{thread_line}set script_dir {{{script_dir}}}
file mkdir [file join $script_dir reports]
catch {{ set_param messaging.defaultLimit 1000000 }}
{limit_lines}
{source_lines}
{synth_line}
report_utilization -file {{{_tcl_path(reports / "utilization.rpt")}}}
catch {{ report_ram_utilization -file {{{_tcl_path(reports / "ram_utilization.rpt")}}} }}
exit
"""
  script.write_text(text, encoding="utf-8")
  return _tool_command(job, "vivado", ["-mode", "batch", "-nojournal", "-source", _tcl_path(script)], script.parent)


def _tcl_generic(value: Any) -> str:
  """Value of a generic inside the braces of synth_design -generic {name=value}.

  Braces keep every character, so a VHDL string is written with plain
  quotes; escaped quotes would reach Vivado as backslashes and quotes.
  """
  if isinstance(value, bool):
    return "true" if value else "false"
  if isinstance(value, str):
    return '"' + value + '"'
  return str(value)


def _quartus_generic(value: Any) -> str:
  if isinstance(value, bool):
    return "true" if value else "false"
  if isinstance(value, str):
    return '"\\"' + value + '\\""'
  return str(value)


def _quartus_script(
  job: dict[str, Any],
  top: str,
  sources: list[Path],
  script: Path,
  reports: Path,
  generics: dict[str, Any] | None = None,
) -> list[str]:
  family = job.get("family")
  if not family:
    raise SystemExit(f"{_job_name(job)}: Quartus jobs require family")
  device = job.get("device")
  project = _sanitize(_job_name(job)) + "_" + top
  source_lines = "\n".join(f"set_global_assignment -name VHDL_FILE {{{_tcl_path(path)}}}" for path in sources)
  device_line = f"set_global_assignment -name DEVICE {{{device}}}\n" if device else ""
  # Generics go into the script, as for Vivado.
  parameter_lines = "".join(
    f"set_parameter -name {name} {_quartus_generic(value)}\n" for name, value in (generics or {}).items()
  )
  # quartus_flow: "map" (Analysis & Synthesis, the default) or "compile".
  run_line = "execute_module -tool map" if job.get("quartus_flow", "map") == "map" else "execute_flow -compile"
  text = f"""# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
load_package flow
project_new {project} -overwrite
set_global_assignment -name FAMILY {{{family}}}
{device_line}set_global_assignment -name TOP_LEVEL_ENTITY {top}
set_global_assignment -name VHDL_INPUT_VERSION VHDL_2008
{source_lines}
{parameter_lines}{run_line}
project_close
"""
  script.write_text(text, encoding="utf-8")
  return _tool_command(job, "quartus_sh", ["-t", str(script)], script.parent)


def _diamond_script(job: dict[str, Any], top: str, sources: list[Path], script: Path, reports: Path) -> list[str]:
  device = job.get("device", "")
  synthesis = job.get("synthesis", "synplify")
  project = _sanitize(_job_name(job)) + "_" + top
  source_lines = "\n".join(f"prj_src add {{{_tcl_path(path)}}}" for path in sources)
  text = f"""# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
prj_project new -name {project} -impl impl1 -dev {{{device}}} -synthesis {{{synthesis}}}
{source_lines}
prj_impl option top {top}
prj_run Synthesis -impl impl1
prj_run Translate -impl impl1
prj_run Map -impl impl1
prj_project save
prj_project close
"""
  script.write_text(text, encoding="utf-8")
  return _tool_command(job, "diamondc", [str(script)], script.parent)


def _radiant_script(job: dict[str, Any], top: str, sources: list[Path], script: Path, reports: Path) -> list[str]:
  device = job.get("device", "")
  synthesis = job.get("synthesis", "synplify")
  project = _sanitize(_job_name(job)) + "_" + top
  source_lines = "\n".join(f"prj_add_source {{{_tcl_path(path)}}}" for path in sources)
  text = f"""# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
prj_create -name {project} -impl impl1 -dev {{{device}}} -synthesis {{{synthesis}}}
{source_lines}
prj_set_impl_opt -impl impl1 top {top}
prj_run Synthesis -impl impl1
prj_run Map -impl impl1
prj_save
prj_close
"""
  script.write_text(text, encoding="utf-8")
  return _tool_command(job, "radiantc", [str(script)], script.parent)


def _libero_script(job: dict[str, Any], top: str, sources: list[Path], script: Path, reports: Path) -> list[str]:
  project = _sanitize(_job_name(job)) + "_" + top
  family = job.get("family", "PolarFire")
  die = job.get("die", "")
  package = job.get("package", "")
  speed = job.get("speed", "")
  source_lines = "\n".join(f"import_files -hdl_source {{{_tcl_path(path)}}}" for path in sources)
  device_parts = [f"-family {{{family}}}"]
  if die:
    device_parts.append(f"-die {{{die}}}")
  if package:
    device_parts.append(f"-package {{{package}}}")
  if speed:
    device_parts.append(f"-speed {{{speed}}}")
  text = f"""# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
new_project -location {{{_tcl_path(script.parent / project)}}} -name {project} {' '.join(device_parts)}
{source_lines}
set_root -module {top}
run_tool -name {{SYNTHESIZE}}
save_project
close_project
"""
  script.write_text(text, encoding="utf-8")
  return _tool_command(job, "libero", ["SCRIPT:" + str(script)], script.parent)


def _custom_script(job: dict[str, Any], top: str, sources: list[Path], script: Path, reports: Path) -> list[str]:
  template = job.get("script_template")
  if not template:
    raise SystemExit(f"{_job_name(job)}: custom jobs require script_template")
  source_list = "\n".join(_tcl_path(path) for path in sources)
  text = str(template).format(
    top=top,
    reports=_tcl_path(reports),
    sources=source_list,
    work=_tcl_path(script.parent),
  )
  script.write_text(text, encoding="utf-8")
  command = _command_list(job.get("command"), "")
  if not command or command == [""]:
    raise SystemExit(f"{_job_name(job)}: custom jobs require command")
  formatted = [arg.format(script=str(script), top=top, reports=str(reports)) for arg in command]
  return _launcher_command(job, formatted, script.parent)


def _tool_script(
  job: dict[str, Any],
  top: str,
  sources: list[Path],
  script: Path,
  reports: Path,
  generics: dict[str, Any] | None = None,
) -> list[str]:
  tool = _tool(job)
  if tool in ("vivado", "xilinx"):
    return _vivado_script(job, top, sources, script, reports, generics)
  if tool in ("quartus", "intel", "altera"):
    return _quartus_script(job, top, sources, script, reports, generics)
  if tool == "diamond":
    return _diamond_script(job, top, sources, script, reports)
  if tool == "radiant":
    return _radiant_script(job, top, sources, script, reports)
  if tool in ("libero", "microchip", "microsemi"):
    return _libero_script(job, top, sources, script, reports)
  if tool == "custom":
    return _custom_script(job, top, sources, script, reports)
  raise SystemExit(f"{_job_name(job)}: unsupported tool {tool}")


def _collect_memory_lines(paths: list[Path], limit: int = 60) -> list[str]:
  lines: list[str] = []
  for path in paths:
    if not path.is_file():
      continue
    try:
      text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
      continue
    for number, line in enumerate(text.splitlines(), start=1):
      if path.name == "run.log" and (line.startswith("command:") or line.startswith("dry run:")):
        continue
      if MEMORY_KEYWORDS.search(line):
        lines.append(f"{path.name}:{number}: {line.strip()}")
        if len(lines) >= limit:
          return lines
  return lines


def _failure_message(paths: list[Path]) -> str:
  for path in paths:
    if not path.is_file():
      continue
    try:
      text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
      continue
    for line in text.splitlines():
      stripped = line.strip()
      for pattern in FAILURE_PATTERNS:
        match = pattern.search(stripped)
        if match:
          return match.group(0)[:240]
  return ""


def _report_files(case_dir: Path) -> list[Path]:
  ignored = {".vhd", ".tcl", ".cmd", ".sh"}
  return [
    path for path in case_dir.rglob("*")
    if path.is_file() and path.suffix.lower() not in ignored
  ]


def _executable_available(job: dict[str, Any], default: str) -> bool:
  exe = _configured_command(job, default)[0]
  path = Path(exe)
  if path.is_absolute() or len(path.parts) > 1:
    return path.exists()
  return shutil.which(exe) is not None


def _default_executable(job: dict[str, Any]) -> str:
  return {
    "vivado": "vivado", "xilinx": "vivado",
    "quartus": "quartus_sh", "intel": "quartus_sh", "altera": "quartus_sh",
    "diamond": "diamondc", "radiant": "radiantc",
    "libero": "libero", "microchip": "libero", "microsemi": "libero",
  }.get(_tool(job), _tool(job))


def _generics_module(case: dict[str, Any], top: str) -> str | None:
  """The module whose generics are read back from the log for this case, or None."""
  kind = case.get("kind")
  if kind == "entity":
    return top
  return GENERICS_MODULE.get(str(kind))


def _tool_roots(job: dict[str, Any]) -> list[Path]:
  """Installation directories of the job's tool (for the retry signatures)."""
  install_dir = _job_path(job, "install_dir")
  return [install_dir] if install_dir is not None else []


def _report_is_fresh(path: Path, started_at: str | None) -> bool:
  """True if the report was written by the run that started at started_at."""
  if not path.is_file() or not started_at:
    return False
  started = dt.datetime.fromisoformat(started_at).timestamp()
  # File systems store modification times with limited precision.
  return path.stat().st_mtime >= started - 2.0


def _evaluate(
  job: dict[str, Any],
  case: dict[str, Any],
  top: str,
  log_text: str,
  reports: Path,
  sources: list[Path] | None = None,
  run: dict[str, Any] | None = None,
) -> dict[str, Any]:
  """Compare the results of one run with the expected results.

  run: the record of the run (run.json): return code, start time, retries.
  A pass needs positive evidence of a complete run of this case: the tool's
  completion message, exit code 0, no error of the project, and reports
  written by this run that have every expected row.
  """
  run = run or {}
  tool = _tool(job)
  part = str(job.get("part") or job.get("device") or "")
  return_code = run.get("return_code")
  expectation = check.find_expectation(check.load_expected(_vendor(job)), case["name"], part)
  if expectation is not None and ("expect_failure" in expectation or "expect_failure_constant" in expectation):
    problems, facts = check.check_expected_failure(tool, log_text, expectation, sources,
                                                   assert_mode=bool(job.get("assert")), return_code=return_code)
    log = check.parse_vivado_log(log_text, top) if tool in ("vivado", "xilinx") else {}
    # The stop must be for the requested generics (Vivado prints them before the error).
    module = _generics_module(case, top)
    if log and module is not None:
      problems += check.check_generics(case.get("generics", {}), log["module_generics"].get(module.lower(), {}))
    # A tool that stopped before elaborating the design says nothing about the check.
    status = "pass" if not problems else ("fail" if facts["elaborated"] else "tool-error")
    return {"status": status, "problems": problems, "metrics": facts, "log": log}
  if tool not in ("vivado", "xilinx"):
    return {"status": "unchecked", "problems": [f"no result extraction for tool {tool} yet"], "metrics": {}, "log": {}}
  log = check.parse_vivado_log(log_text, top)
  util_file = reports / "utilization.rpt"
  metrics = check.parse_vivado_utilization(util_file.read_text(encoding="utf-8", errors="ignore")) if util_file.is_file() else {}
  incomplete = []
  if not log["synth_ran"]:
    incomplete.append("synth_design did not start")
  if not log["completed"]:
    incomplete.append("no 'synth_design completed successfully' in the log")
  error = log["project_error"] or (log["errors"][0] if log["errors"] else None)
  if error:
    incomplete.append(error)
  if return_code != 0:
    incomplete.append(f"tool exit code {return_code}")
  if not util_file.is_file():
    incomplete.append("no utilization report")
  elif not _report_is_fresh(util_file, run.get("started_at")):
    incomplete.append("utilization report older than the run")
  if incomplete:
    problems = ["synthesis did not complete: " + "; ".join(incomplete)]
    return {"status": "tool-error", "problems": problems, "metrics": metrics, "log": log}
  problems: list[str] = []
  module = _generics_module(case, top)
  if module is None:
    problems.append(f"no generics readback for case kind {case.get('kind')}")
  else:
    problems += check.check_generics(case.get("generics", {}), log["module_generics"].get(module.lower(), {}))
  if expectation is None:
    problems.append(f"no expected results for case {case['name']} on {part}")
    return {"status": "unchecked", "problems": problems, "metrics": metrics, "log": log}
  problems += check.compare(metrics, log, expectation, part)
  return {"status": "fail" if problems else "pass", "problems": problems, "metrics": metrics, "log": log}


def _inputs(job: dict[str, Any], case: dict[str, Any], sources: list[Path]) -> dict[str, str]:
  """Provenance of a run: library sources, manifest, commit, and what selects the case."""
  selection = json.dumps({"case": case, "part": job.get("part", job.get("device", "")), "tool": _tool(job),
                          "assert": bool(job.get("assert")), "flow": job.get("manifest_flow")}, sort_keys=True)
  return provenance(sources, extra=[selection])


def _read_run_record(path: Path) -> dict[str, Any] | None:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return None


def _run_item(item: RunItem, work_root: Path, dry_run: bool, clean: bool, evaluate_only: bool = False) -> dict[str, Any]:
  job = item.job
  case = item.case
  top = _top_name(job, case)
  case_dir = work_root / _sanitize(_job_name(job)) / _sanitize(case["name"])
  reports = case_dir / "reports"
  record_file = case_dir / RUN_RECORD

  if clean and not evaluate_only and case_dir.exists():
    shutil.rmtree(case_dir)
  reports.mkdir(parents=True, exist_ok=True)

  script_file = case_dir / f"run_{_tool(job)}.tcl"
  sources = _source_files_for(job, case)
  inputs = _inputs(job, case, sources)
  generics: dict[str, Any] = {}
  if case.get("kind") == "entity":
    generics = dict(case.get("generics", {}))
  elif not evaluate_only:
    top_file = case_dir / f"{top}.vhd"
    top_file.write_text(_render_top(job, case, top), encoding="utf-8")
    sources = sources + [top_file]
  # Evaluation only: keep the script that actually ran.
  command = [] if evaluate_only else _tool_script(job, top, sources, script_file, reports, generics)
  log_file = case_dir / "run.log"

  result: dict[str, Any] = {
    "job": _job_name(job),
    "tool": _tool(job),
    "vendor": _vendor(job),
    "family": _family(job),
    "part": job.get("part", job.get("device", "")),
    "case": case["name"],
    "case_kind": case["kind"],
    "top": top,
    "generics": generics,
    "work_dir": str(case_dir),
    "script": str(script_file),
    "log": str(log_file),
    "command": command,
    "return_code": None,
    "retries": [],
    "problems": [],
    "metrics": {},
    "tool_version": "",
    "critical_warnings": None,
    "provenance": inputs,
    "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
  }

  if dry_run:
    log_file.write_text("dry run: command was not executed\ncommand: " + " ".join(command) + "\n", encoding="utf-8")
    result.update(status="dry-run", completed_at=result["started_at"], message="")
    return result

  if evaluate_only:
    record = _read_run_record(record_file)
    problem = None
    if not log_file.is_file() or record is None:
      problem = "no previous run to evaluate (run.log and run.json are needed)"
    elif not same_inputs(record.get("provenance"), inputs):
      problem = (f"the previous run used other inputs (commit {record.get('provenance', {}).get('commit', '?')}); "
                 "run the case again")
    if problem:
      result.update(status="not-run", completed_at=result["started_at"], message=problem, problems=[problem])
      return result
    text = log_file.read_text(encoding="utf-8", errors="ignore")
    result.update(return_code=record.get("return_code"), retries=record.get("retries", []),
                  started_at=record.get("started_at"))
    evaluation = _evaluate(job, case, top, text, reports, sources, record)
    if record.get("timed_out"):
      evaluation.update(status="timeout", problems=["timeout"])
    elif record.get("persisted"):
      evaluation["status"] = "tool-error"
      evaluation["problems"].insert(0, "transient tool failure in every attempt of the previous run")
    else:
      # The policy of a real run, applied again to the log that is evaluated.
      done = record.get("return_code") == 0 and check.tool_completed(_tool(job), text)
      reason = retry_reason(text, completed=done, roots=_tool_roots(job))
      if reason:
        evaluation["status"] = "tool-error"
        evaluation["problems"].insert(0, f"transient tool failure in the log: {reason}")
    log = evaluation["log"]
    result.update(
      status=evaluation["status"], problems=evaluation["problems"], metrics=evaluation["metrics"],
      tool_version=log.get("version", ""), critical_warnings=log.get("critical_warnings"),
      completed_at=record.get("completed_at", result["started_at"]), message="; ".join(evaluation["problems"])[:400],
    )
    return result

  if not _executable_available(job, _default_executable(job)):
    result.update(status="not-run", completed_at=result["started_at"],
                  message=f"tool not installed: {_configured_command(job, _default_executable(job))[0]}")
    result["problems"] = [result["message"]]
    return result

  env = os.environ.copy()
  env.update({str(k): str(v) for k, v in job.get("env", {}).items()})
  timeout = int(job.get("timeout_seconds", 3600))
  timed_out = False

  def run_once(attempt: int) -> str:
    nonlocal timed_out
    # Reports of an earlier attempt must never be read as results of this one.
    for old in reports.glob("*"):
      if old.is_file():
        old.unlink()
    result["started_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    result["return_code"] = None
    with log_file.open("w", encoding="utf-8", errors="ignore") as handle:
      handle.write("command: " + " ".join(command) + "\n")
      handle.flush()
      try:
        # Machine-wide lock: one heavy EDA run at a time, across sessions.
        # On a timeout run_tool stops the whole tool before the lock is released.
        with heavy_tool_lock(f"{_tool(job)} {_job_name(job)} {case['name']}", kind="synthesis"):
          result["return_code"] = run_tool(command, cwd=case_dir, env=env, stdout=handle,
                                           stderr=subprocess.STDOUT, timeout=timeout)
      except subprocess.TimeoutExpired:
        timed_out = True
        handle.write(f"\ntimeout after {timeout} seconds: the tool was stopped\n")
    return log_file.read_text(encoding="utf-8", errors="ignore")

  def completed(text: str) -> bool:
    # A timeout is a result, never retried.
    return timed_out or (result["return_code"] == 0 and check.tool_completed(_tool(job), text))

  # Shared retry policy (tools/eda_retry.py): every retried attempt is recorded
  # and its log kept as run.attempt<n>.log.
  text, result["retries"], persisted = run_with_retries(run_once, completed=completed, roots=_tool_roots(job),
                                                        log_path=log_file)

  result["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
  record = {key: result[key] for key in ("provenance", "started_at", "completed_at", "return_code", "retries")}
  record.update(timed_out=timed_out, persisted=persisted)
  record_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
  if timed_out:
    result.update(status="timeout", message=f"timeout after {timeout} seconds", problems=["timeout"])
    return result

  evaluation = _evaluate(job, case, top, text, reports, sources, record)
  if persisted:
    evaluation["status"] = "tool-error"
    evaluation["problems"].insert(0, f"transient tool failure in all {len(result['retries'])} attempts: "
                                     + result["retries"][-1])
  log = evaluation["log"]
  result.update(
    status=evaluation["status"],
    problems=evaluation["problems"],
    metrics=evaluation["metrics"],
    tool_version=log.get("version", ""),
    critical_warnings=log.get("critical_warnings"),
    message="; ".join(evaluation["problems"])[:400],
  )
  return result


def _fmt(value: Any) -> str:
  if value is None:
    return "-"
  if isinstance(value, float) and value.is_integer():
    return str(int(value))
  return str(value)


def _write_summary(work_root: Path, results: list[dict[str, Any]], archive: dict[str, Any] | None = None) -> None:
  work_root.mkdir(parents=True, exist_ok=True)
  summary_json = work_root / "summary.json"
  summary_csv = work_root / "summary.csv"
  summary_md = work_root / "summary.md"

  summary_json.write_text(json.dumps({"results": results}, indent=2) + "\n", encoding="utf-8")

  # Results without local paths or commands, suitable for docs/results/.
  public = [
    {
      "tool": r["tool"], "tool_version": r.get("tool_version", ""), "vendor": r["vendor"],
      "family": r["family"], "part": r.get("part", ""), "case": r["case"], "top": r["top"],
      "generics": r.get("generics", {}), "status": r["status"], "metrics": r.get("metrics", {}),
      "critical_warnings": r.get("critical_warnings"), "retries": len(r.get("retries", [])),
      "problems": r.get("problems", []),
      "commit": r.get("provenance", {}).get("commit", ""),
      "sources_hash": r.get("provenance", {}).get("sources_hash", ""),
    }
    for r in results
  ]
  document: dict[str, Any] = {"results": public}
  if archive is not None:
    # Complete logs of the campaign (tools/eda_archive.py).
    document = {"archive": archive, **document}
  (work_root / "summary_public.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

  fields = ["job", "tool", "tool_version", "part", "case", "status", "bram_tiles", "lutram", "uram",
            "registers", "luts", "critical_warnings", "retries", "message"]
  with summary_csv.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    for r in results:
      m = r.get("metrics", {})
      writer.writerow({
        "job": r["job"], "tool": r["tool"], "tool_version": r.get("tool_version", ""), "part": r.get("part", ""),
        "case": r["case"], "status": r["status"], "bram_tiles": m.get("bram_tiles"), "lutram": m.get("lutram"),
        "uram": m.get("uram"), "registers": m.get("registers"), "luts": m.get("luts"),
        "critical_warnings": r.get("critical_warnings"), "retries": len(r.get("retries", [])),
        "message": r.get("message", ""),
      })

  status_text = {"pass": "pass", "fail": "FAIL", "tool-error": "TOOL ERROR (synthesis did not complete)",
                 "unchecked": "UNCHECKED (no expected results)",
                 "not-run": "NOT RUN", "timeout": "TIMEOUT", "dry-run": "dry run"}
  lines = [
    "# Vendor Synthesis Summary",
    "",
    "| Tool | Part | Case | BRAM tiles | LUTRAM | URAM | Registers | LUTs | CRITICAL WARNINGs | Retries | Result | Notes |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
  ]
  for r in results:
    m = r.get("metrics", {})
    tool = f"{r['tool']} {r.get('tool_version', '')}".strip()
    lines.append(
      f"| {tool} | {r.get('part', '')} | {r['case']} | {_fmt(m.get('bram_tiles'))} | {_fmt(m.get('lutram'))} | "
      f"{_fmt(m.get('uram'))} | {_fmt(m.get('registers'))} | {_fmt(m.get('luts'))} | "
      f"{_fmt(r.get('critical_warnings'))} | {len(r.get('retries', []))} | "
      f"{status_text.get(r['status'], r['status'])} | {r.get('message', '')} |"
    )
  counts: dict[str, int] = {}
  for r in results:
    counts[r["status"]] = counts.get(r["status"], 0) + 1
  lines += ["", "Totals: " + ", ".join(f"{status_text.get(k, k)}: {v}" for k, v in sorted(counts.items()))]
  summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_run_items(jobs: list[dict[str, Any]], cases: list[dict[str, Any]]) -> list[RunItem]:
  items: list[RunItem] = []
  for job in jobs:
    job_cases = set(str(name) for name in job.get("cases", []))
    for case in cases:
      if job_cases and case["name"] not in job_cases:
        continue
      if _is_case_supported(job, case):
        items.append(RunItem(job=job, case=case))
  return items


def _print_list(config: dict[str, Any], cases: dict[str, dict[str, Any]]) -> None:
  print("Cases:")
  for case in cases.values():
    print(f"  {case['name']} ({case['kind']}): {case.get('description', '')}")
  print("")
  print("Jobs:")
  for job in config.get("jobs", []):
    enabled = "enabled" if job.get("enabled", False) else "disabled"
    print(f"  {_job_name(job)} ({_tool(job)}, {_vendor(job)}, {enabled})")


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog="lm_vendor_synth",
    description="Run local vendor synthesis smoke campaigns.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=textwrap.dedent(
      """\
      Examples:
        python -m tools.vendor_synth --config tools/vendor_synth/local.example.toml --dry-run
        python -m tools.vendor_synth --config local/vendor_synth.toml --job vivado-zynq7-local
      """
    ),
  )
  parser.add_argument("--config", type=Path, help="local JSON or TOML configuration")
  parser.add_argument("--work-root", type=Path, help="override work/output directory")
  parser.add_argument("--job", action="append", default=[], help="run only this job name")
  parser.add_argument("--case", action="append", default=[], help="run only this case name")
  parser.add_argument("--include-disabled", action="store_true", help="allow disabled jobs to run")
  parser.add_argument("--dry-run", action="store_true", help="generate scripts and summaries without executing tools")
  parser.add_argument("--list", action="store_true", help="list configured jobs and cases")
  parser.add_argument("--no-clean", action="store_true", help="do not remove previous per-case work directories")
  parser.add_argument("--keep-work", action="store_true",
                      help="keep the whole work directory of passing cases (default: keep logs and reports only)")
  parser.add_argument("--keep-going", action="store_true", help="continue after a failed case")
  parser.add_argument("--evaluate-only", action="store_true",
                      help="do not run tools; check the existing results in the work root against the expected results")
  parser.add_argument("--campaign", metavar="NAME",
                      help="campaign run for docs/results: the work root must be new or empty, nothing is pruned, "
                           "and the work root is archived in $LM_EDA_ARCHIVE_DIR (tools/eda_archive.py)")
  return parser


def main(argv: list[str] | None = None) -> int:
  parser = build_parser()
  args = parser.parse_args(argv)
  config = _load_config(args.config) if args.config else {}
  cases = _case_map(config)

  if args.list:
    _print_list(config, cases)
    return 0

  jobs = _selected_jobs(config, set(args.job), args.include_disabled)
  if not jobs:
    raise SystemExit("no jobs selected; enable a job in the config or pass --include-disabled")

  selected_cases = _selected_cases(cases, set(args.case))
  run_config = config.get("run", {})
  configured_root = run_config.get("work_root", config.get("work_root"))
  work_root = args.work_root or (Path(configured_root) if configured_root else default_work_root())
  if not work_root.is_absolute():
    work_root = ROOT_DIR / work_root

  items = _build_run_items(jobs, selected_cases)
  if not items:
    raise SystemExit("no job/case combinations selected")
  if args.campaign:
    if args.dry_run or args.evaluate_only:
      raise SystemExit("--campaign needs real runs (not --dry-run or --evaluate-only)")
    if work_root.exists() and any(work_root.iterdir()):
      raise SystemExit(f"--campaign: the work root {work_root} must be new or empty")

  results: list[dict[str, Any]] = []
  exit_code = 0
  for item in items:
    result = _run_item(item, work_root, args.dry_run, not args.no_clean, args.evaluate_only)
    # A passing run keeps only its logs and reports; a failing one, and every
    # run of a campaign, keeps everything.
    if result["status"] == "pass" and not (args.dry_run or args.evaluate_only or args.keep_work or args.campaign):
      prune(Path(result["work_dir"]), KEEP_AFTER_PASS)
    results.append(result)
    print(f"{result['status']:9} {result['job']} {result['case']} {result.get('message', '')}", flush=True)
    if result["status"] in ("fail", "tool-error", "timeout", "unchecked", "not-run"):
      exit_code = 1
      if not args.keep_going and result["status"] != "not-run":
        break

  _write_summary(work_root, results)
  if args.campaign:
    archive = archive_campaign(args.campaign, work_root)
    _write_summary(work_root, results, archive)
    print(f"archived the campaign: {archive['archive']} ({archive['files']} files, sha256 {archive['sha256']})")
  print(f"wrote vendor synthesis summary to {work_root}")
  return exit_code
