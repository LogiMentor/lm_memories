-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_crw_crw (fallback architecture)
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Fallback architecture of the dual-model entity lm_mem_ram_crw_crw
--  (src/core/lm_mem_ram_crw_crw_ent.vhd), selected by the file manifest for
--  every tool without a validated two-writer architecture. It stops
--  elaboration, so a two-writer RAM is never built silently from flip-flops.
--
--  The stop is a static check of lm_mem_pkg: an assertion with an explicit
--  message, and a constant outside its range for tools that ignore
--  assertions. AMD Vivado ignores assertions unless synth_design -assert is
--  used; it then reports "[Synth 8-11323] assigned value '1' out of range"
--  at the line of C_ERROR_NO_VALIDATED_TWO_WRITER_RAM_FOR_THIS_TOOL, whose
--  comment explains the error. The value depends on a generic, so the file
--  still analyzes and the error is raised at elaboration.
--  lm_mem_ram_rw_rw and lm_mem_ram_crw_cw stop in the same way.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

architecture a_rtl of lm_mem_ram_crw_crw is
  -- Never true for a valid depth: this architecture always stops.
  constant C_OK_SUPPORTED : boolean := g_ram_a_depth < 1;
  constant C_MSG_SUPPORTED : string := "lm_mem_ram_crw_crw: no validated two-writer RAM architecture for this tool. " &
    "lm_memories supports lm_mem_ram_crw_crw, lm_mem_ram_rw_rw and lm_mem_ram_crw_cw " &
    "only with AMD Vivado so far (manifests/lm_memories.toml, flow synth-amd).";
  constant C_ERROR_NO_VALIDATED_TWO_WRITER_RAM_FOR_THIS_TOOL : t_static_check := f_static_check(C_OK_SUPPORTED, C_MSG_SUPPORTED);  -- ERROR: lm_memories has no validated two-writer RAM architecture for this tool (see manifests/lm_memories.toml)
begin
  data_rd_a_o <= (others => '0');
  data_rd_b_o <= (others => '0');
end architecture a_rtl;
