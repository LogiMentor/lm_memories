We are in the root of the public GitHub repository LogiMentor/lm_memories.

Goal:
Create an open-source VHDL memory and FIFO library for FPGA designs.

License:
Apache-2.0.

Important existing folders:
- docs/
- legacy_inputs/

Use the files in legacy_inputs/ as the legacy implementation to import, refactor, and preserve.
Use docs/LM_VHDL_coding_standard.md as the mandatory coding standard.

Repository structure:
Create and use this structure:

src/
  common/
  generic/
  inference/
  primitives/
  fifo/

sim/
  tb/
  scripts/

tools/
  lm_memgen/
  legacy/

docs/
examples/
.github/workflows/

Architecture:
Document the three implementation classes, but do not name folders level0/level1/level2.

Use:
- src/generic/ for portable behavioural VHDL
- src/inference/ for vendor/tool/family-aware RAM inference code
- src/primitives/ for explicit vendor primitive or IP wrappers
- src/fifo/ for FIFO implementations
- src/common/ for packages, common functions, shared types and constants

Initial import:
Import and refactor these legacy files from legacy_inputs/:

- lm_util_ram_crw_crw.vhd
- lm_util_ram_rw_rw.vhd
- lm_util_ram_cr_cw.vhd
- lm_util_ram_cr_cw_ratio.vhd
- lm_util_ram_crw_cr.vhd
- lm_util_ram_crw_crw.vhd
- lm_util_ram_crw_cw.vhd
- lm_util_ram_r_w.vhd
- lm_util_ram_r_w_ratio.vhd
- lm_util_fifo.vhd
- ces_mem_from_file_gen.tcl

Rename old lm_util_* entities to lm_mem_* names.

Suggested mapping:
- lm_util_ram_crw_crw     -> lm_mem_ram_crw_crw
- lm_util_ram_rw_rw       -> lm_mem_ram_rw_rw
- lm_util_ram_cr_cw       -> lm_mem_ram_cr_cw
- lm_util_ram_cr_cw_ratio -> lm_mem_ram_cr_cw_ratio
- lm_util_ram_crw_cr      -> lm_mem_ram_crw_cr
- lm_util_ram_crw_cw      -> lm_mem_ram_crw_cw
- lm_util_ram_r_w         -> lm_mem_ram_r_w
- lm_util_ram_r_w_ratio   -> lm_mem_ram_r_w_ratio
- lm_util_fifo            -> lm_mem_fifo_sync

If a compatibility wrapper is useful, keep it under a clearly marked legacy/compatibility location and document it.

Coding standard:
Strictly follow docs/LM_VHDL_coding_standard.md.

Especially:
- lowercase HDL names and keywords
- file name must match entity name
- one synthesizable entity per RTL file
- generics named g_<name>
- input ports named <name>_i
- output ports named <name>_o
- clock ports named clk_*
- reset ports named rst_*
- architectures named a_rtl, a_behav, or a_tb
- processes labeled proc_<name>
- generate blocks labeled gen_<name>
- 2-space indentation
- IEEE std_logic_1164 and numeric_std only
- named association only
- self-checking testbenches
- testbench entity names tb_<dut_name>

Documentation:
Create or update:
- README.md
- docs/memory_types.md
- docs/vendor_matrix.md
- docs/coding_style.md
- docs/roadmap.md
- docs/services.md

README.md must mention:
- lm_memories is an open-source VHDL memory and FIFO library by LogiMentor
- Apache-2.0 license
- usable in commercial and academic FPGA projects
- generic, inference, and primitives implementation classes
- RAM, ROM, FIFO roadmap
- GHDL simulation quick start
- lm_memgen quick start
- commercial support, FPGA integration, vendor-specific validation, and custom memory/FIFO development are available from LogiMentor

Vendor matrix:
Create docs/vendor_matrix.md with planned/current support status for:
- AMD/Xilinx: ISE, Vivado; Spartan-6, 7-Series, UltraScale, UltraScale+
- Intel/Altera: Quartus; Cyclone, Arria, Stratix
- Lattice: Diamond, Radiant, Yosys/nextpnr; iCE40, ECP5, Nexus
- Microchip/Microsemi: Libero; IGLOO, SmartFusion, RTG4, PolarFire
- Generic: GHDL, Yosys where applicable

Use support status values:
- planned
- simulated
- synthesis-smoke-tested
- production-tested

Testbenches:
Create self-checking VHDL testbenches under sim/tb/.

At minimum create:
- one RAM testbench
- one FIFO testbench

Then add more where practical.

Tests should cover:
- reset behaviour
- basic write/read
- simultaneous read/write
- dual-port operation
- width/depth generics
- ratio memories where practical
- FIFO empty/full behaviour
- randomized write/read sequences where practical

Use assertions.
Make tests runnable with GHDL.

Simulation scripts:
Create sim/scripts/run_ghdl.sh that analyzes, elaborates, and runs all available testbenches.

CI:
Create GitHub Actions workflow under .github/workflows/.

CI must:
- install/use GHDL
- analyze all VHDL sources
- elaborate testbenches
- run self-checking tests
- fail on assertion errors

Python generator:
Create tools/lm_memgen/ as a first usable CLI skeleton.

It should support commands like:

lm_memgen ram \
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

lm_memgen fifo \
  --vendor generic \
  --width 32 \
  --depth 1024 \
  --clocking sync \
  --out examples/lm_fifo_example.vhd

Generator modes:
- generic: emit reusable VHDL with generics
- fixed: emit fully fixed width/depth VHDL without generics

Initial generator behaviour:
Do not invent unrelated implementations.
Emit wrappers around the library modules.
Add clean help text.
Add argument validation.
Add basic Python tests if practical.

Legacy TCL:
Move ces_mem_from_file_gen.tcl to tools/legacy/.
Document that it is legacy input-file memory generation logic.
Do not delete it.
Add a note about possible future migration into lm_memgen.

Quality rules:
- Preserve behaviour of legacy modules unless a change is explicitly documented.
- Do not make large silent rewrites.
- Prefer small, reviewable commits/changes.
- Add clear TODOs only where necessary.
- Every public entity must have a file header.
- Every new testbench must be self-checking.
- The repository must be usable immediately after cloning.

Acceptance criteria:
- The repository structure exists.
- Apache-2.0 LICENSE exists.
- Legacy VHDL files are imported/refactored or compatibility issues are documented.
- README.md and docs exist.
- sim/scripts/run_ghdl.sh exists.
- GitHub Actions CI exists.
- At least one RAM testbench and one FIFO testbench run with GHDL.
- tools/lm_memgen has a usable CLI skeleton.
- The project follows docs/LM_VHDL_coding_standard.md.