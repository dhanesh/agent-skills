#!/usr/bin/env python3
"""Stdlib test suite: garden.py runs no repo-controlled git config — offline, deterministic.

A pinned git source controls its own config. If that config sets `core.fsmonitor`
to a command, a plain `git status` runs it. These tests build the hostile fixture
from the knowledge-git-pins feature entry (an embedded bare repo `R/evil` set to
`core.bare false` and `core.worktree ..`), pin an older commit of it in a bundle
inside `R`, then set a `core.fsmonitor` marker command. A `git` shim on PATH logs
every git argv that garden.py runs. `garden.py sweep` must create no marker, and
every logged argv must hold both `core.fsmonitor=false` and
`core.hooksPath=/dev/null`. The older pin makes the sweep take the STALE path, so
the `git diff` calls of the drift detail are checked too.

Run standalone:  python3 knowledge-gardener/assets/test_garden_git_safe.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import garden  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GARDEN = os.path.join(HERE, "garden.py")
GIT = shutil.which("git")
SH = shutil.which("sh")
SAFE = ("core.fsmonitor=false", "core.hooksPath=/dev/null")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_bundle(root, slug, locator, fingerprint):
    """A spec-conformant OKF bundle with one subject and one git source pin."""
    write(os.path.join(root, "index.md"),
          '---\nokf_version: "0.1"\n---\n\n# Subjects\n\n'
          "* [%s](%s/) - reference explainer\n" % (slug, slug))
    write(os.path.join(root, "log.md"),
          "# Directory Update Log\n\n## 2026-01-01\n* **Creation**: seeded.\n")
    write(os.path.join(root, slug, "explainer.md"),
          "---\ntype: Explainer\ntitle: %s\ntimestamp: 2026-01-01T00:00:00Z\n"
          "sources:\n- type: git\n  locator: %s\n  fingerprint: %s\n"
          "  pinned: 2026-01-01\n---\n\n# %s\n\nBody.\n"
          % (slug, locator, fingerprint, slug))
    write(os.path.join(root, slug, "index.md"),
          "# %s\n\n* [Explainer](explainer.md)\n" % slug)


def isolated_env(home):
    """Env with an empty git config so the run is deterministic."""
    os.makedirs(home)
    gitconfig = os.path.join(home, "gitconfig")
    open(gitconfig, "w").close()
    env = dict(os.environ, HOME=home, GIT_CONFIG_GLOBAL=gitconfig,
               GIT_CONFIG_NOSYSTEM="1")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
        env.pop(key, None)
    return env


@unittest.skipUnless(GIT and SH, "needs git and a POSIX sh")
class GitSafeOnHostileRepo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="garden-git-safe-")
        self.env = isolated_env(os.path.join(self.dir, "home"))

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
        self.commit("init")
        pinned = self.head()
        self.commit("two")
        make_bundle(self.bundle, "evil", self.evil, pinned)
        # The pin is taken first; only then does the repo turn hostile.
        self.g("config", "core.fsmonitor", "touch '%s'" % self.marker)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args, cwd=None):
        return subprocess.run([GIT, *args], cwd=cwd, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def g(self, *args, cwd=None):
        return self.git("--git-dir=%s" % self.evil, *args, cwd=cwd)

    def commit(self, msg):
        self.g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
               "--allow-empty", "-m", msg, cwd=self.repo)

    def head(self):
        return self.g("rev-parse", "HEAD").strip()

    def sweep(self):
        proc = subprocess.run(
            [sys.executable, GARDEN, "sweep", self.bundle],
            env=self.shim_env, cwd=self.dir,
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

    def run_sweep(self):
        if os.path.exists(self.log):
            os.remove(self.log)
        code, out = self.sweep()
        self.assertIn("SOURCE: git %s" % self.evil, out, out)
        return code, out

    def test_sweep_runs_no_fsmonitor(self):
        self.run_sweep()
        self.assertFalse(os.path.exists(self.marker),
                         "core.fsmonitor command ran on garden.py sweep")
        self.assertEqual(self.stray_repo_files(), [],
                         "fsmonitor hook arguments left files in the repo")

    def test_every_sweep_git_call_carries_both_switches(self):
        self.run_sweep()
        argvs = self.logged_argvs()
        self.assertTrue(argvs, "garden.py sweep ran no git command")
        unsafe = [a for a in argvs if not all(s in a.split() for s in SAFE)]
        self.assertEqual(unsafe, [], "git argv without both safe switches")

    def test_stale_drift_diff_calls_are_safe_too(self):
        code, out = self.run_sweep()
        self.assertEqual(code, 1, out)
        self.assertIn("GARDEN_RESULT: STALE", out)
        diffs = [a for a in self.logged_argvs() if " diff " in " %s " % a]
        self.assertTrue(diffs, "the STALE path ran no git diff")
        for argv in diffs:
            self.assertTrue(all(s in argv.split() for s in SAFE), argv)

    def test_safe_switches_come_before_the_subcommand(self):
        self.run_sweep()
        for argv in self.logged_argvs():
            words = argv.split()
            self.assertEqual(words[:4], ["-c", SAFE[0], "-c", SAFE[1]], argv)


@unittest.skipUnless(GIT, "needs git")
class NormalRepoRegression(unittest.TestCase):
    """A normal pin still goes FRESH, then STALE with the changed file named."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="garden-git-normal-")
        self.env = isolated_env(os.path.join(self.dir, "home"))
        self.repo = os.path.join(self.dir, "N")
        self.bundle = os.path.join(self.dir, "nk")
        self.git("init", "-q", self.repo)
        self.commit("a.txt", "one")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        return subprocess.run([GIT, *args], env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def commit(self, name, text):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text + "\n")
        self.git("-C", self.repo, "add", name)
        self.git("-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                 "commit", "-q", "-m", text)

    def sweep(self):
        proc = subprocess.run([sys.executable, GARDEN, "sweep", self.bundle],
                              env=self.env, capture_output=True, text=True,
                              timeout=60)
        return proc.returncode, proc.stdout

    def test_fresh_then_stale_with_changed_file(self):
        head = self.git("-C", self.repo, "rev-parse", "HEAD").strip()
        make_bundle(self.bundle, "normal", self.repo, head)
        code, out = self.sweep()
        self.assertEqual(code, 0, out)
        self.assertIn("GARDEN_RESULT: FRESH (fresh 1,", out)
        self.commit("b.txt", "two")
        code, out = self.sweep()
        self.assertEqual(code, 1, out)
        self.assertIn("changed: b.txt", out)
        self.assertIn("GARDEN_RESULT: STALE", out)


class GitSafeConstant(unittest.TestCase):
    def test_constant_holds_both_switches(self):
        self.assertEqual(garden.GIT_SAFE, ("-c", SAFE[0], "-c", SAFE[1]))

    def test_unavailable_git_reports_the_safe_argv(self):
        saved = os.environ.get("PATH")
        os.environ["PATH"] = ""
        try:
            proc = garden._git(["rev-parse", "HEAD"], cwd=os.getcwd())
        finally:
            if saved is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = saved
        self.assertEqual(proc.returncode, 127)
        self.assertEqual(proc.args[:5], ["git", "-c", SAFE[0], "-c", SAFE[1]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
