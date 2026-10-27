"""Tests for the monitor loop, JSONL rotation and history reports."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pi_guard import collect, monitor, report  # noqa: E402


def tmpdir(test) -> str:
    td = tempfile.TemporaryDirectory()
    test.addCleanup(td.cleanup)
    return td.name


class MonitorTest(unittest.TestCase):
    def run_ticks(self, scenario: str, ticks: int, seed: int = 3):
        db = os.path.join(tmpdir(self), "sub", "samples.jsonl")
        src = collect.SimulatedSource(seed=seed, scenario=scenario)
        mon = monitor.Monitor(src, db=db, warn_temp=70, crit_temp=80)
        samples = mon.run(interval=0, max_ticks=ticks)
        return mon, db, samples

    def test_samples_written_to_jsonl(self):
        mon, db, samples = self.run_ticks("healthy", 5)
        self.assertEqual(len(samples), 5)
        store = monitor.RotatingJsonl(db)
        records = store.read_all()
        self.assertEqual(len(records), 5)
        self.assertIn("temp_c", records[0])

    def test_throttle_scenario_emits_events(self):
        mon, db, _ = self.run_ticks("throttle", 40)
        records = monitor.RotatingJsonl(db).read_all()
        events = [e for r in records for e in r.get("events", [])]
        self.assertIn("THROTTLE", events)
        self.assertTrue(any(e.startswith("RECOVERED") for e in events))

    def test_temp_zone_events(self):
        mon, db, _ = self.run_ticks("throttle", 40)
        records = monitor.RotatingJsonl(db).read_all()
        events = [e for r in records for e in r.get("events", [])]
        self.assertIn("TEMP_WARN", events)
        self.assertIn("TEMP_CRIT", events)

    def test_healthy_scenario_emits_no_temp_events(self):
        mon, db, _ = self.run_ticks("healthy", 20)
        records = monitor.RotatingJsonl(db).read_all()
        events = [e for r in records for e in r.get("events", [])]
        self.assertNotIn("TEMP_WARN", events)
        self.assertNotIn("THROTTLE", events)

    def test_rotation(self):
        db = os.path.join(tmpdir(self), "samples.jsonl")
        # threshold sized so exactly one rotation happens over 12 ticks and
        # every record stays readable across the two generations
        store = monitor.RotatingJsonl(db, max_bytes=1500)
        src = collect.SimulatedSource(seed=1, scenario="healthy")
        mon = monitor.Monitor(src, db=db)
        mon.store = store
        for _ in range(12):
            mon.tick()
        self.assertTrue(os.path.exists(db + ".1"), "rotation should have produced .1")
        self.assertEqual(len(store.read_all()), 12)

    def test_hook_receives_event_env(self):
        db = os.path.join(tmpdir(self), "samples.jsonl")
        out_file = os.path.join(tmpdir(self), "hook.out")
        hook = f"sh -c 'echo $PIGUARD_EVENT >> {out_file}'"
        src = collect.SimulatedSource(seed=3, scenario="throttle")
        mon = monitor.Monitor(src, db=db, hook=hook)
        mon.run(interval=0, max_ticks=40)
        self.assertTrue(os.path.exists(out_file), "hook should have been invoked")
        with open(out_file) as fh:
            content = fh.read()
        self.assertIn("THROTTLE", content)


class ReportTest(unittest.TestCase):
    def make_records(self):
        base = time.time() - 60
        recs = []
        for i in range(10):
            recs.append({
                "ts": base + i,
                "temp_c": 50 + i,
                "arm_mhz": 1500,
                "throttled": 0x4 if i >= 7 else 0,
                "flags": ["throttled"] if i >= 7 else [],
                "events": ["THROTTLE"] if i == 7 else [],
                "source": "simulated",
            })
        return recs

    def test_summary_stats(self):
        s = report.summarize(self.make_records())
        self.assertEqual(s["samples"], 10)
        self.assertEqual(s["temp_min"], 50)
        self.assertEqual(s["temp_max"], 59)
        self.assertEqual(s["temp_avg"], 54.5)
        self.assertEqual(s["throttled_pct"], 30.0)  # i in {7,8,9}
        self.assertEqual(s["flag_pct"], {"throttled": 30.0})
        self.assertEqual(s["total_events"], 1)

    def test_last_state_decodes_bits(self):
        state = report.last_state(self.make_records())
        self.assertEqual(state["sample"]["temp_c"], 59)
        names = [d["name"] for d in state["throttle_bits"]]
        self.assertIn("throttled", names)

    def test_empty_history(self):
        s = report.summarize([])
        self.assertEqual(s["samples"], 0)
        self.assertIsNone(s["temp_max"])
        self.assertIsNone(report.last_state([]))

    def test_human_report_renders(self):
        text = report.render_human(report.summarize(self.make_records()),
                                   report.last_state(self.make_records()))
        self.assertIn("Throttled     : 30.0%", text)
        self.assertIn("THROTTLE", text)

    def test_json_report_renders(self):
        text = report.render_json(report.summarize(self.make_records()),
                                  report.last_state(self.make_records()))
        import json
        doc = json.loads(text)
        self.assertEqual(doc["summary"]["samples"], 10)


if __name__ == "__main__":
    unittest.main()
