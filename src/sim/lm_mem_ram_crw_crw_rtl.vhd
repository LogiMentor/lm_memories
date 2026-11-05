-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_crw_crw (simulation architecture)
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Simulation architecture (reference model) of the dual-model entity
--  lm_mem_ram_crw_crw (src/core/lm_mem_ram_crw_crw_ent.vhd). Not
--  synthesizable: it uses now. Synthesis flows compile a vendor architecture
--  instead (manifests/lm_memories.toml).
--
--  - One process for both clocks. Both ports are handled in the same process
--    execution when their edges fall in the same delta cycle, so no result
--    depends on the order in which a simulator runs processes.
--  - The model works on cells of the narrower width. A wide access covers
--    cells address * ratio + i, the same as address & i.
--  - Read-first on each port: reads are evaluated before the writes of the
--    same edge.
--  - Collision window: the period of the slower clock, measured from the clock
--    edges. Two accesses from different ports to the same cell collide when
--    their edges are less than one window apart, or at the same instant
--    (common clock, or before the periods are known).
--  - Write/write collision: warning; every cell of both writes becomes
--    undefined ('X'), the whole word of each port also where the words only
--    partly overlap, until the next write to it without collision.
--  - Read/write collision: warning. A read whose colliding write is at the
--    same instant or earlier returns 'X' for the colliding cells. A read whose
--    colliding write comes later was already delivered; it only raises the
--    warning.
--  - Access at or beyond the depth: warning; a write is ignored and a read
--    returns 'X'.
--  - Read outputs are 'X' until the first enabled read reaches them (no
--    initial value in the synthesis architectures).
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.lm_mem_pkg.all;

architecture a_rtl of lm_mem_ram_crw_crw is
  constant C_MIN_WIDTH : positive := f_min(g_ram_a_data_w, g_ram_b_data_w);
  constant C_MAX_WIDTH : positive := f_max(g_ram_a_data_w, g_ram_b_data_w);
  constant C_RATIO     : positive := C_MAX_WIDTH / C_MIN_WIDTH;

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

  constant C_A_WORDS   : positive := g_ram_a_data_w / C_MIN_WIDTH;
  constant C_B_WORDS   : positive := g_ram_b_data_w / C_MIN_WIDTH;
  constant C_CELLS     : positive := g_ram_a_depth * C_A_WORDS;
  constant C_NEVER     : time     := -1 hr;

  type t_mem is array (0 to C_CELLS - 1) of std_logic_vector(C_MIN_WIDTH - 1 downto 0);
  type t_times is array (0 to C_CELLS - 1) of time;
  type t_flags is array (0 to C_CELLS - 1) of boolean;

  signal s_data_rd_a   : std_logic_vector(g_ram_a_data_w - 1 downto 0) := (others => 'X');
  signal s_data_rd_b   : std_logic_vector(g_ram_b_data_w - 1 downto 0) := (others => 'X');
  signal s_data_rd_a_p : std_logic_vector(g_ram_a_data_w - 1 downto 0) := (others => 'X');
  signal s_data_rd_b_p : std_logic_vector(g_ram_b_data_w - 1 downto 0) := (others => 'X');

  function f_overlap(a_base, a_n, b_base, b_n : natural) return boolean is
  begin
    return (a_base < b_base + b_n) and (b_base < a_base + a_n);
  end function f_overlap;
begin
  assert false
    report "lm_mem_ram_crw_crw: g_simulation is deprecated and ignored"
    severity note;

  proc_mem : process(clk_a_i, clk_b_i)
    variable v_mem      : t_mem   := (others => (others => '0'));
    variable v_undef    : t_flags := (others => false);
    variable v_wr_t_a   : t_times := (others => C_NEVER);
    variable v_wr_t_b   : t_times := (others => C_NEVER);
    variable v_rd_t_a   : t_times := (others => C_NEVER);
    variable v_rd_t_b   : t_times := (others => C_NEVER);
    variable v_last_a   : time    := C_NEVER;
    variable v_last_b   : time    := C_NEVER;
    variable v_per_a    : time    := 0 fs;
    variable v_per_b    : time    := 0 fs;
    variable v_window   : time;
    variable v_edge_a   : boolean;
    variable v_edge_b   : boolean;
    variable v_rd_a     : std_logic_vector(g_ram_a_data_w - 1 downto 0);
    variable v_rd_b     : std_logic_vector(g_ram_b_data_w - 1 downto 0);
    variable v_rd_a_on  : boolean;
    variable v_rd_b_on  : boolean;
    variable v_wr_a_on  : boolean;
    variable v_wr_b_on  : boolean;
    variable v_base_a   : natural;
    variable v_base_b   : natural;
    variable v_c        : natural;
    variable v_hit      : boolean;
    variable v_w        : natural;

    impure function f_collide(t : time) return boolean is
    begin
      return (t = now) or ((now - t) < v_window);
    end function f_collide;
  begin
    v_edge_a := rising_edge(clk_a_i);
    v_edge_b := rising_edge(clk_b_i);

    -- Collision window: period of the slower clock, once measured.
    if v_edge_a then
      if v_last_a /= C_NEVER then
        v_per_a := now - v_last_a;
      end if;
      v_last_a := now;
    end if;
    if v_edge_b then
      if v_last_b /= C_NEVER then
        v_per_b := now - v_last_b;
      end if;
      v_last_b := now;
    end if;
    if v_per_a > v_per_b then
      v_window := v_per_a;
    else
      v_window := v_per_b;
    end if;

    v_rd_a_on := v_edge_a and (ena_i = '1');
    v_rd_b_on := v_edge_b and (enb_i = '1');
    v_wr_a_on := v_rd_a_on and (wen_a_i = '1');
    v_wr_b_on := v_rd_b_on and (wen_b_i = '1');
    v_base_a  := to_integer(unsigned(addr_a_i)) * C_A_WORDS;
    v_base_b  := to_integer(unsigned(addr_b_i)) * C_B_WORDS;

    -- Reads: state before the writes of this edge (read-first).
    if v_rd_a_on then
      for i in 0 to C_A_WORDS - 1 loop
        v_c := v_base_a + i;
        if v_c >= C_CELLS then
          v_rd_a((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := (others => 'X');
          assert false
            report "lm_mem_ram_crw_crw: port A read beyond the depth; data undefined"
            severity warning;
        elsif f_collide(v_wr_t_b(v_c)) then
          v_rd_a((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := (others => 'X');
          assert false
            report "lm_mem_ram_crw_crw: port A read collides with a port B write; data undefined"
            severity warning;
        elsif v_undef(v_c) then
          v_rd_a((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := (others => 'X');
        else
          v_rd_a((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := v_mem(v_c);
        end if;
      end loop;
    end if;

    if v_rd_b_on then
      for i in 0 to C_B_WORDS - 1 loop
        v_c := v_base_b + i;
        if v_c >= C_CELLS then
          v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := (others => 'X');
          assert false
            report "lm_mem_ram_crw_crw: port B read beyond the depth; data undefined"
            severity warning;
        elsif f_collide(v_wr_t_a(v_c)) then
          v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := (others => 'X');
          assert false
            report "lm_mem_ram_crw_crw: port B read collides with a port A write; data undefined"
            severity warning;
        elsif v_undef(v_c) then
          v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := (others => 'X');
        else
          v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := v_mem(v_c);
        end if;
      end loop;
    end if;

    -- Writes. A read of the other port earlier in the window was already
    -- delivered: warning only.
    -- Write/write collision: a port B write in the window that overlaps this
    -- port A write. Every cell of both writes becomes undefined: the whole
    -- port A word, and the whole port B word of each overlapping cell while
    -- that B write is still its last one.
    if v_wr_a_on then
      v_hit := false;
      for i in 0 to C_A_WORDS - 1 loop
        v_c := v_base_a + i;
        if (v_c < C_CELLS) and f_collide(v_wr_t_b(v_c)) then
          v_hit := true;
          v_w := (v_c / C_B_WORDS) * C_B_WORDS;
          for k in 0 to C_B_WORDS - 1 loop
            if (v_w + k < C_CELLS) and (v_wr_t_b(v_w + k) = v_wr_t_b(v_c)) then
              v_undef(v_w + k) := true;
            end if;
          end loop;
        end if;
      end loop;
      assert not v_hit
        report "lm_mem_ram_crw_crw: port A and port B writes collide; data undefined"
        severity warning;
      for i in 0 to C_A_WORDS - 1 loop
        v_c := v_base_a + i;
        if v_c >= C_CELLS then
          assert false
            report "lm_mem_ram_crw_crw: port A write beyond the depth; ignored"
            severity warning;
        else
          assert not f_collide(v_rd_t_b(v_c))
            report "lm_mem_ram_crw_crw: port A write collides with an earlier port B read"
            severity warning;
          v_mem(v_c)    := data_wr_a_i((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH);
          v_undef(v_c)  := v_hit;
          v_wr_t_a(v_c) := now;
        end if;
      end loop;
    end if;

    if v_wr_b_on then
      v_hit := false;
      for i in 0 to C_B_WORDS - 1 loop
        v_c := v_base_b + i;
        if (v_c < C_CELLS) and f_collide(v_wr_t_a(v_c)) then
          v_hit := true;
          v_w := (v_c / C_A_WORDS) * C_A_WORDS;
          for k in 0 to C_A_WORDS - 1 loop
            if (v_w + k < C_CELLS) and (v_wr_t_a(v_w + k) = v_wr_t_a(v_c)) then
              v_undef(v_w + k) := true;
            end if;
          end loop;
        end if;
      end loop;
      assert not v_hit
        report "lm_mem_ram_crw_crw: port A and port B writes collide; data undefined"
        severity warning;
      for i in 0 to C_B_WORDS - 1 loop
        v_c := v_base_b + i;
        if v_c >= C_CELLS then
          assert false
            report "lm_mem_ram_crw_crw: port B write beyond the depth; ignored"
            severity warning;
        else
          assert not f_collide(v_rd_t_a(v_c))
            report "lm_mem_ram_crw_crw: port B write collides with an earlier port A read"
            severity warning;
          v_mem(v_c)    := data_wr_b_i((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH);
          v_undef(v_c)  := v_hit;
          v_wr_t_b(v_c) := now;
        end if;
      end loop;
    end if;

    -- Reads of this edge that collide with a write of the other port at the
    -- same edge (handled above only for writes of earlier executions).
    if v_rd_a_on and v_wr_b_on and f_overlap(v_base_a, C_A_WORDS, v_base_b, C_B_WORDS) then
      for i in 0 to C_A_WORDS - 1 loop
        if f_overlap(v_base_a + i, 1, v_base_b, C_B_WORDS) then
          v_rd_a((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := (others => 'X');
        end if;
      end loop;
      assert false
        report "lm_mem_ram_crw_crw: port A read collides with a port B write; data undefined"
        severity warning;
    end if;

    if v_rd_b_on and v_wr_a_on and f_overlap(v_base_b, C_B_WORDS, v_base_a, C_A_WORDS) then
      for i in 0 to C_B_WORDS - 1 loop
        if f_overlap(v_base_b + i, 1, v_base_a, C_A_WORDS) then
          v_rd_b((i + 1) * C_MIN_WIDTH - 1 downto i * C_MIN_WIDTH) := (others => 'X');
        end if;
      end loop;
      assert false
        report "lm_mem_ram_crw_crw: port B read collides with a port A write; data undefined"
        severity warning;
    end if;

    -- Record the reads after the writes, so that a write of this edge does not
    -- see the read of this edge as an earlier one.
    if v_rd_a_on then
      for i in 0 to C_A_WORDS - 1 loop
        if v_base_a + i < C_CELLS then
          v_rd_t_a(v_base_a + i) := now;
        end if;
      end loop;
      s_data_rd_a   <= v_rd_a;
      s_data_rd_a_p <= s_data_rd_a;
    end if;

    if v_rd_b_on then
      for i in 0 to C_B_WORDS - 1 loop
        if v_base_b + i < C_CELLS then
          v_rd_t_b(v_base_b + i) := now;
        end if;
      end loop;
      s_data_rd_b   <= v_rd_b;
      s_data_rd_b_p <= s_data_rd_b;
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
