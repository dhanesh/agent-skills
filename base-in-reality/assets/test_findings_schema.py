# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Structural checks for findings.schema.json — stdlib json only (no jsonschema dep)."""
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class FindingsSchema(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "findings.schema.json")) as f:
            self.schema = json.load(f)

    def test_is_object_schema(self):
        self.assertEqual(self.schema["type"], "object")

    def test_required_fields(self):
        req = set(self.schema["required"])
        self.assertEqual(req, {
            "claim", "layer", "location", "verdict", "severity",
            "citations", "recommended_fix",
        })

    def test_verdict_enum(self):
        self.assertEqual(
            set(self.schema["properties"]["verdict"]["enum"]),
            {"VIOLATION", "DEVIATION", "OUTDATED", "UNCONFIRMED"})

    def test_severity_enum(self):
        self.assertEqual(
            set(self.schema["properties"]["severity"]["enum"]),
            {"critical", "high", "medium", "low"})

    def test_layer_enum(self):
        self.assertEqual(
            set(self.schema["properties"]["layer"]["enum"]),
            {"algo", "arch", "biz"})

    def test_citation_requires_url(self):
        cite = self.schema["properties"]["citations"]["items"]
        self.assertIn("url", cite["required"])

    def test_survivors_require_refutation(self):
        # verdict-rubric.md step 4: VIOLATION/DEVIATION record their votes.
        rules = [r for r in self.schema.get("allOf", [])
                 if "refutation" in r.get("then", {}).get("required", [])]
        self.assertEqual(len(rules), 1, rules)
        self.assertEqual(set(rules[0]["if"]["properties"]["verdict"]["enum"]),
                         {"VIOLATION", "DEVIATION"})
        ref = self.schema["properties"]["refutation"]
        self.assertEqual(set(ref["required"]), {"refuters", "verdicts"})
        self.assertGreaterEqual(ref["properties"]["verdicts"]["minItems"], 3)


if __name__ == "__main__":
    unittest.main()
