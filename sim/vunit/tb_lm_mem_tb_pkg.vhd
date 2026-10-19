-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : tb_lm_mem_tb_pkg
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  VUnit unit test of the scoreboard in lm_mem_tb_pkg. It calls the
--  scoreboard directly, so the order of the calls at one instant is chosen by
--  the test, not by the simulator: each case runs the same calls on two
--  scoreboards, once with the read before the write of that instant and once
--  after it, and requires the same result. In the harness this order is the
--  order in which a simulator runs the monitors of the two ports.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

library vunit_lib;
context vunit_lib.vunit_context;

use work.lm_mem_tb_pkg.all;

entity tb_lm_mem_tb_pkg is
  generic(
    runner_cfg : string
  );
end entity tb_lm_mem_tb_pkg;

architecture a_tb of tb_lm_mem_tb_pkg is
  constant C_PORT_A : natural := 0;
  constant C_PORT_B : natural := 1;
  constant C_WORD_A : std_logic_vector(31 downto 0) := x"44332211";
  constant C_CELL_B : std_logic_vector(7 downto 0)  := x"AA";

  -- sv_first: the read at an instant is taken before the write of that
  -- instant; sv_after: after it.
  shared variable sv_first : t_scoreboard;
  shared variable sv_after : t_scoreboard;
begin
  proc_main : process
    variable v_first : t_tb_read;
    variable v_after : t_tb_read;
    variable v_late  : t_tb_read;
  begin
    test_runner_setup(runner, runner_cfg);
    while test_suite loop
      -- Two clocks with edges at the same instant (periods 7 ns and 10 ns, at
      -- 70 ns), collision window 10 ns, cells of 8 bits. Port A writes a
      -- 32-bit word (cells 0 to 3) at 63 ns; port B writes cell 0 at 70 ns,
      -- within the window, so every cell of both writes is undefined after
      -- 70 ns. A read at 70 ns sees the state before 70 ns: the data of
      -- port A, defined, whichever call comes first.
      if run("ww_collision_read_of_the_other_cells") then
        sv_first.configure(8, 16, false, 10 ns, false, true);
        sv_first.write(C_PORT_A, 0, 4, C_WORD_A, 63 ns);
        v_first := sv_first.snapshot(1, 3, 70 ns);
        sv_first.write(C_PORT_B, 0, 1, C_CELL_B, 70 ns);

        sv_after.configure(8, 16, false, 10 ns, false, true);
        sv_after.write(C_PORT_A, 0, 4, C_WORD_A, 63 ns);
        sv_after.write(C_PORT_B, 0, 1, C_CELL_B, 70 ns);
        v_after := sv_after.snapshot(1, 3, 70 ns);

        check_false(v_first.undef, "read before the port B write: cells 1 to 3 undefined");
        check_false(v_after.undef, "read after the port B write: cells 1 to 3 undefined at its own instant");
        check_equal(v_first.exp(23 downto 0), C_WORD_A(31 downto 8), "read before the port B write: data");
        check_equal(v_after.exp(23 downto 0), C_WORD_A(31 downto 8), "read after the port B write: data");
        -- After 70 ns every cell of both writes is undefined, in both orders.
        v_late := sv_first.snapshot(0, 4, 77 ns);
        check_true(v_late.undef, "read at 77 ns (read first at 70 ns): cells defined");
        v_late := sv_after.snapshot(0, 4, 77 ns);
        check_true(v_late.undef, "read at 77 ns (write first at 70 ns): cells defined");
      elsif run("ww_collision_read_of_the_shared_cell") then
        -- Cell 0 is written by both ports.
        sv_first.configure(8, 16, false, 10 ns, false, true);
        sv_first.write(C_PORT_A, 0, 4, C_WORD_A, 63 ns);
        v_first := sv_first.snapshot(0, 4, 70 ns);
        sv_first.write(C_PORT_B, 0, 1, C_CELL_B, 70 ns);

        sv_after.configure(8, 16, false, 10 ns, false, true);
        sv_after.write(C_PORT_A, 0, 4, C_WORD_A, 63 ns);
        sv_after.write(C_PORT_B, 0, 1, C_CELL_B, 70 ns);
        v_after := sv_after.snapshot(0, 4, 70 ns);

        check_false(v_first.undef, "read before the port B write: undefined");
        check_false(v_after.undef, "read after the port B write: undefined at its own instant");
        check_equal(v_first.exp(31 downto 0), C_WORD_A, "read before the port B write: data");
        check_equal(v_after.exp(31 downto 0), C_WORD_A, "read after the port B write: data");
      elsif run("ww_collision_same_instant") then
        -- Common clock: both writes at 70 ns. A read at 70 ns sees the state
        -- before both writes (never written), whatever the order of the calls.
        sv_first.configure(8, 16, true, 10 ns, false, true);
        v_first := sv_first.snapshot(0, 4, 70 ns);
        sv_first.write(C_PORT_A, 0, 4, C_WORD_A, 70 ns);
        sv_first.write(C_PORT_B, 0, 1, C_CELL_B, 70 ns);

        sv_after.configure(8, 16, true, 10 ns, false, true);
        sv_after.write(C_PORT_B, 0, 1, C_CELL_B, 70 ns);
        sv_after.write(C_PORT_A, 0, 4, C_WORD_A, 70 ns);
        v_after := sv_after.snapshot(0, 4, 70 ns);

        check_true(v_first.init and v_after.init, "read at the instant of both writes: not never-written data");
        check_false(v_first.undef or v_after.undef, "read at the instant of both writes: undefined");
        v_late := sv_first.snapshot(1, 3, 80 ns);
        check_true(v_late.undef, "read at 80 ns (port A second): cells 1 to 3 defined");
        v_late := sv_after.snapshot(1, 3, 80 ns);
        check_true(v_late.undef, "read at 80 ns (port A first): cells 1 to 3 defined");
      end if;
    end loop;
    test_runner_cleanup(runner);
  end process proc_main;
end architecture a_tb;
