# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""Tests for result extraction and expected-result checks in vendor_synth.

A fake Vivado (a Python script) prints a synthesis log and writes the
utilization report, so the whole run/evaluate path is exercised without the
real tool. The tests use a private lock file.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from unittest import mock

from tools.eda_archive import sha256_file
from tools.vendor_synth import check
from tools.vendor_synth.cli import main

UTIL_TEMPLATE = """\
+-------------------------+------+-------+------------+-----------+-------+
|        Site Type        | Used | Fixed | Prohibited | Available | Util% |
+-------------------------+------+-------+------------+-----------+-------+
| Slice LUTs*             | {luts} |     0 |          0 |     20800 |  0.00 |
|   LUT as Logic          | {luts} |     0 |          0 |     20800 |  0.00 |
|   LUT as Memory         | {lutram} |     0 |          0 |      9600 |  0.00 |
| Slice Registers         | {regs} |     0 |          0 |     41600 |  0.00 |
| Block RAM Tile    | {bram} |     0 |          0 |        50 |  2.00 |
"""

FAKE_VIVADO = r'''
import os, re, sys
script = open(sys.argv[sys.argv.index("-source") + 1]).read()
cfg = os.environ["FAKE_VIVADO"]
state = os.path.join(os.path.dirname(cfg), "calls.txt")
calls = int(open(state).read()) if os.path.exists(state) else 0
open(state, "w").write(str(calls + 1))
spec = eval(open(cfg).read())
print("****** Vivado v2023.1 (64-bit)")
if calls < spec.get("fail_reads", 0):
    kinds = spec.get("fail_kinds", ["read"])
    kind = kinds[calls % len(kinds)]
    if kind == "read":
        print('couldn\'t read file "C:/Xilinx/Vivado/2023.1/scripts/rt/data/x.tcl": No error')
    elif kind == "rt":
        print('invalid command name "rt-undefined"')
    else:
        print("synth_design failed")
    print("ERROR: [Common 17-69] Command failed: Vivado Synthesis failed")
    sys.exit(1)
os.makedirs(".Xil", exist_ok=True)  # tool scratch, removed after a passing run
open(os.path.join(".Xil", "junk.tcl"), "w").write("x")
top = re.search(r"-top (\S+)", script).group(1)
print("Command: synth_design -mode out_of_context -top " + top)
for module, generics in [(top, spec["generics"])] + spec.get("modules", []):
    print("INFO: [Synth 8-638] synthesizing module '%s' [x.vhd:1]" % module)
    for name, value in generics.items():
        print("\tParameter %s bound to: %s - type: integer " % (name, value))
    print("INFO: [Synth 8-256] done synthesizing module '%s'" % module)
for line in spec.get("extra", []):
    print(line)
if spec.get("truncate"):
    sys.exit(0)  # the log ends here: no report, no completion message, exit code 0
util = re.search(r"report_utilization -file \{([^}]+)\}", script).group(1)
open(util, "w").write(spec["util"])
print("synth_design completed successfully")
sys.exit(spec.get("exit_code", 0))
'''


class CheckParsingTest(unittest.TestCase):
  def test_parse_log_ignores_echoed_commands_and_reads_top_generics(self) -> None:
    log = textwrap.dedent("""\
      ****** Vivado v2023.1 (64-bit)
      # set_msg_config -id {Synth 8-6859} -limit 1000000
      CRITICAL WARNING: [Common 17-741] No write access right to the local Tcl store
      Command: synth_design -mode out_of_context -top lm_mem_ram_r_w
      INFO: [Synth 8-638] synthesizing module 'lm_mem_ram_r_w' [a.vhd:1]
      \tParameter g_ram_depth bound to: 64 - type: integer
      \tParameter g_init_file bound to: (null) - type: string
      INFO: [Synth 8-638] synthesizing module 'lm_mem_ram_sdp' [b.vhd:1]
      \tParameter g_wr_depth bound to: 64 - type: integer
      CRITICAL WARNING: [Synth 8-6859] multi-driven net on pin x
      """)
    parsed = check.parse_vivado_log(log, "lm_mem_ram_r_w")
    self.assertEqual(parsed["version"], "2023.1")
    self.assertEqual(parsed["critical_warnings"], 1)
    self.assertEqual(parsed["critical_warnings_outside_synth"], 1)
    self.assertEqual(parsed["message_ids"]["Synth 8-6859"], 1)
    self.assertEqual(parsed["generics"]["g_ram_depth"], ("64", "integer"))
    self.assertNotIn("g_wr_depth", parsed["generics"])

  def test_generic_mismatch_is_reported(self) -> None:
    problems = check.check_generics({"g_ram_depth": 64}, {"g_ram_depth": ("0", "integer")})
    self.assertEqual(len(problems), 1)
    self.assertIn("synthesized with 0", problems[0])

  def test_utilization_names_for_both_families(self) -> None:
    us = ("| CLB LUTs*  |    3 |\n|   LUT as Memory |    0 |\n| CLB Registers |   5 |\n| Block RAM Tile |  0.5 |\n"
          "|   RAMB36/FIFO*    |    0 |\n|   RAMB18          |    1 |\n| URAM |    0 |\n")
    m = check.parse_vivado_utilization(us)
    self.assertEqual((m["luts"], m["registers"], m["bram_tiles"], m["uram"]), (3.0, 5.0, 0.5, 0.0))
    self.assertEqual((m["ramb36"], m["ramb18"]), (0.0, 1.0))
    # A missing row is unknown, never 0.
    self.assertIsNone(check.parse_vivado_utilization("| CLB LUTs*  |    3 |\n")["uram"])

  def test_uram_absent_only_on_families_without_uram(self) -> None:
    log = {"critical_warnings": 0, "message_ids": {}, "limited_messages": []}
    metrics = {"luts": 0.0, "luts_logic": 0.0, "lutram": 0.0, "registers": 0.0, "bram_tiles": 1.0, "uram": None}
    self.assertEqual(check.compare(metrics, log, {"uram": 0}, "xc7a35tfgg484-2"), [])
    self.assertEqual(len(check.compare(metrics, log, {"uram": 0}, "xcku3p-ffva676-2-e")), 1)

  def test_compare_flags_every_kind_of_mismatch(self) -> None:
    log = {"critical_warnings": 2, "message_ids": {"Synth 8-5787": 4}, "limited_messages": ["Synth 8-1"]}
    metrics = {"bram_tiles": 0.0, "lutram": 0.0, "uram": 0.0, "registers": 65568.0, "luts": 9845.0, "luts_logic": 9845.0}
    expectation = {"bram_tiles": 1, "lutram": 0, "uram": 0, "max_registers": 0, "max_luts": 0,
                   "max_critical_warnings": 0, "forbidden_messages": ["Synth 8-5787"]}
    problems = check.compare(metrics, log, expectation)
    self.assertEqual(len(problems), 6, problems)


class VendorRunTest(unittest.TestCase):
  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.root = Path(self.tmp.name)
    self.fake = self.root / "fake_vivado.py"
    self.fake.write_text(FAKE_VIVADO, encoding="utf-8")
    self.spec = self.root / "spec.txt"
    self.old_env = {k: os.environ.get(k) for k in ("FAKE_VIVADO", "TEMP", "TMP", "TMPDIR", "LM_EDA_MIN_FREE_MB_SYNTH")}
    os.environ["FAKE_VIVADO"] = str(self.spec)
    os.environ["LM_EDA_MIN_FREE_MB_SYNTH"] = "0"  # fake tools: no memory wait
    lockdir = self.root / "lockdir"
    lockdir.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR"):
      os.environ[key] = str(lockdir)
    tempfile.tempdir = None

  def tearDown(self) -> None:
    for key, value in self.old_env.items():
      if value is None:
        os.environ.pop(key, None)
      else:
        os.environ[key] = value
    tempfile.tempdir = None
    self.tmp.cleanup()

  def _run(self, spec: dict, case: str = "r_w_16x64", part: str = "xc7a35tfgg484-2", executable=None,
           extra_args: tuple[str, ...] = ()) -> dict:
    self.spec.write_text(repr(spec), encoding="utf-8")
    config = self.root / "config.json"
    work = self.root / "work"
    config.write_text(json.dumps({
      "work_root": str(work),
      "jobs": [{
        "name": "fake", "enabled": True, "tool": "vivado", "vendor": "amd", "family": "artix7",
        "part": part, "command": executable or [sys.executable, str(self.fake)], "cases": [case],
        "install_dir": "C:/Xilinx/Vivado/2023.1",
      }],
    }), encoding="utf-8")
    code = main(["--config", str(config), *extra_args])
    result = json.loads((work / "summary.json").read_text(encoding="utf-8"))["results"][0]
    result["exit_code"] = code
    return result

  def _good_spec(self, **overrides) -> dict:
    spec = {
      "generics": {"g_ram_data_w": 16, "g_ram_depth": 64, "g_ram_latency": 1},
      "util": UTIL_TEMPLATE.format(luts=0, lutram=0, regs=0, bram=0.5),
    }
    spec.update(overrides)
    return spec

  def test_pass_only_when_results_match(self) -> None:
    result = self._run(self._good_spec())
    self.assertEqual(result["status"], "pass", result["problems"])
    self.assertEqual(result["exit_code"], 0)
    self.assertEqual(result["tool_version"], "2023.1")
    # A passing run keeps its log, reports and script only.
    kept = sorted(p.name for p in Path(result["work_dir"]).iterdir())
    self.assertNotIn(".Xil", kept)
    self.assertIn("run.log", kept)
    self.assertTrue((Path(result["work_dir"]) / "reports" / "utilization.rpt").is_file())

  def test_flip_flop_ram_fails(self) -> None:
    result = self._run(self._good_spec(util=UTIL_TEMPLATE.format(luts=338, lutram=0, regs=1040, bram=0)))
    self.assertEqual(result["status"], "fail")
    self.assertEqual(result["exit_code"], 1)
    self.assertTrue((Path(result["work_dir"]) / ".Xil").is_dir(), "a failing run keeps everything")

  def test_wrong_generic_value_fails(self) -> None:
    result = self._run(self._good_spec(generics={"g_ram_data_w": 16, "g_ram_depth": 0, "g_ram_latency": 1}))
    self.assertEqual(result["status"], "fail")
    self.assertTrue(any("g_ram_depth" in p for p in result["problems"]))

  def test_message_limit_reached_fails(self) -> None:
    extra = ["INFO: [Common 17-14] Message 'Synth 8-3848' appears 100 times and further instances "
             "of the messages will be disabled."]
    result = self._run(self._good_spec(extra=extra))
    self.assertEqual(result["status"], "fail")

  def test_missing_expectation_is_unchecked_not_pass(self) -> None:
    result = self._run(self._good_spec(), part="xc7z010clg400-1")
    self.assertEqual(result["status"], "unchecked")
    self.assertEqual(result["exit_code"], 1)

  def test_missing_tool_is_not_run(self) -> None:
    result = self._run(self._good_spec(), executable=str(self.root / "no_such_vivado.bat"))
    self.assertEqual(result["status"], "not-run")
    self.assertEqual(result["exit_code"], 1)

  def test_transient_failures_are_retried_and_recorded(self) -> None:
    result = self._run(self._good_spec(fail_reads=2, fail_kinds=["rt", "silent"]))
    self.assertEqual(result["status"], "pass", result["problems"])
    self.assertEqual(len(result["retries"]), 2)
    self.assertIn("rt-undefined", result["retries"][0])
    self.assertIn("silent-stop", result["retries"][1])

  def test_evaluate_only_rechecks_without_running(self) -> None:
    first = self._run(self._good_spec())
    self.assertEqual(first["status"], "pass")
    config = self.root / "config.json"
    work = self.root / "work"
    calls_before = (self.root / "calls.txt").read_text()
    code = main(["--config", str(config), "--evaluate-only"])
    result = json.loads((work / "summary.json").read_text(encoding="utf-8"))["results"][0]
    self.assertEqual(code, 0)
    self.assertEqual(result["status"], "pass")
    self.assertEqual(result["command"], [])
    self.assertEqual((self.root / "calls.txt").read_text(), calls_before, "the tool must not run")
    public = json.loads((work / "summary_public.json").read_text(encoding="utf-8"))["results"][0]
    self.assertNotIn("work_dir", public)
    self.assertNotIn("command", public)

  def test_retries_are_capped(self) -> None:
    result = self._run(self._good_spec(fail_reads=5))
    self.assertEqual(result["status"], "tool-error")
    self.assertEqual(result["exit_code"], 1)
    self.assertEqual(len(result["retries"]), 3, "every failed attempt is recorded")
    self.assertIn("transient tool failure in all 3 attempts", result["problems"][0])
    self.assertEqual((self.root / "calls.txt").read_text(), "3")
    # The complete log of every retried attempt is kept.
    kept = sorted(p.name for p in Path(result["work_dir"]).glob("run.attempt*.log"))
    self.assertEqual(kept, ["run.attempt1.log", "run.attempt2.log", "run.attempt3.log"])

  # False passes found by the reviews of PR #4.

  def test_truncated_log_is_not_a_pass(self) -> None:
    result = self._run(self._good_spec(truncate=True))
    self.assertEqual(result["status"], "tool-error")
    self.assertIn("synth_design completed successfully", result["problems"][0])

  def test_truncated_run_does_not_reuse_the_reports_of_an_earlier_run(self) -> None:
    self.assertEqual(self._run(self._good_spec())["status"], "pass")
    (self.root / "calls.txt").unlink()
    result = self._run(self._good_spec(truncate=True), extra_args=("--no-clean",))
    self.assertEqual(result["status"], "tool-error", result["problems"])
    self.assertIn("no utilization report", result["problems"][0])

  def test_nonzero_exit_code_is_not_a_pass(self) -> None:
    result = self._run(self._good_spec(exit_code=3))
    self.assertEqual(result["status"], "tool-error")
    self.assertIn("tool exit code 3", result["problems"][0])

  def test_harmless_line_in_a_completed_run_is_not_retried(self) -> None:
    extra = ['couldn\'t read file "C:/Xilinx/Vivado/2023.1/scripts/init.tcl": no such file or directory']
    result = self._run(self._good_spec(extra=extra))
    self.assertEqual(result["status"], "pass", result["problems"])
    self.assertEqual(result["retries"], [])

  def test_evaluate_only_applies_the_transient_signatures(self) -> None:
    self.assertEqual(self._run(self._good_spec())["status"], "pass")
    # A run that stopped on a broken generated script, after its reports.
    log = self.root / "work" / "fake" / "r_w_16x64" / "run.log"
    log.write_text(log.read_text().replace("synth_design completed successfully", 'invalid command name "rt-undefined"'))
    code = main(["--config", str(self.root / "config.json"), "--evaluate-only"])
    result = json.loads((self.root / "work" / "summary.json").read_text(encoding="utf-8"))["results"][0]
    self.assertEqual(code, 1)
    self.assertEqual(result["status"], "tool-error")
    self.assertIn("rt-undefined", result["problems"][0])

  def test_evaluate_only_refuses_a_run_of_other_inputs(self) -> None:
    self.assertEqual(self._run(self._good_spec())["status"], "pass")
    record_file = self.root / "work" / "fake" / "r_w_16x64" / "run.json"
    record = json.loads(record_file.read_text())
    record["provenance"]["sources_hash"] = "0" * 64
    record_file.write_text(json.dumps(record))
    code = main(["--config", str(self.root / "config.json"), "--evaluate-only"])
    result = json.loads((self.root / "work" / "summary.json").read_text(encoding="utf-8"))["results"][0]
    self.assertEqual(code, 1)
    self.assertEqual(result["status"], "not-run")
    self.assertIn("other inputs", result["problems"][0])

  def test_missing_uram_row_on_ultrascale_plus_fails(self) -> None:
    util = UTIL_TEMPLATE.format(luts=1, lutram=20, regs=16, bram=0)
    result = self._run(self._good_spec(util=util), part="xcku3p-ffva676-2-e")
    self.assertEqual(result["status"], "fail")
    self.assertTrue(any("no URAM row" in p for p in result["problems"]), result["problems"])
    result = self._run(self._good_spec(util=util + "| URAM              |    0 |\n"), part="xcku3p-ffva676-2-e")
    self.assertEqual(result["status"], "pass", result["problems"])

  def test_incomplete_utilization_report_fails(self) -> None:
    util = "\n".join(l for l in UTIL_TEMPLATE.format(luts=0, lutram=0, regs=0, bram=0.5).splitlines()
                     if "Registers" not in l)
    result = self._run(self._good_spec(util=util))
    self.assertEqual(result["status"], "fail")
    self.assertTrue(any("incomplete: no registers" in p for p in result["problems"]), result["problems"])

  def test_campaign_keeps_everything_and_records_the_archive(self) -> None:
    archive = self.root / "archive"
    with mock.patch.dict(os.environ, {"LM_EDA_ARCHIVE_DIR": str(archive)}):
      result = self._run(self._good_spec(fail_reads=1), extra_args=("--campaign", "test"))
    self.assertEqual(result["status"], "pass", result["problems"])
    work_dir = Path(result["work_dir"])
    self.assertTrue((work_dir / ".Xil").is_dir(), "a campaign run is not pruned")
    self.assertTrue((work_dir / "run.attempt1.log").is_file())
    public = json.loads((self.root / "work" / "summary_public.json").read_text(encoding="utf-8"))
    record = public["archive"]
    self.assertEqual(record["sha256"], sha256_file(archive / record["archive"]))
    self.assertTrue(public["results"][0]["sources_hash"])
    # A campaign needs a new work root.
    with mock.patch.dict(os.environ, {"LM_EDA_ARCHIVE_DIR": str(archive)}), self.assertRaises(SystemExit):
      self._run(self._good_spec(), extra_args=("--campaign", "test", "--no-clean"))

  def test_generics_of_the_inner_module_are_checked(self) -> None:
    util = UTIL_TEMPLATE.format(luts=10, lutram=0, regs=0, bram=4) + "| URAM              |    0 |\n"
    inner = {"g_ram_data_w": 36, "g_ram_depth": 4096, "g_ram_latency": 1}
    good = self._good_spec(generics={}, util=util, modules=[("lm_mem_ram_crw_cr", inner)])
    result = self._run(good, case="crw_cr_36x4096_cc", part="xcku3p-ffva676-2-e")
    self.assertEqual(result["status"], "pass", result["problems"])
    bad = self._good_spec(generics={}, util=util, modules=[("lm_mem_ram_crw_cr", {**inner, "g_ram_depth": 4000})])
    result = self._run(bad, case="crw_cr_36x4096_cc", part="xcku3p-ffva676-2-e")
    self.assertEqual(result["status"], "fail")
    self.assertTrue(any("requested 4096, synthesized with 4000" in p for p in result["problems"]))


class ExpectedFailureTest(unittest.TestCase):
  EXPECT = {"expect_failure": ["no validated two-writer RAM architecture"],
            "expect_failure_constant": "C_LM_MEM_TDP_NOT_SUPPORTED"}
  SOURCE = ("library ieee;\n"
            "architecture a_rtl of e is\n"
            "  constant C_OK : boolean := false;\n"
            "  constant C_LM_MEM_TDP_NOT_SUPPORTED : t_static_check := f_static_check(C_OK);  -- ERROR: why\n"
            "begin\n"
            "end architecture;\n")

  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.src = Path(self.tmp.name) / "fallback.vhd"
    self.src.write_text(self.SOURCE, encoding="utf-8")
    self.other = Path(self.tmp.name) / "other.vhd"
    self.other.write_text(self.SOURCE, encoding="utf-8")

  def tearDown(self) -> None:
    self.tmp.cleanup()

  def _range_error(self, path: Path, line: int) -> str:
    # The shape of a real Vivado 2023.1 log (bad_r_w_latency3).
    return (f"Command: synth_design -top e\n"
            f"INFO: [Synth 8-638] synthesizing module 'e' [{self.src.as_posix()}:2]\n"
            f"ERROR: [Synth 8-11323] assigned value '1' out of range [{path.as_posix()}:{line}]\n"
            f"ERROR: [Synth 8-285] failed synthesizing module 'e' [{self.src.as_posix()}:2]\n"
            f"synth_design failed\n"
            f"ERROR: [Common 17-69] Command failed: Vivado Synthesis failed\n")

  def test_vivado_range_error_at_the_constant_passes(self) -> None:
    problems, facts = check.check_expected_failure("vivado", self._range_error(self.src, 4), self.EXPECT, [self.src],
                                                   return_code=1)
    self.assertEqual(problems, [])
    self.assertEqual(facts["check_lines"], [f"{self.src.as_posix()}:4"])
    self.assertIn("Synth 8-11323", facts["fallback_messages"][0])

  def test_vivado_range_error_elsewhere_fails(self) -> None:
    for log in (self._range_error(self.src, 3), self._range_error(self.other, 4)):
      problems, _ = check.check_expected_failure("vivado", log, self.EXPECT, [self.src])
      self.assertEqual(len(problems), 1, problems)
      self.assertIn("names the failing check", problems[0])

  def test_message_outside_an_error_does_not_count(self) -> None:
    # Reviews of PR #4: an INFO or WARNING line with the constant name or the
    # message passed, with no error and no evidence of a stop.
    for log in ("INFO: diagnostic source: C_LM_MEM_TDP_NOT_SUPPORTED\n",
                "WARNING: [Synth 8-63] RTL assertion: \"no validated two-writer RAM architecture\"\n"):
      for assert_mode in (False, True):
        problems, _ = check.check_expected_failure("vivado", log, self.EXPECT, [self.src], assert_mode=assert_mode)
        self.assertTrue(any("names the failing check" in p for p in problems), (log, problems))
        self.assertTrue(any("did not report a stop" in p for p in problems), (log, problems))

  def test_each_vivado_mode_needs_its_own_message(self) -> None:
    # Without -assert the range error is the only evidence; with -assert, the assertion.
    problems, _ = check.check_expected_failure("vivado", self._range_error(self.src, 4), self.EXPECT, [self.src],
                                               assert_mode=True)
    self.assertTrue(any("[Synth 8-63]" in p for p in problems), problems)

  def test_exit_code_zero_is_not_a_stop(self) -> None:
    problems, _ = check.check_expected_failure("vivado", self._range_error(self.src, 4), self.EXPECT, [self.src],
                                               return_code=0)
    self.assertEqual(problems, ["tool exit code 0, but synthesis was expected to stop"])

  def test_tool_failure_before_elaboration_is_not_elaborated(self) -> None:
    log = ("Command: synth_design -top e -assert\n"
           "ERROR: [Common 17-680] Path length exceeds 260-Byte maximum allowed by Windows: C:/x\n"
           "ERROR: [Common 17-69] Command failed: Vivado Synthesis failed\n")
    problems, facts = check.check_expected_failure("vivado", log, self.EXPECT, [self.src])
    self.assertTrue(problems)
    self.assertFalse(facts["elaborated"])
    _, facts = check.check_expected_failure("vivado", self._range_error(self.src, 4), self.EXPECT, [self.src])
    self.assertTrue(facts["elaborated"])

  def test_missing_constant_in_sources_fails(self) -> None:
    problems, _ = check.check_expected_failure("vivado", self._range_error(self.src, 4), self.EXPECT, [])
    self.assertTrue(any("not found in the compiled sources" in p for p in problems))

  def test_vivado_stop_with_fallback_message_passes(self) -> None:
    log = (f'Command: synth_design -top lm_mem_ram_crw_crw -assert\n'
           f"INFO: [Synth 8-638] synthesizing module 'e' [{self.src.as_posix()}:2]\n"
           'ERROR: [Synth 8-63] RTL assertion: "lm_mem_ram_crw_crw: no validated two-writer RAM architecture '
           'for this tool." [x.vhd:31]\n'
           'synth_design failed\n'
           'ERROR: [Common 17-69] Command failed: Synthesis failed\n')
    problems, facts = check.check_expected_failure("vivado", log, self.EXPECT, [self.src], assert_mode=True)
    self.assertEqual(problems, [])
    self.assertTrue(facts["stopped"])
    self.assertEqual(len(facts["fallback_messages"]), 1)

  def test_completed_synthesis_fails(self) -> None:
    log = "Command: synth_design\nsynth_design completed successfully\n"
    problems, _ = check.check_expected_failure("vivado", log, self.EXPECT, [self.src])
    self.assertIn("synthesis completed, but it was expected to stop", problems)

  def test_quartus_stop_with_constant_message_passes(self) -> None:
    log = (f'Error (10405): VHDL error at {self.src.name}(4): value "1" is out of target constraint range (0 to 0) '
           'for C_LM_MEM_TDP_NOT_SUPPORTED\n'
           'Error: Quartus Prime Analysis & Synthesis was unsuccessful. 1 error, 0 warnings\n')
    problems, facts = check.check_expected_failure("quartus", log, self.EXPECT, [self.src])
    self.assertEqual(problems, [])
    self.assertEqual(len(facts["errors"]), 1)

  def test_quartus_constant_named_elsewhere_fails(self) -> None:
    log = ('Error (10405): VHDL error at x.vhd(30): value "1" is out of target constraint range (0 to 0) '
           'for C_LM_MEM_TDP_NOT_SUPPORTED\n'
           'Error: Quartus Prime Analysis & Synthesis was unsuccessful. 1 error, 0 warnings\n')
    problems, _ = check.check_expected_failure("quartus", log, self.EXPECT, [self.src])
    self.assertTrue(any("names the failing check" in p for p in problems), problems)

  def test_stop_without_fallback_message_fails(self) -> None:
    log = ("Error (12006): Node instance inst instantiates undefined entity foo\n"
           "Error: Quartus Prime Analysis & Synthesis was unsuccessful. 1 error, 0 warnings\n")
    problems, _ = check.check_expected_failure("quartus", log, self.EXPECT, [self.src])
    self.assertEqual(len(problems), 2, problems)
    self.assertIn("names the failing check", problems[0])

  def test_quartus_success_fails(self) -> None:
    log = "Info: Quartus Prime Analysis & Synthesis was successful. 0 errors, 3 warnings\n"
    problems, _ = check.check_expected_failure("quartus", log, self.EXPECT, [self.src])
    self.assertIn("synthesis completed", problems[0])

  def test_every_expected_constant_exists_in_its_flow(self) -> None:
    from tools.manifest import load
    sources = {flow: [s.path for s in load().files(flow)] for flow in ("synth-amd", "synth-intel", "synth-unsupported")}
    for vendor, flows in (("amd", ("synth-amd", "synth-unsupported")), ("intel", ("synth-intel",))):
      for entry in check.load_expected(vendor)["expect"]:
        name = entry.get("expect_failure_constant")
        if name:
          found = [p for flow in flows for p in check.locate_constant(name, sources[flow])]
          self.assertTrue(found, f"{vendor} {entry['case']}: {name} not declared in {flows}")


if __name__ == "__main__":
  unittest.main()
