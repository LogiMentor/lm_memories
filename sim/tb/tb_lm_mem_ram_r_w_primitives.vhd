-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : tb_lm_mem_ram_r_w_primitives
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Self-checking testbench for vendor primitive-ready RAM wrappers.
--  Verifies that each wrapper exposes a working portable simulation model.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use std.env.all;

use work.lm_mem_pkg.all;

entity tb_lm_mem_ram_r_w_primitives is
end entity tb_lm_mem_ram_r_w_primitives;

architecture a_tb of tb_lm_mem_ram_r_w_primitives is
  constant C_CLK_PERIOD : time     := 10 ns;
  constant C_DATA_W     : positive := 8;
  constant C_DEPTH      : positive := 16;

  signal s_clk     : std_logic := '0';
  signal s_wen     : std_logic := '0';
  signal s_wr_addr : std_logic_vector(f_ceil_log2(C_DEPTH) - 1 downto 0) := (others => '0');
  signal s_rd_addr : std_logic_vector(f_ceil_log2(C_DEPTH) - 1 downto 0) := (others => '0');
  signal s_wr_dat  : std_logic_vector(C_DATA_W - 1 downto 0) := (others => '0');
  signal s_xilinx  : std_logic_vector(C_DATA_W - 1 downto 0);
  signal s_intel   : std_logic_vector(C_DATA_W - 1 downto 0);
  signal s_lattice : std_logic_vector(C_DATA_W - 1 downto 0);
  signal s_micro   : std_logic_vector(C_DATA_W - 1 downto 0);
begin
  inst_xilinx : entity work.lm_mem_xilinx_ram_r_w_prim
    generic map(
      g_ram_latency => 1,
      g_ram_data_w  => C_DATA_W,
      g_ram_depth   => C_DEPTH,
      g_family      => "7series",
      g_primitive   => "bram"
    )
    port map(
      clk_i     => s_clk,
      ena_i     => '1',
      enb_i     => '1',
      wen_i     => s_wen,
      wr_addr_i => s_wr_addr,
      wr_dat_i  => s_wr_dat,
      rd_addr_i => s_rd_addr,
      rd_dat_o  => s_xilinx
    );

  inst_intel : entity work.lm_mem_intel_ram_r_w_prim
    generic map(
      g_ram_latency => 1,
      g_ram_data_w  => C_DATA_W,
      g_ram_depth   => C_DEPTH,
      g_family      => "cyclone",
      g_primitive   => "m10k"
    )
    port map(
      clk_i     => s_clk,
      ena_i     => '1',
      enb_i     => '1',
      wen_i     => s_wen,
      wr_addr_i => s_wr_addr,
      wr_dat_i  => s_wr_dat,
      rd_addr_i => s_rd_addr,
      rd_dat_o  => s_intel
    );

  inst_lattice : entity work.lm_mem_lattice_ram_r_w_prim
    generic map(
      g_ram_latency => 1,
      g_ram_data_w  => C_DATA_W,
      g_ram_depth   => C_DEPTH,
      g_family      => "nexus",
      g_primitive   => "ebr"
    )
    port map(
      clk_i     => s_clk,
      ena_i     => '1',
      enb_i     => '1',
      wen_i     => s_wen,
      wr_addr_i => s_wr_addr,
      wr_dat_i  => s_wr_dat,
      rd_addr_i => s_rd_addr,
      rd_dat_o  => s_lattice
    );

  inst_microchip : entity work.lm_mem_microchip_ram_r_w_prim
    generic map(
      g_ram_latency => 1,
      g_ram_data_w  => C_DATA_W,
      g_ram_depth   => C_DEPTH,
      g_family      => "polarfire",
      g_primitive   => "lsram"
    )
    port map(
      clk_i     => s_clk,
      ena_i     => '1',
      enb_i     => '1',
      wen_i     => s_wen,
      wr_addr_i => s_wr_addr,
      wr_dat_i  => s_wr_dat,
      rd_addr_i => s_rd_addr,
      rd_dat_o  => s_micro
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
    s_wr_addr <= std_logic_vector(to_unsigned(3, s_wr_addr'length));
    s_rd_addr <= std_logic_vector(to_unsigned(3, s_rd_addr'length));
    s_wr_dat  <= x"5a";
    s_wen     <= '1';

    wait until rising_edge(s_clk);
    s_wen <= '0';

    wait until rising_edge(s_clk);
    wait for 1 ns;

    assert s_xilinx = x"5a"
      report "xilinx primitive wrapper readback mismatch"
      severity error;
    assert s_intel = x"5a"
      report "intel primitive wrapper readback mismatch"
      severity error;
    assert s_lattice = x"5a"
      report "lattice primitive wrapper readback mismatch"
      severity error;
    assert s_micro = x"5a"
      report "microchip primitive wrapper readback mismatch"
      severity error;

    report "tb_lm_mem_ram_r_w_primitives passed" severity note;
    finish;
  end process proc_stim;
end architecture a_tb;
