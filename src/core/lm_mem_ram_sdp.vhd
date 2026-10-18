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
--  - The write and read widths may differ by a power-of-two ratio. The
--    storage has the narrower width; narrow word i of a wide access is at bits
--    (i + 1) * W - 1 downto i * W, at narrow address wide_address & i (the
--    asymmetric RAM template of the vendor guides, e.g. AMD UG901).
--  - Read latency is 1 or 2 read-clock cycles. Both output stages are
--    enabled by enb_i and hold their value while enb_i is low.
--  - Write is enabled by ena_i and wen_i together.
--  - On a common clock, a read of the address written in the same cycle
--    returns the old data. With two clocks, a read and a write of the same
--    address less than one slow-clock period apart are undefined.
--  - Addresses at or beyond the depth (possible only when the depth is not a
--    power of two): a write never changes an address within the depth; a
--    read returns undefined data. There is no address comparison in the core:
--    the storage covers the whole address range, so a non-power-of-two depth
--    uses the next power of two words.
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

  -- Static checks (lm_mem_pkg): elaboration stops at the line of the first
  -- failing check, with its message where assertions are evaluated.
  constant C_OK_LATENCY  : boolean := (g_rd_latency = 1) or (g_rd_latency = 2);
  constant C_OK_RATIO    : boolean := ((C_MAX_WIDTH mod C_MIN_WIDTH) = 0) and f_is_power_of_two(C_RATIO);
  constant C_OK_DEPTH    : boolean := ((g_wr_depth * g_wr_data_w) mod g_rd_data_w) = 0;
  constant C_OK_INIT     : boolean := g_init_file = "";
  constant C_MSG_LATENCY : string  := "lm_mem_ram_sdp: read latency must be 1 or 2";
  constant C_MSG_RATIO   : string  := "lm_mem_ram_sdp: port widths must have a power-of-two ratio";
  constant C_MSG_DEPTH   : string  := "lm_mem_ram_sdp: read depth must resolve to an integer";
  constant C_MSG_INIT    : string  := "lm_mem_ram_sdp: initialization files are reserved for a future import step";
  constant C_ERROR_RD_LATENCY_NOT_1_OR_2   : t_static_check := f_static_check(C_OK_LATENCY, C_MSG_LATENCY);  -- ERROR: g_rd_latency must be 1 or 2
  constant C_ERROR_WIDTH_RATIO_NOT_POW2    : t_static_check := f_static_check(C_OK_RATIO, C_MSG_RATIO);  -- ERROR: port width ratio must be a power of two
  constant C_ERROR_RD_DEPTH_NOT_INTEGER    : t_static_check := f_static_check(C_OK_DEPTH, C_MSG_DEPTH);  -- ERROR: g_wr_depth * g_wr_data_w / g_rd_data_w must be an integer
  constant C_ERROR_INIT_FILE_NOT_SUPPORTED : t_static_check := f_static_check(C_OK_INIT, C_MSG_INIT);  -- ERROR: g_init_file must be ""

  constant C_RD_DEPTH      : positive := (g_wr_depth * g_wr_data_w) / g_rd_data_w;
  constant C_WR_WORDS      : positive := g_wr_data_w / C_MIN_WIDTH;
  constant C_RD_WORDS      : positive := g_rd_data_w / C_MIN_WIDTH;
  constant C_WR_SPAN       : positive := (2 ** f_ceil_log2(g_wr_depth)) * C_WR_WORDS;
  constant C_RD_SPAN       : positive := (2 ** f_ceil_log2(C_RD_DEPTH)) * C_RD_WORDS;
  constant C_STORAGE_DEPTH : positive := f_max(C_WR_SPAN, C_RD_SPAN);
  -- Low address bits that select a narrow word (0 on the narrow port).
  constant C_WR_SEL        : natural  := f_sel_a_b(C_WR_WORDS > 1, f_ceil_log2(C_WR_WORDS), 0);
  constant C_RD_SEL        : natural  := f_sel_a_b(C_RD_WORDS > 1, f_ceil_log2(C_RD_WORDS), 0);

  type t_mem is array (0 to C_STORAGE_DEPTH - 1) of std_logic_vector(C_MIN_WIDTH - 1 downto 0);

  signal s_mem      : t_mem := (others => (others => '0'));
  signal s_rd_dat   : std_logic_vector(g_rd_data_w - 1 downto 0);
  signal s_rd_dat_p : std_logic_vector(g_rd_data_w - 1 downto 0);
begin
  assert false
    report "lm_mem_ram_sdp: g_simulation is deprecated and ignored"
    severity note;

  proc_wr : process(wr_clk_i)
  begin
    if rising_edge(wr_clk_i) then
      if (ena_i = '1') and (wen_i = '1') then
        for i in 0 to C_WR_WORDS - 1 loop
          s_mem(to_integer(unsigned(wr_addr_i) & to_unsigned(i, C_WR_SEL))) <=
            wr_dat_i((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH);
        end loop;
      end if;
    end if;
  end process proc_wr;

  proc_rd : process(rd_clk_i)
  begin
    if rising_edge(rd_clk_i) then
      if enb_i = '1' then
        for i in 0 to C_RD_WORDS - 1 loop
          s_rd_dat((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) <=
            s_mem(to_integer(unsigned(rd_addr_i) & to_unsigned(i, C_RD_SEL)));
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
