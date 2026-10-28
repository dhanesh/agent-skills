#!/usr/bin/env python3
"""Unit tests for the tier tool allowlist and the one-clause rule (spec R4; stdlib only).

Each test drives the real CLI in a subprocess. The proof tool is a small Python script
that writes a marker file, so a refused command can be shown never to have run.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "verify_evidence.py")
sys.path.insert(0, HERE)
import verify_evidence as V  # noqa: E402

GENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
# A fake proof tool: writes the marker file named in argv[1], then passes.
FAKE = ("import sys\n"
        "open(sys.argv[1], 'w').write('ran\\n')\n"
        "print('0 errors')\n")


def listing(root):
    """Every file under root, relative, with its sha256: a snapshot to compare."""
    snap = {}
    for d, _, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                snap[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return snap


class RecordTool(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.root = os.path.join(self.tmp, "app")
        os.makedirs(self.root)
        with open(os.path.join(self.root, "tool.py"), "w", encoding="utf-8") as f:
            f.write(FAKE)
        for args in (("init", "-q", "-b", "main"), ("add", "-A"),
                     ("commit", "-q", "-m", "x")):
            subprocess.run(["git", "-C", self.root, *args], env=GENV, check=True,
                           capture_output=True)
        self.sha = V.head_sha(self.root)
        self.ev = os.path.join(self.tmp, "evidence")
        # An evidence directory that already holds a record, so "unchanged" is tested
        # against real content and not only against absence.
        old = os.path.join(self.ev, "a", "notes-create", self.sha)
        os.makedirs(old)
        with open(os.path.join(old, "evidence.json"), "w", encoding="utf-8") as f:
            f.write('{"kind": "evidence"}\n')
        self.marker = os.path.join(self.tmp, "ran.marker")
        self.env = dict(os.environ, VERIFY_EVIDENCE_DIR=self.ev)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def record(self, tier, tool, clauses="C1"):
        cmd = "%s tool.py %s" % (sys.executable, self.marker)
        return subprocess.run(
            [sys.executable, SCRIPT, "record", "--instance", "a", "--feature",
             "notes-create", "--verifier", "v1", "--tier", tier, "--tool", tool,
             "--clauses", clauses, "--cmd", cmd, "--worktree", self.root],
            capture_output=True, text=True, env=self.env)

    def assertRefused(self, r, *needles):
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        for n in needles:
            self.assertIn(n, r.stderr)
        self.assertFalse(os.path.exists(self.marker), "the refused tool ran")

    def test_tool_from_another_tier_is_refused(self):
        before = listing(self.ev)
        r = self.record("proved", "hypothesis")
        self.assertRefused(r, "not allowed for --tier proved", "use verus")
        self.assertEqual(listing(self.ev), before)

    def test_tool_on_no_list_is_refused(self):
        before = listing(self.ev)
        r = self.record("property", "echo")
        self.assertRefused(r, "not allowed for --tier property",
                           "use proptest, hypothesis, fast-check, rapid")
        self.assertEqual(listing(self.ev), before)

    def test_crosshair_is_not_bounded(self):
        before = listing(self.ev)
        self.assertRefused(self.record("bounded", "crosshair"), "use kani")
        self.assertEqual(listing(self.ev), before)

    def test_clause_list_is_refused(self):
        before = listing(self.ev)
        r = self.record("property", "hypothesis", "C1,C2")
        self.assertRefused(r, "names 2 clauses", "exactly one clause is required")
        self.assertEqual(listing(self.ev), before)

    def test_more_or_less_than_one_clause_is_refused(self):
        before = listing(self.ev)
        for value, count in (("C1 C2", 2), ("C1;C2;C3", 3), ("", 0), (" , ", 0)):
            with self.subTest(clauses=value):
                r = self.record("proved", "verus", value)
                self.assertRefused(r, "names %d clauses" % count,
                                   "exactly one clause is required")
                self.assertEqual(listing(self.ev), before)

    def test_every_allowed_pair_is_accepted(self):
        for tier, tools in sorted(V.TIER_TOOLS.items()):
            for tool in tools:
                with self.subTest(tier=tier, tool=tool):
                    r = self.record(tier, tool)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    path = os.path.join(self.ev, "a", "notes-create", self.sha,
                                        "proof-%s-%s-C1.json" % (tier, tool))
                    self.assertEqual(r.stdout.strip(), "PROOF: %s %s" % (tier, path))
                    self.assertTrue(os.path.isfile(path))
                    self.assertTrue(os.path.exists(self.marker))
                    os.remove(self.marker)

    def test_allowlist_is_the_spec_table(self):
        self.assertEqual(V.TIER_TOOLS, {
            "proved": ("verus",), "bounded": ("kani",),
            "property": ("proptest", "hypothesis", "fast-check", "rapid")})
        self.assertEqual(tuple(V.TIER_TOOLS), V.TIERS)


if __name__ == "__main__":
    unittest.main()
