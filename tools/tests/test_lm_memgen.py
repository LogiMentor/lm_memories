# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor Srl
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from tools.lm_memgen.cli import main


class LmMemgenTests(unittest.TestCase):
  def test_ram_wrapper_generation(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      out = Path(tmpdir) / "lm_mem_example.vhd"
      result = main(
        [
          "ram",
          "--ports",
          "crw_crw",
          "--vendor",
          "xilinx",
          "--family",
          "ultrascaleplus",
          "--tool",
          "vivado",
          "--width-a",
          "32",
          "--depth-a",
          "1024",
          "--width-b",
          "8",
          "--read-latency-a",
          "1",
          "--read-latency-b",
          "1",
          "--style",
          "bram",
          "--mode",
          "generic",
          "--out",
          str(out),
        ]
      )

      self.assertEqual(result, 0)
      text = out.read_text(encoding="utf-8")
      self.assertIn("entity lm_mem_example is", text)
      self.assertIn("entity work.lm_mem_ram_crw_crw", text)

  def test_fifo_wrapper_generation(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      out = Path(tmpdir) / "lm_fifo_example.vhd"
      result = main(
        [
          "fifo",
          "--vendor",
          "generic",
          "--width",
          "32",
          "--depth",
          "1024",
          "--clocking",
          "sync",
          "--out",
          str(out),
        ]
      )

      self.assertEqual(result, 0)
      text = out.read_text(encoding="utf-8")
      self.assertIn("entity lm_fifo_example is", text)
      self.assertIn("entity work.lm_mem_fifo_sync", text)

  def test_async_fifo_wrapper_generation(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      out = Path(tmpdir) / "lm_fifo_async_example.vhd"
      result = main(
        [
          "fifo",
          "--vendor",
          "generic",
          "--width",
          "32",
          "--depth",
          "1024",
          "--clocking",
          "async",
          "--out",
          str(out),
        ]
      )

      self.assertEqual(result, 0)
      text = out.read_text(encoding="utf-8")
      self.assertIn("entity lm_fifo_async_example is", text)
      self.assertIn("entity work.lm_mem_fifo_async", text)
      self.assertIn("wr_clk_i", text)
      self.assertIn("rd_clk_i", text)

  def test_fifo_depth_validation(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      out = Path(tmpdir) / "bad.vhd"
      with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
        main(["fifo", "--width", "8", "--depth", "3", "--out", str(out)])


if __name__ == "__main__":
  unittest.main()
