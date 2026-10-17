-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_tdp1w
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Portable dual-port RAM core with one writer: port A reads and writes,
--  port B only reads. The same description is used for synthesis and
--  simulation.
--
--  - One process per clock. The storage signal is written only by the port A
--    process, so the core is inferable by vendor tools. clk_a_i and clk_b_i
--    may be the same clock.
--  - Read latency is 1 or 2 cycles per port. Both output stages of a port are
--    enabled by its enable and hold their value while it is low.
--  - Port A write is enabled by ena_i and wen_a_i together.
--  - Port A read during a port A write returns the old data (read-first).
--  - A port B read of the address written by port A is undefined when the two
--    accesses are less than one slow-clock period apart, also with a common
--    clock.
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

entity lm_mem_ram_tdp1w is
  generic(
    g_data_w      : positive := 32;
    g_depth       : positive := 1024;
    g_a_latency   : positive := 1;
    g_b_latency   : positive := 1;
    g_init_file   : string   := "";
    g_simulation  : integer  := 1
  );
  port(
    clk_a_i     : in  std_logic;
    clk_b_i     : in  std_logic;
    ena_i       : in  std_logic := '1';
    enb_i       : in  std_logic := '1';
    wen_a_i     : in  std_logic;
    data_wr_a_i : in  std_logic_vector(g_data_w - 1 downto 0);
    addr_a_i    : in  std_logic_vector(f_ceil_log2(g_depth) - 1 downto 0);
    addr_b_i    : in  std_logic_vector(f_ceil_log2(g_depth) - 1 downto 0);
    data_rd_a_o : out std_logic_vector(g_data_w - 1 downto 0);
    data_rd_b_o : out std_logic_vector(g_data_w - 1 downto 0)
  );
end entity lm_mem_ram_tdp1w;

architecture a_rtl of lm_mem_ram_tdp1w is
  constant C_STORAGE_DEPTH : positive := 2 ** f_ceil_log2(g_depth);

  type t_mem is array (0 to C_STORAGE_DEPTH - 1) of std_logic_vector(g_data_w - 1 downto 0);

  signal s_mem          : t_mem := (others => (others => '0'));
  signal s_data_rd_a    : std_logic_vector(g_data_w - 1 downto 0);
  signal s_data_rd_a_p  : std_logic_vector(g_data_w - 1 downto 0);
  signal s_data_rd_b    : std_logic_vector(g_data_w - 1 downto 0);
  signal s_data_rd_b_p  : std_logic_vector(g_data_w - 1 downto 0);
begin
  assert (g_a_latency = 1) or (g_a_latency = 2)
    report "lm_mem_ram_tdp1w: port A latency must be 1 or 2"
    severity failure;

  assert (g_b_latency = 1) or (g_b_latency = 2)
    report "lm_mem_ram_tdp1w: port B latency must be 1 or 2"
    severity failure;

  assert g_init_file = ""
    report "lm_mem_ram_tdp1w: initialization files are reserved for a future import step"
    severity failure;

  assert false
    report "lm_mem_ram_tdp1w: g_simulation is deprecated and ignored"
    severity note;

  proc_port_a : process(clk_a_i)
  begin
    if rising_edge(clk_a_i) then
      if ena_i = '1' then
        s_data_rd_a <= s_mem(to_integer(unsigned(addr_a_i)));
        if wen_a_i = '1' then
          s_mem(to_integer(unsigned(addr_a_i))) <= data_wr_a_i;
        end if;
        s_data_rd_a_p <= s_data_rd_a;
      end if;
    end if;
  end process proc_port_a;

  proc_port_b : process(clk_b_i)
  begin
    if rising_edge(clk_b_i) then
      if enb_i = '1' then
        s_data_rd_b <= s_mem(to_integer(unsigned(addr_b_i)));
        s_data_rd_b_p <= s_data_rd_b;
      end if;
    end if;
  end process proc_port_b;

  gen_port_a_latency_1 : if g_a_latency = 1 generate
    data_rd_a_o <= s_data_rd_a;
  end generate gen_port_a_latency_1;

  gen_port_a_latency_2 : if g_a_latency = 2 generate
    data_rd_a_o <= s_data_rd_a_p;
  end generate gen_port_a_latency_2;

  gen_port_b_latency_1 : if g_b_latency = 1 generate
    data_rd_b_o <= s_data_rd_b;
  end generate gen_port_b_latency_1;

  gen_port_b_latency_2 : if g_b_latency = 2 generate
    data_rd_b_o <= s_data_rd_b_p;
  end generate gen_port_b_latency_2;
end architecture a_rtl;
