-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : tb_lm_mem_component_compat
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Self-checking testbench for user designs that instantiate the RAMs through
--  their own component declarations, written for v0.2.0: the components have
--  no read-output reset port (rd_rst_n_i, rst_a_n_i, rst_b_n_i). Each
--  instance is bound to the library entity by default binding; the entity
--  ports that the component lacks must take their default ('1', reset
--  inactive). Each RAM writes a word and reads it back.
--
--  VHDL-93 and VHDL-2008 (no std.env): the clock stops when the test ends.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_lm_mem_component_compat is
end entity tb_lm_mem_component_compat;

architecture a_tb of tb_lm_mem_component_compat is
  constant C_CLK_PERIOD : time     := 10 ns;
  constant C_DATA_W     : positive := 8;
  constant C_ADDR_W     : positive := 4;
  constant C_DEPTH      : positive := 16;

  -- Component declarations as a user design written for v0.2.0 has them.
  component lm_mem_ram_sdp is
    generic(
      g_wr_data_w  : positive := 32;
      g_wr_depth   : positive := 1024;
      g_rd_data_w  : positive := 32;
      g_rd_latency : positive := 1
    );
    port(
      wr_clk_i  : in  std_logic;
      rd_clk_i  : in  std_logic;
      ena_i     : in  std_logic := '1';
      enb_i     : in  std_logic := '1';
      wen_i     : in  std_logic;
      wr_addr_i : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      wr_dat_i  : in  std_logic_vector(C_DATA_W - 1 downto 0);
      rd_addr_i : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      rd_dat_o  : out std_logic_vector(C_DATA_W - 1 downto 0)
    );
  end component lm_mem_ram_sdp;

  component lm_mem_ram_tdp1w is
    generic(
      g_data_w    : positive := 32;
      g_depth     : positive := 1024;
      g_a_latency : positive := 1;
      g_b_latency : positive := 1
    );
    port(
      clk_a_i     : in  std_logic;
      clk_b_i     : in  std_logic;
      ena_i       : in  std_logic := '1';
      enb_i       : in  std_logic := '1';
      wen_a_i     : in  std_logic;
      data_wr_a_i : in  std_logic_vector(C_DATA_W - 1 downto 0);
      addr_a_i    : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      addr_b_i    : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      data_rd_a_o : out std_logic_vector(C_DATA_W - 1 downto 0);
      data_rd_b_o : out std_logic_vector(C_DATA_W - 1 downto 0)
    );
  end component lm_mem_ram_tdp1w;

  component lm_mem_ram_r_w is
    generic(
      g_ram_latency : positive := 1;
      g_ram_data_w  : positive := 32;
      g_ram_depth   : positive := 1024
    );
    port(
      clk_i     : in  std_logic;
      ena_i     : in  std_logic := '1';
      enb_i     : in  std_logic := '1';
      wen_i     : in  std_logic;
      wr_addr_i : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      wr_dat_i  : in  std_logic_vector(C_DATA_W - 1 downto 0);
      rd_addr_i : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      rd_dat_o  : out std_logic_vector(C_DATA_W - 1 downto 0)
    );
  end component lm_mem_ram_r_w;

  component lm_mem_ram_cr_cw is
    generic(
      g_ram_latency : positive := 1;
      g_ram_data_w  : positive := 32;
      g_ram_depth   : positive := 1024
    );
    port(
      wr_clk_i  : in  std_logic;
      rd_clk_i  : in  std_logic;
      ena_i     : in  std_logic := '1';
      enb_i     : in  std_logic := '1';
      wen_i     : in  std_logic;
      wr_addr_i : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      wr_dat_i  : in  std_logic_vector(C_DATA_W - 1 downto 0);
      rd_addr_i : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      rd_dat_o  : out std_logic_vector(C_DATA_W - 1 downto 0)
    );
  end component lm_mem_ram_cr_cw;

  component lm_mem_ram_r_w_ratio is
    generic(
      g_ram_a_data_w : positive := 32;
      g_ram_a_depth  : positive := 1024;
      g_ram_b_data_w : positive := 32
    );
    port(
      clk_i     : in  std_logic;
      ena_i     : in  std_logic := '1';
      enb_i     : in  std_logic := '1';
      wen_i     : in  std_logic;
      wr_addr_i : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      wr_dat_i  : in  std_logic_vector(C_DATA_W - 1 downto 0);
      rd_addr_i : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      rd_dat_o  : out std_logic_vector(C_DATA_W - 1 downto 0)
    );
  end component lm_mem_ram_r_w_ratio;

  component lm_mem_ram_cr_cw_ratio is
    generic(
      g_ram_a_data_w : positive := 32;
      g_ram_a_depth  : positive := 1024;
      g_ram_b_data_w : positive := 32
    );
    port(
      wr_clk_i  : in  std_logic;
      rd_clk_i  : in  std_logic;
      ena_i     : in  std_logic := '1';
      enb_i     : in  std_logic := '1';
      wen_i     : in  std_logic;
      wr_addr_i : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      wr_dat_i  : in  std_logic_vector(C_DATA_W - 1 downto 0);
      rd_addr_i : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      rd_dat_o  : out std_logic_vector(C_DATA_W - 1 downto 0)
    );
  end component lm_mem_ram_cr_cw_ratio;

  component lm_mem_ram_crw_cr is
    generic(
      g_ram_latency : positive := 1;
      g_ram_data_w  : positive := 32;
      g_ram_depth   : positive := 1024
    );
    port(
      clk_a_i     : in  std_logic;
      clk_b_i     : in  std_logic;
      ena_i       : in  std_logic := '1';
      enb_i       : in  std_logic := '1';
      wen_a_i     : in  std_logic;
      data_wr_a_i : in  std_logic_vector(C_DATA_W - 1 downto 0);
      addr_a_i    : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      addr_b_i    : in  std_logic_vector(C_ADDR_W - 1 downto 0);
      data_rd_a_o : out std_logic_vector(C_DATA_W - 1 downto 0);
      data_rd_b_o : out std_logic_vector(C_DATA_W - 1 downto 0)
    );
  end component lm_mem_ram_crw_cr;

  type t_outputs is array (0 to 8) of std_logic_vector(C_DATA_W - 1 downto 0);

  signal s_clk  : std_logic := '0';
  signal s_done : boolean   := false;
  signal s_wen  : std_logic := '0';
  signal s_addr : std_logic_vector(C_ADDR_W - 1 downto 0) := (others => '0');
  signal s_din  : std_logic_vector(C_DATA_W - 1 downto 0) := (others => '0');
  signal s_out  : t_outputs;
begin
  inst_sdp : lm_mem_ram_sdp
    generic map(g_wr_data_w => C_DATA_W, g_wr_depth => C_DEPTH, g_rd_data_w => C_DATA_W, g_rd_latency => 1)
    port map(wr_clk_i => s_clk, rd_clk_i => s_clk, ena_i => '1', enb_i => '1', wen_i => s_wen,
             wr_addr_i => s_addr, wr_dat_i => s_din, rd_addr_i => s_addr, rd_dat_o => s_out(0));

  inst_tdp1w : lm_mem_ram_tdp1w
    generic map(g_data_w => C_DATA_W, g_depth => C_DEPTH, g_a_latency => 1, g_b_latency => 1)
    port map(clk_a_i => s_clk, clk_b_i => s_clk, ena_i => '1', enb_i => '1', wen_a_i => s_wen,
             data_wr_a_i => s_din, addr_a_i => s_addr, addr_b_i => s_addr,
             data_rd_a_o => s_out(1), data_rd_b_o => s_out(2));

  inst_r_w : lm_mem_ram_r_w
    generic map(g_ram_latency => 1, g_ram_data_w => C_DATA_W, g_ram_depth => C_DEPTH)
    port map(clk_i => s_clk, ena_i => '1', enb_i => '1', wen_i => s_wen,
             wr_addr_i => s_addr, wr_dat_i => s_din, rd_addr_i => s_addr, rd_dat_o => s_out(3));

  inst_cr_cw : lm_mem_ram_cr_cw
    generic map(g_ram_latency => 1, g_ram_data_w => C_DATA_W, g_ram_depth => C_DEPTH)
    port map(wr_clk_i => s_clk, rd_clk_i => s_clk, ena_i => '1', enb_i => '1', wen_i => s_wen,
             wr_addr_i => s_addr, wr_dat_i => s_din, rd_addr_i => s_addr, rd_dat_o => s_out(4));

  inst_r_w_ratio : lm_mem_ram_r_w_ratio
    generic map(g_ram_a_data_w => C_DATA_W, g_ram_a_depth => C_DEPTH, g_ram_b_data_w => C_DATA_W)
    port map(clk_i => s_clk, ena_i => '1', enb_i => '1', wen_i => s_wen,
             wr_addr_i => s_addr, wr_dat_i => s_din, rd_addr_i => s_addr, rd_dat_o => s_out(5));

  inst_cr_cw_ratio : lm_mem_ram_cr_cw_ratio
    generic map(g_ram_a_data_w => C_DATA_W, g_ram_a_depth => C_DEPTH, g_ram_b_data_w => C_DATA_W)
    port map(wr_clk_i => s_clk, rd_clk_i => s_clk, ena_i => '1', enb_i => '1', wen_i => s_wen,
             wr_addr_i => s_addr, wr_dat_i => s_din, rd_addr_i => s_addr, rd_dat_o => s_out(6));

  inst_crw_cr : lm_mem_ram_crw_cr
    generic map(g_ram_latency => 1, g_ram_data_w => C_DATA_W, g_ram_depth => C_DEPTH)
    port map(clk_a_i => s_clk, clk_b_i => s_clk, ena_i => '1', enb_i => '1', wen_a_i => s_wen,
             data_wr_a_i => s_din, addr_a_i => s_addr, addr_b_i => s_addr,
             data_rd_a_o => s_out(7), data_rd_b_o => s_out(8));

  proc_clk : process
  begin
    while not s_done loop
      s_clk <= '0';
      wait for C_CLK_PERIOD / 2;
      s_clk <= '1';
      wait for C_CLK_PERIOD / 2;
    end loop;
    wait;
  end process proc_clk;

  proc_stim : process
  begin
    wait until falling_edge(s_clk);
    s_addr <= std_logic_vector(to_unsigned(5, C_ADDR_W));
    s_din  <= x"A5";
    s_wen  <= '1';
    wait until falling_edge(s_clk);
    s_wen  <= '0';
    -- Read of address 5 at the next rising edge (latency 1).
    wait until falling_edge(s_clk);
    for i in s_out'range loop
      assert s_out(i) = x"A5"
        report "tb_lm_mem_component_compat: RAM " & integer'image(i) & " did not read back the written word"
        severity failure;
    end loop;
    report "tb_lm_mem_component_compat: PASS" severity note;
    s_done <= true;
    wait;
  end process proc_stim;
end architecture a_tb;
