#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  exec python3 "${SCRIPT_DIR}/run_ghdl.py"
fi

exec python "${SCRIPT_DIR}/run_ghdl.py"
