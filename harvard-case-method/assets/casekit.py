#!/usr/bin/env python3
"""casekit.py — decision-forcing case files for the Harvard case method.

The HBS case method's central device is that the *outcome is withheld*: a case
presents only what was knowable at a decision date, the learner commits to a
decision in the protagonist's shoes, and only then is the outcome revealed.
An AI-written "case study" defaults to the opposite — it narrates the whole
story with the ending known, which is a post-mortem, not a case.

This tool makes the withholding mechanical:

  new     scaffold a case directory pinned to a decision date
  lint    refuse a case file that leaks the future into the A-case
  seal    hide the reveal (B-case) behind a speed bump
  commit  record the learner's decision — required before revealing
  reveal  unseal the outcome, only after a decision is on record
  status  where a case stands

Sealing is base64, NOT encryption: it stops accidental reading and enforces
the workflow's ordering, and anyone who wants to peek can. The honest guard
is the commit-before-reveal ordering, which is recorded in state.json.

stdlib only, offline, no wall-clock or randomness in recorded state.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys

DRAFTING, SEALED, COMMITTED, REVEALED = "drafting", "sealed", "committed", "revealed"

CASE_MD, REVEAL_MD, SEALED_FILE = "case.md", "reveal.md", "reveal.sealed"
DECISION_MD, STATE_JSON = "decision.md", "state.json"

# Sections whose content is authorial metadata, not narrative told to the
# learner. Citing a retrospective source is normal; narrating its contents in
# the A-case is the leak. L1/L2/L4 skip these.
EXEMPT_SECTIONS = {"sources", "facilitator notes"}

# L2 — phrases that only make sense once the ending is known.
HINDSIGHT_PHRASES = [
    "turned out to be right", "turned out to be wrong", "went on to",
    "eventually became", "ultimately succeeded", "ultimately failed",
    "in hindsight", "with hindsight", "looking back", "we now know",
    "today, the company", "today the company", "the company would later",
    "would later become", "as history shows", "proved to be correct",
    "the rest is history", "what made this work",
]

# L0 — scaffold residue.
PLACEHOLDER_PATTERNS = [
    re.compile(r"\bTODO\b"), re.compile(r"\bFIXME\b"),
    re.compile(r"<[A-Z][A-Z_ ]{2,}>"),
]

CITATION = re.compile(r"\[\^[^\]]+\]|\(src:[^)]+\)")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
FIGURE = re.compile(
    r"[$₹€£]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|billion|crore|lakh|bn|mn))?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:%|x\b|million|billion|crore|lakh|bn|mn)"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"   # comma-grouped, matched whole
    r"|\b\d{3,}(?:\.\d+)?\b",
    re.IGNORECASE,
)

# A numeral that identifies rather than measures — "Section 230", "RFC 2119",
# "ISO 27001", "Rule 144A". Flagging these as unsourced figures is noise, and
# noise is what pushes an author to --skip-lint, which costs more than it saves.
IDENTIFIER_PREFIX = re.compile(
    r"(?:section|rule|article|chapter|clause|part|form|schedule|exhibit|figure|"
    r"table|no\.?|#|version|v\.?|iso|rfc|ieee|ansi|gaap|ias|ifrs)\s*$",
    re.IGNORECASE,
)

DECISION_SECTIONS = ["decision", "reasoning", "disconfirming evidence",
                     "what would change my mind"]


class CaseError(Exception):
    """A user-facing failure: printed as one clean line, never a traceback."""


# ── file/state helpers ───────────────────────────────────────────────────────

def case_dir(root, slug):
    return os.path.join(root, slug)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def load_state(root, slug):
    path = os.path.join(case_dir(root, slug), STATE_JSON)
    if not os.path.exists(path):
        raise CaseError("no case %r under %s (run `new` first)" % (slug, root))
    try:
        return json.loads(read(path))
    except (ValueError, OSError) as exc:
        raise CaseError("case %r has an unreadable %s: %s" % (slug, STATE_JSON, exc))


def save_state(root, slug, state):
    write(os.path.join(case_dir(root, slug), STATE_JSON),
          json.dumps(state, indent=2, sort_keys=True) + "\n")


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── markdown sectioning ──────────────────────────────────────────────────────

def sections(text):
    """[(heading_lower, [(lineno, line), ...])] — content before any heading
    lands under ''."""
    out, current, buf = [], "", []
    for i, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            out.append((current, buf))
            current, buf = m.group(1).strip().lower(), []
        else:
            buf.append((i, line))
    out.append((current, buf))
    return out


def narrative_lines(text):
    """Lines the learner reads as the case, minus exempt sections and comments."""
    lines = []
    for name, body in sections(text):
        if name in EXEMPT_SECTIONS:
            continue
        for lineno, line in body:
            if line.strip().startswith("<!--"):
                continue
            lines.append((lineno, line))
    return lines


def section_body(text, name):
    for heading, body in sections(text):
        if heading == name.lower():
            return body
    return None


# ── lint ─────────────────────────────────────────────────────────────────────

def lint_case(case_text, decision_date):
    """[(rule, ok, detail)] — the A-case's fitness to be a case, not a story."""
    results = []
    body = narrative_lines(case_text)
    decision_year = int(decision_date[:4])

    hits = ["line %d: %s" % (n, m.group(0))
            for n, line in body for p in PLACEHOLDER_PATTERNS
            for m in [p.search(line)] if m]
    results.append(("L0 no-scaffold-residue", not hits, "; ".join(hits[:4])))

    future = []
    for n, line in body:
        for m in ISO_DATE.finditer(line):
            if m.group(0) > decision_date:
                future.append("line %d: %s" % (n, m.group(0)))
        # Bare years are compared by year alone — a same-year mention could be
        # either side of the decision date, so only strictly-later years fail.
        for m in YEAR.finditer(ISO_DATE.sub(" ", line)):
            if int(m.group(0)) > decision_year:
                future.append("line %d: %s" % (n, m.group(0)))
    results.append(("L1 no-future-dates", not future,
                    "post-%s: %s" % (decision_date, "; ".join(sorted(set(future))[:4]))
                    if future else ""))

    lowered = [(n, line.lower()) for n, line in body]
    leaks = ["line %d: %r" % (n, phrase)
             for n, line in lowered for phrase in HINDSIGHT_PHRASES if phrase in line]
    results.append(("L2 no-hindsight-language", not leaks, "; ".join(leaks[:4])))

    decision = section_body(case_text, "the decision")
    has_q = bool(decision) and any(l.strip().endswith("?") for _, l in decision)
    results.append(("L3 ends-in-a-decision", has_q,
                    "missing '## The Decision' section" if decision is None
                    else "" if has_q else "section asks no question"))

    unsourced = []
    for n, line in body:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or CITATION.search(line):
            continue
        scrubbed = YEAR.sub(" ", line)
        for m in FIGURE.finditer(scrubbed):
            if IDENTIFIER_PREFIX.search(scrubbed[:m.start()]):
                continue
            unsourced.append("line %d: %s" % (n, m.group(0).strip()))
            break
    results.append(("L4 figures-are-sourced", not unsourced, "; ".join(unsourced[:4])))

    comparators = section_body(case_text, "comparators")
    entries = [l for _, l in (comparators or []) if l.strip().startswith(("-", "*"))]
    results.append(("L5 names-comparators", len(entries) >= 1,
                    "missing '## Comparators' section" if comparators is None
                    else "" if entries else "section lists no comparator"))

    return results


def check_decision_file(text):
    """[] if the decision states everything a commit must state, else the gaps."""
    present = {name for name, _ in sections(text)}
    return [s for s in DECISION_SECTIONS if s not in present]


# ── scaffolds ────────────────────────────────────────────────────────────────

def case_scaffold(company, decision_date):
    return """# %s — case as of %s

<!-- Everything below must be knowable on %s. Nothing after it. -->

## The Situation

TODO: the market, the customer problem, the incumbents — as of the decision date.

## The Protagonist

TODO: who decides, what they control, what they are measured on.

## What Is Known

TODO: the facts on the table, each with a citation marker like [^1].

## What Is Uncertain

TODO: what nobody could know yet. This section is why the case is hard.

## Comparators

- TODO: another company that faced this situation (include one that failed).

## The Decision

TODO: state the choice, then ask it. What should <PROTAGONIST> do?

## Sources

[^1]: TODO url
""" % (company, decision_date, decision_date)


REVEAL_SCAFFOLD = """# Reveal (B-case)

## What They Decided

TODO

## What Happened

TODO — including what went wrong, and what luck contributed.

## What Was Unknowable

TODO — the parts that were a coin flip at the decision date.

## Sources

[^1]: TODO url
"""

DECISION_SCAFFOLD = """# My decision

## Decision

TODO: the call, stated as an action.

## Reasoning

TODO

## Disconfirming evidence

TODO: the strongest case against this call.

## What would change my mind

TODO: the observation that would flip it.
"""


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_new(args):
    d = case_dir(args.root, args.slug)
    if os.path.exists(os.path.join(d, STATE_JSON)):
        raise CaseError("case %r already exists at %s" % (args.slug, d))
    if not ISO_DATE.fullmatch(args.decision_date):
        raise CaseError("--decision-date must be YYYY-MM-DD, got %r" % args.decision_date)
    write(os.path.join(d, CASE_MD), case_scaffold(args.company, args.decision_date))
    write(os.path.join(d, REVEAL_MD), REVEAL_SCAFFOLD)
    write(os.path.join(d, DECISION_MD), DECISION_SCAFFOLD)
    save_state(args.root, args.slug, {
        "slug": args.slug, "company": args.company,
        "decision_date": args.decision_date, "stage": DRAFTING,
    })
    print("Created %s" % d)
    print("  %-12s the A-case — write it, then `lint`" % CASE_MD)
    print("  %-12s the B-case — `seal` it before the learner reads" % REVEAL_MD)
    print("  %-12s the learner's call — `commit` it before `reveal`" % DECISION_MD)
    return 0


def cmd_lint(args):
    state = load_state(args.root, args.slug)
    path = os.path.join(case_dir(args.root, args.slug), CASE_MD)
    if not os.path.exists(path):
        raise CaseError("case %r has no %s" % (args.slug, CASE_MD))
    results = lint_case(read(path), state["decision_date"])
    for rule, ok, detail in results:
        print("LINT: %s — %s%s" % (rule, "PASS" if ok else "FAIL",
                                   " (%s)" % detail if detail else ""))
    failed = [r for r, ok, _ in results if not ok]
    print("LINT_RESULT: %s (%d/%d rules)"
          % ("PASS" if not failed else "FAIL", len(results) - len(failed), len(results)))
    return 0 if not failed else 1


def cmd_seal(args):
    state = load_state(args.root, args.slug)
    d = case_dir(args.root, args.slug)
    if state["stage"] != DRAFTING:
        raise CaseError("case %r is already %s" % (args.slug, state["stage"]))
    if not os.path.exists(os.path.join(d, REVEAL_MD)):
        raise CaseError("case %r has no %s to seal" % (args.slug, REVEAL_MD))
    if not args.skip_lint:
        results = lint_case(read(os.path.join(d, CASE_MD)), state["decision_date"])
        failed = [r for r, ok, _ in results if not ok]
        if failed:
            raise CaseError("A-case fails lint (%s) — repair it or pass --skip-lint"
                            % ", ".join(failed))
    text = read(os.path.join(d, REVEAL_MD))
    write(os.path.join(d, SEALED_FILE),
          base64.b64encode(text.encode("utf-8")).decode("ascii") + "\n")
    os.remove(os.path.join(d, REVEAL_MD))
    state.update(stage=SEALED, reveal_sha256=sha256(text))
    save_state(args.root, args.slug, state)
    print("Sealed %s → %s (a speed bump, not encryption)" % (REVEAL_MD, SEALED_FILE))
    return 0


def cmd_commit(args):
    state = load_state(args.root, args.slug)
    d = case_dir(args.root, args.slug)
    if state["stage"] == DRAFTING:
        raise CaseError("seal the reveal before committing a decision")
    if state["stage"] in (COMMITTED, REVEALED):
        raise CaseError("case %r already has a committed decision" % args.slug)
    path = args.file or os.path.join(d, DECISION_MD)
    if not os.path.exists(path):
        raise CaseError("no decision file at %s" % path)
    text = read(path)
    missing = check_decision_file(text)
    if missing:
        raise CaseError("decision is missing required sections: %s"
                        % ", ".join("## " + m for m in missing))
    state.update(stage=COMMITTED, decision_sha256=sha256(text))
    save_state(args.root, args.slug, state)
    print("Committed decision for %r (sha256 %s…)" % (args.slug, state["decision_sha256"][:12]))
    print("The reveal is now available: casekit.py reveal %s" % args.slug)
    return 0


def cmd_reveal(args):
    state = load_state(args.root, args.slug)
    d = case_dir(args.root, args.slug)
    if state["stage"] in (DRAFTING, SEALED):
        raise CaseError("commit a decision before revealing — that ordering is "
                        "the whole method (stage: %s)" % state["stage"])
    blob = read(os.path.join(d, SEALED_FILE))
    try:
        text = base64.b64decode(blob.strip().encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise CaseError("%s is corrupt: %s" % (SEALED_FILE, exc))
    if sha256(text) != state.get("reveal_sha256"):
        raise CaseError("%s does not match the sealed checksum" % SEALED_FILE)
    if state["stage"] != REVEALED:
        state["stage"] = REVEALED
        save_state(args.root, args.slug, state)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def cmd_status(args):
    state = load_state(args.root, args.slug)
    print("slug:          %s" % state["slug"])
    print("company:       %s" % state["company"])
    print("decision date: %s" % state["decision_date"])
    print("stage:         %s" % state["stage"].upper())
    nxt = {DRAFTING: "write case.md + reveal.md, then `lint` and `seal`",
           SEALED: "learner writes decision.md, then `commit`",
           COMMITTED: "`reveal` the B-case and debrief",
           REVEALED: "debrief: score the decision, extract the pattern"}
    print("next:          %s" % nxt[state["stage"]])
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="casekit.py", description=__doc__.split("\n")[0])
    p.add_argument("--root", default="cases", help="case directory root (default: cases)")
    sub = p.add_subparsers(dest="command")

    n = sub.add_parser("new", help="scaffold a case pinned to a decision date")
    n.add_argument("slug")
    n.add_argument("--company", required=True)
    n.add_argument("--decision-date", required=True, metavar="YYYY-MM-DD")
    n.set_defaults(func=cmd_new)

    for name, fn, helptext in (
        ("lint", cmd_lint, "check the A-case for leaked hindsight"),
        ("commit", cmd_commit, "record the learner's decision"),
        ("reveal", cmd_reveal, "unseal the B-case (requires a commit)"),
        ("status", cmd_status, "show where a case stands"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("slug")
        if name == "commit":
            s.add_argument("--file", help="decision file (default: decision.md)")
        s.set_defaults(func=fn)

    s = sub.add_parser("seal", help="hide the B-case behind a speed bump")
    s.add_argument("slug")
    s.add_argument("--skip-lint", action="store_true",
                   help="seal even though the A-case fails lint")
    s.set_defaults(func=cmd_seal)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except CaseError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
