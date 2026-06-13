--=============================================================================
-- Module Name : tb_lm_mem_ram_rw_rw
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Self-checking testbench for lm_mem_ram_rw_rw.
--  Covers basic write/read, simultaneous dual-port writes, and read/write use.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use std.env.all;

use work.lm_mem_pkg.all;

entity tb_lm_mem_ram_rw_rw is
end entity tb_lm_mem_ram_rw_rw;

architecture a_tb of tb_lm_mem_ram_rw_rw is
  constant C_CLK_PERIOD : time     := 10 ns;
  constant C_DATA_W     : positive := 8;
  constant C_DEPTH      : positive := 16;

  signal s_clk      : std_logic := '0';
  signal s_ena      : std_logic := '1';
  signal s_enb      : std_logic := '1';
  signal s_wen_a    : std_logic := '0';
  signal s_wen_b    : std_logic := '0';
  signal s_wr_dat_a : std_logic_vector(C_DATA_W - 1 downto 0) := (others => '0');
  signal s_wr_dat_b : std_logic_vector(C_DATA_W - 1 downto 0) := (others => '0');
  signal s_addr_a   : std_logic_vector(f_ceil_log2(C_DEPTH) - 1 downto 0) := (others => '0');
  signal s_addr_b   : std_logic_vector(f_ceil_log2(C_DEPTH) - 1 downto 0) := (others => '0');
  signal s_rd_dat_a : std_logic_vector(C_DATA_W - 1 downto 0);
  signal s_rd_dat_b : std_logic_vector(C_DATA_W - 1 downto 0);
begin
  inst_dut : entity work.lm_mem_ram_rw_rw
    generic map(
      g_ram_latency => 1,
      g_ram_data_w  => C_DATA_W,
      g_ram_depth   => C_DEPTH,
      g_init_file   => "",
      g_simulation  => 1
    )
    port map(
      clk_i      => s_clk,
      ena_i      => s_ena,
      enb_i      => s_enb,
      wen_a_i    => s_wen_a,
      wen_b_i    => s_wen_b,
      wr_dat_a_i => s_wr_dat_a,
      wr_dat_b_i => s_wr_dat_b,
      addr_a_i   => s_addr_a,
      addr_b_i   => s_addr_b,
      rd_dat_a_o => s_rd_dat_a,
      rd_dat_b_o => s_rd_dat_b
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

    s_addr_a   <= std_logic_vector(to_unsigned(3, s_addr_a'length));
    s_addr_b   <= std_logic_vector(to_unsigned(3, s_addr_b'length));
    s_wr_dat_a <= x"a5";
    s_wen_a    <= '1';
    wait until rising_edge(s_clk);
    wait for 1 ns;

    s_wen_a <= '0';
    wait until rising_edge(s_clk);
    wait for 1 ns;

    assert s_rd_dat_a = x"a5"
      report "port A did not read back the written value"
      severity error;
    assert s_rd_dat_b = x"a5"
      report "port B did not read back the written value"
      severity error;

    s_addr_a   <= std_logic_vector(to_unsigned(4, s_addr_a'length));
    s_addr_b   <= std_logic_vector(to_unsigned(5, s_addr_b'length));
    s_wr_dat_a <= x"12";
    s_wr_dat_b <= x"34";
    s_wen_a    <= '1';
    s_wen_b    <= '1';
    wait until rising_edge(s_clk);
    wait for 1 ns;

    s_wen_a <= '0';
    s_wen_b <= '0';
    wait until rising_edge(s_clk);
    wait for 1 ns;

    assert s_rd_dat_a = x"12"
      report "simultaneous port A write/readback failed"
      severity error;
    assert s_rd_dat_b = x"34"
      report "simultaneous port B write/readback failed"
      severity error;

    s_addr_a   <= std_logic_vector(to_unsigned(4, s_addr_a'length));
    s_addr_b   <= std_logic_vector(to_unsigned(6, s_addr_b'length));
    s_wr_dat_b <= x"56";
    s_wen_b    <= '1';
    wait until rising_edge(s_clk);
    wait for 1 ns;

    assert s_rd_dat_a = x"12"
      report "port A read during port B write failed"
      severity error;

    s_wen_b  <= '0';
    s_addr_b <= std_logic_vector(to_unsigned(6, s_addr_b'length));
    wait until rising_edge(s_clk);
    wait for 1 ns;

    assert s_rd_dat_b = x"56"
      report "port B did not read back its write"
      severity error;

    report "tb_lm_mem_ram_rw_rw passed" severity note;
    finish;
  end process proc_stim;
end architecture a_tb;
