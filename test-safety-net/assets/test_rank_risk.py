"""Unit suite for rank_risk.py. Stdlib only, offline, deterministic."""
import importlib.util
import os
import pathlib
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("rank_risk", os.path.join(HERE, "rank_risk.py"))
rank_risk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rank_risk)


def write(root, rel, text):
    p = pathlib.Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)


class TempRepo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-")


class TestDiscoverUnits(TempRepo):
    def test_finds_module_level_functions_and_classes(self):
        write(self.root, "billing/refund.py",
              "def apply(amount):\n    return amount\n\n\nclass Ledger:\n    pass\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual([u["id"] for u in units],
                         ["billing/refund.py::Ledger", "billing/refund.py::apply"])
        self.assertEqual(units[1]["kind"], "function")
        self.assertEqual(units[0]["kind"], "class")
        self.assertEqual(units[1]["lineno"], 1)

    def test_skips_private_names_nested_defs_test_files_and_vendor(self):
        write(self.root, "app.py",
              "def _helper():\n    pass\n\n\ndef outer():\n    def inner():\n        pass\n    return inner\n")
        write(self.root, "tests/test_app.py", "def test_outer():\n    pass\n")
        write(self.root, "node_modules/pkg/mod.py", "def vendored():\n    pass\n")
        write(self.root, ".venv/lib/mod.py", "def alsovendored():\n    pass\n")
        ids = [u["id"] for u in rank_risk.discover_units(self.root)]
        self.assertEqual(ids, ["app.py::outer"])

    def test_unparseable_file_is_skipped_not_fatal(self):
        write(self.root, "broken.py", "def (((\n")
        write(self.root, "ok.py", "def fine():\n    pass\n")
        ids = [u["id"] for u in rank_risk.discover_units(self.root)]
        self.assertEqual(ids, ["ok.py::fine"])


if __name__ == "__main__":
    unittest.main()
