<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor Srl -->

# Support Matrix

What is verified, per entity and per tool. The numbers behind each entry are
in [`docs/results/`](results/README.md); the plan and the open items are in
[`docs/PLAN.md`](PLAN.md).

Status values:

- **simulated**: the VUnit harness (scoreboard, frozen reference model,
  coverage thresholds) passes on GHDL and Questa; the legacy GHDL tests pass.
- **synthesis checked**: `tools/vendor_synth` passes with the expected
  results of `tools/vendor_synth/expected/<vendor>.toml` (memory primitives,
  register and LUT ceilings, 0 CRITICAL WARNINGs, generics read back).
- **netlist checked**: the same harness passes on the synthesized netlist
  (`sim/scripts/run_postsynth.py`, GHDL with UNISIM and xsim).
- **stops**: synthesis stops at the fallback check
  (`no validated two-writer RAM architecture for this tool`), as expected.
- **not checked**: no campaign with this tool yet.
- **not run**: the tool is not available (Lattice Diamond: license expired;
  Microchip Libero: not installed).

## RTL

| Entity | Simulation | AMD Vivado 2023.1 (`xc7a35tfgg484-2`, `xcku3p-ffva676-2-e`) | Intel Quartus 23.1 (Cyclone V) | Lattice | Microchip |
| --- | --- | --- | --- | --- | --- |
| `lm_mem_ram_r_w` | simulated | synthesis checked; 36 x 4096 on UltraScale+ (URAM) netlist checked | not checked (phase 4) | not run | not run |
| `lm_mem_ram_cr_cw` | simulated | synthesis checked | not checked (phase 4) | not run | not run |
| `lm_mem_ram_r_w_ratio` | simulated | synthesis checked | not checked (phase 4) | not run | not run |
| `lm_mem_ram_cr_cw_ratio` | simulated | synthesis checked | not checked (phase 4) | not run | not run |
| `lm_mem_ram_crw_cr` | simulated | synthesis checked; 36 x 4096 on one clock netlist checked | not checked (phase 4) | not run | not run |
| `lm_mem_ram_crw_crw` | simulated | synthesis checked, netlist checked | stops | not run (stops by design) | not run (stops by design) |
| `lm_mem_ram_rw_rw` | simulated | synthesis checked, netlist checked | stops | not run (stops by design) | not run (stops by design) |
| `lm_mem_ram_crw_cw` | simulated | synthesis checked, netlist checked | stops | not run (stops by design) | not run (stops by design) |
| `lm_mem_fifo_sync`, `lm_mem_fifo_async` (`lm_mem_fifo`) | simulated | synthesis checked | not checked (phase 4) | not run | not run |
| `lm_mem_ram_r_w_infer` | simulated (legacy test) | wrong generics stop synthesis; no expected results | wrong generics stop | not run | not run |
| `lm_mem_*_ram_r_w_prim` (deprecated: no vendor primitive inside) | simulated (legacy test) | wrong generics stop synthesis; no expected results | wrong generics stop | not run | not run |
| ROM | planned | - | - | - | - |

Wrong generics stop synthesis for every entity (static checks,
`f_static_check`): checked with Vivado with and without `synth_design
-assert`, with Quartus, and with GHDL (`tools/tests/test_static_checks.py`).

## Behaviour that depends on the vendor

- Read of the address written in the same cycle on a common-clock simple
  dual-port RAM (`lm_mem_ram_r_w`, `lm_mem_ram_r_w_ratio`): old data,
  **verified on AMD only** (RTL and netlist, block RAM and URAM). Quartus
  23.1 reports `Warning (276027): Inferred dual-clock RAM node ... The
  read-during-write behavior of a dual-clock RAM is undefined` for
  `lm_mem_ram_r_w` 32 x 1024 on Cyclone V; whether old data holds on Intel is
  checked in phase 4.
- Collisions between the ports of a true dual-port RAM, and accesses beyond
  a depth that is not a power of two, are undefined on every vendor.
- URAM: Vivado chooses it by itself for `lm_mem_ram_r_w` 36 x 4096 on
  UltraScale+. Its behaviour (old data, latency 1, zero initial content) is
  checked on the UNISIM simulation model of `URAM288`, not on hardware.

No entity is checked on hardware.
