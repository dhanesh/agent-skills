#!/usr/bin/env python3
"""Isolation tests for the cockpit scripts (stdlib only; run: python3 test_python_isolation.py).

Each cockpit script runs a `python3 -` heredoc. Without `-I`, Python puts the
working directory first on sys.path, so a json.py, hashlib.py or shlex.py in the
current pane's directory runs in place of the standard module. These tests plant
those three modules in a temporary directory, run the scripts from it, and check
that no planted module ran. A static check also fails any inline Python call in
scripts/ that does not start with -I.

The tests never touch the real tmux server: a stub `tmux` that always fails is
first on PATH, and TMUX is unset.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "scripts"))

# The scripts that run without a tmux server, with the line each one prints.
DRIVEN = {
    "agent-list": "no registered agents",
    "agent-status-scan": "agent-status-scan: tmux unreachable; registry left intact",
    "agent-dashboard": "No state file yet.",
    "agent-status-summary": "agents:-",
}

# An inline call: `python3 -` (stdin), `python3 -c`, or `"$PY" -c`, with no -I first.
INLINE_NO_I = re.compile(r'("\$PY"|\bpython3?)\s+-(c\b|\s|$)')


def inline_scripts():
    names = sorted(n for n in os.listdir(SCRIPTS) if n.startswith("agent-"))
    return [os.path.join(SCRIPTS, n) for n in names + ["install.sh"]]


class StaticCheck(unittest.TestCase):
    def test_every_inline_call_runs_isolated(self):
        bad = []
        for path in inline_scripts():
            with open(path, encoding="utf-8") as fh:
                for no, line in enumerate(fh, 1):
                    if INLINE_NO_I.search(line):
                        bad.append(f"{os.path.basename(path)}:{no}: {line.strip()}")
        self.assertEqual(bad, [], "inline Python call without -I")

    def test_heredocs_are_present(self):
        # Guard the static check: it must see the heredocs it claims to cover.
        found = 0
        for path in inline_scripts():
            with open(path, encoding="utf-8") as fh:
                found += sum(1 for line in fh if re.search(r'\bpython3 -I -( |$)', line))
        self.assertGreaterEqual(found, 8)

    def test_pattern_catches_the_unfixed_forms(self):
        for line in ('python3 - "$STATE" <<\'PY\'', '"$PY" -c \'import sys\'',
                     'python3 -c "print(1)"', 'python -'):
            self.assertTrue(INLINE_NO_I.search(line), line)
        for line in ('python3 -I - "$STATE" <<\'PY\'', '"$PY" -I -c \'import sys\'',
                     'python3 "$SCRIPT_DIR/agent_registry.py" resolve x'):
            self.assertFalse(INLINE_NO_I.search(line), line)


class PlantedModules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tah-iso-")
        self.proj = os.path.join(self.tmp, "proj")
        self.bin = os.path.join(self.tmp, "bin")
        self.root = os.path.join(self.tmp, "agents")
        self.marker = os.path.join(self.tmp, "marker")
        os.makedirs(self.proj)
        os.makedirs(self.bin)
        for mod in ("json", "hashlib", "shlex"):
            with open(os.path.join(self.proj, mod + ".py"), "w", encoding="utf-8") as fh:
                fh.write(f"with open({self.marker!r}, 'a') as _f:\n"
                         f"    _f.write('{mod}\\n')\n")
        stub = os.path.join(self.bin, "tmux")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho 'no server running (stub)' >&2\nexit 1\n")
        os.chmod(stub, 0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, name):
        env = {k: v for k, v in os.environ.items()
               if k != "TMUX" and not k.startswith("PYTHON")}
        env.update(PATH=self.bin + os.pathsep + env.get("PATH", ""),
                   AGENT_TMUX_ROOT=self.root, HOME=self.tmp)
        return subprocess.run(["bash", os.path.join(SCRIPTS, name)], cwd=self.proj,
                              env=env, capture_output=True, text=True, timeout=60)

    def test_control_planted_module_runs_without_isolation(self):
        # Proves the fixture works: plain `python3 -` from proj imports json.py.
        subprocess.run([sys.executable, "-", ], input="import json\n", cwd=self.proj,
                       capture_output=True, text=True, timeout=30,
                       env={k: v for k, v in os.environ.items() if not k.startswith("PYTHON")})
        self.assertTrue(os.path.exists(self.marker))

    def test_cockpit_scripts_import_no_planted_module(self):
        for name, expect in DRIVEN.items():
            with self.subTest(script=name):
                r = self.run_script(name)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(expect, r.stdout + r.stderr)
        marker = ""
        if os.path.exists(self.marker):
            with open(self.marker, encoding="utf-8") as fh:
                marker = fh.read()
        self.assertEqual(marker, "", "a planted module ran")


if __name__ == "__main__":
    unittest.main()
