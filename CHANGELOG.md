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
  The two-writer RAMs (`lm_mem_ram_rw_rw`, `lm_mem_ram_crw_cw`,
  `lm_mem_ram_crw_crw`) are not fixed yet.

### Breaking changes

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
  undefined, also with a single clock. Read during write on the same port, and
  on a common-clock simple dual-port RAM, still returns the old data.
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
  different sessions on the same machine queue up.
