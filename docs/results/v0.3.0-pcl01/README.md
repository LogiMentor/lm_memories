# Results: campaign v0.3.0-pcl01

The full campaign of the v0.3.0 code on PCL01, a shared Linux server, run
on 2026-11-25 with `tools/campaign/run_amd.sh` on `e33c6d8`, the merge of
PR #16 (read-output reset, issue #7) into `main`. Tools: Vivado 2024.1 and
xsim 2024.1, Quartus Prime Lite 25.1std.0 Build 1129, Questa Altera Starter
FPGA Edition 2025.2, GHDL 6.0.0, the same versions as
[`v0.2.0-pcl01`](../v0.2.0-pcl01/README.md). `amd_campaign.json` lists the
pilot, the exit code of every step and every archive.

<!-- publish:begin -->
| File | Result | Commit | Tool and version | Device | Archives (SHA-256) |
| --- | --- | --- | --- | --- | --- |
| [`vivado_2024.1_amd.json`](vivado_2024.1_amd.json) | 92 pass of 92 | `e33c6d8` | vivado 2024.1 | `xc7a35tfgg484-2`, `xcku3p-ffva676-2-e` | `vivado_2024.1_amd_20261125T090023Z.tar.xz` (`aa42a3dd64706f968578c57c9a220ce81f63c2b8f8e9d6cc2ecb60f963eaa77d`) |
| [`static_checks_vivado_2024.1.json`](static_checks_vivado_2024.1.json) | 30 pass of 30 | `e33c6d8` | vivado 2024.1 | `xc7a35tfgg484-2` | `static_checks_vivado_2024.1_20261125T091455Z.tar.xz` (`d6e11830060b1af079e157b541088c0a11efc3738112c65ad7a00f4a93ff694b`) |
| [`static_checks_quartus_25.1.json`](static_checks_quartus_25.1.json) | 12 pass of 12 | `e33c6d8` | quartus 25.1std.0 Build 1129 | `5CEBA4F23C7` | `static_checks_quartus_25.1_20261125T091634Z.tar.xz` (`e95d91531a14f3ba0e0d55f3ab82535b21b58b75ec3507b3796787d8d989791c`) |
| [`postsynth_vivado_2024.1_amd.json`](postsynth_vivado_2024.1_amd.json) | 124 PASS of 124 | `e33c6d8` | GHDL 6.0.0 (6.0.0.r0.ge589c698c) [Dunoon edition], vivado 2024.1, xsim 2024.1 | `xc7a35tfgg484-2`, `xcku3p-ffva676-2-e`, RTL (no device) | `postsynth_vivado_2024.1_amd_20261125T095240Z.tar.xz` (`3e5231b10fbeafda4b49d9f8d75ab17103b554737aa4df62483362373494365d`) |
| [`vunit_ghdl_6.0.0.json`](vunit_ghdl_6.0.0.json) | 74 of 74 pass | `e33c6d8` | ghdl 6.0.0 | - (simulation) | `vunit_ghdl_6.0.0_20261125T095342Z.tar.xz` (`0411656eac1e395b600010c2a98064e3493d2b90fc99832cd7ed9a8ea3b28752`) |
| [`vunit_questa_starter_2025.2.json`](vunit_questa_starter_2025.2.json) | 74 of 74 pass | `e33c6d8` | questa starter 2025.2 | - (simulation) | `vunit_questa_starter_2025.2_20261125T095640Z.tar.xz` (`dafc8c94c7d247cd16993078befa8b6becdd66b2929f46176d169bcebb8a3af5`) |
| [`mutation_ghdl_6.0.0.json`](mutation_ghdl_6.0.0.json) | 20 of 20 mutations detected, baseline PASS | `e33c6d8` | ghdl 6.0.0 | - (simulation) | `mutation_ghdl_6.0.0_20261125T095728Z.tar.xz` (`0c3b8fbd4f7870daa94abcd6be348baaebd99b99088595289f5f8e66b0198001`) |
<!-- publish:end -->

## The campaign

- **Commit.** Every step ran on `e33c6d8`, with no resume: the sources,
  the manifest, the harness and the expected results are those of `main`
  after PR #16. Release 0.3.0 is tagged on a later commit of `main`, which
  adds only the command line of the EDA lock (issue #10, PR #11:
  `tools/eda_lock.py`, its tests and its documentation): no change to the
  RTL, the manifest, the harness, the expected results or the checks, so
  these results hold for the release.
- **Pilot.** 11 of 11 checks passed at the first attempt (launcher exit
  codes, a generic changed in the Vivado script, the old code against the
  static checks).
- **Expected results.** Every synthesis result is compared with
  `tools/vendor_synth/expected/amd/2024.1.toml` and
  `tools/vendor_synth/expected/intel/25.1std.toml` as measured files (no
  `[meta] basis`): no result is `pass-provisional`.
- **Tool-errors.** None. No step needed a rerun, and no synthesis needed a
  retry inside vendor_synth.
- **Duration.** 2 h 19 min: pilot 8 min, synthesis 74 min, static checks
  16 min, post-synthesis 37 min, simulations 4 min.

## Comparison with v0.2.0-pcl01

Same machine and tool versions; the code adds the read-output reset of
issue #7, disabled by default.

- **Synthesis (`vivado_2024.1_amd.json`).** 92 of 92 pass, 0 CRITICAL
  WARNINGs. The 55 cases of v0.2.0 give every metric of v0.2.0-pcl01
  unchanged (block RAM tiles, RAMB36/RAMB18, LUTs as memory, URAM,
  registers, LUTs as logic): with the reset generics at their default the
  cores are those of v0.2.0. The 37 new runs are the cases of the reset
  (19 on Kintex UltraScale+, 18 on Artix-7): one LUT per reset port on the
  reset pins of the memory, checked pin by pin (`check_reset_pins`); with
  the port A reset of `lm_mem_ram_crw_cr`, one more LUT on the write enable
  of the block RAM (3 LUTs with both resets on Artix-7, 2 on UltraScale+);
  the two component declarations of v0.2.0 with the results of the entity;
  the read port wider than the write port without the reset, one RAMB36.
- **Static checks.** Vivado 2024.1, 30 of 30, and Quartus 25.1std, 12 of
  12: the cases of v0.2.0 plus `bad_r_w_ratio_8to32_rst` (the reset with a
  read port wider than the write port), which stops at the check of
  `lm_mem_ram_sdp` (`[Synth 8-11323]` without `synth_design -assert`,
  `[Synth 8-63]` with it; Quartus Error 10652 and 10657, exit code 3).
- **Post-synthesis (`postsynth_vivado_2024.1_amd.json`).** 124 of 124 pass
  with 0 errors. The 78 runs of v0.2.0-pcl01 give the same counts. The 46
  new runs are the RTL and netlist simulations of 8 reset configurations
  (`lm_mem_ram_r_w`, `lm_mem_ram_cr_cw`, `lm_mem_ram_crw_cr`, distributed
  RAM and URAM sizes), on GHDL 6.0.0 and xsim 2024.1. Not run on purpose:
  `r_w_ratio.32to8.rst` and `r_w_ratio.32to8.lat2.rst`
  (`POSTSYNTH_EXCLUDED` in `sim/vunit/configs.py`): with a read port
  narrower than the write port, a read during write is undefined on the
  AMD netlist, also without the reset; known defect,
  [issue #15](https://github.com/LogiMentor/lm_memories/issues/15).
- **VUnit.** 74 of 74 on GHDL 6.0.0 and on Questa Starter 2025.2: the 54
  tests of v0.2.0, with the same counters, and the 20 configurations of
  the reset.
- **Mutation.** 20 of 20 detected on GHDL 6.0.0, baseline PASS: the 14 of
  v0.2.0 and the 6 of the reset, one of them the behaviour of v0.2.0
  (reset ignored).
