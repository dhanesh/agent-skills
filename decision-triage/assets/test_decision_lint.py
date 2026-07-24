#!/usr/bin/env python3
"""Stdlib unit tests for decision_lint.py. Offline, deterministic, no repo writes."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decision_lint  # noqa: E402

FULL = """# Decision: Move loan-disbursal reconciliation to a nightly batch job

- Date: 2026-07-25
- Decider: Dhanesh
- Path: full
- Reversibility: irreversible
- Cost of reversal: two engineer-weeks plus a partner-facing comms cycle

## Criteria
- C1: Time to detect a disbursal mismatch
- C2: Engineering cost to build and run for a year
- C3: Opportunity cost of the next-best forgone option

## Options
- O1: Nightly batch reconciliation
- O2: Streaming reconciliation on the event bus

## Assessment
- C1: O1=3 O2=5
- C2: O1=5 O2=2
- C3: O1=4 O2=3

## Outside view
Three comparable teams moved to streaming reconciliation; two ran roughly double their
estimate and one abandoned it after a quarter.

## Pre-mortem
- Nightly windows lengthened past the partner cut-off as volume grew.
- Mismatches aged a full day before anyone saw them, so disputes multiplied.

## Steelman of the rejected option
Streaming detects within seconds, which is the only thing that actually reduces dispute
volume, and the platform cost is paid once.

## Decision
Nightly batch now, with a streaming path kept open behind the same interface.

## Confidence
70%

## Tripwires
- Batch window exceeds 90 minutes, or mismatch count exceeds 50 in a week.

## Review date
2026-10-25

## Outcome
- Reviewed:
- Correct:
- Notes:
"""

FAST = """# Decision: Pick a scheduling library for the notification service

- Date: 2026-07-25
- Decider: Dhanesh
- Path: fast
- Reversibility: reversible
- Cost of reversal: half a day to swap behind the interface
- Deadline: 2026-07-28

## Criteria
- C1: Good enough bar — cron syntax, retries, and a maintained release this year

## Options
- O1: The incumbent library already used elsewhere in the estate

## Assessment
- C1: O1=4

## Decision
Keep the incumbent; it clears the bar.

## Confidence
65%

## Tripwires
- Any missed schedule in production within the first month.

## Review date
2026-08-25

## Outcome
- Reviewed:
- Correct:
- Notes:
"""


def write(text):
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def failures(text):
    path = write(text)
    try:
        return [msg for good, msg in decision_lint.lint(path) if not good]
    finally:
        os.unlink(path)


class TestKnownGood(unittest.TestCase):
    def test_full_path_record_is_clean(self):
        self.assertEqual(failures(FULL), [])

    def test_fast_path_record_is_clean(self):
        self.assertEqual(failures(FAST), [])

    def test_fast_path_does_not_demand_premortem(self):
        self.assertNotIn("Pre-mortem", " ".join(failures(FAST)))


class TestContractOrder(unittest.TestCase):
    """The anti-sycophancy check: a verdict may not precede its criteria."""

    def test_decision_before_criteria_is_rejected(self):
        lines = FULL.split("\n")
        start = lines.index("## Decision")
        block = lines[start : start + 3]
        del lines[start : start + 3]
        insert = lines.index("## Criteria")
        moved = "\n".join(lines[:insert] + block + [""] + lines[insert:])
        msgs = " ".join(failures(moved))
        self.assertIn("before", msgs)
        self.assertIn("Criteria", msgs)

    def test_missing_assessment_section_is_rejected(self):
        broken = FULL.replace("## Assessment", "## Notes")
        self.assertTrue(failures(broken))


class TestNegativeFixtures(unittest.TestCase):
    def test_incomplete_score_matrix(self):
        broken = FULL.replace("- C2: O1=5 O2=2", "- C2: O1=5")
        self.assertIn("incomplete", " ".join(failures(broken)))

    def test_score_out_of_range(self):
        broken = FULL.replace("- C1: O1=3 O2=5", "- C1: O1=3 O2=9")
        self.assertIn("1-5", " ".join(failures(broken)))

    def test_prose_confidence_rejected(self):
        broken = FULL.replace("70%", "high")
        self.assertIn("not 'high'", " ".join(failures(broken)))

    def test_false_certainty_rejected(self):
        self.assertTrue(failures(FULL.replace("70%", "100%")))
        self.assertTrue(failures(FULL.replace("70%", "0%")))

    def test_review_date_not_after_decision_date(self):
        broken = FULL.replace("2026-10-25", "2026-07-01")
        self.assertIn("must fall after", " ".join(failures(broken)))

    def test_empty_steelman_rejected(self):
        broken = FULL.replace(
            "Streaming detects within seconds, which is the only thing that actually reduces dispute\nvolume, and the platform cost is paid once.",
            "",
        )
        self.assertIn("Steelman", " ".join(failures(broken)))

    def test_single_premortem_bullet_rejected(self):
        broken = FULL.replace(
            "- Mismatches aged a full day before anyone saw them, so disputes multiplied.\n", ""
        )
        self.assertIn("Pre-mortem", " ".join(failures(broken)))

    def test_criteria_id_gap_rejected(self):
        broken = FULL.replace("- C2: Engineering cost", "- C4: Engineering cost")
        self.assertIn("without gaps", " ".join(failures(broken)))

    def test_full_path_requires_two_options(self):
        broken = FULL.replace("- O2: Streaming reconciliation on the event bus\n", "")
        self.assertTrue(failures(broken))

    def test_missing_decider_rejected(self):
        broken = FULL.replace("- Decider: Dhanesh", "- Decider:")
        self.assertIn("Decider", " ".join(failures(broken)))

    def test_bad_path_value_rejected(self):
        broken = FULL.replace("- Path: full", "- Path: medium")
        self.assertIn("Path must be", " ".join(failures(broken)))

    def test_missing_tripwires_rejected(self):
        broken = FULL.replace(
            "- Batch window exceeds 90 minutes, or mismatch count exceeds 50 in a week.\n", ""
        )
        self.assertIn("Tripwires", " ".join(failures(broken)))

    def test_empty_file_fails_loudly(self):
        self.assertGreater(len(failures("")), 3)


class TestCli(unittest.TestCase):
    def test_exit_codes(self):
        good = write(FULL)
        bad = write(FULL.replace("70%", "high"))
        try:
            self.assertEqual(decision_lint.main(["decision_lint.py", good]), 0)
            self.assertEqual(decision_lint.main(["decision_lint.py", bad]), 1)
            self.assertEqual(decision_lint.main(["decision_lint.py"]), 2)
        finally:
            os.unlink(good)
            os.unlink(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
