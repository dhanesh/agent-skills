#!/usr/bin/env python3
"""Unit tests for parity_diff.py — stdlib-only, offline, deterministic.

Run standalone:
    cd ai-migration-operating-model/assets && python3 test_parity_diff.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parity_diff  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "parity_diff.py")


class TestNormalize(unittest.TestCase):
    def test_ignored_keys_dropped_at_any_depth(self):
        v = {"a": 1, "ts": 9, "nested": {"ts": 8, "b": [{"ts": 7, "c": 2}]}}
        out = parity_diff.normalize(v, frozenset(["ts"]))
        self.assertEqual(out, {"a": 1, "nested": {"b": [{"c": 2}]}})

    def test_scalars_pass_through(self):
        self.assertEqual(parity_diff.normalize("x"), "x")
        self.assertEqual(parity_diff.normalize(None), None)


class TestDiffValues(unittest.TestCase):
    def test_equal_dicts_regardless_of_key_order(self):
        old = {"b": 2, "a": 1}
        new = {"a": 1, "b": 2}
        self.assertEqual(parity_diff.diff_values(old, new), [])

    def test_value_mismatch_names_the_path(self):
        diffs = parity_diff.diff_values(
            {"a": {"b": [1, {"c": 3}]}}, {"a": {"b": [1, {"c": 4}]}}
        )
        self.assertEqual(len(diffs), 1)
        self.assertIn("$.a.b[1].c", diffs[0])
        self.assertIn("value mismatch", diffs[0])

    def test_missing_and_unexpected_keys(self):
        diffs = parity_diff.diff_values({"a": 1, "b": 2}, {"a": 1, "z": 9})
        self.assertTrue(any("$.b — missing in new" in d for d in diffs))
        self.assertTrue(any("$.z — unexpected in new" in d for d in diffs))

    def test_list_length_mismatch(self):
        diffs = parity_diff.diff_values([1, 2, 3], [1, 2])
        self.assertTrue(any("length mismatch (old=3, new=2)" in d
                            for d in diffs))

    def test_numeric_tolerance_accepts_small_drift(self):
        self.assertEqual(
            parity_diff.diff_values(1.0000001, 1.0000002, tolerance=1e-6), []
        )

    def test_numeric_tolerance_zero_rejects_drift(self):
        diffs = parity_diff.diff_values(1.0000001, 1.0000002)
        self.assertEqual(len(diffs), 1)

    def test_int_and_float_compare_numerically(self):
        self.assertEqual(parity_diff.diff_values(5, 5.0), [])

    def test_bool_is_not_a_number(self):
        diffs = parity_diff.diff_values(True, 1)
        self.assertEqual(len(diffs), 1)
        self.assertIn("type mismatch (old=bool, new=int)", diffs[0])

    def test_type_mismatch_string_vs_number(self):
        diffs = parity_diff.diff_values("5", 5)
        self.assertTrue(any("type mismatch" in d for d in diffs))


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pd-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, obj):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return path

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True, text=True, timeout=30,
        )

    def test_single_file_pass(self):
        a = self._write("old.json", {"x": 1})
        b = self._write("new.json", {"x": 1})
        r = self._run(a, b)
        self.assertEqual(r.returncode, 0)
        self.assertIn("PARITY_RESULT: PASS (1 scenario(s), 0 diff(s))",
                      r.stdout)

    def test_single_file_fail_reports_diff_line(self):
        a = self._write("old.json", {"x": 1})
        b = self._write("new.json", {"x": 2})
        r = self._run(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("DIFF: $.x — value mismatch", r.stdout)
        self.assertIn("PARITY_RESULT: FAIL", r.stdout)

    def test_ignore_flag_drops_noise_keys(self):
        a = self._write("old.json", {"x": 1, "generated_at": "2024-01-01"})
        b = self._write("new.json", {"x": 1, "generated_at": "2025-06-30"})
        r = self._run(a, b, "--ignore", "generated_at")
        self.assertEqual(r.returncode, 0)

    def test_tolerance_flag(self):
        a = self._write("old.json", {"fee": 10.0})
        b = self._write("new.json", {"fee": 10.0 + 1e-10})
        self.assertEqual(self._run(a, b).returncode, 1)
        self.assertEqual(
            self._run(a, b, "--tolerance", "1e-9").returncode, 0
        )

    def test_invalid_json_is_a_parity_failure(self):
        a = os.path.join(self.tmp, "old.json")
        with open(a, "w", encoding="utf-8") as f:
            f.write("{not json")
        b = self._write("new.json", {"x": 1})
        r = self._run(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid JSON in old", r.stdout)

    def test_dir_mode_matches_scenarios_and_flags_missing(self):
        self._write("old/s1.json", {"x": 1})
        self._write("old/s2.json", {"y": 2})
        self._write("new/s1.json", {"x": 1})
        r = self._run(os.path.join(self.tmp, "old"),
                      os.path.join(self.tmp, "new"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("SCENARIO: s1.json — PASS", r.stdout)
        self.assertIn("DIFF: s2.json — scenario missing in new", r.stdout)
        self.assertIn("SCENARIO: s2.json — FAIL", r.stdout)

    def test_dir_mode_all_green(self):
        self._write("old/s1.json", {"x": 1})
        self._write("new/s1.json", {"x": 1})
        r = self._run(os.path.join(self.tmp, "old"),
                      os.path.join(self.tmp, "new"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("PARITY_RESULT: PASS (1 scenario(s), 0 diff(s))",
                      r.stdout)

    def test_mixed_file_and_dir_is_usage_error(self):
        a = self._write("old.json", {"x": 1})
        os.makedirs(os.path.join(self.tmp, "new"), exist_ok=True)
        r = self._run(a, os.path.join(self.tmp, "new"))
        self.assertEqual(r.returncode, 2)

    def test_missing_path_is_usage_error(self):
        a = self._write("old.json", {"x": 1})
        r = self._run(a, os.path.join(self.tmp, "nope.json"))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
