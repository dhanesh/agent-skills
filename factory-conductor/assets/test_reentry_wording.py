#!/usr/bin/env python3
"""test_reentry_wording.py — SKILL.md says when the re-entry timer is installed.

One sentence in the "Scheduled re-entry" section says the timer is installed only when
the grant carries a `reentry` block. The sentences that show the timer
(`reentry status`), remove it (`reentry uninstall`) and say a finished run removes its
own timer (`REENTRY: done`) stay in place.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)


def read_skill():
    with open(os.path.join(SKILL_ROOT, "SKILL.md"), encoding="utf-8") as f:
        return f.read()


def section(text, heading):
    """The body of the `## <heading>` section, up to the next `## ` heading."""
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(heading), text, re.S | re.M)
    if not m:
        raise AssertionError("no '## %s' section" % heading)
    return m.group(1)


def sentences(text, *words):
    """The sentences of `text` (joined into one line) that hold every word."""
    flat = re.sub(r"\s+", " ", text)
    words = [w.lower() for w in words]
    return [s for s in re.split(r"(?<=[.!?])\s+", flat)
            if all(w in s.lower() for w in words)]


class ReentryWording(unittest.TestCase):
    def setUp(self):
        self.text = read_skill()
        self.body = section(self.text, "Scheduled re-entry")

    def test_consent_sentence(self):
        hits = sentences(self.body, "timer", "only", "`reentry` block")
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("installs a re-entry timer only when the grant carries a `reentry` block",
                      hits[0])

    def test_feature_word_check(self):
        # The verify recipe's word check runs on the whole file.
        self.assertTrue(sentences(self.text, "timer", "only", "reentry"))

    def test_show_remove_and_self_removal_stay(self):
        self.assertTrue(sentences(self.text, "reentry status", "timer"))
        self.assertTrue(sentences(self.text, "reentry uninstall", "removes"))
        self.assertTrue(sentences(self.text, "REENTRY: done", "timer"))


if __name__ == "__main__":
    unittest.main()
