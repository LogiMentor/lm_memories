# Coding Style

All new VHDL follows [`LM_VHDL_coding_standard.md`](LM_VHDL_coding_standard.md).

Project conventions used in this import:

- HDL names and keywords are lowercase except constants, which use `C_`.
- Generics use the `g_` prefix.
- Input ports end in `_i`; output ports end in `_o`.
- Clock ports use `clk_*`; reset ports use `rst_*`.
- Synthesizable architectures use `a_rtl`; testbenches use `a_tb`.
- Processes are labeled with `proc_`.
- Generate blocks are labeled with `gen_`.
- Indentation is 2 spaces.
- RTL uses `ieee.std_logic_1164` and `ieee.numeric_std`.
- Entity and file names match.
- Testbenches are self-checking and use assertions.

Legacy code that still violates the standard remains in `legacy_inputs/` until
its behaviour is refactored into compliant `src/` modules.
