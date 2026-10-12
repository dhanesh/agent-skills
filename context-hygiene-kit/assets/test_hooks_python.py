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


if __name__ == "__main__":
    unittest.main()
