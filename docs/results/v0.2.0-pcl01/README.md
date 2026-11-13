# Results: campaign v0.2.0-pcl01

The full campaign of the v0.2.0 code on PCL01, a shared Linux server
(issue #8), run on 2026-11-13 with `tools/campaign/run_amd.sh`. It is the
first campaign of these tool versions: Vivado 2024.1 and xsim 2024.1,
Quartus Prime Lite 25.1std.0 Build 1129, Questa Altera Starter FPGA
Edition 2025.2, GHDL 6.0.0. `amd_campaign.json` lists the pilot, the exit
code of every step and every archive.

<!-- publish:begin -->
| File | Result | Commit | Tool and version | Device | Archives (SHA-256) |
| --- | --- | --- | --- | --- | --- |
| [`vivado_2024.1_amd.json`](vivado_2024.1_amd.json) | 55 pass of 55 | `2e95d7a`; evaluated on `386e8d8` | vivado 2024.1 | `xc7a35tfgg484-2`, `xcku3p-ffva676-2-e` | `vivado_2024.1_amd_20261113T085838Z.tar.xz` (`9a0365fa2da856d08c2b13ba396ab075a0025e3439a1513999e7e62a984fc189`) |
| [`static_checks_vivado_2024.1.json`](static_checks_vivado_2024.1.json) | 28 pass of 28 | `2e95d7a`; evaluated on `386e8d8` | vivado 2024.1 | `xc7a35tfgg484-2` | `static_checks_vivado_2024.1_20261113T091054Z.tar.xz` (`ff104982f6c473e34428e9b5ba06e6e12a586bc0b1775039031dfb0b15c82ae4`) |
| [`static_checks_quartus_25.1.json`](static_checks_quartus_25.1.json) | 11 pass of 11 | `2e95d7a`; evaluated on `386e8d8` | quartus 25.1std.0 Build 1129 | `5CEBA4F23C7` | `static_checks_quartus_25.1_20261113T091218Z.tar.xz` (`0b2dce2a2d36abca5143cd3a41657b618985c2673cb1365d56b3e6d01309e3e4`) |
| [`postsynth_vivado_2024.1_amd.json`](postsynth_vivado_2024.1_amd.json) | 78 PASS of 78 | `2e95d7a` | GHDL 6.0.0 (6.0.0.r0.ge589c698c) [Dunoon edition], vivado 2024.1, xsim 2024.1 | `xc7a35tfgg484-2`, `xcku3p-ffva676-2-e`, RTL (no device) | `postsynth_vivado_2024.1_amd_20261113T093320Z.tar.xz` (`5f7ec64cfa76c686ba73609ebe2d8403b293313120d6693574cadd03e852dee1`) |
| [`vunit_ghdl_6.0.0.json`](vunit_ghdl_6.0.0.json) | 54 of 54 pass | `2e95d7a` | ghdl 6.0.0 | - (simulation) | `vunit_ghdl_6.0.0_20261113T093409Z.tar.xz` (`042f5c35ed4d5d0c7ee7e81d3b6f544721e204a8cbb2300e0c890f1bf7b2a09d`) |
| [`vunit_questa_starter_2025.2.json`](vunit_questa_starter_2025.2.json) | 54 of 54 pass | `2e95d7a` | questa starter 2025.2 | - (simulation) | `vunit_questa_starter_2025.2_20261113T093604Z.tar.xz` (`d5104ec3ab7046b91f7a93c83ee3fa89c3de353df4365169c3fc41cde74eccc4`) |
| [`mutation_ghdl_6.0.0.json`](mutation_ghdl_6.0.0.json) | 14 of 14 mutations detected, baseline PASS | `2e95d7a` | ghdl 6.0.0 | - (simulation) | `mutation_ghdl_6.0.0_20261113T093632Z.tar.xz` (`2466a2d08dc7e3d9662e5dc5a25ec3bf430c221d913a935d8c4287e20188338b`) |
<!-- publish:end -->

## The campaign

- **Commit.** The campaign ran on `2e95d7a`. The sources (`src/`), the
  manifest and the test harness are those of v0.2.0: `sources_hash` and
  `manifest_hash` are the same as in the laptop campaign.
- **Pilot.** 11 of 11 checks passed at the first attempt: launcher exit
  codes (Vivado exits with 0 on a completed run and 1 on every expected
  stop), a generic changed in the Vivado script, and the old code against
  the new static checks.
- **Resume.** The expected results of Vivado 2024.1 and Quartus 25.1std
  started as copies of those of 2023.1 and 23.1std (`[meta] basis`), and
  `publish.py` refuses results compared with such a file. The campaign
  found no difference; the values were then recorded as measured on
  `386e8d8` (the files cite the archives of this campaign), and the three
  synthesis steps (`vivado`, `checks_vivado`, `checks_quartus`) were
  evaluated again on the archived logs (`LM_CAMPAIGN_RESUME`,
  `tools/campaign/resume.py`). No tool ran and no status changed. The other
  steps keep their results from `2e95d7a`.
- **Tool-errors.** None. No step needed a rerun, and no synthesis needed a
  retry inside vendor_synth (`retries` is 0 or empty for every case).
- **Commits.** `2e95d7a` and `386e8d8` are commits of the branch of
  issue #8. If that pull request is merged with a squash, they stay
  readable in the pull request on GitHub, not in the history of `main`.

## Comparison with the laptop campaign

Same code, other tool versions (Vivado 2023.1, Quartus 23.1std, Questa
2021.1 on the laptop; [`../README.md`](../README.md)).

- **Synthesis (`vivado_2024.1_amd.json`).** 55 of 55 pass, and every metric
  of every case is the same as with Vivado 2023.1: block RAM tiles,
  RAMB36/RAMB18, LUTs as memory, URAM, registers, LUTs as logic, 0
  CRITICAL WARNINGs. The notes on distributed RAM, two-writer RAMs and URAM
  of the laptop campaign hold for 2024.1 as well.
- **Static checks.** Vivado 2024.1, 28 of 28, and Quartus 25.1std, 11 of
  11: the same errors at the same lines as with 2023.1 and 23.1std. Vivado
  2024.1 without `synth_design -assert` also ignores the assertions and
  stops at the constant (`[Synth 8-11323]`); with it, at the assertion
  (`[Synth 8-63]`). Quartus 25.1std stops with Error 10652 and 10657, exit
  code 3.
- **Post-synthesis (`postsynth_vivado_2024.1_amd.json`).** 78 of 78 pass
  with 0 errors, on GHDL 6.0.0 (UNISIM from the Vivado 2024.1 sources) and
  xsim 2024.1. Reads compared, exclusions, harness collisions, model
  collision messages and the synthesis metrics of every netlist are the
  same as on the laptop, so the tables of the laptop campaign hold
  unchanged, including the low weight of the collision check on
  UltraScale+ with two clocks.
- **VUnit.** 54 of 54 on GHDL 6.0.0 and on Questa Starter 2025.2.
- **Mutation.** 14 of 14 mutations detected on GHDL 6.0.0, baseline PASS.
- **Duration and tool-errors.** On PCL01 the whole campaign took 1 h 23 min
  (pilot 6 min, synthesis 39 min, static checks 14 min, post-synthesis
  22 min, simulations 3 min) with no tool-error and no retry. The laptop
  has no record of its start, so only the steps after the synthesis compare:
  from the end of the synthesis step to the end of the rerun step, 38 min
  on PCL01 against about 1 h 43 min on the laptop (archive times of
  2026-11-04), where 11 runs ended as tool-error and passed when run again
  and 59 retries of transient Vivado failures were needed inside the steps
  (synthesis 36, static checks 11, post-synthesis 12).
