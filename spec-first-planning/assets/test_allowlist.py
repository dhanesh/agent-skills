#!/usr/bin/env python3
"""Unit tests for allowlist.py — stdlib-only, offline, deterministic.

Run standalone:  python3 spec-first-planning/assets/test_allowlist.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import allowlist  # noqa: E402

_SCRIPT = os.path.join(HERE, "allowlist.py")
_REPO = os.path.dirname(os.path.dirname(HERE))
_COMMITTED = os.path.join(_REPO, ".factory", "check-commands.txt")


class LintTest(unittest.TestCase):
    def test_make_test_passes(self):
        self.assertEqual(allowlist.lint_pattern("make test"), [])

    def test_interpreter_path_fails(self):
        self.assertTrue(allowlist.lint_pattern("/usr/bin/python3 x.py"))

    def test_shell_dash_c_fails(self):
        self.assertTrue(allowlist.lint_pattern("sh -c make"))

    def test_bare_interpreter_fails(self):
        self.assertTrue(allowlist.lint_pattern("python3 x.py"))
        self.assertTrue(allowlist.lint_pattern("node x.js"))

    def test_shell_with_wildcard_fails(self):
        self.assertTrue(allowlist.lint_pattern("bash *"))
        self.assertTrue(allowlist.lint_pattern("bash scripts/x.sh *"))

    def test_shell_running_fixed_script_passes(self):
        self.assertEqual(allowlist.lint_pattern("bash scripts/gates/test_gates.sh"), [])

    def test_launcher_fails(self):
        self.assertTrue(allowlist.lint_pattern("env make test"))
        self.assertTrue(allowlist.lint_pattern("xargs make"))

    def test_bad_literal_tokens_fail(self):
        self.assertTrue(allowlist.lint_pattern("make $(id)"))
        self.assertTrue(allowlist.lint_pattern("make ../x"))
        self.assertTrue(allowlist.lint_pattern("make /etc/passwd"))
        self.assertTrue(allowlist.lint_pattern("* test"))
        self.assertTrue(allowlist.lint_pattern("make {python}"))

    def test_python_placeholder_passes(self):
        self.assertEqual(allowlist.lint_pattern("{python} *"), [])

    def test_comments_and_blanks_skipped(self):
        patterns, problems = allowlist.lint_text("# c\n\n   # d\nmake test\n")
        self.assertEqual(patterns, ["make test"])
        self.assertEqual(problems, [])

    def test_problem_carries_line_number(self):
        _, problems = allowlist.lint_text("make test\n\nsh -c make\n")
        self.assertEqual([n for n, _ in problems], [3])

    def test_committed_allowlist_passes(self):
        patterns, problems = allowlist.load(_COMMITTED)
        self.assertEqual(problems, [])
        self.assertIn("make gate-skill *", patterns)


class MatchTest(unittest.TestCase):
    def test_make_variable_injection_does_not_match(self):
        self.assertFalse(allowlist.arg_ok("SKILL=$(shell id)"))
        self.assertFalse(allowlist.matches("make gate-skill *",
                                           "make gate-skill SKILL=$(shell id)"))

    def test_python_module_flag_does_not_match(self):
        self.assertFalse(allowlist.arg_ok("-mhttp.server"))
        self.assertFalse(allowlist.matches("{python} *", "{python} -mhttp.server"))

    def test_good_arguments_match(self):
        self.assertTrue(allowlist.matches("make gate-skill *",
                                          "make gate-skill SKILL=spec-first-planning"))
        self.assertTrue(allowlist.matches(
            "{python} *", "{python} spec-first-planning/assets/test_allowlist.py"))

    def test_dotdot_and_token_count(self):
        self.assertFalse(allowlist.matches("{python} *", "{python} ../x.py"))
        self.assertFalse(allowlist.matches("{python} *", "{python} a.py b"))
        self.assertFalse(allowlist.matches("make test", "make test; id"))
        self.assertFalse(allowlist.matches("make test", "make"))

    def test_match_command_returns_first_hit(self):
        pats = ["make test", "{python} *"]
        self.assertEqual(allowlist.match_command(pats, "{python} x.py"), "{python} *")
        self.assertIsNone(allowlist.match_command(pats, "make eval"))


class CliTest(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, _SCRIPT, *args],
                              capture_output=True, text=True)

    def test_lint_committed_file(self):
        r = self._run("lint", _COMMITTED)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALLOWLIST-LINT: pass", r.stdout.splitlines()[-1])

    def test_lint_bad_file_and_match(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.txt")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("make test\n/usr/bin/python3 x.py\nsh -c make\n")
            r = self._run("lint", bad)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stdout.count("ALLOWLIST-LINT: fail"), 2)
            self.assertEqual(self._run("match", bad, "make test").returncode, 1)
        r = self._run("match", _COMMITTED, "make gate-skill SKILL=clean-code")
        self.assertEqual(r.returncode, 0)
        self.assertIn("MATCH: make gate-skill *", r.stdout)
        r = self._run("match", _COMMITTED, "{python} -mhttp.server")
        self.assertEqual(r.returncode, 1)

    def test_usage_and_missing_file(self):
        self.assertEqual(self._run().returncode, 2)
        self.assertEqual(self._run("lint", "/nonexistent/x.txt").returncode, 2)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    if result.testsRun == 0:
        sys.stderr.write("no tests ran\n")
        sys.exit(1)
    sys.exit(0 if result.wasSuccessful() else 1)
