<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor Srl -->

# Support Matrix

Status values:

- `planned`: public target is identified but not implemented yet.
- `simulated`: covered by portable simulation models and self-checking tests.
- `synthesis-smoke-tested`: checked with a local vendor synthesis campaign.
- `production-tested`: validated in a reviewed production integration.

## RTL Status

| Area | Entity | Status | Test Coverage |
| --- | --- | --- | --- |
| Generic RAM | `lm_mem_ram_crw_crw` | simulated | GHDL CI |
| Generic RAM | `lm_mem_ram_rw_rw` | simulated | GHDL CI |
| Generic RAM | `lm_mem_ram_cr_cw` | simulated | source-order coverage |
| Generic RAM | `lm_mem_ram_cr_cw_ratio` | simulated | source-order coverage |
| Generic RAM | `lm_mem_ram_crw_cr` | simulated | source-order coverage |
| Generic RAM | `lm_mem_ram_crw_cw` | simulated | source-order coverage |
| Generic RAM | `lm_mem_ram_r_w` | simulated | GHDL CI |
| Generic RAM | `lm_mem_ram_r_w_ratio` | simulated | source-order coverage |
| FIFO | `lm_mem_fifo` | simulated | GHDL CI through wrappers |
| FIFO | `lm_mem_fifo_sync` | simulated | GHDL CI |
| FIFO | `lm_mem_fifo_async` | simulated | GHDL CI |
| Inference RAM | `lm_mem_ram_r_w_infer` | simulated | GHDL CI |
| Xilinx primitive wrapper | `lm_mem_xilinx_ram_r_w_prim` | simulated | GHDL CI |
| Intel primitive wrapper | `lm_mem_intel_ram_r_w_prim` | simulated | GHDL CI |
| Lattice primitive wrapper | `lm_mem_lattice_ram_r_w_prim` | simulated | GHDL CI |
| Microchip primitive wrapper | `lm_mem_microchip_ram_r_w_prim` | simulated | GHDL CI |
| ROM | Generic ROM modules | planned | not started |

## Vendor Status

| Vendor | Tools | Families | Generic | Inference | Primitives |
| --- | --- | --- | --- | --- | --- |
| AMD/Xilinx | ISE, Vivado | Spartan-6, 7-Series, UltraScale, UltraScale+ | simulated | simulated | simulated |
| Intel/Altera | Quartus | Cyclone, Arria, Stratix | simulated | simulated | simulated |
| Lattice | Diamond, Radiant, Yosys/nextpnr | iCE40, ECP5, Nexus | simulated | simulated | simulated |
| Microchip/Microsemi | Libero | IGLOO, SmartFusion, RTG4, PolarFire | simulated | simulated | simulated |
| Generic | GHDL, Yosys where applicable | Portable behavioural targets | simulated | simulated | simulated |

## Local Synthesis Cases

| Case | Coverage Intent |
| --- | --- |
| `generic_r_w_32x1024` | common-clock one-write one-read RAM |
| `generic_rw_rw_32x1024` | common-clock true dual-port RAM |
| `generic_crw_crw_32x1024` | dual-clock true dual-port RAM |
| `infer_r_w_bram_32x1024` | vendor-aware inferred block RAM candidate |
| `infer_r_w_distributed_16x64` | vendor-aware inferred distributed RAM candidate |
| `primitive_r_w_block_32x1024` | vendor primitive-ready one-write one-read wrapper |

Local vendor synthesis results become meaningful only after reviewing the
generated reports and confirming that the expected memory resources were
inferred or instantiated for the selected family.
