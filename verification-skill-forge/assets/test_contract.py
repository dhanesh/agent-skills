#!/usr/bin/env python3
"""Unit tests for `forge.py contract` (stdlib only, offline)."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import forge as F  # noqa: E402

FORGE = os.path.join(HERE, "forge.py")
FIXTURE = os.path.join(os.path.dirname(HERE), "eval", "fixtures", "notes-app")
VERIFY = os.path.join(".claude", "skills", "verify-notes")
FEATURE = os.path.join(VERIFY, "features", "notes-create.md")

CONTRACT = """## Contract

- C1 [pre]: the title is a non-empty string
- C2 [post]: the response is 201 with the stored note's id
- C3 [inv]: notes.jsonl holds one row per stored note

"""


def cli(*args):
    return subprocess.run([sys.executable, FORGE, *args], capture_output=True, text=True)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "app")
        shutil.copytree(FIXTURE, self.root)
        self.vd = os.path.join(self.root, VERIFY)
        self.feature = os.path.join(self.root, FEATURE)
        self.plain = F.read(self.feature)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        with open(self.feature, "w", encoding="utf-8") as f:
            f.write(text)

    def with_contract(self):
        text = self.plain.replace("- constraints: notes:B1\n",
                                  "- constraints: notes:B1\n- trusted: the JSON parser\n", 1)
        return text.replace("## Proof\n", CONTRACT + "## Proof\n", 1)

    def contract(self):
        r = cli("contract", self.feature)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_three_clauses_and_a_trusted_entry(self):
        self.write(self.with_contract())
        got = self.contract()
        self.assertEqual(got["clauses"], [
            {"id": "C1", "kind": "pre", "text": "the title is a non-empty string"},
            {"id": "C2", "kind": "post", "text": "the response is 201 with the stored note's id"},
            {"id": "C3", "kind": "inv", "text": "notes.jsonl holds one row per stored note"},
        ])
        self.assertEqual(got["trusted"], ["the JSON parser"])
        self.assertRegex(got["sha256"], r"^[0-9a-f]{64}\Z")

    def test_hash_covers_the_section_up_to_the_next_heading(self):
        self.write(self.with_contract())
        # The blank line before `## Proof` is part of the section.
        want = hashlib.sha256("\n".join(CONTRACT.splitlines()).encode("utf-8")).hexdigest()
        self.assertEqual(self.contract()["sha256"], want)

    def test_hash_ignores_trailing_spaces_and_other_sections(self):
        text = self.with_contract()
        base = F.parse_contract(text)["sha256"]
        spaced = text.replace("stored note\n", "stored note   \n", 1)
        self.assertEqual(F.parse_contract(spaced)["sha256"], base)
        other = text.replace("## Gotchas\n", "## Gotchas\n\nAlso slow.\n", 1)
        self.assertEqual(F.parse_contract(other)["sha256"], base)
        edited = text.replace("one row per", "a row per", 1)
        self.assertNotEqual(F.parse_contract(edited)["sha256"], base)

    def test_contract_section_keeps_lint_passing(self):
        self.write(self.with_contract())
        r = cli("lint", self.vd)
        self.assertIn("LINT_RESULT: PASS", r.stdout)

    def test_no_section_prints_empty_clauses_and_still_lints(self):
        got = self.contract()
        self.assertEqual(got["clauses"], [])
        self.assertEqual(got["trusted"], [])
        self.assertIsNone(got["sha256"])
        r = cli("lint", self.vd)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("LINT_RESULT: PASS", r.stdout)

    def test_missing_file_says_what_to_pass(self):
        r = cli("contract", os.path.join(self.tmp, "nope.md"))
        self.assertEqual(r.returncode, 2)
        self.assertTrue(re.search(r"cannot read feature file .*features/<id>\.md", r.stderr))


if __name__ == "__main__":
    unittest.main()
