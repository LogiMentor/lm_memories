# Roadmap

## RAM

- Expand self-checking tests for every imported RAM wrapper.
- Add initialization-file support compatible with the legacy TCL flow.
- Add explicit read-during-write mode documentation and tests.
- Add vendor-aware inference modules for AMD/Xilinx, Intel/Altera, Lattice, and
  Microchip/Microsemi families.
- Add primitive wrappers where inference cannot guarantee the requested memory.

## ROM

- Add generic ROM modules.
- Add generated ROM wrappers through `lm_memgen`.
- Migrate the legacy input-file memory generation flow into a Python path.

## FIFO

- Extend synchronous FIFO coverage with randomized traffic and almost flag tests.
- Import CDC resynchronizer support.
- Add asynchronous FIFO implementation and tests.
- Add width-conversion FIFO support after ratio RAM coverage is complete.

## Tooling

- Grow `lm_memgen` from a wrapper generator into a validation-aware memory
  selection tool.
- Add vendor synthesis smoke tests where tool licensing and runners allow it.
- Publish examples for common RAM, ROM, and FIFO instantiations.
