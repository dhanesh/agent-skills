#!/usr/bin/env python3
"""Unit tests for `forge.py lint` on a feature's `## Contract` (stdlib only, offline)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FORGE = os.path.join(HERE, "forge.py")
FIXTURE = os.path.join(os.path.dirname(HERE), "eval", "fixtures", "notes-app")
VERIFY = os.path.join(".claude", "skills", "verify-notes")
FEATURE = os.path.join(VERIFY, "features", "notes-create.md")
REL = "features/notes-create.md"

GOOD = """- C1 [pre]: the title is a non-empty string
- C2 [post]: the response is 201 with the stored note's id
- C3 [inv]: notes.jsonl holds one row per stored note"""
PROOF_END = "(the side effect, not just the response)."


def cli(*args):
    return subprocess.run([sys.executable, FORGE, *args], capture_output=True, text=True)


class ContractLintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "app")
        shutil.copytree(FIXTURE, self.root)
        self.vd = os.path.join(self.root, VERIFY)
        self.feature = os.path.join(self.root, FEATURE)
        with open(self.feature, encoding="utf-8") as f:
            self.plain = f.read()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lint(self, clauses, proof_names="C1, C2 and C3"):
        """Lint the fixture with `clauses` as its contract and `proof_names` in Proof."""
        text = self.plain.replace("## Proof\n", "## Contract\n\n%s\n\n## Proof\n" % clauses, 1)
        text = text.replace(PROOF_END, "%s It proves %s." % (PROOF_END, proof_names), 1)
        with open(self.feature, "w", encoding="utf-8") as f:
            f.write(text)
        return cli("lint", self.vd)

    def assertFailsOn(self, r, cid, words):
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("LINT_RESULT: FAIL", r.stdout)
        lines = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL: ")]
        hits = [ln for ln in lines if REL in ln and " %s " % cid in ln and words in ln]
        self.assertTrue(hits, "no FAIL line names %s (%s): %s" % (cid, words, lines))

    def test_every_clause_named_in_proof_passes(self):
        r = self.lint(GOOD)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("LINT_RESULT: PASS", r.stdout)

    def test_trans_kind_passes(self):
        r = self.lint(GOOD + "\n- C4 [trans]: the row count grows by one",
                      "C1, C2, C3 and C4")
        self.assertIn("LINT_RESULT: PASS", r.stdout)

    def test_duplicate_id_fails(self):
        r = self.lint(GOOD + "\n- C3 [post]: the id is a UUID")
        self.assertFailsOn(r, "C3", "listed twice")

    def test_gap_in_ids_fails(self):
        r = self.lint("- C1 [pre]: the title is a non-empty string\n"
                      "- C3 [post]: the response is 201", "C1 and C3")
        self.assertFailsOn(r, "C3", "follows C1")

    def test_empty_clause_fails(self):
        r = self.lint(GOOD.replace(": notes.jsonl holds one row per stored note", ":"))
        self.assertFailsOn(r, "C3", "no statement")

    def test_unknown_kind_fails(self):
        r = self.lint(GOOD.replace("[inv]", "[always]"))
        self.assertFailsOn(r, "C3", "unknown kind 'always'")

    def test_clause_missing_from_proof_fails(self):
        r = self.lint(GOOD, "C1 and C2")
        self.assertFailsOn(r, "C3", "not named in '## Proof'")

    def test_proof_match_is_a_whole_token(self):
        # C10 in Proof does not name C1.
        r = self.lint("- C1 [pre]: the title is a non-empty string", "C10")
        self.assertFailsOn(r, "C1", "not named in '## Proof'")

    def test_bullet_without_kind_fails(self):
        r = self.lint(GOOD + "\n- C4: no kind given", "C1, C2, C3 and C4")
        self.assertFailsOn(r, "C4", "[kind]: statement")

    def test_no_contract_section_lints_as_before(self):
        r = cli("lint", self.vd)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("LINT_RESULT: PASS", r.stdout)


if __name__ == "__main__":
    unittest.main()
