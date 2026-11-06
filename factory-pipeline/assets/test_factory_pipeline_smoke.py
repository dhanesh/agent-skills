#!/usr/bin/env python3
"""Shape and status suite for factory-pipeline. Stdlib unittest; no pip, no network,
deterministic. The tick's behaviour is in test_pipeline_tick.py; this suite checks that
the docs say what ships and what is pending.
Run:  python3 test_factory_pipeline_smoke.py
"""
import os
import re
import unittest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(SKILL_DIR, rel), encoding="utf-8") as f:
        return f.read()


class TestShape(unittest.TestCase):
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

    def test_status_is_built_not_skeleton(self):
        for rel in ("SKILL.md", "README.md"):
            text = read(rel).lower()
            self.assertIn("status: built.", text, rel)
            self.assertNotIn("partly built", text, rel)
            self.assertNotIn("pending-r21", text, rel)
            self.assertNotIn("no command ships yet", text, rel)

    def test_tick_ships_and_docs_name_each_requirement(self):
        self.assertTrue(os.path.isfile(os.path.join(SKILL_DIR, "assets", "pipeline.py")))
        text = read("references/overview.md")
        for req in ("R13", "R14", "R15", "R16", "R17", "R18", "R19", "R20", "R21"):
            self.assertIn(req, text, req)


if __name__ == "__main__":
    unittest.main()
