# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Run local vendor synthesis smoke campaigns for lm_memories."""

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

try:
  import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
  tomllib = None


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_WORK_ROOT = Path("build/vendor_synth")
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

SOURCE_COMMON = ("src/common/lm_mem_pkg.vhd",)
SOURCE_GENERIC_CRW_CRW = ("src/generic/lm_mem_ram_crw_crw.vhd",)
SOURCE_GENERIC_R_W = (
  "src/generic/lm_mem_ram_crw_crw.vhd",
  "src/generic/lm_mem_ram_r_w.vhd",
)
SOURCE_GENERIC_RW_RW = (
  "src/generic/lm_mem_ram_crw_crw.vhd",
  "src/generic/lm_mem_ram_rw_rw.vhd",
)
SOURCE_INFER_R_W = ("src/inference/lm_mem_ram_r_w_infer.vhd",)
SOURCE_PRIMITIVES = {
  "amd": "src/primitives/xilinx/lm_mem_xilinx_ram_r_w_prim.vhd",
  "xilinx": "src/primitives/xilinx/lm_mem_xilinx_ram_r_w_prim.vhd",
  "intel": "src/primitives/intel/lm_mem_intel_ram_r_w_prim.vhd",
  "altera": "src/primitives/intel/lm_mem_intel_ram_r_w_prim.vhd",
  "lattice": "src/primitives/lattice/lm_mem_lattice_ram_r_w_prim.vhd",
  "microchip": "src/primitives/microchip/lm_mem_microchip_ram_r_w_prim.vhd",
  "microsemi": "src/primitives/microchip/lm_mem_microchip_ram_r_w_prim.vhd",
}
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
    "style": "bram",
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
    lines.append(quoted)
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

  raise SystemExit(f"{case['name']}: unsupported case kind {kind}")


def _source_files_for(job: dict[str, Any], case: dict[str, Any]) -> list[Path]:
  kind = str(case["kind"])
  relative: list[str] = list(SOURCE_COMMON)

  if kind == "generic_r_w":
    relative.extend(SOURCE_GENERIC_R_W)
  elif kind == "generic_rw_rw":
    relative.extend(SOURCE_GENERIC_RW_RW)
  elif kind == "generic_crw_crw":
    relative.extend(SOURCE_GENERIC_CRW_CRW)
  elif kind == "infer_r_w":
    relative.extend(SOURCE_INFER_R_W)
  elif kind == "primitive_r_w":
    vendor = _vendor(job)
    primitive_source = SOURCE_PRIMITIVES.get(vendor)
    if primitive_source is None:
      raise SystemExit(f"{case['name']}: unsupported primitive vendor {vendor}")
    relative.extend(SOURCE_GENERIC_R_W)
    relative.append(primitive_source)
  else:
    raise SystemExit(f"{case['name']}: unsupported case kind {kind}")

  return [ROOT_DIR / path for path in relative]


def _vivado_script(job: dict[str, Any], top: str, sources: list[Path], script: Path, reports: Path) -> list[str]:
  part = job.get("part")
  if not part:
    raise SystemExit(f"{_job_name(job)}: Vivado jobs require part")
  threads = int(job.get("threads", 0))
  script_dir = _tcl_path(script.parent)
  source_lines = "\n".join(f"read_vhdl -vhdl2008 {{{_tcl_path(path)}}}" for path in sources)
  thread_line = f"set_param general.maxThreads {threads}\n" if threads >= 2 else ""
  text = f"""# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
{thread_line}set script_dir {{{script_dir}}}
file mkdir [file join $script_dir reports]
create_project -force lm_vendor_synth [file join $script_dir project] -part {{{part}}}
set_property target_language VHDL [current_project]
{source_lines}
synth_design -top {top} -part {{{part}}}
report_utilization -hierarchical -file [file join $script_dir reports utilization_hier.rpt]
catch {{ report_ram_utilization -file {{{_tcl_path(reports / "ram_utilization.rpt")}}} }}
catch {{ report_synth -file {{{_tcl_path(reports / "synthesis.rpt")}}} }}
catch {{ report_timing_summary -file [file join $script_dir reports timing_summary.rpt] }}
write_checkpoint -force [file join $script_dir reports synth.dcp]
exit
"""
  script.write_text(text, encoding="utf-8")
  return _tool_command(job, "vivado", ["-mode", "batch", "-source", _tcl_path(script)], script.parent)


def _quartus_script(job: dict[str, Any], top: str, sources: list[Path], script: Path, reports: Path) -> list[str]:
  family = job.get("family")
  if not family:
    raise SystemExit(f"{_job_name(job)}: Quartus jobs require family")
  device = job.get("device")
  project = _sanitize(_job_name(job)) + "_" + top
  source_lines = "\n".join(f"set_global_assignment -name VHDL_FILE {{{_tcl_path(path)}}}" for path in sources)
  device_line = f"set_global_assignment -name DEVICE {{{device}}}\n" if device else ""
  text = f"""# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
load_package flow
project_new {project} -overwrite
set_global_assignment -name FAMILY {{{family}}}
{device_line}set_global_assignment -name TOP_LEVEL_ENTITY {top}
set_global_assignment -name VHDL_INPUT_VERSION VHDL_2008
{source_lines}
execute_flow -compile
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


def _tool_script(job: dict[str, Any], top: str, sources: list[Path], script: Path, reports: Path) -> list[str]:
  tool = _tool(job)
  if tool in ("vivado", "xilinx"):
    return _vivado_script(job, top, sources, script, reports)
  if tool in ("quartus", "intel", "altera"):
    return _quartus_script(job, top, sources, script, reports)
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


def _run_item(item: RunItem, work_root: Path, dry_run: bool, clean: bool) -> dict[str, Any]:
  job = item.job
  case = item.case
  top = _top_name(job, case)
  case_dir = work_root / _sanitize(_job_name(job)) / _sanitize(case["name"])
  reports = case_dir / "reports"

  if clean and case_dir.exists():
    shutil.rmtree(case_dir)
  reports.mkdir(parents=True, exist_ok=True)

  top_file = case_dir / f"{top}.vhd"
  script_file = case_dir / f"run_{_tool(job)}.tcl"
  sources = _source_files_for(job, case) + [top_file]
  top_file.write_text(_render_top(job, case, top), encoding="utf-8")
  command = _tool_script(job, top, sources, script_file, reports)
  log_file = case_dir / "run.log"

  status = "dry-run"
  return_code: int | None = None
  started_at = dt.datetime.now(dt.timezone.utc).isoformat()
  completed_at = started_at

  if dry_run:
    log_file.write_text(
      "dry run: command was not executed\ncommand: " + " ".join(command) + "\n",
      encoding="utf-8",
    )
  else:
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in job.get("env", {}).items()})
    timeout = int(job.get("timeout_seconds", 3600))
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    with log_file.open("w", encoding="utf-8", errors="ignore") as handle:
      handle.write("command: " + " ".join(command) + "\n")
      handle.flush()
      try:
        process = subprocess.run(
          command,
          cwd=case_dir,
          env=env,
          stdout=handle,
          stderr=subprocess.STDOUT,
          check=False,
          timeout=timeout,
        )
        return_code = process.returncode
        status = "pass" if process.returncode == 0 else "fail"
      except subprocess.TimeoutExpired:
        status = "timeout"
        return_code = None
        handle.write(f"\ntimeout after {timeout} seconds\n")
    completed_at = dt.datetime.now(dt.timezone.utc).isoformat()

  reports_found = _report_files(case_dir)
  failure_message = _failure_message(reports_found)
  if failure_message and status == "pass":
    status = "fail"
  memory_lines = _collect_memory_lines(reports_found)
  return {
    "job": _job_name(job),
    "tool": _tool(job),
    "vendor": _vendor(job),
    "family": _family(job),
    "case": case["name"],
    "case_kind": case["kind"],
    "status": status,
    "return_code": return_code,
    "top": top,
    "work_dir": str(case_dir),
    "script": str(script_file),
    "log": str(log_file),
    "command": command,
    "started_at": started_at,
    "completed_at": completed_at,
    "reports": [str(path) for path in reports_found],
    "message": failure_message,
    "memory_lines": memory_lines,
  }


def _write_summary(work_root: Path, results: list[dict[str, Any]]) -> None:
  work_root.mkdir(parents=True, exist_ok=True)
  summary_json = work_root / "summary.json"
  summary_csv = work_root / "summary.csv"
  summary_md = work_root / "summary.md"

  summary_json.write_text(json.dumps({"results": results}, indent=2) + "\n", encoding="utf-8")

  with summary_csv.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=["job", "tool", "vendor", "family", "case", "case_kind", "status", "return_code", "work_dir"])
    writer.writeheader()
    for result in results:
      writer.writerow({key: result.get(key) for key in writer.fieldnames})

  lines = [
    "# Vendor Synthesis Summary",
    "",
    "| Job | Tool | Case | Status | Return Code | Message |",
    "| --- | --- | --- | --- | --- | --- |",
  ]
  for result in results:
    lines.append(
      f"| {result['job']} | {result['tool']} | {result['case']} | "
      f"{result['status']} | {result['return_code']} | {result.get('message', '')} |"
    )
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
  parser.add_argument("--keep-going", action="store_true", help="continue after a failed tool run")
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
  work_root = args.work_root or Path(run_config.get("work_root", config.get("work_root", DEFAULT_WORK_ROOT)))
  if not work_root.is_absolute():
    work_root = ROOT_DIR / work_root

  items = _build_run_items(jobs, selected_cases)
  if not items:
    raise SystemExit("no job/case combinations selected")

  results: list[dict[str, Any]] = []
  exit_code = 0
  for item in items:
    result = _run_item(item, work_root, args.dry_run, not args.no_clean)
    results.append(result)
    if result["status"] in ("fail", "timeout"):
      exit_code = 1
      if not args.keep_going:
        break

  _write_summary(work_root, results)
  print(f"wrote vendor synthesis summary to {work_root}")
  return exit_code
