-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_crw_cr
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Dual-clock RAM wrapper with one read/write port and one read-only port.
--  Implemented on lm_mem_ram_tdp1w.
--
--  - rst_a_n_i, rst_b_n_i: optional synchronous resets of the read outputs
--    of port A and port B, active low, each in the clock domain of its port
--    (clk_a_i, clk_b_i). Each acts only while the enable of its port (ena_i,
--    enb_i) is high; with the enable low it has no effect and the output
--    holds its value. With latency 1 a reset clears the output register
--    (block RAM output latch reset, RSTRAM on AMD). With latency 2 it clears
--    both stages (RSTRAM and RSTREG): after the reset the output stays zero
--    until a read made after the reset reaches it. The read of a reset cycle
--    is discarded; the storage is never affected (a port A write in a reset
--    cycle is done). Default '1' (inactive): an instance that leaves them
--    unconnected keeps its behaviour and resources.
--  - Read outputs have no initial value: each is undefined until the first
--    enabled read of its port reaches it (with latency 2, until the second),
--    or until a reset applied with the enable of its port high clears it to
--    zero.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity lm_mem_ram_crw_cr is
  generic(
    g_ram_latency : positive := 1;
    g_ram_data_w  : positive := 32;
    g_ram_depth   : positive := 1024;
    g_init_file   : string   := "";
    g_simulation  : integer  := 1
  );
  port(
    clk_a_i     : in  std_logic;
    clk_b_i     : in  std_logic;
    ena_i       : in  std_logic := '1';
    enb_i       : in  std_logic := '1';
    wen_a_i     : in  std_logic;
    data_wr_a_i : in  std_logic_vector(g_ram_data_w - 1 downto 0);
    addr_a_i    : in  std_logic_vector(f_ceil_log2(g_ram_depth) - 1 downto 0);
    addr_b_i    : in  std_logic_vector(f_ceil_log2(g_ram_depth) - 1 downto 0);
    data_rd_a_o : out std_logic_vector(g_ram_data_w - 1 downto 0);
    data_rd_b_o : out std_logic_vector(g_ram_data_w - 1 downto 0);
    rst_a_n_i   : in  std_logic := '1';
    rst_b_n_i   : in  std_logic := '1'
  );
end entity lm_mem_ram_crw_cr;

architecture a_rtl of lm_mem_ram_crw_cr is
begin
  inst_mem : entity work.lm_mem_ram_tdp1w
    generic map(
      g_data_w     => g_ram_data_w,
      g_depth      => g_ram_depth,
      g_a_latency  => g_ram_latency,
      g_b_latency  => g_ram_latency,
      g_init_file  => g_init_file,
      g_simulation => g_simulation
    )
    port map(
      clk_a_i     => clk_a_i,
      clk_b_i     => clk_b_i,
      ena_i       => ena_i,
      enb_i       => enb_i,
      wen_a_i     => wen_a_i,
      data_wr_a_i => data_wr_a_i,
      addr_a_i    => addr_a_i,
      addr_b_i    => addr_b_i,
      data_rd_a_o => data_rd_a_o,
      data_rd_b_o => data_rd_b_o,
      rst_a_n_i   => rst_a_n_i,
      rst_b_n_i   => rst_b_n_i
    );
end architecture a_rtl;
