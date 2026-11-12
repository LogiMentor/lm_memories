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
--  - Read-output resets (issue #7), one per read port: g_a_rst_en and
--    g_b_rst_en enable them, rst_a_n_i and rst_b_n_i drive them.
--    - Enable false (default): the port is the one of v0.2.0, with no reset
--      logic, so tools infer the same memory with the same resources; the
--      reset input is ignored, and a simulation warning reports it when it
--      is low.
--    - Enable true: a synchronous reset of the read output of that port,
--      active low, in the clock domain of the port. It acts only while the
--      enable of the port (ena_i, enb_i) is high; with the enable low it has
--      no effect and the output holds its value. With latency 1 it clears the
--      output register (block RAM output latch reset, RSTRAM on AMD). With
--      latency 2 it clears both stages (RSTRAM and RSTREG): after the reset
--      the output stays zero until a read made after the reset reaches it.
--      The read of a reset cycle is discarded. A reset never affects the
--      storage: a port A write in a reset cycle is done. Vivado 2023.1 builds
--      each reset with one LUT ("enable AND NOT reset") on the reset pins of
--      the memory.
--  - Read outputs have no initial value: they are undefined until the first
--    enabled read reaches them (with latency 2, until the second enabled read
--    of that port), or, with the reset of the port enabled, until a reset
--    applied with the enable of the port high clears them to zero.
--  - Initial content is zero; the storage has no reset.
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
    g_simulation  : integer  := 1;
    g_a_rst_en    : boolean  := false;
    g_b_rst_en    : boolean  := false
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
    data_rd_b_o : out std_logic_vector(g_data_w - 1 downto 0);
    rst_a_n_i   : in  std_logic := '1';
    rst_b_n_i   : in  std_logic := '1'
  );
end entity lm_mem_ram_tdp1w;

architecture a_rtl of lm_mem_ram_tdp1w is
  -- Static checks (lm_mem_pkg): elaboration stops at the line of the first
  -- failing check, with its message where assertions are evaluated.
  constant C_OK_A_LATENCY  : boolean := (g_a_latency = 1) or (g_a_latency = 2);
  constant C_OK_B_LATENCY  : boolean := (g_b_latency = 1) or (g_b_latency = 2);
  constant C_OK_INIT       : boolean := g_init_file = "";
  constant C_MSG_A_LATENCY : string  := "lm_mem_ram_tdp1w: port A latency must be 1 or 2";
  constant C_MSG_B_LATENCY : string  := "lm_mem_ram_tdp1w: port B latency must be 1 or 2";
  constant C_MSG_INIT      : string  := "lm_mem_ram_tdp1w: initialization files are reserved for a future import step";
  constant C_ERROR_A_LATENCY_NOT_1_OR_2    : t_static_check := f_static_check(C_OK_A_LATENCY, C_MSG_A_LATENCY);  -- ERROR: g_a_latency must be 1 or 2
  constant C_ERROR_B_LATENCY_NOT_1_OR_2    : t_static_check := f_static_check(C_OK_B_LATENCY, C_MSG_B_LATENCY);  -- ERROR: g_b_latency must be 1 or 2
  constant C_ERROR_INIT_FILE_NOT_SUPPORTED : t_static_check := f_static_check(C_OK_INIT, C_MSG_INIT);  -- ERROR: g_init_file must be ""

  constant C_STORAGE_DEPTH : positive := 2 ** f_ceil_log2(g_depth);

  type t_mem is array (0 to C_STORAGE_DEPTH - 1) of std_logic_vector(g_data_w - 1 downto 0);

  signal s_mem          : t_mem := (others => (others => '0'));
  signal s_data_rd_a    : std_logic_vector(g_data_w - 1 downto 0);
  signal s_data_rd_a_p  : std_logic_vector(g_data_w - 1 downto 0);
  signal s_data_rd_b    : std_logic_vector(g_data_w - 1 downto 0);
  signal s_data_rd_b_p  : std_logic_vector(g_data_w - 1 downto 0);
begin
  assert false
    report "lm_mem_ram_tdp1w: g_simulation is deprecated and ignored"
    severity note;

  -- The read-output resets are a function selected by a generic, the
  -- exception of docs/coding_style.md: without one, the port process is the
  -- one of v0.2.0, with no reset logic.
  gen_port_a : if not g_a_rst_en generate
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

    -- Simulation only: synthesis tools do not evaluate an assertion on a
    -- signal.
    assert rst_a_n_i /= '0'
      report "lm_mem_ram_tdp1w: rst_a_n_i is low, but the port A read-output reset is disabled (g_a_rst_en = false)"
      severity warning;
  end generate gen_port_a;

  gen_port_a_rst : if g_a_rst_en generate
    proc_port_a_rst : process(clk_a_i)
    begin
      if rising_edge(clk_a_i) then
        if ena_i = '1' then
          if rst_a_n_i = '0' then
            s_data_rd_a   <= (others => '0');
            s_data_rd_a_p <= (others => '0');
          else
            s_data_rd_a   <= s_mem(to_integer(unsigned(addr_a_i)));
            s_data_rd_a_p <= s_data_rd_a;
          end if;
          if wen_a_i = '1' then
            s_mem(to_integer(unsigned(addr_a_i))) <= data_wr_a_i;
          end if;
        end if;
      end if;
    end process proc_port_a_rst;
  end generate gen_port_a_rst;

  gen_port_b : if not g_b_rst_en generate
    proc_port_b : process(clk_b_i)
    begin
      if rising_edge(clk_b_i) then
        if enb_i = '1' then
          s_data_rd_b <= s_mem(to_integer(unsigned(addr_b_i)));
          s_data_rd_b_p <= s_data_rd_b;
        end if;
      end if;
    end process proc_port_b;

    -- Simulation only: synthesis tools do not evaluate an assertion on a
    -- signal.
    assert rst_b_n_i /= '0'
      report "lm_mem_ram_tdp1w: rst_b_n_i is low, but the port B read-output reset is disabled (g_b_rst_en = false)"
      severity warning;
  end generate gen_port_b;

  gen_port_b_rst : if g_b_rst_en generate
    proc_port_b_rst : process(clk_b_i)
    begin
      if rising_edge(clk_b_i) then
        if enb_i = '1' then
          if rst_b_n_i = '0' then
            s_data_rd_b   <= (others => '0');
            s_data_rd_b_p <= (others => '0');
          else
            s_data_rd_b   <= s_mem(to_integer(unsigned(addr_b_i)));
            s_data_rd_b_p <= s_data_rd_b;
          end if;
        end if;
      end if;
    end process proc_port_b_rst;
  end generate gen_port_b_rst;

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
