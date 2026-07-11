#!/usr/bin/env python3
"""Stdlib test suite for okf_site.py — offline, deterministic.

Fixtures mirror the three OKF bundle families observed in the wild:
Google's sample bundles (okf/bundles/{ga4,stackoverflow,crypto_bitcoin} in
GoogleCloudPlatform/knowledge-catalog: block-list tags, folded multi-line
descriptions, .md-suffixed relative links, no okf_version, no log.md,
viz.html assets, trailing-underscore filenames), spec-canonical bundles
(okf_version root index, log.md, bundle-absolute links, inline tags), and
producer-extended bundles (block lists of dicts in frontmatter, e.g. the
feynman-walkthrough skill's `sources` pins).
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest

import okf_site


def run_cli(*argv):
    out = io.StringIO()
    code = okf_site.main(list(argv), out=out)
    return code, out.getvalue()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class ParserTests(unittest.TestCase):
    def test_google_style_frontmatter(self):
        text = """---
type: BigQuery Table
resource: https://bigquery.googleapis.com/v2/projects/p/tables/events_*
title: Events table (Google Analytics BigQuery Export)
description: Contains Google Analytics event export data from the `x`
  dataset.
tags:
- events
- Google Analytics
- basic queries
timestamp: '2026-05-28T22:53:05+00:00'
---

# Events table
body
"""
        meta, body, had = okf_site.parse_frontmatter(text)
        self.assertTrue(had)
        self.assertEqual(meta["type"], "BigQuery Table")
        self.assertEqual(meta["description"],
                         "Contains Google Analytics event export data "
                         "from the `x` dataset.")
        self.assertEqual(meta["tags"],
                         ["events", "Google Analytics", "basic queries"])
        self.assertEqual(meta["timestamp"], "2026-05-28T22:53:05+00:00")
        self.assertTrue(body.startswith("# Events table"))

    def test_inline_list_quotes_null_and_comment(self):
        text = ('---\ntype: Metric\ntags: [revenue, saas]\nfp: null\n'
                'okf_version: "0.1"\npinned: 2026-07-11 # when\n---\nbody\n')
        meta, _, _ = okf_site.parse_frontmatter(text)
        self.assertEqual(meta["tags"], ["revenue", "saas"])
        self.assertIsNone(meta["fp"])
        self.assertEqual(meta["okf_version"], "0.1")
        self.assertEqual(meta["pinned"], "2026-07-11")

    def test_literal_and_folded_block_scalars(self):
        text = "---\ntype: Note\nlit: |\n  line one\n  line two\nfold: >\n  a\n  b\n---\n"
        meta, _, _ = okf_site.parse_frontmatter(text)
        self.assertEqual(meta["lit"], "line one\nline two")
        self.assertEqual(meta["fold"], "a b")

    def test_block_list_of_dicts(self):
        text = ("---\ntype: Explainer\nsources:\n- type: git\n  locator: /repo\n"
                "  fingerprint: abc\n- type: external\n  locator: https://x\n"
                "  fingerprint: null\n---\nbody\n")
        meta, _, _ = okf_site.parse_frontmatter(text)
        self.assertEqual(meta["sources"][0]["locator"], "/repo")
        self.assertIsNone(meta["sources"][1]["fingerprint"])

    def test_no_frontmatter(self):
        meta, body, had = okf_site.parse_frontmatter("# Just markdown\n")
        self.assertFalse(had)
        self.assertEqual(meta, {})
        self.assertEqual(body, "# Just markdown\n")

    def test_unclosed_frontmatter_is_body(self):
        meta, body, had = okf_site.parse_frontmatter("---\ntype: X\nno close\n")
        self.assertFalse(had)
        self.assertIn("type: X", body)


class HeadingAndPanelTests(unittest.TestCase):
    def test_leading_duplicate_h1_dropped(self):
        out = okf_site.normalize_headings("# My Title\n\ntext\n", "My Title")
        self.assertNotIn("# My Title", out)

    def test_multiple_h1_bodies_demoted(self):
        body = "# Overview\ntext\n# Schema\n```\n# not a heading\n```\n"
        out = okf_site.normalize_headings(body, "Events")
        self.assertIn("## Overview", out)
        self.assertIn("## Schema", out)
        self.assertIn("\n# not a heading\n", out)

    def test_h2_bodies_untouched(self):
        body = "## Overview\ntext\n"
        self.assertEqual(okf_site.normalize_headings(body, "T"), body)

    def test_meta_panel_contents_and_escaping(self):
        meta = {"type": "BigQuery Table", "tags": ["a<b"],
                "resource": "https://example.test/r",
                "timestamp": "2026-05-28", "owner": "d&h",
                "sources": [{"type": "git", "locator": "/repo",
                             "fingerprint": None}]}
        panel = okf_site.meta_panel(meta)
        self.assertIn("BigQuery Table", panel)
        self.assertIn("a&lt;b", panel)
        self.assertIn('<a href="https://example.test/r">', panel)
        self.assertIn("d&amp;h", panel)
        self.assertIn("fingerprint: null", panel)
        self.assertNotIn("description", panel)  # recommended keys not repeated


def make_google_style(root):
    """Mimic okf/bundles/ga4: nested dirs, .md links, assets, no okf_version."""
    os.makedirs(os.path.join(root, "tables"))
    os.makedirs(os.path.join(root, "references", "metrics"))
    with open(os.path.join(root, "index.md"), "w", encoding="utf-8") as f:
        f.write("# GA4 sample bundle\n\nObfuscated Google Analytics export "
                "data for the merchandise store.\n\n"
                "* [tables](tables/index.md) - Google Analytics event export data.\n"
                "* [references](references/) - Joins and metric definitions.\n")
    with open(os.path.join(root, "tables", "index.md"), "w", encoding="utf-8") as f:
        f.write("# Tables\n\n* [Events](events_.md) - the export table.\n")
    with open(os.path.join(root, "tables", "events_.md"), "w", encoding="utf-8") as f:
        f.write("""---
type: BigQuery Table
title: Events table
description: Event export data from the `sample`
  dataset.
tags:
- events
- BigQuery
timestamp: '2026-05-28T22:53:05+00:00'
---
# Events table

See [Event Count](../references/metrics/event_count.md) and the
[visualizer](../viz.html).

# Schema

| field | type |
|---|---|
| event_date | STRING |
""")
    with open(os.path.join(root, "references", "metrics", "event_count.md"),
              "w", encoding="utf-8") as f:
        f.write("---\ntype: Metric\ntitle: Event Count\n---\nCounts events. "
                "Back to [events](/tables/events_.md).\n")
    with open(os.path.join(root, "viz.html"), "w", encoding="utf-8") as f:
        f.write("<html></html>")


def make_spec_style(root):
    """Spec-canonical: okf_version root index, log.md, absolute links."""
    os.makedirs(os.path.join(root, "playbooks"))
    with open(os.path.join(root, "index.md"), "w", encoding="utf-8") as f:
        f.write('---\nokf_version: "0.1"\n---\n\n# Ops knowledge\n\n'
                "* [playbooks](playbooks/) - runbooks\n")
    with open(os.path.join(root, "log.md"), "w", encoding="utf-8") as f:
        f.write("# Directory Update Log\n\n## 2026-07-11\n"
                "* **Creation**: Established [Revenue review]"
                "(/playbooks/revenue-review.md).\n")
    with open(os.path.join(root, "playbooks", "revenue-review.md"), "w",
              encoding="utf-8") as f:
        f.write("---\ntype: Playbook\ntitle: Revenue review\n"
                "tags: [revenue, saas]\n---\nSteps here.\n")


class ScanAndRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_google_style_scan(self):
        make_google_style(self.tmp)
        b = okf_site.scan_bundle(self.tmp)
        self.assertIsNone(b.okf_version)
        self.assertIn("tables/events_.md", b.concepts)
        self.assertIn("", b.indexes)
        self.assertIn("tables", b.indexes)
        self.assertEqual(b.assets, ["viz.html"])
        self.assertIsNone(b.log)

    def test_spec_style_scan(self):
        make_spec_style(self.tmp)
        b = okf_site.scan_bundle(self.tmp)
        self.assertEqual(b.okf_version, "0.1")
        self.assertIsNotNone(b.log)

    def test_conformance_warnings(self):
        os.makedirs(os.path.join(self.tmp, "x"))
        with open(os.path.join(self.tmp, "no-type.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: T\n---\nbody\n")
        with open(os.path.join(self.tmp, "no-fm.md"), "w", encoding="utf-8") as f:
            f.write("just text\n")
        with open(os.path.join(self.tmp, "x", "log.md"), "w", encoding="utf-8") as f:
            f.write("nested\n")
        b = okf_site.scan_bundle(self.tmp)
        joined = "\n".join(b.warnings)
        self.assertIn("missing required `type`", joined)
        self.assertIn("without frontmatter", joined)
        self.assertIn("nested log.md", joined)
        self.assertIn("no-fm.md", b.concepts)  # rendered anyway

    def test_routes_slugified_and_collision_free(self):
        os.makedirs(os.path.join(self.tmp, "tables"))
        for name in ("events_.md", "events.md"):
            with open(os.path.join(self.tmp, "tables", name), "w",
                      encoding="utf-8") as f:
                f.write("---\ntype: T\n---\nb\n")
        b = okf_site.scan_bundle(self.tmp)
        routes = okf_site.plan_routes(b)
        r1 = routes["md:tables/events.md"]
        r2 = routes["md:tables/events_.md"]
        self.assertNotEqual(r1, r2)
        for r in (r1, r2):
            self.assertTrue(r.startswith("tables/events"))
            self.assertNotIn("_", r)

    def test_skip_dirs_ignored(self):
        os.makedirs(os.path.join(self.tmp, ".git"))
        with open(os.path.join(self.tmp, ".git", "x.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\ntype: T\n---\n")
        b = okf_site.scan_bundle(self.tmp)
        self.assertEqual(b.concepts, {})


class LinkRewriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        make_google_style(self.tmp)
        self.b = okf_site.scan_bundle(self.tmp)
        self.routes = okf_site.plan_routes(self.b)

    def rewrite(self, body, current_dir):
        unresolved = []
        out = okf_site.rewrite_links(body, current_dir, self.b, self.routes,
                                     unresolved)
        return out, unresolved

    def test_relative_md_link(self):
        out, un = self.rewrite(
            "[Event Count](../references/metrics/event_count.md)", "tables")
        self.assertIn("(/references/metrics/event-count/)", out)
        self.assertEqual(un, [])

    def test_absolute_md_link_and_fragment(self):
        out, _ = self.rewrite("[e](/tables/events_.md#schema)", "references")
        self.assertIn("(/tables/events/#schema)", out)

    def test_index_md_and_directory_links(self):
        out, _ = self.rewrite("[t](tables/index.md) [r](references/)", "")
        self.assertIn("(/tables/)", out)
        self.assertIn("(/references/)", out)

    def test_asset_link(self):
        out, _ = self.rewrite("[viz](../viz.html)", "tables")
        self.assertIn("(/bundle-assets/viz.html)", out)

    def test_external_and_anchor_untouched(self):
        body = "[x](https://example.test/a.md) [y](#local) [m](mailto:a@b)"
        out, un = self.rewrite(body, "tables")
        self.assertEqual(out, body)
        self.assertEqual(un, [])

    def test_unresolved_recorded_and_left(self):
        out, un = self.rewrite("[gone](missing/nope.md)", "tables")
        self.assertIn("(missing/nope.md)", out)
        self.assertEqual(un, ["missing/nope.md"])

    def test_fenced_code_untouched(self):
        body = "```\n[x](../references/metrics/event_count.md)\n```\n"
        out, _ = self.rewrite(body, "tables")
        self.assertIn("(../references/metrics/event_count.md)", out)


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bundle = os.path.join(self.tmp, "bundle")
        self.out = os.path.join(self.tmp, "site")
        os.makedirs(self.bundle)

    def test_google_style_end_to_end(self):
        make_google_style(self.bundle)
        code, out = run_cli("generate", self.bundle, "--out", self.out)
        self.assertEqual(code, 0)
        self.assertIn("SITE_GENERATED:", out)
        docs = os.path.join(self.out, "src", "content", "docs")
        page = read(os.path.join(docs, "tables", "events.md"))
        self.assertIn('title: "Events table"', page)
        self.assertIn("okf-meta", page)
        self.assertIn("BigQuery Table", page)
        self.assertIn("(/references/metrics/event-count/)", page)
        self.assertIn("(/bundle-assets/viz.html)", page)
        self.assertIn("## Schema", page)  # demoted from H1
        # authored section index kept; synthesized index for references/
        self.assertIn("Events", read(os.path.join(docs, "tables", "index.md")))
        refs_index = read(os.path.join(docs, "references", "index.md"))
        self.assertIn("(/references/metrics/)", refs_index)
        # homepage with cards + tagline from root index prose
        home = read(os.path.join(docs, "index.mdx"))
        self.assertIn("LinkCard", home)
        self.assertIn("Obfuscated Google Analytics", home)
        self.assertIn("Google Analytics event export data.", home)  # index desc
        # overview page from root index body, asset copied, scaffolding
        self.assertTrue(os.path.isfile(os.path.join(docs, "overview.md")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.out, "public", "bundle-assets", "viz.html")))
        for rel in ("astro.config.mjs", "package.json", "src/content.config.ts",
                    "src/styles/okf.css", "public/favicon.svg"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, rel)), rel)
        self.assertFalse(os.path.exists(
            os.path.join(self.out, ".github")))  # no workflow unless asked

    def test_spec_style_changelog_and_version(self):
        make_spec_style(self.bundle)
        code, out = run_cli("generate", self.bundle, "--out", self.out)
        self.assertEqual(code, 0)
        self.assertIn("OKF_VERSION: 0.1", out)
        docs = os.path.join(self.out, "src", "content", "docs")
        changelog = read(os.path.join(docs, "changelog.md"))
        self.assertIn('title: "Changelog"', changelog)
        self.assertIn("## 2026-07-11", changelog)
        self.assertIn("(/playbooks/revenue-review/)", changelog)
        self.assertNotIn("# Directory Update Log", changelog)
        config = read(os.path.join(self.out, "astro.config.mjs"))
        self.assertIn("'Changelog', slug: 'changelog'", config)
        self.assertIn("autogenerate", config)

    def test_producer_extension_keys_rendered(self):
        with open(os.path.join(self.bundle, "explainer.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\ntype: Explainer\ntitle: Pipeline\nsources:\n"
                    "- type: git\n  locator: /repo\n  fingerprint: abc\n---\nbody\n")
        run_cli("generate", self.bundle, "--out", self.out)
        page = read(os.path.join(self.out, "src", "content", "docs",
                                 "explainer.md"))
        self.assertIn("Sources", page)
        self.assertIn("locator: /repo", page)

    def test_missing_title_and_type_degrade(self):
        with open(os.path.join(self.bundle, "raw-notes.md"), "w",
                  encoding="utf-8") as f:
            f.write("plain text, no frontmatter at all\n")
        code, out = run_cli("generate", self.bundle, "--out", self.out)
        self.assertEqual(code, 0)
        self.assertIn("WARN:", out)
        page = read(os.path.join(self.out, "src", "content", "docs",
                                 "raw-notes.md"))
        self.assertIn('title: "Raw notes"', page)
        self.assertIn("Concept", page)  # generic type badge

    def test_base_flag_reaches_config_and_homepage(self):
        make_spec_style(self.bundle)
        run_cli("generate", self.bundle, "--out", self.out, "--base", "/kb",
                "--deploy-workflow")
        config = read(os.path.join(self.out, "astro.config.mjs"))
        self.assertIn('const BASE = "/kb";', config)
        home = read(os.path.join(self.out, "src", "content", "docs",
                                 "index.mdx"))
        self.assertIn('href="/kb/playbooks/"', home)
        self.assertTrue(os.path.isfile(
            os.path.join(self.out, ".github", "workflows", "deploy.yml")))

    def test_refuses_regenerate_without_force(self):
        make_spec_style(self.bundle)
        run_cli("generate", self.bundle, "--out", self.out)
        with self.assertRaises(SystemExit):
            run_cli("generate", self.bundle, "--out", self.out)
        code, _ = run_cli("generate", self.bundle, "--out", self.out, "--force")
        self.assertEqual(code, 0)

    def test_unresolved_links_reported(self):
        with open(os.path.join(self.bundle, "a.md"), "w", encoding="utf-8") as f:
            f.write("---\ntype: Note\n---\n[gone](missing.md)\n")
        code, out = run_cli("generate", self.bundle, "--out", self.out)
        self.assertEqual(code, 0)
        self.assertIn("unresolved link left as-is: missing.md", out)

    def test_title_yaml_escaping(self):
        with open(os.path.join(self.bundle, "q.md"), "w", encoding="utf-8") as f:
            f.write('---\ntype: Note\ntitle: He said "go" \\ now\n---\nb\n')
        run_cli("generate", self.bundle, "--out", self.out)
        page = read(os.path.join(self.out, "src", "content", "docs", "q.md"))
        self.assertIn('title: "He said \\"go\\" \\\\ now"', page)


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_inspect_reports_concepts(self):
        make_google_style(self.tmp)
        code, out = run_cli("inspect", self.tmp)
        self.assertEqual(code, 0)
        self.assertIn("CONCEPT: tables/events_.md [BigQuery Table] "
                      "-> /tables/events/", out)
        self.assertIn("INSPECT_RESULT: OK", out)

    def test_inspect_empty_bundle(self):
        code, out = run_cli("inspect", self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("INSPECT_RESULT: EMPTY", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
