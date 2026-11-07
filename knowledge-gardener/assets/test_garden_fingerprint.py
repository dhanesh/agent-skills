#!/usr/bin/env python3
"""Stdlib test suite: garden.py passes no invalid git pin to git — offline, deterministic.

A git pin lives in the explainer file, which anyone can edit. Its SHA part goes into
`git diff <sha>..HEAD`. Before the fix, a pin such as `--output=<dir>/clobber` acted
as a git option, and git wrote a file named `<dir>/clobber..<sha>`. These tests set
that pin, run `garden.py sweep`, and check that the source is an ERROR, that no
clobber file exists, and that no git argv holds `--output=`. A normal pin still goes
FRESH, then STALE with the changed file named.

Run standalone:  python3 knowledge-gardener/assets/test_garden_fingerprint.py
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
SHA = "a" * 40  # a well-formed 40-hex commit SHA


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


def isolated_env(base):
    """Env with an empty git config so the run is deterministic."""
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
        self.assertTrue(garden.valid_git_pin(SHA))
        self.assertTrue(garden.valid_git_pin(SHA + "+dirty"))

    def test_rejects_options_short_shas_and_other_values(self):
        for bad in ("--output=/tmp/clobber", "-p", SHA[:39], SHA + "0",
                    SHA + "+clean", SHA + "+dirty\n", "HEAD", "main", "",
                    SHA.upper(), "x" * 40, None, 123):
            self.assertFalse(garden.valid_git_pin(bad), repr(bad))

    def test_git_drift_rejects_before_any_git_call(self):
        # A locator that is no repo: a note with no diff proves the check came first.
        detail = garden.git_drift("/nonexistent-garden-test-path",
                                  "--output=x", SHA)
        self.assertEqual(detail["changed_files"], [])
        self.assertIsNone(detail["diffstat"])
        self.assertIn("not a 40-hex commit SHA", detail["note"])


@unittest.skipUnless(GIT and SH, "needs git and a POSIX sh")
class OptionPinIsAnError(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="garden-fingerprint-")
        self.env = isolated_env(self.dir)
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
        write(os.path.join(self.repo, "a.txt"), "one\n")
        self.git("-C", self.repo, "add", "a.txt")
        self.git("-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                 "commit", "-q", "-m", "one")
        # The pin is a git option that, before the fix, wrote a file.
        self.clobber = os.path.join(self.dir, "clobber")
        make_bundle(self.bundle, "normal", self.repo,
                    "--output=%s" % self.clobber)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        subprocess.run([GIT, *args], env=self.env, check=True, capture_output=True)

    def sweep(self):
        proc = subprocess.run(
            [sys.executable, GARDEN, "sweep", self.bundle],
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

    def test_sweep_reports_error_and_writes_no_file(self):
        code, out = self.sweep()
        self.assertEqual(code, 1, out)
        self.assertIn("SOURCE: git %s — ERROR" % self.repo, out)
        self.assertIn("SUBJECT: normal — ERROR", out)
        self.assertIn("not a 40-hex commit SHA", out)
        self.assertNotIn("STALE", out.replace("stale 0", ""))
        self.assertTrue(out.strip().splitlines()[-1]
                        .startswith("GARDEN_RESULT: ERROR ("), out)
        self.assertEqual(self.clobber_files(), [])
        self.assertEqual(self.option_argvs(), [])

    def test_assess_source_returns_error_row(self):
        row = garden.assess_source({"type": "git", "locator": self.repo,
                                    "fingerprint": "--output=%s" % self.clobber})
        self.assertEqual(row["state"], "ERROR")
        self.assertIsNone(row["current_fingerprint"])
        self.assertEqual(self.clobber_files(), [])


@unittest.skipUnless(GIT, "needs git")
class NormalPinRegression(unittest.TestCase):
    """A normal pin still goes FRESH, then STALE with the changed file named."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="garden-fingerprint-normal-")
        self.env = isolated_env(self.dir)
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
        write(os.path.join(self.repo, name), text + "\n")
        self.git("-C", self.repo, "add", name)
        self.git("-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                 "commit", "-q", "-m", text)

    def sweep(self):
        proc = subprocess.run([sys.executable, GARDEN, "sweep", self.bundle],
                              env=self.env, capture_output=True, text=True,
                              timeout=60)
        return proc.returncode, proc.stdout

    def test_fresh_then_stale_with_changed_file(self):
        make_bundle(self.bundle, "normal", self.repo,
                    self.git("-C", self.repo, "rev-parse", "HEAD").strip())
        code, out = self.sweep()
        self.assertEqual(code, 0, out)
        self.assertIn("GARDEN_RESULT: FRESH (fresh 1,", out)
        self.commit("b.txt", "two")
        code, out = self.sweep()
        self.assertEqual(code, 1, out)
        self.assertIn("changed: b.txt", out)
        self.assertIn("GARDEN_RESULT: STALE (", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
