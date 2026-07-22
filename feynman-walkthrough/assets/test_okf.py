#!/usr/bin/env python3
"""Stdlib test suite for okf.py — offline, deterministic.

Covers the OKF v0.1 conformance surface (frontmatter with a non-empty `type`
on every concept, reserved index.md/log.md shapes, okf_version in the root
index) and the source-pinning drift detection built on top of it.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import shutil
import subprocess
import tempfile
import unittest

import okf

TODAY = dt.date(2026, 7, 11)
GIT = shutil.which("git")


def run_cli(*argv):
    out = io.StringIO()
    code = okf.main(list(argv), out=out)
    return code, out.getvalue()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class SlugifyTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(okf.slugify("Ingest Pipeline"), "ingest-pipeline")

    def test_punctuation_collapses(self):
        self.assertEqual(okf.slugify("TCP/IP: how it works!"), "tcp-ip-how-it-works")

    def test_never_empty(self):
        self.assertEqual(okf.slugify("!!!"), "subject")

    def test_max_64_and_no_trailing_dash(self):
        slug = okf.slugify("a b" * 40)
        self.assertLessEqual(len(slug), 64)
        self.assertFalse(slug.endswith("-"))


class FrontmatterTests(unittest.TestCase):
    def test_roundtrip_scalars_lists_and_source_blocks(self):
        meta = {
            "type": "Explainer",
            "title": "My Topic",
            "tags": ["walkthrough", "reference"],
            "custom_key": "kept as-is",
            "sources": [
                {"type": "git", "locator": "/repo", "fingerprint": "abc",
                 "pinned": "2026-07-11"},
                {"type": "external", "locator": "https://example.test",
                 "fingerprint": None, "pinned": "2026-07-11"},
            ],
        }
        body = "# My Topic\n\ncontent\n"
        text = okf.format_frontmatter(meta) + "\n" + body
        parsed, parsed_body = okf.parse_frontmatter(text)
        self.assertEqual(parsed, meta)
        self.assertEqual(parsed_body, body)

    def test_no_frontmatter_returns_text_as_body(self):
        meta, body = okf.parse_frontmatter("# Just markdown\n")
        self.assertEqual(meta, {})
        self.assertEqual(body, "# Just markdown\n")

    def test_quoted_scalar_and_null(self):
        meta, _ = okf.parse_frontmatter(
            '---\nokf_version: "0.1"\nfp: null\n---\n')
        self.assertEqual(meta["okf_version"], "0.1")
        self.assertIsNone(meta["fp"])

    def test_empty_inline_list(self):
        meta, _ = okf.parse_frontmatter("---\ntags: []\n---\n")
        self.assertEqual(meta["tags"], [])


class InitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "knowledge")

    def test_creates_okf_layout(self):
        subject_dir = okf.init_okf(self.root, "Ingest Pipeline", [], today=TODAY)
        self.assertEqual(os.path.basename(subject_dir), "ingest-pipeline")
        for name in (okf.EXPLAINER, okf.FAQ, okf.INDEX):
            self.assertTrue(os.path.isfile(os.path.join(subject_dir, name)), name)
        for name in (okf.INDEX, okf.LOG):
            self.assertTrue(os.path.isfile(os.path.join(self.root, name)), name)

    def test_concepts_conform_every_frontmatter_has_type(self):
        subject_dir = okf.init_okf(self.root, "Raft", [], today=TODAY)
        for name, expected in ((okf.EXPLAINER, "Explainer"), (okf.FAQ, "FAQ")):
            meta, body = okf.read_concept(os.path.join(subject_dir, name))
            self.assertEqual(meta["type"], expected)
            self.assertTrue(body.strip())
        self.assertIn("Raft", read(os.path.join(subject_dir, okf.EXPLAINER)))

    def test_root_index_declares_okf_version_and_links_subject(self):
        okf.init_okf(self.root, "Raft Consensus", [], today=TODAY)
        content = read(os.path.join(self.root, okf.INDEX))
        self.assertIn('okf_version: "%s"' % okf.OKF_SPEC_VERSION, content)
        self.assertIn("(raft-consensus/)", content)

    def test_subject_index_is_reserved_no_frontmatter(self):
        subject_dir = okf.init_okf(self.root, "Raft", [], today=TODAY)
        content = read(os.path.join(subject_dir, okf.INDEX))
        self.assertFalse(content.startswith("---"))
        self.assertIn("(explainer.md)", content)
        self.assertIn("(faq.md)", content)

    def test_log_records_creation_newest_first(self):
        okf.init_okf(self.root, "A", [], today=TODAY)
        okf.init_okf(self.root, "B", [], today=dt.date(2026, 7, 12))
        content = read(os.path.join(self.root, okf.LOG))
        self.assertTrue(content.startswith("# Directory Update Log"))
        self.assertLess(content.index("## 2026-07-12"),
                        content.index("## 2026-07-11"))
        self.assertIn("**Creation**", content)

    def test_same_day_entries_share_one_heading(self):
        okf.init_okf(self.root, "A", [], today=TODAY)
        okf.init_okf(self.root, "B", [], today=TODAY)
        content = read(os.path.join(self.root, okf.LOG))
        self.assertEqual(content.count("## 2026-07-11"), 1)

    def test_second_subject_appends_to_root_index_once(self):
        okf.init_okf(self.root, "A", [], today=TODAY)
        okf.init_okf(self.root, "B", [], today=TODAY)
        content = read(os.path.join(self.root, okf.INDEX))
        self.assertEqual(content.count("(a/)"), 1)
        self.assertEqual(content.count("(b/)"), 1)
        self.assertEqual(content.count("okf_version"), 1)

    def test_refuses_double_init(self):
        okf.init_okf(self.root, "Topic", [], today=TODAY)
        with self.assertRaises(FileExistsError):
            okf.init_okf(self.root, "Topic", [], today=TODAY)

    def test_file_source_pinned_with_resource(self):
        src = os.path.join(self.tmp, "paper.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("abstract")
        subject_dir = okf.init_okf(self.root, "Paper", [src], today=TODAY)
        meta, _ = okf.read_concept(os.path.join(subject_dir, okf.EXPLAINER))
        (source,) = meta["sources"]
        self.assertEqual(source["type"], "file")
        self.assertEqual(source["locator"], os.path.abspath(src))
        self.assertEqual(len(source["fingerprint"]), 64)
        self.assertEqual(meta["resource"], os.path.abspath(src))
        self.assertEqual(meta["created"], "2026-07-11")
        self.assertEqual(meta["timestamp"], "2026-07-11T00:00:00Z")

    def test_external_source_has_null_fingerprint(self):
        subject_dir = okf.init_okf(
            self.root, "Spec", ["https://example.test/spec"], today=TODAY)
        meta, _ = okf.read_concept(os.path.join(subject_dir, okf.EXPLAINER))
        (source,) = meta["sources"]
        self.assertEqual(source["type"], "external")
        self.assertIsNone(source["fingerprint"])


class StatusAndPinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "knowledge")

    def _write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_file_source_fresh_then_stale_then_repinned(self):
        src = os.path.join(self.tmp, "doc.md")
        self._write(src, "v1")
        subject_dir = okf.init_okf(self.root, "Doc", [src], today=TODAY)
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")

        self._write(src, "v2")
        self.assertEqual(okf.status_okf(subject_dir)[0], "STALE")

        okf.pin_okf(self.root, subject_dir, today=dt.date(2026, 7, 12))
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")
        meta, _ = okf.read_concept(os.path.join(subject_dir, okf.EXPLAINER))
        self.assertEqual(meta["timestamp"], "2026-07-12T00:00:00Z")
        self.assertEqual(meta["created"], "2026-07-11")
        self.assertIn("**Update**", read(os.path.join(self.root, okf.LOG)))

    def test_pin_preserves_explainer_body_and_extra_keys(self):
        src = os.path.join(self.tmp, "doc.md")
        self._write(src, "v1")
        subject_dir = okf.init_okf(self.root, "Doc", [src], today=TODAY)
        path = os.path.join(subject_dir, okf.EXPLAINER)
        meta, _ = okf.read_concept(path)
        meta["owner"] = "dhanesh"
        okf.write_concept(path, meta, "# Doc\n\nreal explainer text\n")

        okf.pin_okf(self.root, subject_dir, today=TODAY)
        meta, body = okf.read_concept(path)
        self.assertEqual(meta["owner"], "dhanesh")
        self.assertIn("real explainer text", body)

    def test_dir_source_detects_added_file(self):
        src = os.path.join(self.tmp, "notes")
        os.makedirs(src)
        self._write(os.path.join(src, "a.txt"), "alpha")
        subject_dir = okf.init_okf(self.root, "Notes", [src], today=TODAY)
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")

        self._write(os.path.join(src, "b.txt"), "beta")
        self.assertEqual(okf.status_okf(subject_dir)[0], "STALE")

    def test_external_source_is_unknown(self):
        subject_dir = okf.init_okf(
            self.root, "Spec", ["https://example.test/spec"], today=TODAY)
        overall, rows = okf.status_okf(subject_dir)
        self.assertEqual(overall, "UNKNOWN")
        self.assertEqual(rows[0][1], "UNKNOWN")

    def test_stale_wins_over_unknown(self):
        src = os.path.join(self.tmp, "doc.md")
        self._write(src, "v1")
        subject_dir = okf.init_okf(
            self.root, "Mixed", [src, "https://example.test"], today=TODAY)
        self._write(src, "v2")
        self.assertEqual(okf.status_okf(subject_dir)[0], "STALE")

    def test_no_sources_is_fresh(self):
        subject_dir = okf.init_okf(self.root, "Bare", [], today=TODAY)
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")

    def test_moved_source_is_unknown(self):
        src = os.path.join(self.tmp, "doc.md")
        self._write(src, "v1")
        subject_dir = okf.init_okf(self.root, "Doc", [src], today=TODAY)
        os.remove(src)
        self.assertEqual(okf.status_okf(subject_dir)[0], "UNKNOWN")


@unittest.skipUnless(GIT, "git not available")
class GitSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "knowledge")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.test")
        self._git("config", "user.name", "Test")
        self._commit("a.txt", "v1", "first")

    def _git(self, *args):
        subprocess.run(["git", "-C", self.repo] + list(args),
                       check=True, capture_output=True)

    def _commit(self, name, content, message):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as f:
            f.write(content)
        self._git("add", ".")
        self._git("commit", "-q", "-m", message)

    def test_detects_git_type(self):
        self.assertEqual(okf.detect_source_type(self.repo), "git")

    def test_fresh_then_stale_on_new_commit(self):
        subject_dir = okf.init_okf(self.root, "Repo", [self.repo], today=TODAY)
        meta, _ = okf.read_concept(os.path.join(subject_dir, okf.EXPLAINER))
        self.assertEqual(meta["sources"][0]["type"], "git")
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")

        self._commit("a.txt", "v2", "second")
        self.assertEqual(okf.status_okf(subject_dir)[0], "STALE")

        okf.pin_okf(self.root, subject_dir, today=TODAY)
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")

    def test_dirty_worktree_is_stale(self):
        subject_dir = okf.init_okf(self.root, "Repo", [self.repo], today=TODAY)
        with open(os.path.join(self.repo, "a.txt"), "w", encoding="utf-8") as f:
            f.write("uncommitted")
        self.assertEqual(okf.status_okf(subject_dir)[0], "STALE")

    def test_bundle_outside_repo_not_excluded_by_name(self):
        # A repo dir that merely shares the bundle root's basename must still
        # count as drift: containment is by resolved path, not by name.
        subject_dir = okf.init_okf(self.root, "Repo", [self.repo], today=TODAY)
        os.makedirs(os.path.join(self.repo, os.path.basename(self.root)))
        self._commit(os.path.join(os.path.basename(self.root), "note.md"),
                     "inside a dir named like the bundle root", "lookalike")
        self.assertEqual(okf.status_okf(subject_dir)[0], "STALE")


@unittest.skipUnless(GIT, "git not available")
class SelfPinTests(unittest.TestCase):
    """Bundle root inside the pinned repo: bundle changes are not drift."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.test")
        self._git("config", "user.name", "Test")
        self._commit("src.py", "v1", "first")
        # bundle root lives INSIDE the repo it pins (given as a relative-ish
        # nested path; containment must resolve real paths)
        self.root = os.path.join(self.repo, "docs", "knowledge")

    def _git(self, *args):
        subprocess.run(["git", "-C", self.repo] + list(args),
                       check=True, capture_output=True)

    def _commit(self, name, content, message):
        path = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(path) or self.repo, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self._git("add", ".")
        self._git("commit", "-q", "-m", message)

    def test_pin_ignores_bundle_only_dirt(self):
        subject_dir = okf.init_okf(self.root, "Repo", [self.repo], today=TODAY)
        meta, _ = okf.read_concept(os.path.join(subject_dir, okf.EXPLAINER))
        self.assertNotIn("+dirty", meta["sources"][0]["fingerprint"])
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")

    def test_committing_the_bundle_stays_fresh(self):
        subject_dir = okf.init_okf(self.root, "Repo", [self.repo], today=TODAY)
        self._git("add", ".")
        self._git("commit", "-q", "-m", "add knowledge bundle")
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")
        # editing bundle files afterwards (FAQ append) is also not drift
        with open(os.path.join(subject_dir, okf.FAQ), "a",
                  encoding="utf-8") as f:
            f.write("\nQ: extra?\n")
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "faq update")
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")

    def test_real_source_change_is_still_stale(self):
        subject_dir = okf.init_okf(self.root, "Repo", [self.repo], today=TODAY)
        self._git("add", ".")
        self._git("commit", "-q", "-m", "add knowledge bundle")
        self._commit("src.py", "v2", "source change")
        self.assertEqual(okf.status_okf(subject_dir)[0], "STALE")
        okf.pin_okf(self.root, subject_dir, today=TODAY)
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")

    def test_dirty_source_outside_bundle_is_stale(self):
        subject_dir = okf.init_okf(self.root, "Repo", [self.repo], today=TODAY)
        with open(os.path.join(self.repo, "src.py"), "w",
                  encoding="utf-8") as f:
            f.write("uncommitted")
        self.assertEqual(okf.status_okf(subject_dir)[0], "STALE")

    def test_dirty_pin_compares_by_sha_part(self):
        # Pin taken while a source file was dirty records "<sha>+dirty";
        # once the worktree is clean again on the same sha, that pin is FRESH.
        subject_dir = okf.init_okf(self.root, "Repo", [self.repo], today=TODAY)
        with open(os.path.join(self.repo, "src.py"), "w",
                  encoding="utf-8") as f:
            f.write("wip")
        okf.pin_okf(self.root, subject_dir, today=TODAY)
        meta, _ = okf.read_concept(os.path.join(subject_dir, okf.EXPLAINER))
        self.assertTrue(meta["sources"][0]["fingerprint"].endswith("+dirty"))
        self._git("checkout", "--", "src.py")  # drop the dirt: same sha again
        self.assertEqual(okf.status_okf(subject_dir)[0], "FRESH")

    def test_git_drift_reports_changed_files_outside_bundle(self):
        subject_dir = okf.init_okf(self.root, "Repo", [self.repo], today=TODAY)
        self._git("add", ".")
        self._git("commit", "-q", "-m", "add knowledge bundle")
        pinned = okf.read_concept(
            os.path.join(subject_dir, okf.EXPLAINER))[0]["sources"][0]
        self._commit("src.py", "v2", "source change")
        state, current, committed, dirty = okf.git_drift(
            self.repo, pinned["fingerprint"], self.root)
        self.assertEqual(state, "STALE")
        self.assertEqual(committed, ["M\tsrc.py"])
        self.assertEqual(dirty, [])
        self.assertNotEqual(current, pinned["fingerprint"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "knowledge")

    def test_init_list_status_pin_roundtrip(self):
        src = os.path.join(self.tmp, "doc.md")
        with open(src, "w", encoding="utf-8") as f:
            f.write("v1")

        code, out = run_cli("--root", self.root, "init", "My Topic",
                            "--source", src)
        self.assertEqual(code, 0)
        self.assertIn("OKF_CREATED:", out)

        code, out = run_cli("--root", self.root, "list")
        self.assertEqual(code, 0)
        self.assertIn("my-topic", out)
        self.assertIn("My Topic", out)

        code, out = run_cli("--root", self.root, "status", "My Topic")
        self.assertEqual(code, 0)
        self.assertIn("OKF_STATUS: my-topic FRESH", out)

        with open(src, "w", encoding="utf-8") as f:
            f.write("v2")
        code, out = run_cli("--root", self.root, "status")  # all subjects
        self.assertEqual(code, 1)
        self.assertIn("OKF_STATUS: my-topic STALE", out)

        code, out = run_cli("--root", self.root, "pin", "my-topic")
        self.assertEqual(code, 0)
        self.assertIn("OKF_PINNED:", out)
        code, out = run_cli("--root", self.root, "status", "my-topic")
        self.assertEqual(code, 0)
        self.assertIn("FRESH", out)

    def test_status_on_empty_root_is_ok(self):
        code, out = run_cli("--root", self.root, "status")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_diff_on_non_git_sources_prints_status(self):
        src = os.path.join(self.tmp, "doc.md")
        with open(src, "w", encoding="utf-8") as f:
            f.write("v1")
        run_cli("--root", self.root, "init", "Mixed",
                "--source", src, "--source", "https://example.test/spec")
        with open(src, "w", encoding="utf-8") as f:
            f.write("v2")
        code, out = run_cli("--root", self.root, "diff", "mixed")
        self.assertEqual(code, 0)
        self.assertIn("SOURCE: file %s -> STALE" % os.path.abspath(src), out)
        self.assertIn("SOURCE: external https://example.test/spec -> UNKNOWN",
                      out)
        self.assertIn("OKF_DIFF: mixed STALE", out)
        self.assertNotIn("PINNED:", out)  # fingerprint detail is git-only

    def test_status_on_corrupt_explainer_errors_gracefully(self):
        run_cli("--root", self.root, "init", "Broken")
        explainer = os.path.join(self.root, "broken", okf.EXPLAINER)
        with open(explainer, "w", encoding="utf-8") as f:
            f.write("---\ntype: Explainer\nsources:\n- pinned: 2026-07-11\n"
                    "---\n\n# Broken\n")
        for command in (["status", "broken"], ["diff", "broken"],
                        ["pin", "broken"]):
            with self.assertRaises(SystemExit) as ctx:
                run_cli("--root", self.root, *command)
            self.assertNotEqual(ctx.exception.code, 0)

    def test_corrupt_frontmatter_raises_value_error(self):
        subject_dir = okf.init_okf(self.root, "Broken", [], today=TODAY)
        explainer = os.path.join(subject_dir, okf.EXPLAINER)
        with open(explainer, "w", encoding="utf-8") as f:
            f.write("---\ntitle: no closing fence\n\n# Broken\n")
        with self.assertRaises(ValueError):
            okf.status_okf(subject_dir)
        with self.assertRaises(ValueError):
            okf.diff_okf(subject_dir)


@unittest.skipUnless(GIT, "git not available")
class CliDiffGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.test")
        self._git("config", "user.name", "Test")
        self._commit("src.py", "v1", "first")
        self.root = os.path.join(self.repo, "docs", "knowledge")

    def _git(self, *args):
        subprocess.run(["git", "-C", self.repo] + list(args),
                       check=True, capture_output=True)

    def _commit(self, name, content, message):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as f:
            f.write(content)
        self._git("add", ".")
        self._git("commit", "-q", "-m", message)

    def test_diff_lists_changes_excluding_bundle(self):
        run_cli("--root", self.root, "init", "Repo", "--source", self.repo)
        self._git("add", ".")
        self._git("commit", "-q", "-m", "add bundle")
        self._commit("src.py", "v2", "source change")
        with open(os.path.join(self.repo, "extra.txt"), "w",
                  encoding="utf-8") as f:
            f.write("dirty")
        code, out = run_cli("--root", self.root, "diff", "repo")
        self.assertEqual(code, 0)
        self.assertIn("SOURCE: git ", out)
        self.assertIn(" -> STALE", out)
        self.assertIn("PINNED: ", out)
        self.assertIn("CURRENT: ", out)
        self.assertIn("M\tsrc.py", out)
        self.assertIn("DIRTY\textra.txt", out)
        self.assertNotIn("knowledge/", out.split("PINNED:", 1)[1])
        self.assertIn("OKF_DIFF: repo STALE", out)

    def test_diff_fresh_after_bundle_commit(self):
        run_cli("--root", self.root, "init", "Repo", "--source", self.repo)
        self._git("add", ".")
        self._git("commit", "-q", "-m", "add bundle")
        code, out = run_cli("--root", self.root, "diff", "repo")
        self.assertEqual(code, 0)
        self.assertIn("OKF_DIFF: repo FRESH", out)
        self.assertNotIn("\tM\t", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
