#!/usr/bin/env python3
"""Unit tests for the clean-head rule of `record --tier` (spec R3; stdlib only, offline).

A proof record runs on the committed head only. The recorder refuses a worktree whose
tracked files differ from HEAD, and runs the head proof and the mutant proof each in its
own clean checkout of HEAD. Each test drives the real CLI in a subprocess against a temp
git repo. The proof tool is faked by PATH shims named verus and pytest, which run
tool.py from their cwd. tool.py needs the file named in argv[2], logs its cwd outside the
repo, and passes or fails on mode.txt.
"""
import hashlib
import json
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

# argv: tool.py LOG NEEDED. A missing NEEDED file is a crash (exit 3). mode "ok" passes;
# any other mode prints a failed Hypothesis report and exits 1.
TOOL = ("import os, sys\n"
        "with open(sys.argv[1], 'a') as f:\n"
        "    f.write(os.getcwd() + '\\n')\n"
        "if not os.path.exists(sys.argv[2]):\n"
        "    sys.stderr.write('missing %s\\n' % sys.argv[2])\n"
        "    sys.exit(3)\n"
        "if open('mode.txt').read().strip() == 'ok':\n"
        "    print('1 passed')\n"
        "    sys.exit(0)\n"
        "print('Falsifying example: t(x=0)')\n"
        "print('1 failed in 0.01s')\n"
        "sys.exit(1)\n")
MUTANT = ("diff --git a/mode.txt b/mode.txt\n--- a/mode.txt\n+++ b/mode.txt\n"
          "@@ -1 +1 @@\n-ok\n+failed\n")


def shim(bin_dir, name):
    path = os.path.join(bin_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!%s\nimport runpy, sys\nsys.argv = ['tool.py'] + sys.argv[1:]\n"
                "runpy.run_path('tool.py', run_name='__main__')\n" % sys.executable)
    os.chmod(path, 0o755)


def listing(root):
    """Every file under root (the .git directory included), with its sha256."""
    snap = {}
    for d, _, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                snap[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return snap


class RecordCleanHead(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.root = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(self.root, "proofs"))
        # proofs/spec.rs is tracked but export-ignore: git archive would drop it.
        files = {"tool.py": TOOL, "mode.txt": "ok\n", "src.rs": "fn f() {}\n",
                 "proofs/spec.rs": "spec\n", ".gitattributes": "proofs/ export-ignore\n"}
        for name, text in files.items():
            with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
                f.write(text)
        for args in (("init", "-q", "-b", "main"), ("add", "-A"),
                     ("commit", "-q", "-m", "x")):
            subprocess.run(["git", "-C", self.root, *args], env=GENV, check=True,
                           capture_output=True)
        self.sha = V.head_sha(self.root)
        self.ev = os.path.join(self.tmp, "evidence")
        os.makedirs(self.ev)
        self.log = os.path.join(self.tmp, "ran.log")
        self.scratch = os.path.join(self.tmp, "scratch")
        os.makedirs(self.scratch)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        shim(self.bin, "verus")
        shim(self.bin, "pytest")
        self.patch = os.path.join(self.tmp, "mutant.diff")
        with open(self.patch, "w", encoding="utf-8") as f:
            f.write(MUTANT)
        self.env = dict(os.environ, VERIFY_EVIDENCE_DIR=self.ev, TMPDIR=self.scratch,
                        PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.root, *args], env=GENV, check=True,
                              capture_output=True, text=True)

    def write(self, name, text):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
            f.write(text)

    def record(self, needed="src.rs", tier="proved"):
        if tier == "proved":
            tool, cmd, extra = "verus", "verus", ()
        else:
            tool, cmd = "hypothesis", "pytest"
            extra = ("--mutant-patch", self.patch, "--mutant-author", "verifier")
        return subprocess.run(
            [sys.executable, SCRIPT, "record", "--instance", "a", "--feature",
             "notes-create", "--verifier", "v1", "--tier", tier, "--tool", tool,
             "--clauses", "C1", "--cmd", "%s %s %s" % (cmd, self.log, needed),
             "--worktree", self.root, *extra],
            capture_output=True, text=True, env=self.env)

    def runs(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                return f.read().split()
        except FileNotFoundError:
            return []

    def assertRefusedDirty(self, r, *paths):
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("tracked changes that are not committed", r.stderr)
        for p in paths:
            self.assertIn(p, r.stderr)
        self.assertEqual(listing(self.ev), {}, "a refused record wrote evidence")
        self.assertEqual(self.runs(), [], "a command ran in a dirty worktree")
        self.assertEqual(os.listdir(self.scratch), [], "a checkout was made")

    # ── the dirty-tree refusal ────────────────────────────────────────────────
    def test_unstaged_tracked_edit_is_refused(self):
        self.write("src.rs", "fn f() { 1 }\n")
        before = listing(self.root)
        for tier in ("proved", "property"):
            with self.subTest(tier=tier):
                self.assertRefusedDirty(self.record(tier=tier), "src.rs")
        self.assertEqual(listing(self.root), before, "the edit was not left in place")

    def test_staged_tracked_edit_is_refused(self):
        self.write("mode.txt", "ok\n\n")
        self.git("add", "mode.txt")
        for tier in ("proved", "property"):
            with self.subTest(tier=tier):
                self.assertRefusedDirty(self.record(tier=tier), "mode.txt")

    def test_every_dirty_path_is_named(self):
        self.write("src.rs", "changed\n")
        os.remove(os.path.join(self.root, "proofs", "spec.rs"))
        self.assertRefusedDirty(self.record(), "src.rs", "proofs/spec.rs")

    def test_untracked_file_is_not_dirty(self):
        self.write("notes.txt", "scratch\n")
        r = self.record()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PROOF: proved", r.stdout)

    # ── the clean checkout ────────────────────────────────────────────────────
    def test_export_ignored_tracked_file_is_in_both_checkouts(self):
        for tier in ("proved", "property"):
            with self.subTest(tier=tier):
                r = self.record("proofs/spec.rs", tier=tier)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("PROOF: %s" % tier, r.stdout)

    def test_untracked_file_is_not_in_the_checkout(self):
        # The file sits in the worktree, so a run in the worktree would find it.
        self.write("local.rs", "not committed\n")
        r = self.record("local.rs")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("exited 3", r.stderr)
        self.assertEqual(listing(self.ev), {})
        self.assertNotIn(self.root, self.runs())

    def test_record_says_it_ran_in_a_checkout(self):
        before = listing(self.root)
        r = self.record(tier="property")
        self.assertEqual(r.returncode, 0, r.stderr)
        path = os.path.join(self.ev, "a", "notes-create", self.sha,
                            "proof-property-hypothesis-C1.json")
        self.assertEqual(r.stdout.strip(), "PROOF: property %s" % path)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual((doc["cwd"], doc["ran_in"]), (".", "checkout"))
        # Two runs, each in its own checkout outside the worktree; both are removed,
        # and the worktree, its index and its .git directory are unchanged.
        head, mutant = self.runs()
        self.assertNotEqual(head, mutant)
        for d in (head, mutant):
            self.assertTrue(d.startswith(self.scratch), d)
            self.assertFalse(os.path.exists(d))
        self.assertEqual(os.listdir(self.scratch), [])
        self.assertEqual(listing(self.root), before)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
