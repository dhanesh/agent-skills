#!/usr/bin/env python3
"""Gate-runnable outcome eval for bug-autopsy (docs/eval-standard.md).

Exercises the skill's deliverable contract end-to-end: fixture post-mortems
are written to a tempdir and pushed through the shipped linter exactly as the
workflow's verify-and-repair step runs it (python3 assets/postmortem_lint.py
<file>), grading exit codes and output lines. Known-bad negatives are
mandatory and included: a missing Root cause section, a 1-level why chain,
and a timeline without timestamps must all be rejected. The shipped template
is also graded for usability: with its placeholders filled it must lint
clean. Offline, deterministic, stdlib-only, no repo writes.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
LINT = os.path.join(SKILL, "assets", "postmortem_lint.py")
TEMPLATE = os.path.join(SKILL, "references", "postmortem-template.md")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


GOOD = """# Post-mortem: stale cache served cross-tenant data

## Summary

A cache key omitted the tenant id, so tenant B was served tenant A's
dashboard for up to 5 minutes. A regression test and a key-shape lint rule
now pin the invariant.

## Impact

3 tenants affected over 42 minutes; no writes corrupted; 2 support tickets.

## Timeline

All times UTC.

- 2026-06-30 18:41 — commit abc1234 changed the cache key builder (evidence: git show abc1234, cache/keys.py:57)
- 2026-07-01 09:03 — first cross-tenant read served (inference; evidence: app.log 09:03:12)
- 2026-07-01 09:45 — support ticket filed (evidence: issue #4821)
- 2026-07-01 10:12 — fix deployed, cache flushed (evidence: commit def5678, CI run 9912)

## Root cause

1. **Why did tenant B see tenant A's data?** The dashboard cache key matched for both tenants (evidence: cache/keys.py:57 at abc1234).
2. **Why did the key match?** The tenant id was dropped from the key in a refactor (evidence: diff of abc1234).
3. **Why did the refactor drop it silently?** No test pins tenant isolation of cache keys (evidence: no such case under tests/).
4. **Why was there no such test?** Cache-key construction has no stated invariant a reviewer would see (systemic: missing guardrail).

## Contributing factors

- Cache TTL of 5 minutes widened the exposure window (evidence: cache/config.py:12).

## Fix

Key builder re-includes the tenant id; regression test added. Verified by
commit def5678 and test_cache_keys.py passing in CI run 9912.

## Prevention

- [ ] Lint rule: every cache key includes the tenant id (owner: platform; check: rule fires on abc1234's diff)
- [x] Regression test for tenant isolation of cache keys (owner: platform; check: test_cache_keys.py in CI)

## Links

- Issue #4821; fix PR #4830; incident channel archive.
"""


def lint(tmp, name, text):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return subprocess.run([sys.executable, LINT, path],
                          capture_output=True, text=True, timeout=30)


def fill_template(text):
    """Deterministic placeholder fill: real timestamps, real-looking refs."""
    text = text.replace("YYYY-MM-DD HH:MM", "2026-07-01 14:02")
    return re.sub(r"<[^>\n]+>", "commit abc1234 in cache/keys.py:57", text)


def main():
    tmp = tempfile.mkdtemp(prefix="bug-autopsy-eval-")
    try:
        # Known-good: a complete, evidence-cited, blameless post-mortem.
        r = lint(tmp, "good.md", GOOD)
        check("lint accepts a complete evidence-cited post-mortem",
              r.returncode == 0 and "POSTMORTEM_LINT: PASS" in r.stdout,
              "exit %d" % r.returncode)

        # Negative: Root cause section removed entirely.
        no_root = re.sub(r"## Root cause\n(?:.*\n)*?(?=## Contributing)",
                         "", GOOD)
        r = lint(tmp, "no_root_cause.md", no_root)
        check("lint rejects a post-mortem missing its Root cause section",
              r.returncode != 0
              and "LINT: section: Root cause - FAIL" in r.stdout,
              "exit %d" % r.returncode)

        # Negative: a why chain only 1 level deep.
        shallow = re.sub(
            r"## Root cause\n(?:.*\n)*?(?=## Contributing)",
            "## Root cause\n\n1. **Why did it break?** The cache key was "
            "wrong (evidence: cache/keys.py:57).\n\n",
            GOOD)
        r = lint(tmp, "shallow_whys.md", shallow)
        check("lint rejects a 1-level why chain",
              r.returncode != 0
              and "LINT: root cause: why chain >= 3 levels - FAIL" in r.stdout,
              "exit %d" % r.returncode)

        # Negative: timeline entries stripped of their timestamps.
        no_ts = re.sub(r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2} — ", "- ",
                       GOOD, flags=re.MULTILINE)
        no_ts = no_ts.replace("app.log 09:03:12", "app.log")
        r = lint(tmp, "no_timestamps.md", no_ts)
        check("lint rejects a timeline without timestamps",
              r.returncode != 0
              and "LINT: timeline: every entry timestamped - FAIL" in r.stdout,
              "exit %d" % r.returncode)

        # Blameless advisory: warns without failing the run.
        # NEGATIVE: structurally perfect, entirely evidence-free. This is the
        # shape the linter used to score 11/11 — every section present, every
        # timestamp in place, a 3-level why chain in which every why reads
        # "Because of a thing." The deliverable claims "evidence-cited"; this
        # fixture is what makes that claim falsifiable.
        hollow = """# Post-mortem: the thing broke

## Summary
A thing broke.

## Impact
Some users were affected.

## Timeline
- 2026-08-01T10:00Z - it started
- 2026-08-01T11:00Z - it stopped

## Root cause
- Why did it break? Because of a thing.
- Why was there a thing? Because of another thing.
- Why another thing? Because of a thing.

## Contributing factors
- It was a Tuesday.

## Fix
We fixed it.

## Prevention
- [ ] Do better

## Detection
We noticed.
"""
        r = lint(tmp, "hollow.md", hollow)
        check("negative: structurally complete but evidence-free write-up is rejected",
              r.returncode == 1 and "cites evidence - FAIL" in r.stdout,
              r.stdout.strip().splitlines()[-1][:60])

        blamey = GOOD.replace(
            "- Cache TTL of 5 minutes widened the exposure window "
            "(evidence: cache/config.py:12).",
            "- Human error during the deploy widened the window "
            "(evidence: none).")
        r = lint(tmp, "blamey.md", blamey)
        check("blame-y phrasing warns without failing",
              r.returncode == 0 and "WARN: blame-y phrasing" in r.stdout,
              "exit %d" % r.returncode)

        # Template usability: placeholders filled -> lint-clean.
        with open(TEMPLATE, encoding="utf-8") as f:
            template = f.read()
        r = lint(tmp, "filled_template.md", fill_template(template))
        check("shipped template, placeholders filled, passes lint",
              r.returncode == 0 and "POSTMORTEM_LINT: PASS" in r.stdout,
              "exit %d" % r.returncode)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total, passed = len(_checks), sum(_checks)
    ok = passed == total
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL",
                                              passed, total))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
