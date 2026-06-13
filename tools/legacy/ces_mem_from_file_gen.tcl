#!/usr/bin/env tclsh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# in Windows your can open a terminal (this example use xtclsh as Tcl shell) and run:
# xtclsh.exe ./ces_reg_wizard.tcl -input regmap_test2.txt -outdir test_outdir -output test_output

lappend auto_path [file dirname [info script]]

package require cmdline


  #ARGUMENT          DEFAULT              DESCRIPTION
set options {
  {outdir.arg       ""                    "set the output directory"}
  {vendor.arg       "XILINX"              "set the FPGA Vendor/Synthesizer"}
  {output.arg       ""                    "set the output filename"}
  {infile.arg       ""                    "set the memory initialization file"}
  {latency.arg      "1"                   "set the memory latency"}
}
set usage ": ces_mem_from_file_gen \[options] filename ...\noptions:"

if { [catch {array set params [::cmdline::getoptions argv $options $usage]}]} {
   puts [cmdline::usage $options $usage]
} else {
  parray params
}

# Verify required parameters
set requiredParameters {infile}
foreach parameter $requiredParameters {
    if {$params($parameter) == ""} {
        puts stderr "Missing required parameter: -$parameter"
        exit 1
    }
}


set mem_file_name [file rootname [file tail $params(infile)]]

if {$params(outdir) in {""}} {
  set output_dir $mem_file_name
} else {
set output_dir $params(outdir)
}
if {$params(output) in {""}} {
  set output_file $mem_file_name
} else {
set output_file $params(output)
}


file mkdir $output_dir

# reads the data in to a file variable
set fp [open $params(infile) r]
set content [read $fp]
close $fp

# split the line in multiple lines
set records [split $content "\n"]

# maximum length of identifiers
set maxStringSize 64

# Current date, time, and seconds since epoch
# Array index                                            0  1  2  3  4  5  6
set datetime_arr [clock format [clock seconds] -format {%Y %m %d %H %M %S %s}]

set date "[lindex $datetime_arr 0]-[lindex $datetime_arr 1]-[lindex $datetime_arr 2]"
set compileTime "[lindex $datetime_arr 3]:[lindex $datetime_arr 4]:[lindex $datetime_arr 5]"

# PARSING
set sub_cnt 0

set address_list {}
set data_list {}
set active_region 0

foreach rec $records {
  
  #WIDTH
  regexp {\s*WIDTH\s*=\s*([0-9]*)\s*;} $rec -> width
  #DEPTH
  regexp {\s*DEPTH\s*=\s*([0-9]*)\s*;} $rec -> depth
  #ADDRESS_RADIX
  regexp {\s*ADDRESS_RADIX\s*=\s*([a-zA-z0-9_]*)\s*;} $rec -> address_radix
  #DATA_RADIX
  regexp {\s*DATA_RADIX\s*=\s*([a-zA-z0-9_]*)\s*;} $rec -> data_radix
  #COMMENT
  regexp {\s*--(.*)} $rec -> comment
  #CONTENT BEGIN
  if {$active_region == 0} {
    set active_region [regexp {\s*CONTENT\sBEGIN\s*} $rec]
    continue
  } elseif {$active_region == 1 && [regexp {\s*END\s*} $rec]} {
    set active_region 0
  }
  # no need to parse comments
  set is_comment [regexp {\s*--(.*)} $rec]
  if {$is_comment != 1 && $active_region == 1} {
    # single address
    if {[regexp {\s*([a-fA-F0-9]+)\s*:\s*[a-fA-F0-9]+\s*;} $rec -> local_address]} {
      lappend address_list $local_address
    } elseif {[regexp {\s*\[([a-fA-F0-9]+)..([a-fA-F0-9]+)\]\s*:\s*[a-fA-F0-9]+\s*;} $rec -> start_address_hex end_address_hex]} {
    set start_address [expr 0x$start_address_hex]
    set end_address [expr 0x$end_address_hex]
    #address range
      for {set k $start_address} {$k <= $end_address} {incr k} {
        lappend address_list [format %X $k]
      }      
    }
    # single address
    if {[regexp {\s*[a-fA-F0-9]+\s*:\s*([a-fA-F0-9]+)\s*;} $rec -> local_data]} {
      lappend data_list $local_data
    } elseif {[regexp {\s*\[[a-fA-F0-9]+..[a-fA-F0-9]+\]\s*:\s*([a-fA-F0-9]+)\s*;} $rec -> local_data]} {
      for {set k $start_address} {$k <= $end_address} {incr k} {
        lappend data_list $local_data
      } 
    }
    incr sub_cnt
  }
}

proc ces_log {base x} {
    expr {log($x)/log($base)}
}

proc ces_ceil_log2 {arg1} {
  if {[expr {$arg1 > 1.0}]} {
    set log2_var [ces_log 2 $arg1]
    set ceil_log2 [expr {ceil($log2_var)}]
  } else {
    set ceil_log2 1.0
  }
  return $ceil_log2
}

# parses each row where two elements are separated by a white-space or tab
###############################################################################
# VHDL OUTPUT (entity + architecture)
###############################################################################
set mem_file_name [join "$output_file initgen" "_"]
set vhd_mem_file [open [join "$output_dir $mem_file_name.vhd" "/"] w]

set bus_width [expr {int([ces_ceil_log2 $depth])}]

# write header to the generated VHDL output file (entity + architecture,
# not a package). SPDX-License-Identifier is emitted as line 1 of the
# generated VHDL so SPDX scanners (REUSE, ScanCode, etc.) can pick it
# up without scanning past a decorative divider.
puts $vhd_mem_file "-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2024-2026 LogiMentor S.r.l.
--=============================================================================
-- Module Name : $mem_file_name
-- Library     : -
-- Project     : -
-- Company     : LogiMentor S.r.l.
-------------------------------------------------------------------------------
-- Description: automatically generated on $date at $compileTime by
--              ces_mem_from_file_gen.tcl
--=============================================================================
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
"

if {$params(vendor) in {XILINX}} {
  puts $vhd_mem_file "  
entity $mem_file_name is
  port(
    --* input clock, port A
    clk_a_i     : in  std_logic;
    --* enable, port A
    ena_i       : in  std_logic := '1';
    --* input clock, port B
    clk_b_i     : in  std_logic;
    --* enable, port B
    enb_i       : in  std_logic := '1';
    --* write enable port A
    wen_a_i     : in  std_logic;
    --* write enable port B
    wen_b_i     : in  std_logic;
    --* input data on port A
    data_wr_a_i : in  std_logic_vector($width - 1 downto 0);
    --* input data on port B
    data_wr_b_i : in  std_logic_vector($width - 1 downto 0);
    --* address on port A
    addr_a_i    : in  std_logic_vector($bus_width - 1 downto 0);
    --* address on port B
    addr_b_i    : in  std_logic_vector($bus_width - 1 downto 0);
    --* output data on port A
    data_rd_a_o : out std_logic_vector($width - 1 downto 0);
    --* output data on port B
    data_rd_b_o : out std_logic_vector($width - 1 downto 0)
    );
end $mem_file_name;

architecture a_rtl of $mem_file_name is
    type t_mem is array (0 to $depth - 1) of std_logic_vector($width- 1 downto 0);
    
    constant C_INIT_RAM : t_mem := ("
    for {set k 0} {$k < [llength $address_list]} {incr k} {
      if {$address_radix in {HEX}} {
        set address_item  [expr 0x[lindex $address_list $k]]
      } elseif {$address_radix in {UNS}} {
        set address_item  [lindex $address_list $k]
      } else {
        puts stderr "ONLY HEX AND UNS ADDRESS FORMAT SUPPORTED"
        exit
      }
      if {$data_radix in {HEX}} {
        set data_item  "x\"[lindex $data_list $k]\""
      } elseif {$data_radix in {DEC}} {
        set data_item  "std_logic_vector(signed([lindex $data_list $k],$width))"
      } elseif {$data_radix in {UNS}} {
        set data_item  "std_logic_vector(unsigned([lindex $data_list $k],$width))"
      } else {
        puts stderr "ONLY HEX, DEC AND UNS DATA FORMAT SUPPORTED"
        exit
      } 
      set init_item "$address_item => $data_item"
      if {$k != [expr {[llength $address_list] - 1}]} {
        puts $vhd_mem_file "    $init_item,"
      } else {
        puts $vhd_mem_file "    $init_item"
      }
    }
    
  puts $vhd_mem_file "  );
    
    shared variable v_mem : t_mem := C_INIT_RAM;  
  
    signal s_data_rd_a   : std_logic_vector($width - 1 downto 0);
    signal s_data_rd_b   : std_logic_vector($width - 1 downto 0);
  
  begin
  
        
    -- write port
    proc_port_a : process(clk_a_i)
    begin
      if rising_edge(clk_a_i) then
        if ena_i = '1' then
          s_data_rd_a <= v_mem(to_integer(unsigned(addr_a_i)));
          if wen_a_i = '1' then
            v_mem(to_integer(unsigned(addr_a_i))) := data_wr_a_i;
          end if;
        end if;
      end if;
    end process proc_port_a;
  
    -- read port
    proc_port_b : process(clk_b_i)
    begin
      if rising_edge(clk_b_i) then
        if (enb_i = '1') then
          s_data_rd_b <= v_mem(to_integer(unsigned(addr_b_i)));
          if wen_b_i = '1' then
            v_mem(to_integer(unsigned(addr_b_i))) := data_wr_b_i;
          end if;
        end if;
      end if;
    end process proc_port_b;
    
    data_rd_a_o <= s_data_rd_a;
    data_rd_b_o <= s_data_rd_b;
    
  end architecture a_rtl;
  "
  } elseif {$params(vendor) in {LATTICE}} {
  puts $vhd_mem_file "
entity $mem_file_name is
  port(
    --* input clock
    clk_i       : in  std_logic;
    --* write enable port A
    wen_a_i     : in  std_logic;
    --* write enable port B
    wen_b_i     : in  std_logic;
    --* input data on port A
    data_wr_a_i : in  std_logic_vector($width - 1 downto 0);
    --* input data on port B
    data_wr_b_i : in  std_logic_vector($width - 1 downto 0);
    --* address on port A
    addr_a_i    : in  std_logic_vector($bus_width - 1 downto 0);
    --* address on port B
    addr_b_i    : in  std_logic_vector($bus_width - 1 downto 0);
    --* output data on port A
    data_rd_a_o : out std_logic_vector($width - 1 downto 0);
    --* output data on port B
    data_rd_b_o : out std_logic_vector($width - 1 downto 0)
    );
end $mem_file_name;

architecture a_rtl of $mem_file_name is  
    type t_mem is array (0 to $depth - 1) of std_logic_vector($width- 1 downto 0);
    
    constant C_INIT_RAM : t_mem := ("
    for {set k 0} {$k < [llength $address_list]} {incr k} {
      if {$address_radix in {HEX}} {
        set address_item  [expr 0x[lindex $address_list $k]]
      } elseif {$address_radix in {UNS}} {
        set address_item  [lindex $address_list $k]
      } else {
        puts stderr "ONLY HEX AND UNS ADDRESS FORMAT SUPPORTED"
        exit
      }
      if {$data_radix in {HEX}} {
        set data_item  "x\"[lindex $data_list $k]\""
      } elseif {$data_radix in {DEC}} {
        set data_item  "std_logic_vector(signed([lindex $data_list $k],$width))"
      } elseif {$data_radix in {UNS}} {
        set data_item  "std_logic_vector(unsigned([lindex $data_list $k],$width))"
      } else {
        puts stderr "ONLY HEX, DEC AND UNS DATA FORMAT SUPPORTED"
        exit
      } 
      set init_item "$address_item => $data_item"
      if {$k != [expr {[llength $address_list] - 1}]} {
        puts $vhd_mem_file "    $init_item,"
      } else {
        puts $vhd_mem_file "    $init_item"
      }
    }
    
  puts $vhd_mem_file "  );
    
      signal s_mem_m      : t_mem := C_INIT_RAM;
      attribute syn_ramstyle : string;
      attribute syn_ramstyle of s_mem_m : signal is \"no_rw_check\";
      
      signal s_addr_reg_a   : std_logic_vector($bus_width - 1 downto 0);
      signal s_addr_reg_b   : std_logic_vector($bus_width - 1 downto 0);
    
  begin
  
        
    -- write port
    proc_port_mem : process(clk_i)
    begin
      if rising_edge(clk_i) then        
        if wen_a_i = '1' then
          s_mem_m(to_integer(unsigned(addr_a_i))) <= data_wr_a_i;
        end if;
        if wen_b_i = '1' then
          s_mem_m(to_integer(unsigned(addr_b_i))) <= data_wr_b_i;
        end if;
        
        s_addr_reg_a <= addr_a_i;
        s_addr_reg_b <= addr_b_i;
      end if;
    end process proc_port_mem;
  
    data_rd_a_o <= s_mem_m(to_integer(unsigned(s_addr_reg_a)));
    data_rd_b_o <= s_mem_m(to_integer(unsigned(s_addr_reg_b)));
    
  end architecture a_rtl;
  "
  }
  
close $vhd_mem_file


