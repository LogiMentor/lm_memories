# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
# Run lm_memories GHDL simulations on Windows PowerShell.

$ErrorActionPreference = "Stop"

if ($env:PYTHON) {
  & $env:PYTHON "$PSScriptRoot\run_ghdl.py"
} else {
  & python "$PSScriptRoot\run_ghdl.py"
}

exit $LASTEXITCODE
