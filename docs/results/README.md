<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor Srl -->

# Verification results

This directory holds the results of the AMD campaign of issue #3
(`tools/campaign/run_amd.sh`). They are written by

```sh
python -m tools.campaign.publish <campaign root>
```

from the result files of the campaign and of its reruns. Each published
result records:
- the commit it ran on;
- the tool and its version;
- the device;
- the archive of its complete logs, with the SHA-256.

The commit hashes cited as provenance in this directory (in this file and
in the JSON results) are commits of the history of PR #4, which was merged
with a squash: they are not in the history of `main`. They stay readable in
PR #4 on GitHub (`refs/pull/4/head`). Between those commits and the end
of PR #4, the sources (`src/`), the manifest and the test harness did not
change (`tools/campaign/deps.py`, which lists the files each step depends
on). The scripts that run and judge the steps did change with the second
review; the archived logs were evaluated again with them (below).

The archives are kept outside the repository (`tools/eda_archive.py`). The
script checks each archive file against its SHA-256 before it writes, and
it stops if a file is missing or does not match. It also removes the local
paths:
- source paths become relative to the repository;
- `<campaign>` stands for the campaign root;
- `<vivado>` stands for the Vivado installation;
- `Vivado-<pid>-<host>` stands for Vivado's temporary directories.

## The campaign

- **Commit.** The campaign started on `4d1380c` (2026-11-04).
- **Resume.** On 2026-11-05 it resumed on `f971122` (`LM_CAMPAIGN_RESUME="checks_vivado postsynth"`):
  - `f971122` adds two checks:
    - a tool that dies inside `synth_design` is a tool-error;
    - every collision message of the UNISIM models must pair with a collision of the harness.
  - The Vivado static checks were evaluated again with the checks of `f971122`, on the logs of the first run. No tool ran.
  - The post-synthesis simulation ran again in full on `f971122`.
  - The other steps kept their results from `4d1380c`.
  - The sources (`sources_hash`) and the manifest are the same on both commits.
- **Pilot.** The pilot passed: launcher exit codes, a generic changed in the Vivado script, and the old code against the new static checks.
- **Tool-errors.** Each case that ended as tool-error ran again once (`tools/campaign/rerun.py`), and every rerun passed. The rerun is the published result; the first run is kept under `first_run`, with its archive.
- **Index.** `amd_campaign.json` lists:
  - the pilot cases;
  - the exit code of every step;
  - every archive;
  - the archives replaced by the resume (`superseded_archives`: the post-synthesis run on `4d1380c` and its rerun);
  - for the post-synthesis step, what each count means: 74 runs of the step, 2 syntheses run again with 4 netlist runs, 78 consolidated results.
- **Evaluated again after the second review of PR #4.** Every archived log behind these files was evaluated again with the checks of that round, without running a tool:
  - the synthesis and static checks with `--evaluate-only` on the extracted archives, reruns included;
  - the 78 post-synthesis results, their syntheses and collision messages;
  - the VUnit results, including the simulator of every harness line;
  - the mutation runs.
  No result changed. The GHDL steps ran on GHDL 6.0.0: `Simulator: ghdl_GHDL_6.0.0_(tarball)_Dunoon_edition_` is in the console of the VUnit run and of the 15 mutation runs, and the same simulator is on every harness line.

<!-- publish:begin -->
| File | Result | Commit | Tool and version | Device | Archives (SHA-256) |
| --- | --- | --- | --- | --- | --- |
| [`vivado_2023.1_amd.json`](vivado_2023.1_amd.json) | 55 pass of 55 | `4d1380c` | vivado 2023.1 | `xc7a35tfgg484-2`, `xcku3p-ffva676-2-e` | `vivado_2023.1_amd_20261104T151229Z.tar.xz` (`394f93fdedccc175d2c2c69dfa16f9e85d2566fa0766b2184d9d46b723301734`)<br>`vivado_2023.1_amd_rerun_20261104T164405Z.tar.xz` (`98138fce41c713365091537c50e2fbcda9de1ac71b6cd7d347e4ccc53407a01f`)<br>`vivado_2023.1_amd_rerun_20261104T164710Z.tar.xz` (`78550c4ca55397fd974b4765b1b29815f2dd04a18bc938843f3c5a5ac18091ea`)<br>`vivado_2023.1_amd_rerun_20261104T164812Z.tar.xz` (`8d0e13a93eac25e3ac8b5f6c605c3d91c950f980bf193cfc95a6df29406f1796`)<br>`vivado_2023.1_amd_rerun_20261104T165119Z.tar.xz` (`9da5f6bbcd5e6352bebd528f4a43ad53563432c029cd758d275f3a67dda937ca`) |
| [`static_checks_vivado_2023.1.json`](static_checks_vivado_2023.1.json) | 28 pass of 28 | `4d1380c`, `f971122`; evaluated on `f971122` | vivado 2023.1 | `xc7a35tfgg484-2` | `static_checks_vivado_2023.1_20261104T154157Z.tar.xz` (`ec6e938abf7af90ce870147c7758456e620961b4352b2d0c94c794b2e915c062`)<br>`static_checks_vivado_2023.1_rerun_20261104T165143Z.tar.xz` (`3aeae6cc995f2fc5d8451fb3eb0b3fd10d092e9fed5e6c6c15256c0dd43fdf61`)<br>`static_checks_vivado_2023.1_rerun_20261104T165206Z.tar.xz` (`d687a0812c609bd6a086c0ea691be0dd5d6e6b729ea938d90fb0c9f81bf4037d`)<br>`static_checks_vivado_2023.1_rerun_20261104T165227Z.tar.xz` (`ab0b751517b9708de77093ff84535d9272d48278c7f35b26b9a11854489091e8`)<br>`static_checks_vivado_2023.1_rerun_20261104T165250Z.tar.xz` (`ad06721d2eb2c4cb9f3c00f411f6849911e6adc10068a720824f8211775f2db6`)<br>`static_checks_vivado_2023.1_rerun_20261105T080856Z.tar.xz` (`f7d99c3cf93524821626ef77880b7bb3f2f784bb8eaf0388330009f0f22fdb96`) |
| [`static_checks_quartus_23.1.json`](static_checks_quartus_23.1.json) | 11 pass of 11 | `4d1380c` | quartus 23.1std.0 Build 991 | `5CEBA4F23C7` | `static_checks_quartus_23.1_20261104T154807Z.tar.xz` (`8a4fa6915273cb5571a414963e7cb73697cb5e792f86888a8ee0679a5b85851b`) |
| [`postsynth_vivado_2023.1_amd.json`](postsynth_vivado_2023.1_amd.json) | 78 PASS of 78 | `f971122` | GHDL 6.0.0 (tarball) [Dunoon edition], vivado 2023.1, xsim 2023.1 | `xc7a35tfgg484-2`, `xcku3p-ffva676-2-e`, RTL (no device) | `postsynth_vivado_2023.1_amd_20261105T080829Z.tar.xz` (`bdc80e3e6b7a4194a8b5af5a90dfe951cdee96240875485d87d28c5920a5be71`)<br>`postsynth_vivado_2023.1_amd_rerun_20261105T081121Z.tar.xz` (`74e34b814be8073c7ffb45be87ec93e42c7941f3a3eae3a5a39cebfd3231d72f`)<br>`postsynth_vivado_2023.1_amd_rerun_20261105T081259Z.tar.xz` (`209f2a508b071c609c5ec609da1aa5f57e52e1eecc42e933d4fa8f1675936835`) |
| [`vunit_ghdl_6.0.0.json`](vunit_ghdl_6.0.0.json) | 54 of 54 pass | `4d1380c` | ghdl 6.0.0 | - (simulation) | `vunit_ghdl_6.0.0_20261104T162722Z.tar.xz` (`bd1ab863558d03273363c79aeba7d3306117b4f940f3a58ae7566f147037808f`) |
| [`vunit_questa_2021.1.json`](vunit_questa_2021.1.json) | 54 of 54 pass | `4d1380c` | questa 2021.1 | - (simulation) | `vunit_questa_2021.1_20261104T164121Z.tar.xz` (`80e34fb28ee532240b5d800f746a897b4cbc09e44ad3b0e92ef10313e1c235ab`) |
| [`mutation_ghdl_6.0.0.json`](mutation_ghdl_6.0.0.json) | 14 of 14 mutations detected, baseline PASS | `4d1380c` | ghdl 6.0.0 | - (simulation) | `mutation_ghdl_6.0.0_20261104T164222Z.tar.xz` (`832ae667ac090c342e4289d86851c2527f0067e5c71a772bc3c5720be2cec037`) |
<!-- publish:end -->

Steps of the campaign whose exit code is 1 (`amd_campaign.json`,
`exit_codes`) are the steps that had tool-errors. They passed through the
rerun.

## `vivado_2023.1_amd.json`

- **Synthesis.** Vivado v2023.1 (64-bit), out-of-context synthesis:
  - `python -m tools.vendor_synth --config tools/vendor_synth/vivado_check.toml`;
  - cases from `tools/vendor_synth/cases.toml`;
  - expected results from `tools/vendor_synth/expected/amd.toml`.
- **Result: 55 of 55 pass.**
  - 26 cases on `xc7a35tfgg484-2` and 29 on `xcku3p-ffva676-2-e`.
  - Every case has 0 CRITICAL WARNINGs, and no RAM is built from flip-flops.
  - The cases are the single-writer RAMs, the FIFOs, the two-writer RAMs, and three cases of 36 x 4096 on UltraScale+ (URAM check below).
- **Tool-errors.**
  - 4 runs ended as tool-error and passed when run again:
    - `r_w_32x1024` and `r_w_32x1024_lat2` on `xc7a35tfgg484-2`;
    - `r_w_32x1024_lat2` and `crw_crw_32x1024_lat2` on `xcku3p-ffva676-2-e`.
  - In each of the 4, Vivado could not read a file of its own installation or a script it had just generated under `.Xil`, in all 3 attempts.
  - Inside vendor_synth, 26 cases needed retries of such transient failures, 36 retries in all (field `retries`).
- **Expected results set from measurements.** Vivado chooses distributed RAM for small memories, and the choice depends on the family:
  - 16-word FIFOs: 24 LUTs as memory on 7-series and 20 on UltraScale+.
  - `r_w_16x64` on UltraScale+: 20 LUTs as memory plus 16 output registers. On 7-series it uses one RAMB18.
  - Two-writer RAMs: only RAMB36E1/RAMB18E1 (7-series) or RAMB36E2/RAMB18E2 (UltraScale+), with 0 LUTs and 0 registers. Latency 2 is absorbed in the output register of the block RAM.
- **URAM.**
  - `r_w_36x4096` on `xcku3p-ffva676-2-e` uses one URAM288, with no LUTs and no registers. Vivado reports `[Synth 8-6794] ... will be automatically implemented using URAM`, without any attribute.
  - `crw_cr_36x4096_cc` (both clock ports on one clock) and `rw_rw_36x4096` are not built as URAM. Vivado uses the true dual-port template: 4 RAMB36E2 plus an output multiplexer (10 and 12 LUTs).

## `static_checks_vivado_2023.1.json`

Static checks (`f_static_check` in `lm_mem_pkg`): the fallback of the
two-writer RAMs and wrong generics must stop synthesis at the right check.
The tool is Vivado v2023.1 on `xc7a35tfgg484-2` (`tools/vendor_synth/fallback_check.toml`).

- **Result: 28 of 28 pass.**
  - Job without `-assert` (13 cases): `[Synth 8-11323] assigned value '1' out of range` at the line of the `C_ERROR_<CHECK>` constant.
  - Job with `synth_design -assert` (13 cases): `[Synth 8-63] RTL assertion: "<message>"`.
  - The AMD two-writer architecture (2 cases): same message as the job without `-assert`.
  - Every stop is checked in three places: the error of the check in the log, the outcome "failed" in the status file, and exit code 1.
- **Commits.** The step was evaluated again on `f971122` (field `evaluated_on`). The commit of the run is `4d1380c`.
- **Tool-errors.**
  - On `4d1380c`, `bad_fifo_almost_full_0` with `-assert` printed the expected stop. Vivado then died with `called Tcl_Close on channel with refCount > 0` (exit code 3), and the status file stayed at "started". The checks of `f971122` classify this as a tool-error, not as a result.
  - That case ran again on `f971122` and passed.
  - 4 other runs ended as tool-error (unreadable installation files) and passed when run again.

## `static_checks_quartus_23.1.json`

The same checks with Quartus Prime Lite 23.1std.0 Build 991, Analysis &
Synthesis on `5CEBA4F23C7` (`tools/vendor_synth/quartus_check.toml`).

- **Result: 11 of 11 pass.**
  - The three two-writer RAMs stop at the fallback check.
  - The wrong generics stop with `Error (10652): VHDL Assertion Statement ... assertion is false - report "<message>"`.
- **Tool version.** The vendor_synth of this campaign (`4d1380c`) did not record the Quartus version; it does since the second review of PR #4. For these results, `publish.py` reads it from the run logs of the step (`Info: Version ...`). All of them carry the same version, and an evaluation of the archived logs with the current vendor_synth gives the same.

## `postsynth_vivado_2023.1_amd.json`

Post-synthesis simulation of the two-writer RAMs and of the 36 x 4096 RAMs
(`python sim/scripts/run_postsynth.py`).

- **Synthesis.** Vivado v2023.1, out of context, `-flatten_hierarchy full`, then `write_vhdl -mode funcsim`, on `xc7a35tfgg484-2` and `xcku3p-ffva676-2-e`. The synthesized top is the RTL DUT adapter with the generics of one configuration.
- **Simulation.** The harness and scoreboard of the VUnit tests, with the plain top `tb_lm_mem_ram_plain`, on two simulators:
  - GHDL 6.0.0, with UNISIM analyzed from the Vivado 2023.1 sources;
  - xsim 2023.1, with the precompiled UNISIM.
- **What each run checks.**
  - The RTL runs use the simulation architecture with the 'X' check.
  - The netlist runs do not use that check, because a netlist does not return 'X' for an undefined cell.
  - The stimulus and the exclusions do not depend on the DUT. So the RTL and netlist runs on both simulators compare the same reads.
- **Result: 78 of 78 pass with 0 errors.**
  - RTL: 14 configurations x 2 simulators.
  - Netlist: 11 configurations on `xc7a35tfgg484-2` and 14 on `xcku3p-ffva676-2-e`, x 2 simulators.
  - Two syntheses ended as tool-error and passed when run again: `crw_crw.2clk.16x40.lat21` on `xc7a35tfgg484-2` and `rw_rw.16x48` on `xcku3p-ffva676-2-e`. Vivado could not read its own files in 3 attempts.
  - A netlist is never taken from a failed run.

In the table, each count of model messages is the same on GHDL and xsim,
and every message is paired:
- "Reads compared" and "Excluded" are the same in all runs of a configuration.
- "Harness collisions" are write/write and read/write.
- "Model messages" are the collision messages of the UNISIM model on the netlist, written as write/write + read/write.

| Configuration | Reads compared | Excluded | Block RAM 7-series / UltraScale+ | Harness collisions | Model messages 7-series | Model messages UltraScale+ |
| --- | --- | --- | --- | --- | --- | --- |
| `crw_crw.1clk.32x1024.lat11` | 3344 | 72 | 1 RAMB36 / 1 RAMB36 | 12 / 52 | 12 + 0 | 12 + 0 |
| `crw_crw.2clk.32x1024.lat12` | 3256 | 121 | 1 RAMB36 / 1 RAMB36 | 18 / 89 | 9 + 7 | 0 + 0 |
| `crw_crw.2clk.32to8.lat21` | 3203 | 170 | 1 RAMB36 / 1 RAMB36 | 24 / 99 | 4 + 5 | 1 + 0 |
| `crw_crw.1clk.8to32.lat22` | 3083 | 252 | 1 RAMB36 / 1 RAMB36 | 39 / 127 | 39 + 0 | 39 + 0 |
| `crw_crw.1clk.16x64` | 3318 | 76 | 1 RAMB18 / 1 RAMB18 | 13 / 53 | 13 + 0 | 13 + 0 |
| `rw_rw.32x1024.lat1` | 3336 | 58 | 1 RAMB36 / 1 RAMB36 | 9 / 41 | 9 + 0 | 9 + 0 |
| `rw_rw.32x1024.lat2` | 3300 | 94 | 1 RAMB36 / 1 RAMB36 | 15 / 57 | 15 + 0 | 15 + 0 |
| `crw_cw.2clk.32x1024.lat1` | 1631 | 58 | 1 RAMB36 / 1 RAMB36 | 19 / 41 | 5 + 9 | 0 + 1 |
| `crw_cw.1clk.32x1024.lat2` | 1670 | 30 | 1 RAMB36 / 1 RAMB36 | 8 / 27 | 8 + 19 | 8 + 19 |
| `crw_crw.2clk.16x40.lat21` | 2876 | 484 | 1 RAMB18 / 1 RAMB18 | 33 / 121 | 16 + 8 | 0 + 0 |
| `rw_rw.16x48` | 3086 | 291 | 1 RAMB18 / 1 RAMB18 | 14 / 53 | 14 + 0 | 14 + 0 |
| `r_w.36x4096` | 1721 | 0 | - / 1 URAM288 | 0 / 0 | - | 0 + 0 |
| `crw_cr.1clk.36x4096` | 3339 | 30 | - / 4 RAMB36 | 0 / 30 | - | 0 + 0 |
| `rw_rw.36x4096` | 3346 | 77 | - / 4 RAMB36 | 11 / 55 | - | 11 + 0 |

### Collision check on the netlist

The harness provokes collisions on purpose and excludes from the comparison
every read that collides, and every cell left undefined by a write/write
collision. The UNISIM block RAM models report collisions as assertion errors.

- **Pairing rule.** Each such message is accepted only if it pairs with a collision of the harness:
  - the same instant;
  - the same cells: the address of the primitive is converted to the DUT words through the netlist, including the constant address bits, the crossed ports, and the LUT decode of a RAM split in depth;
  - the same read port, for read/write.
- **Everything else is fatal:** a message that does not pair, an unreadable message, and every other UNISIM diagnostic.
- **Harness collisions without a message** are allowed. Coverage of collisions comes from the harness (`g_min_checked`), not from the model.

### UltraScale+ with two clocks

**On UltraScale+ the collision check has less weight with two clocks.** The
RAMB18E2/RAMB36E2 models report a collision only when the edges of the two
ports fall within 401 ps of each other. That is `t_coll_max` of the model,
which stays at 401 ps with the clock periods of the harness. The models also
skip a write/write collision that writes the same data. With the clocks of the harness, the model therefore reports
almost nothing:

| Configuration | Messages on 7-series | Messages on UltraScale+ | Harness collisions |
| --- | --- | --- | --- |
| `crw_crw.2clk.32x1024.lat12` | 16 | 0 | 107 |
| `crw_crw.2clk.32to8.lat21` | 9 | 1 | 123 |
| `crw_cw.2clk.32x1024.lat1` | 14 | 1 | 60 |
| `crw_crw.2clk.16x40.lat21` | 24 | 0 | 154 |

The 7-series models (RAMB18E1/RAMB36E1) use a window of the shorter clock
period, at most 3 ns, and report many more. With one clock, the two families
report the same messages.

What the netlist simulation shows on UltraScale+ with two clocks:
- Without the model messages, it shows that the netlist returns the right data on every read the harness compares.
- It does not show that the model agrees with the harness about which accesses collide.

### URAM on `xcku3p-ffva676-2-e`

- **Behaviour.** On the URAM netlist of `r_w.36x4096`, a read of an address written by the other port in the same cycle returns the old data. This is the behaviour specified for a common-clock simple dual-port RAM.
- **Other checks.** Latency 1, the output hold while the enable is low, and the reads of never-written cells match the RTL.
- **Limit.** This is a check against the UNISIM `URAM288` model, not against hardware.

## `vunit_ghdl_6.0.0.json`, `vunit_questa_2021.1.json`

The whole VUnit suite (`python sim/scripts/run_vunit_campaign.py`), with no
device:
- GHDL 6.0.0: 54 of 54 pass.
- Questa Sim-64 2021.1: 54 of 54 pass.

Each test records its harness counters.

## `mutation_ghdl_6.0.0.json`

Mutation check on GHDL 6.0.0 (`sim/vunit/mutation_check.py`). The baseline
passes, and all 14 mutations of the cores and of the two-writer simulation
model are detected: each makes at least one test fail.
