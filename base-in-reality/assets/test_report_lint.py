# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Unit tests for report_lint.py (stdlib only; run: python3 test_report_lint.py)."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from report_lint import (  # noqa: E402
    lint_document, lint_findings, load_schema, refutation_downgrades)

# Every surviving VIOLATION/DEVIATION must now record its refutation votes
# (verdict-rubric.md step 4). The "good" fixture carries a clean 3-of-3
# non-refute, so the positive tests prove a fully refuted finding passes; they
# are stricter than before, not looser.
CLEAN_REFUTATION = {"refuters": 3, "verdicts": [False, False, False],
                    "rationale": ""}


def good_finding(**over):
    f = {
        "claim": "uses MD5 for password hashing",
        "layer": "algo",
        "location": "auth/hash.py:12",
        "verdict": "VIOLATION",
        "severity": "critical",
        "recommended_fix": "use a memory-hard KDF per NIST SP 800-63B",
        "citations": [{
            "title": "NIST SP 800-63B",
            "url": "https://pages.nist.gov/800-63-3/sp800-63b.html",
            "quote": "verifiers SHALL store memorized secrets salted and hashed",
            "fetched": True,
        }],
        "refutation": copy.deepcopy(CLEAN_REFUTATION),
    }
    f.update(over)
    return f


class LintFindings(unittest.TestCase):
    def test_good_violation_accepted(self):
        self.assertEqual(lint_findings([good_finding()]), [])

    def test_unconfirmed_without_citations_accepted(self):
        f = good_finding(verdict="UNCONFIRMED", severity="low", citations=[])
        self.assertEqual(lint_findings([f]), [])

    def test_violation_without_fetched_citation_rejected(self):
        f = good_finding()
        f["citations"][0]["fetched"] = False
        errs = lint_findings([f])
        self.assertTrue(any("fabricated-citation" in e for e in errs), errs)

    def test_violation_with_no_citations_rejected(self):
        errs = lint_findings([good_finding(citations=[])])
        self.assertTrue(any("no fetched citation" in e for e in errs), errs)

    def test_missing_required_field_rejected(self):
        f = good_finding()
        del f["recommended_fix"]
        errs = lint_findings([f])
        self.assertTrue(any("recommended_fix" in e for e in errs), errs)

    def test_bad_enums_rejected(self):
        errs = lint_findings([good_finding(verdict="CONFIRMED")])
        self.assertTrue(any("verdict" in e for e in errs), errs)
        errs = lint_findings([good_finding(severity="blocker")])
        self.assertTrue(any("severity" in e for e in errs), errs)
        errs = lint_findings([good_finding(layer="infra")])
        self.assertTrue(any("layer" in e for e in errs), errs)

    def test_citation_missing_required_fields_rejected(self):
        f = good_finding()
        del f["citations"][0]["url"]
        errs = lint_findings([f])
        self.assertTrue(any("citations[0]" in e and "'url'" in e for e in errs), errs)

    def test_outdated_needs_citation(self):
        f = good_finding(verdict="OUTDATED", severity="medium", citations=[])
        errs = lint_findings([f])
        self.assertTrue(any("OUTDATED" in e for e in errs), errs)

    def test_downgraded_must_be_unconfirmed(self):
        f = good_finding(downgraded=True)
        errs = lint_findings([f])
        self.assertTrue(any("downgraded" in e for e in errs), errs)
        ok = good_finding(verdict="UNCONFIRMED", citations=[], downgraded=True)
        self.assertEqual(lint_findings([ok]), [])

    def test_non_list_input_rejected(self):
        self.assertTrue(lint_findings({"claim": "x"}))

    def test_document_wrapper_accepted(self):
        self.assertEqual(lint_document({"findings": [good_finding()]}), [])
        self.assertTrue(lint_document({"results": []}))

    def test_enums_come_from_shipped_schema(self):
        schema = load_schema()
        mutated = copy.deepcopy(schema)
        mutated["properties"]["verdict"]["enum"].append("CUSTOM")
        f = good_finding(verdict="CUSTOM")
        self.assertTrue(lint_findings([f]))  # default schema rejects
        self.assertEqual(lint_findings([f], schema=mutated), [])  # schema drives


def votes(*v):
    return {"refuters": len(v), "verdicts": list(v), "rationale": "r"}


class RefutationAggregation(unittest.TestCase):
    """verdict-rubric.md step 3, mirrored from assets/workflow.mjs."""

    def test_critical_one_of_three_refute_is_downgraded(self):
        self.assertTrue(refutation_downgrades("critical", [True, False, False]))
        self.assertTrue(refutation_downgrades("high", [False, True, False]))
        # control: the threshold is severity-aware, not a blanket ban
        self.assertFalse(refutation_downgrades("medium", [True, False, False]))
        self.assertFalse(refutation_downgrades("critical", [False, False, False]))
        self.assertTrue(refutation_downgrades("low", [True, True, False]))

    def test_crashed_refuters_count_as_refutes(self):
        # A refuter that returned nothing is maximally uncertain (step 2).
        self.assertTrue(refutation_downgrades("critical", [None, False, False]))
        self.assertTrue(refutation_downgrades("critical", [None, None, None]))
        self.assertTrue(refutation_downgrades("medium", [None, None, False]))
        self.assertTrue(refutation_downgrades("medium", [True, None, False]))
        # fewer than 3 recorded votes: the missing refuters crashed
        self.assertTrue(refutation_downgrades("critical", [False, False]))
        self.assertTrue(refutation_downgrades("medium", [False]))
        errs = lint_findings([good_finding(refutation=votes(False, None, False))])
        self.assertTrue(any("refute" in e for e in errs), errs)

    def test_linter_fails_critical_violation_with_one_refute(self):
        errs = lint_findings([good_finding(refutation=votes(True, False, False))])
        self.assertTrue(any("unanimous" in e for e in errs), errs)
        errs = lint_findings([good_finding(severity="high", verdict="DEVIATION",
                                           refutation=votes(False, False, True))])
        self.assertTrue(any("unanimous" in e for e in errs), errs)
        # control: a medium survivor with one dissent is within the rubric
        self.assertEqual(lint_findings([good_finding(
            severity="medium", refutation=votes(True, False, False))]), [])
        errs = lint_findings([good_finding(severity="medium",
                                           refutation=votes(True, True, False))])
        self.assertTrue(any(">= 2 refutes" in e for e in errs), errs)
        # a downgraded finding may carry any votes: it already is UNCONFIRMED
        self.assertEqual(lint_findings([good_finding(
            verdict="UNCONFIRMED", downgraded=True,
            refutation=votes(True, False, False))]), [])

    def test_linter_fails_survivor_missing_refutation(self):
        for verdict in ("VIOLATION", "DEVIATION"):
            f = good_finding(verdict=verdict)
            del f["refutation"]
            errs = lint_findings([f])
            self.assertTrue(any("no recorded refutation" in e for e in errs),
                            (verdict, errs))
        errs = lint_findings([good_finding(refutation={"refuters": 3})])
        self.assertTrue(any("no recorded refutation" in e for e in errs), errs)
        # the field is required only on survivors of the refute phase
        for verdict, cites in (("UNCONFIRMED", []), ("OUTDATED", None)):
            f = good_finding(verdict=verdict, severity="medium")
            if cites is not None:
                f["citations"] = cites
            del f["refutation"]
            self.assertEqual(lint_findings([f]), [], verdict)


class Cli(unittest.TestCase):
    def _run(self, payload):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(payload, tf)
            path = tf.name
        try:
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "report_lint.py"), path],
                capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)

    def test_cli_pass_and_fail(self):
        ok = self._run([good_finding()])
        self.assertEqual(ok.returncode, 0, ok.stdout)
        self.assertIn("LINT_RESULT: PASS", ok.stdout)

        bad = self._run([good_finding(citations=[])])
        self.assertEqual(bad.returncode, 1)
        self.assertIn("LINT_RESULT: FAIL", bad.stdout)

        kept = self._run([good_finding(refutation=votes(True, False, False))])
        self.assertEqual(kept.returncode, 1, kept.stdout)
        self.assertIn("LINT_RESULT: FAIL", kept.stdout)

    def test_cli_unreadable_input(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "report_lint.py"), "/no/such.json"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
