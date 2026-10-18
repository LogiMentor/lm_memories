<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor Srl -->

# Plan: inferable generic RAMs (issue #3)

Status: phase 0 of the implementation (plan and structure). No RTL has been
changed yet.

Sections 1 and 2 record the diagnosis: the reproduction and the root cause.
Section 3 is the revised implementation plan: binding decisions, layout,
manifest, verification and phases.

## 1. Reproduction

### Setup

- Source: `main` at `8b60c86`, the commit named in the issue. The runs used a
  local commit with the same tree (`git diff` is empty).
- Tool: Vivado v2023.1 (64-bit), build 3865809.
- Part: `xc7a35tfgg484-2`.
- Flow: `synth_design -mode out_of_context -top <entity>`, run on the library
  entity itself, so there is exactly one RAM instance and no wrapper logic.
  Sources are read with `read_vhdl -vhdl2008`.
- Size: 32 x 1024 (library defaults, latency 1) unless stated otherwise.
- Reports: `report_utilization`, `report_ram_utilization`, CRITICAL WARNING
  count from the synthesis log.

Vivado limits every message ID to 100 occurrences by default. The "~100
CRITICAL WARNINGs" in the issue is that limit, not the real count. With the
limit raised (`set_msg_config -id {Synth 8-6859} -limit 1000000`), a 32 x 1024
instance reports 65,536 `[Synth 8-6859]` CRITICAL WARNINGs: two per storage bit.

### Results: library entities

| Entity | `g_simulation` | BRAM tiles | LUT as memory | Slice registers | Slice LUTs | CRITICAL WARNINGs |
| --- | --- | --- | --- | --- | --- | --- |
| `lm_mem_ram_r_w_infer` (reference) | n/a | 1 (RAMB36E1, `RAM_SDP`) | 0 | 0 | 0 | 0 |
| `lm_mem_ram_r_w` | 0 | 0 | 0 | 65,568 | 9,845 | 100 shown (limit) |
| `lm_mem_ram_r_w` | 1 | 0 | 0 | 65,568 | 9,845 | 100 shown (limit) |
| `lm_mem_xilinx_ram_r_w_prim` (wraps `lm_mem_ram_r_w`) | 1 (hard-coded) | 0 | 0 | 65,568 | 9,845 | 65,536 (limit raised) |
| `lm_mem_ram_rw_rw` | 0 | 0 | 0 | 65,600 | 19,718 | 65,536 (limit raised) |
| `lm_mem_ram_rw_rw` | 1 | 0 | 0 | 65,600 | 19,718 | 100 shown (limit) |
| `lm_mem_ram_cr_cw` | 0 | 0 | 0 | 65,568 | 9,845 | 100 shown (limit) |
| `lm_mem_ram_cr_cw` | 1 | 0 | 0 | 65,568 | 9,845 | 100 shown (limit) |
| `lm_mem_ram_crw_cr` | 1 | 0 | 0 | 65,600 | 18,581 | 65,536 (limit raised) |
| `lm_mem_ram_crw_cw` | 1 | 0 | 0 | 65,568 | 10,982 | 65,536 (limit raised) |
| `lm_mem_ram_crw_crw` | 1 | 0 | 0 | 65,600 | 19,718 | 100 shown (limit) |
| `lm_mem_ram_r_w_ratio` (32/32) | 1 | 0 | 0 | 65,568 | 9,845 | 100 shown (limit) |
| `lm_mem_ram_r_w_ratio` (32 -> 8) | 1 | 0 | 0 | 65,528 | 16,121 | 108 shown (limit; includes `Synth 8-6858`) |
| `lm_mem_ram_cr_cw_ratio` (32/32) | 1 | 0 | 0 | 65,568 | 9,845 | 65,536 (limit raised) |
| `lm_mem_ram_r_w_infer` 16 x 64 (reference) | n/a | 0.5 (RAMB18E1, `RAM_SDP`) | 0 | 0 | 0 | 0 |
| `lm_mem_ram_r_w` 16 x 64 | 1 | 0 | 0 | 1,040 | 338 | 0 |
| `lm_mem_fifo_sync` 32 x 16 (defaults) | 1 | 0 | 0 | 559 | 165 | 0 |
| `lm_mem_fifo_sync` 32 x 1024 | 1 | 0 | 0 | 65,694 | 10,026 | 100 shown (limit) |
| `lm_mem_fifo_async` 32 x 1024 | 1 | 0 | 0 | 65,755 | 10,041 | 100 shown (limit) |

All runs completed synthesis. "100 shown (limit)" means Vivado's default
per-message-ID limit; the runs with the limit raised show the real count for
a 32 x 1024 instance.

Every generic RAM entity fails to infer BRAM or LUTRAM. The 1024-word cases
build each storage bit as two flip-flops (65,536 + read registers) and raise one
CRITICAL WARNING per flip-flop. The design does not fit the part: 65,568
registers against 41,600 available.

Small memories fail silently. `lm_mem_ram_r_w` at 16 x 64 and the default
`lm_mem_fifo_sync` (32 x 16) put every storage bit in a flip-flop (1,040 and 559
registers) and report no CRITICAL WARNING at all. Every FIFO user is affected,
because `lm_mem_fifo` stores its data in `lm_mem_ram_cr_cw_ratio`.

The "primitive" wrappers (`lm_mem_*_ram_r_w_prim`) instantiate
`lm_mem_ram_r_w` and are affected in the same way.

### Messages

The failing cases report these messages, all pointing at `proc_mem` in
`src/generic/lm_mem_ram_crw_crw.vhd`. The small cases (16 x 64, FIFO 32 x 16)
report only the first one:

- `WARNING: [Synth 8-5787] Register s_mem_reg[N] in module lm_mem_ram_crw_crw
  is clocked by two different clocks in the same process.` (one per word)
- `CRITICAL WARNING: [Synth 8-6859] multi-driven net on pin
  inst_mem/s_mem[N][b] with 1st driver pin 's_mem_reg[N][b]/Q'` and the same
  with `2nd driver pin 's_mem_reg[N][b]__0/Q'` (one per bit and per driver).

GHDL simulation is not affected: `sim/scripts/run_ghdl.py` passes all five
testbenches on the same tree (GHDL 6.0.0, `--std=08`).

## 2. Root cause

### It is the RAM description, not `g_simulation`

`g_simulation` is declared and forwarded by every wrapper, but no architecture
reads it: there is no `if g_simulation ... generate` and no expression that uses
it. Synthesis results are identical with `g_simulation = 0` and `1`
(`lm_mem_ram_r_w`: 65,568 registers, 9,845 LUTs, 0 BRAM in both cases).
Changing the default would not change anything.

### The description

All eight generic RAM entities are thin wrappers around one core,
`lm_mem_ram_crw_crw` (dual-clock, two write ports, mixed width). Wrappers that
have only one write port tie `wen_b_i` to `'0'`. The core describes the memory as:

```vhdl
proc_mem : process(clk_a_i, clk_b_i)
begin
  if rising_edge(clk_a_i) then
    ... p_write_word(s_mem, addr_a_i, ...);   -- write port A
  end if;
  if rising_edge(clk_b_i) then
    ... p_write_word(s_mem, addr_b_i, ...);   -- write port B
  end if;
end process;
```

1. **One process, two clock edges, one signal written under both.** Vivado
   builds a register for each clock edge that writes `s_mem` (`s_mem_reg` and
   `s_mem_reg__0`). Both drive the same net (`Synth 8-5787`, then
   `Synth 8-6859`). A register driven by two clocks cannot be a RAM, so
   inference never starts.
2. **The core is elaborated without the wrapper constants.** Vivado elaborates
   `lm_mem_ram_crw_crw` as a module before it propagates constants across the
   hierarchy, so `wen_b_i => '0'` in a wrapper does not remove the port B write.
   Simple dual-port wrappers (`r_w`, `cr_cw`, `*_ratio`, FIFO) fail for the same
   reason as true dual-port ones.
3. **Two writers cannot be described correctly with a plain `signal`.** Even
   with the structure fixed, every two-writer description that Vivado infers
   breaks the simulation or the language rules. The control experiments below
   show this, with scratch RTL that is not in the repo:

| Control experiment (32 x 1024, scratch RTL) | Vivado result | Verdict |
| --- | --- | --- |
| Single process, two clocks (as today), `s_mem` written only under `clk_a`, port B read-only | 1 RAMB36 (`RAM_TDP`), 0 CRITICAL WARNINGs | Shows the cause: the port B write branch, not the two-clock process |
| Port A read/write on `clk_a`, port B read-only on `clk_b`, one process per clock, `signal` | 1 RAMB36 (`RAM_TDP`), 0 CRITICAL WARNINGs | Usable template for one writer |
| Two writers, single clock, single process, `signal` | No RAM. `Synth 8-4767`: "RAM has multiple writes via different ports in same process. If RAM inferencing intended, write to one port per process." 86,181 LUTs + 32,832 registers | Not inferable |
| Two writers, one process per clock, `signal` | 1 RAMB36 (`RAM_TDP`), 0 CRITICAL WARNINGs | Wrong in simulation: two drivers on a resolved signal. GHDL reads back `000000XX` after writing `000000A5`. Simulation and synthesis do not match. |
| Two writers, one process per clock, plain `shared variable` (UG901 style) | 1 RAMB36 (`RAM_TDP`), 0 CRITICAL WARNINGs | Not VHDL-2008. GHDL `--std=08`: "type of a shared variable must be a protected type" (needs `-frelaxed`) |
| Two writers, one process per clock, `shared variable` of a protected type | The report shows 1 RAMB36 (`RAM_TDP`), but 31 of the 32 `data_rd_a_o` bits are tied to constant 0 (`Synth 8-3917`), plus `Synth 8-3848` "Net dataN ... does not have driver" and `Synth 8-613` "shared variable will be implemented as local to architecture" | Wrong netlist, with no error |

Secondary issues found during the reproduction (not the cause of #3):

- The `lm_mem_ram_r_w_infer` vendor-synth case passes `g_style => "bram"`.
  Vivado ignores that value (`Synth 8-11376`, "'bram' is not a valid value");
  the Vivado value is `"block"`.
- `tools/vendor_synth` marks a case `pass` when the tool exits with 0. A
  run that puts the whole RAM in flip-flops still passes. This is why the
  problem was not caught.
- The helper subprograms (`f_read_word`, `p_write_word`) use bounds checks
  and loops that are not in any vendor template. They are not the cause, but the
  fix replaces them with direct indexing.

## 3. Revised plan

The decisions below were made after the diagnosis. They replace the first
proposal (sections 3 to 5 of commit `680f4b2`). Phase 0 was approved with the
corrections included here; there are no defaults left to confirm.

### 3.1 Approach

Three kinds of implementation, chosen by the number of write ports:

| Kind | Entities | Model |
| --- | --- | --- |
| Portable core | `lm_mem_ram_r_w`, `lm_mem_ram_cr_cw`, `lm_mem_ram_r_w_ratio`, `lm_mem_ram_cr_cw_ratio` (and every FIFO) on `lm_mem_ram_sdp`; `lm_mem_ram_crw_cr` on `lm_mem_ram_tdp1w` | One model for synthesis and simulation |
| Dual model | `lm_mem_ram_crw_crw`, plus `lm_mem_ram_rw_rw` and `lm_mem_ram_crw_cw`, which stay portable wrappers around it | One entity, one simulation architecture (the reference model), one synthesis architecture per vendor or family, one fallback architecture |
| Vendor example | `examples/vendor/` (XPM for AMD, `altera_syncram` for Intel) | Same interface, **outside the verification matrix** |

Rules for the dual model:

- Every architecture of the dual-model entity is named `rtl`. Only the file
  manifest of each flow decides which one is compiled (section 3.3).
- Configurations, `translate_off/on` around behaviour, and `if ... generate`
  on a generic to choose the model are not allowed.
- **Fallback:** for a vendor without a validated two-writer architecture, the
  manifest selects an architecture that stops synthesis. It uses
  `assert false report "..." severity failure` with an explicit message. A
  second mechanism, an out-of-range constant such as
  `constant C_LM_MEM_TDP_NOT_SUPPORTED : natural range 0 to 0 := 1;`, covers
  tools that ignore assertions. Each tool is checked for this (section 3.6).
  A silent fall back to flip-flops is never acceptable.

The dual-model entity is `lm_mem_ram_crw_crw` itself, because it is the most
general one (two clocks, two writers, mixed width). `lm_mem_ram_rw_rw` and
`lm_mem_ram_crw_cw` are portable wrappers around it. This gives one synthesis
architecture per vendor instead of three.

Interface and behaviour:

- Entity names, generics, ports and defaults do not change.
- Today's behaviour is the reference. A frozen copy of today's core is kept as
  `sim/ref/lm_mem_ref_ram_crw_crw.vhd` and renamed so it can sit in the same
  library as the new code. The new code is compared with it cycle by cycle.

### 3.2 Layout

```text
src/
  common/      lm_mem_pkg.vhd, lm_mem_ccd_resync.vhd          (unchanged)
  core/        lm_mem_ram_sdp.vhd, lm_mem_ram_tdp1w.vhd       (portable, one model)
               lm_mem_ram_crw_crw_ent.vhd                     (dual-model entity only)
  generic/     existing public wrappers, same paths            (rewired to src/core)
  sim/         lm_mem_ram_crw_crw_rtl.vhd                     (simulation architecture = reference model)
  vendor/
    amd/       lm_mem_ram_crw_crw_rtl.vhd                     (Vivado synthesis architecture)
    intel/     ...                                            (only when validated)
    lattice/   ...
    microchip/ ...
    unsupported/ lm_mem_ram_crw_crw_rtl.vhd                   (fallback: stops synthesis)
  fifo/, inference/, primitives/                              (unchanged; *_prim deprecated)
sim/
  ref/         lm_mem_ref_ram_crw_crw.vhd                     (frozen copy of today's core)
  tb/          existing testbenches                           (unchanged)
  vunit/       new harnesses and VUnit testbenches, run.py
  scripts/     run_ghdl.py (dedicated workdir), run_xsim.py, run_postsynth.py
examples/vendor/
  amd/         XPM wrapper          (not verified, declared as such)
  intel/       altera_syncram wrapper
manifests/
  lm_memories.toml                                            (single manifest, all flows)
```

Effects on existing users:

- The public wrappers keep their paths. File lists that name them keep
  working.
- `src/generic/lm_mem_ram_crw_crw.vhd` moves to
  `src/core/lm_mem_ram_crw_crw_ent.vhd`, which holds only the entity. A
  downstream file list that is not updated fails at analysis or elaboration
  ("no architecture"), not silently. This must be in the release notes.
- `docs/coding_style.md` requires the entity and the architecture in the same
  file. It gets an exception for dual-model entities.

### 3.3 File manifest

One TOML file, `manifests/lm_memories.toml`, is read by every synthesis and
simulation script. Named file sets are listed in compile order, and each flow
is a list of file sets:

```toml
[filesets.common]
std   = "93"                     # must also analyze as 2008
files = ["src/common/lm_mem_pkg.vhd", "src/common/lm_mem_ccd_resync.vhd"]

[filesets.core]
std   = "93"
files = ["src/core/lm_mem_ram_sdp.vhd", "src/core/lm_mem_ram_tdp1w.vhd",
         "src/core/lm_mem_ram_crw_crw_ent.vhd"]

[filesets.crw_crw_sim]
files = ["src/sim/lm_mem_ram_crw_crw_rtl.vhd"]

[filesets.crw_crw_amd]
files = ["src/vendor/amd/lm_mem_ram_crw_crw_rtl.vhd"]
tools = ["vivado"]               # never compiled by other tools
check = "target-tool"            # see section 3.4

[filesets.crw_crw_unsupported]
files = ["src/vendor/unsupported/lm_mem_ram_crw_crw_rtl.vhd"]
check = "ghdl-93-08"

[flows.sim]
filesets = ["common", "core", "crw_crw_sim", "wrappers", "fifo", "inference", "primitives"]

[flows.synth-amd]
filesets = ["common", "core", "crw_crw_amd", "wrappers", "fifo", "inference", "primitives"]

[flows.synth-intel]               # becomes crw_crw_intel once validated
filesets = ["common", "core", "crw_crw_unsupported", "wrappers", "fifo", "inference", "primitives"]
```

- All files go into library `work`, as today.
- A small reader, `tools/manifest`, gives the ordered file list of a flow to
  the scripts. For downstream users it also prints the list as
  `python -m tools.manifest list --flow synth-amd --format f|tcl|json`.
- `tomllib` needs Python 3.11, so `pyproject.toml` requires Python 3.11 or
  newer. CI already uses 3.12.

### 3.4 Conservative VHDL

- Every file under `src/core/`, `src/generic/` and `src/sim/` must analyze
  with GHDL `--std=93c` **and** `--std=08`. CI checks this. The current code
  already passes both.
- Not allowed in those directories: `process(all)`, `else`/`elsif` and `case`
  generate, `std.env`, `to_hstring`, unconstrained element types, generic types
  and packages, protected types, `??` and matching operators, sequential
  conditional assignments, external names.
- `src/vendor/` and `src/vendor/unsupported/` have no implicit exception. Each
  file set declares its check in the manifest (`check = ...`), and CI or the
  local scripts run exactly that check:

  | `check` value | Meaning | Used for |
  | --- | --- | --- |
  | `ghdl-93-08` | GHDL analysis in `--std=93c` and `--std=08` | `src/vendor/unsupported/` (fallback architecture) |
  | `ghdl-relaxed` | GHDL analysis with `--std=08 -frelaxed` (warnings allowed) | vendor files that GHDL can parse |
  | `target-tool` | Analysis and synthesis only with the tool in `tools = [...]`, plus post-synthesis simulation of the netlist | `src/vendor/amd/` (UG901 template with a plain `shared variable`) |

- The AMD two-writer architecture starts from the UG901 true dual-port
  template, which uses a plain `shared variable`; this is allowed in that file
  only. The protected-type variant is excluded (wrong netlist in Vivado 2023.1,
  section 2).
- Testbenches use VHDL-2008.

### 3.5 Reference behaviour and undefined cases

Defined behaviour (checked):

- Read during write on the **same port**: the old data is returned
  (read-first). This applies to port A of `lm_mem_ram_crw_cr` and to each port
  of the two-writer RAMs.
- Common-clock simple dual port (`lm_mem_ram_r_w`, `lm_mem_ram_r_w_ratio`):
  a read of the address being written in the same cycle returns the old data,
  **where the vendor supports it**. This is checked per vendor with
  post-synthesis simulation, and the exceptions are listed in the README
  status table.
- Enable gating: `ena_i` gates the port A read and write; `enb_i` gates port
  B. Both output stages hold their value while disabled.
- Latency 1 and 2.
- Initial content is zero, and there is no reset.
- Mixed width: the lower narrow address holds the lower bits of the wide word.
- `g_init_file /= ""` is rejected.

Undefined behaviour (excluded from the comparison, but counted):

- **Read during write between different ports of any true dual-port RAM**
  (`lm_mem_ram_crw_cr`, `lm_mem_ram_rw_rw`, `lm_mem_ram_crw_cw`,
  `lm_mem_ram_crw_crw`). This includes `lm_mem_ram_rw_rw` with a single clock,
  because some tools configure the block in two-clock mode.
- Any read/write collision between ports on different clocks, including
  dual-clock simple dual port (`lm_mem_ram_cr_cw`, `lm_mem_ram_cr_cw_ratio`,
  FIFOs). A collision is a read and a write of the same address (or of
  overlapping narrow words) where the two clock edges are less than one period
  of the slower clock apart.
- Two writes to the same address (or overlapping words) on different ports,
  in the same cycle or within the collision window.
- **Accesses at or beyond the depth** (possible only when a depth is not a
  power of two). A read returns undefined data. A write never changes an
  address within the depth. The cores contain no address comparison: the
  storage covers the whole address range, so a non-power-of-two depth uses the
  next power of two words. The scoreboard and the simulation models raise a
  warning for these accesses: a write is ignored by the model, and a read is
  excluded from the comparison. Both are counted. This is a **breaking
  change**: the previous model returned zeros (`CHANGELOG.md`).

How the scoreboard and the models handle collisions:

| Collision | Effect in the scoreboard |
| --- | --- |
| Read/write | Only that read is excluded. The cell keeps the written value. |
| Write/write | Every cell of both writes becomes undefined: the whole word of each port, also where a wide and a narrow word only partly overlap (phase 3: the UltraScale+ RAMB36E2 model corrupts the whole word). Reads of those cells are excluded until the next valid write to them. |

- Each collision raises `assert ... severity warning` and is counted. The
  test report prints the number of collisions and of excluded reads by kind,
  and fails if more than a set fraction of the reads is excluded. Without that
  limit, a test that excludes everything would still pass.
- Where the warning comes from: for the dual-model entity it is raised by the
  simulation architecture in `src/sim/`, which may use `now` and other
  non-synthesizable constructs. A portable core is also the synthesis model,
  so it must not contain timing checks. For portable cores the warning is
  raised by the collision monitor in the testbench harness, which watches the
  core's ports.
- The frozen reference copy in `sim/ref/` does not change. Its result for a
  collision is ignored, like the DUT's.

Confirmed defaults:

- `g_simulation` stays in every signature, is ignored, and is documented as
  deprecated. Each core instance emits one `report ... severity note`.
- `*_ratio`: if a vendor does not infer the asymmetric template, fall back to
  a symmetric RAM at the wider width plus conversion logic.
- `*_prim` wrappers: kept as deprecated wrappers on the new cores. No rename.

### 3.6 Verification

**Tool matrix**

| Role | Tool | Version | Status on the reference machine |
| --- | --- | --- | --- |
| Synthesis AMD | Vivado | 2023.1 | available |
| Synthesis Intel | Quartus Prime Lite | 23.1std.0 build 991 | available (families: Cyclone V, MAX 10) |
| Synthesis Lattice | Diamond (LSE, Synplify Pro) | 3.14.0.75.2 | installed, but the node-locked license expired on 2026-08-20; `pnmainc.exe` exits with no output. **NOT RUN** until the license is renewed |
| Synthesis Microchip | Libero | - | not installed: **NOT RUN** |
| Simulation, reference and CI | GHDL | 6.0.0 (local), Ubuntu package (CI) | available |
| Simulation AMD | Vivado simulator (xsim) | 2023.1 | available |
| Simulation Intel, Lattice, Microchip | Questa Sim | 2021.1 | available |
| | Questa Sim 2024.3, Questa Intel Starter 2023.3 | - | not used (no license) |
| Test runner | VUnit | 4.7.1 (pip) | available; no xsim support (see below) |

**Behaviour tests (new testbenches only)**

- Each new test has three layers:
  - a **harness** in plain VHDL-2008 with no VUnit dependency. It holds the
    DUT adapter, constrained-random stimulus, the scoreboard, and the
    comparison with `sim/ref/`;
  - a **VUnit testbench** that drives the harness: GHDL in CI, and Questa
    2021.1 locally through VUnit's ModelSim/Questa interface;
  - a **plain top** per configuration for simulators without VUnit.
- VUnit has no xsim support: the latest release is v4.7.0, there is no xsim
  interface in `vunit/sim_if`, and the upstream issue VUnit/vunit#209 "Add
  support for Vivado XSIM" is still open. `sim/scripts/run_xsim.py` therefore
  runs the plain tops with the same harness and the same configuration list.
- **DUT adapter:** `lm_tb_dut_<core>` has an `rtl` architecture that
  instantiates the library entity with generics, and a `netlist` architecture
  that instantiates the synthesized top of one fixed configuration. The flow
  manifest picks one, as for the RTL. This lets the same harness and the same
  scoreboard run on RTL and on netlists.
- The existing testbenches stay as they are. `run_ghdl.py` still runs them,
  now with a dedicated workdir and no `.exe`/`.o` files in the repo root.

**Post-synthesis simulation**

- Mandatory for the dual-model entities on every supported vendor. For the
  portable cores it is optional, but the export is prepared.
- AMD: synthesize a generated top with fixed generics, out-of-context, then
  `write_vhdl -mode funcsim`. Run it in GHDL with UNISIM analyzed from
  Vivado's `data/vhdl/src/unisims`; relaxed GHDL flags, if needed, apply only
  to that vendor library. Also run it in xsim with the precompiled UNISIM.
- Intel: VHDL netlist from `quartus_eda`, plus the Cyclone V or MAX 10 atoms,
  in Questa 2021.1.
- Lattice: VHDL netlist from Diamond, plus the ECP5 simulation library, in
  Questa 2021.1.
- Vendor simulation libraries for Questa 2021.1 (Cyclone V and MAX 10 atoms,
  `altera_mf`, `altera_lnsim`, ECP5) are compiled by a repeatable script,
  `sim/scripts/compile_vendor_libs.py`, into the build directory outside the
  repo. The script is written when the first Intel or Lattice post-synthesis
  run needs it (phase 4).
- Microchip: not run.

**Synthesis checks (`tools/vendor_synth`)**

- Expected-results files `tools/vendor_synth/expected/<vendor>.toml`, keyed by
  entity × configuration. Each entry gives the exact number of RAM blocks, the
  LUTRAM count, the register ceiling, CRITICAL WARNINGs = 0, and the forbidden
  message IDs (at least `Synth 8-6859`, `Synth 8-5787`). For a fallback
  architecture the expected result is "synthesis stops with the fallback
  message". A case passes only when the reports match the expected results;
  the exit code alone never makes a pass.
- The per-ID message limit is raised (`set_msg_config -limit`), so the counts
  are real.
- The script reads the effective value of each generic from the log
  (`Parameter <name> bound to: <value>`). The case fails if it differs from
  the requested value.
- It retries at most twice, and only on "couldn't read file". Each retry is
  recorded in the report.
- Runs are sequential, one tool process at a time. Each run holds the
  machine-wide lock `lm_eda_heavy_tools.lock` in the system temporary
  directory (`tools/eda_lock.py`, fixed name, not tied to the repo), so
  sessions on the same machine queue up. The timeout is set with
  `LM_EDA_LOCK_TIMEOUT`, and a waiting run prints who holds the lock. The
  lock is an operating-system lock on the file (`msvcrt.locking` on Windows,
  `flock` elsewhere), released when its holder exits, also after a crash. The
  script never terminates a process it did not start.
- Logs and artefacts go outside the repo. The default is a folder under the
  system temporary directory, changed with `--work-root` or `LM_MEM_BUILD_DIR`.
- A tool that is not installed is reported as **NOT RUN**, never as pass.
- Configurations: 32 x 1024, `lm_mem_ram_r_w` 16 x 64, FIFO 32 x 16, plus
  latency 2, ratio 32 -> 8 and 8 -> 32, and FIFO 32 x 1024.

### 3.7 Reference devices

| Vendor | Device | Status |
| --- | --- | --- |
| AMD 7-series | `xc7a35tfgg484-2` | approved |
| AMD UltraScale+ | `xcku3p-ffva676-2-e` (Kintex UltraScale+) | approved. Expected results cover block RAM only: no URAM, because the cores carry no `ram_style` attribute. |
| Intel | `5CEBA4F23C7` (Cyclone V E), `10M50DAF484C7G` (MAX 10) | approved |
| Lattice | `LFE5U-45F-6BG381C` (ECP5) | approved; blocked by the expired Diamond license |
| Microchip | `MPF100T-FCG484I` (PolarFire) | approved; Libero not installed |

### 3.8 Phases

Each phase ends with a report and waits for confirmation.

0. Plan, layout, manifest format, tool inventory (this revision).
1. Portable cores (`lm_mem_ram_sdp`, `lm_mem_ram_tdp1w`), wrappers and FIFOs
   rewired, `sim/ref/`, VUnit harnesses on GHDL, `run_ghdl.py` workdir,
   `.gitignore`. A draft PR to `main` opens at the end of this phase.
2. Vivado synthesis checks in `tools/vendor_synth`, with expected results.
3. Two-writer RAM on AMD: entity, simulation architecture, Vivado
   architecture, fallback, and post-synthesis on GHDL and xsim.
4. Other vendors, for installed tools only: Quartus and Diamond back ends,
   portable cores, two-writer architectures only where synthesis and
   post-synthesis both pass.
5. Vendor examples, README (approaches, "which one to use", status table
   generated by the scripts).

### 3.9 Open points

1. Diamond license renewal: until it is renewed, Lattice is **NOT RUN**.

### 3.10 Phase 1 implementation notes

- **Accesses beyond the depth:** see section 3.5. The first phase 1 version
  compared addresses with the depth inside the cores; that was removed after
  review. Vivado 2023.1 (`xc7a35tfgg484-2`) checks:
  - `lm_mem_ram_r_w` at 1024 and 1000 words and `lm_mem_ram_crw_cr` at 1000
    words: 1 RAMB36, 0 LUTs, 0 registers. `ENARDEN`/`ENBWREN` are driven
    directly by `ena_i`/`enb_i`, and `WEA` by `wen_i`.
  - At 1536 words: 2 RAMB36 (the storage is 2048 words), 0 LUTs.
  The tests generate accesses beyond the depth in four configurations whose
  depth is not a power of two. They fail if none occurs.
- **Unused generics:** `g_ram_a_latency` of the `*_ratio` wrappers belongs to
  the unused port A output. It is still checked (1 or 2), as before, and
  otherwise ignored.
- **Collision warnings for portable cores** are raised by the testbench
  scoreboard (`sim/vunit/lm_mem_tb_pkg.vhd`), not by the cores (section 3.5).
- **Common clock in the harness:** one process drives both clock signals, so
  the two edges fall in the same delta cycle. A concurrent copy
  (`clk_b <= clk_a`) adds a delta cycle and would make the port B process see
  port A writes too early.
- **FIFO:** compared with a scoreboard (data order and content, wide and narrow
  ports), not with a frozen FIFO copy. The RAM under it is compared with
  `sim/ref/` by the RAM tests.
- **Two-writer RAMs** (`crw_crw`, `rw_rw`, `crw_cw`) are unchanged until
  phase 3. The manifest still lists the original `lm_mem_ram_crw_crw`.
- **Independence from process order** (reviewed after a GHDL/Questa
  difference). GHDL and Questa run processes of the same delta cycle in
  different orders. The new testbenches were checked decision by decision:
  - *Expected value, "never written", "undefined" of a read:* taken from each
    cell's state before the current instant. Each cell keeps its value,
    written flag and undefined flag from before its last write instant.
    **Changed:** the written and undefined flags used to be read after a
    same-instant write if that write was processed first.
  - *Write/write collisions:* decided per cell. **Changed:** a collision
    marked every cell of the second write as undefined, so a wide write and a
    narrow write at the same instant gave different results depending on which
    ran second.
  - *Old-data coverage and read/write collisions:* taken from the write log
    when the read is resolved. A read is resolved only after the instant has
    passed (common clock) or after a full collision window (two clocks), so
    every write that can affect it is already logged. **Changed in `cada114`.**
  - *Stimulus and sampling:* inputs change on falling edges and are sampled on
    rising edges; DUT outputs are observed on falling edges; a common clock
    comes from a single process, so both edges fall in the same delta. Not
    changed.
  - *FIFO scoreboard:* the writer pushes a word before the edge that writes
    it, and the reader pops only words flagged by `valid_o`, so a push and a
    pop in the same delta never touch the same word. Not changed.
  - *Pending queue, counters and the end process:* updates to a protected
    type, so concurrent calls are serialized. The end process flushes only
    after both stimuli finished plus four collision windows. Not changed.
- **Coverage threshold:** every RAM configuration has a minimum number of
  compared reads after all exclusions (`g_min_checked`, 1000 by default). A
  FIFO configuration compares every word written. The final line of each test
  reports the simulator and its version.
- **Simulator:** `sim/vunit/run.py` uses GHDL unless `VUNIT_SIMULATOR` is
  set. The run holds the machine-wide EDA lock. With Questa 2021.1, run one
  test at a time (`-p 1`).
- **Mutation check** (`python sim/vunit/mutation_check.py`): six deliberate
  defects were introduced in the cores one at a time. Each one made a test
  fail at simulation time (not at compile time):
  1. reversed sub-word order;
  2. second output stage not gated by the enable;
  3. write ignoring `ena_i`;
  4. latency 2 taken from stage 1;
  5. write-first instead of read-first on port A;
  6. port B ignoring `enb_i`.

### 3.11 Phase 2 notes (Vivado check)

- **Result:** 34 of 34 cases pass on Vivado 2023.1: 17 cases on
  `xc7a35tfgg484-2` and the same 17 on `xcku3p-ffva676-2-e`. The cases cover
  32 x 1024 at latency 1 and 2, small RAMs and FIFOs, depths that are not a
  power of two, mixed width in both directions, and the FIFOs. Every case has
  0 CRITICAL WARNINGs and no forbidden message (`docs/results/`).
- **Mixed-width inference was broken in phase 1** and is fixed (`2406a71`). With
  the narrow index computed as `address * ratio + i`, Vivado reported
  `Synth 8-4767` ("multiple writes via different ports in same process") and
  built the RAM from registers. The VUnit suite could not see this, because
  the behaviour was correct. The core now uses the concatenation
  `address & i`, as in the asymmetric templates of UG901, and asserts a
  power-of-two ratio. The original documentation already required one.
- **Family-dependent results:** small memories (64 x 16, and the 16-word
  FIFOs) use one RAMB18 or distributed RAM depending on the family. The
  expected results are therefore per part. Four of them were written after
  the first measurement, once the cells had been checked to be RAM
  primitives (`docs/results/README.md`).
- **Tool status:** `tool-error` (synthesis did not complete) is reported
  separately from `fail` (results differ from the expected results); neither
  counts as a pass. Seven runs ended in `tool-error` on the reference machine
  and passed when run again by hand. The symptoms were unreadable
  installation files, a broken internal `.Xil` script, and silent stops,
  while other sessions were running Vivado without the shared lock. The
  automatic retry still covers only `couldn't read file`.
- **`--evaluate-only`** checks existing results again with the current
  expected results without running the tool. It was used to produce the
  final table from the latest run of every case.
- **Not covered yet:** two-writer RAMs (phase 3), and other vendors (phase 4).

### 3.12 Phase 3 notes (two-writer RAMs on AMD)

- **Layout:** `lm_mem_ram_crw_crw` has its entity in
  `src/core/lm_mem_ram_crw_crw_ent.vhd` and three architectures, all named
  `a_rtl` (coding style), picked by the manifest: `src/sim/` (simulation
  reference model), `src/vendor/amd/` (Vivado, UG901 true dual-port
  read-first template with a plain shared variable, asymmetric addressing
  `address & i`) and `src/vendor/unsupported/` (fallback for every other
  synthesis flow). `lm_mem_ram_rw_rw` and `lm_mem_ram_crw_cw` are unchanged
  wrappers.
- **Simulation architecture:** one process for both clocks, so collisions are
  decided in one process execution. The collision window is the period of
  the slower clock, measured from the edges (the same as the harness
  window), and equality of instants when the periods are not known yet.
  Write/write collision: warning, every cell of both writes 'X' (whole
  words) until the next write without collision. Read/write collision: warning; the read returns 'X' when the
  write is at the same instant or earlier, a warning only when it is later
  (the data was already delivered). Accesses beyond the depth: warning, write
  ignored, read 'X'.
- **Harness:** `g_x_on_undef` checks that a read of a cell undefined by a
  write/write collision returns 'X' (simulation models only). Without it, a
  model that forgets the undefined state could not be caught, because those
  reads are excluded from the comparison. Two-writer configurations: 11 (one
  and two clocks, mixed width both ways, latencies 1/1 to 2/2, depths 40 and
  48).
- **Mutation check:** six more defects in the simulation architecture, all
  detected (12/12 in total). The check now works on a copy of `src/`, `sim/`,
  `manifests/` and `tools/`, and fails if `src/` changed. Not covered by
  design: the data returned for an excluded read.
- **Static checks** (`f_static_check` in `lm_mem_pkg`): Vivado 2023.1 ignores
  VHDL assertions unless `synth_design -assert` is used. Probes on
  `xc7a35tfgg484-2`: without `-assert`, no form stops synthesis or prints its
  text (architecture, entity statement part, process, function used for a
  constant, if-generate, severity error and failure, concatenated message,
  static `false`); with `-assert`, every form stops with
  `[Synth 8-63] RTL assertion: "<text>"`. Before this change the fallback
  and every parameter check of the cores were silently ignored by Vivado
  (the fallback produced an empty module). Each check is now a constant of
  subtype `natural range 0 to 0` computed by a function that asserts the
  message: simulators and Vivado with `-assert` print the message; Vivado
  without `-assert` stops with `[Synth 8-11323] assigned value '1' out of
  range` at the line of the `C_ERROR_<CHECK>` constant, whose comment says
  what is wrong. The checks are declared before every declaration that
  depends on them.
- **Vivado tool errors on the reference machine:** intermittent `couldn't
  read file` for installation files that exist, `invalid command name
  "rt-undefined"`, both in runs that launch the synthesis helper process
  (also with `general.maxThreads 1`), and `[Common 17-680] Path length
  exceeds 260-Byte` with deep work directories (Vivado doubles the path in
  `.Xil`). They are tool errors, never results: a run that did not reach
  elaboration is reported as `tool-error` by the expected-failure check.

## Appendix: how the reproduction was run

For each case, one Vivado batch run in a scratch directory:

```tcl
foreach f $sources { read_vhdl -vhdl2008 $f }   ;# src/common, src/generic, src/inference, src/primitives/xilinx, src/fifo
set_msg_config -id {Synth 8-6859} -limit 1000000  ;# only for the "limit raised" runs
synth_design -mode out_of_context -top <entity> -part xc7a35tfgg484-2 -generic <name>=<value>
report_utilization     -file util.rpt
report_ram_utilization -file ram.rpt
```

Generics were passed from inside Tcl (see the `cmd.exe` note in section 4).
For every run, the log line `Parameter <name> bound to: <value>` was checked
against the intended value.
