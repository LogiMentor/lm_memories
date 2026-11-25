<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor Srl -->

# Coding Style

This file is the VHDL coding style reference for `lm_memories`.

## Naming

- HDL keywords and names are lowercase except constants.
- Constants use the `C_` prefix and uppercase names.
- Generics use `g_`.
- Signals use `s_`.
- Variables use `v_`.
- Types use `t_`.
- Functions use `f_`.
- Procedures use `p_`.
- Input ports end in `_i`.
- Output ports end in `_o`.
- Inout ports end in `_io`.
- Clock ports use `clk_*`.
- Reset ports use `rst_*`.
- Instances use `inst_*`.
- Processes use `proc_*`.
- Generate blocks use `gen_*`.
- Synthesizable architectures use `a_rtl`; testbenches use `a_tb`.

## Formatting

- Indentation is 2 spaces.
- Prefer one declaration per line when readability improves.
- Use named association for generic and port maps.
- File names match entity or package names.
- Keep one synthesizable entity per RTL file.
- Put entity and architecture, or package and package body, in the same file.
  Exception: a dual-model entity (`lm_mem_ram_crw_crw`) has its entity in
  `src/core/<name>_ent.vhd` and one architecture file per flow
  (`src/sim/`, `src/vendor/<vendor>/`), all with the same architecture name;
  the file manifest selects one.
- Recommended maximum line length is 120 to 140 characters.

## Language

- Use `ieee.std_logic_1164` and `ieee.numeric_std`.
- Do not use non-standard arithmetic packages.
- Use `std_logic`, `std_logic_vector`, `signed`, and `unsigned`.
- Use explicit conversions at module boundaries.
- Use `resize()` for width adaptation.
- Avoid hard-coded numeric literals and vector dimensions.
- Use generics for architectural parameters and package constants for shared
  constants.

## RTL

- Resets are synchronous unless explicitly stated otherwise.
- Avoid gated, inverted, or multiplexed clocks unless the design explicitly
  requires them.
- Use one rising-edge process per clock domain when practical.
- Avoid latch inference.
- Avoid delay constants in RTL code.
- Avoid embedded synthesis commands except `synthesis translate_off/on`.
- Prefer registered outputs for hierarchical blocks.
- Clearly document clock-domain crossing assumptions.
- Synchronize single-bit clock-domain crossings and use proper handshakes or
  FIFOs for multi-bit crossings.
- A generic never selects a model of an entity: the file manifest selects the
  architecture (no configurations, no `translate_off/on` around behaviour,
  no `if ... generate` on a generic to choose the model).
  Exception: an optional function of a port that a boolean generic enables
  (the read-output reset, issue #7) selects between two processes with two
  separate `if ... generate` blocks, one per value of the generic
  (`gen_rd : if not g_rd_rst_en generate` and
  `gen_rd_rst : if g_rd_rst_en generate`). No `else generate`, `elsif
  generate` or `case generate`: they are VHDL-2008, and the sources must
  analyse as VHDL-93. The block without the function keeps the process of
  the previous release unchanged.

## Testbenches

- Testbench entities use `tb_<dut_name>`.
- Testbench architectures use `a_tb`.
- Testbenches are self-checking and use assertions.
- Clock and reset generation are isolated in clearly named processes.
- Waveform inspection alone is not sufficient for regression coverage.

## Headers

Every source file uses an SPDX license line and a copyright line. VHDL files
also carry the project header used by the current source tree.
