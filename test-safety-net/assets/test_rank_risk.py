"""Unit suite for rank_risk.py. Stdlib only, offline, deterministic."""
import importlib.util
import os
import pathlib
import subprocess
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


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root, capture_output=True, text=True)


class TestChurn(TempRepo):
    def test_counts_commits_per_file(self):
        if git(self.root, "init", "-q", ".").returncode != 0:
            self.skipTest("git unavailable")
        write(self.root, "hot.py", "def a():\n    pass\n")
        write(self.root, "cold.py", "def b():\n    pass\n")
        git(self.root, "add", "-A"); git(self.root, "commit", "-qm", "1")
        for i in range(3):
            write(self.root, "hot.py", f"def a():\n    return {i}\n")
            git(self.root, "add", "-A"); git(self.root, "commit", "-qm", f"c{i}")
        c = rank_risk.churn(self.root)
        self.assertEqual(c["hot.py"], 4)
        self.assertEqual(c["cold.py"], 1)

    def test_no_git_history_degrades_to_empty_not_a_crash(self):
        # A tarball checkout, or a brand-new directory, must not take the ranker
        # down — churn is one signal of two, and the other still works.
        write(self.root, "a.py", "def a():\n    pass\n")
        self.assertEqual(rank_risk.churn(self.root), {})

    def test_counts_a_filename_containing_a_quote(self):
        # git C-quotes filenames with a quote, backslash, or non-ASCII byte in
        # its default `--name-only` output (e.g. `"quo\"te.py"`); without `-z`
        # that quoted key never matches the plain path iter_py_files produces,
        # and the file's churn silently reads as 0.
        if git(self.root, "init", "-q", ".").returncode != 0:
            self.skipTest("git unavailable")
        rel = 'quo"te.py'
        try:
            write(self.root, rel, "def a():\n    pass\n")
        except OSError:
            self.skipTest("filesystem rejects quote in filename")
        git(self.root, "add", "-A")
        r = git(self.root, "commit", "-qm", "1")
        if r.returncode != 0:
            self.skipTest("git rejects quote in filename")
        c = rank_risk.churn(self.root)
        self.assertEqual(c.get(rel), 1)


class TestInboundRefs(TempRepo):
    def test_counts_references_outside_the_defining_file(self):
        write(self.root, "core.py", "def widely_used():\n    pass\n\n\ndef lonely():\n    pass\n")
        write(self.root, "a.py", "from core import widely_used\nwidely_used()\n")
        write(self.root, "b.py", "import core\ncore.widely_used()\n")
        units = rank_risk.discover_units(self.root)
        refs = rank_risk.inbound_refs(self.root, units)
        self.assertEqual(refs["core.py::widely_used"], 3)   # import + 2 call sites
        self.assertEqual(refs["core.py::lonely"], 0)

    def test_does_not_count_the_definition_itself(self):
        write(self.root, "core.py", "def solo():\n    return solo\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual(rank_risk.inbound_refs(self.root, units)["core.py::solo"], 0)

    def test_matches_whole_identifiers_only(self):
        # `apply` must not be found inside `apply_discount` or `reapply`.
        write(self.root, "core.py", "def apply():\n    pass\n")
        write(self.root, "other.py", "def apply_discount():\n    pass\n\n\ndef reapply():\n    pass\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual(rank_risk.inbound_refs(self.root, units)["core.py::apply"], 0)

    def test_digit_glued_identifier_is_not_a_reference(self):
        # "2x" must not count as a reference to a unit named `x` — the old \bx\b
        # boundary semantics, which the single-pass tokeniser has to preserve.
        write(self.root, "core.py", "def x():\n    pass\n")
        write(self.root, "notes.py", "# scale by 2x and 3x for the 4k display\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual(rank_risk.inbound_refs(self.root, units)["core.py::x"], 0)


class TestTriage(TempRepo):
    def _tier(self, rel, src, name):
        write(self.root, rel, src)
        unit = [u for u in rank_risk.discover_units(self.root) if u["name"] == name][0]
        return rank_risk.triage(self.root, unit)

    def test_pure_function_is_tier_1(self):
        tier, reason = self._tier("a.py", "def add(x, y):\n    return x + y\n", "add")
        self.assertEqual(tier, 1)
        self.assertIn("no I/O", reason)

    def test_filesystem_use_is_tier_2_with_a_named_boundary(self):
        tier, reason = self._tier(
            "b.py", "def load(path):\n    with open(path) as f:\n        return f.read()\n", "load")
        self.assertEqual(tier, 2)
        self.assertIn("filesystem", reason)

    def test_clock_use_is_tier_2(self):
        tier, reason = self._tier(
            "c.py", "import datetime\n\n\ndef stamp():\n    return datetime.datetime.now()\n", "stamp")
        self.assertEqual(tier, 2)
        self.assertIn("clock", reason)

    def test_network_call_is_tier_3(self):
        tier, reason = self._tier(
            "d.py", "import requests\n\n\ndef fetch(u):\n    return requests.get(u).json()\n", "fetch")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_network_in_a_constructor_is_tier_3(self):
        # THE headline negative: a class that dials out when you instantiate it
        # cannot be pinned without a seam, and must never get a test written.
        tier, reason = self._tier(
            "e.py",
            "import socket\n\n\nclass Client:\n    def __init__(self, host):\n"
            "        self.sock = socket.create_connection((host, 80))\n",
            "Client")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_module_level_io_makes_every_unit_in_the_file_tier_4(self):
        # Importing the module does I/O, so nothing in it can be reached at all.
        tier, reason = self._tier(
            "f.py",
            "import requests\n\nCONFIG = requests.get('http://x/cfg').json()\n\n\n"
            "def pure(x):\n    return x\n",
            "pure")
        self.assertEqual(tier, 4)
        self.assertIn("import time", reason)

    def test_module_level_data_naming_a_marker_is_not_import_time_io(self):
        # A module-level allowlist that MENTIONS a driver is data, not behaviour.
        # Reading it as I/O condemned every unit in the file to tier 4 and
        # silently dropped them from the net.
        tier, reason = self._tier(
            "g.py",
            'DRIVERS = {"database": ("psycopg2.", "sqlite3.connect")}\n\n\n'
            "def pure(x):\n    return x\n",
            "pure")
        self.assertEqual(tier, 1)


if __name__ == "__main__":
    unittest.main()
