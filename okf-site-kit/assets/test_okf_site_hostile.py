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


if __name__ == "__main__":
    unittest.main()
