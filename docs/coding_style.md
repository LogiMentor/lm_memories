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

## Testbenches

- Testbench entities use `tb_<dut_name>`.
- Testbench architectures use `a_tb`.
- Testbenches are self-checking and use assertions.
- Clock and reset generation are isolated in clearly named processes.
- Waveform inspection alone is not sufficient for regression coverage.

## Headers

Every source file uses an SPDX license line and a copyright line. VHDL files
also carry the project header used by the current source tree.
