"""Tests for the pure history-report aggregation and rendering."""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pi_guard import report  # noqa: E402


def rec(ts, temp_c=50.0, throttled=0, flags=None, events=None, arm_mhz=1500):
    return {
        "ts": ts,
        "temp_c": temp_c,
        "arm_mhz": arm_mhz,
        "throttled": throttled,
        "flags": flags if flags is not None else [],
        "events": events if events is not None else [],
    }


class SummarizeTest(unittest.TestCase):
    def test_empty_history(self):
        s = report.summarize([])
        self.assertEqual(s["samples"], 0)
        self.assertIsNone(s["first"])
        self.assertIsNone(s["last"])
        self.assertIsNone(s["temp_min"])
        self.assertIsNone(s["temp_avg"])
        self.assertIsNone(s["temp_max"])
        self.assertEqual(s["throttled_pct"], 0.0)
        self.assertEqual(s["flag_pct"], {})
        self.assertEqual(s["events"], [])
        self.assertEqual(s["total_events"], 0)

    def test_aggregates_stats(self):
        records = [
            rec(100, 40.0),
            rec(200, 60.0, throttled=0x2, flags=["freq-capped"],
                events=["FREQ_CAP"]),
            rec(300, 80.0, throttled=0x3, flags=["freq-capped", "throttled"]),
        ]
        s = report.summarize(records)
        self.assertEqual(s["samples"], 3)
        self.assertEqual(s["first"], 100)
        self.assertEqual(s["last"], 300)
        self.assertEqual(s["temp_min"], 40.0)
        self.assertEqual(s["temp_max"], 80.0)
        self.assertEqual(s["temp_avg"], 60.0)
        # 2 of 3 samples have a currently-active throttle bit (0x2, 0x3)
        self.assertEqual(s["throttled_pct"], 66.67)
        self.assertEqual(s["flag_pct"], {"freq-capped": 66.67, "throttled": 33.33})
        self.assertEqual(s["total_events"], 1)
        self.assertEqual(s["events"][0], {"ts": 200, "event": "FREQ_CAP"})

    def test_non_numeric_temps_skipped(self):
        s = report.summarize([rec(1, temp_c=None), rec(2, temp_c="n/a")])
        self.assertIsNone(s["temp_min"])
        self.assertIsNone(s["temp_avg"])

    def test_record_without_ts_does_not_crash(self):
        # a valid-JSON line missing "ts" must not raise (read_all skips only
        # unparseable lines); first/last degrade to None
        s = report.summarize([{"temp_c": 50.0, "flags": [], "events": []}])
        self.assertEqual(s["samples"], 1)
        self.assertIsNone(s["first"])
        self.assertIsNone(s["last"])
        self.assertEqual(s["temp_avg"], 50.0)

    def test_dirty_typed_lines_degrade_not_crash(self):
        # valid-JSON lines with wrong-typed values (hand edits, jq rewrites,
        # bare scalars) degrade to empty values instead of crashing the report
        dirty = [
            {"ts": 1, "temp_c": 50.0, "throttled": "0x4", "flags": [], "events": []},
            {"ts": 2, "temp_c": 55.0, "flags": None, "events": "THROTTLE"},
            {"ts": "not-a-number", "temp_c": None, "flags": [3, "throttled"]},
            42,
        ]
        s = report.summarize(dirty)
        self.assertEqual(s["samples"], 4)
        self.assertEqual(s["throttled_pct"], 0.0)
        self.assertEqual(s["flag_pct"], {"throttled": 25.0})
        self.assertEqual(s["first"], 1)
        self.assertIsNone(s["last"])
        # last_state + human rendering survive a dirty final line too
        state = report.last_state(dirty)
        self.assertEqual(state["throttle_bits"], [])
        text = report.render_human(s, state)
        self.assertIn("Throttled     : 0.0%", text)

    def test_events_capped_at_twenty(self):
        records = [rec(i, events=[f"E{i}"]) for i in range(30)]
        s = report.summarize(records)
        self.assertEqual(s["total_events"], 30)
        self.assertEqual(len(s["events"]), 20)
        self.assertEqual(s["events"][-1]["event"], "E29")


class LastStateTest(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(report.last_state([]))

    def test_decodes_throttle_bits_of_last_sample(self):
        state = report.last_state([rec(1), rec(2, throttled=0x5)])
        self.assertEqual(state["sample"]["ts"], 2)
        names = [d["name"] for d in state["throttle_bits"]]
        # 0x5 = under-voltage now + throttled now
        self.assertEqual(names, ["under-voltage", "throttled"])
        self.assertTrue(all(d["occurred"] for d in state["throttle_bits"]))


class RenderHumanTest(unittest.TestCase):
    def test_empty_history_message(self):
        out = report.render_human(report.summarize([]), None)
        self.assertIn("No history yet", out)

    def test_full_report_lines(self):
        records = [
            rec(1000, 40.0),
            rec(2000, 60.0, throttled=0x1, flags=["under-voltage"],
                events=["UNDERVOLT"]),
        ]
        s = report.summarize(records)
        state = report.last_state(records)
        out = report.render_human(s, state)
        self.assertIn("Last sample", out)
        self.assertIn("60.0C", out)          # last sample is rec(2000, 60.0)
        self.assertIn("Samples       : 2", out)
        self.assertIn("min 40.0C", out)
        self.assertIn("max 60.0C", out)
        self.assertIn("Throttled     : 50.0% of samples", out)
        self.assertIn("under-voltage", out)
        self.assertIn("Events        : 1 total", out)
        self.assertIn("UNDERVOLT", out)

    def test_flag_lines_sorted_by_frequency(self):
        records = [
            rec(1, throttled=0x3, flags=["under-voltage", "throttled"]),
            rec(2, throttled=0x3, flags=["under-voltage", "throttled"]),
            rec(3, throttled=0x1, flags=["under-voltage"]),
        ]
        out = report.render_human(report.summarize(records), None)
        uv = out.index("under-voltage")
        th = out.index("throttled")
        self.assertLess(uv, th)
        self.assertIn("100.0%", out)
        self.assertIn("66.67%", out)


class RenderJsonTest(unittest.TestCase):
    def test_roundtrip(self):
        records = [rec(1, 40.0), rec(2, 70.0, throttled=0x1)]
        s, state = report.build_report(type("S", (), {"read_all": staticmethod(lambda: records)})())
        doc = json.loads(report.render_json(s, state))
        self.assertEqual(doc["summary"]["samples"], 2)
        self.assertEqual(doc["summary"]["temp_max"], 70.0)
        self.assertEqual(doc["last_state"]["sample"]["ts"], 2)


if __name__ == "__main__":
    unittest.main()
