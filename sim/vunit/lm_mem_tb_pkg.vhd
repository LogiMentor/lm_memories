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
--  ("cells") and checks every read against it, applying the rules of
--  docs/PLAN.md section 3.5:
--
--  - Two accesses from different ports to overlapping cells collide when
--    their clock edges are at the same instant (common clock) or less than
--    the collision window apart (independent clocks).
--  - Read/write collision: only that read is excluded.
--  - Write/write collision: every cell of both writes becomes undefined (the
--    whole word of each port, also where the words only partly overlap);
--    reads of them are excluded until the next write without collision.
--  - A read and a write at the same instant on the same port, or on a
--    common-clock simple dual-port RAM, return the old data (read-first).
--  - An access at or beyond the depth: a write is ignored by the model, a
--    read is excluded. Both raise a warning and are counted.
--  - Optionally (x_on_undef), a read of a cell left undefined by a
--    write/write collision must return 'X' somewhere in the read word. This
--    checks the collision handling of a simulation model; a netlist never
--    returns 'X'.
--
--  Independence from process order: monitors of different ports may run in
--  any order within a delta cycle. Every decision therefore uses either the
--  state of a cell before the current instant (each cell keeps its state
--  from before the last write instant) or the write log, inspected only once
--  no later write can still collide. Reads are resolved late for that reason.
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
    writes_oor       : natural;
    reads            : natural;
    reads_checked    : natural;
    reads_excl_rw    : natural;
    reads_excl_undef : natural;
    reads_excl_oor   : natural;
    coll_rw          : natural;
    coll_ww          : natural;
    cov_init_read    : natural;
    cov_rdw_old_data : natural;
    hold_checks      : natural;
    undef_out_checks : natural;
    errors           : natural;
  end record t_tb_counts;

  -- State of a read when it is issued (before any write at the same instant).
  type t_tb_read is record
    valid : boolean;
    base  : natural;
    n     : natural;
    t     : time;
    exp   : t_tb_data;
    undef : boolean;
    init  : boolean;
    oor   : boolean;
  end record t_tb_read;

  constant C_TB_NO_READ : t_tb_read := (
    valid => false, base => 0, n => 0, t => 0 ns, exp => (others => '0'),
    undef => false, init => false, oor => false
  );

  type t_scoreboard is protected
    procedure configure(
      cell_w          : positive;
      cells           : positive;
      same_clock      : boolean;
      window          : time;
      cross_rdw_valid : boolean;
      x_on_undef      : boolean := false
    );
    procedure write(port_id : natural; base : natural; n : positive; data : std_logic_vector; t : time);
    procedure count_masked_write;
    impure function snapshot(base : natural; n : positive; t : time) return t_tb_read;
    procedure push_read(
      port_id : natural;
      rd      : t_tb_read;
      dut     : std_logic_vector;
      ref     : std_logic_vector;
      tag     : string
    );
    procedure resolve(now_t : time; flush : boolean);
    procedure add_error(msg : string);
    procedure count_hold_check;
    procedure count_undef_output_check;
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

  -- True when every bit is 'U' or 'X' (an output no read has reached yet).
  function f_tb_all_undef(v : std_logic_vector) return boolean;

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
           " writes_beyond_depth=" & integer'image(c.writes_oor) &
           " reads=" & integer'image(c.reads) &
           " checked=" & integer'image(c.reads_checked) &
           " excluded_rw=" & integer'image(c.reads_excl_rw) &
           " excluded_undefined=" & integer'image(c.reads_excl_undef) &
           " excluded_beyond_depth=" & integer'image(c.reads_excl_oor) &
           " collisions_rw=" & integer'image(c.coll_rw) &
           " collisions_ww=" & integer'image(c.coll_ww) &
           " init_reads=" & integer'image(c.cov_init_read) &
           " old_data_reads=" & integer'image(c.cov_rdw_old_data) &
           " hold_checks=" & integer'image(c.hold_checks) &
           " undefined_output_checks=" & integer'image(c.undef_out_checks) &
           " errors=" & integer'image(c.errors);
  end function f_tb_counts_image;

  function f_tb_all_undef(v : std_logic_vector) return boolean is
  begin
    for i in v'range loop
      if (v(i) /= 'U') and (v(i) /= 'X') then
        return false;
      end if;
    end loop;
    return true;
  end function f_tb_all_undef;

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
    -- cur/written/undef: state after the last write instant t_last.
    -- prev/prev_written/prev_undef: state before t_last.
    type t_cell is record
      cur          : t_tb_data;
      prev         : t_tb_data;
      t_last       : time;
      written      : boolean;
      prev_written : boolean;
      undef        : boolean;
      prev_undef   : boolean;
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
      rd      : t_tb_read;
      dut     : t_tb_data;
      ref     : t_tb_data;
      width   : natural;
      tag     : string(1 to 16);
    end record t_pending;

    type t_pending_arr is array (0 to C_TB_PEND_LEN - 1) of t_pending;

    variable v_cell_w     : positive := 1;
    variable v_n_cells    : positive := 1;
    variable v_same_clock : boolean := true;
    variable v_window     : time := 0 ns;
    variable v_cross_rdw  : boolean := false;
    variable v_x_on_undef : boolean := false;
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

    impure function f_beyond_depth(base : natural; n : natural) return boolean is
    begin
      return base + n > v_n_cells;
    end function f_beyond_depth;

    procedure configure(
      cell_w          : positive;
      cells           : positive;
      same_clock      : boolean;
      window          : time;
      cross_rdw_valid : boolean;
      x_on_undef      : boolean := false
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
      v_x_on_undef := x_on_undef;
      if v_cells /= null then
        deallocate(v_cells);
      end if;
      v_cells := new t_cells(0 to cells - 1);
      for i in 0 to cells - 1 loop
        v_cells(i) := (
          cur => (others => '0'), prev => (others => '0'), t_last => -1 ns,
          written => false, prev_written => false, undef => false, prev_undef => false
        );
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
      variable v_any  : boolean := false;
      variable v_c    : natural;
    begin
      v_counts.writes := v_counts.writes + 1;
      if f_beyond_depth(base, n) then
        v_counts.writes_oor := v_counts.writes_oor + 1;
        report "lm_mem_tb_pkg: write beyond the depth (cell " & integer'image(base) &
               "); ignored by the model"
          severity warning;
        return;
      end if;
      -- Write/write collision: a write of the other port in the window whose
      -- cells overlap this write. Every cell of both writes becomes undefined,
      -- not only the overlapping ones: with mixed widths, vendor models
      -- corrupt the whole word of each port (UltraScale+ RAMB36E2). Cells of
      -- the earlier write are marked only while that write is still their
      -- last one. The result is the same whichever write is processed second.
      for j in v_log'range loop
        if v_log(j).used and (v_log(j).port_id /= port_id) and
           f_overlap(v_log(j).base, v_log(j).n, base, n) and f_collide(v_log(j).t, t) then
          v_any := true;
          for k in 0 to v_log(j).n - 1 loop
            v_c := v_log(j).base + k;
            if (not f_overlap(v_c, 1, base, n)) and (v_cells(v_c).t_last = v_log(j).t) then
              v_cells(v_c).undef := true;
            end if;
          end loop;
        end if;
      end loop;
      for i in 0 to n - 1 loop
        v_c := base + i;
        if v_cells(v_c).t_last /= t then
          v_cells(v_c).prev         := v_cells(v_c).cur;
          v_cells(v_c).prev_written := v_cells(v_c).written;
          v_cells(v_c).prev_undef   := v_cells(v_c).undef;
        end if;
        v_cells(v_c).cur := (others => '0');
        v_cells(v_c).cur(v_cell_w - 1 downto 0) := v_data((i + 1) * v_cell_w - 1 downto i * v_cell_w);
        v_cells(v_c).t_last  := t;
        v_cells(v_c).written := true;
        v_cells(v_c).undef   := v_any;
      end loop;
      if v_any then
        v_counts.coll_ww := v_counts.coll_ww + 1;
        report "lm_mem_tb_pkg: write/write collision on cells " & integer'image(base) &
               " to " & integer'image(base + n - 1) & "; the cells of both writes become undefined"
          severity warning;
      end if;
      v_log(v_log_wr) := (used => true, port_id => port_id, base => base, n => n, t => t);
      v_log_wr := (v_log_wr + 1) mod C_TB_LOG_LEN;
    end procedure write;

    procedure count_masked_write is
    begin
      v_counts.writes_masked := v_counts.writes_masked + 1;
    end procedure count_masked_write;

    impure function snapshot(base : natural; n : positive; t : time) return t_tb_read is
      variable v_res : t_tb_read := C_TB_NO_READ;
      variable v_c   : natural;
      variable v_val : t_tb_data;
      variable v_wr  : boolean;
      variable v_und : boolean;
    begin
      v_res.valid := true;
      v_res.base  := base;
      v_res.n     := n;
      v_res.t     := t;
      v_res.oor   := f_beyond_depth(base, n);
      v_res.init  := true;
      if not v_res.oor then
        for i in 0 to n - 1 loop
          v_c := base + i;
          -- State before any write at instant t, whatever the process order.
          if v_cells(v_c).t_last = t then
            v_val := v_cells(v_c).prev;
            v_wr  := v_cells(v_c).prev_written;
            v_und := v_cells(v_c).prev_undef;
          else
            v_val := v_cells(v_c).cur;
            v_wr  := v_cells(v_c).written;
            v_und := v_cells(v_c).undef;
          end if;
          v_res.exp((i + 1) * v_cell_w - 1 downto i * v_cell_w) := v_val(v_cell_w - 1 downto 0);
          v_res.init  := v_res.init and not v_wr;
          v_res.undef := v_res.undef or v_und;
        end loop;
      else
        v_res.init := false;
      end if;
      return v_res;
    end function snapshot;

    procedure push_read(
      port_id : natural;
      rd      : t_tb_read;
      dut     : std_logic_vector;
      ref     : std_logic_vector;
      tag     : string
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
      v_pend(v_slot).rd      := rd;
      v_pend(v_slot).width   := dut'length;
      v_pend(v_slot).dut     := (others => '0');
      v_pend(v_slot).ref     := (others => '0');
      v_pend(v_slot).dut(dut'length - 1 downto 0) := dut;
      v_pend(v_slot).ref(ref'length - 1 downto 0) := ref;
      v_pend(v_slot).tag     := v_lbl;
      v_counts.reads := v_counts.reads + 1;
    end procedure push_read;

    procedure resolve(now_t : time; flush : boolean) is
      variable v_ready : boolean;
      variable v_rw    : boolean;
      variable v_old   : boolean;
      variable v_w     : natural;
      variable v_rd    : t_tb_read;
    begin
      for i in v_pend'range loop
        if v_pend(i).used then
          v_rd := v_pend(i).rd;
          -- Ready once no write that is still to come can collide with it.
          if v_same_clock then
            v_ready := now_t > v_rd.t;
          else
            v_ready := now_t >= v_rd.t + v_window;
          end if;
          if v_ready or flush then
            v_pend(i).used := false;
            v_rw  := false;
            v_old := false;
            if not v_rd.oor then
              for j in v_log'range loop
                if v_log(j).used and f_overlap(v_log(j).base, v_log(j).n, v_rd.base, v_rd.n) then
                  -- A write at the same instant: the read returns the old data
                  -- (same port, or common-clock simple dual port).
                  if v_log(j).t = v_rd.t then
                    v_old := true;
                  end if;
                  if (not v_cross_rdw) and (v_log(j).port_id /= v_pend(i).port_id) and
                     f_collide(v_log(j).t, v_rd.t) then
                    v_rw := true;
                  end if;
                end if;
              end loop;
            end if;
            v_w := v_pend(i).width;
            if v_rd.oor then
              v_counts.reads_excl_oor := v_counts.reads_excl_oor + 1;
              report "lm_mem_tb_pkg: " & v_pend(i).tag & " read beyond the depth (cell " &
                     integer'image(v_rd.base) & "); read excluded"
                severity warning;
            elsif v_rw then
              v_counts.coll_rw := v_counts.coll_rw + 1;
              v_counts.reads_excl_rw := v_counts.reads_excl_rw + 1;
              report "lm_mem_tb_pkg: read/write collision on " & v_pend(i).tag &
                     " cell " & integer'image(v_rd.base) & "; read excluded"
                severity warning;
            elsif v_rd.undef then
              v_counts.reads_excl_undef := v_counts.reads_excl_undef + 1;
              if v_x_on_undef and not Is_X(v_pend(i).dut(v_w - 1 downto 0)) then
                v_counts.errors := v_counts.errors + 1;
                report "lm_mem_tb_pkg: " & v_pend(i).tag & " read at " & time'image(v_rd.t) &
                       " cell " & integer'image(v_rd.base) &
                       ": undefined after a write/write collision, but the DUT returned " &
                       to_hstring(v_pend(i).dut(v_w - 1 downto 0))
                  severity error;
              end if;
            else
              v_counts.reads_checked := v_counts.reads_checked + 1;
              if v_rd.init then
                v_counts.cov_init_read := v_counts.cov_init_read + 1;
              end if;
              if v_old then
                v_counts.cov_rdw_old_data := v_counts.cov_rdw_old_data + 1;
              end if;
              if v_pend(i).dut(v_w - 1 downto 0) /= v_rd.exp(v_w - 1 downto 0) then
                v_counts.errors := v_counts.errors + 1;
                report "lm_mem_tb_pkg: " & v_pend(i).tag & " read at " & time'image(v_rd.t) &
                       " cell " & integer'image(v_rd.base) &
                       ": DUT " & to_hstring(v_pend(i).dut(v_w - 1 downto 0)) &
                       " expected " & to_hstring(v_rd.exp(v_w - 1 downto 0))
                  severity error;
              end if;
              if v_pend(i).dut(v_w - 1 downto 0) /= v_pend(i).ref(v_w - 1 downto 0) then
                v_counts.errors := v_counts.errors + 1;
                report "lm_mem_tb_pkg: " & v_pend(i).tag & " read at " & time'image(v_rd.t) &
                       " cell " & integer'image(v_rd.base) &
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

    procedure count_undef_output_check is
    begin
      v_counts.undef_out_checks := v_counts.undef_out_checks + 1;
    end procedure count_undef_output_check;

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
