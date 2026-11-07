#!/usr/bin/env python3
"""Stdlib hostile-content suite for okf-site-kit: offline, deterministic.

Each concern has its own TestCase class. Add a new class for a new concern.

PublishedLinkHostsTest: every web host that a file under okf-site-kit names
is on an approved list. The owner removed one community link from
references/bundle-variations.md (decision D24 of run 5: remove the URL and
keep the sentence). This test does not spell the removed host, because then
this file would name it too. It allows only the approved hosts, so the
removed link, or any other unreviewed host, fails the test. To add a link
to a new host, add the host to APPROVED_HOSTS in the same change.

LinkCardAttributeTest: a bundle title or description that holds a quote,
braces, angle brackets or a backslash lands in the homepage <LinkCard>
attributes as one literal string. The test reads each attribute the way a
JSX parser does: a quoted value ends at the next `"` (no backslash escapes)
and a `{...}` value passes only when it holds one JSON string literal.

BodyMarkupTest: a `javascript:`, `vbscript:` or `data:` link (any letter
case), a raw `<script` tag or an `on*=` handler in a bundle body does not
reach a generated .md page as live markup. The page scan uses the same
four patterns as the okf-site-build feature entry. The hostile payloads
are plain text in this file on purpose (they are the fixture, labelled
HOSTILE_*): each one calls okfProbe, which does not exist.

AssetSymlinkTest: a bundle asset that is a symlink to a file outside the
bundle (here a dummy secret) is not copied into public/bundle-assets/, as a
file or as a link, and the run prints a WARN line for it. A dangling
symlink is skipped the same way. A symlink whose target stays inside the
bundle is still copied, as a regular file with the target's bytes. A
normal asset is still copied.
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import shutil
import tempfile
import unittest

import okf_site

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Hosts that okf-site-kit files may name in an http(s) URL.
APPROVED_HOSTS = frozenset({
    "bigquery.googleapis.com",
    "cloud.google.com",
    "document360.com",
    "example.test",
    "flowtivity.ai",
    "github.com",
    "okf.md",
    "openknowledgeformat.com",
    "user.github.io",
    "www.w3.org",
    "x",
})

URL_HOST = re.compile(r"https?://([A-Za-z0-9.-]+)", re.IGNORECASE)


def skill_files():
    for root, dirs, files in os.walk(SKILL_DIR):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
        for name in files:
            yield os.path.join(root, name)


def named_hosts():
    """Return {host: [relative paths]} for every http(s) host in the skill."""
    found: dict[str, list[str]] = {}
    for path in skill_files():
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        rel = os.path.relpath(path, SKILL_DIR)
        for match in URL_HOST.finditer(text):
            found.setdefault(match.group(1).lower(), []).append(rel)
    return found


class PublishedLinkHostsTest(unittest.TestCase):
    def test_every_named_host_is_approved(self):
        unapproved = {
            host: sorted(set(paths))
            for host, paths in named_hosts().items()
            if host not in APPROVED_HOSTS
        }
        self.assertEqual(unapproved, {}, "unapproved hosts in okf-site-kit")

    def test_bundle_variations_keeps_the_community_sentence(self):
        path = os.path.join(SKILL_DIR, "references", "bundle-variations.md")
        with open(path, encoding="utf-8") as fh:
            text = " ".join(fh.read().split())
        self.assertIn("with community sites", text)
        self.assertIn("reproducing spec-shaped examples rather than new dialects", text)
        self.assertIn("https://document360.com/", text)
        self.assertIn("https://flowtivity.ai/", text)

    def test_check_catches_an_unapproved_host(self):
        # Negative fixture: the same regex and list reject a host not on it.
        # The scheme is joined at run time only so that this file does not
        # fail its own walk; the host itself stays plain text.
        scheme = "https"
        sample = f"see [Other]({scheme}://unreviewed.invalid/page) for more"
        hosts = {m.group(1).lower() for m in URL_HOST.finditer(sample)}
        self.assertEqual(hosts - APPROVED_HOSTS, {"unreviewed.invalid"})


ATTR_NAME = re.compile(r"[A-Za-z_][\w:.-]*")

HOSTILE_TITLE = 'Evil" data-probe={okfProbe()} x="'
HOSTILE_DESC = 'Desc" onmouseover={okfProbe()} <b>{1}</b> \\ x="'
HOSTILE_DIR_DESC = 'Guides" data-dirprobe={okfProbe()} x="'


def linkcard_attrs(line):
    """Parse one `<LinkCard ... />` line as JSX does.

    Return (attrs, problem). problem is None when every attribute is one
    literal string and no name repeats.
    """
    i, attrs = len("<LinkCard"), {}
    while True:
        while i < len(line) and line[i] == " ":
            i += 1
        if line[i:] == "/>":
            return attrs, None
        m = ATTR_NAME.match(line, i)
        if not m or line[m.end():m.end() + 1] != "=":
            return attrs, "unparsable at %r" % line[i:i + 30]
        name, i = m.group(0), m.end() + 1
        if line[i:i + 1] == '"':
            j = line.index('"', i + 1)
            value, i = html.unescape(line[i + 1:j]), j + 1
        elif line[i:i + 1] == "{":
            try:
                value, j = json.JSONDecoder().raw_decode(line, i + 1)
            except ValueError:
                return attrs, "attribute %s holds a JS expression" % name
            if not isinstance(value, str) or line[j:j + 1] != "}":
                return attrs, "attribute %s holds a JS expression" % name
            i = j + 1
        else:
            return attrs, "unparsable value for %s" % name
        if name in attrs:
            return attrs, "duplicate attribute %s" % name
        attrs[name] = value


def write_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class LinkCardAttributeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="okf-hostile-")
        self.addCleanup(shutil.rmtree, self.tmp)
        bundle = os.path.join(self.tmp, "bundle")
        write_file(os.path.join(bundle, "index.md"),
                   '---\nokf_version: "0.1"\n---\n# Probe bundle\n\n'
                   "A plain tagline.\n\n"
                   "* [Guides](guides/) - %s\n" % HOSTILE_DIR_DESC)
        write_file(os.path.join(bundle, "guides", "intro.md"),
                   "---\ntype: Guide\ntitle: Intro\n---\n# Intro\n")
        write_file(os.path.join(bundle, "evil.md"),
                   "---\ntype: Concept\ntitle: '%s'\ndescription: '%s'\n"
                   "---\n# Evil\n" % (HOSTILE_TITLE, HOSTILE_DESC))
        # A root concept with no frontmatter: the title comes from its H1.
        write_file(os.path.join(bundle, "bare.md"),
                   "# H1 \"quote\" {okfProbe()} <i>x</i>\n\nBody.\n")
        site = os.path.join(self.tmp, "site")
        code = okf_site.main(["generate", bundle, "--out", site],
                             out=io.StringIO())
        self.assertEqual(code, 0)
        with open(os.path.join(site, "src", "content", "docs", "index.mdx"),
                  encoding="utf-8") as fh:
            self.cards = [ln.strip() for ln in fh
                          if ln.strip().startswith("<LinkCard")]

    def parsed(self):
        out = []
        for line in self.cards:
            attrs, problem = linkcard_attrs(line)
            self.assertIsNone(problem, line)
            self.assertEqual(sorted(attrs), ["description", "href", "title"],
                             line)
            out.append(attrs)
        return out

    def test_every_card_has_only_three_literal_attributes(self):
        self.assertEqual(len(self.parsed()), 3)

    def test_hostile_title_and_description_round_trip(self):
        by_title = {a["title"]: a for a in self.parsed()}
        self.assertIn(HOSTILE_TITLE, by_title)
        self.assertEqual(by_title[HOSTILE_TITLE]["description"], HOSTILE_DESC)
        self.assertIn('H1 "quote" {okfProbe()} <i>x</i>', by_title)

    def test_hostile_directory_description_round_trips(self):
        by_title = {a["title"]: a for a in self.parsed()}
        self.assertEqual(by_title["Guides"]["description"], HOSTILE_DIR_DESC)

    def test_checker_rejects_the_json_in_quotes_form(self):
        # Negative fixture: the pre-fix form, json.dumps text in a quoted
        # attribute, lets the quote end the value and add an attribute.
        line = '<LinkCard title=%s href="/x/" description="d" />' % (
            json.dumps(HOSTILE_TITLE))
        _, problem = linkcard_attrs(line)
        self.assertIsNotNone(problem)

    def test_checker_rejects_a_non_string_expression(self):
        line = '<LinkCard title={okfProbe()} href="/x/" description="d" />'
        _, problem = linkcard_attrs(line)
        self.assertIsNotNone(problem)


# The live-markup patterns of the okf-site-build feature entry.
LIVE_PATTERNS = {
    "md-link-scheme": re.compile(r"\]\(\s*(javascript|vbscript|data):", re.I),
    "href-scheme": re.compile(
        r"""href\s*=\s*["']?\s*(javascript|vbscript|data):""", re.I),
    "script-tag": re.compile(r"<\s*script", re.I),
    "on-handler": re.compile(r"<[a-z][^>]*\son[a-z]+\s*=", re.I),
}

HOSTILE_LINKS = ("[js](javascript:okfProbe) [vb](vbscript:okfProbe) "
                 "[mixed](JaVaScRiPt:okfProbe) [dt](data:text/html;base64,PHA+)")
HOSTILE_HTML = ('<img src="x" onerror="okfProbe()">\n\n'
                "<script>okfProbe()</script>\n\n"
                '<div onclick="okfProbe()">click</div>')
HOSTILE_LOG = ('* [js](javascript:okfProbe) <a href="#" onclick="okfProbe()">'
               "c</a> <script>okfProbe()</script>")

# Bodies that hide a payload from a naive per-line or per-link filter.
HOSTILE_EVASIONS = {
    "link-on-next-line": "[x](\njavascript:okfProbe())",
    "leading-space": "[x](  javascript:okfProbe())",
    "angle-dest": "[x](<javascript:okfProbe()>)",
    "entity-tab": "[x](java&#9;script:okfProbe())",
    "entity-colon": "[x](javascript&#58;okfProbe())",
    "backslash-colon": "[x](javascript\\:okfProbe())",
    "reference": "[x][r]\n\n[r]:\n  JAVASCRIPT:okfProbe()",
    "reference-angle": "[x][r]\n\n[r]: <vbscript:okfProbe()>",
    "autolink": "<javascript:okfProbe()>",
    "backslash-tag": "\\<script>okfProbe()</script>",
    "escaped-tick": "\\`<script>okfProbe()</script>`",
    "tick-in-dest": "[a](x`) <img src=y onerror=okfProbe()>`",
    "comment-end": "<!-- --!><img src=x onerror=okfProbe()> -->",
    "tag-over-lines": "<img\nsrc=x\nonerror=okfProbe()>",
    "crlf-fence": "```\r\n```\r\n<script>okfProbe()</script>\r\n",
    # CommonMark closes the indented fence when the list item ends, so the
    # second column-0 fence closes a new block and the script is live.
    "list-fence": "- item\n  ```\n  code\n```\n```\n<script>okfProbe()</script>",
    "tick-info": "```a`b\n<script>okfProbe()</script>",
}

TAG_OPEN = re.compile(r"<\s*[A-Za-z/!?]")
OUR_TAGS = re.compile(r"</?code>|<(?:https?|mailto):[^\s<>]*>"
                      r"|<[\w.+-]+@[\w.-]+>")
LINK_DEST = re.compile(r"\]\(\s*<?([^\s)>]*)|\]:\s*<?(\S*)")


def live_lines(text):
    """Return the feature-entry pattern labels that match a page body."""
    return [label for line in text.split("\n")
            for label, pat in LIVE_PATTERNS.items() if pat.search(line)]


def raw_tags(text):
    """Every `<` tag start that is not a <code> element or a safe autolink."""
    return TAG_OPEN.findall(OUR_TAGS.sub("", text))


def script_links(text):
    """Link targets that decode to a script-capable scheme."""
    bad = []
    for match in LINK_DEST.finditer(text):
        target = html.unescape(match.group(1) or match.group(2) or "")
        target = re.sub(r"\\(.)", r"\1", target)
        target = re.sub(r"[\x00-\x20]", "", target).lower()
        if target.startswith(("javascript:", "vbscript:", "data:")):
            bad.append(target)
    return bad


def page_body(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    start = lines.index("---", 1) + 1 if lines[0] == "---" else 0
    return "\n".join(lines[start:])


class BodyMarkupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="okf-hostile-body-")
        self.addCleanup(shutil.rmtree, self.tmp)
        bundle = os.path.join(self.tmp, "bundle")
        write_file(os.path.join(bundle, "index.md"),
                   '---\nokf_version: "0.1"\n---\n# Probe bundle\n\n'
                   "A plain tagline.\n\n* [Guides](guides/) - guides\n\n"
                   "Overview links: %s and %s\n" % (HOSTILE_LINKS, HOSTILE_HTML))
        write_file(os.path.join(bundle, "log.md"),
                   "# Log\n\n## 2026-11-07\n%s\n" % HOSTILE_LOG)
        write_file(os.path.join(bundle, "guides", "intro.md"),
                   "---\ntype: Guide\ntitle: Intro <b onclick=okfProbe()>x</b>\n"
                   "---\n# Intro\n\nRead the [overview](../index.md).\n")
        write_file(os.path.join(bundle, "evil.md"),
                   "---\ntype: Concept\ntitle: Evil\n---\n# Evil\n\n"
                   "Body links: %s\n\n%s\n\n"
                   "Kept: [site](https://example.test/a) <https://example.test/b>"
                   " `ARRAY<STRING>`\n\n"
                   "```html\n<b>fenced code stays</b>\n```\n"
                   % (HOSTILE_LINKS, HOSTILE_HTML))
        self.site = os.path.join(self.tmp, "site")
        self.log = io.StringIO()
        code = okf_site.main(["generate", bundle, "--out", self.site],
                             out=self.log)
        self.assertEqual(code, 0)
        self.docs = os.path.join(self.site, "src", "content", "docs")

    def pages(self):
        found = {}
        for base, _, files in os.walk(self.docs):
            for name in files:
                if name.endswith(".md"):
                    path = os.path.join(base, name)
                    found[os.path.relpath(path, self.docs)] = page_body(path)
        return found

    def test_scan_finds_no_live_markup_in_any_page(self):
        pages = self.pages()
        self.assertEqual(
            sorted(pages),
            ["changelog.md", "evil.md", os.path.join("guides", "index.md"),
             os.path.join("guides", "intro.md"), "overview.md"])
        hits = {rel: live_lines(body.replace("```html\n<b>fenced code "
                                             "stays</b>\n```", ""))
                for rel, body in pages.items()}
        self.assertEqual({k: v for k, v in hits.items() if v}, {})

    def test_mixed_case_scheme_is_no_unresolved_link(self):
        self.assertNotIn("JaVaScRiPt", self.log.getvalue())

    def test_normal_markdown_still_renders(self):
        evil = self.pages()["evil.md"]
        self.assertIn("[site](https://example.test/a)", evil)
        self.assertIn("<https://example.test/b>", evil)
        self.assertIn("<code>ARRAY&lt;STRING&gt;</code>", evil)
        self.assertIn("```html\n<b>fenced code stays</b>\n```", evil)
        self.assertIn('&lt;div onclick="okfProbe()">click&lt;/div>', evil)
        self.assertIn('<div class="okf-meta">', evil)
        intro = self.pages()[os.path.join("guides", "intro.md")]
        self.assertIn("[overview](/)", intro)

    def test_every_evasion_is_made_inert(self):
        for name, body in HOSTILE_EVASIONS.items():
            with self.subTest(name):
                self.assertTrue(raw_tags(body) or script_links(body), name)
                out = okf_site.sanitize_body(body)
                self.assertEqual(raw_tags(out), [], out)
                self.assertEqual(script_links(out), [], out)
                self.assertEqual(live_lines(out), [], out)

    def test_scan_catches_the_pre_fix_page(self):
        # Negative fixture: the body as it was copied before the fix.
        body = "%s\n\n%s\n%s" % (HOSTILE_LINKS, HOSTILE_HTML, HOSTILE_LOG)
        self.assertEqual(
            sorted(set(live_lines(body))),
            ["md-link-scheme", "on-handler", "script-tag"])
        self.assertEqual(len(script_links(body)), 5)
        self.assertTrue(raw_tags(body))


# Fixture: a dummy marker, not a real secret. It is the same text as the
# okf-site-build feature entry uses, so a leak is easy to grep for.
DUMMY_SECRET = "OKF-DUMMY-SECRET-123\n"  # scan-leaks:ignore (dummy fixture value)


@unittest.skipUnless(hasattr(os, "symlink"), "needs os.symlink")
class AssetSymlinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="okf-hostile-asset-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bundle = os.path.join(self.tmp, "bundle")
        write_file(os.path.join(self.tmp, "outside", "secret.txt"),
                   DUMMY_SECRET)
        write_file(os.path.join(self.bundle, "index.md"),
                   '---\nokf_version: "0.1"\n---\n# Probe bundle\n\n'
                   "A plain tagline.\n")
        write_file(os.path.join(self.bundle, "assets", "ok.txt"),
                   "plain asset\n")
        assets = os.path.join(self.bundle, "assets")
        try:
            os.symlink(os.path.join("..", "..", "outside", "secret.txt"),
                       os.path.join(assets, "leak.txt"))
            os.symlink(os.path.join(self.tmp, "outside", "secret.txt"),
                       os.path.join(assets, "abs-leak.txt"))
            os.symlink("missing.txt", os.path.join(assets, "dangling.txt"))
            os.symlink("ok.txt", os.path.join(assets, "inside.txt"))
        except (OSError, NotImplementedError) as exc:
            self.skipTest("cannot make symlinks here: %s" % exc)
        self.site = os.path.join(self.tmp, "site")
        self.log = io.StringIO()
        code = okf_site.main(["generate", self.bundle, "--out", self.site],
                             out=self.log)
        self.assertEqual(code, 0, self.log.getvalue())
        self.out_assets = os.path.join(self.site, "public", "bundle-assets",
                                       "assets")

    def test_outside_symlinks_are_not_copied(self):
        for name in ("leak.txt", "abs-leak.txt", "dangling.txt"):
            with self.subTest(name):
                path = os.path.join(self.out_assets, name)
                self.assertFalse(os.path.lexists(path), path)
                self.assertIn("WARN: asset skipped", self.log.getvalue())
                self.assertIn("assets/" + name, self.log.getvalue())

    def test_no_site_file_holds_the_secret(self):
        for base, _, files in os.walk(self.site):
            for name in files:
                path = os.path.join(base, name)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    self.assertNotIn(DUMMY_SECRET.strip(), fh.read(), path)

    def test_normal_and_inside_assets_are_copied(self):
        for name in ("ok.txt", "inside.txt"):
            with self.subTest(name):
                path = os.path.join(self.out_assets, name)
                self.assertTrue(os.path.isfile(path), path)
                self.assertFalse(os.path.islink(path), path)
                with open(path, encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), "plain asset\n")
        self.assertIn("2 asset(s)", self.log.getvalue())

    def test_copy_rechecks_containment(self):
        # The scan accepted the asset; the link is swapped before the copy.
        bundle = okf_site.scan_bundle(self.bundle)
        self.assertIn("assets/ok.txt", bundle.assets)
        ok = os.path.join(self.bundle, "assets", "ok.txt")
        os.remove(ok)
        os.symlink(os.path.join(self.tmp, "outside", "secret.txt"), ok)
        site = os.path.join(self.tmp, "site2")
        okf_site.generate_site(bundle, site, {"title": "T"})
        self.assertFalse(os.path.lexists(
            os.path.join(site, "public", "bundle-assets", "assets", "ok.txt")))

    def test_pre_fix_copy_would_leak(self):
        # Negative fixture: a plain copyfile follows the link, as the
        # generator did before the fix, so the check above can see a leak.
        dst = os.path.join(self.tmp, "prefix-copy.txt")
        shutil.copyfile(os.path.join(self.bundle, "assets", "leak.txt"), dst)
        with open(dst, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), DUMMY_SECRET)
        self.assertFalse(okf_site.asset_inside(
            self.bundle, os.path.join(self.bundle, "assets", "leak.txt")))


class PageSymlinkTest(unittest.TestCase):
    """A .md symlink is a page only when its target stays inside the bundle."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="okf-hostile-page-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bundle = os.path.join(self.tmp, "bundle")
        write_file(os.path.join(self.tmp, "outside", "secret.md"),
                   "# Secret\n\n" + DUMMY_SECRET)
        write_file(os.path.join(self.bundle, "index.md"),
                   '---\nokf_version: "0.1"\n---\n# Probe bundle\n\n'
                   "A plain tagline.\n")
        write_file(os.path.join(self.bundle, "plain.md"),
                   "---\ntype: Concept\n---\n# Plain\n\nPlain body.\n")
        try:
            os.symlink(os.path.join("..", "outside", "secret.md"),
                       os.path.join(self.bundle, "leak.md"))
            os.symlink(os.path.join(self.tmp, "outside", "secret.md"),
                       os.path.join(self.bundle, "abs-leak.md"))
            os.symlink("missing.md", os.path.join(self.bundle, "dangling.md"))
            os.symlink("plain.md", os.path.join(self.bundle, "inside.md"))
        except (OSError, NotImplementedError) as exc:
            self.skipTest("cannot make symlinks here: %s" % exc)
        self.site = os.path.join(self.tmp, "site")
        self.log = io.StringIO()
        code = okf_site.main(["generate", self.bundle, "--out", self.site],
                             out=self.log)
        self.assertEqual(code, 0, self.log.getvalue())
        self.bundle_scan = okf_site.scan_bundle(self.bundle)

    def test_outside_page_symlinks_are_skipped(self):
        for name in ("leak.md", "abs-leak.md", "dangling.md"):
            with self.subTest(name):
                self.assertNotIn(name, self.bundle_scan.concepts)
                self.assertIn("WARN: page skipped", self.log.getvalue())
                self.assertIn(name, self.log.getvalue())

    def test_no_site_file_holds_the_secret(self):
        for base, _, files in os.walk(self.site):
            for name in files:
                path = os.path.join(base, name)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    self.assertNotIn(DUMMY_SECRET.strip(), fh.read(), path)

    def test_normal_and_inside_pages_are_kept(self):
        for name in ("plain.md", "inside.md"):
            with self.subTest(name):
                self.assertIn(name, self.bundle_scan.concepts)

    def test_pre_fix_read_would_leak(self):
        # Negative fixture: a plain open follows the link, as the scan did
        # before the fix, so the check above can see a leak.
        with open(os.path.join(self.bundle, "leak.md"), encoding="utf-8") as fh:
            self.assertIn(DUMMY_SECRET.strip(), fh.read())
        self.assertFalse(okf_site.asset_inside(
            self.bundle, os.path.join(self.bundle, "leak.md")))


if __name__ == "__main__":
    unittest.main()
