# Legacy Import

The public repository keeps the original VHDL legacy inputs under
`legacy_inputs/`. The first imported modules were renamed from `lm_util_*` to
`lm_mem_*`:

| Legacy entity | New entity |
| --- | --- |
| `lm_util_ram_crw_crw` | `lm_mem_ram_crw_crw` |
| `lm_util_ram_rw_rw` | `lm_mem_ram_rw_rw` |
| `lm_util_ram_cr_cw` | `lm_mem_ram_cr_cw` |
| `lm_util_ram_cr_cw_ratio` | `lm_mem_ram_cr_cw_ratio` |
| `lm_util_ram_crw_cr` | `lm_mem_ram_crw_cr` |
| `lm_util_ram_crw_cw` | `lm_mem_ram_crw_cw` |
| `lm_util_ram_r_w` | `lm_mem_ram_r_w` |
| `lm_util_ram_r_w_ratio` | `lm_mem_ram_r_w_ratio` |
| `lm_util_fifo` | `lm_mem_fifo_sync` |

The legacy TCL memory-file generator was moved to
`tools/legacy/ces_mem_from_file_gen.tcl`. It remains available as legacy
input-file memory generation logic. A future task should migrate the supported
input formats into `lm_memgen`.

Known staged migrations:

- Vendor constants from `lm_target_pkg`.
- CDC resynchronizer dependency used by the legacy async FIFO path.
- Memory initialization file parsing.
- Tool-specific RAM inference branches.
