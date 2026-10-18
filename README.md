<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor Srl -->

# lm_memories

`lm_memories` is an open-source VHDL memory and FIFO library by LogiMentor.
It is licensed under Apache-2.0 and is intended for use in commercial and
academic FPGA projects.

The library is organized around three implementation classes:

- `src/generic/`: portable behavioural VHDL for simulation and first-pass integration.
- `src/inference/`: vendor, tool, and family-aware RAM inference code.
- `src/primitives/`: primitive-ready vendor wrapper interfaces and simulation models.

Common packages live in `src/common/`, and FIFO implementations live in
`src/fifo/`.

## Current RTL

- `lm_mem_ram_sdp`, `lm_mem_ram_tdp1w`: portable RAM cores (`src/core/`) that
  vendor tools infer as block or distributed RAM. All one-writer RAMs and the
  FIFOs are built on them.
- `lm_mem_ram_crw_crw`: true dual-port RAM with two writers, two clocks and
  mixed width. It has one entity (`src/core/lm_mem_ram_crw_crw_ent.vhd`) and
  one architecture per flow, chosen by the file manifest: a simulation model
  (`src/sim/`), a Vivado architecture (`src/vendor/amd/`), and a fallback
  (`src/vendor/unsupported/`) that **stops synthesis** with an explicit
  message. Only AMD Vivado has a validated two-writer architecture so far;
  with other tools, `lm_mem_ram_crw_crw`, `lm_mem_ram_rw_rw` and
  `lm_mem_ram_crw_cw` do not synthesize.
- `lm_mem_ram_rw_rw`: common-clock RAM with two read/write ports (wraps
  `lm_mem_ram_crw_crw`).
- `lm_mem_ram_cr_cw`: dual-clock write/read RAM.
- `lm_mem_ram_cr_cw_ratio`: dual-clock write/read RAM with width ratio.
- `lm_mem_ram_crw_cr`: dual-clock read/write plus read RAM.
- `lm_mem_ram_crw_cw`: dual-clock read/write plus write RAM (wraps
  `lm_mem_ram_crw_crw`).
- `lm_mem_ram_r_w`: common-clock write/read RAM.
- `lm_mem_ram_r_w_ratio`: common-clock write/read RAM with width ratio.
- `lm_mem_fifo`: configurable FIFO core with common-clock and dual-clock modes.
- `lm_mem_fifo_sync`: synchronous FIFO wrapper.
- `lm_mem_fifo_async`: asynchronous FIFO wrapper.
- `lm_mem_ram_r_w_infer`: vendor-aware RAM inference implementation.
- Vendor primitive-ready RAM wrappers for AMD/Xilinx, Intel/Altera, Lattice,
  and Microchip/Microsemi.

Support and validation status is tracked in
[`docs/support_matrix.md`](docs/support_matrix.md).

## Simulation Quick Start

The VHDL sources are simulator-neutral and can be used with GHDL, QuestaSim,
ModelSim, XSIM, Riviera-PRO, Active-HDL, and other VHDL-2008 simulators. GHDL
is used by CI because it is available on hosted Linux runners.

For the CI smoke test source set, install GHDL and run one of:

```sh
python sim/scripts/run_ghdl.py
```

```powershell
.\sim\scripts\run_ghdl.ps1
```

```sh
bash sim/scripts/run_ghdl.sh
```

The scripts analyze current VHDL sources, elaborate available self-checking
testbenches, and run them with assertion failures enabled.

The constrained-random testbenches for the RAM cores, the RAM wrappers and the
FIFO run with VUnit (`pip install vunit_hdl`):

```sh
python sim/vunit/run.py -p 4
```

They compare every RAM read with a scoreboard and, cycle by cycle, with a
frozen copy of the previous RAM model (`sim/ref/`).

### Which files to compile

`manifests/lm_memories.toml` lists the files of each flow (simulation,
synthesis per vendor, tests) in compile order. Use it instead of globbing the
source tree. Some entities have several architectures with the same name, one
per flow, so compiling every file would give the wrong model. Print a flow's
file list with:

```sh
python -m tools.manifest list --flow sim
python -m tools.manifest list --flow synth-amd --format tcl
```

## Behaviour notes

- `g_simulation` is **deprecated** and has no effect. It is kept in every
  entity for compatibility; RAM cores report a note about it at the start of
  simulation. Do not rely on it.
- Addresses at or beyond the depth (only possible when a depth is not a power
  of two) are undefined: reads return undefined data, and writes never change
  an address within the depth. The storage covers the next power of two
  words.
- Read during write on the same port, or on a common-clock simple dual-port
  RAM, returns the old data. Between different ports of a true dual-port RAM,
  and between ports on different clocks, it is undefined. This includes
  `lm_mem_ram_rw_rw`, although it has a single clock: a read on one port of
  the address written by the other port in the same cycle returns undefined
  data.
- Two writes to the same address from the two ports of a two-writer RAM, in
  the same cycle or less than one period of the slower clock apart, leave
  that address undefined until the next write to it. The simulation model
  reports every such collision with a warning and returns 'X'.

### Wrong generics and unsupported tools stop synthesis

Every check of a generic (latency, width ratio, depth, FIFO options), and the
fallback of the two-writer RAMs for tools without a validated architecture,
is a static check (`f_static_check` in `lm_mem_pkg`): an assertion with an
explicit message, plus a constant outside its range for tools that ignore
assertions. Simulation, synthesis and elaboration all stop.

**Vivado users: add `-assert` to `synth_design`.** By default Vivado 2023.1
ignores VHDL assertions. Measured on `xc7a35tfgg484-2`, all of these are
ignored without `-assert` and stop synthesis with their text with it,
for severity error and failure: assertions in the architecture, in the
entity statement part, in a process, in a function used for a constant, in
an `if ... generate`, also with a concatenated message.

- With `-assert`: `ERROR: [Synth 8-63] RTL assertion: "lm_mem_ram_sdp: read
  latency must be 1 or 2"`.
- Without `-assert`, synthesis still stops, at the constant:
  `ERROR: [Synth 8-11323] assigned value '1' out of range [.../lm_mem_ram_sdp.vhd:<line>]`.
  The line holds `C_ERROR_<CHECK>`, and its comment says what is wrong.

See [`CHANGELOG.md`](CHANGELOG.md) for breaking changes.

## Heavy tool runs

Synthesis runs (`tools/vendor_synth`) and the VUnit runner take a
machine-wide lock file, `lm_eda_heavy_tools.lock`, in the system temporary
directory. Runs from different sessions on the same machine then wait for
each other instead of running in parallel. A waiting run prints who holds the lock.
Set the timeout with `LM_EDA_LOCK_TIMEOUT` (seconds, default 4 hours). The
lock is an operating-system lock on that file, so it is released when its
holder exits, also after a crash; no process is ever terminated. Waiting runs
are served in order of arrival.

Once a run holds the lock, it also waits until enough physical memory is
free before it starts its tool: 8 GB for synthesis (`LM_EDA_MIN_FREE_MB_SYNTH`,
in MB) and 2 GB for simulation (`LM_EDA_MIN_FREE_MB_SIM`). The timeout is
`LM_EDA_MEMORY_TIMEOUT` (seconds, default 4 hours). Tools started by other
projects do not take this lock; the memory check keeps a run from starting
while they fill the memory.

## Local Vendor Synthesis

Vendor synthesis checks run as local scripts rather than in hosted CI, because
the FPGA tools and their licenses must be installed on the developer machine.

`tools/vendor_synth` synthesizes each case and compares the results with the
expected results in `tools/vendor_synth/expected/<vendor>.toml`. Each entry
gives the number of block RAM tiles, LUTs used as memory, URAM, ceilings on
registers and logic LUTs, zero CRITICAL WARNINGs, and message IDs that must
not appear (for example Vivado `Synth 8-6859` multi-driven nets). A case
passes only when every field matches; the tool exit code alone never makes a
pass. Other results:

- `tool-error`: synthesis did not complete.
- `unchecked`: the case has no expected results.
- `not-run`: the tool is not installed.

None of them counts as a pass.

```sh
python -m tools.vendor_synth --config tools/vendor_synth/vivado_check.toml --keep-going
python -m tools.vendor_synth --config tools/vendor_synth/vivado_check.toml --evaluate-only   # re-check existing results
```

- Cases (`tools/vendor_synth/cases.toml`) synthesize the library entity itself
  out of context. Generics are written into the tool script, and the values
  the tool actually used are read back from its log; any difference fails the
  case.
- Sources come from the vendor's synthesis flow in the manifest.
- Message limits are raised so the counts are complete.
- Vivado's transient `couldn't read file` error is retried at most twice, and
  the retries are recorded.
- Runs are sequential and hold the machine-wide lock described above.
- Results go to `$LM_MEM_BUILD_DIR/vendor_synth`, or to
  `lm_memories_build/vendor_synth` in the system temporary directory. Each run
  writes `summary.md`, `summary.csv`, `summary.json`, and
  `summary_public.json` (no local paths). The checked results are kept in
  `docs/results/`.

Jobs configure the tool with `executable`, `install_dir`, optional
`setup_script`, or `command`. On Windows, point Vivado jobs at
`bin/vivado.bat` or set `install_dir`.

## lm_memgen Quick Start

The first `lm_memgen` CLI skeleton emits wrappers around the library modules:

```sh
python -m tools.lm_memgen ram \
  --ports crw_crw \
  --vendor xilinx \
  --family ultrascaleplus \
  --tool vivado \
  --width-a 32 \
  --depth-a 1024 \
  --width-b 8 \
  --read-latency-a 1 \
  --read-latency-b 1 \
  --style bram \
  --mode generic \
  --out examples/lm_mem_example.vhd

python -m tools.lm_memgen fifo \
  --vendor generic \
  --width 32 \
  --depth 1024 \
  --clocking sync \
  --out examples/lm_fifo_example.vhd

python -m tools.lm_memgen fifo \
  --vendor generic \
  --width 32 \
  --depth 1024 \
  --clocking async \
  --out examples/lm_fifo_async_example.vhd
```

Generator modes:

- `generic`: emit reusable VHDL with wrapper generics.
- `fixed`: emit a wrapper with fixed width/depth values.

## Coding Style

VHDL style and naming rules are documented in
[`docs/coding_style.md`](docs/coding_style.md).

## Planned Work

- Expand self-checking tests for every RAM and FIFO topology.
- Add initialization-file and ROM support.
- Extend vendor-aware inference modules for more RAM topologies and families.
- Replace primitive-ready simulation wrappers with exact vendor primitive
  bindings where required by synthesis-smoke-tested targets.

## Company

Commercial support, FPGA integration, vendor-specific validation, and custom
memory/FIFO development are available from LogiMentor:
[www.logimentor.com](https://www.logimentor.com).
