#!/usr/bin/env python3
"""Unit tests for spec_lint.py — stdlib-only, offline, deterministic.

Run standalone:  cd spec-first-planning/assets && python3 test_spec_lint.py
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_lint  # noqa: E402

GOOD = textwrap.dedent(
    """\
    # Spec: CSV export for saved reports

    ## Problem
    Analysts cannot get report data out of the dashboard; they re-type
    numbers into spreadsheets by hand, which loses time and introduces errors.

    ## Users
    - Data analysts exporting weekly reports

    ## Goals
    - Saved reports downloadable as CSV from the report page

    ## Non-goals
    - Excel (.xlsx) export
    - Scheduled email delivery

    ## Requirements
    - R1: The report page must offer a "Download CSV" action for every saved report.
    - R2: The exported CSV must contain the same rows and columns as the on-screen table, in the same order.
    - R3: Export of a 10000-row report must complete within 5 seconds.

    ## Acceptance criteria
    - R1: Open any saved report; the page shows a Download CSV control and clicking it downloads a .csv file.
    - R2: `python3 tests/compare_export.py fixtures/report.json export.csv` exits 0 (row/column parity).
    - R3: Timing the export endpoint with a 10000-row fixture reports under 5 seconds.

    ## Open questions
    - (none)
    """
)


class TestParse(unittest.TestCase):
    def test_title_strips_spec_prefix(self):
        spec = spec_lint.parse_spec(GOOD)
        self.assertEqual(spec["title"], "CSV export for saved reports")

    def test_requirements_and_criteria_parsed(self):
        spec = spec_lint.parse_spec(GOOD)
        self.assertEqual([n for n, _ in spec["requirements"]], [1, 2, 3])
        self.assertEqual(len(spec["criteria"]), 3)
        self.assertEqual(spec["criteria"][1][1], [2])


class TestLint(unittest.TestCase):
    def assertIssue(self, issues, needle):
        self.assertTrue(
            any(needle in issue for issue in issues),
            "expected an issue containing %r, got: %r" % (needle, issues),
        )

    def test_good_spec_is_clean(self):
        self.assertEqual(spec_lint.lint(GOOD), [])

    def test_missing_non_goals_rejected(self):
        bad = GOOD.replace("## Non-goals\n- Excel (.xlsx) export\n- Scheduled email delivery\n\n", "")
        self.assertIssue(spec_lint.lint(bad), "missing required section '## Non-goals'")

    def test_empty_section_rejected(self):
        bad = GOOD.replace(
            "## Users\n- Data analysts exporting weekly reports\n",
            "## Users\n",
        )
        self.assertIssue(spec_lint.lint(bad), "section '## Users' is empty")

    def test_numbering_gap_rejected(self):
        bad = GOOD.replace("R3:", "R4:").replace("- R3:", "- R4:")
        self.assertIssue(spec_lint.lint(bad), "without gaps or duplicates")

    def test_duplicate_id_rejected(self):
        bad = GOOD.replace(
            "- R2: The exported CSV must contain",
            "- R1: The exported CSV must contain",
        )
        self.assertIssue(spec_lint.lint(bad), "without gaps or duplicates")

    def test_missing_modal_rejected(self):
        bad = GOOD.replace(
            "- R1: The report page must offer",
            "- R1: The report page offers",
        )
        self.assertIssue(spec_lint.lint(bad), "R1 lacks a modal obligation")

    def test_vague_term_without_metric_flagged(self):
        bad = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: The export must be fast.",
        )
        self.assertIssue(spec_lint.lint(bad), "R3 uses vague term 'fast'")

    def test_vague_term_with_metric_allowed(self):
        ok = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: The export must be fast: under 5 seconds for a 10000-row report.",
        )
        self.assertEqual(spec_lint.lint(ok), [])

    def test_requirement_without_criterion_rejected(self):
        bad = GOOD.replace(
            "- R2: `python3 tests/compare_export.py fixtures/report.json "
            "export.csv` exits 0 (row/column parity).\n",
            "",
        )
        self.assertIssue(spec_lint.lint(bad), "R2 has no acceptance criterion")

    def test_criterion_with_unknown_id_rejected(self):
        bad = GOOD.replace(
            "- R3: Timing the export endpoint",
            "- R9: Timing the export endpoint",
        )
        issues = spec_lint.lint(bad)
        self.assertIssue(issues, "unknown requirement R9")
        self.assertIssue(issues, "R3 has no acceptance criterion")

    def test_open_questions_may_be_empty(self):
        ok = GOOD.replace("## Open questions\n- (none)\n", "## Open questions\n")
        self.assertEqual(spec_lint.lint(ok), [])

    def test_missing_open_questions_section_rejected(self):
        bad = GOOD.replace("## Open questions\n- (none)\n", "")
        self.assertIssue(
            spec_lint.lint(bad), "missing required section '## Open questions'"
        )

    def test_requirements_prose_without_bullets_rejected(self):
        bad = GOOD.replace(
            '- R1: The report page must offer a "Download CSV" action for every saved report.\n'
            "- R2: The exported CSV must contain the same rows and columns as the on-screen table, in the same order.\n"
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "The system should export things.",
        )
        self.assertIssue(spec_lint.lint(bad), "no '- R<n>: ...' bullets")

    def test_deterministic_issue_order(self):
        bad = GOOD.replace("must offer", "offers").replace("must contain", "contains")
        self.assertEqual(spec_lint.lint(bad), spec_lint.lint(bad))


class TestCli(unittest.TestCase):
    def _run(self, content):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spec.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            script = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "spec_lint.py"
            )
            return subprocess.run(
                [sys.executable, script, path],
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_cli_pass(self):
        r = self._run(GOOD)
        self.assertEqual(r.returncode, 0)
        self.assertIn("LINT_RESULT: PASS", r.stdout)

    def test_cli_fail(self):
        r = self._run(GOOD.replace("must offer", "offers"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("LINT_RESULT: FAIL", r.stdout)

    def test_cli_bad_usage(self):
        script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "spec_lint.py"
        )
        r = subprocess.run(
            [sys.executable, script], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
