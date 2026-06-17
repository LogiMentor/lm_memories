-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_pkg
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Common package for lm_memories RTL modules.
--  Provides small utility functions used by generic RAM and FIFO components.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

package lm_mem_pkg is
  constant C_META_DELAY_LEN : natural := 2;

  function f_ceil_log2(n : natural) return natural;
  function f_min(l, r : integer) return integer;
  function f_max(l, r : integer) return integer;
  function f_is_power_of_two(n : natural) return boolean;
  function f_sel_a_b(sel : boolean; a, b : integer) return integer;
  function f_uns2slv(inp : unsigned) return std_logic_vector;
  function f_bin2gray(a : std_logic_vector) return std_logic_vector;
  function f_gray2bin(a : std_logic_vector) return std_logic_vector;
  function f_vector_or(slv : std_logic_vector) return std_logic;
end package lm_mem_pkg;

package body lm_mem_pkg is
  function f_ceil_log2(n : natural) return natural is
    variable v_power : natural := 1;
    variable v_width : natural := 0;
  begin
    if n <= 1 then
      v_width := 1;
    else
      while v_power < n loop
        v_power := v_power * 2;
        v_width := v_width + 1;
      end loop;
    end if;

    return v_width;
  end function f_ceil_log2;

  function f_min(l, r : integer) return integer is
    variable v_result : integer := l;
  begin
    if r < l then
      v_result := r;
    end if;

    return v_result;
  end function f_min;

  function f_max(l, r : integer) return integer is
    variable v_result : integer := l;
  begin
    if r > l then
      v_result := r;
    end if;

    return v_result;
  end function f_max;

  function f_is_power_of_two(n : natural) return boolean is
    variable v_power  : natural := 1;
    variable v_result : boolean := false;
  begin
    if n > 0 then
      while v_power < n loop
        v_power := v_power * 2;
      end loop;

      v_result := v_power = n;
    end if;

    return v_result;
  end function f_is_power_of_two;

  function f_sel_a_b(sel : boolean; a, b : integer) return integer is
    variable v_result : integer := b;
  begin
    if sel then
      v_result := a;
    end if;

    return v_result;
  end function f_sel_a_b;

  function f_uns2slv(inp : unsigned) return std_logic_vector is
  begin
    return std_logic_vector(inp);
  end function f_uns2slv;

  function f_bin2gray(a : std_logic_vector) return std_logic_vector is
    variable v_a : std_logic_vector(a'length - 1 downto 0) := a;
  begin
    assert v_a'length > 1
      report "lm_mem_pkg.f_bin2gray: input length must be greater than 1"
      severity failure;

    return v_a xor ("0" & v_a(v_a'length - 1 downto 1));
  end function f_bin2gray;

  function f_gray2bin(a : std_logic_vector) return std_logic_vector is
    variable v_a   : std_logic_vector(a'length - 1 downto 0) := a;
    variable v_bin : std_logic_vector(v_a'range);
    variable v_int : std_logic;
  begin
    assert v_a'length > 1
      report "lm_mem_pkg.f_gray2bin: input length must be greater than 1"
      severity failure;

    v_int := '0';
    for n in v_a'length - 1 downto 0 loop
      v_bin(n) := v_a(n) xor v_int;
      v_int    := v_bin(n);
    end loop;

    return v_bin;
  end function f_gray2bin;

  function f_vector_or(slv : std_logic_vector) return std_logic is
    variable v_result : std_logic := '0';
  begin
    for i in slv'range loop
      v_result := v_result or slv(i);
    end loop;

    return v_result;
  end function f_vector_or;
end package body lm_mem_pkg;
