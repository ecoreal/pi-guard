"""Tests for parsers, bitmask decode and the three data sources."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pi_guard import collect  # noqa: E402


class ParserTest(unittest.TestCase):
    def test_temp(self):
        self.assertEqual(collect.parse_temp("temp='45.6'C"), 45.6)
        self.assertEqual(collect.parse_temp("temp='85.0'C"), 85.0)
        self.assertIsNone(collect.parse_temp("junk"))

    def test_volts(self):
        self.assertEqual(collect.parse_volts("volt=0.8750V"), 0.875)
        self.assertEqual(collect.parse_volts("volt=1.2000V"), 1.2)
        self.assertIsNone(collect.parse_volts("noequals"))

    def test_throttled(self):
        self.assertEqual(collect.parse_throttled("throttled=0x0"), 0)
        self.assertEqual(collect.parse_throttled("throttled=0x20000"), 0x20000)
        self.assertEqual(collect.parse_throttled("throttled=0x20005"), 0x20005)
        self.assertEqual(collect.parse_throttled("garbage"), 0)

    def test_clock(self):
        self.assertEqual(collect.parse_clock("frequency(48)=1500299000"), 1500)
        self.assertIsNone(collect.parse_clock(""))

    def test_mem(self):
        self.assertEqual(collect.parse_mem("gpu=76M"), 76)
        self.assertIsNone(collect.parse_mem("x"))


class DecodeTest(unittest.TestCase):
    def test_zero(self):
        flags = collect.decode_throttled(0)
        self.assertTrue(all(not f["occurred"] for f in flags))

    def test_under_voltage_now(self):
        flags = collect.decode_throttled(0x1)
        uv = [f for f in flags if f["name"] == "under-voltage"][0]
        self.assertTrue(uv["now"])
        self.assertTrue(uv["occurred"])

    def test_occurred_bit_not_now(self):
        flags = collect.decode_throttled(0x10000)
        uv = [f for f in flags if f["name"] == "under-voltage"][0]
        self.assertFalse(uv["now"])
        occ = [f for f in flags if f["name"] == "under-voltage-occurred"][0]
        self.assertTrue(occ["occurred"])
        self.assertNotIn(occ["bit"], collect.NOW_ACTIVE_BITS)

    def test_combined_mask(self):
        now = collect.active_flags(0x20005)
        self.assertEqual(sorted(now), ["throttled", "under-voltage"])

    def test_softtemp(self):
        now = collect.active_flags(0x8)
        self.assertEqual(now, ["softtemp-limit"])


class SimulatedSourceTest(unittest.TestCase):
    def test_deterministic_with_seed(self):
        a = collect.SimulatedSource(seed=7).sample()
        b = collect.SimulatedSource(seed=7).sample()
        self.assertEqual(a["temp_c"], b["temp_c"])

    def test_healthy_stays_under_thresholds(self):
        src = collect.SimulatedSource(seed=3, scenario="healthy")
        for _ in range(200):
            s = src.sample()
            self.assertLess(s["temp_c"], 80)
            self.assertEqual(s["throttled"], 0)

    def test_throttle_scenario_actually_throttles(self):
        src = collect.SimulatedSource(seed=3, scenario="throttle")
        throttled = 0
        for _ in range(120):
            if src.sample()["throttled"]:
                throttled += 1
        self.assertGreater(throttled, 0)


class SysFsSourceTest(unittest.TestCase):
    def test_reads_fixture_root(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        os.makedirs(os.path.join(td.name, "sys/class/thermal/thermal_zone0"))
        os.makedirs(os.path.join(td.name, "sys/devices/system/cpu/cpu0/cpufreq"))
        with open(os.path.join(td.name, "sys/class/thermal/thermal_zone0/temp"), "w") as fh:
            fh.write("54200\n")
        with open(os.path.join(td.name, "sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"), "w") as fh:
            fh.write("1500000\n")
        s = collect.SysFsSource(root=td.name).sample()
        self.assertEqual(s["temp_c"], 54.2)
        self.assertEqual(s["arm_mhz"], 1500)
        self.assertEqual(s["source"], "sysfs")


class VcGenCmdSourceTest(unittest.TestCase):
    def test_parses_canned_output(self):
        src = collect.VcGenCmdSource.__new__(collect.VcGenCmdSource)
        src.binary = "/bin/true"
        calls = {
            "get_throttled": "throttled=0x20005",
            "measure_temp": "temp='61.2'C",
            "measure_volts core": "volt=0.8750V",
            "measure_clock arm": "frequency(48)=1500299000",
            "get_mem gpu": "gpu=76M",
        }

        def fake_run(self, *args):
            return calls.get(" ".join(args), "")

        import types
        orig = collect.VcGenCmdSource._run
        collect.VcGenCmdSource._run = fake_run
        try:
            s = src.sample()
        finally:
            collect.VcGenCmdSource._run = orig
        self.assertEqual(s["temp_c"], 61.2)
        self.assertEqual(s["throttled"], 0x20005)
        self.assertEqual(sorted(s["flags"]), ["throttled", "under-voltage"])
        self.assertEqual(s["arm_mhz"], 1500)
        self.assertEqual(s["gpu_mem_mb"], 76)
        self.assertEqual(s["source"], "vcgencmd")


class SelftestTest(unittest.TestCase):
    def test_selftest_passes(self):
        self.assertTrue(collect.selftest())


if __name__ == "__main__":
    unittest.main()
