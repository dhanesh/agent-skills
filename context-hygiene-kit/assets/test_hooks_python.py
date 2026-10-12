#!/usr/bin/env python3
"""Hooks must find an interpreter via hooks/find_python.sh, not a hard-coded python3.

Each case runs a hook under a PATH that holds symlinks to only the tools the hook
needs, so the host's python3 never leaks in. Variants: python3 present, only a
`python` shim, and no interpreter at all (quiet exit, status 0).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent / "hooks"
TOOLS = ["bash", "cat", "dirname", "mkdir", "grep", "printf", "env", "sh"]


class HooksPython(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.proj = self.root / "proj"
        self.proj.mkdir()
        (self.proj / ".context").mkdir()
        self.transcript = self.root / "t.jsonl"
        self.transcript.write_text("")

    def bindir(self, name):
        d = self.root / name
        d.mkdir(exist_ok=True)
        for t in TOOLS:
            src = shutil.which(t)
            if src:
                (d / t).symlink_to(src)
        return d

    def path_for(self, kind):
        if (self.root / ("bin_" + kind)).exists():
            return str(self.root / ("bin_" + kind))
        d = self.bindir("bin_" + kind)
        if kind in ("python3", "python"):
            shim = d / kind
            shim.write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
            shim.chmod(0o755)
        return str(d)

    def run_hook(self, name, kind):
        """Run a hook in a fresh project; return (result, sorted files it left)."""
        proj = self.root / ("proj_" + kind + "_" + name)
        (proj / ".context").mkdir(parents=True)
        t = proj / "t.jsonl"
        t.write_text(json.dumps({"type": "user", "message": {"role": "user",
                      "content": "We decided to use sqlite for storage."}}) + "\n")
        env = {"PATH": self.path_for(kind), "CLAUDE_PROJECT_DIR": str(proj),
               "HOME": str(self.root)}
        r = subprocess.run(["bash", str(HOOKS / name)], env=env, capture_output=True,
                           text=True, input=json.dumps({"cwd": str(proj),
                                                        "transcript_path": str(t)}))
        files = sorted(str(f.relative_to(proj)) for f in proj.rglob("*") if f.is_file())
        return r, files

    def test_python_only_matches_python3(self):
        for hook in ("stop.sh", "precompact.sh", "session_start.sh"):
            with self.subTest(hook=hook):
                a, fa = self.run_hook(hook, "python3")
                b, fb = self.run_hook(hook, "python")
                self.assertEqual((a.returncode, b.returncode), (0, 0), b.stderr)
                self.assertEqual(a.stdout, b.stdout)
                self.assertEqual(fa, fb)
                if hook != "stop.sh":
                    self.assertIn(".context/digest.md", fb)
                self.assertNotIn("command not found", b.stderr)

    def test_no_interpreter_is_quiet(self):
        for hook in ("stop.sh", "precompact.sh", "session_start.sh"):
            with self.subTest(hook=hook):
                r, files = self.run_hook(hook, "none")
                self.assertEqual(r.returncode, 0, r.stderr)
                want = "" if hook == "session_start.sh" else '{"continue": true}'
                self.assertEqual(r.stdout.strip(), want)
                self.assertEqual(files, ["t.jsonl"])


    def test_hook_without_find_python_exits_quietly(self):
        for hook in ("stop.sh", "precompact.sh", "session_start.sh"):
            with self.subTest(hook=hook):
                d = self.root / ("lone_" + hook.replace(".", "_")) / "hooks"
                d.mkdir(parents=True)
                shutil.copy(HOOKS / hook, d / hook)
                r = subprocess.run(["bash", str(d / hook)], input="{}",
                                   env={"PATH": self.path_for("python3"),
                                        "HOME": str(self.root)},
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                want = "" if hook == "session_start.sh" else '{"continue": true}'
                self.assertEqual(r.stdout.strip(), want)
                self.assertEqual(r.stderr, "")


INSTALL_TOOLS = ["bash", "sh", "cp", "mkdir", "sed", "grep", "chmod", "dirname", "cat",
                 "printf", "tail", "tr", "jq", "uname", "env", "cut", "head", "awk",
                 "mktemp", "rm", "mv", "ls", "wc", "sort", "date", "xcode-select",
                 "git", "find", "basename", "pwd", "touch", "tee", "diff"]


class InstallerShipsResolver(unittest.TestCase):
    """An installed kit has find_python.sh beside its hooks, and the installer
    itself resolves its interpreter the same way the hooks do."""
    INSTALLER = HOOKS.parent.parent / "scripts" / "install.sh"
    EXTRA = 'hooks'

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for t in INSTALL_TOOLS:
            src = shutil.which(t)
            if src:
                (self.bin / t).symlink_to(src)
        self.target = self.root / "proj"
        self.target.mkdir()
        (self.root / "home").mkdir()

    def install(self, path, **env):
        e = {"PATH": path, "HOME": str(self.root / "home")}
        e.update(env)
        return subprocess.run(["/bin/bash", str(self.INSTALLER), str(self.target)],
                              env=e, capture_output=True, text=True, timeout=120)

    def test_installed_kit_has_find_python_next_to_hooks(self):
        r = self.install(os.environ["PATH"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.target / self.EXTRA / "find_python.sh").is_file())

    def test_installer_runs_with_only_a_python_shim(self):
        shim = self.bin / "python"
        shim.write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
        shim.chmod(0o755)
        r = self.install(str(self.bin))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.target / self.EXTRA / "find_python.sh").is_file())

    def test_installer_rejects_python_older_than_3_10(self):
        shim = self.bin / "python"
        shim.write_text('#!/bin/sh\ncase "$*" in *version_info*) exit 1;; esac\nexit 0\n')
        shim.chmod(0o755)
        r = self.install(str(self.bin))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("needs Python 3.10 or newer", r.stderr)
        self.assertFalse((self.target / self.EXTRA).exists())

    def test_installer_without_python_stops_with_a_message(self):
        r = self.install(str(self.bin))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("needs Python 3.10 or newer", r.stderr)
        self.assertIn("SKILL_PYTHON", r.stderr)
        self.assertFalse((self.target / self.EXTRA).exists())


if __name__ == "__main__":
    unittest.main()
