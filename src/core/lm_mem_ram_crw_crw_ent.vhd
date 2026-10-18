-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_ram_crw_crw
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  True dual-port RAM with two writers: entity only (dual-model entity).
--  Both ports have independent clocks, enables, write enables, addresses and
--  read data. Port widths may differ by a power-of-two ratio. Read latency is
--  1 or 2 cycles per port.
--
--  The architecture is chosen by the file manifest of each flow
--  (manifests/lm_memories.toml); every architecture is named a_rtl:
--  - simulation:  src/sim/lm_mem_ram_crw_crw_rtl.vhd (reference model);
--  - AMD Vivado:  src/vendor/amd/lm_mem_ram_crw_crw_rtl.vhd;
--  - other tools: src/vendor/unsupported/lm_mem_ram_crw_crw_rtl.vhd, which
--    stops synthesis.
--
--  Behaviour (all architectures):
--  - Port X write is enabled by its enable and its write enable together.
--  - A read during a write on the same port returns the old data
--    (read-first).
--  - Between the two ports, a read and a write of the same address, or two
--    writes to the same address, are undefined when the two clock edges are
--    less than one period of the slower clock apart. This also holds with a
--    common clock (lm_mem_ram_rw_rw). After two colliding writes the content
--    of that address is undefined until the next write to it.
--  - Both output stages of a port are enabled by its enable and hold their
--    value while it is low.
--  - Addresses at or beyond the depth (possible only when the depth is not a
--    power of two): a write never changes an address within the depth; a
--    read returns undefined data.
--  - Initial content is zero; there is no reset.
--  - g_simulation is deprecated and ignored.
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;

use work.lm_mem_pkg.all;

entity lm_mem_ram_crw_crw is
  generic(
    g_ram_a_latency : positive := 1;
    g_ram_a_data_w  : positive := 32;
    g_ram_a_depth   : positive := 1024;
    g_ram_b_latency : positive := 1;
    g_ram_b_data_w  : positive := 32;
    g_init_file     : string   := "";
    g_simulation    : integer  := 1
  );
  port(
    clk_a_i     : in  std_logic;
    clk_b_i     : in  std_logic;
    ena_i       : in  std_logic := '1';
    enb_i       : in  std_logic := '1';
    wen_a_i     : in  std_logic;
    wen_b_i     : in  std_logic;
    data_wr_a_i : in  std_logic_vector(g_ram_a_data_w - 1 downto 0);
    data_wr_b_i : in  std_logic_vector(g_ram_b_data_w - 1 downto 0);
    addr_a_i    : in  std_logic_vector(f_ceil_log2(g_ram_a_depth) - 1 downto 0);
    addr_b_i    : in  std_logic_vector(f_ceil_log2((g_ram_a_depth * g_ram_a_data_w) / g_ram_b_data_w) - 1 downto 0);
    data_rd_a_o : out std_logic_vector(g_ram_a_data_w - 1 downto 0);
    data_rd_b_o : out std_logic_vector(g_ram_b_data_w - 1 downto 0)
  );
end entity lm_mem_ram_crw_crw;
