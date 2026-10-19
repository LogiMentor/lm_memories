#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Post-synthesis simulation of the two-writer RAMs on AMD (docs/PLAN.md 3.6).

For each part and each two-writer configuration of sim/vunit/configs.py:

1. Vivado synthesizes the RTL DUT adapter (lm_mem_tb_dut) with the fixed
   generics of the configuration, out of context, flattened, from the
   synth-amd flow, and writes a functional netlist (write_vhdl -mode funcsim).
2. A generated DUT adapter, architecture "netlist", instantiates the netlist
   whose generics match; it replaces sim/vunit/lm_mem_tb_dut.vhd.
3. The same harness and scoreboard (flow test-postsynth, plain top
   tb_lm_mem_ram_plain) run on the netlist and, for comparison, on the RTL
   (simulation architecture), with GHDL (UNISIM analyzed from the Vivado
   sources, -frelaxed only for that library) and with xsim (precompiled
   UNISIM).

Verdicts need positive evidence:
- synthesis: "synth_design completed successfully", exit code 0, no error,
  0 CRITICAL WARNINGs, a complete utilization report and a netlist written
  by this run;
- simulation: exit code 0, exactly one RESULT line and it is PASS, exactly
  one harness line with every counter and errors=0, no fatal diagnostic;
- a selection without any netlist comparison is an error (exit code 2).
A netlist is reused only if synth.json records the same inputs (sources,
manifest, wrapper, part: tools/eda_provenance.py); otherwise it is
synthesized again.

Results: <work>/results.json and results.md, <work> = --work-root or
$LM_EDA_WORKDIR/ps; after a passing run only the results and the synthesis
logs and reports stay (--keep-work keeps everything). A --campaign run keeps
everything and archives it (tools/eda_archive.py). Every Vivado run and
every simulation batch holds the machine-wide EDA lock.

  python sim/scripts/run_postsynth.py                      # both parts, GHDL and xsim
  python sim/scripts/run_postsynth.py --part xc7a35tfgg484-2 --sim ghdl --config "crw_crw.*"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "sim" / "vunit"))

from configs import POSTSYNTH_COMMON_CLOCK, POSTSYNTH_PARTS, RAM_CONFIGS  # noqa: E402
from tools.eda_archive import archive_campaign  # noqa: E402
from tools.eda_lock import heavy_tool_lock  # noqa: E402
from tools.eda_proc import run_tool  # noqa: E402
from tools.eda_provenance import commit, manifest_hash, provenance, same_inputs  # noqa: E402
from tools.eda_retry import run_with_retries  # noqa: E402
from tools.eda_workdir import prune, work_root  # noqa: E402
from tools.manifest import load  # noqa: E402
from tools.vendor_synth import check  # noqa: E402

GHDL = os.environ.get("GHDL", "ghdl")
PARTS = ["xc7a35tfgg484-2", "xcku3p-ffva676-2-e"]
# Kept in each synthesis directory after a passing campaign.
KEEP_SYNTH = ("synth.json", "vivado*.log", "utilization.rpt", "run.tcl")
ADAPTER_RTL = ROOT_DIR / "sim" / "vunit" / "lm_mem_tb_dut.vhd"
HARNESS_FILESET = "tb_harness"
RESULT_RE = re.compile(r"lm_mem_tb_harness \S+ \[[^\]]*\]: (.*)")
VERDICT_RE = re.compile(r"tb_lm_mem_ram_plain: RESULT (PASS|FAIL)[^\n]*")
# Every counter of the harness line (f_tb_counts_image in lm_mem_tb_pkg).
REQUIRED_COUNTS = ("writes", "writes_masked", "writes_beyond_depth", "reads", "checked", "excluded_rw",
                   "excluded_undefined", "excluded_beyond_depth", "collisions_rw", "collisions_ww", "init_reads",
                   "old_data_reads", "hold_checks", "undefined_output_checks", "errors")
# Diagnostics that end or invalidate a run: failures and errors of the
# simulators (GHDL, xsim) and of the testbench.
FATAL_RE = re.compile(r"^.*(?:\((?:assertion|report) (?:failure|error)\)|bound check failure|FATAL_ERROR|"
                      r"^(?:Error|Failure|ERROR): ).*$", re.MULTILINE)


def tcl(path: Path) -> str:
  return str(path).replace("\\", "/")


def log2(n: int) -> int:
  return max(1, math.ceil(math.log2(n))) if n > 1 else 1


def netlist_key(name: str, g: dict) -> str:
  short = g["g_dut"].removeprefix("lm_mem_ram_")
  key = f"{short}_{g['g_a_data_w']}x{g['g_a_depth']}_{g['g_b_data_w']}_l{g['g_a_latency']}{g['g_b_latency']}"
  # Common clock: both clock ports of the entity on one clock in the netlist.
  return key + "_cc" if name in POSTSYNTH_COMMON_CLOCK else key


def port_list(g: dict) -> str:
  b_depth = g["g_a_depth"] * g["g_a_data_w"] // g["g_b_data_w"]
  return f"""    clk_a_i  : in  std_logic;
    clk_b_i  : in  std_logic;
    ena_i    : in  std_logic;
    enb_i    : in  std_logic;
    wen_a_i  : in  std_logic;
    wen_b_i  : in  std_logic;
    addr_a_i : in  std_logic_vector({log2(g['g_a_depth']) - 1} downto 0);
    addr_b_i : in  std_logic_vector({log2(b_depth) - 1} downto 0);
    din_a_i  : in  std_logic_vector({g['g_a_data_w'] - 1} downto 0);
    din_b_i  : in  std_logic_vector({g['g_b_data_w'] - 1} downto 0);
    dout_a_o : out std_logic_vector({g['g_a_data_w'] - 1} downto 0);
    dout_b_o : out std_logic_vector({g['g_b_data_w'] - 1} downto 0)"""


PORT_NAMES = ["clk_a_i", "clk_b_i", "ena_i", "enb_i", "wen_a_i", "wen_b_i",
              "addr_a_i", "addr_b_i", "din_a_i", "din_b_i", "dout_a_o", "dout_b_o"]


def wrapper_vhdl(key: str, g: dict, common_clock: bool = False) -> str:
  """Synthesis top: the RTL adapter with the generics of one configuration.

  common_clock: clk_b_i of the adapter is driven by clk_a_i, so the netlist
  has one clock (the wrapper port clk_b_i stays, unused).
  """
  ports = ",\n".join(f"      {p} => {'clk_a_i' if common_clock and p == 'clk_b_i' else p}" for p in PORT_NAMES)
  return f"""-- Generated by sim/scripts/run_postsynth.py: synthesis top for {key}.
library ieee;
use ieee.std_logic_1164.all;

entity lm_ps_{key} is
  port(
{port_list(g)}
  );
end entity;

architecture a_rtl of lm_ps_{key} is
begin
  inst : entity work.lm_mem_tb_dut
    generic map(
      g_dut       => "{g['g_dut']}",
      g_a_data_w  => {g['g_a_data_w']},
      g_a_depth   => {g['g_a_depth']},
      g_b_data_w  => {g['g_b_data_w']},
      g_a_latency => {g['g_a_latency']},
      g_b_latency => {g['g_b_latency']}
    )
    port map(
{ports}
    );
end architecture;
"""


def adapter_vhdl(entries: list[tuple[str, dict]]) -> str:
  """lm_mem_tb_dut, architecture netlist: one generate per synthesized netlist."""
  ports = ",\n".join(f"        {p} => {p}" for p in PORT_NAMES)
  conds, blocks = [], []
  for key, g in entries:
    cond = (f'(g_dut = "{g["g_dut"]}") and (g_a_data_w = {g["g_a_data_w"]}) and (g_a_depth = {g["g_a_depth"]}) and '
            f'(g_b_data_w = {g["g_b_data_w"]}) and (g_a_latency = {g["g_a_latency"]}) and (g_b_latency = {g["g_b_latency"]})')
    conds.append(f"({cond})")
    blocks.append(f"""  gen_{key} : if {cond} generate
    inst_dut : entity work.lm_ps_{key}
      port map(
{ports}
      );
  end generate gen_{key};
""")
  return f"""-- Generated by sim/scripts/run_postsynth.py: DUT adapter on netlists.
-- Same entity as sim/vunit/lm_mem_tb_dut.vhd, architecture "netlist".
library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity lm_mem_tb_dut is
  generic(
    g_dut       : string   := "lm_mem_ram_crw_crw";
    g_a_data_w  : positive := 32;
    g_a_depth   : positive := 1024;
    g_b_data_w  : positive := 32;
    g_a_latency : positive := 1;
    g_b_latency : positive := 1
  );
  port(
    clk_a_i  : in  std_logic;
    clk_b_i  : in  std_logic;
    ena_i    : in  std_logic;
    enb_i    : in  std_logic;
    wen_a_i  : in  std_logic;
    wen_b_i  : in  std_logic;
    addr_a_i : in  std_logic_vector(f_ceil_log2(g_a_depth) - 1 downto 0);
    addr_b_i : in  std_logic_vector(f_ceil_log2((g_a_depth * g_a_data_w) / g_b_data_w) - 1 downto 0);
    din_a_i  : in  std_logic_vector(g_a_data_w - 1 downto 0);
    din_b_i  : in  std_logic_vector(g_b_data_w - 1 downto 0);
    dout_a_o : out std_logic_vector(g_a_data_w - 1 downto 0);
    dout_b_o : out std_logic_vector(g_b_data_w - 1 downto 0)
  );
end entity lm_mem_tb_dut;

architecture netlist of lm_mem_tb_dut is
begin
  assert {" or ".join(conds)}
    report "lm_mem_tb_dut (netlist): no synthesized netlist for " & g_dut & " with these generics"
    severity failure;

{"".join(blocks)}end architecture netlist;
"""


# Exit code recorded for a tool stopped by the timeout of run_logged.
TIMEOUT_RC = -1


def run_logged(cmd: list[str], cwd: Path, log: Path, timeout: int = 7200) -> int:
  """Run a tool with its output appended to log; TIMEOUT_RC after a timeout.

  On a timeout the whole process tree of the tool is stopped first
  (tools/eda_proc.py), so the caller releases the EDA lock after the tool.
  """
  with log.open("a", encoding="utf-8", errors="ignore") as handle:
    handle.write("$ " + " ".join(cmd) + "\n")
    handle.flush()
    try:
      return run_tool(cmd, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
      handle.write(f"\ntimeout after {timeout} seconds: the tool was stopped\n")
      return TIMEOUT_RC


def synth_inputs(part: str, key: str, g: dict, work: Path, common_clock: bool = False) -> dict:
  """Wrapper, script and provenance of one synthesis; nothing is written."""
  netlist = work / f"lm_ps_{key}_funcsim.vhd"
  wrapper = work / f"lm_ps_{key}.vhd"
  wrapper_text = wrapper_vhdl(key, g, common_clock)
  library = [s.path for s in load().files("synth-amd", "vivado")] + [ADAPTER_RTL]
  reads = "\n".join(f"read_vhdl -vhdl2008 {{{tcl(s)}}}" for s in [*library, wrapper])
  # Exits with 1 when synth_design fails (Vivado itself exits with 0).
  synth_line = check.vivado_synth_command(
    f"synth_design -mode out_of_context -top lm_ps_{key} -part {{{part}}} -flatten_hierarchy full")
  script_text = f"""set_param general.maxThreads 2
catch {{ set_param messaging.defaultLimit 1000000 }}
{reads}
{synth_line}
report_utilization -file {{{tcl(work / 'utilization.rpt')}}}
write_vhdl -mode funcsim -force {{{tcl(netlist)}}}
exit
"""
  # The script names work paths; the provenance covers what changes the netlist.
  inputs = provenance(library, extra=[wrapper_text, f"part={part}", f"top=lm_ps_{key}", "flatten_hierarchy=full"])
  return {"netlist": netlist, "wrapper": wrapper, "wrapper_text": wrapper_text, "script_text": script_text,
          "provenance": inputs}


def synthesize(vivado_dir: Path, part: str, key: str, g: dict, work: Path, common_clock: bool = False) -> dict:
  """Synthesize one configuration. ok needs positive evidence of a complete run:
  "synth_design completed successfully", exit code 0, no project error, 0
  CRITICAL WARNINGs, a complete utilization report and a netlist written by
  this run."""
  work.mkdir(parents=True, exist_ok=True)
  inputs = synth_inputs(part, key, g, work, common_clock)
  netlist = inputs["netlist"]
  inputs["wrapper"].write_text(inputs["wrapper_text"], encoding="utf-8")
  script = work / "run.tcl"
  script.write_text(inputs["script_text"], encoding="utf-8")
  util = work / "utilization.rpt"
  log = work / "vivado.log"
  cmd = (["cmd", "/c", str(vivado_dir / "bin" / "vivado.bat")] if os.name == "nt" else
         [str(vivado_dir / "bin" / "vivado")]) + ["-mode", "batch", "-nojournal", "-source", tcl(script)]
  code = None

  def run_once(attempt: int) -> str:
    nonlocal code
    # Outputs of an earlier attempt or run must never count for this one.
    for old in (netlist, util):
      old.unlink(missing_ok=True)
    log.write_text("", encoding="utf-8")
    with heavy_tool_lock(f"postsynth vivado {part} {key}", kind="synthesis"):
      code = run_logged(cmd, work, log)
    return log.read_text(encoding="utf-8", errors="ignore")

  def completed(text: str) -> bool:
    return code == TIMEOUT_RC or (code == 0 and check.tool_completed("vivado", text))

  # Shared retry policy (tools/eda_retry.py): every retried attempt is
  # recorded and its log kept as vivado.attempt<n>.log.
  text, retries, persisted = run_with_retries(run_once, completed=completed, roots=[vivado_dir], log_path=log)
  parsed = check.parse_vivado_log(text, f"lm_ps_{key}")
  metrics = check.parse_vivado_utilization(util.read_text(encoding="utf-8", errors="ignore")) if util.is_file() else {}
  problems = []
  if code == TIMEOUT_RC:
    problems.append("timeout")
  elif code != 0:
    problems.append(f"Vivado exit code {code}")
  if not parsed["completed"]:
    problems.append("no 'synth_design completed successfully' in the log")
  if parsed["project_error"] or parsed["errors"]:
    problems.append(parsed["project_error"] or parsed["errors"][0])
  if persisted:
    problems.append("transient tool failure in every attempt: " + retries[-1])
  if parsed["critical_warnings"]:
    problems.append(f"{parsed['critical_warnings']} CRITICAL WARNINGs")
  missing = [k for k in check.REQUIRED_METRICS if metrics.get(k) is None]
  if missing:
    problems.append("utilization report missing or incomplete: no " + ", ".join(missing))
  if not netlist.is_file():
    problems.append("no netlist")
  # Synth 8-6794: Vivado chose URAM without an attribute.
  uram_messages = [l.strip() for l in text.splitlines() if "[Synth 8-6794]" in l][:2]
  return {"ok": not problems, "problems": problems, "tool_error": persisted, "netlist": str(netlist),
          "metrics": metrics, "uram_messages": uram_messages,
          "critical_warnings": parsed["critical_warnings"], "errors": parsed["errors"][:3],
          "version": parsed["version"], "retries": retries, "provenance": inputs["provenance"]}


def unisim_cells(netlists: list[Path]) -> list[str]:
  cells: set[str] = set()
  for path in netlists:
    cells |= {m.upper() for m in re.findall(r":\s*unisim\.vcomponents\.(\w+)", path.read_text(errors="ignore"), re.I)}
  return sorted(cells)


def tb_sources(mode: str) -> tuple[list[Path], list[Path]]:
  """(library sources, test sources): the library is compiled only for the RTL mode."""
  lib = [s.path for s in load().files("sim")] if mode == "rtl" else []
  return lib, [s.path for s in load().files("test-postsynth")]


def harness_index(files: list[Path]) -> int:
  harness = {s.path for s in load().files("test-postsynth") if s.fileset == HARNESS_FILESET}
  return next(i for i, f in enumerate(files) if f in harness)


def compile_order(mode: str, adapter: Path | None, netlists: list[Path]) -> list[Path]:
  lib, tb = tb_sources(mode)
  # Drop the package from the test list when the library flow already has it.
  tb = [f for f in tb if f not in lib]
  idx = harness_index(tb)
  middle = [ADAPTER_RTL] if mode == "rtl" else [*netlists, adapter]
  return lib + tb[:idx] + middle + tb[idx:]


def generics_args(g: dict, sim_info: str, rtl: bool) -> dict:
  """Testbench generics. 'X' after a write/write collision, and undefined
  outputs before the first read, are properties of the RTL; a netlist returns
  defined values in both cases, so they are checked on the RTL only."""
  args = {k: v for k, v in g.items() if k not in ("g_sim_info", "g_x_on_undef", "g_undef_until_rd")}
  args["g_x_on_undef"] = rtl
  args["g_undef_until_rd"] = rtl
  args["g_sim_info"] = sim_info
  return args


def vhdl_value(value) -> str:
  if isinstance(value, bool):
    return "true" if value else "false"
  return str(value)


def parse_result(log_text: str, return_code: int | None) -> dict:
  """Verdict of one simulation run. PASS needs all of: exit code 0, exactly
  one RESULT line and it is PASS, exactly one harness line with every counter,
  0 errors in it, and no fatal diagnostic anywhere in the log."""
  verdicts = VERDICT_RE.findall(log_text)
  harness = RESULT_RE.findall(log_text)
  counts = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", harness[0])} if len(harness) == 1 else {}
  problems = []
  if return_code == TIMEOUT_RC:
    problems.append("timeout")
  elif return_code != 0:
    problems.append(f"simulator exit code {return_code}")
  if verdicts != ["PASS"]:
    problems.append(f"RESULT lines: {verdicts or 'none'} (expected exactly one PASS)")
  if len(harness) != 1:
    problems.append(f"{len(harness)} harness result lines (expected 1)")
  missing = [k for k in REQUIRED_COUNTS if k not in counts]
  if counts and missing:
    problems.append("harness counters missing: " + ", ".join(missing))
  if counts.get("errors", 0) != 0:
    problems.append(f"errors={counts['errors']}")
  fatal = FATAL_RE.findall(log_text)
  if fatal:
    problems.append(f"{len(fatal)} fatal diagnostics in the log, first: {fatal[0].strip()[:120]}")
  verdict = "PASS" if not problems else ("TIMEOUT" if return_code == TIMEOUT_RC else "FAIL")
  return {"verdict": verdict, "counts": counts, "problems": problems}


def ghdl_version() -> str:
  out = subprocess.run([GHDL, "--version"], capture_output=True, text=True).stdout.splitlines()
  return out[0].strip() if out else "GHDL"


def run_ghdl(vivado_dir: Path, mode: str, files: list[Path], cells: list[str], cfgs: list, work: Path) -> dict:
  if work.exists():
    shutil.rmtree(work)
  work.mkdir(parents=True)
  log = work / "ghdl.log"
  results = {}
  sim_info = re.sub(r"[^A-Za-z0-9._()-]+", "_", ghdl_version())
  with heavy_tool_lock(f"postsynth ghdl {mode} {work.parent.name}", kind="simulation"):
    unisim_dir = work / "unisim"
    lib_flags = []
    if mode == "netlist":
      unisim_dir.mkdir()
      src = vivado_dir / "data" / "vhdl" / "src" / "unisims"
      vendor = [src / "unisim_VPKG.vhd", src / "unisim_VCOMP.vhd"] + [src / "primitive" / f"{c}.vhd" for c in cells]
      for f in vendor:
        # Relaxed flags only for the vendor library.
        if run_logged([GHDL, "-a", "--std=08", "-frelaxed", "-fsynopsys", "--work=unisim",
                       f"--workdir={unisim_dir}", str(f)], work, log) != 0:
          return {"error": f"GHDL could not analyze {f.name} (see {log})"}
      lib_flags = [f"-P{unisim_dir}"]
    for f in files:
      if run_logged([GHDL, "-a", "--std=08", f"--workdir={work}", *lib_flags, str(f)], work, log) != 0:
        return {"error": f"GHDL could not analyze {f.name} (see {log})"}
    # Elaboration and run also elaborate the UNISIM units, which need the
    # flags of their analysis. Our own files were analyzed without them.
    if mode == "netlist":
      lib_flags += ["-frelaxed", "-fsynopsys"]
    if run_logged([GHDL, "-e", "--std=08", f"--workdir={work}", *lib_flags, "tb_lm_mem_ram_plain"], work, log) != 0:
      return {"error": f"GHDL elaboration failed (see {log})"}
    for name, g in cfgs:
      run_log = work / f"{name}.log"
      gens = generics_args(g, sim_info, mode == "rtl")
      cmd = [GHDL, "-r", "--std=08", f"--workdir={work}", *lib_flags, "tb_lm_mem_ram_plain",
             *[f"-g{k}={vhdl_value(v)}" for k, v in gens.items()], "--ieee-asserts=disable-at-0"]
      run_log.write_text("", encoding="utf-8")
      rc = run_logged(cmd, work, run_log)
      results[name] = parse_result(run_log.read_text(encoding="utf-8", errors="ignore"), rc)
  return {"sim": ghdl_version(), "results": results}


def run_xsim(vivado_dir: Path, mode: str, files: list[Path], cfgs: list, work: Path) -> dict:
  if work.exists():
    shutil.rmtree(work)
  work.mkdir(parents=True)
  bindir = vivado_dir / "bin"
  log = work / "xsim.log"
  results = {}

  def tool(name: str, args_file: Path) -> int:
    exe = str(bindir / (name + (".bat" if os.name == "nt" else "")))
    cmd = ["cmd", "/c", exe, "-f", str(args_file)] if os.name == "nt" else [exe, "-f", str(args_file)]
    return run_logged(cmd, work, log)

  version = "xsim"
  with heavy_tool_lock(f"postsynth xsim {mode} {work.parent.name}", kind="simulation"):
    prj = work / "xvhdl.args"
    prj.write_text("--2008\n--nolog\n" + "\n".join(f'"{tcl(f)}"' for f in files) + "\n", encoding="utf-8")
    if tool("xvhdl", prj) != 0:
      return {"error": f"xvhdl failed (see {log})"}
    exe = str(bindir / ("xsim" + (".bat" if os.name == "nt" else "")))
    banner = subprocess.run(["cmd", "/c", exe, "--version"] if os.name == "nt" else [exe, "--version"],
                            capture_output=True, text=True).stdout
    found = re.search(r"(?:xsim|Simulator) v?(\d{4}\.\d+)", banner)
    version = f"xsim {found.group(1)}" if found else "xsim (version unknown)"
    sim_info = re.sub(r"[^A-Za-z0-9._()-]+", "_", version)
    for name, g in cfgs:
      snap = "snap_" + re.sub(r"\W", "_", name)
      gens = generics_args(g, sim_info, mode == "rtl")
      elab = work / f"{snap}.xelab.args"
      lines = ["work.tb_lm_mem_ram_plain", "-L", "unisim", "-debug", "off", "-s", snap, "--nolog"]
      # xelab -f: one token per line, no quoting (string values have no spaces).
      for k, v in gens.items():
        lines += ["-generic_top", f"{k}={vhdl_value(v)}"]
      elab.write_text("\n".join(lines) + "\n", encoding="utf-8")
      if tool("xelab", elab) != 0:
        results[name] = {"verdict": "ELAB FAILED", "counts": {}, "problems": [f"xelab failed (see {log})"]}
        continue
      run_log = work / f"{name}.log"
      sim_args = work / f"{snap}.xsim.args"
      sim_args.write_text(f"{snap}\n-R\n--log\n\"{tcl(run_log)}\"\n", encoding="utf-8")
      rc = tool("xsim", sim_args)
      text = run_log.read_text(encoding="utf-8", errors="ignore") if run_log.is_file() else ""
      results[name] = parse_result(text, rc)
  return {"sim": version, "results": results}


def reusable(work: Path, inputs: dict) -> tuple[dict | None, str]:
  """The record of an earlier synthesis of the same inputs, or None and why not."""
  record_file = work / "synth.json"
  if not inputs["netlist"].is_file():
    return None, "no netlist"
  try:
    record = json.loads(record_file.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return None, "no synth.json"
  if not record.get("ok"):
    return None, "the earlier synthesis failed"
  if not same_inputs(record.get("provenance"), inputs["provenance"]):
    return None, "sources, manifest or configuration changed"
  return record, "same inputs"


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--part", action="append", help=f"default: {', '.join(PARTS)}")
  parser.add_argument("--sim", action="append", choices=("ghdl", "xsim"), help="default: both")
  parser.add_argument("--config", default=".*", help="regular expression on the configuration name")
  parser.add_argument("--vivado-dir", type=Path, default=Path(os.environ.get("LM_VIVADO_DIR", "C:/Xilinx/Vivado/2023.1")))
  parser.add_argument("--work-root", type=Path, default=work_root("ps"),
                      help="default: $LM_EDA_WORKDIR/ps (tools/eda_workdir.py)")
  parser.add_argument("--keep-work", action="store_true",
                      help="keep netlists and simulation directories after a passing run")
  parser.add_argument("--resynth", action="store_true", help="synthesize again even if a netlist of the same inputs exists")
  parser.add_argument("--campaign", metavar="NAME",
                      help="campaign run for docs/results: the work root must be new or empty, nothing is pruned, "
                           "and the work root is archived in $LM_EDA_ARCHIVE_DIR (tools/eda_archive.py)")
  args = parser.parse_args(argv)

  parts = args.part or PARTS
  sims = args.sim or ["ghdl", "xsim"]
  cfgs = [(n, g) for n, g in RAM_CONFIGS if n in POSTSYNTH_PARTS and re.search(args.config, n)]
  part_cfgs = {part: [(n, g) for n, g in cfgs if part in POSTSYNTH_PARTS[n]] for part in parts}
  if not any(part_cfgs.values()):
    # Without a netlist to compare, a run proves nothing.
    print("no post-synthesis comparison selected: no configuration of "
          f"'{args.config}' is synthesized for {', '.join(parts)} (sim/vunit/configs.py POSTSYNTH_PARTS)",
          file=sys.stderr)
    return 2
  root = args.work_root
  if args.campaign and root.exists() and any(root.iterdir()):
    print(f"--campaign: the work root {root} must be new or empty", file=sys.stderr)
    return 2
  out = {"commit": commit(), "manifest_hash": manifest_hash(), "parts": {},
         "configs": [n for n, _ in cfgs if any(n in [m for m, _ in pc] for pc in part_cfgs.values())]}
  failed = False

  # RTL reference, once per simulator (it does not depend on the part).
  rtl = {}
  rtl_cfgs = [(n, g) for n, g in cfgs if n in out["configs"]]
  for sim in sims:
    files = compile_order("rtl", None, [])
    work = root / "rtl" / sim
    rtl[sim] = (run_ghdl(args.vivado_dir, "rtl", files, [], rtl_cfgs, work) if sim == "ghdl"
                else run_xsim(args.vivado_dir, "rtl", files, rtl_cfgs, work))
  out["rtl"] = rtl

  for part in parts:
    if not part_cfgs[part]:
      continue
    part_dir = root / part
    synth = {}
    entries = []
    for name, g in part_cfgs[part]:
      key = netlist_key(name, g)
      if key in synth:
        continue
      work = part_dir / "synth" / key
      inputs = synth_inputs(part, key, g, work, name in POSTSYNTH_COMMON_CLOCK)
      record, why = (None, "--resynth") if args.resynth else reusable(work, inputs)
      if record is not None:
        synth[key] = record
        print(f"{part} {key}: netlist reused ({why})", flush=True)
      else:
        if why != "no netlist":
          print(f"{part} {key}: synthesizing again ({why})", flush=True)
        synth[key] = synthesize(args.vivado_dir, part, key, g, work, name in POSTSYNTH_COMMON_CLOCK)
        (work / "synth.json").write_text(json.dumps(synth[key], indent=1), encoding="utf-8")
      print(f"{part} {key}: synthesis {'ok' if synth[key]['ok'] else 'FAILED ' + '; '.join(synth[key]['problems'])} "
            f"bram={synth[key]['metrics'].get('bram_tiles')} lutram={synth[key]['metrics'].get('lutram')} "
            f"uram={synth[key]['metrics'].get('uram')}", flush=True)
      if synth[key]["ok"]:
        entries.append((key, g))
    adapter = part_dir / "lm_mem_tb_dut_netlist.vhd"
    adapter.write_text(adapter_vhdl(entries), encoding="utf-8")
    netlists = [Path(synth[k]["netlist"]) for k, _ in entries]
    ok_cfgs = [(n, g) for n, g in part_cfgs[part] if synth[netlist_key(n, g)]["ok"]]
    files = compile_order("netlist", adapter, netlists)
    sims_out = {}
    for sim in sims:
      work = part_dir / sim
      sims_out[sim] = (run_ghdl(args.vivado_dir, "netlist", files, unisim_cells(netlists), ok_cfgs, work)
                       if sim == "ghdl" else run_xsim(args.vivado_dir, "netlist", files, ok_cfgs, work))
    out["parts"][part] = {"synth": synth, "sims": sims_out}

  # Report: RTL and netlist side by side.
  lines = ["| Configuration | Part | Simulator | RTL checked / excluded | Netlist checked / excluded | RTL | Netlist |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
  compared = 0
  for part, data in out["parts"].items():
    for sim, res in data["sims"].items():
      for name, _ in part_cfgs[part]:
        r = rtl.get(sim, {}).get("results", {}).get(name, {"verdict": rtl.get(sim, {}).get("error", "-"), "counts": {}})
        n = res.get("results", {}).get(name, {"verdict": res.get("error", "not synthesized"), "counts": {}})
        def ce(x):
          c = x["counts"]
          if not c:
            return "-"
          excl = c.get("excluded_rw", 0) + c.get("excluded_undefined", 0) + c.get("excluded_beyond_depth", 0)
          return f"{c.get('checked', 0)} / {excl}"
        lines.append(f"| {name} | {part} | {sim} | {ce(r)} | {ce(n)} | {r['verdict']} (errors {r['counts'].get('errors', '-')}) | "
                     f"{n['verdict']} (errors {n['counts'].get('errors', '-')}) |")
        failed |= r["verdict"] != "PASS" or n["verdict"] != "PASS"
        compared += 1
  if compared == 0:
    print("no post-synthesis comparison was made", file=sys.stderr)
    failed = True
  root.mkdir(parents=True, exist_ok=True)
  (root / "results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
  (root / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
  print("\n".join(lines))
  if args.campaign:
    # A campaign keeps everything and archives it (tools/eda_archive.py).
    out["archive"] = archive_campaign(args.campaign, root)
    (root / "results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"archived the campaign: {out['archive']['archive']} ({out['archive']['files']} files, "
          f"sha256 {out['archive']['sha256']})")
  # Outside a campaign, a passing run keeps the results and the synthesis
  # logs and reports; netlists and simulation directories go. A failing one
  # keeps everything.
  elif not failed and not args.keep_work:
    for part in out["parts"]:
      for synth_dir in sorted((root / part / "synth").glob("*")):
        prune(synth_dir, KEEP_SYNTH)
      prune(root / part, ["synth"])
    prune(root, ["results.json", "results.md", *out["parts"]])
  return 1 if failed else 0


if __name__ == "__main__":
  raise SystemExit(main())
