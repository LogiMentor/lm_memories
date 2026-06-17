-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_intel_ram_r_w_prim
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Intel/Altera primitive-ready RAM wrapper.
--  The current architecture uses the generic RAM as the portable simulation
--  model; vendor primitive binding is isolated behind this entity.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity lm_mem_intel_ram_r_w_prim is
  generic(
    g_ram_latency : positive := 1;
    g_ram_data_w  : positive := 32;
    g_ram_depth   : positive := 1024;
    g_family      : string   := "arria";
    g_primitive   : string   := "m20k"
  );
  port(
    clk_i     : in  std_logic;
    ena_i     : in  std_logic := '1';
    enb_i     : in  std_logic := '1';
    wen_i     : in  std_logic;
    wr_addr_i : in  std_logic_vector(f_ceil_log2(g_ram_depth) - 1 downto 0);
    wr_dat_i  : in  std_logic_vector(g_ram_data_w - 1 downto 0);
    rd_addr_i : in  std_logic_vector(f_ceil_log2(g_ram_depth) - 1 downto 0);
    rd_dat_o  : out std_logic_vector(g_ram_data_w - 1 downto 0)
  );
end entity lm_mem_intel_ram_r_w_prim;

architecture a_rtl of lm_mem_intel_ram_r_w_prim is
begin
  assert (g_family'length > 0) and (g_primitive'length > 0)
    report "lm_mem_intel_ram_r_w_prim: family and primitive must be set"
    severity failure;

  inst_mem : entity work.lm_mem_ram_r_w
    generic map(
      g_ram_latency => g_ram_latency,
      g_ram_data_w  => g_ram_data_w,
      g_ram_depth   => g_ram_depth,
      g_init_file   => "",
      g_simulation  => 1
    )
    port map(
      clk_i     => clk_i,
      ena_i     => ena_i,
      enb_i     => enb_i,
      wen_i     => wen_i,
      wr_addr_i => wr_addr_i,
      wr_dat_i  => wr_dat_i,
      rd_addr_i => rd_addr_i,
      rd_dat_o  => rd_dat_o
    );
end architecture a_rtl;
