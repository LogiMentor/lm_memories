# Vendor Matrix

Status values:

- `planned`
- `simulated`
- `synthesis-smoke-tested`
- `production-tested`

| Vendor | Tools | Families | Generic | Inference | Primitives |
| --- | --- | --- | --- | --- | --- |
| AMD/Xilinx | ISE, Vivado | Spartan-6, 7-Series, UltraScale, UltraScale+ | simulated | planned | planned |
| Intel/Altera | Quartus | Cyclone, Arria, Stratix | simulated | planned | planned |
| Lattice | Diamond, Radiant, Yosys/nextpnr | iCE40, ECP5, Nexus | simulated | planned | planned |
| Microchip/Microsemi | Libero | IGLOO, SmartFusion, RTG4, PolarFire | simulated | planned | planned |
| Generic | GHDL, Yosys where applicable | Portable behavioural targets | simulated | planned | planned |

The `simulated` status currently means the generic VHDL library has
self-checking GHDL testbenches. Vendor synthesis status will move to
`synthesis-smoke-tested` only after tool-specific jobs or scripts are added.
