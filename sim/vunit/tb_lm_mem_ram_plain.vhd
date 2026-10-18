-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : tb_lm_mem_ram_plain
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Plain top-level for the RAM harness, for simulators without VUnit (xsim)
--  and for the post-synthesis runs. Same generics as tb_lm_mem_ram, without
--  runner_cfg. The last line of the log is
--    tb_lm_mem_ram_plain: RESULT PASS errors=0
--  or a RESULT FAIL line of severity failure.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

entity tb_lm_mem_ram_plain is
  generic(
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
    g_x_on_undef       : boolean  := false;
    g_n_cycles         : positive := 2000;
    g_hot_addrs        : positive := 8;
    g_seed             : positive := 1;
    g_max_excluded_pct : natural  := 25;
    g_min_checked      : natural  := 1000;
    g_sim_info         : string   := "unknown simulator"
  );
end entity tb_lm_mem_ram_plain;

architecture a_tb of tb_lm_mem_ram_plain is
  constant C_TIMEOUT : time := 1 sec;

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
      g_x_on_undef       => g_x_on_undef,
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
    wait until s_done for C_TIMEOUT;
    if not s_done then
      report "tb_lm_mem_ram_plain: RESULT FAIL timeout" severity failure;
    elsif s_errors /= 0 then
      report "tb_lm_mem_ram_plain: RESULT FAIL errors=" & integer'image(s_errors) severity failure;
    else
      report "tb_lm_mem_ram_plain: RESULT PASS errors=0" severity note;
    end if;
    wait;
  end process proc_main;
end architecture a_tb;
