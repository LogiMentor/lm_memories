-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_r_w_infer
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Vendor-aware inference RAM wrapper.
--  Attribute names cover common synthesis tools while preserving simulation
--  portability through standard VHDL behaviour.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.lm_mem_pkg.all;

entity lm_mem_ram_r_w_infer is
  generic(
    g_ram_latency : positive := 1;
    g_ram_data_w  : positive := 32;
    g_ram_depth   : positive := 1024;
    g_vendor      : string   := "generic";
    g_family      : string   := "generic";
    g_tool        : string   := "generic";
    g_style       : string   := "auto"
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
end entity lm_mem_ram_r_w_infer;

architecture a_rtl of lm_mem_ram_r_w_infer is
  type t_mem is array (0 to g_ram_depth - 1) of std_logic_vector(g_ram_data_w - 1 downto 0);

  signal s_mem       : t_mem;
  signal s_rd_dat    : std_logic_vector(g_ram_data_w - 1 downto 0);
  signal s_rd_dat_p  : std_logic_vector(g_ram_data_w - 1 downto 0);

  attribute ram_style    : string;
  attribute ramstyle     : string;
  attribute syn_ramstyle : string;

  attribute ram_style of s_mem    : signal is g_style;
  attribute ramstyle of s_mem     : signal is g_style;
  attribute syn_ramstyle of s_mem : signal is g_style;
begin
  assert (g_ram_latency = 1) or (g_ram_latency = 2)
    report "lm_mem_ram_r_w_infer: latency must be 1 or 2"
    severity failure;

  assert (g_vendor'length > 0) and (g_family'length > 0) and (g_tool'length > 0)
    report "lm_mem_ram_r_w_infer: vendor, family, and tool strings must be set"
    severity failure;

  proc_mem : process(clk_i)
  begin
    if rising_edge(clk_i) then
      if ena_i = '1' then
        if wen_i = '1' then
          s_mem(to_integer(unsigned(wr_addr_i))) <= wr_dat_i;
        end if;
      end if;

      if enb_i = '1' then
        s_rd_dat   <= s_mem(to_integer(unsigned(rd_addr_i)));
        s_rd_dat_p <= s_rd_dat;
      end if;
    end if;
  end process proc_mem;

  gen_latency_1 : if g_ram_latency = 1 generate
    rd_dat_o <= s_rd_dat;
  end generate gen_latency_1;

  gen_latency_2 : if g_ram_latency = 2 generate
    rd_dat_o <= s_rd_dat_p;
  end generate gen_latency_2;
end architecture a_rtl;
