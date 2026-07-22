#!/usr/bin/env python3
"""control_pack_lint.py — deterministic linter for a Migration Control Pack.

A Migration Control Pack is the operating system the agents run on: the
artifacts that turn "rewrite a lot of code" into "run a verified process".
This linter enforces the mechanical half of that contract (the exact
grammar lives in references/control-pack.md):

  RULEBOOK.md               '- MR<n>:' rules, sequential from MR1, each
                            carrying a binding modal (must/never/always)
  DEPENDENCY_MAP.md         >= 2 numbered migration waves, sequential
  GAP_INVENTORY.md          '- G<n>:' gaps, each either an open question
                            (ends with '?') or resolved ('decision:')
  PORTABILITY_TEST_PLAN.md  '- S<n>:' golden scenarios, and the plan must
                            reference the pack's parity script by name
  parity_check.*            the mechanical judge itself (any extension)
  AGENT_WORK_QUEUE.md       '- <unit> [status]' entries with a status from
                            the allowed set (the queue is rebuilt from
                            facts, so statuses must be machine-readable)
  REVIEWER_PROMPTS.md       covers >= 3 named failure-mode categories
  PHASE_GATES.md            >= 3 numbered gates, sequential, and gate 1
                            establishes the judge

Usage:
    python3 control_pack_lint.py <pack-dir>

Output: one "FAIL: ..." line per issue, then a final
"PACK_RESULT: PASS" or "PACK_RESULT: FAIL (n issue(s))" line.
Exit 0 iff the pack is clean; 2 on usage errors.
Stdlib-only, offline, deterministic.
"""

import os
import re
import sys
from collections import OrderedDict

REQUIRED_FILES = (
    "RULEBOOK.md",
    "DEPENDENCY_MAP.md",
    "GAP_INVENTORY.md",
    "PORTABILITY_TEST_PLAN.md",
    "AGENT_WORK_QUEUE.md",
    "REVIEWER_PROMPTS.md",
    "PHASE_GATES.md",
)

QUEUE_STATUSES = (
    "pending",
    "in-progress",
    "migrated",
    "parity-pass",
    "parity-fail",
    "blocked",
)

# Reviewer prompts must attack specific failure modes, not "please review".
# A category counts as covered when any of its cue words appears.
FAILURE_MODES = OrderedDict((
    ("money-precision", ("money", "precision", "rounding", "decimal",
                         "currency")),
    ("time-and-zones", ("timezone", "time zone", "date", "utc", "dst")),
    ("nullability", ("null", "optional", "none", "undefined")),
    ("error-handling", ("error", "exception", "fallback")),
    ("idempotency", ("idempoten", "retry", "duplicate")),
    ("security", ("auth", "security", "permission")),
    ("observability", ("logging", "observability", "metric", "trace")),
))

JUDGE_WORDS = ("judge", "parity", "test", "verif")

MODAL_RE = re.compile(r"\b(must|never|always)\b", re.IGNORECASE)


def _entries(text, prefix):
    """Parse '- <prefix><n>: <text>' bullets; returns [(n, text), ...]."""
    pat = re.compile(r"^- %s(\d+):\s*(.+?)\s*$" % prefix, re.MULTILINE)
    return [(int(m.group(1)), m.group(2)) for m in pat.finditer(text)]


def _check_sequential(numbers, fname, label, issues):
    """Ids must run 1..n in order — gaps and reorders break batch repair."""
    for i, n in enumerate(numbers):
        if n != i + 1:
            issues.append(
                "%s: %s numbering not sequential (expected %d, got %d)"
                % (fname, label, i + 1, n)
            )
            return False
    return True


def _numbered_items(text):
    """Parse '1. ...' numbered lines; returns [(n, text), ...]."""
    pat = re.compile(r"^(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
    return [(int(m.group(1)), m.group(2)) for m in pat.finditer(text)]


def lint_rulebook(text, issues):
    rules = _entries(text, "MR")
    if not rules:
        issues.append("RULEBOOK.md: no '- MR<n>:' rules found")
        return
    if not _check_sequential([n for n, _ in rules], "RULEBOOK.md", "rule",
                             issues):
        return
    for n, body in rules:
        if not MODAL_RE.search(body):
            issues.append(
                "RULEBOOK.md: MR%d lacks a binding modal "
                "(must/never/always)" % n
            )


def lint_dependency_map(text, issues):
    waves = _numbered_items(text)
    if len(waves) < 2:
        issues.append(
            "DEPENDENCY_MAP.md: needs >= 2 numbered migration waves "
            "(found %d)" % len(waves)
        )
        return
    _check_sequential([n for n, _ in waves], "DEPENDENCY_MAP.md", "wave",
                      issues)


def lint_gap_inventory(text, issues):
    gaps = _entries(text, "G")
    if not gaps:
        issues.append("GAP_INVENTORY.md: no '- G<n>:' gap entries found")
        return
    if not _check_sequential([n for n, _ in gaps], "GAP_INVENTORY.md", "gap",
                             issues):
        return
    for n, body in gaps:
        if not (body.endswith("?") or "decision:" in body.lower()):
            issues.append(
                "GAP_INVENTORY.md: G%d is neither an open question ('?') "
                "nor resolved ('decision:')" % n
            )


def lint_test_plan(text, parity_scripts, issues):
    scenarios = _entries(text, "S")
    if not scenarios:
        issues.append(
            "PORTABILITY_TEST_PLAN.md: no '- S<n>:' golden scenarios found"
        )
    else:
        _check_sequential([n for n, _ in scenarios],
                          "PORTABILITY_TEST_PLAN.md", "scenario", issues)
    if parity_scripts and not any(s in text for s in parity_scripts):
        issues.append(
            "PORTABILITY_TEST_PLAN.md: does not reference the parity "
            "script (%s)" % ", ".join(parity_scripts)
        )


def lint_work_queue(text, issues):
    entry_re = re.compile(r"^- (.+?)\s+\[([a-z][a-z-]*)\]\s*$")
    count = 0
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        m = entry_re.match(line)
        if not m:
            issues.append(
                "AGENT_WORK_QUEUE.md: unparseable entry: %r "
                "(expected '- <unit> [status]')" % line
            )
            continue
        count += 1
        status = m.group(2)
        if status not in QUEUE_STATUSES:
            issues.append(
                "AGENT_WORK_QUEUE.md: unknown status '%s' for '%s' "
                "(allowed: %s)" % (status, m.group(1),
                                   ", ".join(QUEUE_STATUSES))
            )
    if count == 0 and not any(
        i.startswith("AGENT_WORK_QUEUE.md: unparseable") for i in issues
    ):
        issues.append("AGENT_WORK_QUEUE.md: no queue entries found")


def lint_reviewer_prompts(text, issues):
    lower = text.lower()
    covered = [
        cat for cat, cues in FAILURE_MODES.items()
        if any(c in lower for c in cues)
    ]
    if len(covered) < 3:
        missing = [c for c in FAILURE_MODES if c not in covered]
        issues.append(
            "REVIEWER_PROMPTS.md: only %d/%d failure-mode categories "
            "covered (need >= 3; missing: %s)"
            % (len(covered), len(FAILURE_MODES), ", ".join(missing))
        )


def lint_phase_gates(text, issues):
    gates = _numbered_items(text)
    if len(gates) < 3:
        issues.append(
            "PHASE_GATES.md: needs >= 3 numbered gates (found %d)"
            % len(gates)
        )
        return
    if not _check_sequential([n for n, _ in gates], "PHASE_GATES.md", "gate",
                             issues):
        return
    first = gates[0][1].lower()
    if not any(w in first for w in JUDGE_WORDS):
        issues.append(
            "PHASE_GATES.md: gate 1 must establish the judge "
            "(mention judge/parity/test/verify)"
        )


def lint_pack(pack_dir):
    """Lint a control-pack directory; returns a list of issue strings."""
    issues = []
    texts = {}
    for name in REQUIRED_FILES:
        path = os.path.join(pack_dir, name)
        if not os.path.isfile(path):
            issues.append("missing artifact: %s" % name)
            texts[name] = None
        else:
            with open(path, encoding="utf-8") as f:
                texts[name] = f.read()

    parity_scripts = sorted(
        f for f in os.listdir(pack_dir)
        if f.startswith("parity_check")
        and os.path.isfile(os.path.join(pack_dir, f))
    )
    if not parity_scripts:
        issues.append("missing artifact: parity script (parity_check.*)")
    else:
        for s in parity_scripts:
            if not open(os.path.join(pack_dir, s), encoding="utf-8").read().strip():
                issues.append("parity script %s is empty" % s)

    checks = (
        ("RULEBOOK.md", lambda t: lint_rulebook(t, issues)),
        ("DEPENDENCY_MAP.md", lambda t: lint_dependency_map(t, issues)),
        ("GAP_INVENTORY.md", lambda t: lint_gap_inventory(t, issues)),
        ("PORTABILITY_TEST_PLAN.md",
         lambda t: lint_test_plan(t, parity_scripts, issues)),
        ("AGENT_WORK_QUEUE.md", lambda t: lint_work_queue(t, issues)),
        ("REVIEWER_PROMPTS.md", lambda t: lint_reviewer_prompts(t, issues)),
        ("PHASE_GATES.md", lambda t: lint_phase_gates(t, issues)),
    )
    for name, fn in checks:
        if texts[name] is not None:
            fn(texts[name])
    return issues


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("Usage: python3 control_pack_lint.py <pack-dir>",
              file=sys.stderr)
        return 2
    pack_dir = argv[0]
    if not os.path.isdir(pack_dir):
        print("ERROR: pack directory not found: %s" % pack_dir,
              file=sys.stderr)
        return 2
    issues = lint_pack(pack_dir)
    for issue in issues:
        print("FAIL: %s" % issue)
    if issues:
        print("PACK_RESULT: FAIL (%d issue(s))" % len(issues))
        return 1
    print("PACK_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
