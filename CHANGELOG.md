<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor Srl -->

# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Fixed

- Generic RAMs were not inferred as block or distributed RAM by vendor tools
  (#3). The single-writer RAMs (`lm_mem_ram_r_w`, `lm_mem_ram_cr_cw`,
  `lm_mem_ram_r_w_ratio`, `lm_mem_ram_cr_cw_ratio`, `lm_mem_ram_crw_cr`) and
  all FIFOs now use the portable cores `lm_mem_ram_sdp` and
  `lm_mem_ram_tdp1w`. Vivado 2023.1 infers them; for example,
  `lm_mem_ram_r_w` 32 x 1024 on `xc7a35tfgg484-2` uses one RAMB36 and no
  flip-flops, where it used 65,568 flip-flops before.
  The two-writer RAMs (`lm_mem_ram_crw_crw`, `lm_mem_ram_rw_rw`,
  `lm_mem_ram_crw_cw`) have a Vivado-only architecture of
  `lm_mem_ram_crw_crw` (UG901 true dual-port template). Its inference check
  and post-synthesis simulation are in progress (issue #3, phase 3).

### Breaking changes

- **Wrong generics stop synthesis in every tool**, also in AMD Vivado, which
  ignores VHDL assertions unless `synth_design -assert` is used. Before, the
  parameter checks of the RAM cores, the ratio wrappers and the FIFO were
  plain assertions, so Vivado synthesized a design with, for example, read
  latency 3 or a width ratio of 3. Each check is now an assertion plus a
  constant outside its range (`f_static_check` in `lm_mem_pkg`). Vivado
  without `-assert` reports `[Synth 8-11323] assigned value '1' out of range`
  at the line of the failing `C_ERROR_<CHECK>` constant; with `-assert`, and
  in simulation, the tools print the message.
- **Two-writer RAMs with tools other than AMD Vivado** (`lm_mem_ram_crw_crw`,
  `lm_mem_ram_rw_rw`, `lm_mem_ram_crw_cw`) now **stop synthesis** with the
  message `no validated two-writer RAM architecture for this tool`; Vivado
  without `-assert` stops at the constant
  `C_ERROR_NO_VALIDATED_TWO_WRITER_RAM_FOR_THIS_TOOL` instead (before: the
  RAM was built from flip-flops). The `synth-intel`, `synth-lattice` and
  `synth-microchip` flows select this fallback until a vendor architecture is
  validated.
- **`src/generic/lm_mem_ram_crw_crw.vhd` is gone.** The entity is now in
  `src/core/lm_mem_ram_crw_crw_ent.vhd`, and its architecture is in one file
  per flow: `src/sim/lm_mem_ram_crw_crw_rtl.vhd` (simulation),
  `src/vendor/amd/lm_mem_ram_crw_crw_rtl.vhd` (Vivado) or
  `src/vendor/unsupported/lm_mem_ram_crw_crw_rtl.vhd` (fallback). A file list
  that is not updated fails at analysis or elaboration, not silently. Take
  the list from `manifests/lm_memories.toml`.
- **Two writes to the same address from both ports** of a two-writer RAM, in
  the same cycle or less than one period of the slower clock apart, leave
  that address undefined until the next write to it. The simulation model
  warns and returns 'X' for it, and for a read that collides with an earlier
  or simultaneous write of the other port (before: a deterministic value).
- **Addresses at or beyond the depth** (possible only when a depth is not a
  power of two) are now undefined:
  - a read returns undefined data (before: zeros);
  - a write never changes an address within the depth (before: ignored).
  The cores have no address comparison. Their storage covers the whole
  address range, so a depth that is not a power of two uses the next power of
  two words. For example, a 32 x 1536 `lm_mem_ram_r_w` now uses two RAMB36 on
  7-series. Designs that only use addresses below the depth are not affected.
- **Width ratios must be a power of two** in `lm_mem_ram_r_w_ratio`,
  `lm_mem_ram_cr_cw_ratio` and the FIFOs. This was already documented; the
  core now asserts it, because the asymmetric RAM template (narrow address =
  wide address & sub-word index) needs it.
- **`tools/vendor_synth`** passes a case only when its results match
  `tools/vendor_synth/expected/<vendor>.toml`. Cases without expected results
  are `unchecked`, and a missing tool is `not-run`. The default output
  directory is outside the repository. Vivado runs out of context in
  non-project mode.
- **Read during write between different ports** of any true dual-port RAM is
  undefined, also with a single clock (`lm_mem_ram_rw_rw`). Read during
  write on the same port, and on a common-clock simple dual-port RAM, still
  returns the old data.
- **File lists:** compile the files listed for your flow in
  `manifests/lm_memories.toml` (`python -m tools.manifest list --flow ...`).
  New files are required (`src/core/`), and later releases will select
  architectures per flow.
- **Python 3.11** or newer is required by the tools (`tomllib`).

### Deprecated

- `g_simulation` has no effect. It stays in every entity for compatibility,
  and each RAM core reports `g_simulation is deprecated and ignored` as a note
  at the start of simulation. Vivado 2023.1 does not print that note during
  synthesis. It will be removed in a future major release.

### Added

- `manifests/lm_memories.toml` and `tools/manifest`: compile order per flow,
  plus language-level checks (GHDL `--std=93c` and `--std=08`).
- VUnit testbenches with a collision-aware scoreboard, comparison with a
  frozen copy of the previous RAM model, coverage thresholds, and a mutation
  check (`sim/vunit/`).
- Vivado inference check (`tools/vendor_synth/vivado_check.toml`) on
  `xc7a35tfgg484-2` and `xcku3p-ffva676-2-e`, with results in
  `docs/results/`.
- `tools/eda_lock.py`: machine-wide lock so that heavy tool runs from
  different sessions on the same machine queue up, served in order of
  arrival.
- Static-check cases in `tools/vendor_synth` (`fallback_check.toml` for
  Vivado with and without `-assert`, `quartus_check.toml`): the fallback and
  wrong generics must stop synthesis at the right check. A case can expect a
  stop (`expect_failure`, `expect_failure_constant`); a run that never
  reached elaboration is a `tool-error`, not a result.
