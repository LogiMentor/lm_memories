-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : tb_lm_mem_ram
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  VUnit testbench for the RAM entities. All checking is done by
--  lm_mem_tb_harness; this file only connects it to the VUnit runner.
--  Clock periods are integer picoseconds so that every simulator can set
--  them from the command line.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

library vunit_lib;
context vunit_lib.vunit_context;

entity tb_lm_mem_ram is
  generic(
    runner_cfg         : string;
    g_dut              : string   := "lm_mem_ram_sdp";
    g_a_data_w         : positive := 32;
    g_a_depth          : positive := 1024;
    g_b_data_w         : positive := 32;
    g_a_latency        : positive := 1;
    g_b_latency        : positive := 1;
    g_a_period_ps      : positive := 10000;
    g_b_period_ps      : positive := 10000;
    g_same_clock       : boolean  := true;
    g_a_writes         : boolean  := true;
    g_a_reads          : boolean  := false;
    g_b_writes         : boolean  := false;
    g_b_reads          : boolean  := true;
    g_cross_rdw_valid  : boolean  := false;
    g_n_cycles         : positive := 2000;
    g_hot_addrs        : positive := 8;
    g_seed             : positive := 1;
    g_max_excluded_pct : natural  := 25;
    g_min_checked      : natural  := 1000;
    g_sim_info         : string   := "unknown simulator"
  );
end entity tb_lm_mem_ram;

architecture a_tb of tb_lm_mem_ram is
  signal s_done   : boolean;
  signal s_errors : natural;
begin
  inst_harness : entity work.lm_mem_tb_harness
    generic map(
      g_dut              => g_dut,
      g_a_data_w         => g_a_data_w,
      g_a_depth          => g_a_depth,
      g_b_data_w         => g_b_data_w,
      g_a_latency        => g_a_latency,
      g_b_latency        => g_b_latency,
      g_a_period         => g_a_period_ps * 1 ps,
      g_b_period         => g_b_period_ps * 1 ps,
      g_same_clock       => g_same_clock,
      g_a_writes         => g_a_writes,
      g_a_reads          => g_a_reads,
      g_b_writes         => g_b_writes,
      g_b_reads          => g_b_reads,
      g_cross_rdw_valid  => g_cross_rdw_valid,
      g_n_cycles         => g_n_cycles,
      g_hot_addrs        => g_hot_addrs,
      g_seed             => g_seed,
      g_max_excluded_pct => g_max_excluded_pct,
      g_min_checked      => g_min_checked,
      g_sim_info         => g_sim_info
    )
    port map(
      done_o   => s_done,
      errors_o => s_errors
    );

  proc_main : process
  begin
    test_runner_setup(runner, runner_cfg);
    while test_suite loop
      if run("random") then
        wait until s_done;
        check_equal(s_errors, 0, "harness errors (see the log above)");
      end if;
    end loop;
    test_runner_cleanup(runner);
  end process proc_main;

  test_runner_watchdog(runner, 1 sec);
end architecture a_tb;
