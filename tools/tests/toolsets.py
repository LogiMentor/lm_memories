# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Toolsets of the two reference machines for the tests (tools/campaign/toolset.py).

LAPTOP: the Windows laptop of the published results up to v0.2.0 (the
version lines as recorded in docs/results: Questa in
vunit_questa_2021.1.json, GHDL in postsynth_vivado_2023.1_amd.json, Quartus
in the logs of static_checks_quartus_23.1). PCL01: the Linux server.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.campaign import toolset

LAPTOP_BANNERS = {
  "vivado": "vivado v2023.1 (64-bit)\n",
  "quartus": "Quartus Prime Shell\nVersion 23.1std.0 Build 991 11/28/2023 SC Lite Edition\n",
  "questa": "Questa Sim-64 vsim 2021.1 Simulator 2021.01 Jan 19 2021\n",
  "ghdl": "GHDL 6.0.0 (tarball) [Dunoon edition]\n",
}
PCL01_BANNERS = {
  "vivado": "vivado v2024.1 (64-bit)\nTool Version Limit: 2024.05\nSW Build 5076996 on Wed May 22 18:36:09 MDT 2024\n",
  "quartus": "Quartus Prime Shell\nVersion 25.1std.0 Build 1129 10/21/2025 SC Lite Edition\n",
  "questa": "Questa Altera Starter FPGA Edition-64 vsim 2025.2 Simulator 2025.05 May 31 2025\n",
  "ghdl": "GHDL 6.0.0 (6.0.0.r0.ge589c698c) [Dunoon edition]\n llvm 18.1.3 code generator\n",
}


def make(banners: dict[str, str]) -> dict:
  """The toolset that detect() gives for these banners."""
  commands = {tool: [tool, "--version"] for tool in banners}
  return toolset.detect(commands, run=lambda command: banners[command[0]])


LAPTOP = make(LAPTOP_BANNERS)
PCL01 = make(PCL01_BANNERS)


def write(root: Path, tools: dict = LAPTOP) -> None:
  """<root>/logs/toolset.json, as run_amd.sh writes it."""
  (root / "logs").mkdir(parents=True, exist_ok=True)
  (root / "logs" / toolset.TOOLSET_FILE).write_text(json.dumps(tools), encoding="utf-8")
