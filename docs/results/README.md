<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor Srl -->

# Synthesis results

This directory holds the results of the vendor synthesis checks
(`tools/vendor_synth`). The JSON files are the `summary_public.json` that the
tool writes; they contain no local paths. The README status table is
generated from these files.

## `vivado_2023.1_amd.json`

- Tool: Vivado v2023.1 (64-bit), out-of-context synthesis.
- Parts: `xc7a35tfgg484-2` (7-series) and `xcku3p-ffva676-2-e` (Kintex
  UltraScale+).
- RTL: commit `3823cef`. The campaign was repeated in phase 3 after the
  static checks (`9e71869`) and the two-writer RAMs (`072b7c8`); the phase 2
  run was on `2406a71`.
- Cases: `tools/vendor_synth/cases.toml`. Expected results:
  `tools/vendor_synth/expected/amd.toml`.
- Command: `python -m tools.vendor_synth --config tools/vendor_synth/vivado_check.toml --keep-going`,
  in one work directory; the cases that ended in `tool-error` were run again
  in the same directory with `--case`, and the whole directory was then
  checked with `--evaluate-only` against the final expected results. That
  evaluation produced this file.
- Result: 55 of 55 pass (26 cases on `xc7a35tfgg484-2`, 29 on
  `xcku3p-ffva676-2-e`): the 34 single-writer and FIFO cases of phase 2,
  unchanged after the static checks, 18 two-writer cases, and 3 cases of
  36 x 4096 on one clock (URAM check, below). There are 0 CRITICAL WARNINGs
  in every case, and no RAM is built from flip-flops.

### Runs that were repeated

On the reference machine, Vivado 2023.1 fails now and then without any
design message. The symptoms are:
- `couldn't read file` for files of its own installation that exist;
- `invalid command name "rt-undefined"` in a script that it generates
  under `.Xil`;
- `synth_design failed` with "1 Errors" but no error message;
- the process stopping before it writes the exit line.

At the time, other sessions on the same machine were running Vivado
without taking the shared lock. The tool reports such a case as `tool-error`,
never as a pass. Seven runs ended this way and were run again by hand; each
case passed the next time:

| Part | Case | Runs |
| --- | --- | --- |
| `xc7a35tfgg484-2` | `crw_cr_32x1024` | 3 tool errors, then pass |
| `xc7a35tfgg484-2` | `r_w_32x1024_lat2` | 1 tool error, then pass |
| `xc7a35tfgg484-2` | `r_w_32x1536` | 1 tool error, then pass |
| `xcku3p-ffva676-2-e` | `r_w_16x64` | 1 tool error, then pass |
| `xcku3p-ffva676-2-e` | `fifo_sync_32x1024` | 1 tool error, then pass |

The automatic retry covers only `couldn't read file` (at most two retries).

In the phase 3 campaign, four runs ended in `tool-error` on
`xc7a35tfgg484-2` and passed when run again: `cr_cw_32x1024` (2 tool
errors), `crw_crw_32to8` (1), `rw_rw_32x1024` (1). No other session was
running Vivado; the errors are the same unreadable installation files and
stops without an error message.

### Expected results set from measurements

Four expected entries were written after the first run, because Vivado
chooses distributed RAM for small memories and the choice depends on the
family:

- 16-word FIFOs: 24 LUTs as memory on 7-series (5 x RAM32M + 2 x
  RAM32X1D) and 20 on UltraScale+ (2 x RAM32M16 + 1 x RAM32M).
- `r_w_16x64` on UltraScale+: 20 LUTs as memory (2 x RAM64M8 + 1 x RAM64M),
  plus 16 output registers. On 7-series the same case uses one RAMB18.

In each case the cells were checked to be RAM primitives before the values
were written.

The two-writer expected results (phase 3) were also written after the first
measurement: every case uses only RAMB36E1/RAMB18E1 (7-series) or
RAMB36E2/RAMB18E2 (UltraScale+) cells, with 0 LUTs and 0 registers; latency
2 is absorbed in the block RAM output register. `crw_crw_16x64` uses one
RAMB18 on both families.

## `static_checks_2026-10-18.json`

Static checks (`f_static_check` in `lm_mem_pkg`): the fallback of the
two-writer RAMs and wrong generics must stop synthesis at the right check.

- Tools: Vivado v2023.1 on `xc7a35tfgg484-2` (`fallback_check.toml`, jobs
  without and with `synth_design -assert`, and the AMD two-writer
  architecture); Quartus Prime Lite 23.1std.0 Build 991, Analysis &
  Synthesis on `5CEBA4F23C7` (`quartus_check.toml`).
- Result: 23 of 23 pass.
  - Vivado without `-assert` (7 cases) and the AMD architecture (2): `[Synth
    8-11323] assigned value '1' out of range` at the line of the
    `C_ERROR_<CHECK>` constant.
  - Vivado with `-assert` (7): `[Synth 8-63] RTL assertion: "<message>"`.
  - Quartus (7): `Error (10652): VHDL Assertion Statement ... assertion is
    false - report "<message>"`.
- The cases without `-assert` were checked again with `--evaluate-only`
  after the last change to the checker. The `-assert` job ran a second time
  in a short work directory: the first time, Vivado stopped with `[Common
  17-680] Path length exceeds 260-Byte maximum` before elaboration (a tool
  error, not a result).

## `postsynth_vivado_2023.1_amd.json`

Post-synthesis simulation of the two-writer RAMs
(`python sim/scripts/run_postsynth.py`).

- Synthesis: Vivado v2023.1, out of context, `-flatten_hierarchy full`, then
  `write_vhdl -mode funcsim`, on `xc7a35tfgg484-2` and `xcku3p-ffva676-2-e`.
  The synthesized top is the RTL DUT adapter with the generics of one
  configuration.
- Simulation: the harness and scoreboard of the VUnit tests, with the plain
  top `tb_lm_mem_ram_plain`, on GHDL 6.0.0 (UNISIM analyzed from the Vivado
  2023.1 sources) and xsim 2023.1 (precompiled UNISIM). The RTL runs use the
  simulation architecture with the 'X' check; the netlist runs do not, since
  a netlist does not return 'X' for an undefined cell.
- Result: 11 configurations x 2 parts x 2 simulators, RTL and netlist: 44 of
  44 pass with 0 errors. In every configuration the four runs (RTL and
  netlist on both simulators) compare the same reads, because the stimulus
  and the exclusions do not depend on the DUT.

| Configuration | Reads compared | Excluded | BRAM 7-series / UltraScale+ | RTL and netlist, GHDL and xsim |
| --- | --- | --- | --- | --- |
| `crw_crw.1clk.32x1024.lat11` | 3348 | 72 | 1 / 1 | 4 x PASS, 0 errors |
| `crw_crw.2clk.32x1024.lat12` | 3261 | 121 | 1 / 1 | 4 x PASS, 0 errors |
| `crw_crw.2clk.32to8.lat21` | 3207 | 170 | 1 / 1 | 4 x PASS, 0 errors |
| `crw_crw.1clk.8to32.lat22` | 3087 | 252 | 1 / 1 | 4 x PASS, 0 errors |
| `crw_crw.1clk.16x64` | 3322 | 76 | 0.5 / 0.5 | 4 x PASS, 0 errors |
| `rw_rw.32x1024.lat1` | 3341 | 58 | 1 / 1 | 4 x PASS, 0 errors |
| `rw_rw.32x1024.lat2` | 3304 | 94 | 1 / 1 | 4 x PASS, 0 errors |
| `crw_cw.2clk.32x1024.lat1` | 1633 | 58 | 1 / 1 | 4 x PASS, 0 errors |
| `crw_cw.1clk.32x1024.lat2` | 1672 | 30 | 1 / 1 | 4 x PASS, 0 errors |
| `crw_crw.2clk.16x40.lat21` | 2880 | 484 | 0.5 / 0.5 | 4 x PASS, 0 errors |
| `rw_rw.16x48` | 3091 | 291 | 0.5 / 0.5 | 4 x PASS, 0 errors |

- A first campaign found 2 errors in `crw_crw.2clk.32to8.lat21` on
  `xcku3p-ffva676-2-e`, on both simulators: after a write/write collision
  between a 32-bit and an 8-bit write, the RAMB36E2 model corrupts the whole
  32-bit word, while the scoreboard had marked only the overlapping byte as
  undefined. The rule is now "every cell of both writes" (commit `3823cef`),
  and the table above is the run after that change.
- Vivado tool errors stopped 8 of the first 22 syntheses (unreadable
  installation files, `invalid command name "rt-undefined"`, a stop with no
  error message). The script retries those at most twice and records each
  retry (6 on `xcku3p-ffva676-2-e`); the others were synthesized when the
  script ran again. A netlist is never taken from a failed run.

## `postsynth_uram_vivado_2023.1_amd.json`

URAM check on `xcku3p-ffva676-2-e`: 36 x 4096 on one clock, where Vivado
chooses URAM without any attribute.

- Synthesis (`vivado_check.toml`, cases `r_w_36x4096`, `crw_cr_36x4096_cc`,
  `rw_rw_36x4096`; the `crw_cr` case ties both clock ports to one clock in a
  generated top):
  - `lm_mem_ram_r_w`: one URAM288, no LUTs, no registers, 0 CRITICAL
    WARNINGs, with `INFO: [Synth 8-6794] RAM ("lm_mem_ram_r_w/inst_mem/s_mem_reg")
    will be automatically implemented using URAM.` The netlist has a single
    `URAM288` cell with `OREG_A`/`OREG_B` off (latency 1).
  - `lm_mem_ram_crw_cr` (one clock) and `lm_mem_ram_rw_rw`: **not** URAM.
    Vivado recognizes the true dual-port template and builds 4 RAMB36E2 of
    1K x 36 plus an output multiplexer (10 and 12 LUTs), no registers,
    0 CRITICAL WARNINGs.
- Post-synthesis (`run_postsynth.py --part xcku3p-ffva676-2-e --config 36x4096`),
  same harness and scoreboard, GHDL 6.0.0 and xsim 2023.1:

  | Configuration | Netlist | Reads compared | Excluded | RTL and netlist, GHDL and xsim |
  | --- | --- | --- | --- | --- |
  | `r_w.36x4096` | 1 URAM288 | 1724 | 0 | 4 x PASS, 0 errors |
  | `crw_cr.1clk.36x4096` | 4 RAMB36E2 | 3344 | 30 | 4 x PASS, 0 errors |
  | `rw_rw.36x4096` | 4 RAMB36E2 | 3350 | 77 | 4 x PASS, 0 errors |

- No divergence from the documented behaviour: on the URAM netlist, the 32
  reads of an address written by the other port in the same cycle return the
  old data, as specified for a common-clock simple dual-port RAM; latency 1,
  the output hold while the enable is low (283 checks) and never-written
  reads (482) match the RTL. This is a check against the UNISIM `URAM288`
  model, not against hardware.
- The `crw_cr` synthesis ended three times in `tool-error` (`couldn't read
  file` for a script Vivado generates under `.Xil`) and passed when the
  script ran again.
