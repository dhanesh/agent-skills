#!/usr/bin/env python3
"""Stdlib unit tests for calibration.py. Offline, deterministic, no repo writes."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibration  # noqa: E402

TEMPLATE = """# Decision: {title}

- Date: 2026-01-10
- Decider: Dhanesh
- Path: fast
- Reversibility: reversible
- Cost of reversal: small

## Criteria
- C1: Good enough bar

## Options
- O1: The chosen option

## Assessment
- C1: O1=4

## Decision
Go.

## Confidence
{confidence}%

## Tripwires
- Something observable goes wrong.

## Review date
2026-04-10

## Outcome
- Reviewed: {reviewed}
- Correct: {correct}
- Notes: {notes}
"""


def make_dir(specs):
    d = tempfile.mkdtemp()
    for i, spec in enumerate(specs):
        body = TEMPLATE.format(
            title="Call number %d" % i,
            confidence=spec.get("confidence", 70),
            reviewed=spec.get("reviewed", "2026-04-10"),
            correct=spec.get("correct", "yes"),
            notes=spec.get("notes", "n/a"),
        )
        with open(os.path.join(d, "record-%02d.md" % i), "w", encoding="utf-8") as fh:
            fh.write(body)
    return d


class TestCollection(unittest.TestCase):
    def test_unreviewed_records_are_skipped(self):
        d = make_dir([{"reviewed": "", "correct": ""}, {"confidence": 80}])
        try:
            self.assertEqual(len(calibration.collect(d)), 1)
        finally:
            shutil.rmtree(d)

    def test_non_markdown_files_ignored(self):
        d = make_dir([{"confidence": 80}])
        try:
            with open(os.path.join(d, "notes.txt"), "w", encoding="utf-8") as fh:
                fh.write("not a record")
            self.assertEqual(len(calibration.collect(d)), 1)
        finally:
            shutil.rmtree(d)

    def test_malformed_correct_value_rejected(self):
        d = make_dir([{"correct": "probably"}])
        try:
            self.assertEqual(calibration.collect(d), [])
        finally:
            shutil.rmtree(d)

    def test_malformed_reviewed_date_rejected(self):
        d = make_dir([{"reviewed": "last April"}])
        try:
            self.assertEqual(calibration.collect(d), [])
        finally:
            shutil.rmtree(d)


class TestScoring(unittest.TestCase):
    def test_perfect_confidence_and_outcome(self):
        recs = [{"confidence": 99, "correct": True}]
        self.assertAlmostEqual(calibration.brier(recs), (0.99 - 1.0) ** 2, places=6)

    def test_always_fifty_is_quarter(self):
        recs = [
            {"confidence": 50, "correct": True},
            {"confidence": 50, "correct": False},
        ]
        self.assertAlmostEqual(calibration.brier(recs), 0.25, places=6)

    def test_confident_and_wrong_is_worse_than_coinflip(self):
        recs = [{"confidence": 90, "correct": False}]
        self.assertGreater(calibration.brier(recs), 0.25)

    def test_empty_corpus_scores_none(self):
        self.assertIsNone(calibration.brier([]))


class TestVerdict(unittest.TestCase):
    def test_small_sample_is_not_read(self):
        recs = [{"confidence": 90, "correct": False} for _ in range(3)]
        rows = calibration.buckets(recs)
        self.assertIn("too few", calibration.verdict(calibration.brier(recs), rows))

    def test_systematic_overconfidence_detected(self):
        recs = [{"confidence": 90, "correct": i < 3} for i in range(10)]
        rows = calibration.buckets(recs)
        self.assertIn("overconfident", calibration.verdict(calibration.brier(recs), rows))

    def test_systematic_underconfidence_detected(self):
        recs = [{"confidence": 55, "correct": i < 9} for i in range(10)]
        rows = calibration.buckets(recs)
        self.assertIn("underconfident", calibration.verdict(calibration.brier(recs), rows))

    def test_well_calibrated_reads_clean(self):
        recs = [{"confidence": 70, "correct": i < 7} for i in range(10)]
        rows = calibration.buckets(recs)
        self.assertIn("calibrated", calibration.verdict(calibration.brier(recs), rows))


class TestCli(unittest.TestCase):
    def test_empty_directory_exits_zero(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(calibration.main(["calibration.py", d]), 0)
        finally:
            shutil.rmtree(d)

    def test_populated_directory_exits_zero(self):
        d = make_dir([{"confidence": 70}, {"confidence": 80, "correct": "no"}])
        try:
            self.assertEqual(calibration.main(["calibration.py", d]), 0)
            self.assertEqual(calibration.main(["calibration.py", d, "--json"]), 0)
        finally:
            shutil.rmtree(d)

    def test_bad_usage_exits_two(self):
        self.assertEqual(calibration.main(["calibration.py"]), 2)
        self.assertEqual(calibration.main(["calibration.py", "/nonexistent/path/xyz"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
