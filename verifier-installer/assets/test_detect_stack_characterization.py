#!/usr/bin/env python3
"""Characterisation tests for detect_stack.py: stdlib-only, offline, deterministic.

These tests pin the full output of the detector on three small fixtures: a
Python repo, a Rust repo and a Node repo. Each test checks every stdout line,
the stderr text and the exit code. A change to any line makes a test fail.
When a change to the output is intended, update the pinned lines here in the
same commit.

The fixture files are the same as the `python`, `rust` and `typescript`
stacks that `drive.py fixture stack` writes for the installer-detect feature
(.claude/skills/verify-agent-skills/features/installer-detect.md). The tests
build them in temporary directories.

Run standalone:  python3 verifier-installer/assets/test_detect_stack_characterization.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "detect_stack.py")

PYTHON_FIXTURE = {
    "pyproject.toml": '[project]\nname = "fx"\nversion = "0.1.0"\n',
    "fx/__init__.py": "def add(a, b):\n    return a + b\n",
}
RUST_FIXTURE = {
    "Cargo.toml": '[package]\nname = "fx"\nversion = "0.1.0"\nedition = "2021"\n',
    "src/lib.rs": "pub fn add(a: u32, b: u32) -> u32 { a + b }\n",
}
NODE_FIXTURE = {
    "package.json": '{"name": "fx", "version": "0.1.0"}\n',
    "tsconfig.json": '{"compilerOptions": {"strict": true}}\n',
    "src/index.ts": "export const add = (a: number, b: number) => a + b;\n",
}

# Raw strings: the detector prints JSON with ASCII escapes, so the em dash in
# the CI proposal appears as the six characters \u2014.

PYTHON_STDOUT = r'''{
  "ci_note": null,
  "errors": [],
  "existing_verifiers": {
    "build": null,
    "ci": null,
    "format": null,
    "test": null
  },
  "lockfiles": [],
  "missing": [
    "build",
    "ci",
    "format",
    "test"
  ],
  "proposals": [
    {
      "action": "create",
      "command": "python3 -m compileall -q -f .",
      "exists": false,
      "file": "Makefile",
      "file_to_create": "Makefile",
      "placeholder": false,
      "rail": "build"
    },
    {
      "action": "create",
      "command": "push a branch / open a PR \u2014 the workflow runs the other rails",
      "exists": false,
      "file": ".github/workflows/verify.yml",
      "file_to_create": ".github/workflows/verify.yml",
      "placeholder": false,
      "rail": "ci"
    },
    {
      "action": "create",
      "command": "make format",
      "exists": false,
      "file": "Makefile",
      "file_to_create": "Makefile",
      "placeholder": true,
      "rail": "format"
    },
    {
      "action": "create",
      "command": "python3 -m unittest discover -s tests",
      "exists": false,
      "file": "tests/test_smoke.py",
      "file_to_create": "tests/test_smoke.py",
      "placeholder": false,
      "rail": "test"
    }
  ],
  "stacks": [
    "python"
  ]
}
'''

RUST_STDOUT = r'''{
  "ci_note": null,
  "errors": [],
  "existing_verifiers": {
    "build": "cargo build",
    "ci": null,
    "format": "cargo fmt --check",
    "test": "cargo test"
  },
  "lockfiles": [],
  "missing": [
    "ci"
  ],
  "proposals": [
    {
      "action": "create",
      "command": "push a branch / open a PR \u2014 the workflow runs the other rails",
      "exists": false,
      "file": ".github/workflows/verify.yml",
      "file_to_create": ".github/workflows/verify.yml",
      "placeholder": false,
      "rail": "ci"
    }
  ],
  "stacks": [
    "rust"
  ]
}
'''

NODE_STDOUT = r'''{
  "ci_note": null,
  "errors": [],
  "existing_verifiers": {
    "build": null,
    "ci": null,
    "format": null,
    "test": null
  },
  "lockfiles": [],
  "missing": [
    "build",
    "ci",
    "format",
    "test"
  ],
  "proposals": [
    {
      "action": "extend",
      "command": "npm run build",
      "exists": true,
      "file": "package.json",
      "file_to_create": "package.json",
      "placeholder": false,
      "rail": "build"
    },
    {
      "action": "create",
      "command": "push a branch / open a PR \u2014 the workflow runs the other rails",
      "exists": false,
      "file": ".github/workflows/verify.yml",
      "file_to_create": ".github/workflows/verify.yml",
      "placeholder": false,
      "rail": "ci"
    },
    {
      "action": "extend",
      "command": "npm run format",
      "exists": true,
      "file": "package.json",
      "file_to_create": "package.json",
      "placeholder": false,
      "rail": "format"
    },
    {
      "action": "extend",
      "command": "npm test",
      "exists": true,
      "file": "package.json",
      "file_to_create": "package.json",
      "placeholder": false,
      "rail": "test"
    }
  ],
  "stacks": [
    "node"
  ]
}
'''


def write_fixture(root, files):
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


class TestDetectStackCharacterization(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vinst-char-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def run_detector(self, files):
        write_fixture(self.root, files)
        return subprocess.run(
            [sys.executable, SCRIPT, self.root],
            capture_output=True, text=True, timeout=30)

    def assert_pinned(self, files, expected_stdout):
        r = self.run_detector(files)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")
        # Compare line by line first, so a failure names the changed line.
        self.assertEqual(r.stdout.splitlines(), expected_stdout.splitlines())
        self.assertEqual(r.stdout, expected_stdout)

    def test_python_fixture(self):
        self.assert_pinned(PYTHON_FIXTURE, PYTHON_STDOUT)

    def test_rust_fixture(self):
        self.assert_pinned(RUST_FIXTURE, RUST_STDOUT)

    def test_node_fixture(self):
        self.assert_pinned(NODE_FIXTURE, NODE_STDOUT)

    def test_output_is_repeatable(self):
        first = self.run_detector(PYTHON_FIXTURE)
        second = subprocess.run(
            [sys.executable, SCRIPT, self.root],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(first.returncode, second.returncode)
        self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
