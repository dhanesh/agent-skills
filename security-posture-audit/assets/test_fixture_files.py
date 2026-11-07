#!/usr/bin/env python3
"""Fixture files: the risky workflow fixtures of security-posture-audit live in labelled
data files under assets/fixtures/, the tests and the eval read them, and no test or eval
source holds a download piped to a shell.

Offline and deterministic. Run standalone:
    python3 security-posture-audit/assets/test_fixture_files.py
"""
import glob
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")

sys.path.insert(0, HERE)
import audit_posture  # noqa: E402

# fixture file -> (the source that reads it, whether the detector flags it)
EXPECTED = {
    "workflow_curl_pipe_sh_step.yml": ("assets/test_audit_posture.py", True),
    "workflow_curl_to_file_step.yml": ("assets/test_audit_posture.py", False),
    "leaky_workflow.yml": ("eval/run_eval.py", True),
}

# The verifier's grep pattern for a download piped to a shell, as a second opinion next
# to the detector's own regex (audit_posture._CURL_PIPE_SH).
GREP_PATTERN = re.compile(r"(curl|wget)[^|]*[|][^|]*\b(ba|z|da|k)?sh\b")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def download_to_shell_lines(text):
    return [n for n, line in enumerate(text.splitlines(), 1)
            if audit_posture._CURL_PIPE_SH.search(line) or GREP_PATTERN.search(line)]


def sources():
    return sorted(glob.glob(os.path.join(HERE, "test_*.py"))
                  + glob.glob(os.path.join(SKILL, "eval", "*.py")))


class FixtureFilesTest(unittest.TestCase):
    def test_each_fixture_exists_with_its_label(self):
        for name in EXPECTED:
            with self.subTest(fixture=name):
                path = os.path.join(FIXTURES, name)
                self.assertTrue(os.path.isfile(path), path)
                head = read(path).splitlines()[:5]
                label = " ".join(ln for ln in head if ln.startswith("#")).lower()
                self.assertIn("detector fixture", label)
                self.assertIn("never run", label)

    def test_fixtures_are_not_workflow_paths(self):
        # An audit of this repo must not report the fixtures as real workflows.
        self.assertNotIn(os.path.join(".github", "workflows"), FIXTURES)

    def test_detector_flags_fixtures_as_expected(self):
        for name, (_, flagged) in EXPECTED.items():
            with self.subTest(fixture=name):
                text = read(os.path.join(FIXTURES, name))
                self.assertEqual(bool(download_to_shell_lines(text)), flagged)
                repo = tempfile.mkdtemp(prefix="spa-fixture-")
                self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
                wf = os.path.join(repo, ".github", "workflows")
                os.makedirs(wf)
                with open(os.path.join(wf, "build.yml"), "w", encoding="utf-8") as fh:
                    fh.write(text)
                hits = [f for f in audit_posture.audit(repo)
                        if f["check"] == "workflow-curl-pipe-sh"]
                if flagged:
                    self.assertEqual(len(hits), 1)
                    self.assertEqual(hits[0]["severity"], "HIGH")
                else:
                    self.assertEqual(hits, [])

    def test_tests_and_eval_read_the_fixtures(self):
        for name, (reader, _) in EXPECTED.items():
            with self.subTest(fixture=name):
                text = read(os.path.join(SKILL, reader))
                self.assertIn('"fixtures"', text)
                self.assertIn('fixture("%s")' % name, text)


class SourceHygieneTest(unittest.TestCase):
    def test_sources_are_found(self):
        names = [os.path.basename(p) for p in sources()]
        for want in ("run_eval.py", "test_audit_posture.py", "test_fixture_files.py"):
            self.assertIn(want, names)

    def test_no_download_to_shell_line_in_sources(self):
        bad = []
        for path in sources():
            for n in download_to_shell_lines(read(path)):
                bad.append("%s:%d" % (os.path.relpath(path, SKILL), n))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
