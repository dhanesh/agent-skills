#!/usr/bin/env python3
"""Unit suite for scaffold_skill.py. Stdlib unittest; no pip, no network.

Covers name validation (accept/refuse), skeleton completeness, gate-shaped
structure of the generated SKILL.md, determinism, refusal to overwrite, the
CLI surface, and that the generated smoke test and eval stub actually run
green standalone. Run:  python3 test_scaffold_skill.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scaffold_skill as S  # noqa: E402

SCRIPT = os.path.join(HERE, "scaffold_skill.py")
NAME = "demo-widget-audit"
SNAKE = "demo_widget_audit"


def tree_bytes(root):
    """{relative path: file bytes} for every file under root."""
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = f.read()
    return out


class TestValidateName(unittest.TestCase):
    def test_accepts_valid_kebab_names(self):
        for name in ("a", "x9", "demo-widget-audit", "a2-b", "repo2skill",
                     "a" * 64):
            self.assertIsNone(S.validate_name(name), name)

    def test_refuses_invalid_names(self):
        for name in ("", "Bad_Name", "-leading", "trailing-", "has space",
                     "UPPER", "under_score", "café", "a" * 65):
            self.assertIsNotNone(S.validate_name(name), repr(name))

    def test_build_files_raises_on_invalid_name(self):
        with self.assertRaises(S.ScaffoldError):
            S.build_files("Bad_Name")


class TestScaffoldOutput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="r2s-test-")
        cls.dest = S.scaffold(NAME, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, rel):
        with open(os.path.join(self.dest, rel), encoding="utf-8") as f:
            return f.read()

    def test_creates_directory_named_after_skill(self):
        self.assertEqual(os.path.basename(self.dest), NAME)
        self.assertTrue(os.path.isdir(self.dest))

    def test_all_skeleton_files_exist(self):
        for rel in ("SKILL.md", "README.md",
                    os.path.join("references", "overview.md"),
                    os.path.join("assets", "test_%s_smoke.py" % SNAKE),
                    os.path.join("eval", "run_eval.py")):
            self.assertTrue(os.path.isfile(os.path.join(self.dest, rel)), rel)

    def test_frontmatter_has_standard_fields(self):
        text = self.read("SKILL.md")
        self.assertTrue(text.startswith("---\n"))
        self.assertRegex(text, r"(?m)^name: %s$" % re.escape(NAME))
        self.assertRegex(text, r'(?m)^description: ".+"$')
        self.assertRegex(text, r"(?m)^license: MIT$")
        self.assertRegex(text, r"(?m)^compatibility: \S")
        self.assertRegex(text, r"(?m)^  author: dhanesh$")
        self.assertRegex(text, r'(?m)^  version: "1\.0\.0"$')
        self.assertRegex(text, r'(?m)^  tags: "\S')

    def test_description_within_limit(self):
        text = self.read("SKILL.md")
        m = re.search(r'(?m)^description: "(.*)"$', text)
        self.assertIsNotNone(m)
        self.assertLessEqual(len(m.group(1)), 1024)

    def test_body_is_gate_shaped(self):
        body = self.read("SKILL.md").split("---", 2)[2]
        # PP-1: >= 3 top-level sections; PP-2: >= 3 numbered steps.
        self.assertGreaterEqual(len(re.findall(r"(?m)^## ", body)), 3)
        self.assertGreaterEqual(len(re.findall(r"(?m)^[0-9]+\. ", body)), 3)
        # PP-3 / PP-4 keyword families.
        self.assertRegex(body, r"(?i)\bdeliverable\b")
        self.assertRegex(body, r"(?i)\b(verify|check)\b")
        # PP-5: skeleton ships no absolutist directives.
        self.assertEqual(
            re.findall(r"(?i)\b(never|always|must not)\b", body), [])

    def test_body_references_only_files_that_exist(self):
        body = self.read("SKILL.md").split("---", 2)[2]
        refs = re.findall(r"(?:references|assets|scripts)/[A-Za-z0-9_./-]+", body)
        self.assertTrue(refs)  # the skeleton links its reference stub
        for ref in refs:
            self.assertTrue(os.path.exists(os.path.join(self.dest, ref)), ref)

    def test_todo_markers_present_for_author(self):
        for rel in ("SKILL.md", "README.md", os.path.join("references", "overview.md")):
            self.assertIn("TODO(repo2skill)", self.read(rel), rel)

    def test_readme_has_install_line(self):
        self.assertIn("npx skills add dhanesh/agent-skills --skill %s" % NAME,
                      self.read("README.md"))

    def test_no_top_level_parameters_md(self):
        self.assertFalse(os.path.exists(os.path.join(self.dest, "PARAMETERS.md")))

    def test_refuses_to_overwrite_existing_target(self):
        with self.assertRaises(S.ScaffoldError):
            S.scaffold(NAME, self.tmp)

    def test_deterministic_output(self):
        other = tempfile.mkdtemp(prefix="r2s-det-")
        try:
            dest2 = S.scaffold(NAME, other)
            self.assertEqual(tree_bytes(self.dest), tree_bytes(dest2))
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_generated_smoke_suite_passes(self):
        r = subprocess.run(
            [sys.executable, "test_%s_smoke.py" % SNAKE],
            cwd=os.path.join(self.dest, "assets"),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_generated_eval_stub_passes_and_speaks_protocol(self):
        r = subprocess.run(
            [sys.executable, "run_eval.py"],
            cwd=os.path.join(self.dest, "eval"),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"(?m)^CHECK: .+ (PASS|FAIL)")
        self.assertRegex(r.stdout, r"(?m)^EVAL_RESULT: PASS \(\d+/\d+ checks\)$")


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r2s-cli-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True, text=True, timeout=60)

    def test_valid_name_scaffolds(self):
        r = self.run_cli(NAME, "--dir", self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, NAME, "SKILL.md")))

    def test_invalid_name_refused_and_nothing_written(self):
        r = self.run_cli("Bad_Name", "--dir", self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Bad_Name")))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_overlong_name_refused(self):
        r = self.run_cli("a" * 65, "--dir", self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_existing_target_refused_nonzero(self):
        first = self.run_cli(NAME, "--dir", self.tmp)
        self.assertEqual(first.returncode, 0, first.stderr)
        again = self.run_cli(NAME, "--dir", self.tmp)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("already exists", again.stderr)

    def test_author_flag_flows_into_metadata_and_readme(self):
        r = self.run_cli(NAME, "--dir", self.tmp, "--author", "someone")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.tmp, NAME, "SKILL.md"), encoding="utf-8") as f:
            self.assertIn("author: someone", f.read())


if __name__ == "__main__":
    unittest.main()
