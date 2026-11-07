#!/usr/bin/env python3
"""Inline Python in the hooks, the installer and the task3 grader must run with -I.

When Python runs code from `-c` or from stdin (`-`), it puts the working directory
first on sys.path. A planted json.py in the project would then run in place of the
standard module. Each hook case starts a hook from inside a git project that holds
planted json.py, hashlib.py and shlex.py marker modules, and checks that no marker
appears and that the hook prints its normal output. The grader case runs
`grade.py task3` from a directory with a probe file, with a planted json.py in
TMPDIR, and checks that the candidate runs isolated in a fresh temporary directory
and that good and bad candidates keep their grades. A static check lists every
inline call that lacks -I.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ASSETS = Path(__file__).resolve().parent
SKILL = ASSETS.parent
HOOKS = ASSETS / "hooks"
INSTALLER = SKILL / "scripts" / "install.sh"
GRADER = SKILL / "eval" / "grade.py"
INLINE = re.compile(r'"\$PY"\s+(?P<flags>(?:-\S+\s+)*?)-(?:c\b|\s|$)')
PLANTED = ("json", "hashlib", "shlex")


def plant(directory, marker, names=PLANTED):
    for name in names:
        (directory / ("%s.py" % name)).write_text(
            "with open(%r, 'a') as _f:\n    _f.write(%r)\n" % (str(marker), name + "\n"))


class Fixture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.marker = self.root / "marker"
        (self.root / "home").mkdir()
        shim_dir = self.root / "bin"
        shim_dir.mkdir()
        shim = shim_dir / "python3"
        shim.write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
        shim.chmod(0o755)
        self.env = {"PATH": "%s:/usr/bin:/bin" % shim_dir,
                    "HOME": str(self.root / "home")}

    def assertNoMarker(self):
        got = self.marker.read_text() if self.marker.exists() else ""
        self.assertEqual(got, "", "a planted module was imported")


class HooksIgnorePlantedModules(Fixture):
    def setUp(self):
        super().setUp()
        self.proj = self.root / "proj"
        self.proj.mkdir()
        subprocess.run(["git", "init", "-q", str(self.proj)], check=True,
                       env=self.env, capture_output=True)
        (self.proj / "a.py").write_text("def f():\n    return 1\n")
        self.transcript = self.proj / "t.jsonl"
        self.transcript.write_text("")
        plant(self.proj, self.marker)
        self.env["CLAUDE_PROJECT_DIR"] = str(self.proj)

    def run_hook(self, name, payload):
        return subprocess.run(["bash", str(HOOKS / name)], cwd=str(self.proj), env=self.env,
                              input=json.dumps(payload), capture_output=True, text=True,
                              timeout=120)

    def session(self):
        return {"cwd": str(self.proj), "transcript_path": str(self.transcript),
                "session_id": "s1"}

    def tool(self):
        return {"cwd": str(self.proj), "tool_name": "Edit",
                "tool_input": {"file_path": "a.py"}}

    def test_session_start(self):
        r = self.run_hook("session_start.sh", self.session())
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["continue"])
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "SessionStart")
        self.assertTrue(spec["additionalContext"].startswith("# world-model digest"),
                        spec["additionalContext"][:80])
        self.assertTrue((self.proj / ".world-model").is_dir())
        self.assertNoMarker()

    def test_stop(self):
        r = self.run_hook("stop.sh", self.session())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), '{"continue": true}')
        self.assertIn("world-model[stop]:", r.stderr, "stop.sh stopped before the harvester")
        self.assertNoMarker()

    def test_pretooluse(self):
        r = self.run_hook("pretooluse.sh", self.tool())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["continue"], True)
        self.assertNoMarker()

    def test_posttooluse_observe(self):
        r = self.run_hook("posttooluse-observe.sh", self.tool())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), '{"continue": true}')
        self.assertIn('{"files": 1', r.stderr, "the observer stopped before it ran")
        self.assertNoMarker()


GOOD = ("import re\n"
        "def slugify(s):\n"
        "    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')\n")
BAD = "def slugify(s):\n    return s\n"
PROBE = ("import json, os, sys\n"
         "print('ISOLATED', sys.flags.isolated, 'CWD_MARKER', os.path.exists('gt-cwd-probe'))\n"
         "open('gt-cand-wrote', 'w').write('x')\n") + GOOD


class GraderRunsCandidateIsolated(Fixture):
    def setUp(self):
        super().setUp()
        self.run_dir = self.root / "gt-run"
        self.tmp = self.root / "gt-tmp"
        self.run_dir.mkdir()
        self.tmp.mkdir()
        (self.run_dir / "gt-cwd-probe").write_text("")
        plant(self.tmp, self.marker, ("json",))
        plant(self.run_dir, self.marker, ("json",))
        self.env["TMPDIR"] = str(self.tmp)

    def grade(self, src):
        cand = self.root / "cand.py"
        cand.write_text(src)
        r = subprocess.run([sys.executable, "-I", str(GRADER), "task3", str(cand)],
                           cwd=str(self.run_dir), env=self.env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_probe_runs_isolated_in_fresh_directory(self):
        got = self.grade(PROBE)
        self.assertEqual(got, {"pass": True, "reason": "ISOLATED 1 CWD_MARKER False\nOK"})
        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()),
                         ["gt-cwd-probe", "json.py"])
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["json.py"],
                         "the grader left its temporary directory behind")
        self.assertNoMarker()

    def test_grades_do_not_change(self):
        self.assertEqual(self.grade(GOOD), {"pass": True, "reason": "OK"})
        bad = self.grade(BAD)
        self.assertFalse(bad["pass"])
        self.assertTrue(bad["reason"].startswith("MISMATCH 'Hello, World!'"), bad["reason"])
        self.assertNoMarker()

    def test_grader_does_not_claim_a_sandbox(self):
        self.assertNotIn("sandbox", GRADER.read_text().lower())


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
        self.assertGreaterEqual(found, 8, "the static check missed inline calls")

    def test_pattern_catches_an_unisolated_call(self):
        for line in ('"$PY" -c "import json"', '"$PY" - "$X" <<\'PY\'', '"$PY" -'):
            self.assertIsNotNone(INLINE.search(line), line)
        self.assertIsNone(INLINE.search('"$PY" "$KIT_HOME/world_model.py" --db x'))


if __name__ == "__main__":
    unittest.main()
