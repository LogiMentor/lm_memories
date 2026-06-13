## VHDL Coding Standard

### 1. Scope

This coding standard applies to:

* Synthesizable RTL (default)
* Behavioral models (explicitly marked)
* Testbenches (explicitly marked)

The goal is to produce readable, consistent, review-friendly VHDL with predictable synthesis and simulation behavior.

---

## 2. Guidelines

### 2.1 Naming & Style

* **G10** Use lowercase letters for all HDL keywords and names, except constant names.
* **G20** Clock signal names shall use a consistent prefix: `clk_*`.
* **G30** Reset signal names shall use a consistent prefix: `rst_*`. Default reset is **active-low synchronous**, e.g. `rst_n_i`.
* **G40** Names should be less than 20 characters long.
* **G50** Input ports shall be named `<name>_i`.
* **G60** Output ports shall be named `<name>_o`.
* **G70** Input/output ports shall be named `<name>_io`.
* **G80** Generics shall be named `g_<name>`.
* **G90** Signals shall be named `s_<name>`.
* **G100** Variables shall be named `v_<name>`.
* **G110** Architectures shall be named `a_<name>`.
  * Typical usage: `a_rtl` (synthesizable), `a_behav` (behavioral), `a_tb` (testbench)
* **G120** Types shall be named `t_<name>`.
* **G130** Constants shall be named `C_<name>` and shall be written in uppercase.
* **G140** Functions shall be named `f_<name>`.
* **G150** Procedures shall be named `p_<name>`.
* **G160** Each instance shall be labeled `inst_<name>`.
* **G170** Use `(x downto 0)` for VHDL buses.
* **G180** Use `(0 to x)` for VHDL arrays.
* **G190** Use **named association** for generic and port mappings; positional association is forbidden.
* **G200** Each process shall be labeled `proc_<name>`.
* **G210** Each generate block shall be labeled `gen_<name>`.
* **G220** Indentation shall be set to **2 spaces**.
* **G230** Put entity and architecture, or package and package body, in the same file.
* **G240** Format each source file with aligned assignments and consistent code beautification.
* **G250** Each source file shall have an appropriate header at the top of the file.

### 2.2 File Header Template

```vhdl
--=============================================================================
-- Module Name : <module_name>
-- Library     : -
-- Project     : -
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  <functional description>
--  <clock/reset assumptions>
--  <interface notes>
-------------------------------------------------------------------------------
```

---

## 3. Rules

### 3.1 Language & Synthesis Rules

* **R10** Use IEEE types only. Use `std_logic`, `std_logic_vector`, `signed`, and `unsigned` from `ieee.numeric_std`. Do not use `bit` or `bit_vector`. Enumerated types are allowed for FSMs.
* **R20** Functions shall reference only arguments and local variables. Avoid multiple exit points and multiple return statements.
* **R30** Avoid embedded synthesis commands, except `synthesis translate_off/on`.
* **R40** Avoid hard-coded numeric literals and vector dimensions. Use generics for architectural parameters. Use package constants for complex modules.
* **R50** Avoid mixing positive-edge and negative-edge triggered flip-flops. Avoid gating, inverting, or multiplexing clock signals unless explicitly required.
* **R60** FSMs shall be coded using a **single clocked process** and an enumerated state type.
* **R70** Do not use delay constants in RTL code.
* **R80** Sensitivity lists shall be complete and shall not contain unnecessary signals. Prefer `process(all)` for combinational logic when supported.
* **R90** Prefer signals over variables for synthesizable logic.
* **R100** Avoid default initialization. When an initial value is required, use a reset.
* **R110** Avoid latch inference.
* **R120** Outputs of hierarchical blocks shall be registered whenever possible. Avoid combinational outputs.
* **R130** Structural and behavioral descriptions shall not be mixed in the same file as much as possible.

---

## 4. Additional Mandatory Conventions

### 4.1 Project & File Organization

* One synthesizable entity per RTL file.
* File name shall match the entity name.
* Recommended directory structure, basic example:

  * `src/`   : RTL sources
  * `sim/`   : testbenches and simulation scripts
  * `docs/`  : documentation
* Shared types and constants shall be placed in dedicated packages.

### 4.2 Libraries and Use Clauses

* Use only required libraries.
* Preferred order:

  1. `ieee.std_logic_1164`
  2. `ieee.numeric_std`
  3. Project-specific packages
* Non-standard arithmetic packages are forbidden.

### 4.3 Port List Conventions

* Ports shall be ordered as follows:

  1. Clock and reset
  2. Inputs
  3. Outputs
  4. Inouts
* Group related interfaces and add short comments (e.g. AXI, streaming, memory).

### 4.4 Synchronous Process Style

* Use one rising-edge process per clock domain.
* Resets are synchronous unless explicitly stated otherwise.
* Use a consistent structure:

  * Default assignments for registered signals
  * Conditional logic (`if`, `case`) after defaults
  * Complete `case` statements with `when others =>`

### 4.5 Combinational Logic

* All outputs shall be assigned default values at the beginning of the process.
* No implied memory or latches are allowed.
* Avoid using combinational process as much as possible, prefer synchronous processes.

### 4.6 Numeric and Conversion Rules

* All arithmetic shall be performed on `signed` or `unsigned` types only.
* Conversions between types shall be explicit and local.
* Use `resize()` for width adaptation.
* Conversion to/from `std_logic_vector` shall only occur at module boundaries.

### 4.7 Clock Domain Crossing (CDC) and Reset Hygiene

* No unsynchronized signals shall cross clock domains.
* Single-bit signals shall use synchronizer flip-flops.
* Multi-bit signals shall use proper handshakes or FIFOs.
* Reset domain assumptions shall be clearly documented and respected.

### 4.8 Assertions and Defensive Coding

* Use `assert` statements to detect invalid generics.
* Assertions intended only for simulation may be wrapped with `translate_off/on`.

### 4.9 Testbench Conventions

* Testbench entity naming: `tb_<dut_name>`.
* Testbench architecture: `a_tb`.
* Testbenches shall be self-checking; waveform inspection alone is not sufficient.
* Clock and reset generation shall be isolated in clearly named processes.

### 4.10 Formatting Rules

* Maximum recommended line length: 120–140 characters.
* Prefer one declaration per line when readability improves.
* Align `=>` in port maps and `<=` in grouped assignments where practical.

---

## 5. Compliance

All new code shall comply with this standard. Deviations must be justified and documented in code comments or design notes.
