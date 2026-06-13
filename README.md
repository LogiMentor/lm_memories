# lm_memories

`lm_memories` is an open-source VHDL memory and FIFO library by LogiMentor.
It is licensed under Apache-2.0 and is intended for use in commercial and
academic FPGA projects.

The library is organized around three implementation classes:

- `src/generic/`: portable behavioural VHDL for simulation and first-pass integration.
- `src/inference/`: planned vendor, tool, and family-aware RAM inference code.
- `src/primitives/`: planned explicit vendor primitive or IP wrappers.

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
- `lm_mem_fifo_sync`: generic synchronous FIFO.

The RAM, ROM, and FIFO roadmap is documented in
[`docs/roadmap.md`](docs/roadmap.md).

## GHDL Quick Start

Install GHDL, then run:

```sh
bash sim/scripts/run_ghdl.sh
```

The script analyzes all current VHDL sources, elaborates available
self-checking testbenches, and runs them with assertion failures enabled.

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
```

Generator modes:

- `generic`: emit reusable VHDL with wrapper generics.
- `fixed`: emit a wrapper with fixed width/depth values.

## Support

Commercial support, FPGA integration, vendor-specific validation, and custom
memory/FIFO development are available from LogiMentor. See
[`docs/services.md`](docs/services.md).
