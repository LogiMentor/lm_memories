-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_fifo
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Generic FIFO adapted from the tested LM FIFO implementation.
--  Supports common-clock and dual-clock operation, optional FWFT behaviour,
--  independent write/read data widths, and programmable almost flags.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.lm_mem_pkg.all;

entity lm_mem_fifo is
  generic(
    g_dual_clock         : boolean := false;
    g_fwft               : boolean := false;
    g_wr_depth           : positive := 16;
    g_wr_data_w          : positive := 32;
    g_rd_data_w          : positive := 32;
    g_rd_latency         : positive := 1;
    g_ren_ctrl           : boolean := true;
    g_wen_ctrl           : boolean := true;
    g_almost_empty_limit : integer := 1;
    g_almost_full_limit  : integer := 1;
    g_sanity_check       : boolean := true;
    g_simulation         : integer := 1
  );
  port(
    wr_clk_i       : in  std_logic;
    rd_clk_i       : in  std_logic;
    wr_rst_n_i     : in  std_logic;
    rd_rst_n_i     : in  std_logic;
    din_i          : in  std_logic_vector(g_wr_data_w - 1 downto 0);
    wen_i          : in  std_logic;
    full_o         : out std_logic;
    almost_full_o  : out std_logic;
    dout_o         : out std_logic_vector(g_rd_data_w - 1 downto 0);
    ren_i          : in  std_logic;
    empty_o        : out std_logic;
    almost_empty_o : out std_logic;
    valid_o        : out std_logic
  );
end entity lm_mem_fifo;

architecture a_rtl of lm_mem_fifo is
  constant C_WR_ADD_BW  : natural := f_ceil_log2(g_wr_depth);
  constant C_MINWIDTH   : integer := f_min(g_wr_data_w, g_rd_data_w);
  constant C_MAXWIDTH   : integer := f_max(g_wr_data_w, g_rd_data_w);
  constant C_RATIO      : integer := C_MAXWIDTH / C_MINWIDTH;
  constant C_DIFF       : integer := f_ceil_log2(C_MAXWIDTH / C_MINWIDTH);
  constant C_RD_DEPTH   : integer := f_sel_a_b(g_wr_data_w > g_rd_data_w, g_wr_depth * C_RATIO, g_wr_depth / C_RATIO);
  constant C_RD_ADD_BW  : natural := f_ceil_log2(C_RD_DEPTH);
  constant C_ADD_BW_MAX : natural := f_sel_a_b(C_WR_ADD_BW > C_RD_ADD_BW, C_WR_ADD_BW, C_RD_ADD_BW);

  signal s_dout              : std_logic_vector(g_rd_data_w - 1 downto 0);
  signal s_mem_ren           : std_logic;
  signal s_r_ptr             : unsigned(C_RD_ADD_BW downto 0) := (others => '0');
  signal s_r_ptr_inc         : unsigned(C_RD_ADD_BW downto 0) := (others => '0');
  signal s_w_ptr             : unsigned(C_WR_ADD_BW downto 0) := (others => '0');
  signal s_w_ptr_inc         : unsigned(C_WR_ADD_BW downto 0) := (others => '0');
  signal s_full              : std_logic := '0';
  signal s_full_comb         : std_logic;
  signal s_full_comb_part    : std_logic_vector(1 downto 0);
  signal s_empty             : std_logic := '1';
  signal s_empty_comb        : std_logic;
  signal s_empty_comb_part   : std_logic_vector(1 downto 0);
  signal s_almost_empty      : std_logic := '1';
  signal s_almost_empty_comb : std_logic;
  signal s_almost_full       : std_logic := '0';
  signal s_almost_full_comb  : std_logic;
  signal s_mem_r_addr        : unsigned(C_RD_ADD_BW - 1 downto 0);
  signal s_mem_valid         : std_logic;
  signal s_ren               : std_logic;
  signal s_wen               : std_logic;
  signal s_din               : std_logic_vector(g_wr_data_w - 1 downto 0);
  signal s_ren_shreg         : std_logic_vector(g_rd_latency downto 0);
  signal s_wadd_gray_comb    : std_logic_vector(C_ADD_BW_MAX downto 0) := (others => '0');
  signal s_radd_gray_comb    : std_logic_vector(C_ADD_BW_MAX downto 0) := (others => '0');
  signal s_wadd_gray_resync  : std_logic_vector(C_ADD_BW_MAX downto 0) := (others => '0');
  signal s_radd_gray_resync  : std_logic_vector(C_ADD_BW_MAX downto 0) := (others => '0');
  signal s_wadd_bin_resync   : std_logic_vector(C_RD_ADD_BW downto 0) := (others => '0');
  signal s_radd_bin_resync   : std_logic_vector(C_WR_ADD_BW downto 0) := (others => '0');
  signal s_diff_w            : unsigned(C_WR_ADD_BW downto 0) := (others => '0');
  signal s_wadd_gray         : unsigned(C_ADD_BW_MAX downto 0) := (others => '0');
  signal s_diff_r            : unsigned(C_RD_ADD_BW downto 0) := (others => '0');
  signal s_radd_gray         : unsigned(C_ADD_BW_MAX downto 0) := (others => '0');
  signal s_wadd_gray_r       : unsigned(C_ADD_BW_MAX downto 0) := (others => '0');
  signal s_radd_gray_r       : unsigned(C_ADD_BW_MAX downto 0) := (others => '0');
begin
  assert f_is_power_of_two(g_wr_depth)
    report "lm_mem_fifo: g_wr_depth must be a power of two"
    severity failure;

  assert (g_rd_latency = 1) or (g_rd_latency = 2)
    report "lm_mem_fifo: g_rd_latency must be 1 or 2"
    severity failure;

  assert (C_MAXWIDTH mod C_MINWIDTH) = 0
    report "lm_mem_fifo: read/write widths must have an integer ratio"
    severity failure;

  assert (g_wr_depth * g_wr_data_w) mod g_rd_data_w = 0
    report "lm_mem_fifo: read depth must resolve to an integer"
    severity failure;

  gen_fwft_latency_check : if g_fwft generate
    assert g_rd_latency = 1
      report "lm_mem_fifo: FWFT shall be used only with read latency 1"
      severity failure;
  end generate gen_fwft_latency_check;

  inst_dpram : entity work.lm_mem_ram_cr_cw_ratio
    generic map(
      g_ram_a_latency => g_rd_latency,
      g_ram_a_data_w  => g_wr_data_w,
      g_ram_a_depth   => g_wr_depth,
      g_ram_b_latency => g_rd_latency,
      g_ram_b_data_w  => g_rd_data_w,
      g_simulation    => g_simulation
    )
    port map(
      wr_clk_i  => wr_clk_i,
      rd_clk_i  => rd_clk_i,
      ena_i     => '1',
      enb_i     => '1',
      wen_i     => s_wen,
      wr_addr_i => std_logic_vector(s_w_ptr(C_WR_ADD_BW - 1 downto 0)),
      wr_dat_i  => s_din,
      rd_addr_i => std_logic_vector(s_mem_r_addr),
      rd_dat_o  => s_dout
    );

  s_ren <= ren_i when g_ren_ctrl = false else ren_i and not s_empty;

  gen_dual_clock_write_gate : if g_dual_clock generate
    s_wen <= wen_i when g_wen_ctrl = false else wen_i and not s_full;
  end generate gen_dual_clock_write_gate;

  gen_common_clock_equal_width_write_gate : if (g_dual_clock = false) and (g_wr_data_w = g_rd_data_w) generate
    s_wen <= wen_i when g_wen_ctrl = false else wen_i and ((not s_full) or s_ren);
  end generate gen_common_clock_equal_width_write_gate;

  gen_common_clock_ratio_write_gate : if (g_dual_clock = false) and (g_wr_data_w /= g_rd_data_w) generate
    s_wen <= wen_i when g_wen_ctrl = false else wen_i and not s_full;
  end generate gen_common_clock_ratio_write_gate;

  s_din <= din_i;

  proc_read_pointer : process(rd_clk_i)
  begin
    if rising_edge(rd_clk_i) then
      if rd_rst_n_i = '0' then
        s_r_ptr        <= (others => '0');
        s_empty        <= '1';
        s_almost_empty <= '1';
      else
        if s_ren = '1' then
          s_r_ptr <= s_r_ptr + 1;
        end if;

        s_empty        <= s_empty_comb;
        s_almost_empty <= s_almost_empty_comb;
      end if;
    end if;
  end process proc_read_pointer;

  proc_write_pointer : process(wr_clk_i)
  begin
    if rising_edge(wr_clk_i) then
      if wr_rst_n_i = '0' then
        s_w_ptr       <= (others => '0');
        s_full        <= '0';
        s_almost_full <= '0';
      else
        if s_wen = '1' then
          s_w_ptr <= s_w_ptr + 1;
        end if;

        s_full        <= s_full_comb;
        s_almost_full <= s_almost_full_comb;
      end if;
    end if;
  end process proc_write_pointer;

  s_r_ptr_inc <= s_r_ptr + 1;
  s_w_ptr_inc <= s_w_ptr + 1;

  proc_ren_shreg : process(rd_clk_i)
  begin
    if rising_edge(rd_clk_i) then
      s_ren_shreg <= s_ren_shreg(s_ren_shreg'left - 1 downto 0) & s_ren;
    end if;
  end process proc_ren_shreg;

  gen_no_fwft : if g_fwft = false generate
    s_mem_ren    <= s_ren;
    s_mem_r_addr <= s_r_ptr(s_mem_r_addr'range);
    s_mem_valid  <= s_ren_shreg(g_rd_latency - 1);
  end generate gen_no_fwft;

  gen_fwft : if g_fwft generate
    s_mem_ren    <= not s_empty_comb;
    s_mem_r_addr <= s_r_ptr_inc(s_mem_r_addr'range) when (s_ren = '1' and s_empty = '0') else
                    s_r_ptr(s_mem_r_addr'range);
    s_mem_valid  <= not s_empty;
  end generate gen_fwft;

  gen_wr_ar1 : if g_wr_data_w = g_rd_data_w generate
    s_wadd_gray_comb <= f_bin2gray(f_uns2slv(s_w_ptr_inc));
    s_radd_gray_comb <= f_bin2gray(f_uns2slv(s_r_ptr_inc));

    gen_dual_clock : if g_dual_clock generate
      s_radd_bin_resync <= f_gray2bin(s_radd_gray_resync);
      s_wadd_bin_resync <= f_gray2bin(s_wadd_gray_resync);
    end generate gen_dual_clock;

    gen_single_clock : if g_dual_clock = false generate
      s_radd_bin_resync <= f_uns2slv(s_r_ptr);
      s_wadd_bin_resync <= f_uns2slv(s_w_ptr);
    end generate gen_single_clock;

    s_wadd_gray <= unsigned(s_wadd_gray_comb);
    s_radd_gray <= unsigned(s_radd_gray_comb);
  end generate gen_wr_ar1;

  gen_wr_ar2 : if g_wr_data_w > g_rd_data_w generate
    s_wadd_gray_comb <= f_bin2gray(f_uns2slv(s_w_ptr_inc) & (C_DIFF - 1 downto 0 => '0'));
    s_radd_gray_comb <= f_bin2gray(f_uns2slv(s_r_ptr_inc));

    gen_dual_clock : if g_dual_clock generate
      s_wadd_bin_resync <= f_gray2bin(s_wadd_gray_resync);
      s_radd_bin_resync <= f_gray2bin(s_radd_gray_resync(s_r_ptr'length - 1 downto C_DIFF));
    end generate gen_dual_clock;

    gen_single_clock : if g_dual_clock = false generate
      s_wadd_bin_resync <= f_uns2slv(s_w_ptr) & (C_DIFF - 1 downto 0 => '0');
      s_radd_bin_resync <= f_uns2slv(s_r_ptr(s_r_ptr'length - 1 downto C_DIFF));
    end generate gen_single_clock;

    s_wadd_gray <= unsigned(s_wadd_gray_comb);
    s_radd_gray <= unsigned(s_radd_gray_comb);
  end generate gen_wr_ar2;

  gen_wr_ar3 : if g_rd_data_w > g_wr_data_w generate
    s_wadd_gray_comb <= f_bin2gray(f_uns2slv(s_w_ptr_inc));
    s_radd_gray_comb <= f_bin2gray(f_uns2slv(s_r_ptr_inc) & (C_DIFF - 1 downto 0 => '0'));

    gen_dual_clock : if g_dual_clock generate
      s_wadd_bin_resync <= f_gray2bin(s_wadd_gray_resync(s_w_ptr'length - 1 downto C_DIFF));
      s_radd_bin_resync <= f_gray2bin(s_radd_gray_resync);
    end generate gen_dual_clock;

    gen_single_clock : if g_dual_clock = false generate
      s_wadd_bin_resync <= f_uns2slv(s_w_ptr(s_w_ptr'length - 1 downto C_DIFF));
      s_radd_bin_resync <= f_uns2slv(s_r_ptr) & (C_DIFF - 1 downto 0 => '0');
    end generate gen_single_clock;

    s_wadd_gray <= unsigned(s_wadd_gray_comb);
    s_radd_gray <= unsigned(s_radd_gray_comb);
  end generate gen_wr_ar3;

  proc_wr_gray_cnt : process(wr_clk_i)
  begin
    if rising_edge(wr_clk_i) then
      if wr_rst_n_i = '0' then
        s_diff_w      <= (others => '0');
        s_wadd_gray_r <= (others => '0');
      else
        s_diff_w <= s_w_ptr - unsigned(s_radd_bin_resync);

        if s_wen = '1' then
          s_wadd_gray_r <= s_wadd_gray(s_wadd_gray_r'length - 1 downto 0);
        end if;
      end if;
    end if;
  end process proc_wr_gray_cnt;

  proc_rd_gray_cnt : process(rd_clk_i)
  begin
    if rising_edge(rd_clk_i) then
      if rd_rst_n_i = '0' then
        s_diff_r      <= (others => '0');
        s_radd_gray_r <= (others => '0');
      else
        s_diff_r <= unsigned(s_wadd_bin_resync) - s_r_ptr;

        if s_ren = '1' then
          s_radd_gray_r <= s_radd_gray(s_radd_gray_r'length - 1 downto 0);
        end if;
      end if;
    end if;
  end process proc_rd_gray_cnt;

  gen_wr_pointer_resync : for i in s_wadd_gray_r'range generate
    inst_wadd_to_rd_sync : entity work.lm_mem_ccd_resync
      generic map(
        g_meta_levels => C_META_DELAY_LEN
      )
      port map(
        clk_i     => rd_clk_i,
        ccd_din_i => s_wadd_gray_r(i),
        ccd_din_o => s_wadd_gray_resync(i)
      );
  end generate gen_wr_pointer_resync;

  gen_rd_pointer_resync : for i in s_radd_gray_r'range generate
    inst_radd_to_wr_sync : entity work.lm_mem_ccd_resync
      generic map(
        g_meta_levels => C_META_DELAY_LEN
      )
      port map(
        clk_i     => wr_clk_i,
        ccd_din_i => s_radd_gray_r(i),
        ccd_din_o => s_radd_gray_resync(i)
      );
  end generate gen_rd_pointer_resync;

  s_full_comb_part(0) <= '1' when s_w_ptr(C_WR_ADD_BW) /= s_radd_bin_resync(C_WR_ADD_BW) and
                                  s_w_ptr(C_WR_ADD_BW - 1 downto 0) =
                                  unsigned(s_radd_bin_resync(C_WR_ADD_BW - 1 downto 0)) else '0';

  s_full_comb_part(1) <= '1' when s_w_ptr_inc(C_WR_ADD_BW) /= s_radd_bin_resync(C_WR_ADD_BW) and
                                  s_w_ptr_inc(C_WR_ADD_BW - 1 downto 0) =
                                  unsigned(s_radd_bin_resync(C_WR_ADD_BW - 1 downto 0)) and
                                  s_wen = '1' else '0';

  s_full_comb <= f_vector_or(s_full_comb_part);

  s_empty_comb_part(0) <= '1' when s_r_ptr = unsigned(s_wadd_bin_resync) else '0';
  s_empty_comb_part(1) <= '1' when s_r_ptr_inc = unsigned(s_wadd_bin_resync) and s_ren = '1' else '0';
  s_empty_comb         <= f_vector_or(s_empty_comb_part);

  s_almost_empty_comb <= '1' when to_integer(unsigned(s_wadd_bin_resync) - s_r_ptr) <=
                                  g_almost_empty_limit + 1 else '0';

  s_almost_full_comb <= '1' when (to_integer(s_w_ptr - unsigned(s_radd_bin_resync)) >=
                                  g_almost_full_limit - 1) and
                                 g_almost_full_limit > 1 else
                        '1' when ((to_integer(s_w_ptr - unsigned(s_radd_bin_resync)) >=
                                   g_almost_full_limit) or
                                  (to_integer(s_w_ptr - unsigned(s_radd_bin_resync)) = 0 and s_wen = '1')) and
                                  g_almost_full_limit = 1 else
                        '0';

  gen_sanity_check : if g_sanity_check generate
    gen_sanity_wr_dual_clock : if g_dual_clock generate
      proc_sanity_wr : process
      begin
        wait until rising_edge(wr_clk_i);
        assert not (s_full = '1' and s_wen = '1')
          report "lm_mem_fifo: write enable asserted while FIFO is full"
          severity warning;
      end process proc_sanity_wr;
    end generate gen_sanity_wr_dual_clock;

    gen_sanity_wr_common_clock : if g_dual_clock = false generate
      proc_sanity_wr : process
      begin
        wait until rising_edge(wr_clk_i);
        assert not (s_full = '1' and s_wen = '1' and
                    (s_ren = '0' or g_wr_data_w /= g_rd_data_w))
          report "lm_mem_fifo: write enable asserted while FIFO is full"
          severity warning;
      end process proc_sanity_wr;
    end generate gen_sanity_wr_common_clock;

    proc_sanity_rd : process
    begin
      wait until rising_edge(rd_clk_i);
      assert not (s_empty = '1' and s_ren = '1')
        report "lm_mem_fifo: read enable asserted while FIFO is empty"
        severity warning;
    end process proc_sanity_rd;
  end generate gen_sanity_check;

  dout_o         <= s_dout;
  almost_full_o  <= s_almost_full;
  almost_empty_o <= s_almost_empty;
  full_o         <= s_full;
  empty_o        <= s_empty;
  valid_o        <= s_mem_valid;
end architecture a_rtl;
