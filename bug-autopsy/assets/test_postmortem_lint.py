#!/usr/bin/env python3
"""Unit tests for postmortem_lint.py — stdlib-only, offline, deterministic.

Run standalone from this directory: python3 test_postmortem_lint.py
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import postmortem_lint as pml  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LINT = os.path.join(HERE, "postmortem_lint.py")
TEMPLATE = os.path.join(HERE, os.pardir, "references", "postmortem-template.md")

DEFAULT_BODIES = {
    "Summary": "A cache key omitted the tenant id, so tenant B was served "
               "tenant A's dashboard for up to 5 minutes.",
    "Impact": "3 tenants affected over 42 minutes; no writes corrupted; "
              "2 support tickets.",
    "Timeline": (
        "All times UTC.\n"
        "\n"
        "- 2026-06-30 18:41 — commit abc1234 changed the cache key builder "
        "(evidence: git show abc1234, cache/keys.py:57)\n"
        "- 2026-07-01 09:03 — first cross-tenant read served (inference; "
        "evidence: app.log 09:03:12)\n"
        "- 2026-07-01 09:45 — support ticket filed (evidence: issue link below)\n"
        "- 2026-07-01 10:12 — fix deployed, cache flushed (evidence: commit "
        "def5678, CI run 9912)"
    ),
    "Root cause": (
        "1. **Why did tenant B see tenant A's data?** The dashboard cache key "
        "matched for both tenants (evidence: cache/keys.py:57 at abc1234).\n"
        "2. **Why did the key match?** The tenant id was dropped from the key "
        "in a refactor (evidence: diff of abc1234).\n"
        "3. **Why did the refactor drop it silently?** No test pins tenant "
        "isolation of cache keys (evidence: no such case under tests/).\n"
        "4. **Why was there no such test?** Cache-key construction has no "
        "stated invariant a reviewer would see (systemic: missing guardrail)."
    ),
    "Contributing factors": "- Cache TTL of 5 minutes widened the exposure "
                            "window (evidence: cache/config.py:12).",
    "Fix": "Key builder re-includes the tenant id; regression test added. "
           "Verified by commit def5678 and test_cache_keys.py passing in CI "
           "run 9912.",
    "Prevention": (
        "- [ ] Lint rule: every cache key includes the tenant id "
        "(owner: platform; check: rule fires on abc1234's diff)\n"
        "- [x] Regression test for tenant isolation of cache keys "
        "(owner: platform; check: test_cache_keys.py in CI)"
    ),
    "Links": "- Issue #4821; fix PR #4830; incident channel archive.",
}


def make_doc(overrides=None, drop=()):
    """Build a post-mortem doc from DEFAULT_BODIES with overrides/drops."""
    overrides = overrides or {}
    parts = ["# Post-mortem: stale cache served cross-tenant data", ""]
    for name in pml.REQUIRED_SECTIONS:
        if name in drop:
            continue
        parts.append("## %s" % name)
        parts.append("")
        parts.append(overrides.get(name, DEFAULT_BODIES[name]))
        parts.append("")
    return "\n".join(parts)


def results(text):
    checks, warnings = pml.lint_text(text)
    return {name: (ok, detail) for name, ok, detail in checks}, warnings


def all_pass(text):
    checks, _ = pml.lint_text(text)
    return all(ok for _, ok, _ in checks)


def fill_template(text):
    """The deterministic fill the eval also applies to the template."""
    text = text.replace("YYYY-MM-DD HH:MM", "2026-07-01 14:02")
    return re.sub(r"<[^>\n]+>", "commit abc1234 in cache/keys.py:57", text)


class GoodDocTests(unittest.TestCase):
    def test_complete_doc_passes_every_check(self):
        checks, warnings = pml.lint_text(make_doc())
        self.assertTrue(checks)
        for name, ok, detail in checks:
            self.assertTrue(ok, "%s failed: %s" % (name, detail))
        self.assertEqual(warnings, [])

    def test_heading_prefix_match_is_case_insensitive(self):
        doc = make_doc().replace("## Root cause",
                                 "## ROOT CAUSE (the whys chain)")
        self.assertTrue(all_pass(doc))

    def test_table_timeline_accepted_header_skipped(self):
        table = ("| Time | Event |\n"
                 "|---|---|\n"
                 "| 2026-07-01 09:03 | first bad read (evidence: app.log) |\n"
                 "| 2026-07-01 10:12 | fix deployed (evidence: def5678) |")
        doc = make_doc({"Timeline": table})
        got, _ = results(doc)
        self.assertTrue(got["timeline: every entry timestamped"][0])


class SectionTests(unittest.TestCase):
    def test_missing_root_cause_fails(self):
        doc = make_doc(drop=("Root cause",))
        got, _ = results(doc)
        self.assertFalse(got["section: Root cause"][0])
        self.assertFalse(got["root cause: why chain >= 3 levels"][0])
        self.assertFalse(all_pass(doc))

    def test_empty_section_fails(self):
        doc = make_doc({"Impact": ""})
        got, _ = results(doc)
        self.assertFalse(got["section: Impact"][0])

    def test_heading_inside_code_fence_does_not_count(self):
        doc = make_doc(drop=("Links",))
        doc += "\n```\n## Links\n- not real\n```\n"
        got, _ = results(doc)
        self.assertFalse(got["section: Links"][0])


class TimelineTests(unittest.TestCase):
    def test_entry_without_timestamp_fails(self):
        doc = make_doc({"Timeline": (
            "- 2026-06-30 18:41 — trigger (evidence: abc1234)\n"
            "- the fix went out later that day (evidence: def5678)")})
        got, _ = results(doc)
        ok, detail = got["timeline: every entry timestamped"]
        self.assertFalse(ok)
        self.assertIn("1/2", detail)

    def test_no_entries_fails(self):
        doc = make_doc({"Timeline": "It all happened fast."})
        got, _ = results(doc)
        self.assertFalse(got["timeline: every entry timestamped"][0])

    def test_bare_clock_time_counts(self):
        doc = make_doc({"Timeline": "- 09:03 — first bad read (evidence: log)"})
        got, _ = results(doc)
        self.assertTrue(got["timeline: every entry timestamped"][0])

    def test_file_line_reference_is_not_a_timestamp(self):
        self.assertIsNone(pml.TIMESTAMP_RE.search(
            "- broke in cache/keys.py:57 (evidence: abc1234)"))


class WhyChainTests(unittest.TestCase):
    def test_two_levels_fail_three_pass(self):
        two = ("1. **Why did it fail?** Bad key (evidence: keys.py:57).\n"
               "2. **Why bad?** Refactor dropped it (evidence: abc1234).")
        doc = make_doc({"Root cause": two})
        got, _ = results(doc)
        self.assertFalse(got["root cause: why chain >= 3 levels"][0])

        three = two + ("\n3. **Why silently?** No isolation test "
                       "(evidence: tests/).")
        got, _ = results(make_doc({"Root cause": three}))
        self.assertTrue(got["root cause: why chain >= 3 levels"][0])

    def test_bulleted_and_plain_why_lines_count(self):
        body = ("- Why did it fail? Bad key.\n"
                "* why did the key match? Dropped id.\n"
                "Why was that possible? No test.")
        got, _ = results(make_doc({"Root cause": body}))
        self.assertTrue(got["root cause: why chain >= 3 levels"][0])


class PreventionTests(unittest.TestCase):
    def test_plain_bullet_fails(self):
        doc = make_doc({"Prevention": (
            "- [ ] Add the lint rule (owner: platform; check: CI)\n"
            "- be more careful with cache keys")})
        got, _ = results(doc)
        ok, detail = got["prevention: checkbox-style actionable items"]
        self.assertFalse(ok)
        self.assertIn("non-checkbox", detail)

    def test_no_checkbox_items_fails(self):
        doc = make_doc({"Prevention": "We should improve testing."})
        got, _ = results(doc)
        self.assertFalse(got["prevention: checkbox-style actionable items"][0])


class BlameWarningTests(unittest.TestCase):
    def test_blame_phrasing_warns_but_does_not_fail(self):
        doc = make_doc({"Contributing factors":
                        "- Human error during the deploy (evidence: none)."})
        checks, warnings = pml.lint_text(doc)
        self.assertTrue(all(ok for _, ok, _ in checks))
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0][1].lower(), "human error")


class TemplateTests(unittest.TestCase):
    def test_raw_template_fails_lint(self):
        with open(TEMPLATE, encoding="utf-8") as f:
            raw = f.read()
        self.assertFalse(all_pass(raw))  # YYYY-MM-DD is not a timestamp

    def test_filled_template_passes_lint(self):
        with open(TEMPLATE, encoding="utf-8") as f:
            raw = f.read()
        checks, _ = pml.lint_text(fill_template(raw))
        for name, ok, detail in checks:
            self.assertTrue(ok, "%s failed: %s" % (name, detail))


class CliTests(unittest.TestCase):
    def _run_on(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as f:
            f.write(text)
            path = f.name
        try:
            return subprocess.run([sys.executable, LINT, path],
                                  capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)

    def test_cli_good_doc_exits_zero(self):
        r = self._run_on(make_doc())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("POSTMORTEM_LINT: PASS", r.stdout)

    def test_cli_bad_doc_exits_nonzero(self):
        r = self._run_on(make_doc(drop=("Root cause",)))
        self.assertEqual(r.returncode, 1)
        self.assertIn("POSTMORTEM_LINT: FAIL", r.stdout)
        self.assertIn("LINT: section: Root cause - FAIL", r.stdout)

    def test_cli_missing_file_and_usage(self):
        r = subprocess.run([sys.executable, LINT, "/nonexistent/pm.md"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, LINT],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
