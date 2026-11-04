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
  in a netlist simulation, a collision error of a UNISIM block RAM model
  is accepted only when it is paired with a collision of the harness
  (check_collisions);
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
import uuid
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
KEEP_SYNTH = ("synth.json", "vivado*.log", "utilization.rpt", "run.tcl", "synth_status.jsonl")
# Log file of Vivado itself; vivado.log holds the console output of the run.
TOOL_LOG = "vivado_tool.log"
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


def run_logged(cmd: list[str], cwd: Path, log: Path, timeout: int = 7200, env: dict[str, str] | None = None) -> int:
  """Run a tool with its output appended to log; TIMEOUT_RC after a timeout.

  On a timeout the whole process tree of the tool is stopped first
  (tools/eda_proc.py), so the caller releases the EDA lock after the tool.
  """
  with log.open("a", encoding="utf-8", errors="ignore") as handle:
    handle.write("$ " + " ".join(cmd) + "\n")
    handle.flush()
    try:
      return run_tool(cmd, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout)
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
  # Records the outcome in the status file and exits with 1 when synth_design fails.
  synth_line = check.vivado_synth_command(
    f"synth_design -mode out_of_context -top lm_ps_{key} -part {{{part}}} -flatten_hierarchy full",
    tcl(work / check.STATUS_FILE))
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
  "synth_design completed successfully", exit code 0, synth_design recorded
  as "ok" for this attempt in the status file, no project error, 0 CRITICAL
  WARNINGs, a complete utilization report and a netlist written by this
  run."""
  work.mkdir(parents=True, exist_ok=True)
  inputs = synth_inputs(part, key, g, work, common_clock)
  netlist = inputs["netlist"]
  inputs["wrapper"].write_text(inputs["wrapper_text"], encoding="utf-8")
  script = work / "run.tcl"
  script.write_text(inputs["script_text"], encoding="utf-8")
  util = work / "utilization.rpt"
  log = work / "vivado.log"
  # Vivado writes its own log to a separate file: by default it is vivado.log
  # in the working directory, the file that run_logged writes the console
  # output to, and the two writers interleaved their lines (the completion
  # message was lost, 2026-11-04).
  tool_log = work / TOOL_LOG
  cmd = (["cmd", "/c", str(vivado_dir / "bin" / "vivado.bat")] if os.name == "nt" else
         [str(vivado_dir / "bin" / "vivado")]) + ["-mode", "batch", "-nojournal", "-log", tcl(tool_log),
                                                  "-source", tcl(script)]
  status_file = work / check.STATUS_FILE
  code = None
  run_id = None

  def run_once(attempt: int) -> str:
    nonlocal code, run_id
    # Outputs of an earlier attempt or run must never count for this one.
    for old in (netlist, util, status_file):
      old.unlink(missing_ok=True)
    log.write_text("", encoding="utf-8")
    run_id = uuid.uuid4().hex
    env = {**os.environ, check.RUN_ID_ENV: run_id}
    with heavy_tool_lock(f"postsynth vivado {part} {key}", kind="synthesis"):
      code = run_logged(cmd, work, log, env=env)
    return log.read_text(encoding="utf-8", errors="ignore")

  def completed(text: str) -> bool:
    return code == TIMEOUT_RC or (code == 0 and check.tool_completed("vivado", text))

  # Shared retry policy (tools/eda_retry.py): every retried attempt is
  # recorded and its log kept as vivado.attempt<n>.log.
  def died(text: str) -> str | None:
    return check.synth_died(status_file, run_id, text)

  text, retries, persisted = run_with_retries(run_once, completed=completed, roots=[vivado_dir], log_path=log,
                                              died=died)
  parsed = check.parse_vivado_log(text, f"lm_ps_{key}")
  metrics = check.parse_vivado_utilization(util.read_text(encoding="utf-8", errors="ignore")) if util.is_file() else {}
  problems = []
  if code == TIMEOUT_RC:
    problems.append("timeout")
  elif code != 0:
    problems.append(f"Vivado exit code {code}")
  if not parsed["completed"]:
    problems.append("no 'synth_design completed successfully' in the log")
  record, status_problems = check.read_synth_status(status_file, run_id)
  if not record or record.get("outcome") != "ok":
    problems.append(f"status file: synth_design of this run is {record.get('outcome') if record else 'not recorded'}"
                    + (f" ({'; '.join(status_problems)})" if status_problems else ""))
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


# Collisions reported by the UNISIM block RAM models in netlist simulations.
# The harness provokes collisions on purpose, counts them and reports each one
# with its instant (lm_mem_tb_pkg); the models report them as assertion
# errors. Such an error is accepted only when it is paired with a collision
# of the harness (check_collisions); every other UNISIM diagnostic, including
# "Address Overlap Error", stays fatal.
_UNISIM_COLL_RE = re.compile(
  r"(?:Memory Collision Error on (?P<prim1>RAMB(?:18|36)E1) :(?P<path1>\S+) at simulation time"
  r"|\[Unisim (?P<prim2>RAMB(?:18|36)E2)-\d+\]\s*: instance (?P<path2>\S+) : Memory Collision at time)"
  r" (?P<t>\d+(?:\.\d+)?) ?(?P<unit>fs|ps|ns|us|ms)\.")
# 7-series: write/write with both addresses; read/write with the read address.
_E1_WW_RE = re.compile(r"address location (\S+) \(hex\) of port A and address location (\S+) \(hex\) of port B")
_E1_RW_RE = re.compile(r"A read was performed on address (\S+) \(hex\) of port ([AB]) while a write was requested "
                       r"to the same address on port ([AB])")
# UltraScale+: every access of the message with its port and address.
# "... WRITE occured on port A (addr:..) and port B (addr:..)": the second
# access has the operation of the first.
_E2_ACCESS_RE = re.compile(r"(?:\b(WRITE|READ)(?: occured)?(?: window)? on|\band) port ([AB]) "
                           r"\((?:addr:)?([0-9A-Za-z]+)")
_HARNESS_WW_RE = re.compile(r"lm_mem_tb_pkg: write/write collision on cells (\d+) to (\d+) at (\d+) ps")
_HARNESS_RW_RE = re.compile(r"lm_mem_tb_pkg: read/write collision on port ([AB])\s+cell (\d+) "
                            r"\(read at (\d+) ps, write at (\d+) ps\)")
_TIME_PS = {"fs": 0.001, "ps": 1, "ns": 1000, "us": 1000000, "ms": 1000000000}

# Funcsim netlists (write_vhdl -mode funcsim): instances of UNISIM cells with
# their generics and port maps.
_INSTANCE_RE = re.compile(r"^\s*(\\[^\\\n]+\\|\w+)\s*:\s*unisim\.vcomponents\.(\w+)\s*$", re.MULTILINE | re.IGNORECASE)
_GENERIC_RE = re.compile(r"(\w+)\s*=>\s*(\d+)\b")
_INIT_RE = re.compile(r"\bINIT\s*=>\s*X\"([0-9A-Fa-f_]+)\"", re.IGNORECASE)
_PORT_MAP_RE = re.compile(r"port map\s*\((.*?)\n\s*\);", re.DOTALL | re.IGNORECASE)
_ASSOC_RE = re.compile(r"^\s*(\w+)(?:\((\d+)(?:\s+downto\s+(\d+))?\))?\s*=>\s*(.+?),?\s*$")
_ACTUAL_RE = re.compile(r"(\\[^\\]+\\|\w+)(?:\((\d+)(?:\s+downto\s+(\d+))?\))?")
_WIDTHS = ("READ_WIDTH_A", "READ_WIDTH_B", "WRITE_WIDTH_A", "WRITE_WIDTH_B")
# Address, enable and write enable of each port of a block RAM primitive
# (RAMB18E1, RAMB36E1, RAMB18E2, RAMB36E2), and the DUT inputs of each DUT port.
_RAMB_PORTS = {"A": ("ADDRARDADDR", "ENARDEN", "WEA"), "B": ("ADDRBWRADDR", "ENBWREN", "WEBWE")}
_DUT_INPUTS = {"A": ("addr_a_i", "ena_i", "wen_a_i"), "B": ("addr_b_i", "enb_i", "wen_b_i")}
# DUT address bits not wired to the block RAM (they select one block of a RAM
# split in depth) are decoded through the LUTs, up to this many bits.
_MAX_DECODED_BITS = 8

# A bit of a port map: ("c", 0 or 1) for a constant, ("s", name, index or
# None) for a signal; None when the text is not understood.
Bit = tuple


def _actual_bits(actual: str, width: int) -> list[Bit] | None:
  """The bits of the actual of an association, most significant first."""
  actual = actual.strip()
  if actual in ("'0'", "'1'"):
    return [("c", int(actual[1]))] * width
  literal = re.fullmatch(r'([BX])"([0-9A-Fa-f_]+)"', actual)
  if literal:
    digits = literal[2].replace("_", "")
    bits = "".join(f"{int(d, 16):04b}" for d in digits) if literal[1] == "X" else digits
    return [("c", int(b)) for b in bits[-width:]] if len(bits) >= width else None
  signal = _ACTUAL_RE.fullmatch(actual)
  if not signal:
    return None
  name, hi, lo = signal[1], signal[2], signal[3]
  if hi is None:
    return [("s", name, None)] if width == 1 else None
  hi_i, lo_i = int(hi), int(lo) if lo is not None else int(hi)
  bits = [("s", name, i) for i in range(hi_i, lo_i - 1, -1)]
  return bits if len(bits) == width else None


def _port_map(body: str) -> dict[str, dict[int | None, Bit | None]]:
  """Formal port -> {bit index (None for a scalar): bit of the actual}."""
  out: dict[str, dict[int | None, Bit | None]] = {}
  for line in body.splitlines():
    assoc = _ASSOC_RE.match(line)
    if not assoc:
      continue
    formal, hi, lo, actual = assoc[1].upper(), assoc[2], assoc[3], assoc[4]
    if hi is None:
      bits = _actual_bits(actual, 1)
      out.setdefault(formal, {})[None] = bits[0] if bits else None
      continue
    hi_i, lo_i = int(hi), int(lo) if lo is not None else int(hi)
    bits = _actual_bits(actual, hi_i - lo_i + 1)
    for k, index in enumerate(range(hi_i, lo_i - 1, -1)):
      out.setdefault(formal, {})[index] = bits[k] if bits else None
  return out


def parse_netlist(text: str) -> dict:
  """Block RAMs and LUTs of a funcsim netlist.

  rams: label -> primitive, port widths, and per primitive port its address
  bits, enable and write enables as bits of the port map. luts: output net
  -> (INIT, input bits).
  """
  rams, luts = {}, {}
  matches = list(_INSTANCE_RE.finditer(text))
  for i, match in enumerate(matches):
    chunk = text[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
    cell = match[2].upper()
    port_map = _PORT_MAP_RE.search(chunk)
    ports = _port_map(port_map[1]) if port_map else {}
    generic_text = chunk[:port_map.start()] if port_map else chunk
    if re.fullmatch(r"RAMB(?:18|36)E[12]", cell):
      generics = {k.upper(): int(v) for k, v in _GENERIC_RE.findall(generic_text)}
      rams[match[1]] = {"primitive": cell, **{k: generics.get(k) for k in _WIDTHS},
                        "ports": {p: {"addr": ports.get(addr, {}), "en": ports.get(en, {}).get(None),
                                      "we": ports.get(we, {})} for p, (addr, en, we) in _RAMB_PORTS.items()}}
    elif re.fullmatch(r"LUT[1-6]", cell):
      init = _INIT_RE.search(generic_text)
      out = ports.get("O", {}).get(None)
      if init and out and out[0] == "s":
        luts[(out[1], out[2])] = (int(init[1].replace("_", ""), 16),
                                  [ports.get(f"I{k}", {}).get(None) for k in range(int(cell[3]))])
  return {"rams": rams, "luts": luts}


def ramb_instances(netlist_text: str) -> dict[str, dict]:
  """Block RAM instances of a funcsim netlist: label -> primitive and port widths."""
  return {label: {k: v for k, v in ram.items() if k != "ports"}
          for label, ram in parse_netlist(netlist_text)["rams"].items()}


def _eval_bit(bit: Bit | None, env: dict, luts: dict, depth: int = 0) -> int | None:
  """Value of a bit of the netlist from the DUT inputs in env, through LUTs; None if unknown."""
  if bit is None:
    return None
  if bit[0] == "c":
    return bit[1]
  key = (bit[1], bit[2])
  if key in env:
    return env[key]
  lut = luts.get(key)
  if lut is None or depth > 6:
    return None
  init, inputs = lut
  values = [_eval_bit(b, env, luts, depth + 1) for b in inputs]
  unknown = [k for k, v in enumerate(values) if v is None]
  outs = set()
  for combo in range(2 ** len(unknown)):
    index = 0
    for k, value in enumerate(values):
      index |= (value if value is not None else (combo >> unknown.index(k)) & 1) << k
    outs.add((init >> index) & 1)
  return outs.pop() if len(outs) == 1 else None


def dut_words(ram: dict, port: str, op: str, addr: int, luts: dict,
              addr_w: dict[str, int]) -> tuple[str, list[int]] | str:
  """The DUT port and the DUT words of an access reported by a block RAM model.

  The address of the model is the address bus of the primitive port: its
  bits wired to a DUT address input give that DUT port and those bits of the
  word; constant bits (the low bits below the port width, bit 15 of a
  RAMB36E1 tied to '1') are dropped. DUT address bits not wired to the
  primitive select a block of a RAM split in depth: each of their values is
  kept only if the enable (and, for a write, the write enable) of this port
  of this primitive can be '1' for it, through the LUTs of the netlist.
  Returns (DUT port, words), or why the access cannot be converted.
  """
  wiring = ram["ports"][port]
  sources = {}
  for prim_bit, bit in wiring["addr"].items():
    if bit is None:
      return f"address bit {prim_bit} of port {port}: connection not understood"
    if bit[0] == "c":
      continue
    if bit[1] not in ("addr_a_i", "addr_b_i") or bit[2] is None:
      return f"address bit {prim_bit} of port {port} is driven by {bit[1]}, not by a DUT address input"
    sources[prim_bit] = bit
  names = {bit[1] for bit in sources.values()}
  if len(names) != 1:
    return f"address of port {port} from {sorted(names) or 'no DUT address input'}"
  dut = "A" if names == {"addr_a_i"} else "B"
  fixed = {bit[2]: (addr >> prim_bit) & 1 for prim_bit, bit in sources.items()}
  free = [b for b in range(addr_w[dut]) if b not in fixed]
  if len(free) > _MAX_DECODED_BITS:
    return f"{len(free)} DUT address bits of port {port} not wired to the block RAM"
  addr_name, en_name, wen_name = _DUT_INPUTS[dut]
  words = []
  for combo in range(2 ** len(free)):
    bits = {**fixed, **{b: (combo >> k) & 1 for k, b in enumerate(free)}}
    if free:
      env = {(addr_name, b): v for b, v in bits.items()}
      env[(en_name, None)] = 1
      env[(wen_name, None)] = 1 if op == "WRITE" else 0
      if _eval_bit(wiring["en"], env, luts) == 0:
        continue
      if op == "WRITE" and wiring["we"] and all(_eval_bit(b, env, luts) == 0 for b in wiring["we"].values()):
        continue
    words.append(sum(v << b for b, v in bits.items()))
  return dut, words


def unisim_collisions(log_text: str) -> list[dict]:
  """Collision messages of the UNISIM block RAM models in a simulation log.

  Each: position of the header, primitive, instance path, instant (ps), kind
  ("ww", "rw" or None when the text is not understood) and the accesses
  (operation, primitive port, address or None).
  """
  events = []
  for match in _UNISIM_COLL_RE.finditer(log_text):
    # The message continues on the next lines (LF in the model's text).
    body = " ".join(log_text[match.end():].split("\n")[:3])
    accesses: list[tuple[str, str, int | None]] = []
    if match["prim1"]:
      ww = _E1_WW_RE.search(body)
      rw = _E1_RW_RE.search(body)
      if ww and (not rw or ww.start() < rw.start()):
        accesses = [("WRITE", "A", _hex(ww[1])), ("WRITE", "B", _hex(ww[2]))]
      elif rw:
        accesses = [("READ", rw[2], _hex(rw[1])), ("WRITE", rw[3], None)]
    else:
      for op, port, addr in _E2_ACCESS_RE.findall(body)[:2]:
        op = op or (accesses[-1][0] if accesses else "")
        accesses.append((op, port, _hex(addr)))
    ops = sorted(op for op, _, _ in accesses)
    kind = "ww" if ops == ["WRITE", "WRITE"] else ("rw" if ops == ["READ", "WRITE"] else None)
    events.append({"pos": match.start(), "primitive": match["prim1"] or match["prim2"],
                   "path": match["path1"] or match["path2"],
                   "t_ps": round(float(match["t"]) * _TIME_PS[match["unit"]]), "kind": kind, "accesses": accesses})
  return events


def _hex(text: str) -> int | None:
  try:
    return int(text, 16)
  except ValueError:
    return None  # 'X' or 'U' in the address


def _edges(t: int, period: int) -> str:
  before = (t // period) * period
  return f"{before}" if before == t else f"{before}/{before + period}"


def check_collisions(log_text: str, context: dict, counts: dict) -> tuple[set[int], list[str], dict]:
  """Pairs the collision errors of the UNISIM models with the harness.

  context: collision_context (netlist, DUT widths and depth, clocks).
  Returns (positions of the accepted messages, problems, facts).

  Every collision message of a model must pair with a collision of the
  harness that excluded the read or made the cells undefined; a harness
  collision without a message is allowed (coverage lost, measured by
  g_min_checked). The address of each access is converted to DUT words
  through the netlist (dut_words).
  - Write/write: a harness write/write collision at the instant of the
    message whose cells overlap a word of each access. Each harness
    collision pairs at most once per block RAM.
  - Read/write: a harness read/write collision on the DUT port of the read
    whose read cells overlap a word of the read access, at the instant of
    the message: its read or its write instant, or, with two clocks, a read
    less than the harness window from the message when the message is at an
    edge of the writing port only. The harness excludes a read for every
    write of the other port in its window but names only one of them
    (2026-11-04, crw_cw.2clk.32x1024.lat1 on RAMB36E1: read at 11570 ns,
    model message at the write of 11571 ns, harness message "write at
    11578000 ps"). At most once per block RAM.
  A message that cannot be read, or whose instance or address cannot be
  converted, is not paired. Messages that are not paired are not accepted
  and stay fatal; each is reported with the clock edges around it and the
  collision window of the harness.
  """
  netlist = context["netlist"]
  rams, luts = netlist["rams"], netlist["luts"]
  a_w, b_w, a_depth = context["a_w"], context["b_w"], context["a_depth"]
  cell_w = min(a_w, b_w)
  ratio = {"A": a_w // cell_w, "B": b_w // cell_w}
  addr_w = {"A": max(1, math.ceil(math.log2(a_depth))), "B": max(1, math.ceil(math.log2(a_depth * a_w // b_w)))}
  harness_ww = [(int(lo), int(hi), int(t)) for lo, hi, t in _HARNESS_WW_RE.findall(log_text)]
  harness_rw = [(port, int(cell), int(t_rd), int(t_wr)) for port, cell, t_rd, t_wr in _HARNESS_RW_RE.findall(log_text)]
  problems = []
  for kind, events, counter in (("write/write", harness_ww, "collisions_ww"),
                                ("read/write", harness_rw, "collisions_rw")):
    if counter in counts and counts[counter] != len(events):
      problems.append(f"harness: {counts[counter]} {kind} collisions counted, {len(events)} reported with "
                      "their instant (the pairing needs every one)")

  def cells(dut: str, words: list[int]) -> list[range]:
    return [range(w * ratio[dut], (w + 1) * ratio[dut]) for w in words]

  def overlap(ranges: list[range], lo: int, hi: int) -> bool:
    return any(r.start <= hi and lo < r.stop for r in ranges)

  same_clock = context.get("same_clock", False)
  t_a, t_b = context.get("a_period_ps"), context.get("b_period_ps")
  window = "same instant (common clock)" if same_clock else f"{max(t_a or 0, t_b or 0)} ps"

  def rw_instant(t: int, read_dut: str, t_rd: int, t_wr: int) -> bool:
    if t in (t_rd, t_wr):
      return True
    if same_clock or not (t_a and t_b):
      return False
    read_period, write_period = (t_a, t_b) if read_dut == "A" else (t_b, t_a)
    return t % read_period != 0 and t % write_period == 0 and abs(t - t_rd) < max(t_a, t_b)
  accepted: set[int] = set()
  unpaired: list[str] = []
  used = {"ww": set(), "rw": set()}
  paired = {"ww": 0, "rw": 0}
  messages = unisim_collisions(log_text)
  for event in messages:
    label = next((l for l in rams if l.lower() in event["path"].lower()), None)
    converted, why = [], None
    if label is None:
      why = "instance not found in the netlist"
    elif event["kind"] is None:
      why = "message not understood"
    else:
      for op, port, addr in event["accesses"]:
        if event["kind"] == "rw" and op == "WRITE":
          continue  # 7-series read/write: the write is on the read address
        if addr is None:
          why = f"address of port {port} not readable"
          break
        result = dut_words(rams[label], port, op, addr, luts, addr_w)
        if isinstance(result, str):
          why = result
          break
        converted.append((op, *result))
    match = None
    if why is None and event["kind"] == "ww":
      match = next((i for i, (lo, hi, t) in enumerate(harness_ww) if (label, i) not in used["ww"]
                    and t == event["t_ps"] and all(overlap(cells(d, w), lo, hi) for _, d, w in converted)), None)
    elif why is None:
      _, read_dut, read_words = converted[0]
      match = next((i for i, (port, cell, t_rd, t_wr) in enumerate(harness_rw) if (label, i) not in used["rw"]
                    and port == read_dut and rw_instant(event["t_ps"], read_dut, t_rd, t_wr)
                    and overlap(cells(read_dut, read_words), cell, cell + ratio[port] - 1)), None)
    if match is not None:
      used[event["kind"]].add((label, match))
      paired[event["kind"]] += 1
      accepted.add(event["pos"])
      continue
    t = event["t_ps"]
    edges = (f"clock edge {t} ps (common clock)" if same_clock or not (t_a and t_b) else
             f"edges around it: A {_edges(t, t_a)} ps, B {_edges(t, t_b)} ps")
    duts = "; ".join(f"{op} DUT port {d} words {w[:4]}" for op, d, w in converted)
    unpaired.append(f"{event['primitive']} {event['kind'] or '?'} collision at {t} ps "
                    f"({event['accesses']}{'; ' + duts if duts else ''}) has no matching collision in the harness"
                    f"{': ' + why if why else ''}; {edges}; harness window {window}")
  problems += unpaired[:5]
  if len(unpaired) > 5:
    problems.append(f"{len(unpaired) - 5} more collisions of the model without a match in the harness")
  facts = {"model_collisions": len(messages), "accepted": len(accepted), "paired_ww": paired["ww"],
           "paired_rw": paired["rw"], "unpaired": len(unpaired), "harness_ww": len(harness_ww),
           "harness_rw": len(harness_rw), "block_rams": sorted(rams)}
  return accepted, problems, facts


def parse_result(log_text: str, return_code: int | None, collisions: dict | None = None) -> dict:
  """Verdict of one simulation run. PASS needs all of: exit code 0, exactly
  one RESULT line and it is PASS, exactly one harness line with every counter,
  0 errors in it, and no fatal diagnostic anywhere in the log.

  collisions (netlist simulations): collision_context of the configuration.
  A collision error of a UNISIM block RAM model is then not fatal when
  check_collisions pairs it with the harness."""
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
  fatal_matches = list(FATAL_RE.finditer(log_text))
  result_facts = {}
  if collisions is not None:
    accepted, coll_problems, result_facts = check_collisions(log_text, collisions, counts)
    problems += coll_problems
    # A fatal line is accepted only if the paired collision message starts in it.
    fatal_matches = [m for m in fatal_matches if not any(m.start() <= pos < m.end() for pos in accepted)]
  fatal = [m.group(0) for m in fatal_matches]
  if fatal:
    problems.append(f"{len(fatal)} fatal diagnostics in the log, first: {fatal[0].strip()[:120]}")
  verdict = "PASS" if not problems else ("TIMEOUT" if return_code == TIMEOUT_RC else "FAIL")
  result = {"verdict": verdict, "counts": counts, "problems": problems}
  if result_facts:
    result["collisions"] = result_facts
  return result


def collision_context(netlist: Path, g: dict) -> dict:
  """Argument collisions of parse_result for one configuration."""
  return {"netlist": parse_netlist(netlist.read_text(encoding="utf-8", errors="ignore")),
          "a_w": g["g_a_data_w"], "b_w": g["g_b_data_w"], "a_depth": g["g_a_depth"],
          "same_clock": g.get("g_same_clock", False), "a_period_ps": g.get("g_a_period_ps"),
          "b_period_ps": g.get("g_b_period_ps")}


def ghdl_version() -> str:
  out = subprocess.run([GHDL, "--version"], capture_output=True, text=True).stdout.splitlines()
  return out[0].strip() if out else "GHDL"


def run_ghdl(vivado_dir: Path, mode: str, files: list[Path], cells: list[str], cfgs: list, work: Path,
             netlists: dict[str, Path] | None = None) -> dict:
  """netlists (netlist mode): configuration name -> its netlist, for the
  pairing of UNISIM collision messages (check_collisions)."""
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
      context = collision_context(netlists[name], g) if netlists else None
      results[name] = parse_result(run_log.read_text(encoding="utf-8", errors="ignore"), rc, context)
  return {"sim": ghdl_version(), "results": results}


def run_xsim(vivado_dir: Path, mode: str, files: list[Path], cfgs: list, work: Path,
             netlists: dict[str, Path] | None = None) -> dict:
  """netlists: as for run_ghdl."""
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
      results[name] = parse_result(text, rc, collision_context(netlists[name], g) if netlists else None)
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
    by_cfg = {n: Path(synth[netlist_key(n, g)]["netlist"]) for n, g in ok_cfgs}
    sims_out = {}
    for sim in sims:
      work = part_dir / sim
      sims_out[sim] = (run_ghdl(args.vivado_dir, "netlist", files, unisim_cells(netlists), ok_cfgs, work, by_cfg)
                       if sim == "ghdl" else run_xsim(args.vivado_dir, "netlist", files, ok_cfgs, work, by_cfg))
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
