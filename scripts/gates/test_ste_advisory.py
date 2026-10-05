#!/usr/bin/env python3
"""Tests for ste-advisory.py (stdlib unittest; offline, deterministic).

Run by scripts/gates/test_gates.sh, so `make gate` runs it. The advisory itself
never fails the gate; these tests prove that its checks find what they claim
to find and skip what they claim to skip.
"""
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "ste-advisory.py")
spec = importlib.util.spec_from_file_location("ste_advisory", SCRIPT)
ste = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ste)


def words(n):
    return " ".join(["word"] * (n - 1) + ["end."])


class LongSentences(unittest.TestCase):
    def test_25_words_passes(self):
        self.assertEqual(ste.check_text(words(25))["long"], [])

    def test_26_words_is_flagged(self):
        hits = ste.check_text(words(26))["long"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], 26)

    def test_soft_wrapped_paragraph_is_joined(self):
        text = "\n".join(["word word word word word word word word word"] * 3) + " end."
        self.assertEqual(len(ste.check_text(text)["long"]), 1)

    def test_list_items_are_separate_units(self):
        text = "- " + " ".join(["word"] * 15) + "\n- " + " ".join(["word"] * 15) + "\n"
        self.assertEqual(ste.check_text(text)["long"], [])

    def test_sentences_split_after_bold_lead(self):
        text = "**" + " ".join(["lead"] * 6) + ".** " + words(20).capitalize()
        self.assertEqual(ste.check_text(text)["long"], [])

    def test_abbreviation_does_not_split_or_count_twice(self):
        text = "Use a tool, e.g. ruff. " + words(10)
        self.assertEqual(ste.check_text(text)["long"], [])


class SkippedRegions(unittest.TestCase):
    LONG = " ".join(["word"] * 40) + " e.g. was rejected."

    def assert_clean(self, text):
        hits = ste.check_text(text)
        self.assertEqual(sum(len(v) for v in hits.values()), 0, hits)

    def test_backtick_fence(self):
        self.assert_clean("```\n" + self.LONG + "\n```\n")

    def test_tilde_fence(self):
        self.assert_clean("~~~text\n" + self.LONG + "\n~~~\n")

    def test_table(self):
        self.assert_clean("| a | b |\n|---|---|\n| " + self.LONG + " | x |\n")

    def test_heading(self):
        self.assert_clean("# " + self.LONG + "\n")

    def test_front_matter(self):
        self.assert_clean("---\ndescription: " + self.LONG + "\n---\nShort text.\n")

    def test_html_comment(self):
        self.assert_clean("<!--\n" + self.LONG + "\n-->\nShort text.\n")

    def test_inline_code(self):
        self.assert_clean("Run `" + self.LONG + "` now.")

    def test_url_does_not_inflate_count(self):
        url = "https://example.com/" + "/".join(["a b"[0]] * 5)
        text = " ".join(["word"] * 24) + " " + url + "."
        self.assertEqual(ste.check_text(text)["long"], [])

    def test_link_target_dropped_link_text_kept(self):
        text = "See [the guide](https://example.com/e.g./etc.) for more."
        self.assertEqual(ste.check_text(text)["latin"], [])


class LatinAndPassive(unittest.TestCase):
    def test_latin_abbreviations(self):
        text = "Use tools, e.g. ruff. That is, i.e. this. Add more etc. Namely viz. this."
        found = [h[1].lower() for h in ste.check_text(text)["latin"]]
        self.assertEqual(found, ["e.g.", "i.e.", "etc.", "viz."])

    def test_via_is_not_flagged(self):
        self.assertEqual(ste.check_text("Send it via the API.")["latin"], [])

    def test_passive_hint(self):
        hits = ste.check_text("The file was rejected by the gate.")["passive"]
        self.assertEqual(len(hits), 1)

    def test_passive_irregular_and_adverb(self):
        hits = ste.check_text("The key is quickly written to disk.")["passive"]
        self.assertEqual(len(hits), 1)

    def test_active_voice_is_clean(self):
        self.assertEqual(ste.check_text("The gate rejects the file.")["passive"], [])

    def test_adjective_after_be_is_skipped(self):
        self.assertEqual(ste.check_text("The output is based on the input.")["passive"], [])


class CommandLine(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args],
                              capture_output=True, text=True, timeout=60)

    def test_exit_zero_with_hints_and_final_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(words(30) + "\n\nUse it, e.g. now.\n")
            r = self.run_cli(p)
        self.assertEqual(r.returncode, 0)
        last = r.stdout.strip().splitlines()[-1]
        self.assertEqual(last, "STE_ADVISORY: 2 hints in 1 files")

    def test_no_files(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "STE_ADVISORY: 0 hints in 0 files")

    def test_missing_file_is_skipped_not_fatal(self):
        r = self.run_cli("/nonexistent/x.md")
        self.assertEqual(r.returncode, 0)
        self.assertIn("skipped", r.stdout)
        self.assertTrue(r.stdout.strip().endswith("STE_ADVISORY: 0 hints in 0 files"))

    def test_examples_limit(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("\n\n".join([words(30)] * 5) + "\n")
            with redirect_stdout(buf):
                ste.main(["--examples", "2", p])
        out = buf.getvalue()
        self.assertIn(": 5 long,", out)
        self.assertEqual(out.count("  long L"), 2)


if __name__ == "__main__":
    unittest.main()
