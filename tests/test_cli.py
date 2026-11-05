"""End-to-end CLI tests with simulated data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = SRC
        return subprocess.run([sys.executable, "-m", "pi_guard.cli", *args],
                              capture_output=True, text=True, env=env, timeout=120)

    def test_once_simulated_human(self):
        r = self.run_cli("once", "--simulate", "--seed", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("temp      :", r.stdout)
        self.assertIn("source    : simulated", r.stdout)

    def test_once_simulated_json(self):
        r = self.run_cli("once", "--simulate", "--json")
        doc = json.loads(r.stdout)
        self.assertIsInstance(doc["temp_c"], float)
        self.assertEqual(doc["source"], "simulated")

    def test_decode_variants(self):
        r = self.run_cli("decode", "0x20000")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("under-voltage-occurred", r.stdout)
        self.assertIn("occurred since boot", r.stdout)
        r = self.run_cli("decode", "0x1")
        self.assertIn("ACTIVE NOW", r.stdout)
        r = self.run_cli("decode", "zzz")
        self.assertEqual(r.returncode, 2)

    def test_selftest(self):
        r = self.run_cli("selftest")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)

    def test_run_then_report_roundtrip(self):
        db_dir = tempfile.TemporaryDirectory()
        self.addCleanup(db_dir.cleanup)
        db = os.path.join(db_dir.name, "samples.jsonl")
        r = self.run_cli("run", "--simulate", "--scenario", "throttle",
                         "--interval", "0", "--max-ticks", "30", "--db", db)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(db))

        r = self.run_cli("report", "--db", db)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Throttled     :", r.stdout)
        self.assertIn("TEMP_CRIT", r.stdout)

        r = self.run_cli("report", "--db", db, "--json")
        doc = json.loads(r.stdout)
        self.assertGreater(doc["summary"]["samples"], 0)
        self.assertGreater(doc["summary"]["throttled_pct"], 0)

    def test_report_empty_is_friendly(self):
        db_dir = tempfile.TemporaryDirectory()
        self.addCleanup(db_dir.cleanup)
        db = os.path.join(db_dir.name, "empty.jsonl")
        r = self.run_cli("report", "--db", db)
        self.assertEqual(r.returncode, 0)
        self.assertIn("No history yet", r.stdout)

    def test_report_tolerates_dirty_lines(self):
        # one wrong-typed-but-valid-JSON line must not take the report down
        db_dir = tempfile.TemporaryDirectory()
        self.addCleanup(db_dir.cleanup)
        db = os.path.join(db_dir.name, "dirty.jsonl")
        with open(db, "w") as fh:
            fh.write('{"ts": 1, "temp_c": 50.0, "throttled": 0, "flags": []}\n')
            fh.write('{"ts": 2, "temp_c": 75.5, "throttled": "0x4",'
                     ' "flags": null, "events": "THROTTLE"}\n')
        r = self.run_cli("report", "--db", db)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Samples       : 2", r.stdout)
        r = self.run_cli("report", "--db", db, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["summary"]["samples"], 2)


if __name__ == "__main__":
    unittest.main()
