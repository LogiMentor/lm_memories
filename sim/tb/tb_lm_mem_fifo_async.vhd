-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : tb_lm_mem_fifo_async
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Self-checking testbench for lm_mem_fifo_async.
--  Covers reset, asynchronous write/read clocks, full, empty, and read order.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use std.env.all;

entity tb_lm_mem_fifo_async is
end entity tb_lm_mem_fifo_async;

architecture a_tb of tb_lm_mem_fifo_async is
  constant C_WR_PERIOD : time     := 8 ns;
  constant C_RD_PERIOD : time     := 11 ns;
  constant C_DATA_W    : positive := 8;
  constant C_DEPTH     : positive := 8;

  signal s_wr_clk       : std_logic := '0';
  signal s_rd_clk       : std_logic := '0';
  signal s_wr_rst_n     : std_logic := '0';
  signal s_rd_rst_n     : std_logic := '0';
  signal s_din          : std_logic_vector(C_DATA_W - 1 downto 0) := (others => '0');
  signal s_wen          : std_logic := '0';
  signal s_ren          : std_logic := '0';
  signal s_dout         : std_logic_vector(C_DATA_W - 1 downto 0);
  signal s_full         : std_logic;
  signal s_almost_full  : std_logic;
  signal s_empty        : std_logic;
  signal s_almost_empty : std_logic;
  signal s_valid        : std_logic;
begin
  inst_dut : entity work.lm_mem_fifo_async
    generic map(
      g_data_w             => C_DATA_W,
      g_depth              => C_DEPTH,
      g_almost_empty_limit => 1,
      g_almost_full_limit  => 1
    )
    port map(
      wr_clk_i       => s_wr_clk,
      wr_rst_n_i     => s_wr_rst_n,
      rd_clk_i       => s_rd_clk,
      rd_rst_n_i     => s_rd_rst_n,
      din_i          => s_din,
      wen_i          => s_wen,
      ren_i          => s_ren,
      dout_o         => s_dout,
      full_o         => s_full,
      almost_full_o  => s_almost_full,
      empty_o        => s_empty,
      almost_empty_o => s_almost_empty,
      valid_o        => s_valid
    );

  proc_wr_clk : process
  begin
    while true loop
      s_wr_clk <= '0';
      wait for C_WR_PERIOD / 2;
      s_wr_clk <= '1';
      wait for C_WR_PERIOD / 2;
    end loop;
  end process proc_wr_clk;

  proc_rd_clk : process
  begin
    while true loop
      s_rd_clk <= '0';
      wait for C_RD_PERIOD / 2;
      s_rd_clk <= '1';
      wait for C_RD_PERIOD / 2;
    end loop;
  end process proc_rd_clk;

  proc_stim : process
  begin
    wait until rising_edge(s_wr_clk);
    wait until rising_edge(s_rd_clk);
    wait until rising_edge(s_wr_clk);
    wait until rising_edge(s_rd_clk);

    assert s_empty = '1'
      report "async FIFO must be empty during reset"
      severity error;
    assert s_full = '0'
      report "async FIFO must not be full during reset"
      severity error;

    s_wr_rst_n <= '1';
    s_rd_rst_n <= '1';

    for i in 0 to C_DEPTH - 1 loop
      wait until rising_edge(s_wr_clk);
      s_din <= std_logic_vector(to_unsigned(64 + i, C_DATA_W));
      s_wen <= '1';
    end loop;

    wait until rising_edge(s_wr_clk);
    s_wen <= '0';

    wait until rising_edge(s_wr_clk);
    wait until rising_edge(s_wr_clk);

    assert s_full = '1'
      report "async FIFO full flag did not assert"
      severity error;

    wait until rising_edge(s_rd_clk);
    wait until rising_edge(s_rd_clk);
    wait until rising_edge(s_rd_clk);

    for i in 0 to C_DEPTH - 1 loop
      s_ren <= '1';
      wait until rising_edge(s_rd_clk);
      wait for 1 ns;

      assert s_valid = '1'
        report "async FIFO valid did not assert during reads"
        severity error;
      assert s_dout = std_logic_vector(to_unsigned(64 + i, C_DATA_W))
        report "async FIFO read order mismatch"
        severity error;
    end loop;

    s_ren <= '0';
    wait until rising_edge(s_rd_clk);
    wait until rising_edge(s_rd_clk);
    wait for 1 ns;

    assert s_empty = '1'
      report "async FIFO empty flag did not assert after draining"
      severity error;

    report "tb_lm_mem_fifo_async passed" severity note;
    finish;
  end process proc_stim;
end architecture a_tb;
