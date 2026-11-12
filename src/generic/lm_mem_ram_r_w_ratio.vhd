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
--  - Read-output reset (issue #7): g_ram_b_rst_en enables it, rd_rst_n_i
--    drives it.
--    - g_ram_b_rst_en = false (default): the read port is the one of v0.2.0, with
--      no reset logic, so tools infer the same memory with the same
--      resources; rd_rst_n_i is ignored, and a simulation warning reports it
--      when it is low.
--    - g_ram_b_rst_en = true: rd_rst_n_i is a synchronous reset of the read
--      output, active low, in the clk_i domain. It acts only while enb_i is
--      high; with enb_i low it has no effect and the output holds its value.
--      With latency 1 it clears the output register (block RAM output latch
--      reset, RSTRAM on AMD). With latency 2 it clears both stages (RSTRAM
--      and RSTREG): after the reset the output stays zero until a read made
--      after the reset reaches it. The read of a reset cycle is discarded;
--      the storage and the write port are never affected. Vivado 2023.1
--      builds it with one LUT ("enable AND NOT reset") on the reset pins of
--      the memory.
--  - The read output has no initial value: it is undefined until the first
--    enabled read reaches it (with latency 2, until the second), or, with
--    the reset enabled, until a reset applied with enb_i high clears it to
--    zero.
--  - The read-output reset is not supported with a read port wider than the
--    write port: g_ram_b_rst_en = true then stops elaboration (static check of
--    lm_mem_ram_sdp).
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
    g_simulation    : integer  := 1;
    g_ram_b_rst_en  : boolean  := false
  );
  port(
    clk_i      : in  std_logic;
    ena_i      : in  std_logic := '1';
    enb_i      : in  std_logic := '1';
    wen_i      : in  std_logic;
    wr_addr_i  : in  std_logic_vector(f_ceil_log2(g_ram_a_depth) - 1 downto 0);
    wr_dat_i   : in  std_logic_vector(g_ram_a_data_w - 1 downto 0);
    rd_addr_i  : in  std_logic_vector(f_ceil_log2((g_ram_a_depth * g_ram_a_data_w) / g_ram_b_data_w) - 1 downto 0);
    rd_dat_o   : out std_logic_vector(g_ram_b_data_w - 1 downto 0);
    rd_rst_n_i : in  std_logic := '1'
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
      g_simulation => g_simulation,
      g_rd_rst_en  => g_ram_b_rst_en
    )
    port map(
      wr_clk_i   => clk_i,
      rd_clk_i   => clk_i,
      ena_i      => ena_i,
      enb_i      => enb_i,
      wen_i      => wen_i,
      wr_addr_i  => wr_addr_i,
      wr_dat_i   => wr_dat_i,
      rd_addr_i  => rd_addr_i,
      rd_dat_o   => rd_dat_o,
      rd_rst_n_i => rd_rst_n_i
    );
end architecture a_rtl;
