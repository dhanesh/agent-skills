# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Tests for extract_text.py dispatch and text passthrough — stdlib only."""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "extract_text.py")


def run(path):
    return subprocess.run(
        [sys.executable, SCRIPT, path],
        capture_output=True, text=True,
    )


class ExtractText(unittest.TestCase):
    def test_markdown_passthrough(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("# Title\nGET /ping returns pong\n")
            p = f.name
        try:
            r = run(p)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("GET /ping", r.stdout)
        finally:
            os.unlink(p)

    def test_unknown_extension_errors_cleanly(self):
        with tempfile.NamedTemporaryFile("w", suffix=".xyz", delete=False) as f:
            f.write("data")
            p = f.name
        try:
            r = run(p)
            self.assertEqual(r.returncode, 3)
            self.assertIn("unsupported", r.stderr.lower())
        finally:
            os.unlink(p)


if __name__ == "__main__":
    unittest.main()
