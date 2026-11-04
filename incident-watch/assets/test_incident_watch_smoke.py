#!/usr/bin/env python3
"""Skeleton suite for incident-watch. Stdlib unittest; no pip, no network, deterministic.

No command ships yet, so this suite checks the skeleton's shape and its honest
status line. Grow it into the real unit suite when tooling lands in assets/.
Run:  python3 test_incident_watch_smoke.py
"""
import os
import re
import unittest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(SKILL_DIR, rel), encoding="utf-8") as f:
        return f.read()


class TestSkeleton(unittest.TestCase):
    def test_skill_md_and_readme_exist(self):
        for rel in ("SKILL.md", "README.md"):
            self.assertTrue(os.path.isfile(os.path.join(SKILL_DIR, rel)), rel)

    def test_frontmatter_name_matches_directory(self):
        text = read("SKILL.md")
        m = re.search(r"^name:\s*(\S+)\s*$", text, re.M)
        self.assertIsNotNone(m, "frontmatter has a name: field")
        self.assertEqual(m.group(1), os.path.basename(SKILL_DIR))

    def test_standard_metadata_fields_present(self):
        text = read("SKILL.md")
        for key in ("license:", "compatibility:", "author:", "version:", "tags:"):
            self.assertIn(key, text, key)

    def test_body_keeps_gate_passing_structure(self):
        body = read("SKILL.md").split("---", 2)[2]
        self.assertGreaterEqual(len(re.findall(r"^## ", body, re.M)), 3)
        self.assertGreaterEqual(len(re.findall(r"^[0-9]+\. ", body, re.M)), 3)

    def test_status_line_in_skill_and_readme(self):
        for rel in ("SKILL.md", "README.md"):
            self.assertIn("in development: no command ships yet", read(rel).lower(), rel)

    def test_no_tooling_ships_yet(self):
        shipped = [f for f in os.listdir(os.path.join(SKILL_DIR, "assets"))
                   if not f.startswith("test_") and f != "__pycache__"]
        self.assertEqual(shipped, [])
        self.assertFalse(os.path.exists(os.path.join(SKILL_DIR, "scripts")))


if __name__ == "__main__":
    unittest.main()
