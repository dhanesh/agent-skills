#!/usr/bin/env python3
"""pp5_reasons.py: PP-5, every absolute in a SKILL.md carries a reason.

PP-5 used to count absolutes (never, always, MUST NOT) against hedges ("usually",
"when in doubt", SHOULD, MAY) and flag a skill with too few hedges. Current Claude
guidance draws the line elsewhere: an absolute rule earns its place when it states
why, or encodes a real constraint, and a hedge on a real requirement reads as
permission to under-deliver. So this check no longer counts hedges. It asks each
absolute for a reason:

  - in its own sentence: a reason cue (because, so that, since, otherwise, or else,
    which would, to avoid, to keep, ", so"), or a ": " or " — " after the sentence's
    first absolute followed by a consequence clause;
  - or in the next sentence of the same paragraph, list item or table row, when that
    sentence reads as a consequence (a reason cue, or a consequence cue such as
    "would", "cannot" or "breaks").

Fenced code, inline code and the BCP 14 declaration sentence are exempt; the block
and fence rules are bcp14_registry.py's, so the two gates agree on what is prose.

Usage:
  pp5_reasons.py <SKILL.md>...   one "<file>:<line>: <sentence prefix>" line per
                                 unreasoned absolute; exits 0 either way
  pp5_reasons.py --count <SKILL.md>...   prints PP5_UNREASONED: <n>
  pp5_reasons.py --self-test     asserts the detector's cases; PP5_SELFTEST: PASS|FAIL
stdlib only, offline, deterministic.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bcp14_registry as reg  # noqa: E402  (segments, CODE_SPAN, DECL: one definition of prose)

# An absolutist directive. Case-insensitive, as PP-5 always was: "Never", "always",
# "MUST NOT". Lowercase "must not" is already banned from SKILL.md prose by the
# BCP 14 register gate, so in practice this is the capitalised form.
ABSOLUTE = re.compile(
    r"(?<![A-Za-z-])(must not|never|always|do not ever|under no circumstances)(?![A-Za-z-])",
    re.I)

# A reason stated in the absolute's own sentence.
REASON = re.compile(
    r"(?<![A-Za-z])(because|so that|since|otherwise|or else|which would|to avoid|to keep"
    r"|so (?:the|a|an|it|they|you|no|nothing|each|every|one|that)\b"
    r"|or (?:it|the|a|you|they) (?:would|will|can|could)"
    r"|lest|in case)(?![A-Za-z])|,\s*so(?![A-Za-z])", re.I)

# A ": " or " — " introducing a consequence after the absolute.
COLON = re.compile(r"(?::|\s[—–]|\s--)\s+(?=\S+(?:\s+\S+){2})")

# A next sentence that reads as the consequence of the one before it.
CONSEQUENCE = re.compile(
    r"(?<![A-Za-z])(would|could|cannot|can't|can not|breaks?|corrupts?|leaks?|loses?"
    r"|fails?|silently|risks?|unsafe|irreversibl[ey]|destroys?|overwrites?|wipes?"
    r"|the (?:only|one) way|that is why|this is why|that way|this way|the reason"
    r"|is (?:how|what|why)|(?:this|that|it) is (?:the|a|an|what|how|why)|it's the|means|so )"
    r"(?![A-Za-z])", re.I)

# "never"/"always" is a directive only where an imperative can stand: at the start
# of a sentence or clause, or after "you" or a BCP 14 keyword. "The guard never
# fetches" describes behaviour; it is not a rule and needs no reason. MUST NOT is
# always a directive.
DIRECTIVE_LEAD = re.compile(
    r"(?:^|[.!?;:(—–]|\s-|\s--|\byou|\bYou|\bMUST|\bSHOULD|\bMAY|\band)\s*$")


def is_directive(masked, m):
    """True when the absolute at match `m` is a directive, not a description."""
    if m.group(1).upper() == "MUST NOT" and m.group(1).isupper():
        return True
    if m.group(1).lower() == "must not":
        return True
    lead = masked[max(0, m.start() - 12):m.start()]
    # A list marker or line start counts as a clause start.
    lead = re.sub(r"^\s*(?:[-+|]|\d+[.)])\s*$", "", lead)
    return bool(DIRECTIVE_LEAD.search(lead))


ABBREV = re.compile(r"\b(?:e\.g|i\.e|etc|vs|cf|approx)\.", re.I)
SENTENCE_END = re.compile(r"[.!?](?=[\s)\]\"']|$)")


def mask(text):
    """Mask inline code, the BCP 14 declaration and emphasis markers without moving
    any character, so positions in the masked text are positions in the source."""
    chars = list(text)
    # Code becomes a run of "x" (a word, so "`docker` always uses" still reads as a
    # description, not a sentence-initial "Always"); the declaration becomes blanks.
    for m in reg.CODE_SPAN.finditer(text):
        for i in range(m.start(), m.end()):
            chars[i] = "x"
    for m in reg.DECL.finditer(text):
        for i in range(m.start(), m.end()):
            chars[i] = " "
    for i, c in enumerate(chars):
        if c == "*":
            chars[i] = " "
    out = "".join(chars)
    # Keep abbreviations from ending a sentence.
    return ABBREV.sub(lambda m: m.group(0)[:-1] + " ", out)


def sentences(masked):
    """[(start, end)] sentence spans of a masked block."""
    spans, start = [], 0
    for m in SENTENCE_END.finditer(masked):
        spans.append((start, m.end()))
        start = m.end()
    if masked[start:].strip():
        spans.append((start, len(masked)))
    return spans


def has_reason(sentence, after, next_sentence):
    """The detector. `sentence` is the absolute's sentence, `after` the part of it
    that follows the absolute, `next_sentence` the following sentence of the same
    block or "". True when the absolute states why."""
    if REASON.search(sentence):
        return True
    if COLON.search(after):
        return True
    if next_sentence and (REASON.search(next_sentence) or CONSEQUENCE.search(next_sentence)):
        return True
    return False


def unreasoned(path):
    """[(line, sentence prefix)] for every absolute in `path` with no reason."""
    with open(path, encoding="utf-8") as f:
        body, first = reg.body_with_start(f.read())
    found = []
    for seg in reg.segments(body, first):
        text, starts = "", []
        for n, s in seg:
            starts.append((len(text), n))
            text += s + " "
        masked = mask(text)
        spans = sentences(masked)
        for m in ABSOLUTE.finditer(masked):
            if not is_directive(masked, m):
                continue
            k = next(i for i, (a, e) in enumerate(spans) if a <= m.start() < e)
            a, e = spans[k]
            nxt = masked[spans[k + 1][0]:spans[k + 1][1]] if k + 1 < len(spans) else ""
            # A reason after the sentence's first absolute covers every absolute in
            # it: "MUST NOT accept X — an agent quoting Y MUST NOT validate Z".
            first_abs = ABSOLUTE.search(masked, a, e)
            if has_reason(masked[a:e], masked[first_abs.end():e], nxt):
                continue
            line = max(n for off, n in starts if off <= m.start())
            prefix = " ".join(text[a:e].replace("*", "").split())
            if len(prefix) > 90:
                prefix = prefix[:89] + "…"
            if (line, prefix) not in found:
                found.append((line, prefix))
    return found


CASES = [
    # (label, sentence, after-the-absolute, next sentence, expected)
    ("reason cue 'because'", "You MUST NOT push to main, because CI deploys it.",
     " push to main, because CI deploys it.", "", True),
    ("reason cue 'so that'", "Always pin the version so that reruns match.",
     " pin the version so that reruns match.", "", True),
    ("reason cue 'otherwise'", "Never skip the lock; otherwise two runs collide.",
     " skip the lock; otherwise two runs collide.", "", True),
    ("reason cue 'to avoid'", "Always quote the path to avoid word splitting.",
     " quote the path to avoid word splitting.", "", True),
    ("a colon then a consequence", "Never edit the ledger by hand: the hash chain breaks.",
     " edit the ledger by hand: the hash chain breaks.", "", True),
    ("an em dash then a consequence", "MUST NOT push — the remote deploys every commit.",
     " push — the remote deploys every commit.", "", True),
    ("the reason in the next sentence", "Never push to main.", " push to main.",
     "A push there would deploy untested code.", True),
    ("a comma-so consequence", "Never harvest tool output, so a poisoned file cannot forge a marker.",
     " harvest tool output, so a poisoned file cannot forge a marker.", "", True),
    ("a comma-so after a word", "MUST NOT harvest facts or evidence, so output cannot forge one.",
     " harvest facts or evidence, so output cannot forge one.", "", True),
    ("a next sentence naming the purpose", "You MUST NOT splice it into the control channel.",
     " splice it into the control channel.", "This is the prompt-injection defense.", True),
    ("a bare absolute", "Never push to main.", " push to main.", "", False),
    ("a next sentence that is another rule", "You MUST NOT modify source.", " modify source.",
     "Create only test files.", False),
    ("an exception is not a reason", "Never push to main unless asked.",
     " push to main unless asked.", "", False),
    ("a hedge is not a reason", "Usually you never push.", " push.", "", False),
    ("a label colon before the absolute", "Rule: never push to main.", " push to main.", "", False),
]


def self_test():
    rc = 0
    for label, sent, after, nxt, want in CASES:
        got = has_reason(sent, after, nxt)
        print("%s: %s" % ("PASS" if got == want else "FAIL", label))
        rc |= got != want
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "SKILL.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("---\nname: x\n---\n# x\n\nNever push.\n\n```\nnever push\n```\n"
                    "Run `never` here.\n\n- Always pin it.\n  Reruns would drift.\n")
        got = unreasoned(p)
        want = [(6, "Never push.")]
        print("%s: file scan finds the bare absolute at its line, skips code and a reasoned item"
              % ("PASS" if got == want else "FAIL"))
        rc |= got != want
    print("PP5_SELFTEST: %s" % ("FAIL" if rc else "PASS"))
    return 1 if rc else 0


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    count = bool(argv) and argv[0] == "--count"
    paths = argv[1:] if count else argv
    if not paths:
        print(__doc__.split("\n\n")[-2], file=sys.stderr)
        return 2
    total = 0
    for p in paths:
        for line, prefix in unreasoned(p):
            total += 1
            if not count:
                print("%s:%d: %s" % (p, line, prefix))
    if count:
        print("PP5_UNREASONED: %d" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
