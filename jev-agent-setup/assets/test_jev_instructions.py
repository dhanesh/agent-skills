"""Offline tests for the installed Jev instruction block: the Box row and the images rule. Stdlib only.

The installer writes into a temp home, and each test reads the managed block back from
every agent instruction file, so the tests check what an agent reads, not only the source.
"""
import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import install_jev_setup as inst  # noqa: E402

ENV = {"PATH": "/usr/bin:/bin"}


def table_rows(block: str) -> dict:
    """Map each primitive name in the 'Primitive mapping' table to its row text."""
    section = block.split("## Primitive mapping", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3:
            m = re.match(r"\*\*(\w+)\*\*", cells[1])
            if m:
                rows[m.group(1)] = line
    return rows


def rules(block: str) -> list:
    """Return each numbered rule in the 'Rules' section as one joined string."""
    section = block.split("## Rules", 1)[1].split("\n## ", 1)[0]
    return [" ".join(r.split()) for r in re.split(r"\n(?=\d+\. )", section.strip())]


def images_rule(block: str):
    for r in rules(block):
        if "`jev --images`" in r and "Box" in r and "the user chose" in r:
            return r
    return None


class InstalledBlockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = inst.main(["--home", str(home), "--no-cli"], env=dict(ENV))
        assert code == 0, code
        cls.blocks = {}
        for name, path in inst.target_paths(home, ENV).items():
            _, body, _ = inst.split_managed(path.read_text())
            cls.blocks[name] = body

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_target_has_a_block(self):
        self.assertEqual(set(self.blocks), set(inst.TARGETS))
        for name, body in self.blocks.items():
            self.assertIsNotNone(body, name)

    def test_box_row_in_decision_table(self):
        for name, body in self.blocks.items():
            rows = table_rows(body)
            self.assertIn("Box", rows, name)
            self.assertIn("--images", rows["Box"], name)
            self.assertIn("image", rows["Box"].lower(), name)

    def test_existing_rows_kept(self):
        for name, body in self.blocks.items():
            self.assertEqual(set(table_rows(body)), {"Score", "Choice", "Noul", "Box"}, name)

    def test_images_rule_says_only_with_flag_and_user_chosen_files(self):
        for name, body in self.blocks.items():
            r = images_rule(body)
            self.assertIsNotNone(r, name)
            self.assertIn("MUST go to Jev only with `jev --images`", r)
            self.assertIn("only from files the user chose", r)
            self.assertIn("MUST NOT pick", r)

    def test_rules_keep_their_numbers(self):
        for name, body in self.blocks.items():
            nums = [int(r.split(".", 1)[0]) for r in rules(body)]
            self.assertEqual(nums, list(range(1, 9)), name)


class CheckerNegativeTest(unittest.TestCase):
    """The helpers reject a block that lacks the Box row or the images rule."""

    OLD = (
        "## Primitive mapping\n\n| Decision shape | Primitive | Examples |\n|---|---|---|\n"
        "| Yes / no | **Noul** (probability of yes) | x |\n\n## Rules\n\n"
        "1. One rule.\n2. Images MAY go out with `jev --images`.\n\n## Next\n"
    )

    def test_old_block_has_no_box_row(self):
        self.assertNotIn("Box", table_rows(self.OLD))

    def test_old_block_has_no_images_rule(self):
        self.assertIsNone(images_rule(self.OLD))


if __name__ == "__main__":
    unittest.main()
