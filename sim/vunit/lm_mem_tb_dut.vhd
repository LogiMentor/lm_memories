-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_tb_dut
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  DUT adapter for the RAM harness (simulation only).
--  Gives every RAM entity the same two-port interface: port A and port B, each
--  with clock, enable, write enable, address, write data and read data.
--  For simple dual-port entities, port A is the write port and port B the read
--  port; the unused outputs are driven with zeros.
--
--  This file holds the "rtl" architecture, which instantiates the library
--  entity named by g_dut with generics. A post-synthesis flow replaces this
--  file with one whose architecture instantiates a synthesized netlist; the
--  flow manifest selects the file.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity lm_mem_tb_dut is
  generic(
    g_dut       : string   := "lm_mem_ram_sdp";
    g_a_data_w  : positive := 32;
    g_a_depth   : positive := 1024;
    g_b_data_w  : positive := 32;
    g_a_latency : positive := 1;
    g_b_latency : positive := 1
  );
  port(
    clk_a_i  : in  std_logic;
    clk_b_i  : in  std_logic;
    ena_i    : in  std_logic;
    enb_i    : in  std_logic;
    wen_a_i  : in  std_logic;
    wen_b_i  : in  std_logic;
    addr_a_i : in  std_logic_vector(f_ceil_log2(g_a_depth) - 1 downto 0);
    addr_b_i : in  std_logic_vector(f_ceil_log2((g_a_depth * g_a_data_w) / g_b_data_w) - 1 downto 0);
    din_a_i  : in  std_logic_vector(g_a_data_w - 1 downto 0);
    din_b_i  : in  std_logic_vector(g_b_data_w - 1 downto 0);
    dout_a_o : out std_logic_vector(g_a_data_w - 1 downto 0);
    dout_b_o : out std_logic_vector(g_b_data_w - 1 downto 0)
  );
end entity lm_mem_tb_dut;

architecture rtl of lm_mem_tb_dut is
begin
  gen_sdp : if g_dut = "lm_mem_ram_sdp" generate
    dout_a_o <= (others => '0');

    inst_dut : entity work.lm_mem_ram_sdp
      generic map(
        g_wr_data_w  => g_a_data_w,
        g_wr_depth   => g_a_depth,
        g_rd_data_w  => g_b_data_w,
        g_rd_latency => g_b_latency
      )
      port map(
        wr_clk_i  => clk_a_i,
        rd_clk_i  => clk_b_i,
        ena_i     => ena_i,
        enb_i     => enb_i,
        wen_i     => wen_a_i,
        wr_addr_i => addr_a_i,
        wr_dat_i  => din_a_i,
        rd_addr_i => addr_b_i,
        rd_dat_o  => dout_b_o
      );
  end generate gen_sdp;

  gen_tdp1w : if g_dut = "lm_mem_ram_tdp1w" generate
    inst_dut : entity work.lm_mem_ram_tdp1w
      generic map(
        g_data_w    => g_a_data_w,
        g_depth     => g_a_depth,
        g_a_latency => g_a_latency,
        g_b_latency => g_b_latency
      )
      port map(
        clk_a_i     => clk_a_i,
        clk_b_i     => clk_b_i,
        ena_i       => ena_i,
        enb_i       => enb_i,
        wen_a_i     => wen_a_i,
        data_wr_a_i => din_a_i,
        addr_a_i    => addr_a_i,
        addr_b_i    => addr_b_i,
        data_rd_a_o => dout_a_o,
        data_rd_b_o => dout_b_o
      );
  end generate gen_tdp1w;

  gen_r_w : if g_dut = "lm_mem_ram_r_w" generate
    dout_a_o <= (others => '0');

    inst_dut : entity work.lm_mem_ram_r_w
      generic map(
        g_ram_latency => g_b_latency,
        g_ram_data_w  => g_a_data_w,
        g_ram_depth   => g_a_depth
      )
      port map(
        clk_i     => clk_a_i,
        ena_i     => ena_i,
        enb_i     => enb_i,
        wen_i     => wen_a_i,
        wr_addr_i => addr_a_i,
        wr_dat_i  => din_a_i,
        rd_addr_i => addr_b_i,
        rd_dat_o  => dout_b_o
      );
  end generate gen_r_w;

  gen_cr_cw : if g_dut = "lm_mem_ram_cr_cw" generate
    dout_a_o <= (others => '0');

    inst_dut : entity work.lm_mem_ram_cr_cw
      generic map(
        g_ram_latency => g_b_latency,
        g_ram_data_w  => g_a_data_w,
        g_ram_depth   => g_a_depth
      )
      port map(
        wr_clk_i  => clk_a_i,
        rd_clk_i  => clk_b_i,
        ena_i     => ena_i,
        enb_i     => enb_i,
        wen_i     => wen_a_i,
        wr_addr_i => addr_a_i,
        wr_dat_i  => din_a_i,
        rd_addr_i => addr_b_i,
        rd_dat_o  => dout_b_o
      );
  end generate gen_cr_cw;

  gen_r_w_ratio : if g_dut = "lm_mem_ram_r_w_ratio" generate
    dout_a_o <= (others => '0');

    inst_dut : entity work.lm_mem_ram_r_w_ratio
      generic map(
        g_ram_a_latency => g_a_latency,
        g_ram_a_data_w  => g_a_data_w,
        g_ram_a_depth   => g_a_depth,
        g_ram_b_latency => g_b_latency,
        g_ram_b_data_w  => g_b_data_w
      )
      port map(
        clk_i     => clk_a_i,
        ena_i     => ena_i,
        enb_i     => enb_i,
        wen_i     => wen_a_i,
        wr_addr_i => addr_a_i,
        wr_dat_i  => din_a_i,
        rd_addr_i => addr_b_i,
        rd_dat_o  => dout_b_o
      );
  end generate gen_r_w_ratio;

  gen_cr_cw_ratio : if g_dut = "lm_mem_ram_cr_cw_ratio" generate
    dout_a_o <= (others => '0');

    inst_dut : entity work.lm_mem_ram_cr_cw_ratio
      generic map(
        g_ram_a_latency => g_a_latency,
        g_ram_a_data_w  => g_a_data_w,
        g_ram_a_depth   => g_a_depth,
        g_ram_b_latency => g_b_latency,
        g_ram_b_data_w  => g_b_data_w
      )
      port map(
        wr_clk_i  => clk_a_i,
        rd_clk_i  => clk_b_i,
        ena_i     => ena_i,
        enb_i     => enb_i,
        wen_i     => wen_a_i,
        wr_addr_i => addr_a_i,
        wr_dat_i  => din_a_i,
        rd_addr_i => addr_b_i,
        rd_dat_o  => dout_b_o
      );
  end generate gen_cr_cw_ratio;

  gen_crw_cr : if g_dut = "lm_mem_ram_crw_cr" generate
    inst_dut : entity work.lm_mem_ram_crw_cr
      generic map(
        g_ram_latency => g_a_latency,
        g_ram_data_w  => g_a_data_w,
        g_ram_depth   => g_a_depth
      )
      port map(
        clk_a_i     => clk_a_i,
        clk_b_i     => clk_b_i,
        ena_i       => ena_i,
        enb_i       => enb_i,
        wen_a_i     => wen_a_i,
        data_wr_a_i => din_a_i,
        addr_a_i    => addr_a_i,
        addr_b_i    => addr_b_i,
        data_rd_a_o => dout_a_o,
        data_rd_b_o => dout_b_o
      );
  end generate gen_crw_cr;

  gen_crw_crw : if g_dut = "lm_mem_ram_crw_crw" generate
    inst_dut : entity work.lm_mem_ram_crw_crw
      generic map(
        g_ram_a_latency => g_a_latency,
        g_ram_a_data_w  => g_a_data_w,
        g_ram_a_depth   => g_a_depth,
        g_ram_b_latency => g_b_latency,
        g_ram_b_data_w  => g_b_data_w
      )
      port map(
        clk_a_i     => clk_a_i,
        clk_b_i     => clk_b_i,
        ena_i       => ena_i,
        enb_i       => enb_i,
        wen_a_i     => wen_a_i,
        wen_b_i     => wen_b_i,
        data_wr_a_i => din_a_i,
        data_wr_b_i => din_b_i,
        addr_a_i    => addr_a_i,
        addr_b_i    => addr_b_i,
        data_rd_a_o => dout_a_o,
        data_rd_b_o => dout_b_o
      );
  end generate gen_crw_crw;

  -- Common clock: port B uses clk_a_i (the harness drives both clocks from
  -- one process when g_same_clock is set).
  gen_rw_rw : if g_dut = "lm_mem_ram_rw_rw" generate
    inst_dut : entity work.lm_mem_ram_rw_rw
      generic map(
        g_ram_latency => g_a_latency,
        g_ram_data_w  => g_a_data_w,
        g_ram_depth   => g_a_depth
      )
      port map(
        clk_i      => clk_a_i,
        ena_i      => ena_i,
        enb_i      => enb_i,
        wen_a_i    => wen_a_i,
        wen_b_i    => wen_b_i,
        wr_dat_a_i => din_a_i,
        wr_dat_b_i => din_b_i,
        addr_a_i   => addr_a_i,
        addr_b_i   => addr_b_i,
        rd_dat_a_o => dout_a_o,
        rd_dat_b_o => dout_b_o
      );
  end generate gen_rw_rw;

  gen_crw_cw : if g_dut = "lm_mem_ram_crw_cw" generate
    dout_b_o <= (others => '0');

    inst_dut : entity work.lm_mem_ram_crw_cw
      generic map(
        g_ram_latency => g_a_latency,
        g_ram_data_w  => g_a_data_w,
        g_ram_depth   => g_a_depth
      )
      port map(
        clk_a_i     => clk_a_i,
        clk_b_i     => clk_b_i,
        ena_i       => ena_i,
        enb_i       => enb_i,
        wen_a_i     => wen_a_i,
        wen_b_i     => wen_b_i,
        data_wr_a_i => din_a_i,
        data_wr_b_i => din_b_i,
        addr_a_i    => addr_a_i,
        addr_b_i    => addr_b_i,
        data_rd_a_o => dout_a_o
      );
  end generate gen_crw_cw;
end architecture rtl;
