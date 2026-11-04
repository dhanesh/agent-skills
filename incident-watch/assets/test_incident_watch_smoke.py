#!/usr/bin/env python3
"""Shape suite for incident-watch. Stdlib unittest; no pip, no network, deterministic.

Checks the skill's shape and its honest status: `incident probe` and `incident close`
ship (R22 to R26), and no doc still calls the skill partly built. The behaviour is in
test_incident.py.
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
            text = read(rel).lower()
            self.assertIn("status: built", text, rel)
            self.assertNotIn("no command ships yet", text, rel)
            self.assertNotIn("partly built", text, rel)
            self.assertNotIn("not built yet", text, rel)

    def test_every_part_is_named(self):
        text = read("SKILL.md")
        for req in ("R22", "R23", "R24", "R25", "R26"):
            self.assertIn(req, text, req)
        self.assertIn("NEXT: run bug-autopsy with <record path>", text)

    def test_shipped_tooling(self):
        shipped = sorted(f for f in os.listdir(os.path.join(SKILL_DIR, "assets"))
                         if not f.startswith("test_") and f.endswith(".py"))
        self.assertEqual(shipped, ["incident.py", "incident_testkit.py"])
        self.assertIn('"$SKILL_DIR/assets/incident.py"', read("SKILL.md"))

    def test_skill_forbids_rollback_and_deploy(self):
        self.assertIn("You MUST NOT run a rollback, a deploy", read("SKILL.md"))


if __name__ == "__main__":
    unittest.main()
