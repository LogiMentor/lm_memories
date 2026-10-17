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
- RTL: commit `2406a71` (`src/` and the manifest are unchanged since then).
- Cases: `tools/vendor_synth/cases.toml`. Expected results:
  `tools/vendor_synth/expected/amd.toml`.
- Command: `python -m tools.vendor_synth --config tools/vendor_synth/vivado_check.toml --keep-going`.
  On the reference machine the cases were run in groups of five with
  `--case`, one group after the other. The latest run of every case was then
  collected in one directory and checked again with `--evaluate-only` against
  the final expected results. That evaluation produced this file.
- Result: 34 of 34 pass. There are 0 CRITICAL WARNINGs in every case, and
  no RAM is built from flip-flops.

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
