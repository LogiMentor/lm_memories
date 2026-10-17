-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_tb_pkg
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Testbench package for the RAM harness (VHDL-2008, simulation only).
--
--  t_scoreboard models the memory at the granularity of the narrowest port
--  ("cells") and checks every read against it, applying the collision rules
--  of docs/PLAN.md section 3.5:
--
--  - Two accesses from different ports to overlapping cells collide when
--    their clock edges are at the same instant (common clock) or less than
--    the collision window apart (independent clocks).
--  - Read/write collision: only that read is excluded.
--  - Write/write collision: the cells become undefined; reads of them are
--    excluded until the next write without collision.
--  - A read and a write at the same instant on the same port, or on a
--    common-clock simple dual-port RAM, return the old data (read-first).
--
--  Reads are checked late, once no later write can still collide with them.
--  Every collision raises a warning and is counted.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;

package lm_mem_tb_pkg is
  constant C_TB_DATA_W    : positive := 256;
  constant C_TB_MAX_CELLS : positive := 65536;
  constant C_TB_LOG_LEN   : positive := 256;
  constant C_TB_PEND_LEN  : positive := 64;

  subtype t_tb_data is std_logic_vector(C_TB_DATA_W - 1 downto 0);

  type t_tb_counts is record
    writes           : natural;
    writes_masked    : natural;
    reads            : natural;
    reads_checked    : natural;
    reads_excl_rw    : natural;
    reads_excl_undef : natural;
    coll_rw          : natural;
    coll_ww          : natural;
    cov_init_read    : natural;
    cov_rdw_old_data : natural;
    hold_checks      : natural;
    errors           : natural;
  end record t_tb_counts;

  type t_scoreboard is protected
    procedure configure(
      cell_w          : positive;
      cells           : positive;
      same_clock      : boolean;
      window          : time;
      cross_rdw_valid : boolean
    );
    procedure write(port_id : natural; base : natural; n : positive; data : std_logic_vector; t : time);
    procedure count_masked_write;
    impure function expected(base : natural; n : positive; t : time) return t_tb_data;
    impure function undefined(base : natural; n : positive) return boolean;
    impure function never_written(base : natural; n : positive) return boolean;
    procedure push_read(
      port_id : natural;
      base    : natural;
      n       : positive;
      t       : time;
      exp     : t_tb_data;
      undef   : boolean;
      init    : boolean;
      dut     : std_logic_vector;
      ref     : std_logic_vector;
      tag   : string
    );
    procedure resolve(now_t : time; flush : boolean);
    procedure add_error(msg : string);
    procedure count_hold_check;
    impure function counts return t_tb_counts;
    impure function pending return natural;
  end protected t_scoreboard;

  -- Word queue for FIFO scoreboards.
  type t_tb_queue is protected
    procedure push(data : std_logic_vector);
    impure function pop return t_tb_data;
    impure function size return natural;
  end protected t_tb_queue;

  function f_tb_counts_image(c : t_tb_counts) return string;

  procedure p_tb_rand_slv(
    variable seed1 : inout positive;
    variable seed2 : inout positive;
    variable value : out   std_logic_vector
  );

  procedure p_tb_rand_int(
    variable seed1 : inout positive;
    variable seed2 : inout positive;
    constant low   : in    natural;
    constant high  : in    natural;
    variable value : out   natural
  );

  procedure p_tb_rand_bit(
    variable seed1 : inout positive;
    variable seed2 : inout positive;
    constant prob  : in    real;
    variable value : out   std_logic
  );
end package lm_mem_tb_pkg;

package body lm_mem_tb_pkg is
  function f_tb_counts_image(c : t_tb_counts) return string is
  begin
    return "writes=" & integer'image(c.writes) &
           " writes_masked=" & integer'image(c.writes_masked) &
           " reads=" & integer'image(c.reads) &
           " checked=" & integer'image(c.reads_checked) &
           " excluded_rw=" & integer'image(c.reads_excl_rw) &
           " excluded_undefined=" & integer'image(c.reads_excl_undef) &
           " collisions_rw=" & integer'image(c.coll_rw) &
           " collisions_ww=" & integer'image(c.coll_ww) &
           " init_reads=" & integer'image(c.cov_init_read) &
           " old_data_reads=" & integer'image(c.cov_rdw_old_data) &
           " hold_checks=" & integer'image(c.hold_checks) &
           " errors=" & integer'image(c.errors);
  end function f_tb_counts_image;

  procedure p_tb_rand_slv(
    variable seed1 : inout positive;
    variable seed2 : inout positive;
    variable value : out   std_logic_vector
  ) is
    variable v_r : real;
  begin
    for i in value'range loop
      uniform(seed1, seed2, v_r);
      if v_r < 0.5 then
        value(i) := '0';
      else
        value(i) := '1';
      end if;
    end loop;
  end procedure p_tb_rand_slv;

  procedure p_tb_rand_int(
    variable seed1 : inout positive;
    variable seed2 : inout positive;
    constant low   : in    natural;
    constant high  : in    natural;
    variable value : out   natural
  ) is
    variable v_r : real;
  begin
    uniform(seed1, seed2, v_r);
    value := low + natural(floor(v_r * real(high - low + 1)));
    if value > high then
      value := high;
    end if;
  end procedure p_tb_rand_int;

  procedure p_tb_rand_bit(
    variable seed1 : inout positive;
    variable seed2 : inout positive;
    constant prob  : in    real;
    variable value : out   std_logic
  ) is
    variable v_r : real;
  begin
    uniform(seed1, seed2, v_r);
    if v_r < prob then
      value := '1';
    else
      value := '0';
    end if;
  end procedure p_tb_rand_bit;

  type t_tb_queue is protected body
    constant C_LEN : positive := 65536;
    type t_arr is array (0 to C_LEN - 1) of t_tb_data;
    variable v_arr  : t_arr;
    variable v_head : natural := 0;
    variable v_tail : natural := 0;
    variable v_size : natural := 0;

    procedure push(data : std_logic_vector) is
    begin
      assert v_size < C_LEN
        report "lm_mem_tb_pkg: queue overflow"
        severity failure;
      v_arr(v_tail) := (others => '0');
      v_arr(v_tail)(data'length - 1 downto 0) := data;
      v_tail := (v_tail + 1) mod C_LEN;
      v_size := v_size + 1;
    end procedure push;

    impure function pop return t_tb_data is
      variable v_res : t_tb_data;
    begin
      assert v_size > 0
        report "lm_mem_tb_pkg: queue underflow"
        severity failure;
      v_res  := v_arr(v_head);
      v_head := (v_head + 1) mod C_LEN;
      v_size := v_size - 1;
      return v_res;
    end function pop;

    impure function size return natural is
    begin
      return v_size;
    end function size;
  end protected body t_tb_queue;

  type t_scoreboard is protected body
    type t_cell is record
      cur     : t_tb_data;
      prev    : t_tb_data;
      t_last  : time;
      written : boolean;
      undef   : boolean;
    end record t_cell;

    type t_cells is array (natural range <>) of t_cell;
    type t_cells_ptr is access t_cells;

    type t_log_entry is record
      used    : boolean;
      port_id : natural;
      base    : natural;
      n       : natural;
      t       : time;
    end record t_log_entry;

    type t_log is array (0 to C_TB_LOG_LEN - 1) of t_log_entry;

    type t_pending is record
      used    : boolean;
      port_id : natural;
      base    : natural;
      n       : natural;
      t       : time;
      exp     : t_tb_data;
      undef   : boolean;
      init    : boolean;
      dut     : t_tb_data;
      ref     : t_tb_data;
      width   : natural;
      tag   : string(1 to 16);
    end record t_pending;

    type t_pending_arr is array (0 to C_TB_PEND_LEN - 1) of t_pending;

    variable v_cell_w     : positive := 1;
    variable v_n_cells    : positive := 1;
    variable v_same_clock : boolean := true;
    variable v_window     : time := 0 ns;
    variable v_cross_rdw  : boolean := false;
    variable v_cells      : t_cells_ptr := null;
    variable v_log        : t_log;
    variable v_log_wr     : natural := 0;
    variable v_pend       : t_pending_arr;
    variable v_counts     : t_tb_counts := (others => 0);

    function f_overlap(a_base, a_n, b_base, b_n : natural) return boolean is
    begin
      return (a_base < b_base + b_n) and (b_base < a_base + a_n);
    end function f_overlap;

    impure function f_collide(t1, t2 : time) return boolean is
    begin
      if v_same_clock then
        return t1 = t2;
      elsif t1 >= t2 then
        return (t1 - t2) < v_window;
      else
        return (t2 - t1) < v_window;
      end if;
    end function f_collide;

    procedure configure(
      cell_w          : positive;
      cells           : positive;
      same_clock      : boolean;
      window          : time;
      cross_rdw_valid : boolean
    ) is
    begin
      assert cells <= C_TB_MAX_CELLS
        report "lm_mem_tb_pkg: too many cells for the scoreboard"
        severity failure;
      v_cell_w     := cell_w;
      v_n_cells    := cells;
      v_same_clock := same_clock;
      v_window     := window;
      v_cross_rdw  := cross_rdw_valid;
      if v_cells /= null then
        deallocate(v_cells);
      end if;
      v_cells := new t_cells(0 to cells - 1);
      for i in 0 to cells - 1 loop
        v_cells(i).cur     := (others => '0');
        v_cells(i).prev    := (others => '0');
        v_cells(i).t_last  := -1 ns;
        v_cells(i).written := false;
        v_cells(i).undef   := false;
      end loop;
      for i in v_log'range loop
        v_log(i).used := false;
      end loop;
      for i in v_pend'range loop
        v_pend(i).used := false;
      end loop;
      v_counts := (others => 0);
    end procedure configure;

    procedure write(port_id : natural; base : natural; n : positive; data : std_logic_vector; t : time) is
      variable v_data : std_logic_vector(data'length - 1 downto 0) := data;
      variable v_ww   : boolean := false;
    begin
      v_counts.writes := v_counts.writes + 1;
      for i in v_log'range loop
        if v_log(i).used and (v_log(i).port_id /= port_id) and
           f_overlap(v_log(i).base, v_log(i).n, base, n) and f_collide(v_log(i).t, t) then
          v_ww := true;
        end if;
      end loop;
      if v_ww then
        v_counts.coll_ww := v_counts.coll_ww + 1;
        report "lm_mem_tb_pkg: write/write collision on cells " & integer'image(base) &
               " to " & integer'image(base + n - 1) & "; the cells become undefined"
          severity warning;
      end if;
      for i in 0 to n - 1 loop
        if base + i < v_n_cells then
          if v_cells(base + i).t_last /= t then
            v_cells(base + i).prev := v_cells(base + i).cur;
          end if;
          v_cells(base + i).cur := (others => '0');
          v_cells(base + i).cur(v_cell_w - 1 downto 0) := v_data((i + 1) * v_cell_w - 1 downto i * v_cell_w);
          v_cells(base + i).t_last  := t;
          v_cells(base + i).written := true;
          v_cells(base + i).undef   := v_ww;
        end if;
      end loop;
      v_log(v_log_wr) := (used => true, port_id => port_id, base => base, n => n, t => t);
      v_log_wr := (v_log_wr + 1) mod C_TB_LOG_LEN;
    end procedure write;

    procedure count_masked_write is
    begin
      v_counts.writes_masked := v_counts.writes_masked + 1;
    end procedure count_masked_write;

    impure function expected(base : natural; n : positive; t : time) return t_tb_data is
      variable v_res : t_tb_data := (others => '0');
    begin
      for i in 0 to n - 1 loop
        if base + i < v_n_cells then
          if v_cells(base + i).t_last = t then
            v_res((i + 1) * v_cell_w - 1 downto i * v_cell_w) := v_cells(base + i).prev(v_cell_w - 1 downto 0);
          else
            v_res((i + 1) * v_cell_w - 1 downto i * v_cell_w) := v_cells(base + i).cur(v_cell_w - 1 downto 0);
          end if;
        end if;
      end loop;
      return v_res;
    end function expected;

    impure function undefined(base : natural; n : positive) return boolean is
    begin
      for i in 0 to n - 1 loop
        if (base + i < v_n_cells) and v_cells(base + i).undef then
          return true;
        end if;
      end loop;
      return false;
    end function undefined;

    impure function never_written(base : natural; n : positive) return boolean is
    begin
      for i in 0 to n - 1 loop
        if (base + i < v_n_cells) and v_cells(base + i).written then
          return false;
        end if;
      end loop;
      return true;
    end function never_written;

    procedure push_read(
      port_id : natural;
      base    : natural;
      n       : positive;
      t       : time;
      exp     : t_tb_data;
      undef   : boolean;
      init    : boolean;
      dut     : std_logic_vector;
      ref     : std_logic_vector;
      tag   : string
    ) is
      variable v_slot : integer := -1;
      variable v_lbl  : string(1 to 16) := (others => ' ');
    begin
      for i in v_pend'range loop
        if (not v_pend(i).used) and (v_slot < 0) then
          v_slot := i;
        end if;
      end loop;
      assert v_slot >= 0
        report "lm_mem_tb_pkg: pending read queue overflow"
        severity failure;
      for i in 1 to tag'length loop
        if i <= v_lbl'length then
          v_lbl(i) := tag(tag'low + i - 1);
        end if;
      end loop;
      v_pend(v_slot).used    := true;
      v_pend(v_slot).port_id := port_id;
      v_pend(v_slot).base    := base;
      v_pend(v_slot).n       := n;
      v_pend(v_slot).t       := t;
      v_pend(v_slot).exp     := exp;
      v_pend(v_slot).undef   := undef;
      v_pend(v_slot).init    := init;
      v_pend(v_slot).width   := dut'length;
      v_pend(v_slot).dut     := (others => '0');
      v_pend(v_slot).ref     := (others => '0');
      v_pend(v_slot).dut(dut'length - 1 downto 0) := dut;
      v_pend(v_slot).ref(ref'length - 1 downto 0) := ref;
      v_pend(v_slot).tag   := v_lbl;
      v_counts.reads := v_counts.reads + 1;
    end procedure push_read;

    procedure resolve(now_t : time; flush : boolean) is
      variable v_ready : boolean;
      variable v_rw    : boolean;
      variable v_old   : boolean;
      variable v_w     : natural;
    begin
      for i in v_pend'range loop
        if v_pend(i).used then
          if v_same_clock then
            v_ready := now_t > v_pend(i).t;
          else
            v_ready := now_t >= v_pend(i).t + v_window;
          end if;
          if v_ready or flush then
            v_pend(i).used := false;
            v_rw := false;
            v_old := false;
            -- A write at the same instant as the read: the read returns the
            -- old data (same port, or common-clock simple dual port). Taken
            -- from the log, so it does not depend on process order.
            for j in v_log'range loop
              if v_log(j).used and (v_log(j).t = v_pend(i).t) and
                 f_overlap(v_log(j).base, v_log(j).n, v_pend(i).base, v_pend(i).n) then
                v_old := true;
              end if;
            end loop;
            if not v_cross_rdw then
              for j in v_log'range loop
                if v_log(j).used and (v_log(j).port_id /= v_pend(i).port_id) and
                   f_overlap(v_log(j).base, v_log(j).n, v_pend(i).base, v_pend(i).n) and
                   f_collide(v_log(j).t, v_pend(i).t) then
                  v_rw := true;
                end if;
              end loop;
            end if;
            v_w := v_pend(i).width;
            if v_rw then
              v_counts.coll_rw := v_counts.coll_rw + 1;
              v_counts.reads_excl_rw := v_counts.reads_excl_rw + 1;
              report "lm_mem_tb_pkg: read/write collision on " & v_pend(i).tag &
                     " cell " & integer'image(v_pend(i).base) & "; read excluded"
                severity warning;
            elsif v_pend(i).undef then
              v_counts.reads_excl_undef := v_counts.reads_excl_undef + 1;
            else
              v_counts.reads_checked := v_counts.reads_checked + 1;
              if v_pend(i).init then
                v_counts.cov_init_read := v_counts.cov_init_read + 1;
              end if;
              if v_old then
                v_counts.cov_rdw_old_data := v_counts.cov_rdw_old_data + 1;
              end if;
              if v_pend(i).dut(v_w - 1 downto 0) /= v_pend(i).exp(v_w - 1 downto 0) then
                v_counts.errors := v_counts.errors + 1;
                report "lm_mem_tb_pkg: " & v_pend(i).tag & " read at " & time'image(v_pend(i).t) &
                       " cell " & integer'image(v_pend(i).base) &
                       ": DUT " & to_hstring(v_pend(i).dut(v_w - 1 downto 0)) &
                       " expected " & to_hstring(v_pend(i).exp(v_w - 1 downto 0))
                  severity error;
              end if;
              if v_pend(i).dut(v_w - 1 downto 0) /= v_pend(i).ref(v_w - 1 downto 0) then
                v_counts.errors := v_counts.errors + 1;
                report "lm_mem_tb_pkg: " & v_pend(i).tag & " read at " & time'image(v_pend(i).t) &
                       " cell " & integer'image(v_pend(i).base) &
                       ": DUT " & to_hstring(v_pend(i).dut(v_w - 1 downto 0)) &
                       " reference model " & to_hstring(v_pend(i).ref(v_w - 1 downto 0))
                  severity error;
              end if;
            end if;
          end if;
        end if;
      end loop;
    end procedure resolve;

    procedure add_error(msg : string) is
    begin
      v_counts.errors := v_counts.errors + 1;
      report "lm_mem_tb_pkg: " & msg severity error;
    end procedure add_error;

    procedure count_hold_check is
    begin
      v_counts.hold_checks := v_counts.hold_checks + 1;
    end procedure count_hold_check;

    impure function counts return t_tb_counts is
    begin
      return v_counts;
    end function counts;

    impure function pending return natural is
      variable v_n : natural := 0;
    begin
      for i in v_pend'range loop
        if v_pend(i).used then
          v_n := v_n + 1;
        end if;
      end loop;
      return v_n;
    end function pending;
  end protected body t_scoreboard;
end package body lm_mem_tb_pkg;
