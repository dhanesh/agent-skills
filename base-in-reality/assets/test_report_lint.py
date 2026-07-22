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
from report_lint import lint_document, lint_findings, load_schema  # noqa: E402


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

    def test_cli_unreadable_input(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "report_lint.py"), "/no/such.json"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
