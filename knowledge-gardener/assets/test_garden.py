#!/usr/bin/env python3
"""Unit tests for garden.py — stdlib-only, offline, deterministic.

Run standalone:  cd knowledge-gardener/assets && python3 test_garden.py
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import garden  # noqa: E402

GIT = shutil.which("git")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def explainer_text(sources_block, title="Fixture subject"):
    return ("---\n"
            "type: Explainer\n"
            "title: %s\n"
            "timestamp: 2026-01-01T00:00:00Z\n"
            "%s"
            "---\n\n# %s\n\nBody.\n" % (title, sources_block, title))


def source_block(stype, locator, fingerprint):
    fp = "null" if fingerprint is None else fingerprint
    return ("sources:\n"
            "- type: %s\n"
            "  locator: %s\n"
            "  fingerprint: %s\n"
            "  pinned: 2026-01-01\n" % (stype, locator, fp))


def make_bundle(root, subjects):
    """subjects: {slug: sources_block}. Writes a spec-conformant bundle."""
    write(os.path.join(root, "index.md"),
          '---\nokf_version: "0.1"\n---\n\n# Subjects\n\n' +
          "".join("* [%s](%s/) - reference explainer\n" % (s, s)
                  for s in subjects))
    write(os.path.join(root, "log.md"),
          "# Directory Update Log\n\n## 2026-01-01\n* **Creation**: seeded.\n")
    for slug, block in subjects.items():
        write(os.path.join(root, slug, "explainer.md"), explainer_text(block))
        write(os.path.join(root, slug, "index.md"),
              "# %s\n\n* [Explainer](explainer.md)\n" % slug)


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
        + list(args),
        cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(path):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", path], check=True,
                   capture_output=True)
    write(os.path.join(path, "pipeline.py"), "def run():\n    return 1\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "initial")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True,
                          capture_output=True, text=True).stdout.strip()
    return head


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="garden-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)


class TestFrontmatter(TempDirCase):
    def test_parses_pins(self):
        meta = garden.parse_frontmatter(explainer_text(
            source_block("git", "/repo", "abc123")))
        pins = garden.extract_sources(meta)
        self.assertEqual(meta["type"], "Explainer")
        self.assertEqual(pins, [{"type": "git", "locator": "/repo",
                                 "fingerprint": "abc123",
                                 "pinned": "2026-01-01"}])

    def test_null_fingerprint_parses_to_none(self):
        meta = garden.parse_frontmatter(explainer_text(
            source_block("external", "https://example.invalid/doc", None)))
        self.assertIsNone(garden.extract_sources(meta)[0]["fingerprint"])

    def test_missing_frontmatter_raises(self):
        with self.assertRaises(garden.SubjectError):
            garden.parse_frontmatter("# No frontmatter here\n")

    def test_unterminated_frontmatter_raises(self):
        with self.assertRaises(garden.SubjectError):
            garden.parse_frontmatter("---\ntype: Explainer\n# never closed\n")

    def test_garbage_line_raises(self):
        with self.assertRaises(garden.SubjectError):
            garden.parse_frontmatter("---\ntype Explainer no colon\n---\n")

    def test_inline_list_sources_rejected(self):
        meta = garden.parse_frontmatter(
            "---\ntype: Explainer\nsources: [a, b]\n---\nbody\n")
        with self.assertRaises(garden.SubjectError):
            garden.extract_sources(meta)

    def test_no_sources_key_means_no_pins(self):
        meta = garden.parse_frontmatter("---\ntype: Explainer\n---\nbody\n")
        self.assertEqual(garden.extract_sources(meta), [])


class TestFingerprints(TempDirCase):
    def test_file_fingerprint_is_sha256(self):
        import hashlib
        p = self.path("doc.txt")
        write(p, "hello\n")
        self.assertEqual(garden.fingerprint(p, "file"),
                         hashlib.sha256(b"hello\n").hexdigest())
        self.assertEqual(garden.detect_source_type(p), "file")

    def test_dir_fingerprint_deterministic_and_change_sensitive(self):
        d = self.path("data")
        write(os.path.join(d, "a.txt"), "A")
        write(os.path.join(d, "sub", "b.txt"), "B")
        first = garden.fingerprint(d, "dir")
        self.assertEqual(garden.fingerprint(d, "dir"), first)
        write(os.path.join(d, "a.txt"), "A2")
        self.assertNotEqual(garden.fingerprint(d, "dir"), first)

    def test_dir_fingerprint_agrees_with_sibling_okf(self):
        # The whole point of the gardener: fingerprints computed here must
        # compare equal to the ones feynman-walkthrough's okf.py pinned
        # (traversal quirks included). Cross-check when the sibling exists.
        okf_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..",
            "feynman-walkthrough", "assets", "okf.py")
        if not os.path.isfile(okf_path):
            self.skipTest("sibling okf.py not installed")
        import importlib.util
        spec = importlib.util.spec_from_file_location("sibling_okf", okf_path)
        okf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(okf)
        d = self.path("data")
        write(os.path.join(d, "a.txt"), "A")
        write(os.path.join(d, "sub", "b.txt"), "B")
        write(os.path.join(d, ".git", "junk"), "vendored")
        self.assertEqual(garden.fingerprint(d, "dir"),
                         okf.fingerprint(d, "dir"))
        doc = self.path("doc.txt")
        write(doc, "same bytes\n")
        self.assertEqual(garden.fingerprint(doc, "file"),
                         okf.fingerprint(doc, "file"))

    def test_git_fingerprint_agrees_with_sibling_okf_for_in_repo_bundle(self):
        """The `git` source type is the ONLY one where the two tools could
        disagree, and it was the one the agreement test never covered — so the
        gardener called every in-repo bundle STALE while okf.py called it
        FRESH, in the layout both skills recommend. Cover it here."""
        okf_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..",
            "feynman-walkthrough", "assets", "okf.py")
        if not os.path.isfile(okf_path):
            self.skipTest("sibling okf.py not installed")
        import importlib.util
        import subprocess
        spec = importlib.util.spec_from_file_location("sibling_okf2", okf_path)
        okf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(okf)

        repo = self.path("repo")
        os.makedirs(repo, exist_ok=True)

        def git(*args):
            return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                                   *args], cwd=repo, capture_output=True, text=True)

        if git("init", "-q", ".").returncode != 0:
            self.skipTest("git unavailable")
        write(os.path.join(repo, "app.py"), "print(1)\n")
        git("add", "-A"); git("commit", "-qm", "init")

        bundle = os.path.join(repo, "docs", "knowledge")
        os.makedirs(bundle, exist_ok=True)
        write(os.path.join(bundle, "index.md"), "# Subjects\n")

        # Bundle present but uncommitted: neither tool may call the repo dirty.
        self.assertEqual(garden.fingerprint(repo, "git", bundle),
                         okf.fingerprint(repo, "git", bundle))
        self.assertNotIn(garden.DIRTY, garden.fingerprint(repo, "git", bundle))

        # Bundle committed: the source did not change, so the pin still holds.
        pinned = garden.fingerprint(repo, "git", bundle)
        git("add", "-A"); git("commit", "-qm", "add bundle")
        after = garden.fingerprint(repo, "git", bundle)
        self.assertEqual(after, okf.fingerprint(repo, "git", bundle))
        self.assertEqual(garden.git_drift(repo, pinned, after, bundle)["changed_files"], [])
        self.assertEqual(okf.git_drift(repo, pinned, bundle)[0], "FRESH")

        # A real source change must still register in BOTH tools.
        write(os.path.join(repo, "app.py"), "print(2)\n")
        git("add", "-A"); git("commit", "-qm", "change source")
        current = garden.fingerprint(repo, "git", bundle)
        self.assertEqual(garden.git_drift(repo, pinned, current, bundle)["changed_files"],
                         ["app.py"])
        self.assertEqual(okf.git_drift(repo, pinned, bundle)[0], "STALE")

    def test_external_has_no_fingerprint(self):
        self.assertIsNone(garden.fingerprint("https://example.invalid",
                                             "external"))
        self.assertEqual(garden.detect_source_type("https://example.invalid"),
                         "external")

    @unittest.skipUnless(GIT, "git not installed")
    def test_git_fingerprint_head_and_dirty_marker(self):
        repo = self.path("repo")
        head = make_repo(repo)
        self.assertEqual(garden.detect_source_type(repo), "git")
        self.assertEqual(garden.fingerprint(repo, "git"), head)
        write(os.path.join(repo, "pipeline.py"), "def run():\n    return 2\n")
        self.assertEqual(garden.fingerprint(repo, "git"), head + "+dirty")


class TestDiscovery(TempDirCase):
    def test_finds_bundle_and_subjects(self):
        root = self.path("knowledge")
        make_bundle(root, {"alpha": "", "beta": ""})
        bundles = garden.find_bundles([self.tmp])
        self.assertEqual(bundles, [(root, "0.1")])
        self.assertEqual(garden.find_subjects(root), ["alpha", "beta"])

    def test_index_without_okf_version_is_not_a_bundle(self):
        write(self.path("docs", "index.md"), "# Just docs\n")
        self.assertEqual(garden.find_bundles([self.tmp]), [])

    def test_no_nested_bundle_hunting_inside_a_bundle(self):
        root = self.path("knowledge")
        make_bundle(root, {"alpha": ""})
        make_bundle(os.path.join(root, "alpha", "inner"), {"x": ""})
        self.assertEqual(garden.find_bundles([self.tmp]), [(root, "0.1")])

    def test_missing_root_finds_nothing(self):
        self.assertEqual(garden.find_bundles([self.path("nope")]), [])


class TestAssessment(TempDirCase):
    def _bundle_with(self, block):
        root = self.path("knowledge")
        make_bundle(root, {"subj": block})
        return root

    def test_fresh_file_source(self):
        doc = self.path("paper.txt")
        write(doc, "content\n")
        fp = garden.fingerprint(doc, "file")
        root = self._bundle_with(source_block("file", doc, fp))
        info = garden.assess_subject(root, "subj")
        self.assertEqual(info["state"], "FRESH")
        self.assertEqual(info["sources"][0]["state"], "FRESH")

    def test_stale_file_source(self):
        doc = self.path("paper.txt")
        write(doc, "content\n")
        fp = garden.fingerprint(doc, "file")
        root = self._bundle_with(source_block("file", doc, fp))
        write(doc, "revised content\n")
        info = garden.assess_subject(root, "subj")
        self.assertEqual(info["state"], "STALE")

    def test_external_source_unknown(self):
        root = self._bundle_with(
            source_block("external", "https://example.invalid/doc", None))
        info = garden.assess_subject(root, "subj")
        self.assertEqual(info["state"], "UNKNOWN")

    def test_moved_file_source_unknown(self):
        root = self._bundle_with(
            source_block("file", self.path("gone.txt"), "deadbeef"))
        info = garden.assess_subject(root, "subj")
        self.assertEqual(info["state"], "UNKNOWN")
        self.assertIsNone(info["sources"][0]["current_fingerprint"])

    def test_stale_beats_unknown_overall(self):
        doc = self.path("paper.txt")
        write(doc, "content\n")
        fp = garden.fingerprint(doc, "file")
        block = (source_block("file", doc, fp) +
                 "- type: external\n  locator: https://example.invalid\n"
                 "  fingerprint: null\n  pinned: 2026-01-01\n")
        root = self._bundle_with(block)
        write(doc, "revised\n")
        self.assertEqual(garden.assess_subject(root, "subj")["state"], "STALE")

    def test_no_pins_is_fresh(self):
        root = self._bundle_with("")
        self.assertEqual(garden.assess_subject(root, "subj")["state"], "FRESH")

    def test_corrupt_frontmatter_is_error_not_crash(self):
        root = self.path("knowledge")
        make_bundle(root, {"subj": ""})
        write(os.path.join(root, "subj", "explainer.md"),
              "---\ntype: Explainer\ntitle: broken\n# closing fence lost\n")
        info = garden.assess_subject(root, "subj")
        self.assertEqual(info["state"], "ERROR")
        self.assertIn("unterminated", info["error"])

    @unittest.skipUnless(GIT, "git not installed")
    def test_stale_git_source_names_changed_files(self):
        repo = self.path("repo")
        head = make_repo(repo)
        root = self._bundle_with(source_block("git", repo, head))
        self.assertEqual(garden.assess_subject(root, "subj")["state"], "FRESH")
        write(os.path.join(repo, "pipeline.py"), "def run():\n    return 2\n")
        write(os.path.join(repo, "extra.py"), "X = 1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "change")
        info = garden.assess_subject(root, "subj")
        row = info["sources"][0]
        self.assertEqual(info["state"], "STALE")
        self.assertEqual(row["changed_files"], ["extra.py", "pipeline.py"])
        self.assertIn("2 files changed", row["diffstat"])

    @unittest.skipUnless(GIT, "git not installed")
    def test_dirty_worktree_only_is_stale_with_note(self):
        repo = self.path("repo")
        head = make_repo(repo)
        root = self._bundle_with(source_block("git", repo, head))
        write(os.path.join(repo, "pipeline.py"), "def run():\n    return 3\n")
        row = garden.assess_subject(root, "subj")["sources"][0]
        self.assertEqual(row["state"], "STALE")
        self.assertEqual(row["changed_files"], ["pipeline.py"])
        self.assertIn("uncommitted", row["note"])


class TestCLI(TempDirCase):
    def run_main(self, *argv):
        out = io.StringIO()
        code = garden.main(list(argv), out=out)
        return code, out.getvalue()

    def test_sweep_fresh_exits_zero(self):
        doc = self.path("paper.txt")
        write(doc, "content\n")
        make_bundle(self.path("knowledge"),
                    {"subj": source_block("file", doc,
                                          garden.fingerprint(doc, "file"))})
        code, out = self.run_main("sweep", self.tmp)
        self.assertEqual(code, 0)
        self.assertIn("SUBJECT: subj — FRESH", out)
        self.assertIn("GARDEN_RESULT: FRESH", out)

    def test_sweep_stale_exits_nonzero(self):
        doc = self.path("paper.txt")
        write(doc, "content\n")
        make_bundle(self.path("knowledge"),
                    {"subj": source_block("file", doc,
                                          garden.fingerprint(doc, "file"))})
        write(doc, "revised\n")
        code, out = self.run_main("sweep", self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("GARDEN_RESULT: STALE", out)
        self.assertIn("pinned", out)

    def test_sweep_error_exits_nonzero(self):
        root = self.path("knowledge")
        make_bundle(root, {"subj": ""})
        write(os.path.join(root, "subj", "explainer.md"), "# no frontmatter\n")
        code, out = self.run_main("sweep", self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("SUBJECT: subj — ERROR", out)
        self.assertIn("GARDEN_RESULT: ERROR", out)

    def test_sweep_unknown_only_exits_zero(self):
        make_bundle(self.path("knowledge"),
                    {"subj": source_block("external",
                                          "https://example.invalid", None)})
        code, out = self.run_main("sweep", self.tmp)
        self.assertEqual(code, 0)
        self.assertIn("GARDEN_RESULT: UNKNOWN", out)

    def test_sweep_no_bundles_says_so(self):
        os.makedirs(self.path("empty"))
        code, out = self.run_main("sweep", self.path("empty"))
        self.assertEqual(code, 0)
        self.assertIn("GARDEN_RESULT: NO_BUNDLES", out)

    def test_json_output_is_parseable_and_agrees(self):
        import json
        doc = self.path("paper.txt")
        write(doc, "content\n")
        make_bundle(self.path("knowledge"),
                    {"subj": source_block("file", doc,
                                          garden.fingerprint(doc, "file"))})
        write(doc, "revised\n")
        code, out = self.run_main("json", self.tmp)
        data = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(data["result"], "STALE")
        self.assertEqual(data["exit_code"], 1)
        self.assertEqual(data["bundles"][0]["subjects"][0]["state"], "STALE")


if __name__ == "__main__":
    unittest.main(verbosity=1)
