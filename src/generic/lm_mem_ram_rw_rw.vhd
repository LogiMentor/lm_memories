-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_rw_rw
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Common-clock dual-port RAM wrapper with two read/write ports.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity lm_mem_ram_rw_rw is
  generic(
    g_ram_latency : positive := 1;
    g_ram_data_w  : positive := 32;
    g_ram_depth   : positive := 1024;
    g_init_file   : string   := "";
    g_simulation  : integer  := 1
  );
  port(
    clk_i      : in  std_logic;
    ena_i      : in  std_logic := '1';
    enb_i      : in  std_logic := '1';
    wen_a_i    : in  std_logic;
    wen_b_i    : in  std_logic;
    wr_dat_a_i : in  std_logic_vector(g_ram_data_w - 1 downto 0);
    wr_dat_b_i : in  std_logic_vector(g_ram_data_w - 1 downto 0);
    addr_a_i   : in  std_logic_vector(f_ceil_log2(g_ram_depth) - 1 downto 0);
    addr_b_i   : in  std_logic_vector(f_ceil_log2(g_ram_depth) - 1 downto 0);
    rd_dat_a_o : out std_logic_vector(g_ram_data_w - 1 downto 0);
    rd_dat_b_o : out std_logic_vector(g_ram_data_w - 1 downto 0)
  );
end entity lm_mem_ram_rw_rw;

architecture a_rtl of lm_mem_ram_rw_rw is
begin
  inst_mem : entity work.lm_mem_ram_crw_crw
    generic map(
      g_ram_a_latency => g_ram_latency,
      g_ram_a_data_w  => g_ram_data_w,
      g_ram_a_depth   => g_ram_depth,
      g_ram_b_latency => g_ram_latency,
      g_ram_b_data_w  => g_ram_data_w,
      g_init_file     => g_init_file,
      g_simulation    => g_simulation
    )
    port map(
      clk_a_i     => clk_i,
      clk_b_i     => clk_i,
      ena_i       => ena_i,
      enb_i       => enb_i,
      wen_a_i     => wen_a_i,
      wen_b_i     => wen_b_i,
      data_wr_a_i => wr_dat_a_i,
      data_wr_b_i => wr_dat_b_i,
      addr_a_i    => addr_a_i,
      addr_b_i    => addr_b_i,
      data_rd_a_o => rd_dat_a_o,
      data_rd_b_o => rd_dat_b_o
    );
end architecture a_rtl;
