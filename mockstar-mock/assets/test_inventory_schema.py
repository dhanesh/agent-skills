# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Structural checks for inventory.schema.json — stdlib json only."""
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "..", "references", "inventory.schema.json")


class InventorySchema(unittest.TestCase):
    def setUp(self):
        with open(SCHEMA_PATH) as f:
            self.schema = json.load(f)

    def test_top_level_is_object_with_endpoints(self):
        self.assertEqual(self.schema["type"], "object")
        self.assertIn("endpoints", self.schema["properties"])
        self.assertEqual(self.schema["properties"]["endpoints"]["type"], "array")

    def test_endpoint_required_fields(self):
        rec = self.schema["properties"]["endpoints"]["items"]
        required = set(rec["required"])
        self.assertEqual(
            required,
            {"method", "path", "responses", "provenance", "confidence"},
        )

    def test_confidence_enum(self):
        rec = self.schema["properties"]["endpoints"]["items"]
        self.assertEqual(
            set(rec["properties"]["confidence"]["enum"]),
            {"grounded", "inferred"},
        )

    def test_method_enum_includes_graphql_post(self):
        rec = self.schema["properties"]["endpoints"]["items"]
        methods = set(rec["properties"]["method"]["enum"])
        self.assertTrue({"GET", "POST", "PUT", "PATCH", "DELETE"}.issubset(methods))

    def test_provenance_has_source_and_locator(self):
        rec = self.schema["properties"]["endpoints"]["items"]
        prov = rec["properties"]["provenance"]
        self.assertEqual(set(prov["required"]), {"source", "locator"})


if __name__ == "__main__":
    unittest.main()
