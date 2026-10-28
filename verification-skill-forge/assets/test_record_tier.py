#!/usr/bin/env python3
"""Unit tests for `verify_evidence.py record --tier` (spec R3; stdlib only, offline).

The proof tools are faked by small Python scripts in the test repo, so no verus or
other prover is needed. The artifact check is factory-conductor's own _check_record.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(REPO, "factory-conductor", "assets"))
import verify_evidence as V  # noqa: E402
import conductor as C  # noqa: E402

GENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
# A fake prover: prints to both streams, records its cwd, exits with argv[1].
FAKE = ("import os, sys\n"
        "print('verified 3 functions, 0 errors in', os.getcwd())\n"
        "sys.stderr.write('note: fake prover\\n')\n"
        "sys.exit(int(sys.argv[1]))\n")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = V.main(argv)
        except SystemExit as e:  # argparse errors
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def tree(root):
    """Every file under root, relative, with its bytes: a snapshot to compare."""
    snap = {}
    for d, _, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                snap[os.path.relpath(p, root)] = f.read()
    return snap


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.root = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(self.root, "sub"))
        with open(os.path.join(self.root, "prover.py"), "w", encoding="utf-8") as f:
            f.write(FAKE)
        for args in (("init", "-q", "-b", "main"), ("add", "-A"),
                     ("commit", "-q", "-m", "x")):
            subprocess.run(["git", "-C", self.root, *args], env=GENV, check=True,
                           capture_output=True)
        self.sha = V.head_sha(self.root)
        self.env = os.environ.pop("VERIFY_EVIDENCE_DIR", None)
        self.ev = os.path.join(self.root, ".verify")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self.env is not None:
            os.environ["VERIFY_EVIDENCE_DIR"] = self.env

    def tier(self, code, *extra, worktree=None):
        cmd = "%s prover.py %d" % (sys.executable, code)
        return run(["record", "--instance", "a", "--feature", "notes-create",
                    "--verifier", "v1", "--tier", "proved", "--tool", "verus",
                    "--clauses", "C1", "--cmd", cmd,
                    "--worktree", worktree or self.root, *extra])

    def outdir(self):
        return os.path.join(self.ev, "a", "notes-create", self.sha)


class RecordTier(Base):
    def test_exit_zero_writes_pinned_proof_record(self):
        # Run from a subdirectory: the command still runs from the worktree root.
        rc, out, err = self.tier(0, worktree=os.path.join(self.root, "sub"))
        self.assertEqual(rc, 0, err)
        path = os.path.join(self.outdir(), "proof-proved-verus-C1.json")
        self.assertIn("PROOF: proved %s" % path, out)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["argv"], [sys.executable, "prover.py", "0"])
        self.assertEqual(doc["exit_status"], 0)
        self.assertEqual(doc["verifier"], "v1")
        self.assertEqual((doc["tier"], doc["tool"], doc["clauses"]),
                         ("proved", "verus", ["C1"]))
        self.assertEqual((doc["sha"], doc["instance"], doc["feature"]),
                         (self.sha, "a", "notes-create"))
        self.assertNotIn("action", doc)
        paths = [x["path"] for x in doc["artifacts"]]
        self.assertEqual(paths, ["proof-proved-verus-C1.stdout",
                                 "proof-proved-verus-C1.stderr"])
        for art in doc["artifacts"]:
            p = os.path.join(self.outdir(), art["path"])
            self.assertEqual(V.sha256_file(p), art["sha256"])
        with open(os.path.join(self.outdir(), paths[0]), encoding="utf-8") as f:
            stdout = f.read()
        self.assertIn("verified 3 functions", stdout)
        cwd = stdout.split(" in ", 1)[1].strip()
        self.assertEqual(os.path.realpath(cwd), os.path.realpath(self.root))

    def test_edited_stdout_is_reported_altered_by_conductor(self):
        rc, _, err = self.tier(0)
        self.assertEqual(rc, 0, err)
        out = self.outdir()
        with open(os.path.join(out, "proof-proved-verus-C1.json"), encoding="utf-8") as f:
            doc = json.load(f)
        self.assertIsNone(C._check_record(doc, "notes-create", self.sha, "v1", out, self.ev))
        with open(os.path.join(out, "proof-proved-verus-C1.stdout"), "a") as f:
            f.write("forged\n")
        self.assertEqual(C._check_record(doc, "notes-create", self.sha, "v1", out, self.ev),
                         "evidence-artifact-altered")

    def test_exit_one_writes_nothing(self):
        before = tree(self.root)
        rc, out, err = self.tier(1)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("exited 1", err)
        self.assertIn("nothing recorded", err)
        self.assertFalse(os.path.exists(self.ev))
        self.assertEqual(tree(self.root), before)

    def test_invalid_input_writes_nothing(self):
        cases = [("--clauses", "C1,C2"), ("--tool", "Bad Tool"), ("--cmd", "'unclosed"),
                 ("--cmd", "   "), ("--verifier", " "), ("--artifact", "prover.py"),
                 ("--action", "x"), ("--timeout", "0")]
        for flag, value in cases:
            with self.subTest(flag=flag, value=value):
                rc, _, err = self.tier(0, flag, value)
                self.assertEqual(rc, 2, err)
                self.assertFalse(os.path.exists(self.ev))

    def test_missing_tool_writes_nothing(self):
        rc, _, err = run(["record", "--instance", "a", "--feature", "notes-create",
                          "--verifier", "v1", "--tier", "proved", "--tool", "verus",
                          "--clauses", "C1", "--cmd", "no-such-prover-xyz",
                          "--worktree", self.root])
        self.assertEqual(rc, 1)
        self.assertIn("cannot run", err)
        self.assertFalse(os.path.exists(self.ev))

    def test_timeout_writes_nothing(self):
        cmd = "%s -c 'import time; time.sleep(5)'" % sys.executable
        rc, _, err = run(["record", "--instance", "a", "--feature", "notes-create",
                          "--verifier", "v1", "--tier", "proved", "--tool", "verus",
                          "--clauses", "C1", "--cmd", cmd, "--timeout", "1",
                          "--worktree", self.root])
        self.assertEqual(rc, 1)
        self.assertIn("did not finish", err)
        self.assertFalse(os.path.exists(self.ev))


class RuntimeRecordUnchanged(Base):
    def test_plain_record_writes_evidence_json(self):
        art = os.path.join(self.tmp, "create.json")
        with open(art, "w", encoding="utf-8") as f:
            f.write("{}\n")
        rc, out, err = run(["record", "--instance", "a", "--feature", "notes-create",
                            "--verifier", "v1", "--result", "pass", "--action", "POST",
                            "--observed", "201", "--side-effect", "a row",
                            "--artifact", art, "--worktree", self.root])
        self.assertEqual(rc, 0, err)
        path = os.path.join(self.outdir(), "evidence.json")
        self.assertEqual(out.strip(), "EVIDENCE: pass %s" % path)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(sorted(doc), sorted(
            ["schema", "kind", "instance", "feature", "sha", "verifier", "result",
             "action", "observed", "side_effects", "artifacts", "captured_at"]))
        self.assertEqual(doc["artifacts"], [{"path": "create.json",
                                             "sha256": V.sha256_file(art)}])
        self.assertIsNone(C._check_record(doc, "notes-create", self.sha, "v1",
                                          self.outdir(), self.ev))

    def test_plain_record_still_requires_its_flags(self):
        rc, _, err = run(["record", "--instance", "a", "--feature", "notes-create",
                          "--verifier", "v1", "--worktree", self.root])
        self.assertEqual(rc, 2)
        self.assertIn("the following arguments are required: --result, --action, "
                      "--observed", err)
        for missing in ([], ["--side-effect", "a row"]):
            rc, _, err = run(["record", "--instance", "a", "--feature", "notes-create",
                              "--verifier", "v1", "--result", "pass", "--action", "a",
                              "--observed", "o", "--worktree", self.root, *missing])
            self.assertEqual(rc, 2, err)
        self.assertFalse(os.path.exists(self.ev))

    def test_plain_record_with_missing_artifact_writes_nothing(self):
        rc, _, err = run(["record", "--instance", "a", "--feature", "notes-create",
                          "--verifier", "v1", "--result", "pass", "--action", "a",
                          "--observed", "o", "--side-effect", "x", "--artifact",
                          os.path.join(self.tmp, "nope.json"), "--worktree", self.root])
        self.assertEqual(rc, 2)
        self.assertIn("does not exist", err)
        self.assertFalse(os.path.exists(self.ev))

    def test_tier_flags_without_tier_are_refused(self):
        rc, _, err = run(["record", "--instance", "a", "--feature", "notes-create",
                          "--verifier", "v1", "--result", "pass", "--action", "a",
                          "--observed", "o", "--tool", "verus", "--worktree", self.root])
        self.assertEqual(rc, 2)
        self.assertIn("add --tier", err)


if __name__ == "__main__":
    unittest.main()
