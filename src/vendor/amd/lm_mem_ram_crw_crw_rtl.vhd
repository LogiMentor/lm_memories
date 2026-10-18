-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_crw_crw (AMD Vivado architecture)
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Vivado synthesis architecture of the dual-model entity lm_mem_ram_crw_crw
--  (src/core/lm_mem_ram_crw_crw_ent.vhd). Read by Vivado only
--  (manifests/lm_memories.toml, check = "target-tool"); it is verified by
--  synthesis and by post-synthesis simulation of the netlist.
--
--  - Based on the true dual-port, read-first templates of AMD UG901,
--    including the asymmetric one: one process per port and a plain shared
--    variable as storage. The protected-type variant is not used: Vivado
--    2023.1 builds a wrong netlist from it (docs/PLAN.md section 2).
--  - The storage has the narrower width. Narrow word i of a wide access is at
--    narrow address wide_address & i. The ratio must be a power of two.
--  - The storage covers the whole address range: there is no address
--    comparison, and a depth that is not a power of two uses the next power
--    of two words.
--  - In RTL simulation the result of a collision between the ports depends on
--    the process order; the collision behaviour is undefined (see the entity).
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.lm_mem_pkg.all;

architecture a_rtl of lm_mem_ram_crw_crw is
  constant C_MIN_WIDTH     : positive := f_min(g_ram_a_data_w, g_ram_b_data_w);
  constant C_MAX_WIDTH     : positive := f_max(g_ram_a_data_w, g_ram_b_data_w);
  constant C_RATIO         : positive := C_MAX_WIDTH / C_MIN_WIDTH;

  -- Static checks (lm_mem_pkg): elaboration stops at the line of the first
  -- failing check, with its message where assertions are evaluated.
  constant C_OK_A_LATENCY  : boolean := (g_ram_a_latency = 1) or (g_ram_a_latency = 2);
  constant C_OK_B_LATENCY  : boolean := (g_ram_b_latency = 1) or (g_ram_b_latency = 2);
  constant C_OK_RATIO      : boolean := ((C_MAX_WIDTH mod C_MIN_WIDTH) = 0) and f_is_power_of_two(C_RATIO);
  constant C_OK_DEPTH      : boolean := ((g_ram_a_depth * g_ram_a_data_w) mod g_ram_b_data_w) = 0;
  constant C_OK_INIT       : boolean := g_init_file = "";
  constant C_MSG_A_LATENCY : string  := "lm_mem_ram_crw_crw: port A latency must be 1 or 2";
  constant C_MSG_B_LATENCY : string  := "lm_mem_ram_crw_crw: port B latency must be 1 or 2";
  constant C_MSG_RATIO     : string  := "lm_mem_ram_crw_crw: port widths must have a power-of-two ratio";
  constant C_MSG_DEPTH     : string  := "lm_mem_ram_crw_crw: port B depth must resolve to an integer";
  constant C_MSG_INIT      : string  := "lm_mem_ram_crw_crw: initialization files are reserved for a future import step";
  constant C_ERROR_A_LATENCY_NOT_1_OR_2    : t_static_check := f_static_check(C_OK_A_LATENCY, C_MSG_A_LATENCY);  -- ERROR: g_ram_a_latency must be 1 or 2
  constant C_ERROR_B_LATENCY_NOT_1_OR_2    : t_static_check := f_static_check(C_OK_B_LATENCY, C_MSG_B_LATENCY);  -- ERROR: g_ram_b_latency must be 1 or 2
  constant C_ERROR_WIDTH_RATIO_NOT_POW2    : t_static_check := f_static_check(C_OK_RATIO, C_MSG_RATIO);  -- ERROR: port width ratio must be a power of two
  constant C_ERROR_B_DEPTH_NOT_INTEGER     : t_static_check := f_static_check(C_OK_DEPTH, C_MSG_DEPTH);  -- ERROR: g_ram_a_depth * g_ram_a_data_w / g_ram_b_data_w must be an integer
  constant C_ERROR_INIT_FILE_NOT_SUPPORTED : t_static_check := f_static_check(C_OK_INIT, C_MSG_INIT);  -- ERROR: g_init_file must be ""

  constant C_B_DEPTH       : positive := (g_ram_a_depth * g_ram_a_data_w) / g_ram_b_data_w;
  constant C_A_WORDS       : positive := g_ram_a_data_w / C_MIN_WIDTH;
  constant C_B_WORDS       : positive := g_ram_b_data_w / C_MIN_WIDTH;
  constant C_A_SPAN        : positive := (2 ** f_ceil_log2(g_ram_a_depth)) * C_A_WORDS;
  constant C_B_SPAN        : positive := (2 ** f_ceil_log2(C_B_DEPTH)) * C_B_WORDS;
  constant C_STORAGE_DEPTH : positive := f_max(C_A_SPAN, C_B_SPAN);
  -- Low address bits that select a narrow word (0 on the narrow port).
  constant C_A_SEL         : natural  := f_sel_a_b(C_A_WORDS > 1, f_ceil_log2(C_A_WORDS), 0);
  constant C_B_SEL         : natural  := f_sel_a_b(C_B_WORDS > 1, f_ceil_log2(C_B_WORDS), 0);

  type t_mem is array (0 to C_STORAGE_DEPTH - 1) of std_logic_vector(C_MIN_WIDTH - 1 downto 0);

  shared variable sv_mem : t_mem := (others => (others => '0'));

  signal s_data_rd_a   : std_logic_vector(g_ram_a_data_w - 1 downto 0);
  signal s_data_rd_b   : std_logic_vector(g_ram_b_data_w - 1 downto 0);
  signal s_data_rd_a_p : std_logic_vector(g_ram_a_data_w - 1 downto 0);
  signal s_data_rd_b_p : std_logic_vector(g_ram_b_data_w - 1 downto 0);
begin
  assert false
    report "lm_mem_ram_crw_crw: g_simulation is deprecated and ignored"
    severity note;

  proc_port_a : process(clk_a_i)
  begin
    if rising_edge(clk_a_i) then
      if ena_i = '1' then
        for i in 0 to C_A_WORDS - 1 loop
          s_data_rd_a((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) <=
            sv_mem(to_integer(unsigned(addr_a_i) & to_unsigned(i, C_A_SEL)));
          if wen_a_i = '1' then
            sv_mem(to_integer(unsigned(addr_a_i) & to_unsigned(i, C_A_SEL))) :=
              data_wr_a_i((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH);
          end if;
        end loop;
        s_data_rd_a_p <= s_data_rd_a;
      end if;
    end if;
  end process proc_port_a;

  proc_port_b : process(clk_b_i)
  begin
    if rising_edge(clk_b_i) then
      if enb_i = '1' then
        for i in 0 to C_B_WORDS - 1 loop
          s_data_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) <=
            sv_mem(to_integer(unsigned(addr_b_i) & to_unsigned(i, C_B_SEL)));
          if wen_b_i = '1' then
            sv_mem(to_integer(unsigned(addr_b_i) & to_unsigned(i, C_B_SEL))) :=
              data_wr_b_i((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH);
          end if;
        end loop;
        s_data_rd_b_p <= s_data_rd_b;
      end if;
    end if;
  end process proc_port_b;

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
