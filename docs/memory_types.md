# Memory Types

`lm_memories` separates portable models from vendor-aware implementation code.

## Generic

The generic class in `src/generic/` provides clean behavioural VHDL that is
usable for simulation, early integration, and portable synthesis experiments.
The first imported RAMs preserve the legacy naming semantics with new
`lm_mem_*` entity names.

## Inference

The inference class in `src/inference/` is reserved for vendor, tool, and FPGA
family-aware templates. These modules will keep portable public interfaces while
selecting coding patterns known to infer block RAM, distributed RAM, UltraRAM,
or equivalent resources in specific tools.

## Primitives

The primitives class in `src/primitives/` is reserved for explicit vendor
primitive or IP wrappers. These wrappers are intended for cases where inference
is not precise enough or a project needs exact primitive selection.

## RAMs

Current RAM modules are:

- `lm_mem_ram_crw_crw`: true dual-port RAM, independent clocks, both ports read/write.
- `lm_mem_ram_rw_rw`: common-clock dual-port RAM, both ports read/write.
- `lm_mem_ram_cr_cw`: dual-clock write/read RAM with equal widths.
- `lm_mem_ram_cr_cw_ratio`: dual-clock write/read RAM with different widths.
- `lm_mem_ram_crw_cr`: dual-clock read/write plus read-only RAM.
- `lm_mem_ram_crw_cw`: dual-clock read/write plus write-only RAM.
- `lm_mem_ram_r_w`: common-clock write/read RAM with equal widths.
- `lm_mem_ram_r_w_ratio`: common-clock write/read RAM with different widths.

Different-width RAMs require an integer width ratio. Same-address dual-write
collisions are considered undefined in this initial generic model.

## FIFOs

Current FIFO modules are:

- `lm_mem_fifo_sync`: synchronous FIFO with active-low synchronous reset,
  empty/full flags, almost-empty/full flags, and valid output.

Async FIFO support is planned after the CDC support package is imported.

## Legacy Import Notes

The legacy modules in `legacy_inputs/` used `lm_util_*` names and depended on
project packages that are not present in this public repository. The initial
import refactors the public memory/FIFO entities into standalone `lm_mem_*`
sources. Vendor-specialized branches, file initialization logic, and async FIFO
CDC resynchronizers are documented for staged migration rather than silently
copied with unresolved dependencies.
