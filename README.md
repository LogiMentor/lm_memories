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

- `lm_mem_ram_crw_crw`: generic true dual-port RAM core.
- `lm_mem_ram_rw_rw`: common-clock RAM with two read/write ports.
- `lm_mem_ram_cr_cw`: dual-clock write/read RAM.
- `lm_mem_ram_cr_cw_ratio`: dual-clock write/read RAM with width ratio.
- `lm_mem_ram_crw_cr`: dual-clock read/write plus read RAM.
- `lm_mem_ram_crw_cw`: dual-clock read/write plus write RAM.
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

For commercial simulators, compile files in the same order used by
`sim/scripts/run_ghdl.py`: common package, generic RAMs, inference RAMs, FIFOs,
primitive wrapper simulation models, then testbenches.

## Local Vendor Synthesis

Vendor synthesis checks are provided as local scripts rather than hosted CI jobs
because FPGA tools and licenses must be installed on the developer machine.

The local runner generates synthesizable tops, emits vendor batch scripts, runs
enabled tool/family jobs, and collects logs and summary files under
`build/vendor_synth/` by default.

Dry-run the generated vendor project scripts with:

```sh
python -m tools.vendor_synth --config tools/vendor_synth/local.example.toml --dry-run --include-disabled
```

Local jobs should normally point to the vendor executable or batch file. An
installed tool directory and optional environment setup can also be configured.
For Vivado on Windows, using the full path to `bin/vivado.bat` is preferred.

The runner supports `executable`, `install_dir`, optional `setup_script`, and
advanced `command` launch styles. Vivado jobs use the vendor default thread
setting when `threads` is unset or `threads <= 1`; set `threads >= 2` only when
a fixed higher thread count is needed.

Results are written as `summary.json`, `summary.csv`, `summary.md`, and one
per-case directory containing the generated top, tool script, log, and reports.
The runner also scans vendor logs for common error text because some Vivado
failures still return process exit code 0.

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
