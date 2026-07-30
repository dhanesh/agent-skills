#!/usr/bin/env python3
"""Unit suite for casekit.py — stdlib only, offline, deterministic."""
import base64
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casekit  # noqa: E402


CLEAN_CASE = """# Acme — case as of 2011-06-30

## The Situation

Online payments required a merchant account that took weeks to approve.[^1]

## The Protagonist

The founders, deciding how to reach developers.

## What Is Known

Incumbents charged 2.9% per transaction.[^1]

## What Is Uncertain

Whether developers would trust a startup with money movement.

## Comparators

- A rival launched the same year and shut down after failing to get a bank partner.

## The Decision

Should they build their own risk stack or resell an incumbent's?

## Sources

[^1]: https://example.test/2019/retrospective — published 2019, cited not narrated.
"""


def run(argv):
    """Invoke the CLI, capturing output. Returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = casekit.main(argv)
    return code, out.getvalue(), err.getvalue()


class TempCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="casekit-test-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def new(self, slug="acme", date="2011-06-30"):
        code, _, _ = run(["--root", self.root, "new", slug,
                          "--company", "Acme", "--decision-date", date])
        self.assertEqual(code, 0)
        return os.path.join(self.root, slug)

    def state(self, slug="acme"):
        with open(os.path.join(self.root, slug, "state.json"), encoding="utf-8") as fh:
            return json.load(fh)


class TestLint(unittest.TestCase):
    def lint(self, text, date="2011-06-30"):
        return {rule.split()[0]: (ok, detail)
                for rule, ok, detail in casekit.lint_case(text, date)}

    def test_clean_case_passes_every_rule(self):
        for rule, (ok, detail) in self.lint(CLEAN_CASE).items():
            self.assertTrue(ok, "%s failed: %s" % (rule, detail))

    def test_scaffold_residue_is_caught(self):
        self.assertFalse(self.lint(CLEAN_CASE.replace(
            "The founders", "TODO the founders"))["L0"][0])

    def test_future_year_is_a_leak(self):
        ok, detail = self.lint(CLEAN_CASE.replace(
            "Online payments", "By 2014 the market had consolidated. Online payments"))["L1"]
        self.assertFalse(ok)
        self.assertIn("2014", detail)

    def test_future_iso_date_is_a_leak(self):
        self.assertFalse(self.lint(CLEAN_CASE.replace(
            "## The Protagonist", "The round closed 2011-09-01.\n\n## The Protagonist"))["L1"][0])

    def test_same_year_is_allowed(self):
        self.assertTrue(self.lint(CLEAN_CASE.replace(
            "The founders", "In 2011 the founders"))["L1"][0])

    def test_sources_section_may_cite_later_work(self):
        # The clean fixture cites a 2019 retrospective; citing is not narrating.
        self.assertTrue(self.lint(CLEAN_CASE)["L1"][0])

    def test_hindsight_language_is_caught(self):
        ok, detail = self.lint(CLEAN_CASE.replace(
            "The founders,", "The bet turned out to be right. The founders,"))["L2"]
        self.assertFalse(ok)
        self.assertIn("turned out to be right", detail)

    def test_hindsight_match_is_case_insensitive(self):
        self.assertFalse(self.lint(CLEAN_CASE.replace(
            "## What Is Uncertain", "In Hindsight the moat was distribution.\n\n"
                                    "## What Is Uncertain"))["L2"][0])

    def test_missing_decision_section_fails(self):
        self.assertFalse(self.lint(CLEAN_CASE.replace("## The Decision", "## Wrap Up"))["L3"][0])

    def test_decision_section_without_a_question_fails(self):
        ok, detail = self.lint(CLEAN_CASE.replace(
            "Should they build their own risk stack or resell an incumbent's?",
            "They chose to build their own risk stack."))["L3"]
        self.assertFalse(ok)
        self.assertIn("no question", detail)

    def test_unsourced_figure_fails(self):
        ok, detail = self.lint(CLEAN_CASE.replace(
            "Incumbents charged 2.9% per transaction.[^1]",
            "Incumbents charged 2.9% per transaction."))["L4"]
        self.assertFalse(ok)
        self.assertIn("2.9%", detail)

    def test_years_do_not_count_as_unsourced_figures(self):
        self.assertTrue(self.lint(CLEAN_CASE.replace(
            "The founders", "In 2011 the founders"))["L4"][0])

    def test_currency_and_multiplier_figures_are_detected(self):
        for figure in ("$40 million in funding", "grew 10x that quarter",
                       "₹12,00,000 in float", "handled 5000 merchants"):
            text = CLEAN_CASE.replace("## What Is Uncertain",
                                      "They %s.\n\n## What Is Uncertain" % figure)
            self.assertFalse(self.lint(text)["L4"][0], figure)

    def test_identifying_numerals_are_not_figures(self):
        # Regression: "Section 230"/"ISO 27001" are references, not measurements.
        # Flagging them is the noise that pushes an author to --skip-lint.
        for phrase in ("Section 230 does not apply.", "Certified to ISO 27001.",
                       "RFC 2119 language is used.", "Rule 144A governed the placement.",
                       "See Exhibit 10.1 of the filing."):
            text = CLEAN_CASE.replace("## What Is Uncertain",
                                      "%s\n\n## What Is Uncertain" % phrase)
            self.assertTrue(self.lint(text)["L4"][0], phrase)

    def test_comma_grouped_figures_report_the_whole_number(self):
        text = CLEAN_CASE.replace("## What Is Uncertain",
                                  "They had about 2,200 employees.\n\n## What Is Uncertain")
        ok, detail = self.lint(text)["L4"]
        self.assertFalse(ok)
        self.assertIn("2,200", detail)   # not the "200" fragment

    def test_currency_figure_reports_its_scale_word(self):
        text = CLEAN_CASE.replace("## What Is Uncertain",
                                  "They raised $40 million.\n\n## What Is Uncertain")
        self.assertIn("$40 million", self.lint(text)["L4"][1])

    def test_small_counts_do_not_need_a_citation(self):
        self.assertTrue(self.lint(CLEAN_CASE.replace(
            "## What Is Uncertain", "They had 4 employees.\n\n## What Is Uncertain"))["L4"][0])

    def test_missing_comparators_fails(self):
        self.assertFalse(self.lint(CLEAN_CASE.replace("## Comparators", "## Notes"))["L5"][0])

    def test_empty_comparators_section_fails(self):
        text = CLEAN_CASE.replace(
            "- A rival launched the same year and shut down after failing to get a bank partner.",
            "None that matter.")
        ok, detail = self.lint(text)["L5"]
        self.assertFalse(ok)
        self.assertIn("no comparator", detail)


class TestDecisionFile(unittest.TestCase):
    def test_complete_decision_has_no_gaps(self):
        text = "\n".join("## " + s for s in casekit.DECISION_SECTIONS)
        self.assertEqual(casekit.check_decision_file(text), [])

    def test_missing_sections_are_named(self):
        missing = casekit.check_decision_file("## Decision\nShip it.\n")
        self.assertIn("disconfirming evidence", missing)
        self.assertIn("what would change my mind", missing)


class TestWorkflow(TempCase):
    def seal_clean(self, slug="acme"):
        d = self.new(slug)
        casekit.write(os.path.join(d, "case.md"), CLEAN_CASE)
        return d, run(["--root", self.root, "seal", slug])

    def test_new_scaffolds_three_files_and_state(self):
        d = self.new()
        for name in ("case.md", "reveal.md", "decision.md", "state.json"):
            self.assertTrue(os.path.exists(os.path.join(d, name)), name)
        self.assertEqual(self.state()["stage"], casekit.DRAFTING)

    def test_new_refuses_a_duplicate_slug(self):
        self.new()
        code, _, err = run(["--root", self.root, "new", "acme",
                            "--company", "Acme", "--decision-date", "2011-06-30"])
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_new_rejects_a_malformed_decision_date(self):
        code, _, err = run(["--root", self.root, "new", "x",
                            "--company", "X", "--decision-date", "June 2011"])
        self.assertEqual(code, 2)
        self.assertIn("YYYY-MM-DD", err)

    def test_lint_fails_on_the_fresh_scaffold(self):
        self.new()
        code, out, _ = run(["--root", self.root, "lint", "acme"])
        self.assertEqual(code, 1)
        self.assertIn("LINT_RESULT: FAIL", out)

    def test_seal_refuses_a_case_that_fails_lint(self):
        self.new()
        code, _, err = run(["--root", self.root, "seal", "acme"])
        self.assertEqual(code, 2)
        self.assertIn("fails lint", err)
        self.assertEqual(self.state()["stage"], casekit.DRAFTING)

    def test_skip_lint_overrides_the_refusal(self):
        self.new()
        code, _, _ = run(["--root", self.root, "seal", "acme", "--skip-lint"])
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["stage"], casekit.SEALED)

    def test_seal_removes_the_plaintext_reveal(self):
        d, (code, _, _) = self.seal_clean()
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(d, "reveal.md")))
        self.assertTrue(os.path.exists(os.path.join(d, "reveal.sealed")))

    def test_sealed_blob_is_not_plaintext_but_round_trips(self):
        d, _ = self.seal_clean()
        blob = casekit.read(os.path.join(d, "reveal.sealed"))
        self.assertNotIn("What Happened", blob)
        self.assertIn("What Happened",
                      base64.b64decode(blob.strip()).decode("utf-8"))

    def test_reveal_is_refused_before_a_decision_is_committed(self):
        self.seal_clean()
        code, out, err = run(["--root", self.root, "reveal", "acme"])
        self.assertEqual(code, 2)
        self.assertIn("commit a decision before revealing", err)
        self.assertNotIn("What Happened", out)

    def test_commit_is_refused_while_still_drafting(self):
        self.new()
        code, _, err = run(["--root", self.root, "commit", "acme"])
        self.assertEqual(code, 2)
        self.assertIn("seal the reveal", err)

    def test_commit_is_refused_when_the_decision_is_incomplete(self):
        d, _ = self.seal_clean()
        casekit.write(os.path.join(d, "decision.md"), "## Decision\nBuild it.\n")
        code, _, err = run(["--root", self.root, "commit", "acme"])
        self.assertEqual(code, 2)
        self.assertIn("## disconfirming evidence", err)
        self.assertEqual(self.state()["stage"], casekit.SEALED)

    def test_full_happy_path_reveals_the_b_case(self):
        d, _ = self.seal_clean()
        casekit.write(os.path.join(d, "decision.md"),
                      "".join("## %s\nx\n\n" % s for s in casekit.DECISION_SECTIONS))
        self.assertEqual(run(["--root", self.root, "commit", "acme"])[0], 0)
        self.assertEqual(self.state()["stage"], casekit.COMMITTED)
        code, out, _ = run(["--root", self.root, "reveal", "acme"])
        self.assertEqual(code, 0)
        self.assertIn("What Happened", out)
        self.assertEqual(self.state()["stage"], casekit.REVEALED)

    def test_a_decision_cannot_be_swapped_after_commit(self):
        d, _ = self.seal_clean()
        casekit.write(os.path.join(d, "decision.md"),
                      "".join("## %s\nx\n\n" % s for s in casekit.DECISION_SECTIONS))
        run(["--root", self.root, "commit", "acme"])
        code, _, err = run(["--root", self.root, "commit", "acme"])
        self.assertEqual(code, 2)
        self.assertIn("already has a committed decision", err)

    def test_reveal_detects_a_tampered_seal(self):
        d, _ = self.seal_clean()
        casekit.write(os.path.join(d, "decision.md"),
                      "".join("## %s\nx\n\n" % s for s in casekit.DECISION_SECTIONS))
        run(["--root", self.root, "commit", "acme"])
        casekit.write(os.path.join(d, "reveal.sealed"),
                      base64.b64encode(b"a different ending").decode("ascii") + "\n")
        code, _, err = run(["--root", self.root, "reveal", "acme"])
        self.assertEqual(code, 2)
        self.assertIn("checksum", err)

    def test_status_reports_stage_and_next_step(self):
        self.new()
        code, out, _ = run(["--root", self.root, "status", "acme"])
        self.assertEqual(code, 0)
        self.assertIn("DRAFTING", out)
        self.assertIn("next:", out)

    def test_unknown_slug_fails_cleanly(self):
        code, _, err = run(["--root", self.root, "status", "ghost"])
        self.assertEqual(code, 2)
        self.assertIn("no case", err)
        self.assertNotIn("Traceback", err)

    def test_corrupt_state_fails_cleanly(self):
        self.new()
        casekit.write(os.path.join(self.root, "acme", "state.json"), "{not json")
        code, _, err = run(["--root", self.root, "status", "acme"])
        self.assertEqual(code, 2)
        self.assertIn("unreadable", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
