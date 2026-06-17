-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_crw_crw
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Generic true dual-port RAM.
--  Both ports have independent clocks, enables, write enables, address buses,
--  and read data outputs. Port widths may differ by an integer power-of-two
--  ratio. Read latency is one or two cycles per port.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.lm_mem_pkg.all;

entity lm_mem_ram_crw_crw is
  generic(
    g_ram_a_latency : positive := 1;
    g_ram_a_data_w  : positive := 32;
    g_ram_a_depth   : positive := 1024;
    g_ram_b_latency : positive := 1;
    g_ram_b_data_w  : positive := 32;
    g_init_file     : string   := "";
    g_simulation    : integer  := 1
  );
  port(
    clk_a_i     : in  std_logic;
    clk_b_i     : in  std_logic;
    ena_i       : in  std_logic := '1';
    enb_i       : in  std_logic := '1';
    wen_a_i     : in  std_logic;
    wen_b_i     : in  std_logic;
    data_wr_a_i : in  std_logic_vector(g_ram_a_data_w - 1 downto 0);
    data_wr_b_i : in  std_logic_vector(g_ram_b_data_w - 1 downto 0);
    addr_a_i    : in  std_logic_vector(f_ceil_log2(g_ram_a_depth) - 1 downto 0);
    addr_b_i    : in  std_logic_vector(f_ceil_log2((g_ram_a_depth * g_ram_a_data_w) / g_ram_b_data_w) - 1 downto 0);
    data_rd_a_o : out std_logic_vector(g_ram_a_data_w - 1 downto 0);
    data_rd_b_o : out std_logic_vector(g_ram_b_data_w - 1 downto 0)
  );
end entity lm_mem_ram_crw_crw;

architecture a_rtl of lm_mem_ram_crw_crw is
  constant C_MIN_WIDTH       : positive := f_min(g_ram_a_data_w, g_ram_b_data_w);
  constant C_MAX_WIDTH       : positive := f_max(g_ram_a_data_w, g_ram_b_data_w);
  constant C_RATIO           : positive := C_MAX_WIDTH / C_MIN_WIDTH;
  constant C_PORT_B_DEPTH    : natural  := (g_ram_a_depth * g_ram_a_data_w) / g_ram_b_data_w;
  constant C_STORAGE_DEPTH   : positive := f_max(g_ram_a_depth, C_PORT_B_DEPTH);

  type t_mem is array (0 to C_STORAGE_DEPTH - 1) of std_logic_vector(C_MIN_WIDTH - 1 downto 0);

  signal s_mem         : t_mem := (others => (others => '0'));
  signal s_data_rd_a   : std_logic_vector(g_ram_a_data_w - 1 downto 0);
  signal s_data_rd_b   : std_logic_vector(g_ram_b_data_w - 1 downto 0);
  signal s_data_rd_a_p : std_logic_vector(g_ram_a_data_w - 1 downto 0);
  signal s_data_rd_b_p : std_logic_vector(g_ram_b_data_w - 1 downto 0);

  function f_base_index(p_addr : natural; p_width : positive) return natural is
    variable v_index : natural := p_addr;
  begin
    if p_width /= C_MIN_WIDTH then
      v_index := p_addr * C_RATIO;
    end if;

    return v_index;
  end function f_base_index;

  function f_read_word(
    p_mem   : t_mem;
    p_addr  : std_logic_vector;
    p_width : positive
  ) return std_logic_vector is
    variable v_data : std_logic_vector(p_width - 1 downto 0) := (others => '0');
    variable v_base : natural;
  begin
    v_base := f_base_index(to_integer(unsigned(p_addr)), p_width);

    if p_width = C_MIN_WIDTH then
      if v_base < C_STORAGE_DEPTH then
        v_data := p_mem(v_base);
      end if;
    else
      for i in 0 to C_RATIO - 1 loop
        if v_base + i < C_STORAGE_DEPTH then
          v_data((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := p_mem(v_base + i);
        end if;
      end loop;
    end if;

    return v_data;
  end function f_read_word;

  procedure p_write_word(
    signal p_mem   : inout t_mem;
    p_addr         : in    std_logic_vector;
    p_width        : in    positive;
    p_data         : in    std_logic_vector
  ) is
    variable v_base : natural;
  begin
    v_base := f_base_index(to_integer(unsigned(p_addr)), p_width);

    if p_width = C_MIN_WIDTH then
      if v_base < C_STORAGE_DEPTH then
        p_mem(v_base) <= p_data;
      end if;
    else
      for i in 0 to C_RATIO - 1 loop
        if v_base + i < C_STORAGE_DEPTH then
          p_mem(v_base + i) <= p_data((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH);
        end if;
      end loop;
    end if;
  end procedure p_write_word;
begin
  assert (g_ram_a_latency = 1) or (g_ram_a_latency = 2)
    report "lm_mem_ram_crw_crw: port A latency must be 1 or 2"
    severity failure;

  assert (g_ram_b_latency = 1) or (g_ram_b_latency = 2)
    report "lm_mem_ram_crw_crw: port B latency must be 1 or 2"
    severity failure;

  assert (C_MAX_WIDTH mod C_MIN_WIDTH) = 0
    report "lm_mem_ram_crw_crw: port widths must have an integer ratio"
    severity failure;

  assert ((g_ram_a_depth * g_ram_a_data_w) mod g_ram_b_data_w) = 0
    report "lm_mem_ram_crw_crw: port B depth must resolve to an integer"
    severity failure;

  assert g_init_file = ""
    report "lm_mem_ram_crw_crw: initialization files are reserved for a future import step"
    severity failure;

  proc_mem : process(clk_a_i, clk_b_i)
  begin
    if rising_edge(clk_a_i) then
      if ena_i = '1' then
        s_data_rd_a   <= f_read_word(s_mem, addr_a_i, g_ram_a_data_w);
        s_data_rd_a_p <= s_data_rd_a;

        if wen_a_i = '1' then
          p_write_word(s_mem, addr_a_i, g_ram_a_data_w, data_wr_a_i);
        end if;
      end if;
    end if;

    if rising_edge(clk_b_i) then
      if enb_i = '1' then
        s_data_rd_b   <= f_read_word(s_mem, addr_b_i, g_ram_b_data_w);
        s_data_rd_b_p <= s_data_rd_b;

        if wen_b_i = '1' then
          p_write_word(s_mem, addr_b_i, g_ram_b_data_w, data_wr_b_i);
        end if;
      end if;
    end if;
  end process proc_mem;

  gen_port_a_latency_1 : if g_ram_a_latency = 1 generate
    data_rd_a_o <= s_data_rd_a;
  end generate gen_port_a_latency_1;

  gen_port_a_latency_2 : if g_ram_a_latency = 2 generate
    data_rd_a_o <= s_data_rd_a_p;
  end generate gen_port_a_latency_2;

  gen_port_b_latency_1 : if g_ram_b_latency = 1 generate
    data_rd_b_o <= s_data_rd_b;
  end generate gen_port_b_latency_1;

  gen_port_b_latency_2 : if g_ram_b_latency = 2 generate
    data_rd_b_o <= s_data_rd_b_p;
  end generate gen_port_b_latency_2;
end architecture a_rtl;
