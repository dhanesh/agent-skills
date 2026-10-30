#!/usr/bin/env python3
"""Unit tests for the mutant check of bounded and property records (spec R5; stdlib only).

Each test drives the real CLI in a subprocess against a temp git repo. The proof tool is a
small Python script that prints what the real tool prints for the state in mode.txt, and
appends its cwd to a log outside the repo, so a test can see where (and whether) it ran.
It is reached through PATH shims named after each tool's runner (pytest, cargo, ...).
"""
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
SCRIPT = os.path.join(HERE, "verify_evidence.py")
DOC = os.path.join(os.path.dirname(HERE), "references", "proof-tiers.md")
sys.path.insert(0, HERE)
import verify_evidence as V  # noqa: E402

GENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

# What each tool prints when its check fails on a mutant, and when the mutant only broke
# the build or the harness. Shortened from the tools' real reports.
FAILED = {
    "hypothesis": (
        "============================= test session starts ==============================\n"
        "collected 1 item\n\ntests/test_notes.py F                                    [100%]\n"
        "=================================== FAILURES ===================================\n"
        "E   AssertionError: assert '' == ' '\n"
        "Falsifying example: test_title_roundtrip(\n    title=' ',\n)\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_notes.py::test_title_roundtrip - AssertionError\n"
        "============================== 1 failed in 0.31s ===============================\n"),
    "proptest": (
        "running 1 test\ntest tests::title_roundtrip ... FAILED\n\nfailures:\n\n"
        "---- tests::title_roundtrip stdout ----\nthread 'tests::title_roundtrip' panicked "
        "at src/lib.rs:40:5:\nTest failed: assertion failed: back == t; minimal failing "
        "input: t = \" \"\n\tsuccesses: 3\n\tlocal rejects: 0\n\n"
        "test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured\n"),
    "kani": (
        "Checking harness check_title...\n\nRESULTS:\nCheck 1: title.assertion.1\n"
        "\t - Status: FAILURE\n\t - Description: \"assertion failed: n <= MAX\"\n\n"
        "SUMMARY:\n ** 1 of 12 failed\n\nVERIFICATION:- FAILED\n"
        "Verification Time: 0.41s\n"),
    "fast-check": (
        "FAIL tests/notes.test.ts\n  x title round-trips (12 ms)\n\n"
        "Property failed after 4 tests\n{ seed: 1234, path: \"3:0\", endOnFailure: true }\n"
        "Counterexample: [\" \"]\nShrunk 1 time(s)\n"),
    "rapid": (
        "--- FAIL: TestTitleRoundtrip (0.00s)\n    notes_test.go:14: [rapid] failed after "
        "3 tests: title changed\n        To reproduce, specify -run=\"TestTitleRoundtrip\" "
        "-rapid.failfile=\"testdata/rapid/TestTitleRoundtrip.fail\"\n"
        "    notes_test.go:10: [rapid] draw t: \" \"\nFAIL\nFAIL\texample/notes\t0.01s\n"),
}
BROKEN = {
    "hypothesis": (
        "==================================== ERRORS ====================================\n"
        "_______________________ ERROR collecting tests/test_notes.py ________________\n"
        "E   SyntaxError: invalid syntax\n"
        "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
        "=============================== 1 error in 0.05s ===============================\n"),
    "proptest": ("error[E0425]: cannot find value `tt` in this scope\n --> src/lib.rs:40:20\n"
                 "error: could not compile `notes` (lib test) due to 1 previous error\n"),
    "kani": ("error[E0308]: mismatched types\n --> src/lib.rs:12:5\n"
             "error: could not compile `notes`\n"),
    "fast-check": ("FAIL tests/notes.test.ts\n  ● Test suite failed to run\n\n"
                   "    tests/notes.test.ts:3:7 - error TS2322: Type 'string' is not "
                   "assignable to type 'number'.\n"),
    "rapid": ("# example/notes\n./notes.go:9:2: undefined: tt\n"
              "FAIL\texample/notes [build failed]\n"),
}

# The fake tool: mode.txt says which state the code is in. "ok" passes; any other mode
# prints MODES[mode] for the tool named in argv[2] and exits 1 (a crash exits 2).
TOOL = ("import json, os, sys\n"
        "with open(sys.argv[1], 'a') as f:\n"
        "    f.write(os.getcwd() + '\\n')\n"
        "mode = open('mode.txt').read().strip()\n"
        "if mode in ('ok', 'pass'):\n"
        "    print('test result: ok. 1 passed; 0 failed')\n"
        "    sys.exit(0)\n"
        "out = json.load(open('modes.json'))[mode][sys.argv[2]]\n"
        "sys.stdout.write(out)\n"
        "sys.exit(2 if mode == 'broken' else 1)\n")


# The --cmd each tool is recorded with, and the shims it needs: name -> words to drop.
RUN = {"verus": "verus", "kani": "kani", "proptest": "cargo test", "hypothesis": "pytest",
       "fast-check": "vitest", "rapid": "go test"}
SHIMS = {"verus": 0, "kani": 0, "cargo": 1, "pytest": 0, "vitest": 0, "go": 1}


def shim(bin_dir, name, skip=0):
    """Write bin_dir/name: it drops its first skip args and runs tool.py from its cwd."""
    path = os.path.join(bin_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!%s\nimport runpy, sys\nsys.argv = ['tool.py'] + sys.argv[%d:]\n"
                "runpy.run_path('tool.py', run_name='__main__')\n"
                % (sys.executable, 1 + skip))
    os.chmod(path, 0o755)
    return path


def patch_to(mode):
    """A diff that changes mode.txt from ok to mode."""
    return ("diff --git a/mode.txt b/mode.txt\n--- a/mode.txt\n+++ b/mode.txt\n"
            "@@ -1 +1 @@\n-ok\n+%s\n" % mode)


def listing(root):
    """Every file under root, relative, with its sha256: a snapshot to compare."""
    snap = {}
    for d, _, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                snap[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return snap


class RecordMutant(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.root = os.path.join(self.tmp, "app")
        os.makedirs(self.root)
        files = {"tool.py": TOOL, "mode.txt": "ok\n",
                 "modes.json": json.dumps({"failed": FAILED, "broken": BROKEN})}
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
        # A private TMPDIR, so a mutant copy left behind shows up as a file here.
        self.scratch = os.path.join(self.tmp, "scratch")
        os.makedirs(self.scratch)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        for name, skip in SHIMS.items():
            shim(self.bin, name, skip)
        self.env = dict(os.environ, VERIFY_EVIDENCE_DIR=self.ev, TMPDIR=self.scratch,
                        PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        self.before = listing(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def diff(self, mode):
        path = os.path.join(self.tmp, "mutant-%s.diff" % mode)
        with open(path, "w", encoding="utf-8") as f:
            f.write(patch_to(mode))
        return path

    def record(self, *extra, tier="property", tool="hypothesis"):
        cmd = "%s %s %s" % (RUN[tool], self.log, tool)
        return subprocess.run(
            [sys.executable, SCRIPT, "record", "--instance", "a", "--feature",
             "notes-create", "--verifier", "v1", "--tier", tier, "--tool", tool,
             "--clauses", "C1", "--cmd", cmd, "--worktree", self.root, *extra],
            capture_output=True, text=True, env=self.env)

    def runs(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                return f.read().split()
        except FileNotFoundError:
            return []

    def assertClean(self):
        """The worktree is unchanged and no mutant copy is left behind."""
        self.assertEqual(listing(self.root), self.before)
        self.assertEqual(os.listdir(self.scratch), [])

    def assertNothingWritten(self, r, code, *needles):
        self.assertEqual(r.returncode, code, r.stderr)
        self.assertEqual(r.stdout, "")
        for n in needles:
            self.assertIn(n, r.stderr)
        self.assertEqual(listing(self.ev), {})
        self.assertClean()

    # ── refusals ──────────────────────────────────────────────────────────────
    def test_property_without_a_mutant_is_refused(self):
        for extra in ((), ("--mutant-author", "verifier"),
                      ("--mutant-patch", self.diff("failed"))):
            with self.subTest(extra=extra):
                r = self.record(*extra)
                self.assertNothingWritten(r, 2, "needs --mutant-patch DIFF and "
                                          "--mutant-author verifier|forge")
        self.assertEqual(self.runs(), [], "the tool ran without a mutant")

    def test_bounded_without_a_mutant_is_refused(self):
        r = self.record(tier="bounded", tool="kani")
        self.assertNothingWritten(r, 2, "--tier bounded needs --mutant-patch")
        self.assertEqual(self.runs(), [])

    def test_bad_author_is_refused(self):
        r = self.record("--mutant-patch", self.diff("failed"), "--mutant-author", "me")
        self.assertNothingWritten(r, 2, "--mutant-author 'me'", "verifier or forge")
        self.assertEqual(self.runs(), [])

    def test_missing_or_empty_patch_is_refused(self):
        r = self.record("--mutant-patch", os.path.join(self.tmp, "nope.diff"),
                        "--mutant-author", "verifier")
        self.assertNothingWritten(r, 2, "cannot read --mutant-patch")
        empty = os.path.join(self.tmp, "empty.diff")
        open(empty, "w").close()
        r = self.record("--mutant-patch", empty, "--mutant-author", "verifier")
        self.assertNothingWritten(r, 2, "is empty")
        self.assertEqual(self.runs(), [])

    def test_patch_that_does_not_apply_is_refused_before_any_run(self):
        path = os.path.join(self.tmp, "stale.diff")
        with open(path, "w", encoding="utf-8") as f:
            f.write(patch_to("failed").replace("-ok", "-stale"))
        r = self.record("--mutant-patch", path, "--mutant-author", "verifier")
        self.assertNothingWritten(r, 2, "--mutant-patch does not apply")
        self.assertEqual(self.runs(), [], "a command ran before the patch was checked")

    def test_proved_refuses_mutant_flags(self):
        r = self.record("--mutant-patch", self.diff("failed"), "--mutant-author",
                        "forge", tier="proved", tool="verus")
        self.assertNothingWritten(r, 2, "--mutant-patch, --mutant-author cannot be used "
                                  "with --tier proved")
        self.assertEqual(self.runs(), [])

    def test_mutant_flags_without_tier_are_refused(self):
        r = subprocess.run(
            [sys.executable, SCRIPT, "record", "--instance", "a", "--feature",
             "notes-create", "--verifier", "v1", "--result", "pass", "--action", "x",
             "--observed", "y", "--side-effect", "none: z", "--artifact", self.log,
             "--mutant-author", "verifier", "--worktree", self.root],
            capture_output=True, text=True, env=self.env)
        self.assertNothingWritten(r, 2, "--mutant-author only apply to a proof record")

    # ── mutant outcomes ───────────────────────────────────────────────────────
    def test_mutant_that_passes_writes_nothing(self):
        r = self.record("--mutant-patch", self.diff("pass"), "--mutant-author", "verifier")
        self.assertNothingWritten(r, 1, "exited 0 on the mutant", "nothing recorded")
        head, mutant = self.runs()
        self.assertEqual(head, self.root)
        self.assertNotEqual(mutant, self.root)

    def test_mutant_that_only_breaks_the_build_writes_nothing(self):
        for tier, tool in (("property", t) for t in V.TIER_TOOLS["property"]):
            with self.subTest(tool=tool):
                r = self.record("--mutant-patch", self.diff("broken"),
                                "--mutant-author", "verifier", tier=tier, tool=tool)
                self.assertNothingWritten(r, 1, "on the mutant but its output",
                                          "breaks the build or the harness")
        r = self.record("--mutant-patch", self.diff("broken"), "--mutant-author",
                        "forge", tier="bounded", tool="kani")
        self.assertNothingWritten(r, 1, "lacks 'VERIFICATION:- FAILED'")

    def test_mutant_failing_another_tools_way_lacks_the_signature(self):
        # A hypothesis record whose mutant output is a kani report: it failed, but not
        # the way a failed property test does.
        cmd = "pytest %s kani" % self.log
        r = subprocess.run(
            [sys.executable, SCRIPT, "record", "--instance", "a", "--feature",
             "notes-create", "--verifier", "v1", "--tier", "property", "--tool",
             "hypothesis", "--clauses", "C1", "--cmd", cmd, "--worktree", self.root,
             "--mutant-patch", self.diff("failed"), "--mutant-author", "verifier"],
            capture_output=True, text=True, env=self.env)
        self.assertNothingWritten(r, 1, "lacks 'Falsifying example'")

    def test_mutant_that_fails_with_the_signature_is_recorded(self):
        diff = self.diff("failed")
        r = self.record("--mutant-patch", diff, "--mutant-author", "verifier")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertClean()
        out = os.path.join(self.ev, "a", "notes-create", self.sha)
        path = os.path.join(out, "proof-property-hypothesis-C1.json")
        self.assertEqual(r.stdout.strip(), "PROOF: property %s" % path)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual((doc["tier"], doc["tool"], doc["clauses"], doc["result"]),
                         ("property", "hypothesis", ["C1"], "pass"))
        self.assertEqual(doc["mutant_author"], "verifier")
        self.assertEqual((doc["exit_status"], doc["mutant_exit_status"]), (0, 1))
        stem = "proof-property-hypothesis-C1"
        names = [x["path"] for x in doc["artifacts"]]
        self.assertEqual(names, [stem + e for e in (
            ".stdout", ".stderr", ".mutant.patch", ".mutant.exit", ".mutant.stdout",
            ".mutant.stderr")])
        for art in doc["artifacts"]:
            with open(os.path.join(out, art["path"]), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), art["sha256"])

        def read(ext):
            with open(os.path.join(out, stem + ext), encoding="utf-8") as f:
                return f.read()
        with open(diff, encoding="utf-8") as f:
            self.assertEqual(read(".mutant.patch"), f.read())
        self.assertEqual(read(".mutant.exit"), "1\n")
        self.assertEqual(read(".mutant.stdout"), FAILED["hypothesis"])
        self.assertIn("1 passed", read(".stdout"))
        # Both runs used the same argv: once at the root, once in a copy elsewhere.
        head, mutant = self.runs()
        self.assertEqual(head, self.root)
        self.assertFalse(mutant.startswith(self.root))
        self.assertTrue(mutant.startswith(self.scratch))

    def test_every_tool_records_with_its_signature(self):
        for tier in V.MUTANT_TIERS:
            for tool in V.TIER_TOOLS[tier]:
                with self.subTest(tool=tool):
                    r = self.record("--mutant-patch", self.diff("failed"),
                                    "--mutant-author", "forge", tier=tier, tool=tool)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    path = os.path.join(self.ev, "a", "notes-create", self.sha,
                                        "proof-%s-%s-C1.json" % (tier, tool))
                    with open(path, encoding="utf-8") as f:
                        self.assertEqual(json.load(f)["mutant_author"], "forge")
                    self.assertClean()

    def test_uncommitted_change_is_not_in_the_copy(self):
        # The copy is HEAD, not the dirty worktree: a worktree edit that would make the
        # patch not apply does not matter, and is left in place.
        with open(os.path.join(self.root, "mode.txt"), "w", encoding="utf-8") as f:
            f.write("ok\n\n")
        self.before = listing(self.root)
        r = self.record("--mutant-patch", self.diff("failed"), "--mutant-author",
                        "verifier")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertClean()

    # ── the signature table ───────────────────────────────────────────────────
    def test_signatures_cover_the_mutant_tiers(self):
        tools = {t for tier in V.MUTANT_TIERS for t in V.TIER_TOOLS[tier]}
        self.assertEqual(set(V.FAILURE_SIGNATURES), tools)
        for tool in tools:
            with self.subTest(tool=tool):
                self.assertIsNone(V._signature_miss(tool, FAILED[tool].encode()))
                self.assertIsNotNone(V._signature_miss(tool, BROKEN[tool].encode()))
                # A failed check next to a build error still means the build broke.
                both = (FAILED[tool] + BROKEN[tool]).encode()
                self.assertIn("matches", V._signature_miss(tool, both))
                self.assertIsNotNone(V._signature_miss(tool, b""))

    def test_doc_table_matches_the_code(self):
        with open(DOC, encoding="utf-8") as f:
            text = f.read()
        section = text.split("## Failure signatures", 1)[1]
        rows = {}
        for line in section.splitlines():
            m = re.match(r"^\| `([a-z-]+)` \| (.*) \| (.*) \|$", line)
            if m:
                rows[m.group(1)] = {
                    "require": tuple(re.findall(r"`([^`]+)`", m.group(2))),
                    "reject": tuple(re.findall(r"`([^`]+)`", m.group(3)))}
        self.assertEqual(rows, V.FAILURE_SIGNATURES)


if __name__ == "__main__":
    unittest.main()
