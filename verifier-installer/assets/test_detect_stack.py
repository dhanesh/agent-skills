#!/usr/bin/env python3
"""Unit tests for detect_stack.py — stdlib-only, offline, deterministic.

Run standalone:  cd verifier-installer/assets && python3 test_detect_stack.py
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

import detect_stack  # noqa: E402

SCRIPT = os.path.join(HERE, "detect_stack.py")


def write(root, rel, content=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class TempRepoCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vinst-test-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def plan(self):
        return detect_stack.detect(self.root)


class TestPythonDetection(TempRepoCase):
    def test_pytest_repo(self):
        write(self.root, "pyproject.toml",
              "[project]\nname = \"x\"\n[tool.pytest.ini_options]\n")
        write(self.root, "tests/test_x.py", "def test_ok():\n    assert True\n")
        plan = self.plan()
        self.assertEqual(plan["stacks"], ["python"])
        self.assertEqual(plan["existing_verifiers"]["test"], "python3 -m pytest")
        self.assertIn("ci", plan["missing"])

    def test_unittest_repo(self):
        write(self.root, "setup.py", "from setuptools import setup\nsetup()\n")
        write(self.root, "tests/test_a.py", "import unittest\n")
        plan = self.plan()
        self.assertEqual(plan["stacks"], ["python"])
        self.assertEqual(plan["existing_verifiers"]["test"],
                         "python3 -m unittest discover")

    def test_requirements_only_no_tests(self):
        write(self.root, "requirements.txt", "flask\n")
        plan = self.plan()
        self.assertEqual(plan["stacks"], ["python"])
        self.assertIsNone(plan["existing_verifiers"]["test"])
        self.assertIn("test", plan["missing"])
        test_props = [p for p in plan["proposals"] if p["rail"] == "test"]
        self.assertEqual(test_props[0]["file_to_create"], "tests/test_smoke.py")


class TestNodeDetection(TempRepoCase):
    def test_full_scripts_and_lockfile(self):
        write(self.root, "package.json", json.dumps({
            "name": "x",
            "scripts": {"test": "node t.js", "build": "node b.js",
                        "format": "prettier -c ."},
        }))
        write(self.root, "package-lock.json", "{}")
        plan = self.plan()
        self.assertEqual(plan["stacks"], ["node"])
        ev = plan["existing_verifiers"]
        self.assertEqual(ev["test"], "npm test")
        self.assertEqual(ev["build"], "npm run build")
        self.assertEqual(ev["format"], "npm run format")
        self.assertEqual(plan["lockfiles"], ["package-lock.json"])
        self.assertEqual(plan["missing"], ["ci"])

    def test_placeholder_test_script_is_not_a_verifier(self):
        write(self.root, "package.json", json.dumps({
            "scripts": {"test": "echo \"Error: no test specified\" && exit 1"},
        }))
        plan = self.plan()
        self.assertIsNone(plan["existing_verifiers"]["test"])
        self.assertIn("test", plan["missing"])

    def test_lint_script_counts_as_format_rail(self):
        write(self.root, "package.json", json.dumps({
            "scripts": {"lint": "eslint ."},
        }))
        plan = self.plan()
        self.assertEqual(plan["existing_verifiers"]["format"], "npm run lint")

    def test_malformed_package_json_is_reported_not_fatal(self):
        write(self.root, "package.json", "{\"scripts\": {broken")
        plan = self.plan()  # must not raise
        self.assertIn("node", plan["stacks"])
        self.assertEqual(len(plan["errors"]), 1)
        self.assertEqual(plan["errors"][0]["file"], "package.json")
        self.assertIsNone(plan["existing_verifiers"]["test"])

    def test_nested_package_json_is_not_the_root_stack(self):
        write(self.root, "sub/package.json", "{}")
        self.assertNotIn("node", self.plan()["stacks"])

    def test_node_modules_is_skipped(self):
        write(self.root, "package.json", "{}")
        write(self.root, "node_modules/dep/go.mod", "module dep\n")
        self.assertNotIn("go", self.plan()["stacks"])


class TestOtherStacks(TempRepoCase):
    def test_go_module_is_toolchain_complete(self):
        write(self.root, "go.mod", "module example.com/x\n")
        plan = self.plan()
        self.assertEqual(plan["stacks"], ["go"])
        ev = plan["existing_verifiers"]
        self.assertEqual(ev["build"], "go build ./...")
        self.assertEqual(ev["test"], "go test ./...")
        self.assertEqual(ev["format"], "gofmt -l .")
        self.assertEqual(plan["missing"], ["ci"])

    def test_rust_crate_is_toolchain_complete(self):
        write(self.root, "Cargo.toml", "[package]\nname = \"x\"\n")
        plan = self.plan()
        self.assertEqual(plan["stacks"], ["rust"])
        self.assertEqual(plan["existing_verifiers"]["test"], "cargo test")
        self.assertEqual(plan["missing"], ["ci"])

    def test_makefile_targets(self):
        write(self.root, "Makefile",
              "VAR := 1\n\nbuild:\n\ttrue\n\ntest: build\n\ttrue\n\nfmt:\n\ttrue\n")
        plan = self.plan()
        self.assertEqual(plan["stacks"], ["make"])
        ev = plan["existing_verifiers"]
        self.assertEqual(ev["build"], "make build")
        self.assertEqual(ev["test"], "make test")
        self.assertEqual(ev["format"], "make fmt")

    def test_make_beats_lower_priority_stacks(self):
        write(self.root, "Makefile", "test:\n\ttrue\n")
        write(self.root, "go.mod", "module x\n")
        plan = self.plan()
        self.assertEqual(plan["existing_verifiers"]["test"], "make test")
        # go still fills the rails make lacks
        self.assertEqual(plan["existing_verifiers"]["build"], "go build ./...")


class TestBareRepoAndCI(TempRepoCase):
    def test_bare_repo_all_missing(self):
        plan = self.plan()
        self.assertEqual(plan["stacks"], [])
        self.assertEqual(plan["missing"], ["build", "ci", "format", "test"])
        for rail in ("build", "ci", "format", "test"):
            self.assertIsNone(plan["existing_verifiers"][rail])
        rails = sorted(p["rail"] for p in plan["proposals"])
        self.assertEqual(rails, ["build", "ci", "format", "test"])

    def test_existing_workflow_detected_and_not_duplicated(self):
        write(self.root, "go.mod", "module x\n")
        write(self.root, ".github/workflows/ci.yml",
              "on: push\njobs:\n  t:\n    steps:\n      - run: go test ./...\n")
        plan = self.plan()
        self.assertEqual(plan["existing_verifiers"]["ci"],
                         ".github/workflows/ci.yml")
        self.assertEqual(plan["missing"], [])
        self.assertEqual(plan["proposals"], [])

    def test_workflow_that_verifies_nothing_is_not_the_ci_rail(self):
        # A stale-bot / dependabot / labeler workflow is extremely common in
        # exactly the neglected repos this skill targets. Crediting the first
        # .yml in sorted order made the agent report CI installed and move on,
        # leaving the repo with an auto-labeler and no verify job.
        write(self.root, "go.mod", "module x\n")
        write(self.root, ".github/workflows/stale.yml",
              "on:\n  schedule:\n    - cron: '0 0 * * *'\n"
              "jobs:\n  stale:\n    steps:\n      - uses: actions/stale@v9\n")
        plan = self.plan()
        self.assertIsNone(plan["existing_verifiers"]["ci"])
        self.assertIn("ci", plan["missing"])
        self.assertIn("stale.yml", plan["ci_note"])
        self.assertIn("extend one", plan["ci_note"])

    def test_proposal_says_extend_when_the_file_already_exists(self):
        # The plan is what the agent acts on; it must carry the fact that a file
        # is already there, rather than leaving prose as the only thing between
        # the agent and a clobbered Makefile.
        write(self.root, "Makefile", "help:\n\t@echo hi\n")
        write(self.root, "requirements.txt", "requests\n")
        plan = self.plan()
        by_file = {}
        for pr in plan["proposals"]:
            by_file.setdefault(pr["file"], set()).add(pr["action"])
        self.assertIn("Makefile", by_file)
        self.assertEqual(by_file["Makefile"], {"extend"})
        for pr in plan["proposals"]:
            self.assertEqual(pr["exists"], pr["action"] == "extend")
            self.assertEqual(pr["file"], pr["file_to_create"])   # deprecated alias holds

    def test_ci_proposal_targets_verify_workflow(self):
        plan = self.plan()
        ci = [p for p in plan["proposals"] if p["rail"] == "ci"][0]
        self.assertEqual(ci["file_to_create"], ".github/workflows/verify.yml")


class TestDeterminism(TempRepoCase):
    def _mixed_repo(self):
        write(self.root, "package.json", json.dumps({"scripts": {"build": "b"}}))
        write(self.root, "pyproject.toml", "[project]\nname='x'\n")
        write(self.root, "Makefile", "test:\n\ttrue\n")
        write(self.root, "yarn.lock", "")

    def test_two_runs_are_byte_identical(self):
        self._mixed_repo()
        a = json.dumps(detect_stack.detect(self.root), sort_keys=True)
        b = json.dumps(detect_stack.detect(self.root), sort_keys=True)
        self.assertEqual(a, b)

    def test_all_lists_sorted(self):
        self._mixed_repo()
        plan = self.plan()
        self.assertEqual(plan["stacks"], sorted(plan["stacks"]))
        self.assertEqual(plan["missing"], sorted(plan["missing"]))
        self.assertEqual(plan["lockfiles"], sorted(plan["lockfiles"]))
        rails = [p["rail"] for p in plan["proposals"]]
        self.assertEqual(rails, sorted(rails))


class TestCLI(TempRepoCase):
    def test_cli_emits_valid_json(self):
        write(self.root, "go.mod", "module x\n")
        r = subprocess.run([sys.executable, SCRIPT, self.root],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(r.stdout)
        self.assertEqual(plan["stacks"], ["go"])

    def test_cli_rejects_missing_dir(self):
        r = subprocess.run(
            [sys.executable, SCRIPT, os.path.join(self.root, "nope")],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)

    def test_cli_usage(self):
        r = subprocess.run([sys.executable, SCRIPT],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
