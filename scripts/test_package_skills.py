#!/usr/bin/env python3
"""Unit suite for scripts/package-skills.py — stdlib only, offline, deterministic.

Run standalone: `python3 scripts/test_package_skills.py`, or via `make test-tools`
(`make gate` runs it too).
"""

import importlib.util
import json
import os
import shutil
import stat
import tempfile
import unittest
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location("package_skills", os.path.join(_HERE, "package-skills.py"))
pkg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pkg)


SKILL_MD = """---
name: {name}
description: >-
  A demo skill used by the packager tests. Second sentence that should not
  reach the release-notes table.
license: MIT
compatibility: Requires python3.
metadata:
  author: dhanesh
  version: "{version}"
  tags: "demo,test"
---

# Demo

Body text.
"""


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if mode is not None:
        os.chmod(path, mode)


class TempRepo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.out = os.path.join(self.root, "dist")
        self.addCleanup(shutil.rmtree, self.root, True)

    def make_skill(self, name, version="1.0.0"):
        skill_dir = os.path.join(self.root, name)
        write(os.path.join(skill_dir, "SKILL.md"), SKILL_MD.format(name=name, version=version))
        write(os.path.join(skill_dir, "README.md"), "# %s\n" % name)
        write(os.path.join(skill_dir, "references", "detail.md"), "detail\n")
        write(os.path.join(skill_dir, "scripts", "install.sh"), "#!/bin/sh\nexit 0\n", mode=0o755)
        write(os.path.join(skill_dir, "eval", "run_eval.py"), "print('ok')\n")
        return skill_dir


class TestFrontmatter(unittest.TestCase):
    def test_parses_scalars_folded_blocks_and_nested_metadata(self):
        front = pkg.parse_frontmatter(SKILL_MD.format(name="demo-skill", version="2.1.0"))
        self.assertEqual(front["name"], "demo-skill")
        self.assertEqual(front["license"], "MIT")
        self.assertIn("A demo skill used by the packager tests.", front["description"])
        # A folded scalar collapses to a single line.
        self.assertNotIn("\n", front["description"])
        self.assertEqual(front["metadata"]["version"], "2.1.0")
        self.assertEqual(front["metadata"]["author"], "dhanesh")

    def test_rejects_missing_and_unclosed_frontmatter(self):
        with self.assertRaises(ValueError):
            pkg.parse_frontmatter("# No frontmatter\n")
        with self.assertRaises(ValueError):
            pkg.parse_frontmatter("---\nname: x\nstill going\n")


class TestCollectFiles(TempRepo):
    def test_excludes_build_artifacts(self):
        skill_dir = self.make_skill("demo-skill")
        write(os.path.join(skill_dir, "assets", "__pycache__", "x.cpython-311.pyc"), "junk\n")
        write(os.path.join(skill_dir, "assets", "tool.pyc"), "junk\n")
        write(os.path.join(skill_dir, ".DS_Store"), "junk\n")

        files = pkg.collect_files(skill_dir)
        self.assertIn("SKILL.md", files)
        self.assertIn(os.path.join("references", "detail.md"), files)
        self.assertFalse([f for f in files if "__pycache__" in f or f.endswith(".pyc") or ".DS_Store" in f])
        self.assertEqual(files, sorted(files))


class TestPackage(TempRepo):
    def test_one_archive_per_skill_rooted_at_the_skill_folder(self):
        self.make_skill("alpha-skill")
        self.make_skill("beta-skill", version="2.0.0")

        manifest = pkg.package(self.root, self.out)

        self.assertEqual([e["name"] for e in manifest["skills"]], ["alpha-skill", "beta-skill"])
        for name in ("alpha-skill", "beta-skill"):
            archive = os.path.join(self.out, "%s.zip" % name)
            self.assertTrue(os.path.isfile(archive))
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
            # Exactly one top-level entry, and SKILL.md sits directly inside it.
            self.assertEqual({n.split("/")[0] for n in names}, {name})
            self.assertIn("%s/SKILL.md" % name, names)
            self.assertIn("%s/references/detail.md" % name, names)
            self.assertIn("%s/eval/run_eval.py" % name, names)

    def test_preserves_the_executable_bit_on_shipped_scripts(self):
        self.make_skill("alpha-skill")
        pkg.package(self.root, self.out)

        with zipfile.ZipFile(os.path.join(self.out, "alpha-skill.zip")) as zf:
            modes = {i.filename: (i.external_attr >> 16) for i in zf.infolist()}
        self.assertTrue(modes["alpha-skill/scripts/install.sh"] & stat.S_IXUSR)
        self.assertFalse(modes["alpha-skill/SKILL.md"] & stat.S_IXUSR)

    def test_archives_are_byte_identical_across_runs(self):
        # The release workflow skips publishing when SHA256SUMS is unchanged, so
        # a nondeterministic archive would mint a release on every merge.
        self.make_skill("alpha-skill")
        pkg.package(self.root, self.out)
        with open(os.path.join(self.out, "alpha-skill.zip"), "rb") as fh:
            first = fh.read()
        first_sums = read(os.path.join(self.out, "SHA256SUMS"))

        os.utime(os.path.join(self.root, "alpha-skill", "SKILL.md"), (0, 0))
        pkg.package(self.root, self.out)
        with open(os.path.join(self.out, "alpha-skill.zip"), "rb") as fh:
            second = fh.read()

        self.assertEqual(first, second)
        self.assertEqual(first_sums, read(os.path.join(self.out, "SHA256SUMS")))

    def test_changed_content_changes_the_checksum(self):
        skill_dir = self.make_skill("alpha-skill")
        pkg.package(self.root, self.out)
        before = read(os.path.join(self.out, "SHA256SUMS"))

        write(os.path.join(skill_dir, "references", "detail.md"), "detail, revised\n")
        pkg.package(self.root, self.out)

        self.assertNotEqual(before, read(os.path.join(self.out, "SHA256SUMS")))

    def test_sidecars_describe_the_archives(self):
        self.make_skill("alpha-skill", version="1.2.3")
        pkg.package(self.root, self.out, revision="abc1234")

        manifest = json.loads(read(os.path.join(self.out, "manifest.json")))
        entry = manifest["skills"][0]
        self.assertEqual(manifest["revision"], "abc1234")
        self.assertEqual(entry["version"], "1.2.3")
        self.assertEqual(entry["license"], "MIT")
        self.assertEqual(entry["archive"], "alpha-skill.zip")
        self.assertEqual(entry["sha256"], pkg.sha256_of(os.path.join(self.out, "alpha-skill.zip")))

        sums = read(os.path.join(self.out, "SHA256SUMS"))
        self.assertEqual(sums, "%s  alpha-skill.zip\n" % entry["sha256"])

        notes = read(os.path.join(self.out, "RELEASE_NOTES.md"))
        self.assertIn("`alpha-skill`", notes)
        self.assertIn("1.2.3", notes)
        self.assertIn("abc1234", notes)
        self.assertIn("Upload", notes)

    def test_only_the_named_skill_is_packaged(self):
        self.make_skill("alpha-skill")
        self.make_skill("beta-skill")

        manifest = pkg.package(self.root, self.out, only=["beta-skill"])

        self.assertEqual([e["name"] for e in manifest["skills"]], ["beta-skill"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "alpha-skill.zip")))

    def test_stale_archives_do_not_survive_a_rebuild(self):
        self.make_skill("alpha-skill")
        self.make_skill("beta-skill")
        pkg.package(self.root, self.out)
        shutil.rmtree(os.path.join(self.root, "beta-skill"))

        pkg.package(self.root, self.out)

        self.assertFalse(os.path.exists(os.path.join(self.out, "beta-skill.zip")))


class TestNegativeFixtures(TempRepo):
    def test_directory_without_skill_md_is_not_a_skill(self):
        self.make_skill("alpha-skill")
        write(os.path.join(self.root, "docs", "notes.md"), "not a skill\n")

        self.assertEqual(pkg.discover_skills(self.root), ["alpha-skill"])

    def test_name_directory_mismatch_fails_loudly(self):
        skill_dir = self.make_skill("alpha-skill")
        write(os.path.join(skill_dir, "SKILL.md"), SKILL_MD.format(name="renamed-skill", version="1.0.0"))

        with self.assertRaises(ValueError) as ctx:
            pkg.package(self.root, self.out)
        self.assertIn("must match the directory name", str(ctx.exception))

    def test_unknown_skill_name_fails_loudly(self):
        self.make_skill("alpha-skill")
        with self.assertRaises(ValueError):
            pkg.package(self.root, self.out, only=["nope"])

    def test_empty_repo_fails_rather_than_publishing_nothing(self):
        with self.assertRaises(ValueError):
            pkg.package(self.root, self.out)

    def test_broken_frontmatter_fails_the_build(self):
        skill_dir = self.make_skill("alpha-skill")
        write(os.path.join(skill_dir, "SKILL.md"), "# no frontmatter at all\n")
        with self.assertRaises(ValueError):
            pkg.package(self.root, self.out)


class TestReleaseNotes(unittest.TestCase):
    def test_table_cells_stay_on_one_row(self):
        entries = [
            {
                "name": "alpha-skill",
                "version": "1.0.0",
                "bytes": 2048,
                "description": "Does a thing | with a pipe. And a second sentence that should be dropped.",
            }
        ]
        notes = pkg.render_release_notes(entries)
        row = [ln for ln in notes.splitlines() if ln.startswith("| `alpha-skill`")][0]
        self.assertIn("\\|", row)  # a literal pipe cannot break the table
        self.assertNotIn("second sentence", row)
        self.assertIn("2 KB", row)

    def test_an_abbreviation_is_not_mistaken_for_a_sentence_end(self):
        entries = [
            {
                "name": "alpha-skill",
                "version": "1.0.0",
                "bytes": 1024,
                "description": "Audits a repository for agent readiness, e.g. runnable verifiers. Dropped.",
            }
        ]
        row = [ln for ln in pkg.render_release_notes(entries).splitlines() if ln.startswith("| `alpha")][0]
        self.assertIn("runnable verifiers", row)
        self.assertNotIn("Dropped", row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
