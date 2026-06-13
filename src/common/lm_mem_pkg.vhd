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

package lm_mem_pkg is
  function f_ceil_log2(n : natural) return natural;
  function f_min(l, r : natural) return natural;
  function f_max(l, r : natural) return natural;
  function f_is_power_of_two(n : natural) return boolean;
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

  function f_min(l, r : natural) return natural is
    variable v_result : natural := l;
  begin
    if r < l then
      v_result := r;
    end if;

    return v_result;
  end function f_min;

  function f_max(l, r : natural) return natural is
    variable v_result : natural := l;
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
end package body lm_mem_pkg;
