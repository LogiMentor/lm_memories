<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor Srl -->

# Plan: inferable generic RAMs (issue #3)

Status: phase A (diagnosis only). No RTL was changed. This document records
the reproduction, the root cause, the proposed fix and the proposed tests, plus
the decisions that are still open.

## 1. Reproduction

### Setup

- Source: `main` at `8b60c86`, the commit named in the issue. The runs used a
  local commit with the same tree (`git diff` is empty).
- Tool: Vivado v2023.1 (64-bit), build 3865809.
- Part: `xc7a35tfgg484-2`.
- Flow: `synth_design -mode out_of_context -top <entity>`, run on the library
  entity itself, so there is exactly one RAM instance and no wrapper logic.
  Sources are read with `read_vhdl -vhdl2008`.
- Size: 32 x 1024 (library defaults, latency 1) unless stated otherwise.
- Reports: `report_utilization`, `report_ram_utilization`, CRITICAL WARNING
  count from the synthesis log.

Vivado limits every message ID to 100 occurrences by default. The "~100
CRITICAL WARNINGs" in the issue is that limit, not the real count. With the
limit raised (`set_msg_config -id {Synth 8-6859} -limit 1000000`), a 32 x 1024
instance reports 65,536 `[Synth 8-6859]` CRITICAL WARNINGs: two per storage bit.

### Results: library entities

| Entity | `g_simulation` | BRAM tiles | LUT as memory | Slice registers | Slice LUTs | CRITICAL WARNINGs |
| --- | --- | --- | --- | --- | --- | --- |
| `lm_mem_ram_r_w_infer` (reference) | n/a | 1 (RAMB36E1, `RAM_SDP`) | 0 | 0 | 0 | 0 |
| `lm_mem_ram_r_w` | 0 | 0 | 0 | 65,568 | 9,845 | 100 shown (limit) |
| `lm_mem_ram_r_w` | 1 | 0 | 0 | 65,568 | 9,845 | 100 shown (limit) |
| `lm_mem_xilinx_ram_r_w_prim` (wraps `lm_mem_ram_r_w`) | 1 (hard-coded) | 0 | 0 | 65,568 | 9,845 | 65,536 (limit raised) |
| `lm_mem_ram_rw_rw` | 0 | 0 | 0 | 65,600 | 19,718 | 65,536 (limit raised) |
| `lm_mem_ram_rw_rw` | 1 | 0 | 0 | 65,600 | 19,718 | 100 shown (limit) |
| `lm_mem_ram_cr_cw` | 0 | 0 | 0 | 65,568 | 9,845 | 100 shown (limit) |
| `lm_mem_ram_cr_cw` | 1 | 0 | 0 | 65,568 | 9,845 | 100 shown (limit) |
| `lm_mem_ram_crw_cr` | 1 | 0 | 0 | 65,600 | 18,581 | 65,536 (limit raised) |
| `lm_mem_ram_crw_cw` | 1 | 0 | 0 | 65,568 | 10,982 | 65,536 (limit raised) |
| `lm_mem_ram_crw_crw` | 1 | 0 | 0 | 65,600 | 19,718 | 100 shown (limit) |
| `lm_mem_ram_r_w_ratio` (32/32) | 1 | 0 | 0 | 65,568 | 9,845 | 100 shown (limit) |
| `lm_mem_ram_r_w_ratio` (32 -> 8) | 1 | 0 | 0 | 65,528 | 16,121 | 108 shown (limit; includes `Synth 8-6858`) |
| `lm_mem_ram_cr_cw_ratio` (32/32) | 1 | 0 | 0 | 65,568 | 9,845 | 65,536 (limit raised) |
| `lm_mem_ram_r_w_infer` 16 x 64 (reference) | n/a | 0.5 (RAMB18E1, `RAM_SDP`) | 0 | 0 | 0 | 0 |
| `lm_mem_ram_r_w` 16 x 64 | 1 | 0 | 0 | 1,040 | 338 | 0 |
| `lm_mem_fifo_sync` 32 x 16 (defaults) | 1 | 0 | 0 | 559 | 165 | 0 |
| `lm_mem_fifo_sync` 32 x 1024 | 1 | 0 | 0 | 65,694 | 10,026 | 100 shown (limit) |
| `lm_mem_fifo_async` 32 x 1024 | 1 | 0 | 0 | 65,755 | 10,041 | 100 shown (limit) |

All runs completed synthesis. "100 shown (limit)" means Vivado's default
per-message-ID limit; the runs with the limit raised show the real count for
a 32 x 1024 instance.

Every generic RAM entity fails to infer BRAM or LUTRAM. The 1024-word cases
build each storage bit as two flip-flops (65,536 + read registers) and raise one
CRITICAL WARNING per flip-flop. The design does not fit the part: 65,568
registers against 41,600 available.

Small memories fail silently. `lm_mem_ram_r_w` at 16 x 64 and the default
`lm_mem_fifo_sync` (32 x 16) put every storage bit in a flip-flop (1,040 and 559
registers) and report no CRITICAL WARNING at all. Every FIFO user is affected,
because `lm_mem_fifo` stores its data in `lm_mem_ram_cr_cw_ratio`.

The "primitive" wrappers (`lm_mem_*_ram_r_w_prim`) instantiate
`lm_mem_ram_r_w` and are affected in the same way.

### Messages

The failing cases report these messages, all pointing at `proc_mem` in
`src/generic/lm_mem_ram_crw_crw.vhd`. The small cases (16 x 64, FIFO 32 x 16)
report only the first one:

- `WARNING: [Synth 8-5787] Register s_mem_reg[N] in module lm_mem_ram_crw_crw
  is clocked by two different clocks in the same process.` (one per word)
- `CRITICAL WARNING: [Synth 8-6859] multi-driven net on pin
  inst_mem/s_mem[N][b] with 1st driver pin 's_mem_reg[N][b]/Q'` and the same
  with `2nd driver pin 's_mem_reg[N][b]__0/Q'` (one per bit and per driver).

GHDL simulation is not affected: `sim/scripts/run_ghdl.py` passes all five
testbenches on the same tree (GHDL 6.0.0, `--std=08`).

## 2. Root cause

### It is the RAM description, not `g_simulation`

`g_simulation` is declared and forwarded by every wrapper, but no architecture
reads it: there is no `if g_simulation ... generate` and no expression that uses
it. Synthesis results are identical with `g_simulation = 0` and `1`
(`lm_mem_ram_r_w`: 65,568 registers, 9,845 LUTs, 0 BRAM in both cases).
Changing the default would not change anything.

### The description

All eight generic RAM entities are thin wrappers around one core,
`lm_mem_ram_crw_crw` (dual-clock, two write ports, mixed width). Wrappers that
have only one write port tie `wen_b_i` to `'0'`. The core describes the memory as:

```vhdl
proc_mem : process(clk_a_i, clk_b_i)
begin
  if rising_edge(clk_a_i) then
    ... p_write_word(s_mem, addr_a_i, ...);   -- write port A
  end if;
  if rising_edge(clk_b_i) then
    ... p_write_word(s_mem, addr_b_i, ...);   -- write port B
  end if;
end process;
```

1. **One process, two clock edges, one signal written under both.** Vivado
   builds a register for each clock edge that writes `s_mem` (`s_mem_reg` and
   `s_mem_reg__0`). Both drive the same net (`Synth 8-5787`, then
   `Synth 8-6859`). A register driven by two clocks cannot be a RAM, so
   inference never starts.
2. **The core is elaborated without the wrapper constants.** Vivado elaborates
   `lm_mem_ram_crw_crw` as a module before it propagates constants across the
   hierarchy, so `wen_b_i => '0'` in a wrapper does not remove the port B write.
   Simple dual-port wrappers (`r_w`, `cr_cw`, `*_ratio`, FIFO) fail for the same
   reason as true dual-port ones.
3. **Two writers cannot be described correctly with a plain `signal`.** Even
   with the structure fixed, every two-writer description that Vivado infers
   breaks the simulation or the language rules. The control experiments below
   show this, with scratch RTL that is not in the repo:

| Control experiment (32 x 1024, scratch RTL) | Vivado result | Verdict |
| --- | --- | --- |
| Single process, two clocks (as today), `s_mem` written only under `clk_a`, port B read-only | 1 RAMB36 (`RAM_TDP`), 0 CRITICAL WARNINGs | Shows the cause: the port B write branch, not the two-clock process |
| Port A read/write on `clk_a`, port B read-only on `clk_b`, one process per clock, `signal` | 1 RAMB36 (`RAM_TDP`), 0 CRITICAL WARNINGs | Usable template for one writer |
| Two writers, single clock, single process, `signal` | No RAM. `Synth 8-4767`: "RAM has multiple writes via different ports in same process. If RAM inferencing intended, write to one port per process." 86,181 LUTs + 32,832 registers | Not inferable |
| Two writers, one process per clock, `signal` | 1 RAMB36 (`RAM_TDP`), 0 CRITICAL WARNINGs | Wrong in simulation: two drivers on a resolved signal. GHDL reads back `000000XX` after writing `000000A5`. Simulation and synthesis do not match. |
| Two writers, one process per clock, plain `shared variable` (UG901 style) | 1 RAMB36 (`RAM_TDP`), 0 CRITICAL WARNINGs | Not VHDL-2008. GHDL `--std=08`: "type of a shared variable must be a protected type" (needs `-frelaxed`) |
| Two writers, one process per clock, `shared variable` of a protected type | The report shows 1 RAMB36 (`RAM_TDP`), but 31 of the 32 `data_rd_a_o` bits are tied to constant 0 (`Synth 8-3917`), plus `Synth 8-3848` "Net dataN ... does not have driver" and `Synth 8-613` "shared variable will be implemented as local to architecture" | Wrong netlist, with no error |

Secondary issues found during the reproduction (not the cause of #3):

- The `lm_mem_ram_r_w_infer` vendor-synth case passes `g_style => "bram"`.
  Vivado ignores that value (`Synth 8-11376`, "'bram' is not a valid value");
  the Vivado value is `"block"`.
- `tools/vendor_synth` marks a case `pass` when the tool exits with 0. A
  run that puts the whole RAM in flip-flops still passes. This is why the
  problem was not caught.
- The helper subprograms (`f_read_word`, `p_write_word`) use bounds checks
  and loops that are not in any vendor template. They are not the cause, but the
  fix replaces them with direct indexing.

## 3. Proposed fix

### Goals and constraints

- Clean VHDL-2008: no `shared variable` of a non-protected type, no `-frelaxed`,
  no vendor library in the generic path.
- Same entity names, generics, ports, port widths and defaults as today, so no
  downstream change is needed.
- Same behaviour as the current model where hardware can provide it (see
  "Behaviour to keep").
- One process per clock domain and one writer per memory signal, as already
  required by `docs/coding_style.md`.

### New internal cores

Replace the single core with two cores. Each core has one entity per file, in
`src/generic/`.

**`lm_mem_ram_sdp`: one write port, one read port.**

- Generics: `g_wr_data_w`, `g_wr_depth`, `g_rd_data_w`, `g_rd_latency`,
  `g_init_file` (`g_simulation` is passed through and unused, see the open
  decisions).
- Ports: `wr_clk_i`, `ena_i`, `wen_i`, `wr_addr_i`, `wr_dat_i`, `rd_clk_i`,
  `enb_i`, `rd_addr_i`, `rd_dat_o`.
- Write process on `wr_clk_i`, read process on `rd_clk_i`. The storage is a
  `signal` written only by the write process. This is the UG901 simple
  dual-port template and works with the same clock on both sides.
- Mixed width: the storage is an array of `min(wr, rd)` width. The wide port
  loops over `RATIO` narrow words inside its own process, as in the UG901
  asymmetric templates. The narrow address is `wide_addr * RATIO + i`, and
  narrow word `i` sits at bits `(i+1)*W-1 downto i*W`, as in today's
  `f_read_word`/`p_write_word`. The Vivado check (section 4) must confirm the
  asymmetric case. If it does not infer, the fallback is wide storage with a
  registered sub-word select on the narrow port.
- Latency 2: a second output register with the same enable as the first. Vivado
  can merge it into the BRAM output register (`DOB_REG`).

**`lm_mem_ram_tdp1w`: port A read/write, port B read-only.**

- Process A on `clk_a_i` reads and writes the storage `signal`. Process B on
  `clk_b_i` only reads it. This is the first control experiment above:
  `RAM_TDP`, 1 RAMB36, 0 CRITICAL WARNINGs.

### Entity mapping

| Entity | Write ports | New implementation |
| --- | --- | --- |
| `lm_mem_ram_r_w` | 1 | `lm_mem_ram_sdp`, `wr_clk_i = rd_clk_i = clk_i` |
| `lm_mem_ram_cr_cw` | 1 | `lm_mem_ram_sdp` |
| `lm_mem_ram_r_w_ratio` | 1 | `lm_mem_ram_sdp`, mixed width, common clock |
| `lm_mem_ram_cr_cw_ratio` | 1 | `lm_mem_ram_sdp`, mixed width (this also fixes `lm_mem_fifo*`) |
| `lm_mem_ram_crw_cr` | 1 (A) | `lm_mem_ram_tdp1w` |
| `lm_mem_ram_rw_rw` | 2 | see "Two write ports" |
| `lm_mem_ram_crw_cw` | 2 | see "Two write ports" |
| `lm_mem_ram_crw_crw` | 2 | see "Two write ports" |

`lm_mem_ram_r_w_infer` keeps its structure; only the Vivado `ram_style` value
is fixed (`"block"`/`"distributed"`).

### Behaviour to keep

These rules describe today's model. The new tests check them against the old
model (section 4).

- Read during write to the same address on the same clock returns the old data
  (read-first). This is the result of signal semantics in the current single
  process. 7-series BRAM supports it.
- `ena_i` gates both the read and the write of port A. `enb_i` gates the read
  (and, where present, the write) of port B. Both output register stages hold
  their value while the enable is low.
- Initial content is all zeros. There is no reset on the storage or the output
  registers.
- `g_init_file /= ""` is still rejected by an assertion.
- Mixed width: lower narrow address = lower bits of the wide word.
- Two writes to the same address in the same cycle: today port B wins (it is
  assigned later in the process). Hardware does not guarantee this, so the
  behaviour becomes "undefined" in the documentation and the tests do not check
  it.

### Two write ports

No strategy that meets all the constraints gives inferred BRAM with two
writers. The options:

| Option | Inference | Clean VHDL-2008 | GHDL | Cost |
| --- | --- | --- | --- | --- |
| A. Declare it not supported for inference. Keep a corrected behavioural model and refuse synthesis by default with an elaboration-time assertion (opt-in generic for designs that want registers). | no | yes | yes | Users must use a vendor wrapper for TDP |
| B. Vendor architecture in `src/primitives/` (e.g. `xpm_memory_tdpram` for AMD) | yes | per vendor | XPM cannot be simulated in GHDL (SystemVerilog models) | One wrapper per vendor, no portable test |
| C. `shared variable` of a protected type | no: Vivado 2023.1 produces a wrong netlist (port A read tied to 0) | yes | yes | Ruled out for Vivado 2023.1 |
| D. Plain `shared variable` (UG901 template) | yes (verified) | no, needs `-frelaxed` | only with `-frelaxed` | Ruled out by the issue |
| E. Live value table / XOR multi-bank built from SDP RAMs | yes | yes | yes | 2-4x BRAM, plus a `depth x 1` table in registers; different collision behaviour |
| F. Two processes writing one `signal` | yes (verified) | legal for resolved types only | gives `X` on every write (verified) | Ruled out: simulation and synthesis do not match |

Recommendation: A now (so no user gets a register file by accident), and B as
a separate follow-up for AMD. C would be the clean answer, but Vivado 2023.1
synthesizes it to a wrong netlist with no error. That makes it unsafe, which is
worse than not supported. E is not recommended for a general library.

### Work breakdown (phase B, after approval)

1. Add `lm_mem_ram_sdp` and `lm_mem_ram_tdp1w`, and move the 1-writer wrappers
   to them. Keep the old core as a test-only reference model under `sim/ref/`.
2. Two-writer entities: apply the chosen option.
3. Fix the `ram_style` value in the vendor-synth case.
4. Add the tests in section 4. Update `docs/support_matrix.md` (status per
   entity, inference result per family) and the README.

## 4. Proposed tests

### Behaviour: GHDL + VUnit (CI)

- Add a VUnit `run.py` (`vunit_hdl` from pip) next to the current runner, and
  run it in the `ghdl` CI job. Keep `run_ghdl.py` until the VUnit suite covers
  the same tests.
- One generic, self-checking testbench per core and a thin one per wrapper, with
  VUnit configurations for:
  - depth: 16, 64, 1024; width: 1, 8, 32;
  - ratio: 1:1, 1:2, 1:4, 2:1, 4:1 (`*_ratio` entities);
  - latency 1 and 2;
  - clocks: same clock; and unrelated periods (e.g. 10 ns / 7 ns) for `c*`
    entities.
- Stimulus: constrained-random writes and reads with random enables, checked
  against a scoreboard. For dual-clock cases the scoreboard is a protected type
  inside the testbench, which is legal VHDL-2008.
- Directed checks for the rules in "Behaviour to keep": read-first on the same
  address, enable gating on both stages, latency, mixed-width bit order, zero
  initial content.
- Regression against the old model: the same random stream drives the new
  entity and the reference copy of today's `lm_mem_ram_crw_crw` in `sim/ref/`.
  Outputs must match cycle by cycle, except for same-cycle write collisions.
- Two-writer entities (if option A): keep the behavioural tests, and add a check
  that the synthesis guard fires when it is not overridden.

### Inference: Vivado script (local, not in CI)

- Extend `tools/vendor_synth` instead of adding a new tool: each built-in case
  gets expectations such as `expect_bram_tiles`, `expect_lutram`,
  `max_registers`, `max_critical_warnings = 0`, and forbidden message IDs
  (`Synth 8-6859`, `Synth 8-5787`, `Synth 8-4767`). A case is `pass` only if
  the reports match. Exit code 0 alone is not enough.
- Add one case per entity on `xc7a35tfgg484-2`, out-of-context: 32 x 1024
  (expect BRAM), 16 x 64 (expect a RAM primitive, BRAM or LUTRAM, never
  flip-flops; the reference template gets one RAMB18 with `ram_style` left at
  `auto`), plus mixed-width, latency 2 and the FIFOs.
- Read the counts from `report_ram_utilization`, `report_utilization` and
  `get_msg_config -severity {CRITICAL WARNING} -count`. Raise the per-ID
  message limit so the counts are real.
- Lessons from this reproduction that the script must handle:
  - Run each case in its own working directory, and retry once on
    "couldn't read file ..." errors. In this campaign Vivado 2023.1 failed at
    random reading its own Tcl helpers or the shared `.Xil` directory. This
    happened in about 1 run in 4, also with one run at a time, and a retry
    always succeeded.
  - Do not pass generics as `name=value` on the command line through
    `vivado.bat`: `cmd.exe` splits on `=`, and Vivado silently binds the generic
    to 0. Generate a top-level wrapper (as `vendor_synth` already does) or
    pass generics in a Tcl file.
  - One case at 32 x 1024 that fails inference needs about 1.5 GB of RAM and a
    few minutes. Run cases one after another by default.
- Optional CI proxy (open decision): Yosys + `ghdl-yosys-plugin`,
  `proc; memory -nomap; stat`. The job asserts that each core produces one
  memory cell and no `depth x width` flip-flop array. This catches the
  "dissolved into registers" class of bug without vendor tools.

## 5. Open decisions

1. **Two-writer entities** (`rw_rw`, `crw_cw`, `crw_crw`): option A, B or E
   (see above; C, D and F are ruled out). The recommendation is A, with B as a
   follow-up.
2. **`g_simulation`**: it has no effect today. Options: keep it as a documented
   no-op for compatibility; give it a meaning (for example, allow the
   register-based two-writer model only when `g_simulation = 1`); or deprecate
   it. A default of 1 in a synthesizable library is misleading in any case.
3. **Same-address write collision on two-writer ports**: document it as
   undefined (proposed), or keep "port B wins" in the model.
4. **Mixed-width fallback**: if Vivado does not infer the asymmetric SDP
   template, accept wide storage with a narrow-port mux, or limit `*_ratio` to
   ratios Vivado supports.
5. **VUnit in CI**: add `vunit_hdl` as a CI dependency and migrate the current
   testbenches, or keep the plain GHDL runner and add only the new tests.
6. **Yosys proxy in CI**: add the OSS CAD Suite to CI for a vendor-neutral
   inference check, or rely only on local vendor runs.
7. **Primitive wrapper names**: `lm_mem_*_ram_r_w_prim` instantiate the generic
   RAM, not vendor primitives. Rename them, or implement real primitives (out of
   scope for #3).

## Appendix: how the reproduction was run

For each case, one Vivado batch run in a scratch directory:

```tcl
foreach f $sources { read_vhdl -vhdl2008 $f }   ;# src/common, src/generic, src/inference, src/primitives/xilinx, src/fifo
set_msg_config -id {Synth 8-6859} -limit 1000000  ;# only for the "limit raised" runs
synth_design -mode out_of_context -top <entity> -part xc7a35tfgg484-2 -generic <name>=<value>
report_utilization     -file util.rpt
report_ram_utilization -file ram.rpt
```

Generics were passed from inside Tcl (see the `cmd.exe` note in section 4).
For every run, the log line `Parameter <name> bound to: <value>` was checked
against the intended value.
