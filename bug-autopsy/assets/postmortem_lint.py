#!/usr/bin/env python3
"""Deterministic structural linter for bug-autopsy post-mortems.

Usage: python3 postmortem_lint.py <postmortem.md>

Structural checks (any failure -> non-zero exit):
  - every required section is present as a `## ` heading and non-empty.
    Heading match is a case-insensitive prefix, so "## Root cause (the whys
    chain)" satisfies the required "Root cause" section.
  - Timeline: at least one entry, and every entry carries a timestamp
    (a YYYY-MM-DD date, optionally with HH:MM[:SS], or a bare HH:MM time).
    Entries are bullet lines; a markdown table is also accepted (the header
    row and separator row are not counted as entries).
  - Root cause: the whys chain is at least 3 "Why ...?" levels deep.
  - Prevention: at least one `- [ ]` / `- [x]` checkbox item, and every
    bullet in the section is checkbox-style — an item you cannot phrase as
    a tickable box is not actionable and checkable.

Warnings (reported, never fail the run):
  - blame-y phrasing ("human error", "should have known", ...). A blameless
    post-mortem names systemic causes, not people; each warning is a prompt
    to reframe, left to judgment because quoted text can be legitimate.

Fenced code blocks are ignored entirely, so example snippets can contain
headings or bullets without confusing the linter.

Output protocol: one `LINT: <name> - PASS|FAIL (<detail>)` line per check,
`WARN: ...` lines for advisories, and a final
`POSTMORTEM_LINT: PASS (n/n checks)` or `POSTMORTEM_LINT: FAIL (k/n checks)`.
Exit 0 iff every structural check passed. Stdlib-only, offline, deterministic.
"""

import re
import sys

REQUIRED_SECTIONS = (
    "Summary",
    "Impact",
    "Timeline",
    "Root cause",
    "Contributing factors",
    "Fix",
    "Prevention",
    "Links",
)

HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"  # date [+ time]
    r"|(?<![\d:])\d{1,2}:\d{2}(?::\d{2})?(?![\d:])"  # bare clock time
)
WHY_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?[*_]*\s*why\b", re.IGNORECASE)
BULLET_RE = re.compile(r"^\s*[-*+]\s+\S")
CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[(?: |x|X)\]\s+\S")
TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
# Evidence markers. SKILL.md's deliverable promises an "evidence-cited" write-up
# where every timeline entry and every why cites `file:line`, a commit sha, a CI
# run or an issue link — but the linter only ever checked that the sections were
# present and non-empty, so a post-mortem whose every why read "Because of a
# thing." scored a clean 11/11. These are what make the claim checkable.
EVIDENCE_RE = re.compile(
    r"(?:[\w./+-]+\.[A-Za-z0-9]{1,8}:\d+"      # path/to/file.py:14
    r"|\b[0-9a-f]{7,40}\b"                       # commit sha
    r"|https?://\S+"                              # CI run, issue, dashboard
    r"|#\d+"                                      # issue / PR reference
    r"|\bPR\s*#?\d+|\bissue\s*#?\d+"
    r"|\b(?:run|build|job|pipeline)\s*#?\d+)",
    re.IGNORECASE)
# A DECLARED BASIS also counts. The requirement is that the author says what
# backs the statement — not that a commit sha exists for every line. An entry
# reconstructed rather than recorded may say so, and a systemic conclusion may
# name itself as one; both are honest, and both beat tempting an author to
# invent a plausible sha to satisfy a linter. What fails is an assertion with
# no stated basis at all ("Because of a thing.").
INFERENCE_RE = re.compile(
    r"(?:\bevidence\s*[:=]"
    r"|\bsystemic\s*[:=]"
    r"|\binferen(?:ce|ces)\b|\binferred\b"
    r"|\bno artifact\b|\bunrecorded\b"
    r"|\bper\s+(?:the\s+)?(?:log|logs|transcript|report)\b)",
    re.IGNORECASE)
OWNER_RE = re.compile(r"(?:@[\w.-]+|\bowner\s*[:=]|\bowned by\b)", re.IGNORECASE)
BLAME_RE = re.compile(
    r"human error|should have known|careless|negligen\w*|operator error"
    r"|to blame|at fault|fault of",
    re.IGNORECASE,
)


def _visible_lines(text):
    """(lineno, line) pairs with fenced code blocks removed."""
    out = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append((lineno, line))
    return out


def _sections(vlines):
    """Ordered (title, body) pairs; body is a list of (lineno, line)."""
    sections = []
    body = None
    for lineno, line in vlines:
        m = HEADING_RE.match(line)
        if m:
            body = []
            sections.append((m.group(1), body))
        elif body is not None:
            body.append((lineno, line))
    return sections


def _find(sections, name):
    want = name.lower()
    for title, body in sections:
        if title.lower().startswith(want):
            return body
    return None


def lint_text(text):
    """Return (checks, warnings).

    checks   -> list of (name, ok, detail) structural results
    warnings -> list of (lineno, phrase) blame-y phrasing advisories
    """
    checks = []
    warnings = []
    vlines = _visible_lines(text)
    sections = _sections(vlines)

    found = {}
    for name in REQUIRED_SECTIONS:
        body = _find(sections, name)
        if body is None:
            checks.append(("section: %s" % name, False,
                           "missing `## %s` heading" % name))
            continue
        found[name] = body
        nonempty = any(line.strip() for _, line in body)
        checks.append(("section: %s" % name, nonempty,
                       "" if nonempty else "heading present but body is empty"))

    # Timeline: entries exist and every entry carries a timestamp.
    tl_name = "timeline: every entry timestamped"
    if "Timeline" not in found:
        checks.append((tl_name, False, "no Timeline section to check"))
    else:
        body = found["Timeline"]
        entries = [(n, line) for n, line in body if BULLET_RE.match(line)]
        if not entries:
            rows = [(n, line) for n, line in body
                    if TABLE_ROW_RE.match(line) and not TABLE_SEP_RE.match(line)]
            entries = rows[1:]  # first data-less row is the table header
        if not entries:
            checks.append((tl_name, False,
                           "no entries (use `- <timestamp> - <event>` bullets)"))
        else:
            missing = [n for n, line in entries if not TIMESTAMP_RE.search(line)]
            detail = "%d/%d entries timestamped" % (
                len(entries) - len(missing), len(entries))
            if missing:
                detail += "; missing on line(s) %s" % ", ".join(
                    str(n) for n in missing)
            checks.append((tl_name, not missing, detail))

    # Root cause: the whys chain is at least 3 levels deep.
    why_name = "root cause: why chain >= 3 levels"
    if "Root cause" not in found:
        checks.append((why_name, False, "no Root cause section to check"))
    else:
        whys = [n for n, line in found["Root cause"] if WHY_RE.match(line)]
        checks.append((why_name, len(whys) >= 3,
                       "%d why level(s) found" % len(whys)))

    # Prevention: checkbox-style actionable items only.
    prev_name = "prevention: checkbox-style actionable items"
    if "Prevention" not in found:
        checks.append((prev_name, False, "no Prevention section to check"))
    else:
        bullets = [(n, line) for n, line in found["Prevention"]
                   if BULLET_RE.match(line)]
        boxes = [n for n, line in bullets if CHECKBOX_RE.match(line)]
        plain = [n for n, line in bullets if not CHECKBOX_RE.match(line)]
        if not boxes:
            checks.append((prev_name, False,
                           "no checkbox items (use `- [ ] <action>`)"))
        elif plain:
            checks.append((prev_name, False,
                           "non-checkbox bullet(s) on line(s) %s" % ", ".join(
                               str(n) for n in plain)))
        else:
            checks.append((prev_name, True, "%d actionable item(s)" % len(boxes)))

    # Evidence: every timeline entry cites an artifact (or is marked an inference).
    ev_name = "timeline: every entry cites evidence"
    if "Timeline" not in found:
        checks.append((ev_name, False, "no Timeline section to check"))
    else:
        body = found["Timeline"]
        entries = [(n, line) for n, line in body if BULLET_RE.match(line)]
        if not entries:
            rows = [(n, line) for n, line in body
                    if TABLE_ROW_RE.match(line) and not TABLE_SEP_RE.match(line)]
            entries = rows[1:]
        if not entries:
            checks.append((ev_name, False, "no entries to check"))
        else:
            bare = [n for n, line in entries
                    if not (EVIDENCE_RE.search(line) or INFERENCE_RE.search(line))]
            detail = "%d/%d entries cite evidence" % (
                len(entries) - len(bare), len(entries))
            if bare:
                detail += ("; uncited on line(s) %s (add file:line, a commit sha, "
                           "a CI/issue link, or mark it `(inference)`)"
                           % ", ".join(str(n) for n in bare))
            checks.append((ev_name, not bare, detail))

    # Evidence: each why level is backed, not just asserted.
    wev_name = "root cause: every why cites evidence"
    if "Root cause" not in found:
        checks.append((wev_name, False, "no Root cause section to check"))
    else:
        whys = [(n, line) for n, line in found["Root cause"] if WHY_RE.match(line)]
        if not whys:
            checks.append((wev_name, False, "no why levels to check"))
        else:
            bare = [n for n, line in whys
                    if not (EVIDENCE_RE.search(line) or INFERENCE_RE.search(line))]
            detail = "%d/%d why levels cite evidence" % (len(whys) - len(bare), len(whys))
            if bare:
                detail += ("; uncited on line(s) %s"
                           % ", ".join(str(n) for n in bare))
            checks.append((wev_name, not bare, detail))

    # Fix: names the commit / test / CI run that verifies it (SKILL.md step 5).
    fix_name = "fix: names a verifying commit, test, or CI run"
    if "Fix" not in found:
        checks.append((fix_name, False, "no Fix section to check"))
    else:
        joined = " ".join(line for _, line in found["Fix"])
        ok = bool(EVIDENCE_RE.search(joined))
        checks.append((fix_name, ok,
                       "" if ok else "no commit sha, file:line, test id or CI link found"))

    # Prevention: each item carries an owner AND a completion check.
    own_name = "prevention: every item has an owner"
    if "Prevention" not in found:
        checks.append((own_name, False, "no Prevention section to check"))
    else:
        boxes = [(n, line) for n, line in found["Prevention"]
                 if CHECKBOX_RE.match(line)]
        if not boxes:
            checks.append((own_name, False, "no checkbox items to check"))
        else:
            bare = [n for n, line in boxes if not OWNER_RE.search(line)]
            detail = "%d/%d items have an owner" % (len(boxes) - len(bare), len(boxes))
            if bare:
                detail += ("; missing on line(s) %s (add `@owner` or `owner:`)"
                           % ", ".join(str(n) for n in bare))
            checks.append((own_name, not bare, detail))

    # Blameless advisory: warn, never fail.
    for lineno, line in vlines:
        m = BLAME_RE.search(line)
        if m:
            warnings.append((lineno, m.group(0)))

    return checks, warnings


def run(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        print("POSTMORTEM_LINT: FAIL (cannot read %s: %s)" % (path, exc))
        return 2
    checks, warnings = lint_text(text)
    for name, ok, detail in checks:
        suffix = " (%s)" % detail if detail else ""
        print("LINT: %s - %s%s" % (name, "PASS" if ok else "FAIL", suffix))
    for lineno, phrase in warnings:
        print("WARN: blame-y phrasing on line %d: %r "
              "- name the systemic cause, not a person" % (lineno, phrase))
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    verdict = "PASS" if passed == total else "FAIL"
    print("POSTMORTEM_LINT: %s (%d/%d checks)" % (verdict, passed, total))
    return 0 if passed == total else 1


def main(argv):
    if len(argv) != 2:
        print("usage: postmortem_lint.py <postmortem.md>", file=sys.stderr)
        return 2
    return run(argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
