--=============================================================================
-- Module Name : lm_mem_fifo_sync
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Generic synchronous FIFO.
--  Reset is active-low synchronous. Write and read operations share clk_i.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity lm_mem_fifo_sync is
  generic(
    g_data_w             : positive := 32;
    g_depth              : positive := 16;
    g_almost_empty_limit : natural  := 1;
    g_almost_full_limit  : natural  := 1
  );
  port(
    clk_i          : in  std_logic;
    rst_n_i        : in  std_logic;
    din_i          : in  std_logic_vector(g_data_w - 1 downto 0);
    wen_i          : in  std_logic;
    ren_i          : in  std_logic;
    dout_o         : out std_logic_vector(g_data_w - 1 downto 0);
    full_o         : out std_logic;
    almost_full_o  : out std_logic;
    empty_o        : out std_logic;
    almost_empty_o : out std_logic;
    valid_o        : out std_logic
  );
end entity lm_mem_fifo_sync;

architecture a_rtl of lm_mem_fifo_sync is
  type t_mem is array (0 to g_depth - 1) of std_logic_vector(g_data_w - 1 downto 0);

  signal s_mem       : t_mem;
  signal s_wr_ptr    : natural range 0 to g_depth - 1 := 0;
  signal s_rd_ptr    : natural range 0 to g_depth - 1 := 0;
  signal s_count     : natural range 0 to g_depth := 0;
  signal s_dout      : std_logic_vector(g_data_w - 1 downto 0) := (others => '0');
  signal s_valid     : std_logic := '0';

  function f_next_ptr(p_ptr : natural) return natural is
    variable v_next : natural := 0;
  begin
    if p_ptr = g_depth - 1 then
      v_next := 0;
    else
      v_next := p_ptr + 1;
    end if;

    return v_next;
  end function f_next_ptr;

  function f_almost_full(
    p_count : natural;
    p_depth : positive;
    p_limit : natural
  ) return std_logic is
    variable v_result : std_logic := '0';
  begin
    if p_limit >= p_depth then
      if p_count > 0 then
        v_result := '1';
      end if;
    elsif p_count >= p_depth - p_limit then
      v_result := '1';
    end if;

    return v_result;
  end function f_almost_full;
begin
  assert f_is_power_of_two(g_depth)
    report "lm_mem_fifo_sync: g_depth must be a power of two for this initial FIFO implementation"
    severity failure;

  proc_fifo : process(clk_i)
    variable v_read  : boolean;
    variable v_write : boolean;
  begin
    if rising_edge(clk_i) then
      if rst_n_i = '0' then
        s_wr_ptr <= 0;
        s_rd_ptr <= 0;
        s_count  <= 0;
        s_dout   <= (others => '0');
        s_valid  <= '0';
      else
        v_read  := (ren_i = '1') and (s_count > 0);
        v_write := (wen_i = '1') and ((s_count < g_depth) or v_read);
        s_valid <= '0';

        if v_read then
          s_dout  <= s_mem(s_rd_ptr);
          s_rd_ptr <= f_next_ptr(s_rd_ptr);
          s_valid <= '1';
        end if;

        if v_write then
          s_mem(s_wr_ptr) <= din_i;
          s_wr_ptr        <= f_next_ptr(s_wr_ptr);
        end if;

        if v_write and not v_read then
          s_count <= s_count + 1;
        elsif v_read and not v_write then
          s_count <= s_count - 1;
        end if;
      end if;
    end if;
  end process proc_fifo;

  dout_o         <= s_dout;
  valid_o        <= s_valid;
  full_o         <= '1' when s_count = g_depth else '0';
  empty_o        <= '1' when s_count = 0 else '0';
  almost_full_o  <= f_almost_full(s_count, g_depth, g_almost_full_limit);
  almost_empty_o <= '1' when s_count <= g_almost_empty_limit else '0';
end architecture a_rtl;
