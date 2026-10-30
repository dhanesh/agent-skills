#!/usr/bin/env python3
"""Unit tests for the runner binding of `record --tier` (spec R1; stdlib only, offline).

A tier record counts only when its --cmd runs its --tool. Each test drives the real CLI
in a subprocess against a temp git repo. The proof tools are PATH shims: small Python
scripts named after a runner (pytest, uv, cargo, true) that run the repo's tool.py, which
appends to a marker file. So a refused command can be shown never to have run, and an
accepted one to have run.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(HERE, "verify_evidence.py")
DOC = os.path.join(os.path.dirname(HERE), "references", "proof-tiers.md")
sys.path.insert(0, HERE)
import verify_evidence as V  # noqa: E402

GENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
# The fake proof tool: logs its cwd to $RUN_MARKER, passes while mode.txt says ok, and
# prints Hypothesis's failure report otherwise.
TOOL = ("import os, sys\n"
        "with open(os.environ['RUN_MARKER'], 'a') as f:\n"
        "    f.write(os.getcwd() + '\\n')\n"
        "if open('mode.txt').read().strip() == 'ok':\n"
        "    print('1 passed in 0.01s')\n"
        "    sys.exit(0)\n"
        "print('Falsifying example: f()\\n1 failed in 0.01s')\n"
        "sys.exit(1)\n")
MUTANT = ("diff --git a/mode.txt b/mode.txt\n--- a/mode.txt\n+++ b/mode.txt\n"
          "@@ -1 +1 @@\n-ok\n+failed\n")
# The spec's literal tables (R1).
SPEC_RUNNERS = {"verus": (("verus",), ("cargo", "verus")),
                "kani": (("kani",), ("cargo", "kani")),
                "hypothesis": (("pytest",), ("python", "-m", "pytest"),
                               ("python3", "-m", "pytest")),
                "proptest": (("cargo", "test"),),
                "fast-check": (("vitest",), ("jest",)),
                "rapid": (("go", "test"),)}
SPEC_WRAPPERS = (("uv", "run"), ("poetry", "run"), ("pdm", "run"), ("pipenv", "run"),
                 ("hatch", "run"), ("pnpm", "exec"), ("yarn",), ("bunx",), ("npx",))
# (tool, argv, runner_matches) vectors.
VECTORS = [
    ("verus", "verus src/notes.rs", True),
    ("verus", "cargo verus verify", True),
    ("verus", "true", False),
    ("verus", "cargo test", False),
    ("kani", "cargo kani --harness h", True),
    ("kani", "kani", True),
    ("hypothesis", "pytest", True),
    ("hypothesis", "pytest -q tests/test_notes.py", True),
    ("hypothesis", "python -m pytest", True),
    ("hypothesis", "python3 -m pytest", True),
    ("hypothesis", "python3.12 -m pytest", True),
    ("hypothesis", "uv run pytest", True),
    ("hypothesis", "poetry run pytest", True),
    ("hypothesis", "uv run python3 -m pytest", True),
    ("hypothesis", "uv run true", False),
    ("hypothesis", "./bin/pytest", False),
    ("hypothesis", "/usr/bin/python3 -m pytest", False),
    ("hypothesis", "uv run ./bin/pytest", False),
    ("hypothesis", "uv run --with x pytest", False),
    ("hypothesis", "uv run poetry run pytest", False),
    ("hypothesis", "python -X dev -m pytest", False),
    ("hypothesis", "python3.x -m pytest", False),
    ("hypothesis", "pythonista -m pytest", False),
    ("hypothesis", "py.test", False),
    ("hypothesis", "uv run", False),
    ("hypothesis", "uv pytest", False),
    ("proptest", "cargo test", True),
    ("proptest", "cargo test --release", True),
    ("proptest", "cargo +nightly test", False),
    ("proptest", "cargo", False),
    ("fast-check", "vitest run", True),
    ("fast-check", "npx vitest run", True),
    ("fast-check", "pnpm exec jest", True),
    ("fast-check", "yarn jest", True),
    ("fast-check", "bunx vitest", True),
    ("fast-check", "node vitest", False),
    ("rapid", "go test ./...", True),
    ("rapid", "go vet", False),
    ("echo", "echo", False),
]


def listing(root):
    """Every file under root, relative, with its sha256: a snapshot to compare."""
    snap = {}
    for d, _, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                snap[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return snap


def shim(bin_dir, name, skip=0):
    """Write bin_dir/name: it drops its first skip args and runs tool.py from its cwd."""
    path = os.path.join(bin_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!%s\nimport runpy, sys\nsys.argv = ['tool.py'] + sys.argv[%d:]\n"
                "runpy.run_path('tool.py', run_name='__main__')\n"
                % (sys.executable, 1 + skip))
    os.chmod(path, 0o755)
    return path


class RecordRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.root = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(self.root, "bin"))
        for name, text in (("tool.py", TOOL), ("mode.txt", "ok\n")):
            with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
                f.write(text)
        # A runner-named script inside the repo: a path to it is still refused.
        shim(os.path.join(self.root, "bin"), "pytest")
        for args in (("init", "-q", "-b", "main"), ("add", "-A"),
                     ("commit", "-q", "-m", "x")):
            subprocess.run(["git", "-C", self.root, *args], env=GENV, check=True,
                           capture_output=True)
        self.sha = V.head_sha(self.root)
        self.bin = os.path.join(self.tmp, "shims")
        os.makedirs(self.bin)
        for name, skip in (("pytest", 0), ("python3.12", 2), ("uv", 2), ("poetry", 2),
                           ("true", 0), ("cargo", 0), ("verus", 0)):
            shim(self.bin, name, skip)
        self.ev = os.path.join(self.tmp, "evidence")
        # An evidence directory that already holds a record, so "unchanged" is tested
        # against real content and not only against absence.
        old = os.path.join(self.ev, "a", "notes-create", self.sha)
        os.makedirs(old)
        with open(os.path.join(old, "evidence.json"), "w", encoding="utf-8") as f:
            f.write('{"kind": "evidence"}\n')
        self.marker = os.path.join(self.tmp, "ran.marker")
        self.patch = os.path.join(self.tmp, "mutant.diff")
        with open(self.patch, "w", encoding="utf-8") as f:
            f.write(MUTANT)
        self.env = dict(os.environ, VERIFY_EVIDENCE_DIR=self.ev, RUN_MARKER=self.marker,
                        PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def record(self, tier, tool, cmd, *extra):
        if tier in V.MUTANT_TIERS:
            extra = ("--mutant-patch", self.patch, "--mutant-author", "verifier") + extra
        return subprocess.run(
            [sys.executable, SCRIPT, "record", "--instance", "a", "--feature",
             "notes-create", "--verifier", "v1", "--tier", tier, "--tool", tool,
             "--clauses", "C1", "--cmd", cmd, "--worktree", self.root, *extra],
            capture_output=True, text=True, env=self.env)

    def assertRunnerRefused(self, r, argv0, tool):
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        runners = ", ".join(" ".join(x) for x in V.RUNNERS[tool])
        self.assertIn("verify_evidence: --cmd runs %s, which is not a runner of --tool %s "
                      "(%s)" % (argv0, tool, runners), r.stderr)
        self.assertFalse(os.path.exists(self.marker), "the refused command ran")

    # ── refusals ──────────────────────────────────────────────────────────────
    def test_a_command_that_is_not_the_tools_runner_is_refused(self):
        before = listing(self.ev)
        cases = [("proved", "verus", "true", "true"),
                 ("property", "hypothesis", "uv run true", "uv"),
                 ("property", "hypothesis", "./bin/pytest", "./bin/pytest"),
                 ("property", "proptest", "cargo +nightly test", "cargo"),
                 ("property", "hypothesis", "uv run --with x pytest", "uv"),
                 ("property", "hypothesis", "%s -m pytest" % sys.executable,
                  sys.executable)]
        for tier, tool, cmd, argv0 in cases:
            with self.subTest(cmd=cmd):
                r = self.record(tier, tool, cmd)
                self.assertRunnerRefused(r, argv0, tool)
                self.assertEqual(listing(self.ev), before)
        self.assertEqual(listing(self.root)["mode.txt"],
                         hashlib.sha256(b"ok\n").hexdigest())

    def test_the_runner_check_comes_before_the_other_checks(self):
        # Every later check would fail too; the runner refusal is the one reported.
        before = listing(self.ev)
        r = subprocess.run(
            [sys.executable, SCRIPT, "record", "--instance", "a", "--feature",
             "notes-create", "--verifier", "v1", "--tier", "property", "--tool",
             "hypothesis", "--clauses", "C1,C2", "--timeout", "0", "--cmd", "true",
             "--mutant-patch", os.path.join(self.tmp, "nope.diff"),
             "--worktree", self.root],
            capture_output=True, text=True, env=self.env)
        self.assertRunnerRefused(r, "true", "hypothesis")
        self.assertNotIn("clauses", r.stderr)
        self.assertEqual(listing(self.ev), before)

    def test_the_tier_tool_check_still_comes_first(self):
        r = self.record("proved", "hypothesis", "true")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not allowed for --tier proved", r.stderr)
        self.assertNotIn("not a runner", r.stderr)
        self.assertFalse(os.path.exists(self.marker))

    # ── accepted runners ──────────────────────────────────────────────────────
    def test_runners_through_path_shims_are_recorded(self):
        path = os.path.join(self.ev, "a", "notes-create", self.sha,
                            "proof-property-hypothesis-C1.json")
        for cmd in ("pytest", "python3.12 -m pytest", "uv run pytest",
                    "poetry run pytest"):
            with self.subTest(cmd=cmd):
                r = self.record("property", "hypothesis", cmd)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.strip(), "PROOF: property %s" % path)
                with open(self.marker, encoding="utf-8") as f:
                    runs = f.read().split()
                self.assertEqual(len(runs), 2, "one head run and one mutant run")
                self.assertEqual(runs[0], self.root)
                os.remove(self.marker)
                os.remove(path)

    def test_proved_through_a_shim_is_recorded(self):
        r = self.record("proved", "verus", "verus src/notes.rs")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PROOF: proved", r.stdout)
        self.assertTrue(os.path.exists(self.marker))

    # ── the rule and its tables ───────────────────────────────────────────────
    def test_runner_matches_vectors(self):
        for tool, cmd, want in VECTORS:
            with self.subTest(tool=tool, cmd=cmd):
                self.assertIs(V.runner_matches(tool, cmd.split()), want)
        self.assertFalse(V.runner_matches("hypothesis", []))

    def test_tables_are_the_spec_tables(self):
        self.assertEqual(V.RUNNERS, SPEC_RUNNERS)
        self.assertEqual(V.WRAPPERS, SPEC_WRAPPERS)
        tools = {t for ts in V.TIER_TOOLS.values() for t in ts}
        self.assertEqual(set(V.RUNNERS), tools)

    def test_doc_table_matches_the_code(self):
        with open(DOC, encoding="utf-8") as f:
            section = f.read().split("## Runners", 1)[1].split("\n## ", 1)[0]
        rows = {}
        for line in section.splitlines():
            m = re.match(r"^\| `([a-z-]+)` \| (.*) \|$", line)
            if m:
                rows[m.group(1)] = tuple(tuple(x.split())
                                         for x in re.findall(r"`([^`]+)`", m.group(2)))
        self.assertEqual(rows, V.RUNNERS)
        wrap = re.search(r"^Wrappers: (.*?)\.$", section, re.M | re.S).group(1)
        self.assertEqual(tuple(tuple(x.split()) for x in re.findall(r"`([^`]+)`", wrap)),
                         V.WRAPPERS)

    def test_every_vendored_copy_matches(self):
        r = subprocess.run(["git", "-C", REPO, "ls-files", "*verify_evidence.py"],
                           capture_output=True, text=True, check=True)
        copies = [p for p in r.stdout.split() if os.path.basename(p) ==
                  "verify_evidence.py"]
        self.assertGreater(len(copies), 1)
        with open(SCRIPT, "rb") as f:
            want = f.read()
        for rel in copies:
            with self.subTest(copy=rel):
                with open(os.path.join(REPO, rel), "rb") as f:
                    self.assertEqual(f.read(), want)


if __name__ == "__main__":
    unittest.main()
