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

SPEC_LINT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec_lint.py")

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

    ## Constraints
    - B1 [invariant]: No exported row may differ from the on-screen table.
    - T1 [boundary]: Export of a 10000-row report finishes within 5 seconds.

    ## Required truths
    - RT1 [SPECIFICATION_READY]: The CSV writer reproduces every row and column exactly. (parent: OUTCOME; maps_to: B1; reqs: R1, R2; confidence: 0.8; check: python3 tests/compare_export.py fixtures/report.json export.csv)
    - RT2 [SPECIFICATION_READY]: The export path stays within the time budget at scale. (parent: RT1; maps_to: T1; reqs: R3; confidence: 0.7; check: python3 tests/bench_export.py --rows 10000 --max-seconds 5)

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

    def test_no_after_hint_gives_empty_lists(self):
        spec = spec_lint.parse_spec(GOOD)
        self.assertEqual(spec["after"], {1: [], 2: [], 3: []})

    def test_after_hint_parses_to_ints(self):
        ok = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [after: R2]",
        )
        spec = spec_lint.parse_spec(ok)
        self.assertEqual(spec["after"][3], [2])

    def test_after_hint_comma_separated_list_parses_in_order(self):
        ok = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [after: R1, R2]",
        )
        spec = spec_lint.parse_spec(ok)
        self.assertEqual(spec["after"][3], [1, 2])

    def test_after_hint_matches_r_case_insensitively(self):
        ok = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [after: r2]",
        )
        spec = spec_lint.parse_spec(ok)
        self.assertEqual(spec["after"][3], [2])


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

    def test_after_hint_valid_is_clean(self):
        ok = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [after: R2]",
        )
        self.assertEqual(spec_lint.lint(ok), [])

    def test_after_hint_unknown_requirement_fails(self):
        bad = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [after: R9]",
        )
        self.assertIssue(spec_lint.lint(bad), "unknown requirement R9")

    def test_after_hint_cycle_fails(self):
        bad = GOOD.replace(
            '- R1: The report page must offer a "Download CSV" action for every saved report.',
            '- R1: The report page must offer a "Download CSV" action for every saved report. [after: R2]',
        ).replace(
            "- R2: The exported CSV must contain the same rows and columns as the on-screen table, in the same order.",
            "- R2: The exported CSV must contain the same rows and columns as the on-screen table, in the same order. [after: R1]",
        )
        self.assertIssue(spec_lint.lint(bad), "after: hints have a cycle")

    def test_after_hint_malformed_token_fails(self):
        bad = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [after: foo]",
        )
        self.assertIssue(spec_lint.lint(bad), "R3 [after: ...] has a malformed id 'foo'")

    def test_after_hint_partial_id_fails(self):
        # "R22x" used to be silently read as R22 (findall grabbed the digits
        # and ignored the trailing garbage) — it must now be rejected outright.
        bad = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [after: R22x]",
        )
        self.assertIssue(spec_lint.lint(bad), "R3 [after: ...] has a malformed id 'R22x'")

    def test_after_hint_empty_fails(self):
        bad = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [after: ]",
        )
        self.assertIssue(spec_lint.lint(bad), "R3 [after: ...] has a malformed id ''")

    def test_after_hint_trailing_comma_fails(self):
        bad = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [after: R2,]",
        )
        issues = spec_lint.lint(bad)
        self.assertIssue(issues, "R3 [after: ...] has a malformed id ''")
        # R2 itself is still a well-formed, known id and must not also be
        # reported as unknown.
        self.assertFalse(any("unknown requirement R2" in i for i in issues))

    def test_after_hint_malformed_token_is_not_silently_dropped(self):
        # Before this fix, "[after: foo]" parsed to an empty id list with no
        # diagnostic at all — parse_spec must now surface it in malformed_after.
        ok_looking = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [after: foo]",
        )
        spec = spec_lint.parse_spec(ok_looking)
        self.assertEqual(spec["after"][3], [])
        self.assertEqual(spec["malformed_after"], [(3, "foo")])

    def test_after_hint_keyword_matches_case_insensitively(self):
        ok = GOOD.replace(
            "- R3: Export of a 10000-row report must complete within 5 seconds.",
            "- R3: Export of a 10000-row report must complete within 5 seconds. [AFTER: R2]",
        )
        spec = spec_lint.parse_spec(ok)
        self.assertEqual(spec["after"][3], [2])
        self.assertEqual(spec_lint.lint(ok), [])


LIGHT = """# Spec: Export

## Problem
Users cannot export rows.

## Users
- analysts

## Goals
- export works

## Non-goals
- PDF

## Constraints
- B1 [invariant]: No row is lost.
- T1 [boundary]: Export finishes within 10 s for 10000 rows.

## Required truths
- RT1 [SPECIFICATION_READY]: Every row reaches the file. (parent: OUTCOME; maps_to: B1; reqs: R1; confidence: 0.8; check: python3 -m pytest -k rows)
- RT2 [NOT_SATISFIED]: The writer streams. (parent: RT1; maps_to: T1; reqs: R1; confidence: 0.6; check: python3 bench.py --max 10)

## Requirements
- R1: The export must include every row.

## Acceptance criteria
- R1: run `python3 -m pytest -k rows`, expect exit 0.

## Open questions
"""

# I3: RT1 and RT2 parent each other (a cycle); RT3 anchors on OUTCOME.
CYCLE = """# Spec: Cycle

## Problem
p

## Users
- u

## Goals
- g

## Non-goals
- n

## Constraints
- B1 [invariant]: c1

## Required truths
- RT1 [SPECIFICATION_READY]: t1 (parent: RT2; maps_to: B1; reqs: R1; confidence: 0.5; check: true)
- RT2 [SPECIFICATION_READY]: t2 (parent: RT1; maps_to: B1; reqs: R1; confidence: 0.5; check: true)
- RT3 [SPECIFICATION_READY]: t3 (parent: OUTCOME; maps_to: B1; reqs: R1; confidence: 0.5; check: true)

## Requirements
- R1: The thing must happen.

## Acceptance criteria
- R1: run true, expect exit 0.

## Open questions
"""

# I3: a two-level chain RT2 -> RT1 -> OUTCOME reaches the root and is clean.
CHAIN = """# Spec: Chain

## Problem
p

## Users
- u

## Goals
- g

## Non-goals
- n

## Constraints
- B1 [invariant]: c1
- T1 [boundary]: c2

## Required truths
- RT1 [SPECIFICATION_READY]: t1 (parent: OUTCOME; maps_to: B1; reqs: R1; confidence: 0.5; check: true)
- RT2 [SPECIFICATION_READY]: t2 (parent: RT1; maps_to: T1; reqs: R1; confidence: 0.5; check: true)

## Requirements
- R1: The thing must happen.

## Acceptance criteria
- R1: run true, expect exit 0.

## Open questions
"""


FULL = LIGHT.replace("RT2 [NOT_SATISFIED]", "RT2 [SPECIFICATION_READY]") + """
## Tensions
- TN1 [trade_off]: Streaming vs. atomic write. (between: B1, T1; status: resolved; strategy: Partition)

## Solution options
- OPT-A: Stream rows to a temp file, rename at end. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2)
- OPT-B: Build in memory, then write. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1)
Recommended: OPT-A — satisfies every RT at the lowest complexity.

## Iterations
- I1: constrained, tensioned, anchored; chose OPT-A.

## Decisions
- D1: May the export add a dependency? -> no (source: sweep)
"""


class LightRules(unittest.TestCase):
    def issues(self, text):
        return spec_lint.lint(text)

    def test_light_spec_is_clean(self):
        self.assertEqual(self.issues(LIGHT), [])

    def test_missing_constraints_section_fails(self):
        t = LIGHT.replace("## Constraints\n- B1 [invariant]: No row is lost.\n- T1 [boundary]: Export finishes within 10 s for 10000 rows.\n\n", "")
        self.assertTrue(any("Constraints" in i for i in self.issues(t)))

    def test_bad_constraint_type_fails(self):
        t = LIGHT.replace("B1 [invariant]", "B1 [wish]")
        self.assertTrue(any("B1" in i and "type" in i for i in self.issues(t)))

    def test_unmapped_constraint_fails(self):
        t = LIGHT.replace("maps_to: T1;", "maps_to: B1;")
        self.assertTrue(any("T1" in i and "no required truth" in i for i in self.issues(t)))

    def test_truth_unknown_constraint_fails(self):
        t = LIGHT.replace("maps_to: B1;", "maps_to: B9;")
        self.assertTrue(any("RT1" in i and "B9" in i for i in self.issues(t)))

    def test_truth_unknown_requirement_fails(self):
        t = LIGHT.replace("reqs: R1; confidence: 0.8", "reqs: R7; confidence: 0.8")
        self.assertTrue(any("RT1" in i and "R7" in i for i in self.issues(t)))

    def test_truth_without_check_fails(self):
        t = LIGHT.replace("; check: python3 -m pytest -k rows)", ")")
        self.assertTrue(any("RT1" in i and "check" in i for i in self.issues(t)))

    def test_bad_parent_fails(self):
        t = LIGHT.replace("parent: RT1;", "parent: RT5;")
        self.assertTrue(any("RT2" in i and "parent" in i for i in self.issues(t)))

    def test_no_outcome_root_fails(self):
        t = LIGHT.replace("parent: OUTCOME;", "parent: RT2;")
        self.assertTrue(any("OUTCOME" in i for i in self.issues(t)))

    def test_confidence_out_of_range_fails(self):
        t = LIGHT.replace("confidence: 0.8", "confidence: 1.4")
        self.assertTrue(any("RT1" in i and "confidence" in i for i in self.issues(t)))

    def test_bad_status_fails(self):
        t = LIGHT.replace("RT1 [SPECIFICATION_READY]", "RT1 [DONE]")
        self.assertTrue(any("RT1" in i and "status" in i for i in self.issues(t)))

    # I1: parentheses inside a required-truth statement used to be where the
    # lazy split landed, corrupting the field parse.
    def test_truth_statement_with_parentheses_parses_correctly(self):
        t = LIGHT.replace(
            "RT1 [SPECIFICATION_READY]: Every row reaches the file.",
            "RT1 [SPECIFICATION_READY]: Every row (including duplicates) reaches the file.",
        )
        self.assertEqual(self.issues(t), [])

    def test_truth_without_field_list_is_malformed(self):
        t = LIGHT.replace(
            "## Required truths\n",
            "## Required truths\n- RT9 [SATISFIED]: No field list at all.\n",
        )
        issues = self.issues(t)
        self.assertTrue(any("RT9" in i and "Required truths bullet is not" in i for i in issues))

    # Minor: duplicate RT ids get their own message (mirrors the constraint one).
    def test_duplicate_truth_id_fails(self):
        t = LIGHT.replace("RT2 [NOT_SATISFIED]", "RT1 [NOT_SATISFIED]")
        self.assertTrue(any("required truth RT1 is defined twice" in i for i in self.issues(t)))

    # I3: every RT must trace back to OUTCOME through parent links, not just
    # have a non-dangling immediate parent — a cycle among RTs must be caught.
    def test_cycle_does_not_trace_to_outcome(self):
        issues = self.issues(CYCLE)
        self.assertTrue(any("RT1" in i and "does not trace back to OUTCOME" in i for i in issues))
        self.assertTrue(any("RT2" in i and "does not trace back to OUTCOME" in i for i in issues))
        self.assertFalse(any("RT3" in i and "does not trace back to OUTCOME" in i for i in issues))

    def test_chain_reaches_outcome_is_clean(self):
        self.assertEqual(self.issues(CHAIN), [])


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


class ConvergedRules(unittest.TestCase):
    def lint(self, t, mode):
        return spec_lint.lint(t, mode=mode)

    def test_full_spec_converges_and_is_unattended_ready(self):
        self.assertEqual(self.lint(FULL, "converged"), [])
        self.assertEqual(self.lint(FULL, "unattended"), [])

    def test_light_spec_does_not_converge(self):
        self.assertTrue(self.lint(LIGHT, "converged"))

    def test_not_ready_truth_blocks_convergence(self):
        t = FULL.replace("RT2 [SPECIFICATION_READY]", "RT2 [PARTIAL]")
        self.assertTrue(any("RT2" in i for i in self.lint(t, "converged")))

    def test_unresolved_tension_without_decision_fails(self):
        t = FULL.replace("status: resolved; strategy: Partition", "status: accepted; strategy: Accept")
        self.assertTrue(any("TN1" in i and "decision" in i for i in self.lint(t, "converged")))

    def test_recommending_the_less_pragmatic_option_fails(self):
        t = FULL.replace("OPT-B: Build in memory, then write. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1)",
                         "OPT-B: Build in memory, then write. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1, RT2)")
        t = t.replace("Recommended: OPT-A", "Recommended: OPT-B")
        self.assertTrue(any("OPT-A" in i and "pragmatic" in i for i in self.lint(t, "converged")))

    def test_recommended_must_satisfy_every_truth(self):
        t = FULL.replace("Recommended: OPT-A", "Recommended: OPT-B")
        self.assertTrue(any("OPT-B" in i and "RT2" in i for i in self.lint(t, "converged")))

    def test_tie_needs_a_decision(self):
        t = FULL.replace("(complexity: Medium; reversibility: TWO_WAY; satisfies: RT1)",
                         "(complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2)")
        self.assertTrue(any("tie" in i for i in self.lint(t, "converged")))
        t2 = t.replace("at the lowest complexity.", "at the lowest complexity. (decision: D1)")
        self.assertEqual(self.lint(t2, "converged"), [])

    def test_iteration_cap(self):
        extra = "".join("- I%d: again\n" % n for n in range(2, 7))
        t = FULL.replace("- I1: constrained, tensioned, anchored; chose OPT-A.\n",
                         "- I1: constrained, tensioned, anchored; chose OPT-A.\n" + extra)
        self.assertTrue(any("iteration cap" in i for i in self.lint(t, "converged")))

    def test_open_question_blocks_convergence(self):
        t = FULL.replace("## Open questions\n", "## Open questions\n- Which delimiter?\n")
        self.assertTrue(any("Open questions" in i for i in self.lint(t, "converged")))

    def test_unattended_needs_answered_decisions(self):
        t = FULL.replace("-> no (source: sweep)", "-> (source: sweep)")
        self.assertTrue(any("D1" in i for i in self.lint(t, "unattended")))
        t2 = FULL.split("## Decisions")[0]
        self.assertTrue(any("Decisions" in i for i in self.lint(t2, "unattended")))

    def test_unknown_decision_reference_fails(self):
        t = FULL.replace("strategy: Partition)", "strategy: Partition; decision: D9)")
        self.assertTrue(any("D9" in i for i in self.lint(t, "converged")))

    def test_option_satisfying_an_unknown_truth_fails(self):
        t = FULL.replace("satisfies: RT1)", "satisfies: RT1, RT9)")
        issues = self.lint(t, "converged")
        self.assertIn("OPT-B satisfies unknown truth RT9", issues)
        self.assertEqual(self.lint(FULL, "converged"), [])

    def test_cli_help_prints_usage_and_exits_0(self):
        for flag in ("-h", "--help"):
            r = subprocess.run([sys.executable, SPEC_LINT, flag], capture_output=True,
                               text=True, timeout=30)
            self.assertEqual(r.returncode, 0, flag)
            self.assertIn("usage: spec_lint.py [--converged|--unattended] <spec.md>", r.stdout)

    def test_docstring_names_the_modes(self):
        self.assertIn("[--converged|--unattended]", spec_lint.__doc__)

    def test_cli_modes(self):
        import subprocess, sys, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.md")
            with open(p, "w") as f:
                f.write(LIGHT)
            run = lambda *a: subprocess.run([sys.executable, SPEC_LINT, *a, p], capture_output=True, text=True)
            self.assertEqual(run().returncode, 0)
            self.assertEqual(run("--converged").returncode, 1)
            self.assertEqual(run("--unattended").returncode, 1)


if __name__ == "__main__":
    unittest.main()
