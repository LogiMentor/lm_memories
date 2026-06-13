-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : tb_lm_mem_ram_r_w_infer
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Self-checking testbench for lm_mem_ram_r_w_infer.
--  Verifies portable simulation behaviour for the inference implementation.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use std.env.all;

use work.lm_mem_pkg.all;

entity tb_lm_mem_ram_r_w_infer is
end entity tb_lm_mem_ram_r_w_infer;

architecture a_tb of tb_lm_mem_ram_r_w_infer is
  constant C_CLK_PERIOD : time     := 10 ns;
  constant C_DATA_W     : positive := 8;
  constant C_DEPTH      : positive := 16;

  signal s_clk     : std_logic := '0';
  signal s_wen     : std_logic := '0';
  signal s_wr_addr : std_logic_vector(f_ceil_log2(C_DEPTH) - 1 downto 0) := (others => '0');
  signal s_rd_addr : std_logic_vector(f_ceil_log2(C_DEPTH) - 1 downto 0) := (others => '0');
  signal s_wr_dat  : std_logic_vector(C_DATA_W - 1 downto 0) := (others => '0');
  signal s_rd_dat  : std_logic_vector(C_DATA_W - 1 downto 0);
begin
  inst_dut : entity work.lm_mem_ram_r_w_infer
    generic map(
      g_ram_latency => 1,
      g_ram_data_w  => C_DATA_W,
      g_ram_depth   => C_DEPTH,
      g_vendor      => "xilinx",
      g_family      => "ultrascaleplus",
      g_tool        => "vivado",
      g_style       => "block"
    )
    port map(
      clk_i     => s_clk,
      ena_i     => '1',
      enb_i     => '1',
      wen_i     => s_wen,
      wr_addr_i => s_wr_addr,
      wr_dat_i  => s_wr_dat,
      rd_addr_i => s_rd_addr,
      rd_dat_o  => s_rd_dat
    );

  proc_clk : process
  begin
    while true loop
      s_clk <= '0';
      wait for C_CLK_PERIOD / 2;
      s_clk <= '1';
      wait for C_CLK_PERIOD / 2;
    end loop;
  end process proc_clk;

  proc_stim : process
  begin
    wait until rising_edge(s_clk);
    s_wr_addr <= std_logic_vector(to_unsigned(2, s_wr_addr'length));
    s_rd_addr <= std_logic_vector(to_unsigned(2, s_rd_addr'length));
    s_wr_dat  <= x"c3";
    s_wen     <= '1';

    wait until rising_edge(s_clk);
    s_wen <= '0';

    wait until rising_edge(s_clk);
    wait for 1 ns;

    assert s_rd_dat = x"c3"
      report "inference RAM readback mismatch"
      severity error;

    report "tb_lm_mem_ram_r_w_infer passed" severity note;
    finish;
  end process proc_stim;
end architecture a_tb;
