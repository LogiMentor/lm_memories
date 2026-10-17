-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_sdp
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Portable simple dual-port RAM core: one write port, one read port.
--  The same description is used for synthesis and simulation.
--
--  - One process per clock. The storage signal is written only by the write
--    process, so the core is inferable by vendor tools as block or
--    distributed RAM. wr_clk_i and rd_clk_i may be the same clock.
--  - The write and read widths may differ by an integer ratio. The storage
--    has the narrower width; narrow word i of a wide access is at bits
--    (i + 1) * W - 1 downto i * W, at narrow address wide_address * ratio + i.
--  - Read latency is 1 or 2 read-clock cycles. Both output stages are
--    enabled by enb_i and hold their value while enb_i is low.
--  - Write is enabled by ena_i and wen_i together.
--  - On a common clock, a read of the address written in the same cycle
--    returns the old data. With two clocks, a read and a write of the same
--    address less than one slow-clock period apart are undefined.
--  - Addresses beyond the depth are ignored on write; a read of such an
--    address leaves the read registers unchanged.
--  - Initial content is zero; there is no reset.
--  - g_simulation is deprecated and ignored.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.lm_mem_pkg.all;

entity lm_mem_ram_sdp is
  generic(
    g_wr_data_w  : positive := 32;
    g_wr_depth   : positive := 1024;
    g_rd_data_w  : positive := 32;
    g_rd_latency : positive := 1;
    g_init_file  : string   := "";
    g_simulation : integer  := 1
  );
  port(
    wr_clk_i  : in  std_logic;
    rd_clk_i  : in  std_logic;
    ena_i     : in  std_logic := '1';
    enb_i     : in  std_logic := '1';
    wen_i     : in  std_logic;
    wr_addr_i : in  std_logic_vector(f_ceil_log2(g_wr_depth) - 1 downto 0);
    wr_dat_i  : in  std_logic_vector(g_wr_data_w - 1 downto 0);
    rd_addr_i : in  std_logic_vector(f_ceil_log2((g_wr_depth * g_wr_data_w) / g_rd_data_w) - 1 downto 0);
    rd_dat_o  : out std_logic_vector(g_rd_data_w - 1 downto 0)
  );
end entity lm_mem_ram_sdp;

architecture a_rtl of lm_mem_ram_sdp is
  constant C_MIN_WIDTH     : positive := f_min(g_wr_data_w, g_rd_data_w);
  constant C_MAX_WIDTH     : positive := f_max(g_wr_data_w, g_rd_data_w);
  constant C_RATIO         : positive := C_MAX_WIDTH / C_MIN_WIDTH;
  constant C_RD_DEPTH      : positive := (g_wr_depth * g_wr_data_w) / g_rd_data_w;
  constant C_STORAGE_DEPTH : positive := f_max(g_wr_depth, C_RD_DEPTH);
  constant C_WR_WORDS      : positive := g_wr_data_w / C_MIN_WIDTH;
  constant C_RD_WORDS      : positive := g_rd_data_w / C_MIN_WIDTH;

  type t_mem is array (0 to C_STORAGE_DEPTH - 1) of std_logic_vector(C_MIN_WIDTH - 1 downto 0);

  signal s_mem      : t_mem := (others => (others => '0'));
  signal s_rd_dat   : std_logic_vector(g_rd_data_w - 1 downto 0);
  signal s_rd_dat_p : std_logic_vector(g_rd_data_w - 1 downto 0);
begin
  assert (g_rd_latency = 1) or (g_rd_latency = 2)
    report "lm_mem_ram_sdp: read latency must be 1 or 2"
    severity failure;

  assert (C_MAX_WIDTH mod C_MIN_WIDTH) = 0
    report "lm_mem_ram_sdp: port widths must have an integer ratio"
    severity failure;

  assert ((g_wr_depth * g_wr_data_w) mod g_rd_data_w) = 0
    report "lm_mem_ram_sdp: read depth must resolve to an integer"
    severity failure;

  assert g_init_file = ""
    report "lm_mem_ram_sdp: initialization files are reserved for a future import step"
    severity failure;

  assert false
    report "lm_mem_ram_sdp: g_simulation is deprecated and ignored"
    severity note;

  proc_wr : process(wr_clk_i)
    variable v_base : natural;
  begin
    if rising_edge(wr_clk_i) then
      if (ena_i = '1') and (wen_i = '1') then
        v_base := to_integer(unsigned(wr_addr_i)) * C_WR_WORDS;
        for i in 0 to C_WR_WORDS - 1 loop
          if v_base + i < C_STORAGE_DEPTH then
            s_mem(v_base + i) <= wr_dat_i((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH);
          end if;
        end loop;
      end if;
    end if;
  end process proc_wr;

  proc_rd : process(rd_clk_i)
    variable v_base : natural;
  begin
    if rising_edge(rd_clk_i) then
      if enb_i = '1' then
        v_base := to_integer(unsigned(rd_addr_i)) * C_RD_WORDS;
        for i in 0 to C_RD_WORDS - 1 loop
          if v_base + i < C_STORAGE_DEPTH then
            s_rd_dat((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) <= s_mem(v_base + i);
          end if;
        end loop;
        s_rd_dat_p <= s_rd_dat;
      end if;
    end if;
  end process proc_rd;

  gen_latency_1 : if g_rd_latency = 1 generate
    rd_dat_o <= s_rd_dat;
  end generate gen_latency_1;

  gen_latency_2 : if g_rd_latency = 2 generate
    rd_dat_o <= s_rd_dat_p;
  end generate gen_latency_2;
end architecture a_rtl;
