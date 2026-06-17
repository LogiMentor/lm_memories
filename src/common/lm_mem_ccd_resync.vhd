-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ccd_resync
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Cross-clock-domain single-bit re-synchronizer.
--  The signal is sampled through g_meta_levels flip-flops in the destination
--  clock domain.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

entity lm_mem_ccd_resync is
  generic(
    g_meta_levels : positive := 2
  );
  port(
    clk_i     : in  std_logic;
    ccd_din_i : in  std_logic;
    ccd_din_o : out std_logic
  );
end entity lm_mem_ccd_resync;

architecture a_rtl of lm_mem_ccd_resync is
  signal s_din_meta : std_logic_vector(g_meta_levels - 1 downto 0);
begin
  assert g_meta_levels >= 2
    report "lm_mem_ccd_resync: g_meta_levels must be at least 2"
    severity failure;

  proc_resync : process(clk_i)
  begin
    if rising_edge(clk_i) then
      s_din_meta <= s_din_meta(s_din_meta'left - 1 downto 0) & ccd_din_i;
    end if;
  end process proc_resync;

  ccd_din_o <= s_din_meta(s_din_meta'left);
end architecture a_rtl;
