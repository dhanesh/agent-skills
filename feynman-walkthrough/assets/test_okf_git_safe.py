#!/usr/bin/env python3
"""Stdlib test suite: okf.py runs no repo-controlled git config — offline, deterministic.

A pinned git source controls its own config. If that config sets `core.fsmonitor`
to a command, a plain `git status` runs it. These tests build the hostile fixture
from the knowledge-git-pins feature entry (an embedded bare repo `R/evil` set to
`core.bare false` and `core.worktree ..`), pin it, then set a `core.fsmonitor`
marker command. A `git` shim on PATH logs every git argv that okf.py runs.
`okf status` and `okf diff` must create no marker, and every logged argv must hold
both `core.fsmonitor=false` and `core.hooksPath=/dev/null`.
"""
from __future__ import annotations

import os
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
SAFE = ("core.fsmonitor=false", "core.hooksPath=/dev/null")


@unittest.skipUnless(GIT and SH, "needs git and a POSIX sh")
class GitSafeOnHostileRepo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="okf-git-safe-")
        home = os.path.join(self.dir, "home")
        os.makedirs(home)
        gitconfig = os.path.join(home, "gitconfig")
        open(gitconfig, "w").close()
        # Isolate from the user's own git config so the run is deterministic.
        self.env = dict(os.environ, HOME=home, GIT_CONFIG_GLOBAL=gitconfig,
                        GIT_CONFIG_NOSYSTEM="1")
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
            self.env.pop(key, None)

        self.log = os.path.join(self.dir, "git-argv.log")
        self.marker = os.path.join(self.dir, "fsmonitor-ran")
        bindir = os.path.join(self.dir, "bin")
        os.makedirs(bindir)
        shim = os.path.join(bindir, "git")
        with open(shim, "w") as f:
            f.write('#!/bin/sh\nprintf "%%s " "$@" >> "%s"\necho >> "%s"\n'
                    'exec "%s" "$@"\n' % (self.log, self.log, GIT))
        os.chmod(shim, 0o755)
        self.shim_env = dict(self.env,
                             PATH=bindir + os.pathsep + self.env.get("PATH", ""))

        self.repo = os.path.join(self.dir, "R")
        self.evil = os.path.join(self.repo, "evil")
        self.bundle = os.path.join(self.repo, "knowledge")
        self.git("init", "-q", self.repo)
        self.git("-C", self.repo, "init", "-q", "--bare", "evil")
        self.g("config", "core.bare", "false")
        self.g("config", "core.worktree", "..")
        self.g("--work-tree=.", "read-tree", "--empty", cwd=self.repo)
        self.g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
               "--allow-empty", "-m", "init", cwd=self.repo)
        code, out = self.okf("init", "Evil", "--source", self.evil, shim=False)
        self.assertEqual(code, 0, out)
        # The pin is taken first; only then does the repo turn hostile.
        self.g("config", "core.fsmonitor", "touch '%s'" % self.marker)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args, cwd=None):
        subprocess.run([GIT, *args], cwd=cwd, env=self.env, check=True,
                       capture_output=True)

    def g(self, *args, cwd=None):
        self.git("--git-dir=%s" % self.evil, *args, cwd=cwd)

    def okf(self, *args, shim=True):
        proc = subprocess.run(
            [sys.executable, OKF, "--root", self.bundle, *args],
            env=self.shim_env if shim else self.env, cwd=self.dir,
            capture_output=True, text=True, timeout=60,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def logged_argvs(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [line.strip() for line in f if line.strip()]

    def stray_repo_files(self):
        return sorted(set(os.listdir(self.repo)) - {".git", "evil", "knowledge"})

    def assert_safe_run(self, *args):
        if os.path.exists(self.log):
            os.remove(self.log)
        code, out = self.okf(*args)
        self.assertIn("SOURCE: git", out, out)
        self.assertFalse(os.path.exists(self.marker),
                         "core.fsmonitor command ran on okf %s" % args[0])
        self.assertEqual(self.stray_repo_files(), [],
                         "fsmonitor hook arguments left files in the repo")
        argvs = self.logged_argvs()
        self.assertTrue(argvs, "okf %s ran no git command" % args[0])
        unsafe = [a for a in argvs if not all(s in a.split() for s in SAFE)]
        self.assertEqual(unsafe, [], "git argv without both safe switches")
        return code, out

    def test_status_runs_no_fsmonitor_and_every_git_call_is_safe(self):
        self.assert_safe_run("status", "evil")

    def test_diff_runs_no_fsmonitor_and_every_git_call_is_safe(self):
        self.assert_safe_run("diff", "evil")

    def test_safe_switches_come_before_the_subcommand(self):
        self.assert_safe_run("status", "evil")
        for argv in self.logged_argvs():
            words = argv.split()
            self.assertEqual(words[:4], ["-c", SAFE[0], "-c", SAFE[1]], argv)


@unittest.skipUnless(GIT, "needs git")
class NormalRepoRegression(unittest.TestCase):
    """A normal pin still goes FRESH, then STALE with the changed file named."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="okf-git-normal-")
        home = os.path.join(self.dir, "home")
        os.makedirs(home)
        gitconfig = os.path.join(home, "gitconfig")
        open(gitconfig, "w").close()
        self.env = dict(os.environ, HOME=home, GIT_CONFIG_GLOBAL=gitconfig,
                        GIT_CONFIG_NOSYSTEM="1")
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
        self.assertIn("A\tb.txt", out)
        self.assertIn("OKF_DIFF: normal STALE", out)


class GitSafeConstant(unittest.TestCase):
    def test_constant_holds_both_switches(self):
        self.assertEqual(okf.GIT_SAFE, ("-c", SAFE[0], "-c", SAFE[1]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
