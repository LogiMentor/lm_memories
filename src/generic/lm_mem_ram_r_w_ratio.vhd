-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_r_w_ratio
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Common-clock RAM wrapper with one write port and one read port.
--  The read and write data widths may differ by an integer ratio.
--  Implemented on lm_mem_ram_sdp, common clock.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity lm_mem_ram_r_w_ratio is
  generic(
    g_ram_a_latency : positive := 1;
    g_ram_a_data_w  : positive := 32;
    g_ram_a_depth   : positive := 1024;
    g_ram_b_latency : positive := 1;
    g_ram_b_data_w  : positive := 32;
    g_init_file     : string   := "";
    g_simulation    : integer  := 1
  );
  port(
    clk_i     : in  std_logic;
    ena_i     : in  std_logic := '1';
    enb_i     : in  std_logic := '1';
    wen_i     : in  std_logic;
    wr_addr_i : in  std_logic_vector(f_ceil_log2(g_ram_a_depth) - 1 downto 0);
    wr_dat_i  : in  std_logic_vector(g_ram_a_data_w - 1 downto 0);
    rd_addr_i : in  std_logic_vector(f_ceil_log2((g_ram_a_depth * g_ram_a_data_w) / g_ram_b_data_w) - 1 downto 0);
    rd_dat_o  : out std_logic_vector(g_ram_b_data_w - 1 downto 0)
  );
end entity lm_mem_ram_r_w_ratio;

architecture a_rtl of lm_mem_ram_r_w_ratio is
  -- Static checks (lm_mem_pkg): elaboration stops at the line of the first
  -- failing check, with its message where assertions are evaluated.
  constant C_OK_A_LATENCY  : boolean := (g_ram_a_latency = 1) or (g_ram_a_latency = 2);
  constant C_MSG_A_LATENCY : string  := "lm_mem_ram_r_w_ratio: port A latency must be 1 or 2";
  constant C_ERROR_A_LATENCY_NOT_1_OR_2 : t_static_check := f_static_check(C_OK_A_LATENCY, C_MSG_A_LATENCY);  -- ERROR: g_ram_a_latency must be 1 or 2 (unused port A output, checked for compatibility)
begin
  inst_mem : entity work.lm_mem_ram_sdp
    generic map(
      g_wr_data_w  => g_ram_a_data_w,
      g_wr_depth   => g_ram_a_depth,
      g_rd_data_w  => g_ram_b_data_w,
      g_rd_latency => g_ram_b_latency,
      g_init_file  => g_init_file,
      g_simulation => g_simulation
    )
    port map(
      wr_clk_i  => clk_i,
      rd_clk_i  => clk_i,
      ena_i     => ena_i,
      enb_i     => enb_i,
      wen_i     => wen_i,
      wr_addr_i => wr_addr_i,
      wr_dat_i  => wr_dat_i,
      rd_addr_i => rd_addr_i,
      rd_dat_o  => rd_dat_o
    );
end architecture a_rtl;
