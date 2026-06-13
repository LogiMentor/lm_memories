#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD_DIR="${ROOT_DIR}/sim/build/ghdl"
GHDL="${GHDL:-ghdl}"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

GHDL_FLAGS=(--std=08 --workdir="${BUILD_DIR}")

sources=(
  "${ROOT_DIR}/src/common/lm_mem_pkg.vhd"
  "${ROOT_DIR}/src/generic/lm_mem_ram_crw_crw.vhd"
  "${ROOT_DIR}/src/generic/lm_mem_ram_rw_rw.vhd"
  "${ROOT_DIR}/src/generic/lm_mem_ram_cr_cw.vhd"
  "${ROOT_DIR}/src/generic/lm_mem_ram_cr_cw_ratio.vhd"
  "${ROOT_DIR}/src/generic/lm_mem_ram_crw_cr.vhd"
  "${ROOT_DIR}/src/generic/lm_mem_ram_crw_cw.vhd"
  "${ROOT_DIR}/src/generic/lm_mem_ram_r_w.vhd"
  "${ROOT_DIR}/src/generic/lm_mem_ram_r_w_ratio.vhd"
  "${ROOT_DIR}/src/fifo/lm_mem_fifo_sync.vhd"
)

for src in "${sources[@]}"; do
  "${GHDL}" -a "${GHDL_FLAGS[@]}" "${src}"
done

for tb in "${ROOT_DIR}"/sim/tb/tb_*.vhd; do
  "${GHDL}" -a "${GHDL_FLAGS[@]}" "${tb}"
done

tests=(
  tb_lm_mem_ram_rw_rw
  tb_lm_mem_fifo_sync
)

for test_name in "${tests[@]}"; do
  "${GHDL}" -e "${GHDL_FLAGS[@]}" "${test_name}"
  "${GHDL}" -r "${GHDL_FLAGS[@]}" "${test_name}" --assert-level=error
done
