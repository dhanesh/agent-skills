#!/usr/bin/env python3
"""decision_lint.py <record.md> [...]

Deterministic linter for decision records produced by the decision-triage skill.
Stdlib only, offline, no writes.

The load-bearing check is CONTRACT ORDER: `## Decision` must appear after both
`## Criteria` and `## Assessment`. A verdict written before its criteria is a
rationalised preference, and that is the failure mode this whole skill exists
to prevent — so it is enforced mechanically rather than by instruction.

Output protocol: one `PASS:`/`FAIL:` line per check, then a final
`LINT_RESULT: PASS (n/n checks)` or `LINT_RESULT: FAIL (k/n checks)`.
Exit 0 iff every check passed.
"""

import datetime
import re
import sys

SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
FIELD_RE = re.compile(r"^-\s*([A-Za-z][A-Za-z ]*?)\s*:\s*(.*)$")
CRITERION_RE = re.compile(r"^-\s*C(\d+)\s*:\s*(.+?)\s*$")
OPTION_RE = re.compile(r"^-\s*O(\d+)\s*:\s*(.+?)\s*$")
SCORE_ROW_RE = re.compile(r"^-\s*C(\d+)\s*:\s*(.+?)\s*$")
SCORE_CELL_RE = re.compile(r"O(\d+)\s*=\s*(\d+)")
CONFIDENCE_RE = re.compile(r"^(\d{1,3})\s*%$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FULL_SECTIONS = (
    "Criteria",
    "Options",
    "Assessment",
    "Outside view",
    "Pre-mortem",
    "Steelman of the rejected option",
    "Decision",
    "Confidence",
    "Tripwires",
    "Review date",
    "Outcome",
)
FAST_SECTIONS = (
    "Criteria",
    "Options",
    "Assessment",
    "Decision",
    "Confidence",
    "Tripwires",
    "Review date",
    "Outcome",
)
HEADER_FIELDS = ("Date", "Decider", "Path", "Reversibility", "Cost of reversal")

# Prose stand-ins people reach for instead of committing to a number.
PROSE_CONFIDENCE = ("high", "low", "medium", "moderate", "very high", "certain", "sure")


def parse_sections(text):
    """Return (ordered list of (name, body), header block before first section)."""
    marks = [(m.start(), m.group(1)) for m in SECTION_RE.finditer(text)]
    header = text[: marks[0][0]] if marks else text
    out = []
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[pos:end].split("\n", 1)
        out.append((name, body[1] if len(body) > 1 else ""))
    return out, header


def parse_date(value):
    if not ISO_DATE_RE.match(value.strip()):
        return None
    try:
        return datetime.date.fromisoformat(value.strip())
    except ValueError:
        return None


def bullets(body):
    return [ln.strip() for ln in body.splitlines() if ln.strip().startswith("- ")]


def prose(body):
    return [
        ln.strip()
        for ln in body.splitlines()
        if ln.strip() and not ln.strip().startswith("```")
    ]


def lint(path):
    """Return (list of (ok, message), ) for one record."""
    results = []

    def ok(msg):
        results.append((True, msg))

    def bad(msg):
        results.append((False, msg))

    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return [(False, "record unreadable: %s" % exc)]

    sections, header = parse_sections(text)
    names = [n for n, _ in sections]
    bodies = dict(sections)
    index = {n: i for i, n in enumerate(names)}

    # 1. Title
    title = re.search(r"^#\s+Decision:\s*(.+?)\s*$", text, re.M)
    if title and len(title.group(1)) >= 8:
        ok("title states the decision in one sentence")
    else:
        bad("title must read '# Decision: <the choice, in one sentence>'")

    # 2. Header fields
    fields = {}
    for line in header.splitlines():
        m = FIELD_RE.match(line.strip())
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()
    missing = [f for f in HEADER_FIELDS if not fields.get(f)]
    if missing:
        bad("header missing or empty: %s" % ", ".join(missing))
    else:
        ok("header carries date, decider, path, reversibility, reversal cost")

    # 3. Path selects the required section set
    path_value = fields.get("Path", "").lower()
    if path_value not in ("fast", "full"):
        bad("Path must be 'fast' or 'full' (got %r)" % fields.get("Path", ""))
        required = FAST_SECTIONS
    else:
        ok("path declared: %s" % path_value)
        required = FULL_SECTIONS if path_value == "full" else FAST_SECTIONS

    absent = [s for s in required if s not in index]
    if absent:
        bad("missing required sections for the %s path: %s" % (path_value or "fast", ", ".join(absent)))
    else:
        ok("all required sections present for the %s path" % (path_value or "fast"))

    # 4. CONTRACT ORDER — the anti-sycophancy check.
    if "Decision" in index and "Criteria" in index and "Assessment" in index:
        if index["Decision"] > index["Criteria"] and index["Decision"] > index["Assessment"]:
            ok("verdict follows the criteria and the assessment")
        else:
            bad(
                "'## Decision' appears before '## Criteria'/'## Assessment' — a verdict "
                "written ahead of its criteria is a rationalised preference"
            )
    else:
        bad("cannot check contract order: Criteria, Assessment and Decision must all exist")

    # 5. Criteria ids, contiguous from C1
    crits = [int(m.group(1)) for m in (CRITERION_RE.match(b) for b in bullets(bodies.get("Criteria", ""))) if m]
    min_crit = 2 if path_value == "full" else 1
    if len(crits) < min_crit:
        bad("the %s path needs at least %d criteria (found %d)" % (path_value or "fast", min_crit, len(crits)))
    elif crits != list(range(1, len(crits) + 1)):
        bad("criteria ids must run C1..Cn without gaps (found %s)" % crits)
    else:
        ok("%d criteria, ids contiguous from C1" % len(crits))

    # 6. Option ids, contiguous from O1
    opts = [int(m.group(1)) for m in (OPTION_RE.match(b) for b in bullets(bodies.get("Options", ""))) if m]
    min_opt = 2 if path_value == "full" else 1
    if len(opts) < min_opt:
        bad("the %s path needs at least %d options (found %d)" % (path_value or "fast", min_opt, len(opts)))
    elif opts != list(range(1, len(opts) + 1)):
        bad("option ids must run O1..On without gaps (found %s)" % opts)
    else:
        ok("%d options, ids contiguous from O1" % len(opts))

    # 7. Complete score matrix, one row per criterion, scores 1-5
    rows = {}
    bad_scores = []
    for b in bullets(bodies.get("Assessment", "")):
        m = SCORE_ROW_RE.match(b)
        if not m:
            continue
        cells = {}
        for cm in SCORE_CELL_RE.finditer(m.group(2)):
            score = int(cm.group(2))
            if not 1 <= score <= 5:
                bad_scores.append("C%s/O%s=%d" % (m.group(1), cm.group(1), score))
            cells[int(cm.group(1))] = score
        rows[int(m.group(1))] = cells
    if crits and opts:
        holes = [
            "C%d/O%d" % (c, o)
            for c in crits
            for o in opts
            if o not in rows.get(c, {})
        ]
        if holes:
            bad("score matrix incomplete — no score for %s" % ", ".join(holes[:8]))
        elif bad_scores:
            bad("scores must be integers 1-5 (offending: %s)" % ", ".join(bad_scores[:8]))
        else:
            ok("score matrix complete: %d criteria x %d options" % (len(crits), len(opts)))
    else:
        bad("cannot check the score matrix without criteria and options")

    # 8. Full-path adversarial sections must carry substance
    if path_value == "full":
        if len(prose(bodies.get("Outside view", ""))) >= 1:
            ok("outside view recorded")
        else:
            bad("'## Outside view' is empty — name the reference class and its base rates")

        pm = bullets(bodies.get("Pre-mortem", ""))
        if len(pm) >= 2 and all(len(b) > 12 for b in pm):
            ok("pre-mortem lists %d distinct failure modes" % len(pm))
        else:
            bad("'## Pre-mortem' needs at least 2 substantive failure modes as '- ' bullets")

        if len(prose(bodies.get("Steelman of the rejected option", ""))) >= 1:
            ok("rejected option steelmanned")
        else:
            bad("'## Steelman of the rejected option' is empty — argue it in earnest")

    # 9. Decision statement
    if len(prose(bodies.get("Decision", ""))) >= 1:
        ok("decision stated")
    else:
        bad("'## Decision' is empty")

    # 10. Numeric confidence, and not false certainty
    conf_lines = prose(bodies.get("Confidence", ""))
    conf_raw = conf_lines[0] if conf_lines else ""
    cm = CONFIDENCE_RE.match(conf_raw)
    if cm and 1 <= int(cm.group(1)) <= 99:
        ok("confidence recorded as %s" % conf_raw)
    elif conf_raw.lower().strip(". ") in PROSE_CONFIDENCE:
        bad("confidence must be a number, not %r — prose confidence cannot be scored" % conf_raw)
    else:
        bad("confidence must be an integer percentage between 1%% and 99%% (got %r)" % conf_raw)

    # 11. Tripwires
    if len(bullets(bodies.get("Tripwires", ""))) >= 1:
        ok("at least one tripwire condition set")
    else:
        bad("'## Tripwires' needs at least one observable condition as a '- ' bullet")

    # 12. Review date strictly after the decision date
    decided = parse_date(fields.get("Date", ""))
    review_lines = prose(bodies.get("Review date", ""))
    reviewed = parse_date(review_lines[0]) if review_lines else None
    if not decided:
        bad("header Date must be an ISO date (YYYY-MM-DD)")
    elif not reviewed:
        bad("'## Review date' must be an ISO date (YYYY-MM-DD)")
    elif reviewed <= decided:
        bad("review date %s must fall after the decision date %s" % (reviewed, decided))
    else:
        ok("review scheduled for %s" % reviewed)

    return results


def main(argv):
    paths = argv[1:]
    if not paths:
        sys.stderr.write("usage: decision_lint.py <record.md> [...]\n")
        return 2

    total = 0
    passed = 0
    for path in paths:
        if len(paths) > 1:
            print("=== %s ===" % path)
        for good, msg in lint(path):
            total += 1
            passed += 1 if good else 0
            print("%s: %s" % ("PASS" if good else "FAIL", msg))

    if passed == total:
        print("LINT_RESULT: PASS (%d/%d checks)" % (passed, total))
        return 0
    print("LINT_RESULT: FAIL (%d/%d checks)" % (passed, total))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
