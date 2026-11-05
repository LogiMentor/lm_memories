<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor Srl -->

# Changelog

All notable changes to this project are documented here.

## [Unreleased]

## [0.2.0] - 2026-11-05

First numbered release (issue #3, PR #4). It has breaking changes; before
1.0.0 they raise the minor number. Read **Migration for vendored copies**
first.

**Validated vendors:** AMD Vivado 2023.1 on Artix-7 (`xc7a35tfgg484-2`) and
Kintex UltraScale+ (`xcku3p-ffva676-2-e`); Intel Quartus 23.1
(`5CEBA4F23C7`) only for the portable cores and the static checks; Lattice
and Microchip not verified.

### Migration for vendored copies

For a copy of `src/` inside another repository. Each item says what to check
in the design that uses the library.

1. **File list.** Take it again from `manifests/lm_memories.toml`
   (`python -m tools.manifest list --flow synth-amd --format tcl`, `--flow
   sim`, ...). `src/generic/lm_mem_ram_crw_crw.vhd` is gone: copy
   `src/core/` and the architecture of your flow (`src/sim/`,
   `src/vendor/amd/` or `src/vendor/unsupported/`). An old list fails at
   analysis or elaboration ("no architecture"), not silently.
2. **Read outputs are undefined before the first read.** No read output
   register has an initial value: the data output is undefined until the
   first enabled read reaches it (with latency 2, until the second). The
   simulation model of the two-writer RAMs drives 'X' in that phase. Check
   that nothing samples the read data, or a value derived from it (a
   comparison, a checksum, a testbench check right after reset), before a
   read was enabled; qualify it with your own valid flag.
3. **Collisions are undefined.** On the two-writer RAMs, two writes to the
   same address from both ports, in the same cycle or less than one period
   of the slower clock apart, leave the word undefined until the next write
   to it (with mixed widths, also the untouched parts of the wide word). On
   any true dual-port RAM, a read on one port during a write of the other
   port to the same address returns undefined data, also with a single
   clock (`lm_mem_ram_rw_rw`). The simulation model of the two-writer RAMs
   warns and returns 'X' for both (before: a deterministic value). Arbitrate
   the two ports in the design; a testbench that expected the old value
   must avoid the collision or accept 'X'. Read during write on the same
   port still returns the old data; on a common-clock simple dual-port RAM
   the old data is guaranteed only for AMD Vivado so far.
4. **Addresses at or beyond the depth are undefined** (only possible with a
   depth that is not a power of two): a read returns undefined data (before:
   zeros); a write never changes a word within the depth (before: ignored).
   Check that nothing relies on reading zeros there. The storage now takes
   the next power of two words, which may cost more block RAM (32 x 1536
   `lm_mem_ram_r_w`: two RAMB36 on 7-series).
5. **`g_simulation` is deprecated.** It has no effect and still exists in
   every entity, so instantiations that set it keep compiling; each RAM core
   prints the note `g_simulation is deprecated and ignored` at the start of
   simulation. Remove it from your instantiations: a later release removes
   it, listed under its breaking changes.
6. **`LM_MEM_BUILD_DIR` is replaced by `LM_EDA_WORKDIR`** for the heavy runs:
   `tools/vendor_synth` and `sim/scripts/run_postsynth.py` work under
   `$LM_EDA_WORKDIR` (default `C:/lmeda` on Windows, `lmeda` in the system
   temporary directory elsewhere) and ignore `LM_MEM_BUILD_DIR`. The VUnit
   and mutation campaigns remove it from the environment of their runs.
   `LM_MEM_BUILD_DIR` still sets the output of the developer runners
   `sim/vunit/run.py` and `sim/scripts/run_ghdl.py`. Scripts that looked for
   synthesis results under `$LM_MEM_BUILD_DIR` must look under
   `$LM_EDA_WORKDIR`.
7. **AMD Vivado: add `-assert` to `synth_design`.** Without it Vivado ignores
   VHDL assertions; wrong generics still stop synthesis, but with
   `[Synth 8-11323] assigned value '1' out of range` at the line of a
   `C_ERROR_<CHECK>` constant instead of the message. With `-assert` the
   message is printed (`[Synth 8-63] RTL assertion: "..."`). In project mode
   put it in the More Options of the synthesis run, with `-name` and
   `-value`:

   ```tcl
   set_property -name {STEPS.SYNTH_DESIGN.ARGS.MORE OPTIONS} -value {-assert} -objects [get_runs synth_1]
   ```

   The short form `set_property STEPS.SYNTH_DESIGN.ARGS.MORE\ OPTIONS -assert
   [get_runs synth_1]` does not work: `set_property` reads `-assert` as one
   of its own options (`[Common 17-170] Unknown option '-assert'`), and the
   run is synthesized without it. Checked with Vivado 2023.1 on
   `xc7a35tfgg484-2` (`lm_mem_ram_r_w` with read latency 3): with the
   `-name`/`-value` form the run calls `synth_design ... -assert` and stops
   with `[Synth 8-63] RTL assertion: "lm_mem_ram_sdp: read latency must be 1
   or 2"`; without it, with `[Synth 8-11323]`.
8. **Generics that used to pass may now stop synthesis or elaboration** (see
   Breaking changes): width ratios that are not a power of two, FIFO
   almost-flag limits out of range (the default limits need a read depth of
   at least 3), `g_meta_levels` below 2, and the two-writer RAMs with any
   tool other than AMD Vivado.

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
  `lm_mem_ram_crw_crw` (UG901 true dual-port template). Vivado 2023.1 maps
  every checked configuration to block RAM only, on `xc7a35tfgg484-2` and
  `xcku3p-ffva676-2-e`, and the netlists pass the same tests as the RTL
  (post-synthesis simulation, `docs/results/`).

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
- **More wrong generics stop synthesis**, also in Vivado without `-assert`:
  `lm_mem_ccd_resync` with `g_meta_levels` below 2 (before: a one-flip-flop
  synchronizer), `lm_mem_ram_r_w_infer` with a latency other than 1 or 2 or
  an empty vendor, family or tool string, the primitive wrappers with an
  empty family or primitive string. The FIFOs now check their almost-flag
  limits: `g_almost_empty_limit` in 0 to read depth - 2 and
  `g_almost_full_limit` in 1 to the write depth (before: no check; outside
  these ranges a flag never changes). The default limits (1, 1) need a read
  depth of at least 3.
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
  the whole word written by each port undefined until the next write to it,
  with mixed widths also the untouched parts of the wide word. The simulation
  model warns and returns 'X' for it, and for a read that collides with an
  earlier or simultaneous write of the other port (before: a deterministic
  value).
- **Addresses at or beyond the depth** (possible only when a depth is not a
  power of two) are now undefined:
  - a read returns undefined data (before: zeros);
  - a write never changes an address within the depth (before: ignored).
  The cores have no address comparison. Their storage covers the whole
  address range, so a depth that is not a power of two uses the next power of
  two words. For example, a 32 x 1536 `lm_mem_ram_r_w` now uses two RAMB36 on
  7-series. Designs that only use addresses below the depth are not affected.
- **Read outputs are undefined until the first enabled read** reaches them
  (with latency 2, until the second), in every RAM: no output register has
  an initial value. This was already so for the single-writer RAMs and the
  previous two-writer model; the simulation model of the two-writer RAMs now
  drives 'X' in that phase (before this change in the PR: zeros). Hardware
  powers up with defined values, but no design may rely on them.
- **Width ratios must be a power of two** in `lm_mem_ram_r_w_ratio`,
  `lm_mem_ram_cr_cw_ratio` and the FIFOs. This was already documented; the
  core now asserts it, because the asymmetric RAM template (narrow address =
  wide address & sub-word index) needs it.
- **Work directories of heavy runs:** `tools/vendor_synth` and
  `sim/scripts/run_postsynth.py` work under `$LM_EDA_WORKDIR` (default
  `C:/lmeda` on Windows, `lmeda` in the system temporary directory
  elsewhere), no longer under
  `$LM_MEM_BUILD_DIR`, and prune passing runs to their logs and reports.
- **`tools/vendor_synth`** passes a case only when its results match
  `tools/vendor_synth/expected/<vendor>.toml`. Cases without expected results
  are `unchecked`, and a missing tool is `not-run`. The default output
  directory is outside the repository. Vivado runs out of context in
  non-project mode.
- **Read during write between different ports** of any true dual-port RAM is
  undefined, also with a single clock (`lm_mem_ram_rw_rw`). Read during
  write on the same port still returns the old data. On a common-clock
  simple dual-port RAM the old data is guaranteed only where verified for
  the vendor: AMD Vivado so far; Intel is checked in phase 4 (Quartus 23.1
  `Warning (276027)`: dual-clock RAM, read during write undefined).
- **File lists:** compile the files listed for your flow in
  `manifests/lm_memories.toml` (`python -m tools.manifest list --flow ...`).
  New files are required (`src/core/`), and later releases will select
  architectures per flow.
- **Python 3.11** or newer is required by the tools (`tomllib`).

### Deprecated

- `g_simulation` has no effect. It stays in every entity for compatibility,
  and each RAM core reports `g_simulation is deprecated and ignored` as a note
  at the start of simulation. Vivado 2023.1 does not print that note during
  synthesis. It will be removed in a later release, as a breaking change.

### Changed

- `tools/eda_lock.py`: the default free-memory threshold before a synthesis
  run (`LM_EDA_MIN_FREE_MB_SYNTH`) is 4096 MB (before: 8192 MB), the value
  used by the whole AMD campaign. Simulation stays at 2048 MB
  (`LM_EDA_MIN_FREE_MB_SIM`).

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
  arrival; `LM_EDA_LOCK_DIR` sets its directory (users sharing a Windows
  machine).
- `tools/eda_proc.py`: a timeout stops the whole process tree of the tool
  before the lock is released. `tools/eda_retry.py`: one retry policy
  (completed runs and project errors are never retried; the log of every
  attempt is kept). `tools/eda_provenance.py`: hash of sources and manifest,
  and commit, in every synthesis artefact. `tools/eda_archive.py`:
  campaign runs (`--campaign`) are archived outside the repository
  (`LM_EDA_ARCHIVE_DIR`) with their SHA-256 in the results.
- `sim/scripts/run_vunit_campaign.py`: the VUnit suite as a campaign, with
  structured results (xunit) and archived logs.
- Post-synthesis simulation of the two-writer RAMs on AMD
  (`sim/scripts/run_postsynth.py`): the same harness and scoreboard on Vivado
  funcsim netlists, with GHDL (UNISIM) and xsim, RTL and netlist side by side.
  Plain top-level `sim/vunit/tb_lm_mem_ram_plain.vhd` for simulators without
  VUnit.
- Static-check cases in `tools/vendor_synth` (`fallback_check.toml` for
  Vivado with and without `-assert`, `quartus_check.toml`): the fallback and
  wrong generics must stop synthesis at the right check. A case can expect a
  stop (`expect_failure`, `expect_failure_constant`); a run that never
  reached elaboration is a `tool-error`, not a result.
