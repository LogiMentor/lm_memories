# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""One retry policy for every lm_memories script that runs a heavy EDA tool.

Some tool failures say nothing about the design: on the reference machine
Vivado 2023.1 sometimes cannot read files of its own installation, runs a
broken script it generated, or stops without an error message. A run that
fails with one of the signatures below is run again, at most MAX_RETRIES
times. Every failed attempt is recorded with its signature and log line. If
the failure persists, the caller reports the run as tool-error, never as a
result. Any other failure is not retried.

  from tools.eda_retry import run_with_retries
  text, attempts, persisted = run_with_retries(lambda attempt: run_tool_once())
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

MAX_RETRIES = 2


@dataclass(frozen=True)
class Signature:
  name: str
  meaning: str
  match: Callable[[list[str]], str | None]


def _line_with(text: str) -> Callable[[list[str]], str | None]:
  def match(lines: list[str]) -> str | None:
    return next((line.strip() for line in lines if text in line), None)
  return match


def _silent_stop(lines: list[str]) -> str | None:
  """synth_design failed, and the only error is the generic [Common 17-69]."""
  if not any(line.startswith("synth_design failed") for line in lines):
    return None
  errors = {line.split("]")[0] + "]" for line in lines if line.startswith("ERROR: [")}
  if errors <= {"ERROR: [Common 17-69]"}:
    return "synth_design failed without an error message"
  return None


# The single list of transient signatures. Order matters only for the
# reported reason: the first signature that matches is recorded.
SIGNATURES = (
  Signature("unreadable-file", "the tool cannot read a file of its installation or one it generated",
            _line_with("couldn't read file")),
  Signature("rt-undefined", "the tool runs a broken script that it generated (.Xil)",
            _line_with('invalid command name "rt-undefined"')),
  Signature("silent-stop", "synthesis stops without an error message", _silent_stop),
)


def transient_reason(text: str) -> str | None:
  """'<signature>: <log line>' for the first transient signature in a tool log, else None."""
  lines = text.splitlines()
  for signature in SIGNATURES:
    line = signature.match(lines)
    if line is not None:
      return f"{signature.name}: {line[:200]}"
  return None


def run_with_retries(
  run_once: Callable[[int], str],
  max_retries: int = MAX_RETRIES,
) -> tuple[str, list[str], bool]:
  """Run a tool until its log shows no transient signature, at most 1 + max_retries times.

  run_once(attempt) runs the tool once and returns its log text. Returns the
  last log text, one record per failed attempt ("attempt <n>: <reason>"),
  and whether the transient failure persisted (then the run is a tool-error).
  """
  attempts: list[str] = []
  for attempt in range(max_retries + 1):
    text = run_once(attempt)
    reason = transient_reason(text)
    if reason is None:
      return text, attempts, False
    attempts.append(f"attempt {attempt + 1}: {reason}")
  return text, attempts, True
