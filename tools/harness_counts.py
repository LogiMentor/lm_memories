# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Counters of the final line of the VUnit harness (f_tb_counts_image in lm_mem_tb_pkg).

  lm_mem_tb_harness <dut> [<simulator>]: writes=860 ... errors=0

Used by the post-synthesis simulation (sim/scripts/run_postsynth.py) and the
VUnit campaign (sim/scripts/run_vunit_campaign.py). A result counts only if
every counter of REQUIRED_COUNTS is there exactly once as name=<digits>,
nothing else is on the line, and errors=0 is written explicitly: an empty
line, a missing, malformed or repeated counter, or errors other than 0 is a
problem.

  from tools.harness_counts import parse_counts
  counts, problems = parse_counts(text_after_the_colon)
"""

from __future__ import annotations

import re

REQUIRED_COUNTS = ("writes", "writes_masked", "writes_beyond_depth", "reads", "checked", "excluded_rw",
                   "excluded_undefined", "excluded_beyond_depth", "collisions_rw", "collisions_ww", "init_reads",
                   "old_data_reads", "hold_checks", "undefined_output_checks", "errors")
_COUNTER_RE = re.compile(r"(\w+)=(\d+)")


def parse_counts(text: str) -> tuple[dict[str, int], list[str]]:
  """(counters, problems) of the counter part of one harness line."""
  counts: dict[str, int] = {}
  problems = []
  tokens = text.split()
  if not tokens:
    return {}, ["harness line without counters"]
  for token in tokens:
    match = _COUNTER_RE.fullmatch(token)
    if not match:
      problems.append(f"malformed harness counter {token!r}")
      continue
    name, value = match[1], int(match[2])
    if name in counts:
      problems.append(f"harness counter {name} repeated")
      continue
    counts[name] = value
  missing = [k for k in REQUIRED_COUNTS if k not in counts]
  if missing:
    problems.append("harness counters missing: " + ", ".join(missing))
  if counts.get("errors", 0) != 0:
    problems.append(f"errors={counts['errors']}")
  return counts, problems
