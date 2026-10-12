#!/usr/bin/env python3
"""Tests for hooks/find_python.sh: the sh twin of skill-contract's resolve_python().

Each case builds a temp bin of fake executables and runs the function under a
PATH that holds only that bin, so the host's Python never leaks in.
"""
import json
import os
import re
import shutil
import subprocess
import sys
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


class HooksPython(unittest.TestCase):
    """Each world-model hook finds its interpreter via find_python, not python3."""
    TOOLS = ["bash", "cat", "dirname", "mkdir", "grep", "printf", "env", "sh"]
    HOOKS = HERE / "hooks"
    NAMES = ["pretooluse.sh", "posttooluse-observe.sh", "stop.sh", "session_start.sh"]

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)

    def path_for(self, kind):
        d = self.root / ("bin_" + kind)
        if d.exists():
            return str(d)
        d.mkdir()
        for t in self.TOOLS:
            src = shutil.which(t)
            if src:
                (d / t).symlink_to(src)
        if kind in ("python3", "python"):
            shim = d / kind
            shim.write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
            shim.chmod(0o755)
        return str(d)

    def run_hook(self, name, kind):
        """Run a hook in a fresh seeded project; return (result, files left, db tables)."""
        proj = self.root / ("proj_" + kind + "_" + name.replace(".", "_"))
        (proj / ".git").mkdir(parents=True)
        (proj / "x.py").write_text("print(1)\n")
        db = proj / ".world-model" / "model.db"
        subprocess.run([sys.executable, str(HERE / "world_model.py"), "--db", str(db),
                        "bootstrap", str(proj)], cwd=proj, capture_output=True)
        # Give precall something to say about x.py (real interpreter, kit CLI).
        subprocess.run([sys.executable, str(HERE / "world_model.py"), "--db", str(db),
                        "observe", "x.py", "reads", "db.py", "--subj-kind", "file",
                        "--obj-kind", "file", "--evidence", "test"],
                       cwd=proj, capture_output=True)
        t = proj / "t.jsonl"
        t.write_text("")
        before = db.stat().st_size if db.exists() else -1
        payload = {"cwd": str(proj), "transcript_path": str(t), "tool_name": "Edit",
                   "tool_input": {"file_path": "x.py"}}
        env = {"PATH": self.path_for(kind), "HOME": str(self.root)}
        r = subprocess.run(["bash", str(self.HOOKS / name)], input=json.dumps(payload),
                           env=env, capture_output=True, text=True)
        files = sorted(str(f.relative_to(proj)) for f in proj.rglob("*")
                       if f.is_file() and ".git" not in f.parts)
        return r, files, (before, db.stat().st_size if db.exists() else -1), str(proj)

    @staticmethod
    def norm(text, proj):
        # The digest carries a second-resolution timestamp; mask it.
        return re.sub(r"\d{4}-\d\d-\d\dT[\d:.]+\+00:00", "T", text.replace(proj, "P"))

    def test_python_only_matches_python3(self):
        for hook in self.NAMES:
            with self.subTest(hook=hook):
                a, fa, sa, pa = self.run_hook(hook, "python3")
                b, fb, sb, pb = self.run_hook(hook, "python")
                self.assertEqual((a.returncode, b.returncode), (0, 0), b.stderr)
                self.assertEqual(self.norm(a.stdout, pa), self.norm(b.stdout, pb))
                self.assertEqual(fa, fb)
                self.assertEqual(sa[1] - sa[0], sb[1] - sb[0])

    def test_effects_are_real_with_python_only(self):
        # Guard against a vacuous pass: the hooks must have done something.
        r, files, _, _ = self.run_hook("session_start.sh", "python")
        self.assertIn(".world-model/digest.md", files)
        self.assertIn("additionalContext", r.stdout)
        a, _, _, pa = self.run_hook("pretooluse.sh", "python3")
        b, _, _, pb = self.run_hook("pretooluse.sh", "python")
        self.assertIn("additionalContext", a.stdout)
        self.assertIn("x.py reads db.py", json.loads(b.stdout)[
            "hookSpecificOutput"]["additionalContext"])
        self.assertEqual(a.stdout.replace(pa, "P"), b.stdout.replace(pb, "P"))

    def test_no_interpreter_is_quiet(self):
        for hook in self.NAMES:
            with self.subTest(hook=hook):
                r, files, _, _ = self.run_hook(hook, "none")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.strip(), '{"continue": true}')
                self.assertNotIn(".world-model/digest.md", files)


if __name__ == "__main__":
    unittest.main()
