#!/usr/bin/env python3
"""Unit suite for rigor.py — stdlib only, offline, deterministic."""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rigor  # noqa: E402


GOOD_RICE = """# RICE — Q3 roadmap

## Items

- Bulk fee upload | reach=1200/quarter | impact=2 | confidence=0.8 | effort=3 | src: cohorts [^1]
- Autopay nudges | reach=9000/quarter | impact=0.5 | confidence=0.5 | effort=2 | src: analytics [^1]
- Dashboard v2 | reach=340/quarter | impact=3 | confidence=0.8 | effort=5 | src: CRM [^2]

## Sources
[^1]: https://example.test/a
[^2]: https://example.test/b
"""

GOOD_PLAN = """# Plan — FY27

## Drivers

- institutions = 120 | src: signed pipeline [^1]
- students_per_institution = 850 | src: enrolment data [^1]
- attach_rate = 0.06 | assumption
- avg_ticket = 62000 | src: rate card [^2]

## Top Down

- revenue = institutions * students_per_institution * attach_rate * avg_ticket

## Bottom Up

- revenue = 420000000 | src: sales capacity model [^3]

## Trajectory

- FY27-Q1 | 60000000
- FY27-Q2 | 90000000
- FY27-Q3 | 120000000
- FY27-Q4 | 150000000

## What Would Have To Be True

- [least likely] Attach rate holds at 6% as we move down-market
- Sales can onboard 30 institutions a quarter

## Sources
[^1]: https://example.test/pipeline
[^2]: https://example.test/rates
[^3]: https://example.test/capacity
"""


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = rigor.main(argv)
    return code, out.getvalue(), err.getvalue()


class TempFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rigor-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def file(self, text, name="f.md"):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path


class TestSafeEval(unittest.TestCase):
    def test_arithmetic_over_named_drivers(self):
        self.assertEqual(rigor.safe_eval("a * b + 2", {"a": 3.0, "b": 4.0}), 14.0)

    def test_undefined_driver_is_named(self):
        with self.assertRaises(rigor.RigorError) as ctx:
            rigor.safe_eval("a * missing", {"a": 1.0})
        self.assertIn("missing", str(ctx.exception))

    def test_function_calls_are_refused(self):
        for expr in ("__import__('os').system('x')", "open('f')", "a.__class__"):
            with self.assertRaises(rigor.RigorError):
                rigor.safe_eval(expr, {"a": 1.0})

    def test_string_constants_are_refused(self):
        with self.assertRaises(rigor.RigorError):
            rigor.safe_eval("'abc'", {})

    def test_syntax_error_is_a_clean_failure(self):
        with self.assertRaises(rigor.RigorError):
            rigor.safe_eval("a *", {"a": 1.0})


class TestRiceParsing(unittest.TestCase):
    def rules(self, text):
        items, errors = rigor.parse_rice(text)
        return {r.split()[0]: (ok, d) for r, ok, d in rigor.lint_rice(items, errors)}

    def test_good_sheet_passes_every_rule(self):
        for rule, (ok, detail) in self.rules(GOOD_RICE).items():
            self.assertTrue(ok, "%s failed: %s" % (rule, detail))

    def test_score_matches_the_rice_formula(self):
        items, _ = rigor.parse_rice(GOOD_RICE)
        bulk = [i for i in items if i["name"] == "Bulk fee upload"][0]
        self.assertAlmostEqual(bulk["score"], 1200 * 2 * 0.8 / 3)

    def test_reach_without_a_period_is_refused(self):
        text = GOOD_RICE.replace("reach=1200/quarter", "reach=1200")
        ok, detail = self.rules(text)["R0"]
        self.assertFalse(ok)
        self.assertIn("needs a period", detail)

    def test_reach_accepts_per_as_well_as_slash(self):
        items, errors = rigor.parse_rice(
            GOOD_RICE.replace("reach=1200/quarter", "reach=1200 per quarter"))
        self.assertEqual(errors, [])
        self.assertEqual(len(items), 3)

    def test_mixed_periods_are_caught(self):
        text = GOOD_RICE.replace("reach=9000/quarter", "reach=9000/month")
        ok, detail = self.rules(text)["R2"]
        self.assertFalse(ok)
        self.assertIn("month", detail)

    def test_unsourced_reach_is_caught(self):
        text = GOOD_RICE.replace(" | src: cohorts [^1]", "")
        ok, detail = self.rules(text)["R3"]
        self.assertFalse(ok)
        self.assertIn("Bulk fee upload", detail)

    def test_off_scale_impact_is_caught(self):
        ok, detail = self.rules(GOOD_RICE.replace("impact=2", "impact=4"))["R4"]
        self.assertFalse(ok)
        self.assertIn("allowed", detail)

    def test_every_intercom_impact_value_is_accepted(self):
        for value in ("3", "2", "1", "0.5", "0.25"):
            text = GOOD_RICE.replace("impact=2", "impact=" + value)
            self.assertTrue(self.rules(text)["R4"][0], value)

    def test_confidence_as_a_percentage_is_caught(self):
        ok, detail = self.rules(GOOD_RICE.replace("confidence=0.8", "confidence=80"))["R5"]
        self.assertFalse(ok)
        self.assertIn("0 < c <= 1", detail)

    def test_zero_effort_is_caught(self):
        self.assertFalse(self.rules(GOOD_RICE.replace("effort=3", "effort=0"))["R6"][0])

    def test_missing_field_is_named(self):
        ok, detail = self.rules(GOOD_RICE.replace(" | effort=3", ""))["R0"]
        self.assertFalse(ok)
        self.assertIn("effort", detail)

    def test_single_item_cannot_be_prioritised(self):
        text = "## Items\n\n- Only | reach=10/quarter | impact=1 | confidence=1 | effort=1 | src: x\n"
        self.assertFalse(self.rules(text)["R1"][0])


class TestTieDetection(unittest.TestCase):
    def test_scores_within_the_band_group_together(self):
        items = [{"name": "a", "score": 100.0}, {"name": "b", "score": 90.0},
                 {"name": "c", "score": 40.0}]
        groups = rigor.tie_groups(items)
        self.assertEqual([[i["name"] for i in g] for g in groups], [["a", "b"]])

    def test_clearly_separated_scores_are_not_tied(self):
        items = [{"name": "a", "score": 100.0}, {"name": "b", "score": 40.0}]
        self.assertEqual(rigor.tie_groups(items), [])

    def test_a_chain_of_near_ties_groups_transitively(self):
        items = [{"name": "a", "score": 100.0}, {"name": "b", "score": 85.0},
                 {"name": "c", "score": 75.0}]
        self.assertEqual(len(rigor.tie_groups(items)[0]), 3)


class TestPlanChecks(unittest.TestCase):
    def rules(self, text, tolerance=2.0, max_growth=2.0):
        return {r.split()[0]: (ok, d)
                for r, ok, d in rigor.lint_plan(text, tolerance, max_growth)}

    def test_good_plan_passes_every_rule(self):
        for rule, (ok, detail) in self.rules(GOOD_PLAN).items():
            self.assertTrue(ok, "%s failed: %s" % (rule, detail))

    def test_missing_section_short_circuits_with_a_name(self):
        res = self.rules(GOOD_PLAN.replace("## Bottom Up", "## Notes"))
        self.assertFalse(res["P0"][0])
        self.assertIn("Bottom Up", res["P0"][1])
        self.assertNotIn("P6", res)   # no point checking arithmetic without the section

    def test_driver_with_neither_source_nor_assumption_flag(self):
        ok, detail = self.rules(
            GOOD_PLAN.replace("- institutions = 120 | src: signed pipeline [^1]",
                              "- institutions = 120"))["P2"]
        self.assertFalse(ok)
        self.assertIn("institutions", detail)

    def test_mostly_assumptions_is_a_hypothesis_not_a_plan(self):
        text = GOOD_PLAN
        for name, val in (("institutions", "120"), ("students_per_institution", "850"),
                          ("avg_ticket", "62000")):
            text = rigor.re.sub(r"- %s = %s \| src:[^\n]*" % (name, val),
                                "- %s = %s | assumption" % (name, val), text)
        ok, detail = self.rules(text)["P3"]
        self.assertFalse(ok)
        self.assertIn("hypothesis, not a plan", detail)

    def test_top_down_evaluates_with_drivers_in_scope(self):
        # Regression: the formula must see ## Drivers, not be parsed in isolation.
        self.assertTrue(self.rules(GOOD_PLAN)["P4"][0])

    def test_top_down_referencing_an_undefined_driver_fails_with_its_name(self):
        ok, detail = self.rules(GOOD_PLAN.replace(
            "revenue = institutions * students_per_institution * attach_rate * avg_ticket",
            "revenue = institutions * churn_rate"))["P4"]
        self.assertFalse(ok)
        self.assertIn("churn_rate", detail)

    def test_sizings_far_apart_are_caught(self):
        ok, detail = self.rules(GOOD_PLAN.replace("revenue = 420000000",
                                                  "revenue = 12000000"))["P6"]
        self.assertFalse(ok)
        self.assertIn("fiction", detail)

    def test_tolerance_is_configurable(self):
        text = GOOD_PLAN.replace("revenue = 420000000", "revenue = 150000000")
        self.assertFalse(self.rules(text, tolerance=2.0)["P6"][0])
        self.assertTrue(self.rules(text, tolerance=5.0)["P6"][0])

    def test_hockey_stick_growth_is_caught(self):
        text = GOOD_PLAN.replace("- FY27-Q2 | 90000000", "- FY27-Q2 | 400000000")
        ok, detail = self.rules(text)["P7"]
        self.assertFalse(ok)
        self.assertIn("FY27-Q1→FY27-Q2", detail)

    def test_steady_growth_passes(self):
        self.assertTrue(self.rules(GOOD_PLAN)["P7"][0])

    def test_trajectory_is_optional(self):
        text = GOOD_PLAN.split("## Trajectory")[0] + \
            "## What Would Have To Be True" + \
            GOOD_PLAN.split("## What Would Have To Be True")[1]
        self.assertNotIn("P7", self.rules(text))

    def test_one_condition_is_not_enough(self):
        text = GOOD_PLAN.replace(
            "- Sales can onboard 30 institutions a quarter\n", "")
        self.assertFalse(self.rules(text)["P8"][0])

    def test_exactly_one_least_likely_marker_is_required(self):
        self.assertFalse(self.rules(GOOD_PLAN.replace("[least likely] ", ""))["P9"][0])
        self.assertFalse(self.rules(GOOD_PLAN.replace(
            "- Sales can onboard", "- [least likely] Sales can onboard"))["P9"][0])


class TestCli(TempFile):
    def test_rice_prints_a_ranking_for_a_valid_sheet(self):
        code, out, _ = run(["rice", self.file(GOOD_RICE)])
        self.assertEqual(code, 0)
        self.assertIn("RICE_RESULT: PASS", out)
        self.assertIn("Ranked", out)
        self.assertIn("Autopay nudges", out)

    def test_rice_refuses_to_rank_an_invalid_sheet(self):
        code, out, _ = run(["rice", self.file(GOOD_RICE.replace("impact=2", "impact=9"))])
        self.assertEqual(code, 1)
        self.assertIn("No ranking printed", out)
        self.assertNotIn("Ranked (score", out)

    def test_rice_flags_low_confidence_items(self):
        _, out, _ = run(["rice", self.file(GOOD_RICE)])
        self.assertIn("Confidence at or below 50%", out)
        self.assertIn("not a discount factor", out)

    def test_plan_passes_a_reconciled_plan(self):
        code, out, _ = run(["plan", self.file(GOOD_PLAN)])
        self.assertEqual(code, 0, out)
        self.assertIn("PLAN_RESULT: PASS", out)

    def test_plan_fails_and_explains_why_it_matters(self):
        code, out, _ = run(["plan", self.file(GOOD_PLAN.replace(
            "revenue = 420000000", "revenue = 9000000"))])
        self.assertEqual(code, 1)
        self.assertIn("unfalsifiable", out)

    def test_missing_file_fails_cleanly(self):
        code, _, err = run(["plan", os.path.join(self.dir, "nope.md")])
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)
        self.assertNotIn("Traceback", err)

    def test_no_command_prints_help(self):
        self.assertEqual(run([])[0], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
