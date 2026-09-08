#!/usr/bin/env python3
"""Unit tests for spec_to_tasks.py — stdlib-only, offline, deterministic.

Run standalone:  cd spec-first-planning/assets && python3 test_spec_to_tasks.py
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_to_tasks  # noqa: E402

GOOD = textwrap.dedent(
    """\
    # Spec: CSV export for saved reports

    ## Problem
    Analysts re-type report numbers into spreadsheets by hand.

    ## Users
    - Data analysts exporting weekly reports

    ## Goals
    - Saved reports downloadable as CSV

    ## Non-goals
    - Excel (.xlsx) export

    ## Requirements
    - R1: The report page must offer a "Download CSV" action for every saved report. [where: web/reports/]
    - R2: The exported CSV must contain the same rows and columns as the on-screen table, in the same order.
    - R3: Export of a 10000-row report must complete within 5 seconds.

    ## Acceptance criteria
    - R1: Open any saved report; the page shows a Download CSV control and clicking it downloads a .csv file.
    - R2: `python3 tests/compare_export.py fixtures/report.json export.csv` exits 0 (row/column parity).
    - R2: A report with zero rows exports a CSV containing only the header row.
    - R3: Timing the export endpoint with a 10000-row fixture reports under 5 seconds.

    ## Open questions
    - (none)
    """
)

# Doctored spec: R2 has no acceptance criterion, so no task is derivable.
UNCOVERED = GOOD.replace(
    "- R2: `python3 tests/compare_export.py fixtures/report.json export.csv` "
    "exits 0 (row/column parity).\n"
    "- R2: A report with zero rows exports a CSV containing only the header row.\n",
    "",
)


class TestDerivePlan(unittest.TestCase):
    def test_full_coverage(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        self.assertEqual(plan["uncovered"], [])
        self.assertEqual(
            plan["coverage"], {"R1": ["T1"], "R2": ["T2"], "R3": ["T3"]}
        )
        self.assertEqual(len(plan["tasks"]), 3)

    def test_every_task_has_verify(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        for task in plan["tasks"]:
            self.assertTrue(task["verify"].strip(), "task %s lacks verify" % task["id"])

    def test_multiple_criteria_join_into_verify(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        t2 = plan["tasks"][1]
        self.assertEqual(t2["requirement_ids"], ["R2"])
        self.assertIn("compare_export.py", t2["verify"])
        self.assertIn("only the header row", t2["verify"])
        self.assertEqual(len(t2["_verify_steps"]), 2)

    def test_where_hint_lifted_out_of_title(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        t1 = plan["tasks"][0]
        self.assertEqual(t1["_where"], "web/reports/")
        self.assertNotIn("[where:", t1["title"])
        self.assertIn("Download CSV", t1["title"])

    def test_verify_step_strips_rid_prefix(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        self.assertFalse(plan["tasks"][0]["verify"].startswith("R1"))

    def test_uncovered_requirement_reported(self):
        plan = spec_to_tasks.derive_plan(UNCOVERED)
        self.assertEqual(plan["uncovered"], ["R2"])
        self.assertEqual(plan["coverage"]["R2"], [])
        # Remaining requirements still get deterministic task ids.
        self.assertEqual(plan["coverage"]["R1"], ["T1"])
        self.assertEqual(plan["coverage"]["R3"], ["T2"])

    def test_deterministic_output(self):
        a = json.dumps(spec_to_tasks.to_json(spec_to_tasks.derive_plan(GOOD)))
        b = json.dumps(spec_to_tasks.to_json(spec_to_tasks.derive_plan(GOOD)))
        self.assertEqual(a, b)

    def test_json_schema_keys(self):
        out = spec_to_tasks.to_json(spec_to_tasks.derive_plan(GOOD))
        self.assertEqual(sorted(out.keys()), ["coverage", "tasks", "uncovered"])
        for task in out["tasks"]:
            for key in ("id", "requirement_ids", "title", "verify"):
                self.assertIn(key, task)
            self.assertFalse(any(k.startswith("_") for k in task))


class TestMarkdown(unittest.TestCase):
    def test_markdown_names_verify_and_coverage(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        md = spec_to_tasks.render_markdown(plan, "spec.md")
        self.assertIn("## T1 —", md)
        self.assertIn("- Verify:", md)
        self.assertIn("| R3 | T3 |", md)
        self.assertIn("- Where: web/reports/", md)

    def test_markdown_marks_uncovered(self):
        plan = spec_to_tasks.derive_plan(UNCOVERED)
        md = spec_to_tasks.render_markdown(plan, "spec.md")
        self.assertIn("NONE — UNCOVERED", md)
        self.assertIn("## Uncovered requirements", md)


class TestCli(unittest.TestCase):
    def _run(self, content, *flags):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spec.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            script = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "spec_to_tasks.py"
            )
            return subprocess.run(
                [sys.executable, script, path, *flags],
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_cli_pass_markdown(self):
        r = self._run(GOOD)
        self.assertEqual(r.returncode, 0)
        self.assertIn("COVERAGE: R1 -> T1", r.stdout)
        self.assertIn("TASKS_RESULT: PASS (3/3 requirements", r.stdout)

    def test_cli_json_is_parseable(self):
        r = self._run(GOOD, "--json")
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["uncovered"], [])

    def test_cli_uncovered_exits_nonzero(self):
        r = self._run(UNCOVERED)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNCOVERED: R2", r.stdout)
        self.assertIn("TASKS_RESULT: FAIL", r.stdout)

    def test_cli_no_requirements_exits_2(self):
        r = self._run("# Spec: empty\n\n## Problem\nSomething.\n")
        self.assertEqual(r.returncode, 2)



class TestCoverageOwnership(unittest.TestCase):
    """An `R<n>` token anywhere in a criterion used to count as coverage."""

    SPEC = """# Spec: importer

## Requirements
- R1: The importer must reject a row with a missing id.
- R2: The importer must emit a summary count.

## Acceptance criteria
- R1: run `grep R2 fixtures.txt` and see the row rejected (this also proves R2).
"""

    def test_incidental_mention_is_not_coverage(self):
        plan = spec_to_tasks.derive_plan(self.SPEC)
        self.assertEqual(plan["coverage"]["R2"], [])
        self.assertIn("R2", plan["uncovered"])

    def test_verify_step_is_not_duplicated(self):
        plan = spec_to_tasks.derive_plan(self.SPEC)
        t1 = [t for t in plan["tasks"] if t["id"] == "T1"][0]
        self.assertEqual(t1["verify"].count("grep R2 fixtures.txt"), 1)

    def test_foreign_prefix_is_stripped_from_the_step(self):
        plan = spec_to_tasks.derive_plan(self.SPEC)
        t1 = [t for t in plan["tasks"] if t["id"] == "T1"][0]
        self.assertFalse(t1["verify"].startswith("R1:"))

    def test_prefixless_criterion_still_covers_its_mentions(self):
        spec = """# Spec: legacy

## Requirements
- R1: The thing must happen.

## Acceptance criteria
- Covers R1 by running the check.
"""
        plan = spec_to_tasks.derive_plan(spec)
        self.assertEqual(plan["coverage"]["R1"], ["T1"])
        self.assertEqual(plan["uncovered"], [])

if __name__ == "__main__":
    unittest.main()
