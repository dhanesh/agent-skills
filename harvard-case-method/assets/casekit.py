#!/usr/bin/env python3
"""casekit.py — case-method reasoning and calibration scoring for real decisions.

Two modes, one record format.

  LIVE (default) — a business or product decision you actually have to make.
  There is no outcome to hide: the future hasn't happened. The discipline is
  a complete decision record (Stanford's Decision Quality chain: Frame,
  Alternatives, Information, Values, Reasoning, Commitment), a premortem, and
  dated probability predictions that resolve in real calendar time. Those
  resolutions are what turn one decision into a calibration profile.

  DRILL (--drill) — a historical case with a known ending, used for fast reps.
  Here the outcome DOES need hiding, so `seal`/`reveal` apply and the brief is
  linted for leaked hindsight.

  new       scaffold a decision (brief.md + decision.md)
  lint      grade the brief: sourced figures, a real option set, a base rate
  commit    validate the decision record against the DQ chain, fingerprint it
  resolve   record how a prediction actually turned out
  score     Brier score and calibration for one decision
  profile   the same across every decision — the trainer's actual output
  status    where a decision stands
  seal/reveal   drill mode only: withhold a known ending until a commit exists

What this tool does NOT do is judge the quality of your reasoning. It checks
completeness and scores predictions, both of which are mechanical. Which link
of the chain was weakest is the coach's call, not a regex's.

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
LIVE, DRILL = "live", "drill"

BRIEF_MD, DECISION_MD, REVEAL_MD = "brief.md", "decision.md", "reveal.md"
SEALED_FILE, STATE_JSON = "reveal.sealed", "state.json"

# Sections that are authorial metadata rather than the case itself.
EXEMPT_SECTIONS = {"sources", "coach notes"}

# The Decision Quality chain (Howard/Spetzler) plus the two disciplines that
# earn their place on evidence: a premortem, and a stated falsifier.
# Commitment is split into the call itself and what would reverse it.
DECISION_SECTIONS = [
    "frame",                  # DQ: Frame
    "alternatives considered",  # DQ: Alternatives
    "values",                 # DQ: Values
    "reasoning",              # DQ: Reasoning
    "premortem",
    "decision",               # DQ: Commitment
    "falsifier",
    "predictions",
]

BRIEF_SECTIONS = ["the decision", "situation", "options on the table",
                  "evidence", "reference class", "open uncertainties"]

HINDSIGHT_PHRASES = [
    "turned out to be right", "turned out to be wrong", "went on to",
    "eventually became", "ultimately succeeded", "ultimately failed",
    "in hindsight", "with hindsight", "looking back", "we now know",
    "today, the company", "today the company", "would later become",
    "as history shows", "the rest is history", "what made this work",
]

PLACEHOLDER_PATTERNS = [re.compile(r"\bTODO\b"), re.compile(r"\bFIXME\b"),
                        re.compile(r"<[A-Z][A-Z_ ]{2,}>")]

CITATION = re.compile(r"\[\^[^\]]+\]|\(src:[^)]+\)")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")

FIGURE = re.compile(
    r"[$₹€£]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|billion|crore|lakh|bn|mn))?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:%|x\b|million|billion|crore|lakh|bn|mn)"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"
    r"|\b\d{3,}(?:\.\d+)?\b",
    re.IGNORECASE,
)

# A numeral that identifies rather than measures — "Section 230", "ISO 27001".
IDENTIFIER_PREFIX = re.compile(
    r"(?:section|rule|article|chapter|clause|part|form|schedule|exhibit|figure|"
    r"table|no\.?|#|version|v\.?|iso|rfc|ieee|ansi|gaap|ias|ifrs)\s*$",
    re.IGNORECASE,
)

# "- 2026-10-31 | 0.70 | Weekly active merchants exceed 500"
PREDICTION = re.compile(
    r"^\s*[-*]\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([01]?\.\d+|[01])\s*\|\s*(\S.*?)\s*$")

MIN_ALTERNATIVES = 3   # two options is a whether-or-not trap, not a choice
MIN_REFERENCE_CLASS = 2  # one comparator is an anecdote, not a base rate


class CaseError(Exception):
    """A user-facing failure: one clean line, never a traceback."""


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
        raise CaseError("no decision %r under %s (run `new` first)" % (slug, root))
    try:
        return json.loads(read(path))
    except (ValueError, OSError) as exc:
        raise CaseError("decision %r has an unreadable %s: %s" % (slug, STATE_JSON, exc))


def save_state(root, slug, state):
    write(os.path.join(case_dir(root, slug), STATE_JSON),
          json.dumps(state, indent=2, sort_keys=True) + "\n")


def all_slugs(root):
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root)
                  if os.path.exists(os.path.join(root, d, STATE_JSON)))


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── markdown sectioning ──────────────────────────────────────────────────────

def sections(text):
    """[(heading_lower, [(lineno, line), ...])]; pre-heading content under ''."""
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


def section_body(text, name):
    for heading, body in sections(text):
        if heading == name.lower():
            return body
    return None


def bullets(text, name):
    return [l.strip().lstrip("-*").strip()
            for _, l in (section_body(text, name) or [])
            if l.strip().startswith(("-", "*")) and l.strip().lstrip("-*").strip()]


def narrative_lines(text):
    lines = []
    for name, body in sections(text):
        if name in EXEMPT_SECTIONS:
            continue
        for lineno, line in body:
            if line.strip().startswith("<!--"):
                continue
            lines.append((lineno, line))
    return lines


def unsourced_figures(text):
    out = []
    for n, line in narrative_lines(text):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or CITATION.search(line):
            continue
        if PREDICTION.match(line):        # a forecast is not an evidentiary claim
            continue
        scrubbed = YEAR.sub(" ", line)
        for m in FIGURE.finditer(scrubbed):
            if IDENTIFIER_PREFIX.search(scrubbed[:m.start()]):
                continue
            out.append("line %d: %s" % (n, m.group(0).strip()))
            break
    return out


# ── lint (the brief) ─────────────────────────────────────────────────────────

def lint_brief(text, mode, decision_date=None):
    """[(rule, ok, detail)] — is the brief a decidable case, or a story?"""
    results = []
    body = narrative_lines(text)

    hits = ["line %d: %s" % (n, m.group(0))
            for n, line in body for p in PLACEHOLDER_PATTERNS
            for m in [p.search(line)] if m]
    results.append(("B0 no-scaffold-residue", not hits, "; ".join(hits[:4])))

    missing = [s for s in BRIEF_SECTIONS if section_body(text, s) is None]
    results.append(("B1 has-every-section", not missing,
                    "missing: %s" % ", ".join("## " + m.title() for m in missing) if missing else ""))

    # The question need not end the line — "…flat fee? Decide by 2026-09-30." is
    # the natural phrasing, and requiring a trailing '?' rejected it.
    decision = section_body(text, "the decision")
    has_q = bool(decision) and any("?" in l for _, l in decision)
    has_date = bool(decision) and any(ISO_DATE.search(l) for _, l in decision)
    results.append(("B2 decision-is-a-dated-question", has_q and has_date,
                    "" if has_q and has_date else
                    "needs a question mark and a decide-by date (YYYY-MM-DD)"))

    options = bullets(text, "options on the table")
    results.append(("B3 real-option-set", len(options) >= MIN_ALTERNATIVES,
                    "%d option(s); %d+ required — two is a whether-or-not trap"
                    % (len(options), MIN_ALTERNATIVES)
                    if len(options) < MIN_ALTERNATIVES else ""))

    figs = unsourced_figures(text)
    results.append(("B4 figures-are-sourced", not figs, "; ".join(figs[:4])))

    refs = bullets(text, "reference class")
    results.append(("B5 has-a-base-rate", len(refs) >= MIN_REFERENCE_CLASS,
                    "%d entr(y/ies); %d+ required — one comparator is an anecdote"
                    % (len(refs), MIN_REFERENCE_CLASS)
                    if len(refs) < MIN_REFERENCE_CLASS else ""))

    unc = [l for _, l in (section_body(text, "open uncertainties") or []) if l.strip()]
    results.append(("B6 names-what-is-unknown", bool(unc),
                    "" if unc else "'## Open Uncertainties' is empty — "
                                   "a decision with no uncertainty needs no case"))

    if mode == DRILL:
        # Only a drill case has an ending that could leak.
        leaks, lowered = [], [(n, l.lower()) for n, l in body]
        for n, line in lowered:
            for phrase in HINDSIGHT_PHRASES:
                if phrase in line:
                    leaks.append("line %d: %r" % (n, phrase))
        results.append(("D1 no-hindsight-language", not leaks, "; ".join(leaks[:4])))

        future = []
        if decision_date:
            year = int(decision_date[:4])
            for n, line in body:
                for m in ISO_DATE.finditer(line):
                    if m.group(0) > decision_date:
                        future.append("line %d: %s" % (n, m.group(0)))
                for m in YEAR.finditer(ISO_DATE.sub(" ", line)):
                    if int(m.group(0)) > year:
                        future.append("line %d: %s" % (n, m.group(0)))
        results.append(("D2 no-future-dates", not future,
                        "; ".join(sorted(set(future))[:4])))
    return results


# ── the decision record ──────────────────────────────────────────────────────

def parse_predictions(text):
    """([{date, p, claim}], [error, ...]) from the ## Predictions section."""
    preds, errors = [], []
    for lineno, line in (section_body(text, "predictions") or []):
        if not line.strip() or not line.strip().startswith(("-", "*")):
            continue
        m = PREDICTION.match(line)
        if not m:
            errors.append("line %d: expected `- YYYY-MM-DD | 0.NN | claim`" % lineno)
            continue
        date, raw, claim = m.group(1), m.group(2), m.group(3)
        p = float(raw)
        if not 0 < p < 1:
            errors.append("line %d: probability %s — use a value strictly between "
                          "0 and 1; certainty is not a forecast" % (lineno, raw))
            continue
        preds.append({"date": date, "p": p, "claim": claim})
    return preds, errors


def check_decision(text):
    """[] if the record is complete and well-formed, else every problem found."""
    problems = []
    present = {name for name, _ in sections(text)}
    missing = [s for s in DECISION_SECTIONS if s not in present]
    if missing:
        problems.append("missing sections: %s" % ", ".join("## " + m.title() for m in missing))

    residue = ["line %d: %s" % (n, m.group(0))
               for n, line in enumerate(text.splitlines(), 1)
               for p in PLACEHOLDER_PATTERNS for m in [p.search(line)] if m]
    if residue:
        problems.append("unfilled scaffold: %s" % "; ".join(residue[:4]))

    if "alternatives considered" in present:
        alts = bullets(text, "alternatives considered")
        if len(alts) < MIN_ALTERNATIVES:
            problems.append("only %d alternative(s) considered; %d+ required — "
                            "the option set is where most decisions are lost"
                            % (len(alts), MIN_ALTERNATIVES))

    if "predictions" in present:
        preds, errors = parse_predictions(text)
        problems.extend(errors)
        if not preds:
            problems.append("no well-formed predictions — a decision with nothing "
                            "falsifiable cannot be scored later")
    return problems


# ── scoring ──────────────────────────────────────────────────────────────────

def brier(pairs):
    """Mean squared error of probabilistic forecasts. 0 perfect, 0.25 coin flip."""
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def calibration_bins(pairs, width=0.1):
    """[(low, high, n, mean_p, hit_rate)] over non-empty deciles."""
    buckets = {}
    for p, o in pairs:
        idx = min(int(p / width), int(1 / width) - 1)
        buckets.setdefault(idx, []).append((p, o))
    out = []
    for idx in sorted(buckets):
        vals = buckets[idx]
        out.append((idx * width, (idx + 1) * width, len(vals),
                    sum(p for p, _ in vals) / len(vals),
                    sum(o for _, o in vals) / len(vals)))
    return out


def resolved_pairs(state):
    """[(probability, outcome)] for predictions that have been resolved."""
    res = state.get("resolutions", {})
    return [(pred["p"], 1.0 if res[str(i)] else 0.0)
            for i, pred in enumerate(state.get("predictions", []), 1)
            if str(i) in res]


def format_score(state, label):
    pairs = resolved_pairs(state)
    total = len(state.get("predictions", []))
    lines = ["%s" % label,
             "  predictions:     %d recorded, %d resolved" % (total, len(pairs))]
    if not pairs:
        lines.append("  (nothing resolved yet — `resolve` them as the dates arrive)")
        return lines, None
    score = brier(pairs)
    mean_p = sum(p for p, _ in pairs) / len(pairs)
    hit = sum(o for _, o in pairs) / len(pairs)
    lines += [
        "  Brier score:     %.4f   (0.25 = coin flip, lower is better)" % score,
        "  mean confidence: %.3f" % mean_p,
        "  hit rate:        %.3f" % hit,
        "  calibration gap: %+.3f  (%s)" % (
            mean_p - hit,
            "over-confident" if mean_p - hit > 0.05 else
            "under-confident" if mean_p - hit < -0.05 else "well calibrated"),
        "  bins:",
    ]
    for lo, hi, n, mp, hr in calibration_bins(pairs):
        lines.append("    %.1f–%.1f  n=%-3d  said %.2f  actual %.2f"
                     % (lo, hi, n, mp, hr))
    return lines, score


# ── scaffolds ────────────────────────────────────────────────────────────────

def brief_scaffold(question, deadline, mode):
    hint = ("<!-- DRILL: everything below must be knowable on the decision date. -->"
            if mode == DRILL else
            "<!-- The agent assembles this: research, base rates, sourcing. -->")
    return """# Decision brief

%s

## The Decision

TODO: %s Decide by %s.

## Situation

TODO: what is happening, and why the decision is live now.

## Options on the Table

- TODO: option one
- TODO: option two
- TODO: option three (fewer than three is a whether-or-not trap)

## Evidence

TODO: what is actually known, each figure carrying a citation like [^1].

## Reference Class

- TODO: a comparable case and how it went
- TODO: a second one, including at least one that failed

## Open Uncertainties

TODO: what nobody can know yet. This is why the decision is hard.

## Sources

[^1]: TODO url
""" % (hint, question, deadline)


DECISION_SCAFFOLD = """# Decision record

<!-- You own this file. The agent may argue with it; it must not write it. -->

## Frame

TODO: the decision you are actually making, and what is out of scope.

## Alternatives Considered

- TODO: including at least one not offered in the brief
- TODO
- TODO

## Values

TODO: what you are optimising for, and the trade-off you will accept.

## Reasoning

TODO: why your choice beats each alternative above.

## Premortem

TODO: it is the resolution date and this decision failed. Write the story of
how — as fact, not possibility. Certainty is what makes this work.

## Decision

TODO: the call, stated as an action with an owner and a date.

## Falsifier

TODO: the observation that would tell you this was wrong, and by when.

## Predictions

- TODO-DATE | 0.70 | TODO a claim that will be plainly true or false by then
"""

REVEAL_SCAFFOLD = """# Reveal (drill mode)

## What They Decided

TODO

## What Happened

TODO — including what went wrong, and what luck contributed.

## What Was Unknowable

TODO

## Sources

[^1]: TODO url
"""


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_new(args):
    d = case_dir(args.root, args.slug)
    if os.path.exists(os.path.join(d, STATE_JSON)):
        raise CaseError("decision %r already exists at %s" % (args.slug, d))
    if not ISO_DATE.fullmatch(args.by):
        raise CaseError("--by must be YYYY-MM-DD, got %r" % args.by)
    mode = DRILL if args.drill else LIVE
    write(os.path.join(d, BRIEF_MD), brief_scaffold(args.decision, args.by, mode))
    write(os.path.join(d, DECISION_MD), DECISION_SCAFFOLD)
    state = {"slug": args.slug, "decision": args.decision, "deadline": args.by,
             "mode": mode, "stage": DRAFTING, "predictions": [], "resolutions": {}}
    if mode == DRILL:
        write(os.path.join(d, REVEAL_MD), REVEAL_SCAFFOLD)
        state["decision_date"] = args.by
    save_state(args.root, args.slug, state)
    print("Created %s  (%s mode)" % (d, mode))
    print("  %-12s the case — agent-assembled, then `lint`" % BRIEF_MD)
    print("  %-12s your reasoning and predictions — then `commit`" % DECISION_MD)
    if mode == DRILL:
        print("  %-12s the known ending — `seal` it before discussing" % REVEAL_MD)
    return 0


def cmd_lint(args):
    state = load_state(args.root, args.slug)
    path = os.path.join(case_dir(args.root, args.slug), BRIEF_MD)
    if not os.path.exists(path):
        raise CaseError("decision %r has no %s" % (args.slug, BRIEF_MD))
    results = lint_brief(read(path), state.get("mode", LIVE), state.get("decision_date"))
    for rule, ok, detail in results:
        print("LINT: %s — %s%s" % (rule, "PASS" if ok else "FAIL",
                                   " (%s)" % detail if detail else ""))
    failed = [r for r, ok, _ in results if not ok]
    print("LINT_RESULT: %s (%d/%d rules)"
          % ("PASS" if not failed else "FAIL", len(results) - len(failed), len(results)))
    return 0 if not failed else 1


def cmd_commit(args):
    state = load_state(args.root, args.slug)
    d = case_dir(args.root, args.slug)
    if state.get("mode") == DRILL and state["stage"] == DRAFTING:
        raise CaseError("drill mode: seal the reveal before committing a decision")
    if state["stage"] in (COMMITTED, REVEALED):
        raise CaseError("decision %r is already committed" % args.slug)
    path = args.file or os.path.join(d, DECISION_MD)
    if not os.path.exists(path):
        raise CaseError("no decision record at %s" % path)
    text = read(path)
    problems = check_decision(text)
    if problems:
        raise CaseError("decision record is incomplete:\n  - " + "\n  - ".join(problems))
    preds, _ = parse_predictions(text)
    state.update(stage=COMMITTED, decision_sha256=sha256(text), predictions=preds)
    save_state(args.root, args.slug, state)
    print("Committed %r (sha256 %s…) with %d prediction(s):"
          % (args.slug, state["decision_sha256"][:12], len(preds)))
    for i, p in enumerate(preds, 1):
        print("  %d. [%s] p=%.2f  %s" % (i, p["date"], p["p"], p["claim"]))
    print("Resolve them as the dates arrive: casekit.py resolve %s --n 1 --outcome yes"
          % args.slug)
    return 0


def cmd_resolve(args):
    state = load_state(args.root, args.slug)
    preds = state.get("predictions", [])
    if state["stage"] not in (COMMITTED, REVEALED):
        raise CaseError("commit the decision before resolving its predictions")
    if not 1 <= args.n <= len(preds):
        raise CaseError("prediction %d does not exist (%d recorded)" % (args.n, len(preds)))
    key = str(args.n)
    outcome = args.outcome == "yes"
    prior = state.setdefault("resolutions", {}).get(key)
    if prior is not None and prior != outcome and not args.force:
        raise CaseError("prediction %d is already resolved as %s — pass --force to "
                        "overwrite (and say why in the debrief)"
                        % (args.n, "yes" if prior else "no"))
    state["resolutions"][key] = outcome
    save_state(args.root, args.slug, state)
    p = preds[args.n - 1]
    print("Resolved %d as %s: said p=%.2f — %s"
          % (args.n, args.outcome.upper(), p["p"], p["claim"]))
    return 0


def cmd_score(args):
    state = load_state(args.root, args.slug)
    lines, _ = format_score(state, "%s — %s" % (state["slug"], state["decision"]))
    print("\n".join(lines))
    print("\nWhich link of the chain was weakest (frame, alternatives, information,")
    print("values, reasoning, commitment) is the coach's judgement, not this tool's.")
    return 0


def cmd_profile(args):
    slugs = all_slugs(args.root)
    if not slugs:
        raise CaseError("no decisions under %s" % args.root)
    every, blocks = [], []
    for slug in slugs:
        state = load_state(args.root, slug)
        lines, _ = format_score(state, "%s (%s)" % (slug, state.get("mode", LIVE)))
        blocks.append("\n".join(lines))
        every.extend(resolved_pairs(state))
    print("\n\n".join(blocks))
    print("\n" + "=" * 62)
    print("ACROSS %d DECISION(S): %d resolved prediction(s)" % (len(slugs), len(every)))
    if not every:
        print("No calibration profile yet. One decision is an anecdote; the profile")
        print("is the trainer's actual output, and it needs resolved forecasts.")
        return 0
    mean_p = sum(p for p, _ in every) / len(every)
    hit = sum(o for _, o in every) / len(every)
    print("  Brier score:     %.4f   (0.25 = coin flip, lower is better)" % brier(every))
    print("  mean confidence: %.3f" % mean_p)
    print("  hit rate:        %.3f" % hit)
    print("  calibration gap: %+.3f  (%s)" % (
        mean_p - hit,
        "over-confident" if mean_p - hit > 0.05 else
        "under-confident" if mean_p - hit < -0.05 else "well calibrated"))
    for lo, hi, n, mp, hr in calibration_bins(every):
        print("    %.1f–%.1f  n=%-3d  said %.2f  actual %.2f" % (lo, hi, n, mp, hr))
    if len(every) < 10:
        print("  (fewer than 10 resolved forecasts — treat these numbers as "
              "directional, not a calibration verdict)")
    return 0


def cmd_seal(args):
    state = load_state(args.root, args.slug)
    d = case_dir(args.root, args.slug)
    if state.get("mode") != DRILL:
        raise CaseError("seal applies to drill cases only; %r is a live decision "
                        "with no known ending to hide" % args.slug)
    if state["stage"] != DRAFTING:
        raise CaseError("decision %r is already %s" % (args.slug, state["stage"]))
    if not os.path.exists(os.path.join(d, REVEAL_MD)):
        raise CaseError("decision %r has no %s to seal" % (args.slug, REVEAL_MD))
    if not args.skip_lint:
        results = lint_brief(read(os.path.join(d, BRIEF_MD)), DRILL,
                             state.get("decision_date"))
        failed = [r for r, ok, _ in results if not ok]
        if failed:
            raise CaseError("brief fails lint (%s) — repair it or pass --skip-lint"
                            % ", ".join(failed))
    text = read(os.path.join(d, REVEAL_MD))
    write(os.path.join(d, SEALED_FILE),
          base64.b64encode(text.encode("utf-8")).decode("ascii") + "\n")
    os.remove(os.path.join(d, REVEAL_MD))
    state.update(stage=SEALED, reveal_sha256=sha256(text))
    save_state(args.root, args.slug, state)
    print("Sealed %s → %s (a speed bump, not encryption)" % (REVEAL_MD, SEALED_FILE))
    return 0


def cmd_reveal(args):
    state = load_state(args.root, args.slug)
    d = case_dir(args.root, args.slug)
    if state.get("mode") != DRILL:
        raise CaseError("reveal applies to drill cases only; %r is a live decision "
                        "and its outcome has not happened yet" % args.slug)
    if state["stage"] in (DRAFTING, SEALED):
        raise CaseError("commit a decision before revealing — that ordering is the "
                        "whole point of a drill (stage: %s)" % state["stage"])
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


NEXT_STEP = {
    (LIVE, DRAFTING): "assemble brief.md, `lint` it, then write decision.md and `commit`",
    (LIVE, COMMITTED): "act on it; `resolve` each prediction as its date arrives, then `score`",
    (DRILL, DRAFTING): "write brief.md and reveal.md, `lint`, then `seal`",
    (DRILL, SEALED): "reason through the brief, write decision.md, then `commit`",
    (DRILL, COMMITTED): "`reveal` the ending and debrief",
    (DRILL, REVEALED): "`resolve` the predictions against the reveal, then `score`",
}


def cmd_status(args):
    state = load_state(args.root, args.slug)
    mode, stage = state.get("mode", LIVE), state["stage"]
    print("slug:      %s" % state["slug"])
    print("decision:  %s" % state["decision"])
    print("decide by: %s" % state["deadline"])
    print("mode:      %s" % mode.upper())
    print("stage:     %s" % stage.upper())
    preds, res = state.get("predictions", []), state.get("resolutions", {})
    print("forecasts: %d recorded, %d resolved" % (len(preds), len(res)))
    print("next:      %s" % NEXT_STEP.get((mode, stage), "—"))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="casekit.py", description=__doc__.split("\n")[0])
    p.add_argument("--root", default="decisions", help="decision root (default: decisions)")
    sub = p.add_subparsers(dest="command")

    n = sub.add_parser("new", help="scaffold a decision")
    n.add_argument("slug")
    n.add_argument("--decision", required=True, help="the question, in one line")
    n.add_argument("--by", required=True, metavar="YYYY-MM-DD", help="decide-by date")
    n.add_argument("--drill", action="store_true",
                   help="historical case with a known ending (enables seal/reveal)")
    n.set_defaults(func=cmd_new)

    for name, fn, helptext in (
        ("lint", cmd_lint, "grade the brief"),
        ("score", cmd_score, "Brier score and calibration for one decision"),
        ("status", cmd_status, "show where a decision stands"),
        ("reveal", cmd_reveal, "drill only: unseal the ending (requires a commit)"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("slug")
        s.set_defaults(func=fn)

    c = sub.add_parser("commit", help="validate and fingerprint the decision record")
    c.add_argument("slug")
    c.add_argument("--file", help="decision record (default: decision.md)")
    c.set_defaults(func=cmd_commit)

    r = sub.add_parser("resolve", help="record how a prediction turned out")
    r.add_argument("slug")
    r.add_argument("--n", type=int, required=True, help="prediction number (1-based)")
    r.add_argument("--outcome", required=True, choices=["yes", "no"])
    r.add_argument("--force", action="store_true", help="overwrite a prior resolution")
    r.set_defaults(func=cmd_resolve)

    s = sub.add_parser("seal", help="drill only: hide the known ending")
    s.add_argument("slug")
    s.add_argument("--skip-lint", action="store_true")
    s.set_defaults(func=cmd_seal)

    pr = sub.add_parser("profile", help="calibration across every decision")
    pr.set_defaults(func=cmd_profile)
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
