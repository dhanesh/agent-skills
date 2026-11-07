#!/usr/bin/env python3
"""Inline Python in the hooks and the installer must run with -I.

When Python runs code from `-c` or from stdin (`-`), it puts the working directory
first on sys.path. A planted json.py in the project would then run in place of the
standard module. Each case starts a hook from inside a project that holds a planted
json.py marker module, and checks that no marker appears and that the hook prints
its normal output. A static check lists every inline call that lacks -I.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ASSETS = Path(__file__).resolve().parent
HOOKS = ASSETS / "hooks"
INSTALLER = ASSETS.parent / "scripts" / "install.sh"
INLINE = re.compile(r'"\$PY"\s+(?P<flags>(?:-\S+\s+)*?)-(?:c\b|\s|$)')


class HooksIgnorePlantedModules(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.proj = self.root / "proj"
        self.proj.mkdir()
        self.marker = self.root / "marker"
        (self.proj / "json.py").write_text(
            "with open(%r, 'a') as _f:\n    _f.write('json\\n')\n" % str(self.marker))
        self.transcript = self.proj / "t.jsonl"
        self.transcript.write_text(json.dumps({"type": "user", "message": {
            "role": "user", "content": "We decided to use sqlite for storage."}}) + "\n")
        (self.root / "home").mkdir()

    def run_hook(self, name):
        shim_dir = self.root / "bin"
        shim_dir.mkdir(exist_ok=True)
        shim = shim_dir / "python3"
        if not shim.exists():
            shim.write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
            shim.chmod(0o755)
        env = {"PATH": "%s:/usr/bin:/bin" % shim_dir, "HOME": str(self.root / "home"),
               "CLAUDE_PROJECT_DIR": str(self.proj)}
        payload = json.dumps({"cwd": str(self.proj),
                              "transcript_path": str(self.transcript),
                              "session_id": "s1"})
        return subprocess.run(["bash", str(HOOKS / name)], cwd=str(self.proj), env=env,
                              input=payload, capture_output=True, text=True, timeout=60)

    def test_session_start_ignores_planted_json(self):
        r = self.run_hook("session_start.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertTrue(out["additionalContext"].startswith("# Context Digest"),
                        out["additionalContext"][:80])
        self.assertFalse(self.marker.exists(), "planted json.py was imported")

    def test_stop_ignores_planted_json(self):
        r = self.run_hook("stop.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), '{"continue": true}')
        self.assertFalse(self.marker.exists(), "planted json.py was imported")
        self.assertTrue((self.proj / ".context").is_dir(),
                        "stop.sh stopped before the harvester ran")


class NoInlineCallWithoutIsolation(unittest.TestCase):
    FILES = sorted(HOOKS.glob("*.sh")) + [INSTALLER]

    def test_every_inline_call_has_dash_I(self):
        found, bad = 0, []
        for f in self.FILES:
            for n, line in enumerate(f.read_text().splitlines(), 1):
                for m in INLINE.finditer(line):
                    found += 1
                    if "-I" not in m.group("flags").split():
                        bad.append("%s:%d: %s" % (f.name, n, line.strip()))
        self.assertEqual(bad, [])
        self.assertGreaterEqual(found, 3, "the static check found no inline call")

    def test_pattern_catches_an_unisolated_call(self):
        for line in ('"$PY" -c "import json"', '"$PY" - "$X" <<\'PY\'', '"$PY" -'):
            self.assertIsNotNone(INLINE.search(line), line)
        self.assertIsNone(INLINE.search('"$PY" "$KIT_HOME/harvest.py" --x'))


if __name__ == "__main__":
    unittest.main()
