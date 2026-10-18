-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LogiMentor Srl
--=============================================================================
-- Module Name : lm_mem_tb_harness
-- Library     : -
-- Project     : lm_memories
-- Company     : LogiMentor Srl
-------------------------------------------------------------------------------
-- Description:
--  Self-checking RAM test harness (VHDL-2008, simulation only, no VUnit
--  dependency). Used by the VUnit testbenches and by plain top-levels for
--  simulators without VUnit support.
--
--  - Drives constrained-random traffic on both ports of lm_mem_tb_dut and of
--    the frozen reference model lm_mem_ref_ram_crw_crw.
--  - Checks every read against the scoreboard of lm_mem_tb_pkg and against
--    the reference model, cycle by cycle, except reads excluded by the
--    collision rules (docs/PLAN.md section 3.5).
--  - Checks that each read output holds its value while its enable is low.
--  - Addresses cover the whole address range, so a depth that is not a power
--    of two also gets accesses beyond the depth (undefined: writes ignored by
--    the model, reads excluded, both counted).
--  - Fails on coverage holes: fewer checked reads than g_min_checked, no read
--    of never-written data, no read-first case where one is expected, no
--    masked write, no hold check, no access beyond a non-power-of-two depth,
--    or too many reads excluded by collisions.
--  - g_x_on_undef: reads of cells left undefined by a write/write collision
--    must return 'X' (simulation models only, see lm_mem_tb_pkg).
--  - Process order: inputs change on falling edges and are sampled on rising
--    edges; outputs are observed on falling edges; every scoreboard decision
--    uses the state before the current instant (see lm_mem_tb_pkg).
-------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.lm_mem_pkg.all;
use work.lm_mem_tb_pkg.all;

entity lm_mem_tb_harness is
  generic(
    g_dut              : string   := "lm_mem_ram_sdp";
    g_a_data_w         : positive := 32;
    g_a_depth          : positive := 1024;
    g_b_data_w         : positive := 32;
    g_a_latency        : positive := 1;
    g_b_latency        : positive := 1;
    g_a_period         : time     := 10 ns;
    g_b_period         : time     := 10 ns;
    g_same_clock       : boolean  := true;
    g_a_writes         : boolean  := true;
    g_a_reads          : boolean  := false;
    g_b_writes         : boolean  := false;
    g_b_reads          : boolean  := true;
    g_cross_rdw_valid  : boolean  := false;
    g_x_on_undef       : boolean  := false;
    g_n_cycles         : positive := 2000;
    g_hot_addrs        : positive := 8;
    g_seed             : positive := 1;
    g_max_excluded_pct : natural  := 25;
    g_min_checked      : natural  := 1000;
    g_sim_info         : string   := "unknown simulator"
  );
  port(
    done_o   : out boolean;
    errors_o : out natural
  );
end entity lm_mem_tb_harness;

architecture a_tb of lm_mem_tb_harness is
  constant C_CELL_W   : positive := f_min(g_a_data_w, g_b_data_w);
  constant C_A_WORDS  : positive := g_a_data_w / C_CELL_W;
  constant C_B_WORDS  : positive := g_b_data_w / C_CELL_W;
  constant C_B_DEPTH  : positive := (g_a_depth * g_a_data_w) / g_b_data_w;
  constant C_CELLS    : positive := g_a_depth * C_A_WORDS;
  constant C_A_AW     : positive := f_ceil_log2(g_a_depth);
  constant C_B_AW     : positive := f_ceil_log2(C_B_DEPTH);
  constant C_A_SPAN   : positive := 2 ** C_A_AW;
  constant C_B_SPAN   : positive := 2 ** C_B_AW;
  constant C_POW2     : boolean  := (C_A_SPAN = g_a_depth) and (C_B_SPAN = C_B_DEPTH);
  constant C_A_HOT    : positive := f_min(g_hot_addrs, g_a_depth);
  constant C_B_HOT    : positive := f_max(1, f_min((C_A_HOT * C_A_WORDS) / C_B_WORDS, C_B_DEPTH));
  constant C_WINDOW   : time     := maximum(g_a_period, g_b_period);
  constant C_PORT_A   : natural  := 0;
  constant C_PORT_B   : natural  := 1;

  shared variable sv_sb : t_scoreboard;

  signal s_clk_a    : std_logic := '0';
  signal s_clk_b    : std_logic := '0';
  signal s_ena      : std_logic := '0';
  signal s_enb      : std_logic := '0';
  signal s_wen_a    : std_logic := '0';
  signal s_wen_b    : std_logic := '0';
  signal s_addr_a   : std_logic_vector(C_A_AW - 1 downto 0) := (others => '0');
  signal s_addr_b   : std_logic_vector(C_B_AW - 1 downto 0) := (others => '0');
  signal s_din_a    : std_logic_vector(g_a_data_w - 1 downto 0) := (others => '0');
  signal s_din_b    : std_logic_vector(g_b_data_w - 1 downto 0) := (others => '0');
  signal s_dout_a   : std_logic_vector(g_a_data_w - 1 downto 0);
  signal s_dout_b   : std_logic_vector(g_b_data_w - 1 downto 0);
  signal s_rdout_a  : std_logic_vector(g_a_data_w - 1 downto 0);
  signal s_rdout_b  : std_logic_vector(g_b_data_w - 1 downto 0);
  signal s_ref_wenb : std_logic;
  signal s_cfg_done : boolean := false;
  signal s_a_done   : boolean := false;
  signal s_b_done   : boolean := false;
  signal s_stop     : boolean := false;
begin
  assert (g_dut = "lm_mem_ram_sdp") or (g_dut = "lm_mem_ram_tdp1w") or
         (g_dut = "lm_mem_ram_r_w") or (g_dut = "lm_mem_ram_cr_cw") or
         (g_dut = "lm_mem_ram_r_w_ratio") or (g_dut = "lm_mem_ram_cr_cw_ratio") or
         (g_dut = "lm_mem_ram_crw_cr") or (g_dut = "lm_mem_ram_crw_crw") or
         (g_dut = "lm_mem_ram_rw_rw") or (g_dut = "lm_mem_ram_crw_cw")
    report "lm_mem_tb_harness: unknown DUT " & g_dut
    severity failure;

  -- One process drives both clocks so that a common clock has both edges in
  -- the same delta cycle.
  gen_clk_common : if g_same_clock generate
    process
    begin
      while not s_stop loop
        s_clk_a <= '1';
        s_clk_b <= '1';
        wait for g_a_period / 2;
        s_clk_a <= '0';
        s_clk_b <= '0';
        wait for g_a_period / 2;
      end loop;
      wait;
    end process;
  end generate gen_clk_common;

  gen_clk_split : if not g_same_clock generate
    process
    begin
      while not s_stop loop
        s_clk_a <= '1';
        wait for g_a_period / 2;
        s_clk_a <= '0';
        wait for g_a_period / 2;
      end loop;
      wait;
    end process;

    process
    begin
      while not s_stop loop
        s_clk_b <= '1';
        wait for g_b_period / 2;
        s_clk_b <= '0';
        wait for g_b_period / 2;
      end loop;
      wait;
    end process;
  end generate gen_clk_split;

  inst_dut : entity work.lm_mem_tb_dut
    generic map(
      g_dut       => g_dut,
      g_a_data_w  => g_a_data_w,
      g_a_depth   => g_a_depth,
      g_b_data_w  => g_b_data_w,
      g_a_latency => g_a_latency,
      g_b_latency => g_b_latency
    )
    port map(
      clk_a_i  => s_clk_a,
      clk_b_i  => s_clk_b,
      ena_i    => s_ena,
      enb_i    => s_enb,
      wen_a_i  => s_wen_a,
      wen_b_i  => s_wen_b,
      addr_a_i => s_addr_a,
      addr_b_i => s_addr_b,
      din_a_i  => s_din_a,
      din_b_i  => s_din_b,
      dout_a_o => s_dout_a,
      dout_b_o => s_dout_b
    );

  s_ref_wenb <= s_wen_b when g_b_writes else '0';

  inst_ref : entity work.lm_mem_ref_ram_crw_crw
    generic map(
      g_ram_a_latency => g_a_latency,
      g_ram_a_data_w  => g_a_data_w,
      g_ram_a_depth   => g_a_depth,
      g_ram_b_latency => g_b_latency,
      g_ram_b_data_w  => g_b_data_w,
      g_init_file     => "",
      g_simulation    => 1
    )
    port map(
      clk_a_i     => s_clk_a,
      clk_b_i     => s_clk_b,
      ena_i       => s_ena,
      enb_i       => s_enb,
      wen_a_i     => s_wen_a,
      wen_b_i     => s_ref_wenb,
      data_wr_a_i => s_din_a,
      data_wr_b_i => s_din_b,
      addr_a_i    => s_addr_a,
      addr_b_i    => s_addr_b,
      data_rd_a_o => s_rdout_a,
      data_rd_b_o => s_rdout_b
    );

  proc_cfg : process
  begin
    sv_sb.configure(
      cell_w          => C_CELL_W,
      cells           => C_CELLS,
      same_clock      => g_same_clock,
      window          => C_WINDOW,
      cross_rdw_valid => g_cross_rdw_valid,
      x_on_undef      => g_x_on_undef
    );
    s_cfg_done <= true;
    wait;
  end process proc_cfg;

  -- Stimulus: inputs change on the falling edge of the port clock.
  proc_stim_a : process
    variable v_s1   : positive := g_seed;
    variable v_s2   : positive := 7919;
    variable v_bit  : std_logic;
    variable v_int  : natural;
    variable v_data : std_logic_vector(g_a_data_w - 1 downto 0);
  begin
    wait until s_cfg_done;
    for i in 1 to g_n_cycles loop
      wait until falling_edge(s_clk_a);
      p_tb_rand_bit(v_s1, v_s2, 0.85, v_bit);
      s_ena <= v_bit;
      if g_a_writes then
        p_tb_rand_bit(v_s1, v_s2, 0.5, v_bit);
        s_wen_a <= v_bit;
      end if;
      p_tb_rand_bit(v_s1, v_s2, 0.75, v_bit);
      if v_bit = '1' then
        p_tb_rand_int(v_s1, v_s2, 0, C_A_HOT - 1, v_int);
      else
        p_tb_rand_int(v_s1, v_s2, 0, C_A_SPAN - 1, v_int);
      end if;
      s_addr_a <= std_logic_vector(to_unsigned(v_int, C_A_AW));
      p_tb_rand_slv(v_s1, v_s2, v_data);
      s_din_a <= v_data;
    end loop;
    wait until falling_edge(s_clk_a);
    s_ena   <= '0';
    s_wen_a <= '0';
    s_a_done <= true;
    wait;
  end process proc_stim_a;

  proc_stim_b : process
    variable v_s1   : positive := g_seed + 104729;
    variable v_s2   : positive := 1299709;
    variable v_bit  : std_logic;
    variable v_int  : natural;
    variable v_data : std_logic_vector(g_b_data_w - 1 downto 0);
  begin
    wait until s_cfg_done;
    for i in 1 to g_n_cycles loop
      wait until falling_edge(s_clk_b);
      p_tb_rand_bit(v_s1, v_s2, 0.85, v_bit);
      s_enb <= v_bit;
      if g_b_writes then
        p_tb_rand_bit(v_s1, v_s2, 0.5, v_bit);
        s_wen_b <= v_bit;
      end if;
      p_tb_rand_bit(v_s1, v_s2, 0.75, v_bit);
      if v_bit = '1' then
        p_tb_rand_int(v_s1, v_s2, 0, C_B_HOT - 1, v_int);
      else
        p_tb_rand_int(v_s1, v_s2, 0, C_B_SPAN - 1, v_int);
      end if;
      s_addr_b <= std_logic_vector(to_unsigned(v_int, C_B_AW));
      p_tb_rand_slv(v_s1, v_s2, v_data);
      s_din_b <= v_data;
    end loop;
    wait until falling_edge(s_clk_b);
    s_enb   <= '0';
    s_wen_b <= '0';
    s_b_done <= true;
    wait;
  end process proc_stim_b;

  -- Monitors: sample on the rising edge, observe outputs on the falling edge.
  proc_mon_a : process
    variable v_tok1     : t_tb_read := C_TB_NO_READ;
    variable v_out      : t_tb_read := C_TB_NO_READ;
    variable v_new      : t_tb_read;
    variable v_base     : natural;
    variable v_have_out : boolean;
    variable v_have_obs : boolean := false;
    variable v_last     : std_logic_vector(g_a_data_w - 1 downto 0);
  begin
    wait until s_cfg_done;
    while not s_stop loop
      wait until rising_edge(s_clk_a);
      v_have_out := false;
      v_base := to_integer(unsigned(s_addr_a)) * C_A_WORDS;
      if g_a_writes and (s_wen_a = '1') then
        if s_ena = '1' then
          sv_sb.write(C_PORT_A, v_base, C_A_WORDS, s_din_a, now);
        else
          sv_sb.count_masked_write;
        end if;
      end if;
      if g_a_reads and (s_ena = '1') then
        v_new := sv_sb.snapshot(v_base, C_A_WORDS, now);
        if g_a_latency = 1 then
          v_out := v_new;
        else
          v_out  := v_tok1;
          v_tok1 := v_new;
        end if;
        v_have_out := v_out.valid;
      end if;
      wait until falling_edge(s_clk_a);
      if v_have_out then
        sv_sb.push_read(C_PORT_A, v_out, s_dout_a, s_rdout_a, "port A");
        v_last := s_dout_a;
        v_have_obs := true;
      elsif g_a_reads and v_have_obs then
        if s_dout_a /= v_last then
          sv_sb.add_error("port A output changed while its enable was low");
        end if;
        sv_sb.count_hold_check;
      end if;
      sv_sb.resolve(now, false);
    end loop;
    wait;
  end process proc_mon_a;

  proc_mon_b : process
    variable v_tok1     : t_tb_read := C_TB_NO_READ;
    variable v_out      : t_tb_read := C_TB_NO_READ;
    variable v_new      : t_tb_read;
    variable v_base     : natural;
    variable v_have_out : boolean;
    variable v_have_obs : boolean := false;
    variable v_last     : std_logic_vector(g_b_data_w - 1 downto 0);
  begin
    wait until s_cfg_done;
    while not s_stop loop
      wait until rising_edge(s_clk_b);
      v_have_out := false;
      v_base := to_integer(unsigned(s_addr_b)) * C_B_WORDS;
      if g_b_writes and (s_wen_b = '1') then
        if s_enb = '1' then
          sv_sb.write(C_PORT_B, v_base, C_B_WORDS, s_din_b, now);
        else
          sv_sb.count_masked_write;
        end if;
      end if;
      if g_b_reads and (s_enb = '1') then
        v_new := sv_sb.snapshot(v_base, C_B_WORDS, now);
        if g_b_latency = 1 then
          v_out := v_new;
        else
          v_out  := v_tok1;
          v_tok1 := v_new;
        end if;
        v_have_out := v_out.valid;
      end if;
      wait until falling_edge(s_clk_b);
      if v_have_out then
        sv_sb.push_read(C_PORT_B, v_out, s_dout_b, s_rdout_b, "port B");
        v_last := s_dout_b;
        v_have_obs := true;
      elsif g_b_reads and v_have_obs then
        if s_dout_b /= v_last then
          sv_sb.add_error("port B output changed while its enable was low");
        end if;
        sv_sb.count_hold_check;
      end if;
      sv_sb.resolve(now, false);
    end loop;
    wait;
  end process proc_mon_b;

  proc_end : process
    variable v_c        : t_tb_counts;
    variable v_excluded : natural;
  begin
    done_o   <= false;
    errors_o <= 0;
    wait until s_a_done and s_b_done;
    wait for 4 * C_WINDOW + 4 * maximum(g_a_period, g_b_period);
    sv_sb.resolve(now, true);
    v_c := sv_sb.counts;
    v_excluded := v_c.reads_excl_rw + v_c.reads_excl_undef;

    if v_c.reads_checked < g_min_checked then
      sv_sb.add_error("coverage: " & integer'image(v_c.reads_checked) & " reads checked, minimum " &
                      integer'image(g_min_checked));
    end if;
    if v_excluded * 100 > g_max_excluded_pct * v_c.reads then
      sv_sb.add_error("too many excluded reads: " & integer'image(v_excluded) & " of " &
                      integer'image(v_c.reads));
    end if;
    if (g_a_depth > g_hot_addrs) and (v_c.cov_init_read = 0) then
      sv_sb.add_error("coverage: no read of never-written data");
    end if;
    if (g_a_writes and g_a_reads) or g_cross_rdw_valid then
      if v_c.cov_rdw_old_data = 0 then
        sv_sb.add_error("coverage: no read-during-write returning old data");
      end if;
    end if;
    if v_c.writes_masked = 0 then
      sv_sb.add_error("coverage: no write masked by a low enable");
    end if;
    if v_c.hold_checks = 0 then
      sv_sb.add_error("coverage: no output hold check");
    end if;
    if not C_POW2 then
      if (g_a_writes or g_b_writes) and (v_c.writes_oor = 0) then
        sv_sb.add_error("coverage: no write beyond the depth");
      end if;
      if v_c.reads_excl_oor = 0 then
        sv_sb.add_error("coverage: no read beyond the depth");
      end if;
    end if;

    v_c := sv_sb.counts;
    report "lm_mem_tb_harness " & g_dut & " [" & g_sim_info & "]: " & f_tb_counts_image(v_c) severity note;
    errors_o <= v_c.errors;
    done_o   <= true;
    s_stop   <= true;
    wait;
  end process proc_end;
end architecture a_tb;
