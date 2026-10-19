-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : tb_lm_mem_fifo
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  VUnit testbench for lm_mem_fifo with constrained-random traffic.
--  The scoreboard is a queue of narrow words: a wide write pushes its words
--  lowest bits first, a wide read pops them in the same order. Writes are only
--  issued while full_o is low, so every issued write is accepted; reads are
--  checked whenever valid_o is high. The test also checks that the FIFO ends
--  empty and that every written word was read back, so every configuration
--  checks exactly g_n_writes * write_width / read_width reads.
--  Process order: the writer pushes a word before the rising edge that writes
--  it, and the reader pops only words already flagged by valid_o, so a push
--  and a pop in the same delta cycle never touch the same word.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

library vunit_lib;
context vunit_lib.vunit_context;

use work.lm_mem_pkg.all;
use work.lm_mem_tb_pkg.all;

entity tb_lm_mem_fifo is
  generic(
    runner_cfg     : string;
    g_dual_clock   : boolean  := true;
    g_wr_data_w    : positive := 16;
    g_rd_data_w    : positive := 16;
    g_wr_depth     : positive := 16;
    g_rd_latency   : positive := 1;
    g_wr_period_ps : positive := 10000;
    g_rd_period_ps : positive := 7000;
    g_n_writes     : positive := 400;
    g_seed         : positive := 1;
    g_sim_info     : string   := "unknown simulator"
  );
end entity tb_lm_mem_fifo;

architecture a_tb of tb_lm_mem_fifo is
  constant C_CELL_W   : positive := f_min(g_wr_data_w, g_rd_data_w);
  constant C_WR_WORDS : positive := g_wr_data_w / C_CELL_W;
  constant C_RD_WORDS : positive := g_rd_data_w / C_CELL_W;
  constant C_N_READS  : positive := (g_n_writes * C_WR_WORDS) / C_RD_WORDS;
  constant C_RD_PER   : time     := g_rd_period_ps * 1 ps;
  constant C_WR_PER   : time     := g_wr_period_ps * 1 ps;

  shared variable sv_queue : t_tb_queue;

  signal s_wr_clk   : std_logic := '0';
  signal s_rd_clk   : std_logic := '0';
  signal s_wr_rst_n : std_logic := '0';
  signal s_rd_rst_n : std_logic := '0';
  signal s_din      : std_logic_vector(g_wr_data_w - 1 downto 0) := (others => '0');
  signal s_wen      : std_logic := '0';
  signal s_ren      : std_logic := '0';
  signal s_dout     : std_logic_vector(g_rd_data_w - 1 downto 0);
  signal s_full     : std_logic;
  signal s_afull    : std_logic;
  signal s_empty    : std_logic;
  signal s_aempty   : std_logic;
  signal s_valid    : std_logic;
  signal s_wr_done  : boolean := false;
  signal s_rd_done  : boolean := false;
  signal s_errors   : natural := 0;
  signal s_stop     : boolean := false;
begin
  assert (g_n_writes * C_WR_WORDS) mod C_RD_WORDS = 0
    report "tb_lm_mem_fifo: g_n_writes must fill whole read words"
    severity failure;

  gen_clk_common : if not g_dual_clock generate
    process
    begin
      while not s_stop loop
        s_wr_clk <= '1';
        s_rd_clk <= '1';
        wait for C_WR_PER / 2;
        s_wr_clk <= '0';
        s_rd_clk <= '0';
        wait for C_WR_PER / 2;
      end loop;
      wait;
    end process;
  end generate gen_clk_common;

  gen_clk_split : if g_dual_clock generate
    process
    begin
      while not s_stop loop
        s_wr_clk <= '1';
        wait for C_WR_PER / 2;
        s_wr_clk <= '0';
        wait for C_WR_PER / 2;
      end loop;
      wait;
    end process;

    process
    begin
      while not s_stop loop
        s_rd_clk <= '1';
        wait for C_RD_PER / 2;
        s_rd_clk <= '0';
        wait for C_RD_PER / 2;
      end loop;
      wait;
    end process;
  end generate gen_clk_split;

  inst_dut : entity work.lm_mem_fifo
    generic map(
      g_dual_clock => g_dual_clock,
      g_wr_depth   => g_wr_depth,
      g_wr_data_w  => g_wr_data_w,
      g_rd_data_w  => g_rd_data_w,
      g_rd_latency => g_rd_latency
    )
    port map(
      wr_clk_i       => s_wr_clk,
      rd_clk_i       => s_rd_clk,
      wr_rst_n_i     => s_wr_rst_n,
      rd_rst_n_i     => s_rd_rst_n,
      din_i          => s_din,
      wen_i          => s_wen,
      full_o         => s_full,
      almost_full_o  => s_afull,
      dout_o         => s_dout,
      ren_i          => s_ren,
      empty_o        => s_empty,
      almost_empty_o => s_aempty,
      valid_o        => s_valid
    );

  proc_writer : process
    variable v_s1   : positive := g_seed;
    variable v_s2   : positive := 4421;
    variable v_bit  : std_logic;
    variable v_data : std_logic_vector(g_wr_data_w - 1 downto 0);
    variable v_sent : natural := 0;
  begin
    for i in 1 to 4 loop
      wait until falling_edge(s_wr_clk);
    end loop;
    s_wr_rst_n <= '1';
    while v_sent < g_n_writes loop
      wait until falling_edge(s_wr_clk);
      p_tb_rand_bit(v_s1, v_s2, 0.6, v_bit);
      if (v_bit = '1') and (s_full = '0') then
        p_tb_rand_slv(v_s1, v_s2, v_data);
        s_din <= v_data;
        s_wen <= '1';
        for w in 0 to C_WR_WORDS - 1 loop
          sv_queue.push(v_data((w + 1) * C_CELL_W - 1 downto w * C_CELL_W));
        end loop;
        v_sent := v_sent + 1;
      else
        s_wen <= '0';
      end if;
    end loop;
    wait until falling_edge(s_wr_clk);
    s_wen <= '0';
    s_wr_done <= true;
    wait;
  end process proc_writer;

  proc_reader : process
    variable v_s1   : positive := g_seed + 7;
    variable v_s2   : positive := 99991;
    variable v_bit  : std_logic;
    variable v_exp  : std_logic_vector(g_rd_data_w - 1 downto 0);
    variable v_word : t_tb_data;
    variable v_got  : natural := 0;
    variable v_err  : natural := 0;
  begin
    for i in 1 to 4 loop
      wait until falling_edge(s_rd_clk);
    end loop;
    s_rd_rst_n <= '1';
    while v_got < C_N_READS loop
      wait until falling_edge(s_rd_clk);
      if s_valid = '1' then
        for w in 0 to C_RD_WORDS - 1 loop
          v_word := sv_queue.pop;
          v_exp((w + 1) * C_CELL_W - 1 downto w * C_CELL_W) := v_word(C_CELL_W - 1 downto 0);
        end loop;
        if s_dout /= v_exp then
          v_err := v_err + 1;
          report "tb_lm_mem_fifo: read " & integer'image(v_got) & " got " & to_hstring(s_dout) &
                 " expected " & to_hstring(v_exp)
            severity error;
        end if;
        v_got := v_got + 1;
      end if;
      p_tb_rand_bit(v_s1, v_s2, 0.6, v_bit);
      s_ren <= v_bit;
    end loop;
    s_ren <= '0';
    for i in 1 to 8 loop
      wait until falling_edge(s_rd_clk);
      if s_valid = '1' then
        v_err := v_err + 1;
        report "tb_lm_mem_fifo: valid_o high after the last expected read" severity error;
      end if;
    end loop;
    if s_empty /= '1' then
      v_err := v_err + 1;
      report "tb_lm_mem_fifo: FIFO not empty at the end" severity error;
    end if;
    if sv_queue.size /= 0 then
      v_err := v_err + 1;
      report "tb_lm_mem_fifo: " & integer'image(sv_queue.size) & " words never read" severity error;
    end if;
    report "tb_lm_mem_fifo [" & g_sim_info & "]: words_written=" &
           integer'image(g_n_writes * C_WR_WORDS) & " reads_checked=" & integer'image(v_got) &
           " errors=" & integer'image(v_err)
      severity note;
    s_errors  <= v_err;
    s_rd_done <= true;
    wait;
  end process proc_reader;

  proc_main : process
  begin
    test_runner_setup(runner, runner_cfg);
    while test_suite loop
      if run("random") then
        wait until s_wr_done and s_rd_done;
        check_equal(s_errors, 0, "FIFO scoreboard errors (see the log above)");
      end if;
    end loop;
    s_stop <= true;
    test_runner_cleanup(runner);
  end process proc_main;

  test_runner_watchdog(runner, 1 sec);
end architecture a_tb;
