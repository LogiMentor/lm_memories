# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
"""tools/campaign/publish.py: docs/results/ from a campaign root, on a fake campaign."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from tools.campaign import publish
from tools.campaign.rerun import ROOT_DIR
from tools.tests import toolsets
from tools.vendor_synth import check

C1, C2 = "1" * 40, "2" * 40
REPO = ROOT_DIR.as_posix()


def _write(path: Path, data) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(data), encoding="utf-8")


class Campaign:
  """A campaign root and an archive directory with real archive files."""

  def __init__(self, base: Path):
    self.root, self.archives = base / "c", base / "archive"
    self.archives.mkdir(parents=True)

  def archive(self, name: str, sha: str | None = None) -> dict:
    content = name.encode()
    (self.archives / name).write_bytes(content)
    return {"archive": name, "sha256": sha or hashlib.sha256(content).hexdigest(), "files": 3}

  def synth(self, sub: str, archive: str, results: list[dict], before: list[dict] | None = None,
            where: Path | None = None) -> None:
    work = where or self.root / sub
    full = [{"job": r["job"], "case": r["case"], "status": r["status"],
             "provenance": {"commit": r["commit"], "sources_hash": "s"}} for r in results]
    public = [{k: v for k, v in r.items() if k != "job"} | {"sources_hash": "s"} for r in results]
    _write(work / "summary.json", {"results": full})
    _write(work / "summary_public.json", {"archive": self.archive(archive), "results": public})
    if before is not None:
      _write(work / "summary.before_20261105T000000Z.json",
             {"results": [{"job": r["job"], "case": r["case"], "status": r["status"],
                           "provenance": {"commit": r["commit"]}} for r in before]})


def _result(job, case, status, commit=C1, part="xc7a35tfgg484-2", tool="vivado", version="2023.1", problems=()):
  return {"job": job, "tool": tool, "tool_version": version, "vendor": "amd", "family": "artix7", "part": part,
          "case": case, "top": "t", "generics": {}, "status": status, "metrics": {}, "critical_warnings": 0,
          "retries": 0, "problems": list(problems), "commit": commit,
          "expected": _record(tool, version)}


def _record(tool: str, version: str) -> dict | None:
  """The record of the measured expected results of a tool version, as vendor_synth writes it."""
  try:
    return check.load_expected("intel" if tool == "quartus" else "amd", version)["record"]
  except check.ExpectedError:
    return None


def _ps(archive: dict, commit: str, parts: dict, configs: list[str]) -> dict:
  return {"commit": commit, "manifest_hash": "m", "configs": configs, "archive": archive,
          "rtl": {"ghdl": {"sim": "GHDL 6.0.0", "results": {c: {"verdict": "PASS", "counts": {"errors": 0}}
                                                         for c in configs}}},
          "parts": parts}


def _netlist(ok: bool, key: str, configs: list[str], commit: str) -> dict:
  synth = {key: {"ok": ok, "tool_error": not ok, "problems": [] if ok else ["Vivado exit code 1"],
                 "netlist": "C:\\lmeda\\c\\ps\\x.vhd", "metrics": {"bram_tiles": 0.5}, "critical_warnings": 0,
                 "version": "2023.1", "retries": ["attempt 1: x"], "provenance": {"commit": commit, "sources_hash": "h"}}}
  sims = {"ghdl": {"sim": "GHDL 6.0.0", "results": {} if not ok else {
    c: {"verdict": "PASS", "counts": {"errors": 0}, "collisions": {"model_collisions": 1, "block_rams": ["b"]}}
    for c in configs}}}
  return {"synth": synth, "sims": sims}


def make_campaign(base: Path, rerun_sha: str | None = None, problem: str = "") -> Campaign:
  c = Campaign(base)
  root = c.root
  c.synth("vs", "vivado_a.tar.xz", [_result("vivado-artix7", "a", "pass", problems=[problem] if problem else []),
                                    _result("vivado-artix7", "b", "tool-error",
                                            problems=[f"couldn't read file \"{root.as_posix()}/vs/x/.Xil/"
                                                      "Vivado-123-pcname/realtime.tcl\""])])
  c.synth("vs", "vivado_rerun_b.tar.xz", [_result("vivado-artix7", "b", "pass")],
          where=root / "rerun" / "vs" / "vivado-artix7" / "b")
  if rerun_sha:
    _write(root / "rerun" / "vs" / "vivado-artix7" / "b" / "summary_public.json",
           {"archive": c.archive("vivado_rerun_b.tar.xz", rerun_sha), "results": [_result("vivado-artix7", "b", "pass")]})
  # checks_vivado evaluated again on C2: the commit of the run stays C1.
  checks = [_result("vivado-artix7-checks", "x", "pass", commit=C2,
                    problems=[f"[{REPO}/src/common/lm_mem_pkg.vhd:101]"])]
  c.synth("fc", "checks_v.tar.xz", checks, before=[_result("vivado-artix7-checks", "x", "fail", commit=C1)])
  c.synth("qc", "checks_q.tar.xz", [_result("quartus-cyclonev", "q", "pass", tool="quartus", version="",
                                            part="5CEBA4F23C7")
                                    | {"expected": _record("quartus", "23.1std.0 Build 991")}])
  (root / "qc" / "quartus_cyclonev" / "q").mkdir(parents=True)
  (root / "qc" / "quartus_cyclonev" / "q" / "run.log").write_text(
    "    Info: Version 23.1std.0 Build 991 11/28/2023 SC Lite Edition\n", encoding="utf-8")
  configs = ["rw_rw.16x48"]
  _write(root / "ps" / "results.json",
         _ps(c.archive("ps.tar.xz"), C2, {"xcku3p-ffva676-2-e": _netlist(False, "rw_rw_16x48_16_l11", configs, C2)},
             configs))
  _write(root / "rerun" / "ps" / "xcku3p-ffva676-2-e" / "results.json",
         _ps(c.archive("ps_rerun.tar.xz"), C2, {"xcku3p-ffva676-2-e": _netlist(True, "rw_rw_16x48_16_l11", configs, C2)},
             configs))
  _write(root / "ps.before_20261105T000000Z" / "results.json", _ps(c.archive("ps_old.tar.xz"), C1, {}, configs))
  _write(root / "vu" / "ghdl_6.0.0" / "results.json",
         {"commit": C1, "passed": 2, "total": 2, "tests": [], "archive": c.archive("vu.tar.xz")})
  _write(root / "mut" / "ghdl_6.0.0" / "results.json",
         {"commit": C1, "simulator": "ghdl", "mutations": [{"verdict": "DETECTED"}], "baseline": {"verdict": "PASS"},
          "archive": c.archive("mut.tar.xz")})
  _write(root / "rerun" / "rerun.json", {"ok": True, "items": [
    {"step": "vivado", "job": "vivado-artix7", "part": "xc7a35tfgg484-2", "case": "b", "rerun_status": "pass"},
    {"step": "postsynth", "job": "postsynth", "part": "xcku3p-ffva676-2-e", "case": "rw_rw.16x48",
     "rerun_status": "pass"}]})
  _write(root / "summary.json", {"commit": C1, "ok": True, "resumes": [{"commit": C2, "steps": ["checks_vivado"]}],
                                 "pilot": {"ok": True, "cases": []},
                                 "steps": {"postsynth": {"ok": True, "runs": 1, "reruns": 1}},
                                 "driver_archive": c.archive("driver.tar.xz")})
  toolsets.write(root)
  return c


class ExpectedRecordTest(unittest.TestCase):
  """A result compared with expected values not yet measured, or with a file
  that changed since, is not published."""

  def _collect_with(self, record):
    with tempfile.TemporaryDirectory() as tmp:
      c = make_campaign(Path(tmp))
      path = c.root / "vs" / "summary_public.json"
      data = json.loads(path.read_text(encoding="utf-8"))
      data["results"][0]["expected"] = record
      path.write_text(json.dumps(data), encoding="utf-8")
      with self.assertRaises(publish.PublishError) as raised:
        publish.collect(c.root, c.archives)
      return str(raised.exception)

  def test_provisional_expected_values_are_refused(self):
    record = check.load_expected("amd", "2024.1")["record"]
    self.assertEqual(record["file"], "tools/vendor_synth/expected/amd/2024.1.toml")
    self.assertIn("not yet measured", record["basis"])
    message = self._collect_with(record)
    self.assertIn("vivado vivado-artix7 a: compared with expected results not yet measured", message)
    self.assertIn("remove [meta] basis and evaluate the logs again", message)

  def test_expected_values_changed_after_the_evaluation_are_refused(self):
    record = {**check.load_expected("amd", "2023.1")["record"], "sha256": "0" * 64}
    self.assertIn("expected/amd/2023.1.toml changed after the evaluation", self._collect_with(record))

  def test_a_result_without_record_is_refused(self):
    self.assertIn("no record of the expected results", self._collect_with(None))

  def test_measured_expected_values_are_published(self):
    with tempfile.TemporaryDirectory() as tmp:
      c = make_campaign(Path(tmp))
      first = publish.collect(c.root, c.archives)["vivado_2023.1_amd.json"]["results"][0]
      self.assertEqual(first["expected"], {**check.load_expected("amd", "2023.1")["record"], "basis": None})


class PublishTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.base = Path(self.tmp.name)

  def tearDown(self):
    self.tmp.cleanup()

  def test_rerun_replaces_tool_error_and_keeps_first_run(self):
    c = make_campaign(self.base)
    files = publish.collect(c.root, c.archives)
    vs = files["vivado_2023.1_amd.json"]
    self.assertEqual(vs["counts"], {"pass": 2})
    b = vs["results"][1]
    self.assertEqual((b["status"], b["archive"]["archive"]), ("pass", "vivado_rerun_b.tar.xz"))
    self.assertEqual(b["first_run"]["status"], "tool-error")
    self.assertEqual(b["first_run"]["archive"]["archive"], "vivado_a.tar.xz")
    # The campaign root and the host name are gone from the problems.
    self.assertIn('"<campaign>/vs/x/.Xil/Vivado-<pid>-<host>/realtime.tcl"', b["first_run"]["problems"][0])

  def test_every_result_has_commit_tool_version_device_archive(self):
    c = make_campaign(self.base)
    files = publish.collect(c.root, c.archives)
    for name in ("vivado_2023.1_amd.json", "static_checks_vivado_2023.1.json", "static_checks_quartus_23.1.json"):
      for r in files[name]["results"]:
        self.assertTrue(all(r.get(k) for k in ("commit", "tool", "tool_version", "part")), (name, r))
        self.assertEqual(len(r["archive"]["sha256"]), 64)
    for r in files["postsynth_vivado_2023.1_amd.json"]["results"]:
      self.assertTrue(r["commit"] and r["archive"]["sha256"], r)
    self.assertEqual(files["static_checks_quartus_23.1.json"]["results"][0]["tool_version"], "23.1std.0 Build 991")

  def test_evaluated_again_keeps_commit_of_the_run(self):
    c = make_campaign(self.base)
    x = publish.collect(c.root, c.archives)["static_checks_vivado_2023.1.json"]["results"][0]
    self.assertEqual((x["commit"], x["evaluated_on"]), (C1, C2))
    self.assertEqual(x["problems"], ["[src/common/lm_mem_pkg.vhd:101]"])

  def test_postsynth_rerun_and_superseded(self):
    c = make_campaign(self.base)
    files = publish.collect(c.root, c.archives)
    ps = files["postsynth_vivado_2023.1_amd.json"]
    netlist = [r for r in ps["results"] if r["model"] == "netlist"]
    self.assertEqual(len(netlist), 1)
    self.assertEqual(netlist[0]["archive"]["archive"], "ps_rerun.tar.xz")
    self.assertEqual(netlist[0]["first_run"]["status"], "tool-error")
    self.assertEqual(netlist[0]["synthesis"]["retries"], 1)
    self.assertNotIn("block_rams", netlist[0]["collisions"])
    self.assertEqual(ps["verdicts"], {"PASS": 2})
    campaign = files["amd_campaign.json"]
    self.assertEqual([a["archive"] for a in campaign["superseded_archives"]], ["ps_old.tar.xz"])
    self.assertIn("vivado_rerun_b.tar.xz", [a["archive"] for a in campaign["archives"]])

  def test_postsynth_runs_are_explained(self):
    # Second review of PR #4: "runs" of the post-synthesis step (74) did not
    # say what it counts, next to the 78 published results.
    c = make_campaign(self.base)
    step = publish.collect(c.root, c.archives)["amd_campaign.json"]["steps"]["postsynth"]
    self.assertEqual((step["main_step_runs"], step["reruns"], step["rerun_netlist_runs"], step["consolidated_results"]),
                     (1, 1, 1, 2))
    self.assertNotIn("runs", step)
    self.assertIn("consolidated_results", step["meaning"])

  def test_wrong_sha_stops(self):
    c = make_campaign(self.base, rerun_sha="0" * 64)
    with self.assertRaisesRegex(publish.PublishError, "vivado_rerun_b.tar.xz: SHA-256"):
      publish.collect(c.root, c.archives)

  def test_missing_archive_stops(self):
    c = make_campaign(self.base)
    (c.archives / "mut.tar.xz").unlink()
    with self.assertRaisesRegex(publish.PublishError, "mut.tar.xz not found"):
      publish.collect(c.root, c.archives)

  def test_local_path_left_stops(self):
    c = make_campaign(self.base, problem="read D:/other/place/file.tcl")
    with self.assertRaisesRegex(publish.PublishError, "local paths left"):
      publish.collect(c.root, c.archives)

  def test_failed_campaign_is_not_published(self):
    c = make_campaign(self.base)
    summary = json.loads((c.root / "summary.json").read_text(encoding="utf-8"))
    _write(c.root / "summary.json", {**summary, "ok": False})
    with self.assertRaisesRegex(publish.PublishError, "did not pass"):
      publish.collect(c.root, c.archives)

  def test_readme_table_between_markers(self):
    c = make_campaign(self.base)
    files = publish.collect(c.root, c.archives)
    out = self.base / "out"
    out.mkdir()
    (out / "README.md").write_text(f"# Results\n\n{publish.BEGIN}\nold\n{publish.END}\n\n## Notes\n", encoding="utf-8")
    publish.write(files, out)
    text = (out / "README.md").read_text(encoding="utf-8")
    self.assertTrue(text.startswith("# Results\n"))
    self.assertTrue(text.endswith("## Notes\n"))
    self.assertNotIn("old", text)
    row = next(line for line in text.splitlines() if line.startswith("| [`static_checks_vivado"))
    self.assertIn("`1111111`; evaluated on `2222222`", row)
    vivado = next(line for line in text.splitlines() if line.startswith("| [`vivado_2023.1"))
    self.assertLess(vivado.index("vivado_a.tar.xz"), vivado.index("vivado_rerun_b.tar.xz"))
    self.assertTrue((out / "amd_campaign.json").is_file())



# The toolset of a Linux machine, with the installations as detect() records them.
LINUX_TOOLS = {"tools": {
  "vivado": {"version": "2024.1", "command": ["/opt/eda/amd/Vivado/2024.1/bin/vivado", "-version"]},
  "quartus": {"version": "25.1std.0 Build 1129", "release": "25.1std", "edition": "Lite",
              "command": ["/opt/eda/altera/25.1std/quartus/bin/quartus_sh", "--version"]},
  "questa": {"version": "2025.2", "edition": "starter", "command": ["/opt/eda/altera/25.1std/questa_fse/bin/vsim"]},
  "ghdl": {"version": "6.0.0", "command": ["/opt/eda/ghdl/6.0.0-llvm/bin/ghdl", "--version"]},
}}


class LinuxPublishTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.base = Path(self.tmp.name)

  def tearDown(self):
    self.tmp.cleanup()

  def test_linux_paths_are_removed(self):
    home = Path("/home/someone")
    scrub = publish.scrubber(home / "lmeda" / "c", LINUX_TOOLS, home / "lm_eda_archive", home=home)
    cases = {
      "couldn't read file \"/opt/eda/amd/Vivado/2024.1/scripts/rt/x.tcl\"": "\"<vivado>/scripts/rt/x.tcl\"",
      "/opt/eda/altera/25.1std/quartus/bin/quartus_sh -t run.tcl": "<quartus>/bin/quartus_sh -t run.tcl",
      "/opt/eda/altera/25.1std/questa_fse/bin/vsim -c": "<questa>/bin/vsim -c",
      "/opt/eda/ghdl/6.0.0-llvm/lib/ghdl/vendors": "<ghdl>/lib/ghdl/vendors",
      "/home/someone/lmeda/c/vs/fake/r_w/run.log": "<campaign>/vs/fake/r_w/run.log",
      "/home/someone/lm_eda_archive/vivado.tar.xz": "<archive>/vivado.tar.xz",
      "/home/someone/.venvs/lm/bin/python -m tools.vendor_synth": "~/.venvs/lm/bin/python -m tools.vendor_synth",
      # Without the toolset: the usual Linux places of each tool.
      "/tools/Xilinx/Vivado/2023.2/data/vhdl": "<vivado>/data/vhdl",
      "/srv/intelFPGA_lite/24.1std/quartus/bin/quartus_sh": "<quartus>/bin/quartus_sh",
    }
    for raw, clean in cases.items():
      with self.subTest(raw=raw):
        self.assertIn(clean, scrub(raw))
        self.assertEqual(publish.local_paths(scrub(raw)), [])
    # The home directory alone, not a longer name that starts with it.
    self.assertEqual(scrub("/home/someone2/x"), "/home/someone2/x")

  def test_paths_under_home_and_opt_stop_the_publication(self):
    self.assertTrue(publish.local_paths("/home/other/file.vhd"))
    self.assertTrue(publish.local_paths("/opt/other/tool/bin/x"))

  def test_a_campaign_with_linux_paths_is_published(self):
    c = make_campaign(self.base, problem="couldn't read file \"/opt/eda/amd/Vivado/2024.1/scripts/x.tcl\"")
    toolsets.write(c.root, {**toolsets.PCL01, "tools": {**toolsets.PCL01["tools"], **LINUX_TOOLS["tools"]}})
    files = publish.collect(c.root, c.archives)
    self.assertIn("<vivado>/scripts/x.tcl", files["vivado_2024.1_amd.json"]["results"][0]["problems"][0])
    campaign = files["amd_campaign.json"]
    self.assertEqual(campaign["toolset"]["vivado"], {"version": "2024.1"})
    self.assertEqual(campaign["campaigns"]["vunit_questa"], "questa_starter_2025.2")
    self.assertNotIn("command", json.dumps(campaign["toolset"]))

  def test_each_campaign_has_its_own_directory(self):
    c = make_campaign(self.base)
    files = publish.collect(c.root, c.archives)
    out = self.base / "results" / "v0.2.0-pcl01"
    self.assertIsNone(publish.other_campaign(files, out))
    publish.write(files, out)
    self.assertTrue((out / "README.md").read_text(encoding="utf-8").startswith("# Results: campaign v0.2.0-pcl01\n"))
    # The same campaign again (after a resume): allowed.
    self.assertIsNone(publish.other_campaign(files, out))
    # Another campaign: refused.
    other = {**files, "amd_campaign.json": {**files["amd_campaign.json"], "commit": C2}}
    self.assertIn("another campaign", publish.other_campaign(other, out))
    # The laptop campaign in docs/results (no toolset recorded) is never overwritten.
    self.assertIn("another campaign", publish.other_campaign(files, ROOT_DIR / "docs" / "results"))

  def test_main_needs_the_output_directory(self):
    c = make_campaign(self.base)
    with self.assertRaises(SystemExit):
      publish.main([str(c.root)])
    out = self.base / "out"
    out.mkdir()
    (out / "vivado_2023.1_amd.json").write_text("{}", encoding="utf-8")
    err = io.StringIO()
    with mock.patch.dict(os.environ, {"LM_EDA_ARCHIVE_DIR": str(c.archives)}), redirect_stderr(err):
      self.assertEqual(publish.main([str(c.root), "--out", str(out)]), 1)
    self.assertIn("results without amd_campaign.json", err.getvalue())


if __name__ == "__main__":
  unittest.main()
