--=============================================================================
-- Module Name : tb_lm_mem_fifo_sync
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Self-checking testbench for lm_mem_fifo_sync.
--  Covers reset, basic write/read, empty/full flags, and simultaneous access.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use std.env.all;

entity tb_lm_mem_fifo_sync is
end entity tb_lm_mem_fifo_sync;

architecture a_tb of tb_lm_mem_fifo_sync is
  constant C_CLK_PERIOD : time     := 10 ns;
  constant C_DATA_W     : positive := 8;
  constant C_DEPTH      : positive := 4;

  signal s_clk          : std_logic := '0';
  signal s_rst_n        : std_logic := '0';
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
  inst_dut : entity work.lm_mem_fifo_sync
    generic map(
      g_data_w             => C_DATA_W,
      g_depth              => C_DEPTH,
      g_almost_empty_limit => 1,
      g_almost_full_limit  => 1
    )
    port map(
      clk_i          => s_clk,
      rst_n_i        => s_rst_n,
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
    s_rst_n <= '0';
    wait until rising_edge(s_clk);
    wait until rising_edge(s_clk);
    wait for 1 ns;

    assert s_empty = '1'
      report "FIFO must be empty after reset"
      severity error;
    assert s_full = '0'
      report "FIFO must not be full after reset"
      severity error;
    assert s_valid = '0'
      report "FIFO valid must be low after reset"
      severity error;

    s_rst_n <= '1';
    wait until rising_edge(s_clk);

    for i in 0 to C_DEPTH - 1 loop
      s_din <= std_logic_vector(to_unsigned(16 + i, C_DATA_W));
      s_wen <= '1';
      s_ren <= '0';
      wait until rising_edge(s_clk);
      wait for 1 ns;
    end loop;

    s_wen <= '0';
    wait for 1 ns;

    assert s_full = '1'
      report "FIFO full flag did not assert"
      severity error;
    assert s_empty = '0'
      report "FIFO empty flag asserted while full"
      severity error;

    s_din <= x"aa";
    s_wen <= '1';
    s_ren <= '1';
    wait until rising_edge(s_clk);
    wait for 1 ns;

    assert s_valid = '1'
      report "FIFO valid did not assert on read"
      severity error;
    assert s_dout = x"10"
      report "FIFO did not preserve read order for first word"
      severity error;
    assert s_full = '1'
      report "FIFO should remain full on simultaneous read/write from full"
      severity error;

    s_wen <= '0';

    for i in 1 to C_DEPTH - 1 loop
      s_ren <= '1';
      wait until rising_edge(s_clk);
      wait for 1 ns;

      assert s_valid = '1'
        report "FIFO valid dropped during sequential reads"
        severity error;
      assert s_dout = std_logic_vector(to_unsigned(16 + i, C_DATA_W))
        report "FIFO read order failed"
        severity error;
    end loop;

    wait until rising_edge(s_clk);
    wait for 1 ns;

    assert s_valid = '1'
      report "FIFO did not output the simultaneous-write word"
      severity error;
    assert s_dout = x"aa"
      report "FIFO simultaneous-write word mismatch"
      severity error;

    s_ren <= '0';
    wait until rising_edge(s_clk);
    wait for 1 ns;

    assert s_empty = '1'
      report "FIFO empty flag did not assert after draining"
      severity error;

    report "tb_lm_mem_fifo_sync passed" severity note;
    finish;
  end process proc_stim;
end architecture a_tb;
