# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""One retry policy for every lm_memories script that runs a heavy EDA tool.

Some tool failures say nothing about the design: on the reference machine
Vivado 2023.1 sometimes cannot read files of its own installation or files
it generated itself, runs a broken script it generated, or stops without an
error message. Such a run is run again, at most MAX_RETRIES times. Rules, in
this order, for each attempt:

1. A completed run is a result and is never retried, whatever its log says
   (completed: a predicate of the caller, for example "synth_design
   completed successfully" and exit code 0).
2. A run in which the tool died during its step is retried, whatever its log
   says (died: a predicate of the caller, for example the status file of
   synth_design still at "started", tools/vendor_synth/check.py). The run
   has no result, even when the log already shows an error of the project:
   on 2026-11-04 Vivado printed the expected stop of a static check and
   then died before synth_design returned (signature "tool-died").
3. A run with an error of the project (any error message other than the
   generic "command failed" of the tool) is a result and is never retried.
4. Otherwise the run is retried if its log has one of the signatures below.
   "couldn't read file" counts only for a file of the tool installation
   (roots, given by the caller) or one the tool generated (a path through a
   .Xil directory).

Before each rerun the policy waits: RETRY_DELAYS seconds (30 s before the
first rerun, 60 s before the second), or the comma-separated seconds of
$LM_EDA_RETRY_DELAYS (for example "30,60"; "0" for no wait; the last value
also serves later reruns). The failures of 2026-11-04 came in bursts: three
runs within 30 seconds all failed. The wait is outside the machine-wide EDA
lock, which each run takes for itself.

Every retried attempt is recorded with its signature and log line, and the
wait that followed it ("attempt <n>: <reason>; waited <s> s before attempt
<n+1>"); its complete log is kept next to the log of the run (log_path), as
<name>.attempt<n><suffix>. If the failure persists, the caller reports the
run as tool-error, never as a result.

  from tools.eda_retry import run_with_retries
  text, attempts, persisted = run_with_retries(run_once, completed=done, roots=[install_dir], log_path=log)
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_RETRIES = 2
# Seconds to wait before the first, second, ... rerun; the last value also
# serves later reruns. Overridden by $LM_EDA_RETRY_DELAYS ("30,60").
RETRY_DELAYS = (30.0, 60.0)
RETRY_DELAYS_ENV = "LM_EDA_RETRY_DELAYS"
_WAIT_RE = re.compile(r"; waited [0-9.]+ s before attempt \d+$")

# Error messages that only say that a command failed; the cause is elsewhere.
GENERIC_ERRORS = ("Common 17-69",)
_VIVADO_ERROR_RE = re.compile(r"^ERROR: \[([^\]]+)\]")
_QUARTUS_ERROR_RE = re.compile(r"^\s*Error \((\d+)\)")
# Quartus summary lines ("... was unsuccessful").
_QUARTUS_GENERIC = ("293001",)
_UNREADABLE_RE = re.compile(r"couldn't read file \"([^\"]+)\"")


@dataclass(frozen=True)
class Signature:
  name: str
  meaning: str
  match: Callable[[list[str], tuple[str, ...]], str | None]


def _norm(path: str) -> str:
  return path.replace("\\", "/").rstrip("/").lower()


def _tool_file(path: str, roots: tuple[str, ...]) -> bool:
  p = _norm(path)
  return "/.xil/" in p or any(p == r or p.startswith(r + "/") for r in roots)


def _unreadable_tool_file(lines: list[str], roots: tuple[str, ...]) -> str | None:
  for line in lines:
    match = _UNREADABLE_RE.search(line)
    if match and _tool_file(match.group(1), roots):
      return line.strip()
  return None


def _line_with(text: str) -> Callable[[list[str], tuple[str, ...]], str | None]:
  def match(lines: list[str], roots: tuple[str, ...]) -> str | None:
    return next((line.strip() for line in lines if text in line), None)
  return match


def _silent_stop(lines: list[str], roots: tuple[str, ...]) -> str | None:
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
            _unreadable_tool_file),
  Signature("rt-undefined", "the tool runs a broken script that it generated (.Xil)",
            _line_with('invalid command name "rt-undefined"')),
  Signature("silent-stop", "synthesis stops without an error message", _silent_stop),
)


def _roots(roots: Iterable[str | Path]) -> tuple[str, ...]:
  return tuple(_norm(str(r)) for r in roots)


def project_error(text: str) -> str | None:
  """The first error message of the project in a tool log (not a generic "command failed"), else None."""
  for line in text.splitlines():
    match = _VIVADO_ERROR_RE.match(line)
    if match and match.group(1) not in GENERIC_ERRORS:
      return line.strip()
    match = _QUARTUS_ERROR_RE.match(line)
    if match and match.group(1) not in _QUARTUS_GENERIC:
      return line.strip()
  return None


def transient_reason(text: str, roots: Iterable[str | Path] = ()) -> str | None:
  """'<signature>: <log line>' for the first transient signature in a tool log, else None.

  Only the signatures: a caller decides with retry_reason, which also applies
  rules 1 and 2.
  """
  lines = text.splitlines()
  normalized = _roots(roots)
  for signature in SIGNATURES:
    line = signature.match(lines, normalized)
    if line is not None:
      return f"{signature.name}: {line[:200]}"
  return None


def retry_reason(text: str, completed: bool, roots: Iterable[str | Path] = (), died: str | None = None) -> str | None:
  """Why this attempt must be run again, or None if its log is a result.

  died: why the tool died during its step (the caller knows), or None.
  """
  if completed:
    return None
  if died is not None:
    return f"tool-died: {died}"
  if project_error(text) is not None:
    return None
  return transient_reason(text, roots)


def retry_delays() -> tuple[float, ...]:
  """Seconds to wait before each rerun: $LM_EDA_RETRY_DELAYS if set, else RETRY_DELAYS."""
  value = os.environ.get(RETRY_DELAYS_ENV, "").strip()
  if not value:
    return RETRY_DELAYS
  try:
    delays = tuple(float(v) for v in value.split(","))
  except ValueError:
    delays = ()
  if not delays or any(d < 0 for d in delays):
    raise ValueError(f"{RETRY_DELAYS_ENV}={value!r}: expected seconds separated by commas, for example 30,60")
  return delays


def retry_delay(rerun: int, delays: tuple[float, ...] | None = None) -> float:
  """Seconds to wait before rerun number rerun (1 for the first rerun)."""
  delays = retry_delays() if delays is None else delays
  return delays[min(rerun, len(delays)) - 1]


def wait_record(seconds: float, next_attempt: int) -> str:
  """The suffix of an attempt record for the wait that followed it."""
  return f"; waited {seconds:g} s before attempt {next_attempt}"


def record_reason(entry: str) -> str:
  """The signature and log line of an attempt record, without "attempt <n>: " and the wait."""
  if entry.startswith("attempt ") and ": " in entry:
    entry = entry.split(": ", 1)[1]
  return _WAIT_RE.sub("", entry)


def attempt_log(log_path: Path, attempt: int) -> Path:
  """Where the complete log of a retried attempt (1-based) is kept."""
  return log_path.with_name(f"{log_path.stem}.attempt{attempt}{log_path.suffix}")


def run_with_retries(
  run_once: Callable[[int], str],
  completed: Callable[[str], bool] = lambda text: False,
  roots: Iterable[str | Path] = (),
  log_path: Path | None = None,
  max_retries: int = MAX_RETRIES,
  died: Callable[[str], str | None] = lambda text: None,
  delays: tuple[float, ...] | None = None,
  sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, list[str], bool]:
  """Run a tool until its log is a result, at most 1 + max_retries times.

  run_once(attempt) runs the tool once, writing its log to log_path if given,
  and returns the log text; completed(text) tells whether that run completed,
  died(text) why the tool died during its step (rule 2) or None.
  Before each rerun it waits retry_delay (delays: default retry_delays()).
  Returns the last log text, one record per retried attempt ("attempt <n>:
  <reason>", with "; waited <s> s before attempt <n+1>" when a rerun
  followed), and whether the transient failure persisted (then the run is a
  tool-error).
  """
  attempts: list[str] = []
  roots = tuple(roots)
  delays = retry_delays() if delays is None else delays
  for attempt in range(max_retries + 1):
    text = run_once(attempt)
    done = completed(text)
    reason = retry_reason(text, done, roots, None if done else died(text))
    if reason is None:
      return text, attempts, False
    if log_path is not None and log_path.is_file():
      shutil.copyfile(log_path, attempt_log(log_path, attempt + 1))
    record = f"attempt {attempt + 1}: {reason}"
    if attempt < max_retries:
      seconds = retry_delay(attempt + 1, delays)
      record += wait_record(seconds, attempt + 2)
      attempts.append(record)
      sleep(seconds)
    else:
      attempts.append(record)
  return text, attempts, True
