#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
#
# AMD campaign for docs/results (issue #3): pilot, then every campaign in
# sequence. Every tool run holds the machine-wide EDA lock and waits there
# for free memory (tools/eda_lock.py); the logs of every step are archived
# with their SHA-256 (tools/eda_archive.py).
#
#   bash tools/campaign/run_amd.sh
#
# Environment (defaults in brackets):
#   LM_CAMPAIGN_ROOT     campaign directory, must be new or empty
#                        [$LM_EDA_WORKDIR/c, or lmeda/c in the system temp]
#   LM_EDA_ARCHIVE_DIR   archives [~/lm_eda_archive]
#   LM_VIVADO_DIR        [C:/Xilinx/Vivado/2023.1]
#   LM_QUARTUS_DIR       [C:/intelFPGA_lite/23.1std/quartus] (checked only)
#   VUNIT_MODELSIM_PATH  Questa [C:/questasim64_2021.1/win64]
#   GHDL                 GHDL executable [ghdl on PATH, else C:/msys64/ucrt64/bin/ghdl]
#
# Steps:
#   pilot           python -m tools.campaign.pilot (launcher exit codes,
#                   string generics, 1000 for 1024, old code); if it fails,
#                   the script stops with exit code 1
#   vivado          vendor_synth, vivado_check.toml (55 cases)
#   checks_vivado   vendor_synth, fallback_check.toml (with and without -assert)
#   checks_quartus  vendor_synth, quartus_check.toml
#   postsynth       run_postsynth.py, both parts, URAM configurations included
#   vunit_ghdl      run_vunit_campaign.py on GHDL
#   vunit_questa    run_vunit_campaign.py on Questa (-p 1)
#   mutation        mutation_check.py --campaign, with baseline
#
# Result: <campaign root>/summary.md and summary.json (tools/campaign/summarize.py).
# Exit code: 0 only if the pilot and every step passed.

set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO" || exit 2

fail() { echo "run_amd.sh: $*" >&2; exit 2; }

# Prerequisites, before anything runs.
PY=${PYTHON:-python}
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), sys.version' 2>/dev/null \
  || fail "Python 3.11 or newer is needed as '$PY' (set PYTHON)"
"$PY" -c 'import vunit' 2>/dev/null || fail "VUnit is not installed for $PY (pip install vunit_hdl)"
if [ -z "${GHDL:-}" ]; then
  if command -v ghdl >/dev/null 2>&1; then GHDL=$(command -v ghdl)
  elif [ -x /c/msys64/ucrt64/bin/ghdl.exe ] || [ -x /c/msys64/ucrt64/bin/ghdl ]; then GHDL=/c/msys64/ucrt64/bin/ghdl
  else fail "GHDL not found: put it on PATH or set GHDL"; fi
fi
export GHDL
export PATH="$(dirname "$GHDL"):$PATH"
export LM_VIVADO_DIR=${LM_VIVADO_DIR:-C:/Xilinx/Vivado/2023.1}
LM_QUARTUS_DIR=${LM_QUARTUS_DIR:-C:/intelFPGA_lite/23.1std/quartus}
export VUNIT_MODELSIM_PATH=${VUNIT_MODELSIM_PATH:-C:/questasim64_2021.1/win64}
[ -d "$LM_VIVADO_DIR/bin" ] || fail "Vivado not found in $LM_VIVADO_DIR"
[ -d "$LM_QUARTUS_DIR" ] || fail "Quartus not found in $LM_QUARTUS_DIR"
[ -d "$VUNIT_MODELSIM_PATH" ] || fail "Questa not found in $VUNIT_MODELSIM_PATH"
# The tool configurations name these directories; a different installation needs them changed too.
grep -q "install_dir = \"$LM_VIVADO_DIR\"" tools/vendor_synth/vivado_check.toml \
  || fail "tools/vendor_synth/*.toml name another Vivado directory than $LM_VIVADO_DIR"
[ -z "$(git status --porcelain --untracked-files=no)" ] \
  || fail "tracked files differ from HEAD: commit first, the results record the commit"

ROOT=${LM_CAMPAIGN_ROOT:-$("$PY" -c 'from tools.eda_workdir import work_root; print(work_root("c").as_posix())')}
if [ -d "$ROOT" ] && [ -n "$(ls -A "$ROOT")" ]; then
  fail "campaign root $ROOT is not empty (move or delete it; set LM_CAMPAIGN_ROOT for another one)"
fi
mkdir -p "$ROOT/logs" || fail "cannot create $ROOT"
STEPS="$ROOT/logs/steps.txt"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
{
  echo "$(stamp) start commit $(git rev-parse HEAD)"
  echo "python: $("$PY" --version 2>&1); ghdl: $("$GHDL" --version | head -1); vivado: $LM_VIVADO_DIR"
} > "$STEPS"
echo "Campaign root: $ROOT"
echo "Progress: $STEPS"

step() {
  local name=$1; shift
  echo "$(stamp) begin $name" | tee -a "$STEPS"
  "$@" > "$ROOT/logs/$name.txt" 2>&1
  local rc=$?
  echo "$(stamp) end $name rc=$rc" | tee -a "$STEPS"
  return $rc
}

summarize() {
  "$PY" -m tools.campaign.summarize "$ROOT" > "$ROOT/logs/summarize.txt" 2>&1
  local rc=$?
  echo
  echo "Summary: $ROOT/summary.md"
  return $rc
}

if ! step pilot "$PY" -m tools.campaign.pilot "$ROOT/pilot"; then
  echo "$(stamp) pilot failed: campaigns not started" | tee -a "$STEPS"
  summarize
  exit 1
fi

step vivado "$PY" -m tools.vendor_synth --config tools/vendor_synth/vivado_check.toml --keep-going \
  --campaign vivado_2023.1_amd --work-root "$ROOT/vs"
step checks_vivado "$PY" -m tools.vendor_synth --config tools/vendor_synth/fallback_check.toml --keep-going \
  --campaign static_checks_vivado_2023.1 --work-root "$ROOT/fc"
step checks_quartus "$PY" -m tools.vendor_synth --config tools/vendor_synth/quartus_check.toml --keep-going \
  --campaign static_checks_quartus_23.1 --work-root "$ROOT/qc"
step postsynth "$PY" sim/scripts/run_postsynth.py --campaign postsynth_vivado_2023.1_amd --work-root "$ROOT/ps"
step vunit_ghdl env LM_EDA_WORKDIR="$ROOT" "$PY" sim/scripts/run_vunit_campaign.py ghdl_6.0.0 -p 2
step vunit_questa env LM_EDA_WORKDIR="$ROOT" VUNIT_SIMULATOR=modelsim \
  "$PY" sim/scripts/run_vunit_campaign.py questa_2021.1 -p 1
step mutation env LM_EDA_WORKDIR="$ROOT" "$PY" sim/vunit/mutation_check.py --campaign ghdl_6.0.0
echo "$(stamp) done" >> "$STEPS"

summarize
exit $?
