-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_fifo_sync
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Synchronous FIFO wrapper around lm_mem_fifo.
--  Uses the tested FIFO core with g_dual_clock set to false.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

entity lm_mem_fifo_sync is
  generic(
    g_data_w             : positive := 32;
    g_depth              : positive := 16;
    g_rd_latency         : positive := 1;
    g_fwft               : boolean  := false;
    g_ren_ctrl           : boolean  := true;
    g_wen_ctrl           : boolean  := true;
    g_almost_empty_limit : integer  := 1;
    g_almost_full_limit  : integer  := 1;
    g_sanity_check       : boolean  := true;
    g_simulation         : integer  := 1
  );
  port(
    clk_i          : in  std_logic;
    rst_n_i        : in  std_logic;
    din_i          : in  std_logic_vector(g_data_w - 1 downto 0);
    wen_i          : in  std_logic;
    ren_i          : in  std_logic;
    dout_o         : out std_logic_vector(g_data_w - 1 downto 0);
    full_o         : out std_logic;
    almost_full_o  : out std_logic;
    empty_o        : out std_logic;
    almost_empty_o : out std_logic;
    valid_o        : out std_logic
  );
end entity lm_mem_fifo_sync;

architecture a_rtl of lm_mem_fifo_sync is
begin
  inst_fifo : entity work.lm_mem_fifo
    generic map(
      g_dual_clock         => false,
      g_fwft               => g_fwft,
      g_wr_depth           => g_depth,
      g_wr_data_w          => g_data_w,
      g_rd_data_w          => g_data_w,
      g_rd_latency         => g_rd_latency,
      g_ren_ctrl           => g_ren_ctrl,
      g_wen_ctrl           => g_wen_ctrl,
      g_almost_empty_limit => g_almost_empty_limit,
      g_almost_full_limit  => g_almost_full_limit,
      g_sanity_check       => g_sanity_check,
      g_simulation         => g_simulation
    )
    port map(
      wr_clk_i       => clk_i,
      rd_clk_i       => clk_i,
      wr_rst_n_i     => rst_n_i,
      rd_rst_n_i     => rst_n_i,
      din_i          => din_i,
      wen_i          => wen_i,
      full_o         => full_o,
      almost_full_o  => almost_full_o,
      dout_o         => dout_o,
      ren_i          => ren_i,
      empty_o        => empty_o,
      almost_empty_o => almost_empty_o,
      valid_o        => valid_o
    );
end architecture a_rtl;
