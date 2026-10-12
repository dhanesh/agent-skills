#!/usr/bin/env python3
"""Tests for hooks/find_python.sh: the sh twin of skill-contract's resolve_python().

Each case builds a temp bin of fake executables and runs the function under a
PATH that holds only that bin, so the host's Python never leaks in.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "hooks" / "find_python.sh"
REPO = HERE.parent.parent
COPIES = [
    SCRIPT,
    REPO / "context-hygiene-kit" / "assets" / "hooks" / "find_python.sh",
    REPO / "tmux-agent-herdr-lite" / "scripts" / "find_python.sh",
]


class FindPython(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.bin = Path(self._td.name)

    def fake(self, name, body="exit 0"):
        p = self.bin / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o755)
        return p

    def run_fp(self, **env):
        e = {"PATH": str(self.bin)}
        e.update(env)
        r = subprocess.run(
            ["/bin/sh", "-c", '. "$1"; find_python; rc=$?; printf %s "$PY"; exit $rc',
             "_", str(SCRIPT)],
            env=e, capture_output=True, text=True)
        return r

    def test_only_python(self):
        p = self.fake("python")
        r = self.run_fp()
        self.assertEqual((r.returncode, r.stdout), (0, str(p)))

    def test_python3_beats_python(self):
        p3 = self.fake("python3")
        self.fake("python")
        self.assertEqual(self.run_fp().stdout, str(p3))

    def test_skill_python_wins(self):
        m = self.fake("mypy3")
        self.fake("python3")
        self.assertEqual(self.run_fp(SKILL_PYTHON="mypy3").stdout, str(m))

    def test_skill_python_missing_falls_through(self):
        p3 = self.fake("python3")
        self.assertEqual(self.run_fp(SKILL_PYTHON="missing").stdout, str(p3))

    def _stub_setup(self, uname, xcode_rc):
        marker = self.bin / "ran"
        p3 = self.fake("python3", f"touch '{marker}'")
        self.fake("uname", f"echo {uname}")
        self.fake("xcode-select", f"exit {xcode_rc}")
        py = self.fake("python")
        return p3, py, marker

    def test_stub_skipped_without_running(self):
        p3, py, marker = self._stub_setup("Darwin", 2)
        r = self.run_fp(_FIND_PYTHON_STUB=str(p3))
        self.assertEqual(r.stdout, str(py))
        self.assertFalse(marker.exists())

    def test_stub_with_xcode_tools_is_used(self):
        p3, _, _ = self._stub_setup("Darwin", 0)
        self.assertEqual(self.run_fp(_FIND_PYTHON_STUB=str(p3)).stdout, str(p3))

    def test_stub_path_on_linux_is_used(self):
        p3, _, _ = self._stub_setup("Linux", 2)
        self.assertEqual(self.run_fp(_FIND_PYTHON_STUB=str(p3)).stdout, str(p3))

    def test_nothing_found(self):
        r = self.run_fp()
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_copies_identical(self):
        missing = [c for c in COPIES if not c.exists()]
        if missing:
            self.skipTest("sibling copy absent (installed kit): %s" % missing[0])
        data = {c.read_bytes() for c in COPIES}
        self.assertEqual(len(data), 1, "find_python.sh copies differ")


if __name__ == "__main__":
    unittest.main()
