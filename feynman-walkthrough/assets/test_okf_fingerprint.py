#!/usr/bin/env python3
"""Stdlib test suite: okf.py passes no invalid git pin to git — offline, deterministic.

A git pin lives in the explainer file, which anyone can edit. Its SHA part goes into
`git diff <sha>..HEAD`. Before the fix, a pin such as `--output=<dir>/clobber` acted
as a git option, and git wrote a file named `<dir>/clobber..HEAD`. These tests set
that pin, run `okf status` and `okf diff`, and check that the source is an ERROR, that
no clobber file exists, and that no git argv holds `--output=`. A normal pin still
goes FRESH, then STALE with the changed file named.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import okf

HERE = os.path.dirname(os.path.abspath(__file__))
OKF = os.path.join(HERE, "okf.py")
GIT = shutil.which("git")
SH = shutil.which("sh")
SHA = "a" * 40  # a well-formed 40-hex commit SHA


def _isolated_env(base):
    home = os.path.join(base, "home")
    os.makedirs(home)
    gitconfig = os.path.join(home, "gitconfig")
    open(gitconfig, "w").close()
    env = dict(os.environ, HOME=home, GIT_CONFIG_GLOBAL=gitconfig,
               GIT_CONFIG_NOSYSTEM="1")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
        env.pop(key, None)
    return env


class ValidGitPin(unittest.TestCase):
    def test_accepts_a_sha_and_a_dirty_sha(self):
        self.assertTrue(okf.valid_git_pin(SHA))
        self.assertTrue(okf.valid_git_pin(SHA + "+dirty"))

    def test_rejects_options_short_shas_and_other_values(self):
        for bad in ("--output=/tmp/clobber", "-p", SHA[:39], SHA + "0",
                    SHA + "+clean", SHA + "+dirty\n", "HEAD", "main", "",
                    SHA.upper(), "x" * 40, None, 123):
            self.assertFalse(okf.valid_git_pin(bad), repr(bad))

    def test_git_drift_rejects_before_any_git_call(self):
        # A locator that is no repo: an ERROR proves the check came first.
        state = okf.git_drift("/nonexistent-okf-test-path", "--output=x")
        self.assertEqual(state, ("ERROR", None, [], []))


@unittest.skipUnless(GIT and SH, "needs git and a POSIX sh")
class OptionPinIsAnError(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="okf-fingerprint-")
        self.env = _isolated_env(self.dir)
        self.log = os.path.join(self.dir, "git-argv.log")
        bindir = os.path.join(self.dir, "bin")
        os.makedirs(bindir)
        shim = os.path.join(bindir, "git")
        with open(shim, "w") as f:
            f.write('#!/bin/sh\nprintf "%%s " "$@" >> "%s"\necho >> "%s"\n'
                    'exec "%s" "$@"\n' % (self.log, self.log, GIT))
        os.chmod(shim, 0o755)
        self.shim_env = dict(self.env,
                             PATH=bindir + os.pathsep + self.env.get("PATH", ""))
        self.repo = os.path.join(self.dir, "N")
        self.bundle = os.path.join(self.dir, "nk")
        self.git("init", "-q", self.repo)
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("one\n")
        self.git("-C", self.repo, "add", "a.txt")
        self.git("-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                 "commit", "-q", "-m", "one")
        code, out = self.okf("init", "Normal", "--source", self.repo)
        self.assertEqual(code, 0, out)
        # The pin becomes a git option that, before the fix, wrote a file.
        self.clobber = os.path.join(self.dir, "clobber")
        explainer = os.path.join(self.bundle, "normal", "explainer.md")
        with open(explainer) as f:
            text = f.read()
        text, n = re.subn(r"(?m)^(\s*)fingerprint:.*$",
                          lambda m: "%sfingerprint: --output=%s"
                          % (m.group(1), self.clobber), text)
        self.assertEqual(n, 1)
        with open(explainer, "w") as f:
            f.write(text)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        subprocess.run([GIT, *args], env=self.env, check=True, capture_output=True)

    def okf(self, *args):
        proc = subprocess.run(
            [sys.executable, OKF, "--root", self.bundle, *args],
            env=self.shim_env, cwd=self.dir, capture_output=True, text=True,
            timeout=60,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def clobber_files(self):
        return [n for n in os.listdir(self.dir) if n.startswith("clobber")]

    def option_argvs(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [line for line in f if "--output=" in line]

    def test_status_reports_error_and_writes_no_file(self):
        code, out = self.okf("status", "normal")
        self.assertNotEqual(code, 0, out)
        self.assertIn("SOURCE: git %s -> ERROR" % self.repo, out)
        self.assertIn("OKF_STATUS: normal ERROR", out)
        self.assertEqual(self.clobber_files(), [])
        self.assertEqual(self.option_argvs(), [])

    def test_diff_reports_error_and_writes_no_file(self):
        code, out = self.okf("diff", "normal")
        self.assertNotEqual(code, 0, out)
        self.assertIn("SOURCE: git %s -> ERROR" % self.repo, out)
        self.assertIn("OKF_DIFF: normal ERROR", out)
        self.assertNotIn("PINNED: --output=", out)
        self.assertNotIn("CURRENT:", out)
        self.assertEqual(self.clobber_files(), [])
        self.assertEqual(self.option_argvs(), [])


@unittest.skipUnless(GIT, "needs git")
class NormalPinRegression(unittest.TestCase):
    """A normal pin still goes FRESH, then STALE with the changed file named."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="okf-fingerprint-normal-")
        self.env = _isolated_env(self.dir)
        self.repo = os.path.join(self.dir, "N")
        self.bundle = os.path.join(self.dir, "nk")
        self.git("init", "-q", self.repo)
        self.commit("a.txt", "one")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        subprocess.run([GIT, *args], env=self.env, check=True, capture_output=True)

    def commit(self, name, text):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text + "\n")
        self.git("-C", self.repo, "add", name)
        self.git("-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                 "commit", "-q", "-m", text)

    def okf(self, *args):
        proc = subprocess.run([sys.executable, OKF, "--root", self.bundle, *args],
                              env=self.env, capture_output=True, text=True,
                              timeout=60)
        return proc.returncode, proc.stdout

    def test_fresh_then_stale_with_changed_file(self):
        self.assertEqual(self.okf("init", "Normal", "--source", self.repo)[0], 0)
        code, out = self.okf("status", "normal")
        self.assertEqual(code, 0, out)
        self.assertIn("OKF_STATUS: normal FRESH", out)
        self.commit("b.txt", "two")
        code, out = self.okf("status", "normal")
        self.assertEqual(code, 1, out)
        self.assertIn("OKF_STATUS: normal STALE", out)
        code, out = self.okf("diff", "normal")
        self.assertEqual(code, 0, out)
        shas = re.findall(r"(?m)^  (?:PINNED|CURRENT): ([0-9a-f]{40})$", out)
        self.assertEqual(len(shas), 2, out)
        self.assertNotEqual(shas[0], shas[1])
        self.assertIn("A\tb.txt", out)
        self.assertIn("OKF_DIFF: normal STALE", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
