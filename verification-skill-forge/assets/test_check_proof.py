#!/usr/bin/env python3
"""Unit tests for `forge.py check-proof`, the escape-hatch scan (spec R4; stdlib only,
offline).

The rule is tested twice against escape_hatch_vectors.json, the vector list that the
conductor's copy of the rule also passes: once per line through forge.escape_hatches,
and once per case through the real CLI on a temp git repo whose base and head commits
hold the case's files.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FORGE = os.path.join(HERE, "forge.py")
VECTORS = os.path.join(HERE, "escape_hatch_vectors.json")
sys.path.insert(0, HERE)
import forge  # noqa: E402

GENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def load_vectors():
    with open(VECTORS, encoding="utf-8") as f:
        return json.load(f)


def git(d, *args):
    subprocess.run(["git", "-C", d, *args], check=True, capture_output=True, env=GENV)


def write_tree(d, files):
    for rel, text in files.items():
        p = os.path.join(d, *rel.split("/"))
        if text is None:
            if os.path.exists(p):
                os.remove(p)
            continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)


def check_proof(*args, cwd=None):
    r = subprocess.run([sys.executable, FORGE, "check-proof", *args], cwd=cwd,
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


class Repo:
    """A temp git repo: a seed commit, the base commit, then the head commit."""

    def __init__(self, base, head):
        self.dir = tempfile.mkdtemp(prefix="check-proof-")
        git(self.dir, "init", "-q")
        write_tree(self.dir, {"seed.txt": "seed\n"})
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-q", "-m", "seed")
        write_tree(self.dir, base)
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-q", "--allow-empty", "-m", "base")
        for rel in base:
            if rel not in head:
                head = dict(head, **{rel: None})
        write_tree(self.dir, head)
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-q", "--allow-empty", "-m", "head")

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestRuleVectors(unittest.TestCase):
    def test_every_line_vector_gives_its_hits(self):
        lines = load_vectors()["lines"]
        self.assertTrue(lines)
        for v in lines:
            with self.subTest(line=v["line"]):
                self.assertEqual(forge.escape_hatches(v["line"]),
                                 [tuple(h) for h in v["hits"]])

    def test_the_vectors_cover_the_named_cases(self):
        hits = {v["line"].strip(): v["hits"] for v in load_vectors()["lines"]}
        for line in ("assume(x)", "x.assume(y);", "#[verifier::external_body]"):
            self.assertTrue(hits[line], line)
        for line in ("prop_assume(x > 0);", "kani::assume(x > 0);", "my_assume(x);",
                     "fn external_body() {}"):
            self.assertEqual(hits[line], [], line)

    def test_the_rule_regex_is_the_spec_regex(self):
        self.assertEqual(forge.ESCAPE_ATTR_RE.pattern,
                         r"#\[verifier\b[^\]]*\bexternal_body\b")
        self.assertIn(r"#\[verifier\b[^\]]*\bexternal_body\b", load_vectors()["rule"])


class TestCheckProofCases(unittest.TestCase):
    def test_every_case_gives_its_result(self):
        cases = load_vectors()["cases"]
        self.assertTrue(cases)
        for case in cases:
            with self.subTest(case=case["name"]):
                repo = Repo(case["base"], case["head"])
                try:
                    code, out, err = check_proof("--root", repo.dir, "--base", "HEAD~1",
                                                 "--head", "HEAD")
                finally:
                    repo.close()
                want = ["ESCAPE: %s:%d %s" % tuple(e) for e in case["escapes"]]
                want.append("CHECK_PROOF_RESULT: %s" % ("FAIL" if want else "PASS"))
                self.assertEqual(out.splitlines(), want, err)
                self.assertEqual(code, 1 if case["escapes"] else 0, out + err)

    def test_the_proof_fixture_fails_with_two_escapes_in_order(self):
        case = load_vectors()["cases"][0]
        repo = Repo(case["base"], case["head"])
        try:
            code, out, _ = check_proof("--root", repo.dir, "--base", "HEAD~1",
                                       "--head", "HEAD")
        finally:
            repo.close()
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines(), [
            "ESCAPE: src/lib.rs:1 external_body",
            "ESCAPE: src/lib.rs:3 assume(",
            "CHECK_PROOF_RESULT: FAIL"])

    def test_root_defaults_to_the_current_directory(self):
        repo = Repo({}, {"src/lib.rs": "fn f() { admit() }\n"})
        try:
            code, out, _ = check_proof("--base", "HEAD~1", "--head", "HEAD", cwd=repo.dir)
        finally:
            repo.close()
        self.assertEqual((code, out.splitlines()),
                         (1, ["ESCAPE: src/lib.rs:1 admit(", "CHECK_PROOF_RESULT: FAIL"]))

    def test_the_head_ref_is_scanned_not_the_worktree(self):
        repo = Repo({}, {"src/lib.rs": "fn f() {}\n"})
        try:
            write_tree(repo.dir, {"src/lib.rs": "fn f() { assume(true) }\n"})
            code, out, _ = check_proof("--root", repo.dir, "--base", "HEAD~1",
                                       "--head", "HEAD")
        finally:
            repo.close()
        self.assertEqual((code, out.splitlines()), (0, ["CHECK_PROOF_RESULT: PASS"]))


class TestCheckProofBadInput(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({}, {"src/lib.rs": "fn f() { assume(true) }\n"})

    def tearDown(self):
        self.repo.close()

    def test_a_root_that_is_not_a_work_tree_exits_2(self):
        plain = tempfile.mkdtemp(prefix="check-proof-plain-")
        try:
            code, out, err = check_proof("--root", plain, "--base", "HEAD~1",
                                         "--head", "HEAD")
        finally:
            shutil.rmtree(plain, ignore_errors=True)
        self.assertEqual(code, 2)
        self.assertNotIn("CHECK_PROOF_RESULT", out)
        self.assertIn("not a git work tree", err)

    def test_a_missing_root_exits_2(self):
        code, _, err = check_proof("--root", os.path.join(self.repo.dir, "absent"),
                                   "--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(code, 2)
        self.assertIn("not a git work tree", err)

    def test_an_unknown_base_or_head_exits_2(self):
        for args in (("--base", "no-such-ref", "--head", "HEAD"),
                     ("--base", "HEAD~1", "--head", "no-such-ref"),
                     ("--base", "HEAD~9", "--head", "HEAD"),
                     ("--base=-x", "--head", "HEAD")):
            with self.subTest(args=args):
                code, out, err = check_proof("--root", self.repo.dir, *args)
                self.assertEqual(code, 2, out + err)
                self.assertNotIn("CHECK_PROOF_RESULT", out)
                self.assertIn("does not resolve to a commit", err)


if __name__ == "__main__":
    unittest.main(verbosity=1)
