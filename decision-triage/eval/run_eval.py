#!/usr/bin/env python3
"""Gate-runnable outcome eval for decision-triage (docs/eval-standard.md).

End-to-end over the skill's deliverable contract. The harness copies the skill
into a tempdir, lifts the two record variants out of the *shipped* template in
`references/decision-record-template.md`, fills their placeholders with fixture
content, and runs the shipped linter and calibration scorer against them via
subprocess. The grader is model-free.

The promise under test: a user who follows the shipped template produces a record
that passes the shipped linter, the linter rejects each defect the skill exists to
prevent (above all a verdict written before its criteria — the anti-sycophancy
contract), and reviewed records turn into a Brier score with the arithmetic the
skill claims.

Stdlib-only, offline, deterministic, no repo writes. Runs in well under a minute.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

PLACEHOLDER_RE = re.compile(r"<[^>\n]*>")
FENCE_RE = re.compile(r"```markdown\n(.*?)```", re.S)

FILLER = (
    "reconciliation latency measured against the partner cut-off window"
)

# Confidence/outcome series with a hand-computable Brier score.
# 6 calls at 80% (4 right), 4 calls at 60% (2 right).
CAL_SERIES = [(80, True), (80, True), (80, True), (80, True), (80, False), (80, False),
              (60, True), (60, True), (60, False), (60, False)]

CAL_RECORD = """# Decision: Calibration fixture number {i}

- Date: 2026-01-10
- Decider: Fixture Owner
- Path: fast
- Reversibility: reversible
- Cost of reversal: negligible

## Criteria
- C1: Good enough bar for the fixture

## Options
- O1: The option that cleared the bar

## Assessment
- C1: O1=4

## Decision
Proceed with the option that cleared the bar.

## Confidence
{conf}%

## Tripwires
- The fixture stops being representative.

## Review date
2026-04-10

## Outcome
- Reviewed: {reviewed}
- Correct: {correct}
- Notes: fixture
"""


class Grader(object):
    def __init__(self):
        self.results = []

    def check(self, name, ok, detail):
        self.results.append((name, bool(ok), detail))
        print("CHECK: %s — %s (%s)" % (name, "PASS" if ok else "FAIL", detail))

    def report(self):
        passed = sum(1 for _, ok, _ in self.results if ok)
        total = len(self.results)
        if passed == total:
            print("EVAL_RESULT: PASS (%d/%d checks)" % (passed, total))
            return 0
        print("EVAL_RESULT: FAIL (%d/%d checks)" % (passed, total))
        return 1


def extract_templates(skill_dir):
    """Lift the two record variants out of the shipped template document."""
    path = os.path.join(skill_dir, "references", "decision-record-template.md")
    with open(path, "r", encoding="utf-8") as fh:
        blocks = FENCE_RE.findall(fh.read())
    if len(blocks) < 2:
        raise RuntimeError("expected 2 fenced record variants, found %d" % len(blocks))
    return blocks[0], blocks[1]


def fill(template):
    """Replace every <placeholder> with fixture prose the linter should accept."""
    return PLACEHOLDER_RE.sub(FILLER, template)


def run_lint(workdir, record_text, name="record.md"):
    path = os.path.join(workdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(record_text)
    proc = subprocess.run(
        [sys.executable, os.path.join(workdir, "assets", "decision_lint.py"), path],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout


def run_calibration(workdir, records_dir, extra=()):
    proc = subprocess.run(
        [sys.executable, os.path.join(workdir, "assets", "calibration.py"), records_dir] + list(extra),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout


def move_decision_above_criteria(record):
    """Seed the defect the skill exists to prevent: verdict ahead of criteria."""
    lines = record.split("\n")
    start = lines.index("## Decision")
    end = start + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    block = lines[start:end]
    del lines[start:end]
    insert = lines.index("## Criteria")
    return "\n".join(lines[:insert] + block + lines[insert:])


def main():
    g = Grader()
    workdir = tempfile.mkdtemp(prefix="decision-triage-eval-")
    try:
        for sub in ("assets", "references"):
            shutil.copytree(os.path.join(SKILL, sub), os.path.join(workdir, sub))

        full_tpl, fast_tpl = extract_templates(workdir)
        full = fill(full_tpl)
        fast = fill(fast_tpl)

        # --- The shipped template must satisfy the shipped linter -------------
        rc, out = run_lint(workdir, full, "full.md")
        g.check("template-full-path-lints-clean", rc == 0 and "LINT_RESULT: PASS" in out,
                "exit=%d %s" % (rc, out.strip().splitlines()[-1] if out.strip() else "no output"))

        rc, out = run_lint(workdir, fast, "fast.md")
        g.check("template-fast-path-lints-clean", rc == 0 and "LINT_RESULT: PASS" in out,
                "exit=%d %s" % (rc, out.strip().splitlines()[-1] if out.strip() else "no output"))

        # Template and linter must agree on the section vocabulary, both ways.
        sys.path.insert(0, os.path.join(workdir, "assets"))
        import decision_lint  # noqa: E402

        shipped = set(re.findall(r"^##\s+(.+?)\s*$", full_tpl, re.M))
        required = set(decision_lint.FULL_SECTIONS)
        g.check("template-covers-every-required-section", required <= shipped,
                "linter requires %d sections, template ships %d, missing=%s"
                % (len(required), len(shipped), sorted(required - shipped) or "none"))

        # --- Negative fixtures: each defect must be rejected ------------------
        negatives = [
            ("rejects-verdict-before-criteria", move_decision_above_criteria(full), "before"),
            ("rejects-incomplete-score-matrix", full.replace("- C2: O1=3 O2=5", "- C2: O1=3"), "incomplete"),
            ("rejects-prose-confidence", full.replace("70%", "high"), "must be a number"),
            ("rejects-review-date-not-after-decision", full.replace("2026-10-25", "2026-01-01"), "must fall after"),
            ("rejects-empty-record", "", "title must read"),
        ]
        for i, (name, text, expect) in enumerate(negatives):
            rc, out = run_lint(workdir, text, "neg-%d.md" % i)
            g.check(name, rc != 0 and expect in out,
                    "exit=%d, expected message %r %s" % (rc, expect, "found" if expect in out else "ABSENT"))

        # A negative fixture that must NOT trip: the fast path drops the
        # adversarial sections by design, and the linter must allow that.
        rc, out = run_lint(workdir, fast, "fast-again.md")
        g.check("fast-path-not-penalised-for-missing-premortem",
                rc == 0 and "Pre-mortem" not in out, "exit=%d" % rc)

        # --- Calibration over a series with a hand-computable score -----------
        records = os.path.join(workdir, "records")
        os.makedirs(records)
        for i, (conf, correct) in enumerate(CAL_SERIES):
            with open(os.path.join(records, "rec-%02d.md" % i), "w", encoding="utf-8") as fh:
                fh.write(CAL_RECORD.format(i=i, conf=conf, reviewed="2026-04-10",
                                           correct="yes" if correct else "no"))

        expected = sum((c / 100.0 - (1.0 if k else 0.0)) ** 2 for c, k in CAL_SERIES) / len(CAL_SERIES)
        rc, out = run_calibration(workdir, records)
        m = re.search(r"Brier score:\s*([0-9.]+)", out)
        got = float(m.group(1)) if m else None
        g.check("calibration-brier-matches-arithmetic",
                rc == 0 and got is not None and abs(got - expected) < 5e-4,
                "expected %.4f, got %s" % (expected, got))

        g.check("calibration-reads-overconfidence",
                "overconfident" in out,
                "verdict line: %r" % (out.strip().splitlines()[-2] if len(out.strip().splitlines()) > 1 else out))

        # Negative: unreviewed records must be excluded, not counted as wrong.
        with open(os.path.join(records, "rec-unreviewed.md"), "w", encoding="utf-8") as fh:
            fh.write(CAL_RECORD.format(i=99, conf=70, reviewed="", correct=""))
        rc, out2 = run_calibration(workdir, records)
        g.check("calibration-excludes-unreviewed-records",
                rc == 0 and "CALIBRATION_RESULT: OK (%d records)" % len(CAL_SERIES) in out2,
                "still %d records after adding an unreviewed one" % len(CAL_SERIES))

        # Negative: an empty corpus reports EMPTY rather than inventing a score.
        empty = os.path.join(workdir, "empty")
        os.makedirs(empty)
        rc, out3 = run_calibration(workdir, empty)
        g.check("calibration-empty-corpus-reports-empty",
                rc == 0 and "CALIBRATION_RESULT: EMPTY" in out3, "exit=%d" % rc)

        return g.report()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
