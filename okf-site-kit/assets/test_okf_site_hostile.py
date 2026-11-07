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
"""
from __future__ import annotations

import os
import re
import unittest

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


if __name__ == "__main__":
    unittest.main()
