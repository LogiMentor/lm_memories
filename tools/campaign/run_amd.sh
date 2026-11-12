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
#                        [$LM_EDA_WORKDIR/c; default C:/lmeda/c on Windows,
#                        lmeda/c in the system temp elsewhere]
#   LM_EDA_ARCHIVE_DIR   archives [~/lm_eda_archive]
#   LM_VIVADO_DIR        Vivado installation directory (required; the
#                        configurations of vendor_synth read it through
#                        install_dir_env, run_postsynth.py and the pilot too)
#   LM_QUARTUS_DIR       Quartus installation directory, the one with bin/
#                        or bin64/ (required)
#   VUNIT_MODELSIM_PATH  directory of the Questa executables (required)
#   GHDL                 GHDL executable [$VUNIT_GHDL_PATH/ghdl, else ghdl on PATH]
#   LM_EDA_RETRY_DELAYS  seconds to wait before the first, second rerun of a
#                        transient tool failure [30,60] (tools/eda_retry.py)
#   LM_CAMPAIGN_RESUME   resume an existing campaign root (its pilot passed)
#                        with only these steps, separated by spaces [unset]:
#                        vivado, checks_vivado, checks_quartus: the results
#                        evaluated again with the current checks, no tool
#                        run (tools/campaign/resume.py); postsynth: run again
#                        from scratch (the old ps kept as ps.before_<time>).
#                        Refused if a kept step's sources, manifest, harness
#                        or configuration changed since it ran
#                        (tools/campaign/deps.py, logs/deps.json).
#                        Then the tool-errors of these steps run again, and
#                        the summary is written again. The results of the
#                        other steps stay as they are; logs/steps.txt records
#                        the commit of the resume.
#
# Steps:
#   pilot           python -m tools.campaign.pilot (launcher exit codes,
#                   string generics, 1000 for 1024, old code); a case that
#                   ends as tool-error runs again, at most twice; if a case
#                   fails or stays a tool-error, the script stops with exit
#                   code 1
#   vivado          vendor_synth, vivado_check.toml (55 cases)
#   checks_vivado   vendor_synth, fallback_check.toml (with and without -assert)
#   checks_quartus  vendor_synth, quartus_check.toml
#   postsynth       run_postsynth.py, both parts, URAM configurations included
#   vunit_ghdl      run_vunit_campaign.py on GHDL
#   vunit_questa    run_vunit_campaign.py on Questa (-p 1)
#   mutation        mutation_check.py --campaign, with baseline
#   rerun_tool_errors  python -m tools.campaign.rerun: every case that ended
#                   as tool-error runs once more (never a fail); the summary
#                   lists each one with its first and second result
#
# Tool versions: detected once at the start (tools/campaign/toolset.py) and
# written to logs/toolset.json; the campaign names, and so the names of the
# result files, come from there (vivado_<version>_amd, ...). A resume
# requires the same versions installed.
#
# The memory thresholds of the EDA lock (LM_EDA_MIN_FREE_MB_SYNTH, default
# 4096 MB, and LM_EDA_MIN_FREE_MB_SIM, default 2048 MB) are recorded in
# logs/steps.txt and in the summary.
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
  if [ -n "${VUNIT_GHDL_PATH:-}" ] && { [ -x "$VUNIT_GHDL_PATH/ghdl" ] || [ -x "$VUNIT_GHDL_PATH/ghdl.exe" ]; }; then
    GHDL=$VUNIT_GHDL_PATH/ghdl
  elif command -v ghdl >/dev/null 2>&1; then GHDL=$(command -v ghdl)
  else fail "GHDL not found: set GHDL or VUNIT_GHDL_PATH, or put ghdl on PATH"; fi
fi
export GHDL
export PATH="$(dirname "$GHDL"):$PATH"
# The tool installations come from the environment only: no default
# directory, and the configurations read the same variables.
[ -n "${LM_VIVADO_DIR:-}" ] || fail "set LM_VIVADO_DIR to the Vivado installation directory"
[ -n "${LM_QUARTUS_DIR:-}" ] || fail "set LM_QUARTUS_DIR to the Quartus installation directory"
[ -n "${VUNIT_MODELSIM_PATH:-}" ] || fail "set VUNIT_MODELSIM_PATH to the directory of the Questa executables"
export LM_VIVADO_DIR LM_QUARTUS_DIR VUNIT_MODELSIM_PATH
[ -d "$LM_VIVADO_DIR/bin" ] || fail "Vivado not found in $LM_VIVADO_DIR"
[ -d "$LM_QUARTUS_DIR" ] || fail "Quartus not found in $LM_QUARTUS_DIR"
[ -d "$VUNIT_MODELSIM_PATH" ] || fail "Questa not found in $VUNIT_MODELSIM_PATH"
[ -z "$(git status --porcelain --untracked-files=no)" ] \
  || fail "tracked files differ from HEAD: commit first, the results record the commit"

ROOT=${LM_CAMPAIGN_ROOT:-$("$PY" -c 'from tools.eda_workdir import work_root; print(work_root("c").as_posix())')}
RESUME=${LM_CAMPAIGN_RESUME:-}
TOOLSET="$ROOT/logs/toolset.json"
TAG=$(date -u +%Y%m%dT%H%M%SZ)
if [ -n "$RESUME" ]; then
  for s in $RESUME; do
    case $s in vivado|checks_vivado|checks_quartus|postsynth) ;;
      *) fail "LM_CAMPAIGN_RESUME: $s cannot be resumed (vivado, checks_vivado, checks_quartus, postsynth)";;
    esac
  done
  [ -f "$ROOT/logs/steps.txt" ] || fail "LM_CAMPAIGN_RESUME: $ROOT is not a campaign root (no logs/steps.txt)"
  "$PY" -c 'import json, sys; sys.exit(0 if json.load(open(sys.argv[1])).get("ok") else 1)' "$ROOT/pilot/pilot.json" \
    2>/dev/null || fail "LM_CAMPAIGN_RESUME: the pilot of $ROOT did not pass"
  # The kept steps must have the dependencies they ran with (tools/campaign/deps.py).
  "$PY" -m tools.campaign.deps check "$ROOT" $RESUME     || fail "LM_CAMPAIGN_RESUME: refused, the dependencies of a kept step changed (above)"
  # And the tools they ran with (tools/campaign/toolset.py).
  "$PY" -m tools.campaign.toolset check "$TOOLSET" || fail "LM_CAMPAIGN_RESUME: refused, the tools are not those of the campaign (above)"
else
  [ -d "$ROOT" ] && [ -n "$(ls -A "$ROOT")" ] \
    && fail "campaign root $ROOT is not empty (move or delete it; set LM_CAMPAIGN_ROOT for another one)"
  # The tool versions, before anything is written (tools/campaign/toolset.py).
  DETECTED=$("$PY" -m tools.campaign.toolset detect) || fail "cannot read the version of every tool (above)"
fi
mkdir -p "$ROOT/logs" || fail "cannot create $ROOT"
[ -n "$RESUME" ] || printf '%s\n' "$DETECTED" > "$TOOLSET" || fail "cannot write $TOOLSET"
# Campaign name of a step, from the toolset of the campaign.
name() { "$PY" -m tools.campaign.toolset name "$TOOLSET" "$1" || fail "no campaign name for $1 in $TOOLSET"; }
N_VIVADO=$(name vivado) || exit 2
N_CHECKS_VIVADO=$(name checks_vivado) || exit 2
N_CHECKS_QUARTUS=$(name checks_quartus) || exit 2
N_POSTSYNTH=$(name postsynth) || exit 2
N_VUNIT_GHDL=$(name vunit_ghdl) || exit 2
N_VUNIT_QUESTA=$(name vunit_questa) || exit 2
N_MUTATION=$(name mutation) || exit 2
STEPS="$ROOT/logs/steps.txt"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
# Memory thresholds of the EDA lock in effect for every run of the campaign
# (tools/eda_lock.py), recorded in the step log and in the summary.
MEMORY=$("$PY" -c 'from tools.eda_lock import min_free_mb
print("; ".join("%s %d MB (%s)" % ((k,) + (int(min_free_mb(k)[0]), min_free_mb(k)[1])) for k in ("synthesis", "simulation")))') \
  || fail "cannot read the memory thresholds of tools/eda_lock.py"
# Waits before a rerun (tools/eda_retry.py); a wrong value stops here.
RETRY_WAITS=$("$PY" -c 'from tools.eda_retry import retry_delays, RETRY_DELAYS_ENV
print("%s s (%s)" % (", ".join("%g" % d for d in retry_delays()), RETRY_DELAYS_ENV))') \
  || fail "wrong LM_EDA_RETRY_DELAYS (seconds separated by commas, for example 30,60)"
if [ -n "$RESUME" ]; then
  {
    echo "$(stamp) resume commit $(git rev-parse HEAD) steps: $RESUME"
    echo "memory thresholds: $MEMORY"
    echo "retry waits: $RETRY_WAITS"
  } >> "$STEPS"
else
  {
    echo "$(stamp) start commit $(git rev-parse HEAD)"
    echo "python: $("$PY" --version 2>&1); ghdl: $("$GHDL" --version | head -1); vivado: $LM_VIVADO_DIR"
    echo "campaigns: $N_VIVADO $N_CHECKS_VIVADO $N_CHECKS_QUARTUS $N_POSTSYNTH $N_VUNIT_GHDL $N_VUNIT_QUESTA $N_MUTATION"
    echo "memory thresholds: $MEMORY"
    echo "retry waits: $RETRY_WAITS"
  } > "$STEPS"
  "$PY" -m tools.campaign.deps record "$ROOT" > /dev/null || fail "cannot record the dependencies of the steps"
fi
echo "Campaign root: $ROOT"
echo "Memory thresholds: $MEMORY"
echo "Retry waits: $RETRY_WAITS"
echo "Progress: $STEPS"

step() {
  local name=$1; shift
  # A resumed step keeps the driver log of its earlier run.
  [ -f "$ROOT/logs/$name.txt" ] && mv "$ROOT/logs/$name.txt" "$ROOT/logs/$name.before_$TAG.txt"
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

if [ -n "$RESUME" ]; then
  FRESH=""
  for s in $RESUME; do
    if [ "$s" = postsynth ]; then
      [ -d "$ROOT/ps" ] && mv "$ROOT/ps" "$ROOT/ps.before_$TAG"
      step postsynth "$PY" sim/scripts/run_postsynth.py --campaign "$N_POSTSYNTH" --work-root "$ROOT/ps"
      FRESH="postsynth"
    else
      step "reevaluate_$s" "$PY" -m tools.campaign.resume "$ROOT" "$s"
    fi
  done
  step rerun_tool_errors "$PY" -m tools.campaign.rerun "$ROOT" --steps $RESUME ${FRESH:+--fresh $FRESH}
  "$PY" -m tools.campaign.deps record "$ROOT" $RESUME > /dev/null || fail "cannot record the dependencies of the steps"
  echo "$(stamp) done (resume)" >> "$STEPS"
  summarize
  exit $?
fi

if ! step pilot "$PY" -m tools.campaign.pilot "$ROOT/pilot"; then
  echo "$(stamp) pilot failed: campaigns not started" | tee -a "$STEPS"
  summarize
  exit 1
fi

step vivado "$PY" -m tools.vendor_synth --config tools/vendor_synth/vivado_check.toml --keep-going \
  --campaign "$N_VIVADO" --work-root "$ROOT/vs"
step checks_vivado "$PY" -m tools.vendor_synth --config tools/vendor_synth/fallback_check.toml --keep-going \
  --campaign "$N_CHECKS_VIVADO" --work-root "$ROOT/fc"
step checks_quartus "$PY" -m tools.vendor_synth --config tools/vendor_synth/quartus_check.toml --keep-going \
  --campaign "$N_CHECKS_QUARTUS" --work-root "$ROOT/qc"
step postsynth "$PY" sim/scripts/run_postsynth.py --campaign "$N_POSTSYNTH" --work-root "$ROOT/ps"
step vunit_ghdl env LM_EDA_WORKDIR="$ROOT" VUNIT_SIMULATOR=ghdl "$PY" sim/scripts/run_vunit_campaign.py "$N_VUNIT_GHDL" -p 2
step vunit_questa env LM_EDA_WORKDIR="$ROOT" VUNIT_SIMULATOR=modelsim \
  "$PY" sim/scripts/run_vunit_campaign.py "$N_VUNIT_QUESTA" -p 1
step mutation env LM_EDA_WORKDIR="$ROOT" VUNIT_SIMULATOR=ghdl "$PY" sim/vunit/mutation_check.py --campaign "$N_MUTATION"
# Once more, only the cases that ended as tool-error (never a fail).
step rerun_tool_errors "$PY" -m tools.campaign.rerun "$ROOT"
echo "$(stamp) done" >> "$STEPS"

summarize
exit $?
