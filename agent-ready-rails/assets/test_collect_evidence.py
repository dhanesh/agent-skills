#!/usr/bin/env python3
"""Unit tests for collect_evidence.py (stdlib only; run: python3 test_collect_evidence.py).

Builds tempdir fixture repos and asserts the collector's evidence per rail.
The collector is evidence-only — these tests assert what it FLAGS, not scores.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from collect_evidence import RAILS, collect, main  # noqa: E402


def write(root, rel, content=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path) or path, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def kinds(result, rail):
    return {e["kind"] for e in result[rail]["evidence"]}


class RailedRepo(unittest.TestCase):
    """A repo with all six rails present yields evidence on every rail."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rails-railed-")
        r = cls.tmp
        write(r, "pytest.ini", "[pytest]\n")
        write(r, "tests/test_app.py", "def test_ok():\n    assert True\n")
        write(r, "Makefile", "test:\n\tpytest -q\n\nlint:\n\truff check .\n")
        write(r, "ruff.toml", "line-length = 100\n")
        write(r, ".editorconfig", "root = true\n")
        write(r, "CONTRIBUTING.md", "# style\n")
        write(r, ".github/workflows/ci.yml",
              "name: ci\njobs:\n  t:\n    steps:\n      - run: pytest -q\n")
        write(r, "CLAUDE.md", "# repo guide\n")
        write(r, "README.md", "# app\n")
        write(r, "docs/arch.md", "# arch\n")
        write(r, "src/README.md", "# src map\n")
        write(r, ".claude/settings.json",
              json.dumps({"permissions": {"allow": ["Bash(make test)"], "deny": []}}))
        write(r, ".mcp.json", json.dumps({"mcpServers": {"gh": {"command": "gh-mcp"}}}))
        write(r, ".github/CODEOWNERS", "* @dhanesh\n")
        write(r, ".github/PULL_REQUEST_TEMPLATE.md", "## What\n")
        write(r, ".gitignore", "__pycache__\n*.pyc\ndist\n")
        cls.result = collect(r)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_all_rails_present(self):
        for rail in RAILS:
            self.assertTrue(self.result[rail]["present"], rail)

    def test_verifier_kinds(self):
        self.assertLessEqual({"test-config", "test-dir", "make-target", "lint-config"},
                             kinds(self.result, "R-verifiers"))

    def test_makefile_targets_detected(self):
        mk = [e for e in self.result["R-verifiers"]["evidence"] if e["kind"] == "make-target"]
        self.assertEqual(len(mk), 1)
        self.assertIn("lint", mk[0]["detail"])
        self.assertIn("test", mk[0]["detail"])

    def test_ci_workflow_runs_tests(self):
        self.assertIn("workflow-runs-tests", kinds(self.result, "R-ci"))
        self.assertNotIn("workflow-no-tests", kinds(self.result, "R-ci"))

    def test_house_style_kinds(self):
        self.assertLessEqual({"lint-config", "editorconfig", "style-doc"},
                             kinds(self.result, "R-house-style"))

    def test_context_kinds(self):
        self.assertLessEqual({"agent-context-file", "readme", "docs-dir", "per-dir-readme"},
                             kinds(self.result, "R-context"))

    def test_scoped_tools_parses_permissions(self):
        entries = self.result["R-scoped-tools"]["evidence"]
        settings = [e for e in entries if e["kind"] == "claude-settings"]
        self.assertEqual(len(settings), 1)
        self.assertIn("1 allow", settings[0]["detail"])
        self.assertIn("mcp-config", kinds(self.result, "R-scoped-tools"))

    def test_checkpoint_kinds(self):
        self.assertLessEqual({"codeowners", "pr-template", "gitignore"},
                             kinds(self.result, "R-checkpoints"))

    def test_deterministic(self):
        again = collect(self.tmp)
        self.assertEqual(json.dumps(self.result, sort_keys=True),
                         json.dumps(again, sort_keys=True))


class BareRepo(unittest.TestCase):
    """Code-only repo: every rail is absent (negative fixture)."""

    def test_all_rails_absent(self):
        tmp = tempfile.mkdtemp(prefix="rails-bare-")
        try:
            write(tmp, "app.py", "print('hi')\n")
            write(tmp, "lib/util.py", "x = 1\n")
            result = collect(tmp)
            for rail in RAILS:
                self.assertFalse(result[rail]["present"], rail)
                self.assertEqual(result[rail]["evidence"], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Nuances(unittest.TestCase):
    def test_workflow_without_tests_flagged(self):
        tmp = tempfile.mkdtemp(prefix="rails-noci-")
        try:
            write(tmp, ".github/workflows/deploy.yml",
                  "name: deploy\njobs:\n  d:\n    steps:\n      - run: ./deploy.sh\n")
            result = collect(tmp)
            self.assertTrue(result["R-ci"]["present"])
            self.assertEqual(kinds(result, "R-ci"), {"workflow-no-tests"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_malformed_settings_yields_error_entry_not_crash(self):
        tmp = tempfile.mkdtemp(prefix="rails-badjson-")
        try:
            write(tmp, ".claude/settings.json", "{ not json !!!")
            result = collect(tmp)  # must not raise
            entries = result["R-scoped-tools"]["evidence"]
            self.assertEqual([e["kind"] for e in entries], ["settings-error"])
            self.assertIn("invalid JSON", entries[0]["detail"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_skip_dirs_ignored(self):
        tmp = tempfile.mkdtemp(prefix="rails-skip-")
        try:
            write(tmp, "node_modules/dep/Makefile", "test:\n\ttrue\n")
            write(tmp, ".git/CLAUDE.md", "not real\n")
            result = collect(tmp)
            self.assertFalse(result["R-verifiers"]["present"])
            self.assertFalse(result["R-context"]["present"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Cli(unittest.TestCase):
    def test_cli_emits_json_and_rail_filter(self):
        tmp = tempfile.mkdtemp(prefix="rails-cli-")
        try:
            write(tmp, "README.md", "# x\n")
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "collect_evidence.py"),
                 "--rails", "R-context", tmp],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(sorted(data), ["R-context"])
            self.assertTrue(data["R-context"]["present"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_rejects_missing_dir_and_unknown_rail(self):
        self.assertEqual(main(["/nonexistent-dir-xyz"]), 2)
        tmp = tempfile.mkdtemp(prefix="rails-cli2-")
        try:
            self.assertEqual(main(["--rails", "R-bogus", tmp]), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_help_states_agent_grades(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "collect_evidence.py"), "--help"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("grades", proc.stdout)
        self.assertIn("evidence", proc.stdout)


if __name__ == "__main__":
    unittest.main()
