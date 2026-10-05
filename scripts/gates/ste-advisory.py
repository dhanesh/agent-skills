#!/usr/bin/env python3
"""ste-advisory.py — advisory lint for "80% of the way to ASD-STE100" prose.

Usage: python3 scripts/gates/ste-advisory.py [--examples N] FILE.md ...

The convention lives in docs/writing-style.md. This script gives hints only.
It always exits 0, so it can never block a commit or a gate. `make ste` runs it
on the Markdown files that the branch changes, minus */SKILL.md (prompt bodies
are out of scope for the convention).

Checks:
  long      a sentence over 25 words (the description target in the style guide)
  latin     a Latin abbreviation: e.g., i.e., etc., viz.
  passive   a be-verb followed by a past participle (a heuristic, so a hint only)

Not checked, by design:
  "via"           as a preposition it is too noisy to flag without a parser.
  noun clusters   "more than three nouns in a row" needs part-of-speech tags,
                  and a stdlib script has none. A word-shape guess is mostly noise.

Skipped regions: front-matter, fenced code (``` and ~~~), inline code, tables,
headings, HTML comments, URLs and link targets (the link text stays).

Output: one block per file with counts and up to N examples per check, then the
final line `STE_ADVISORY: <hints> hints in <files> files` (files = files read).

Stdlib only.
"""
import re
import sys

LONG_LIMIT = 25
DEFAULT_EXAMPLES = 3

LATIN_RE = re.compile(r"(?<![\w.])(e\.\s?g\.|i\.\s?e\.|etc\.|viz\.)", re.I)
# Abbreviations whose dot does not end a sentence. Protected before the split.
NO_SPLIT = ["e.g.", "i.e.", "etc.", "viz.", "vs.", "cf.", "approx.", "Dr.", "Mr.", "Ms.", "No."]

BE = r"(?:am|is|are|was|were|be|been|being)"
IRREGULAR = (
    "known seen done made given taken written shown built run set kept found sent "
    "held left put read told chosen drawn driven broken spoken hidden proven begun "
    "bound caught felt got gotten lost meant paid said sold shut split spent stood "
    "taught thought understood won worn"
).split()
PASSIVE_RE = re.compile(
    r"\b" + BE + r"\s+(?:\w+ly\s+)?(\w{3,}ed|" + "|".join(IRREGULAR) + r")\b", re.I
)
# -ed words that are usually adjectives after a be-verb, not passives.
NOT_PARTICIPLE = {"need", "based", "used", "supposed", "interested", "tired", "bored",
                  "excited", "concerned", "advanced", "detailed", "limited", "related",
                  "red", "bed", "shed", "embed", "exceed", "proceed", "succeed"}

FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
REF_LINK_RE = re.compile(r"\[([^\]]*)\]\[[^\]]*\]")
AUTOLINK_RE = re.compile(r"<(?:https?://|mailto:)[^>]*>")
URL_RE = re.compile(r"\b(?:https?://|www\.)\S+")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
SPLIT_RE = re.compile(r"(?<=[.!?])[\"')\]*_]*\s+(?=[\"'(\[*_]*[A-Z0-9])")
WORD_RE = re.compile(r"[A-Za-z0-9]")


def units(text):
    """Yield (line_no, prose) for each paragraph or list item outside skipped regions."""
    lines = text.split("\n")
    i = 0
    # front-matter
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() in ("---", "..."):
                i = j + 1
                break
    fence = None
    in_comment = False
    buf, start = [], 0

    def flush():
        if buf:
            yield_list.append((start, " ".join(buf)))
        buf.clear()

    yield_list = []
    while i < len(lines):
        raw = lines[i]
        n = i + 1
        i += 1
        if fence:
            if raw.strip().startswith(fence):
                fence = None
            continue
        m = FENCE_RE.match(raw)
        if m:
            flush()
            fence = m.group(1)[0] * 3
            continue
        if in_comment:
            if "-->" in raw:
                in_comment = False
                raw = raw.split("-->", 1)[1]
            else:
                continue
        while "<!--" in raw:
            before, after = raw.split("<!--", 1)
            if "-->" in after:
                raw = before + " " + after.split("-->", 1)[1]
            else:
                raw = before
                in_comment = True
        line = raw.strip()
        while line.startswith(">"):
            line = line[1:].strip()
        if not line:
            flush()
            continue
        if line.startswith("#") or line.startswith("|") or re.fullmatch(r"[-*_=\s]{3,}", line):
            flush()
            continue
        if LIST_RE.match(line):
            flush()
            line = LIST_RE.sub("", line, count=1)
        if not buf:
            start = n
        buf.append(line)
    flush()
    return yield_list


def clean(prose):
    prose = INLINE_CODE_RE.sub(" CODE ", prose)
    prose = IMAGE_RE.sub(r"\1", prose)
    prose = LINK_RE.sub(r"\1", prose)
    prose = REF_LINK_RE.sub(r"\1", prose)
    prose = AUTOLINK_RE.sub(" LINK ", prose)
    prose = URL_RE.sub(" LINK ", prose)
    prose = HTML_TAG_RE.sub(" ", prose)
    return re.sub(r"\s+", " ", prose).strip()


def sentences(prose):
    protected = prose
    for k, abbr in enumerate(NO_SPLIT):
        protected = re.sub(re.escape(abbr), f"\x00{k}\x00", protected, flags=re.I)
    for part in SPLIT_RE.split(protected):
        for k, abbr in enumerate(NO_SPLIT):
            part = part.replace(f"\x00{k}\x00", abbr)
        part = part.strip()
        if part:
            yield part


def word_count(sentence):
    return sum(1 for w in sentence.split() if WORD_RE.search(w))


def check_text(text):
    """Return {'long': [(line, words, snippet)], 'latin': [...], 'passive': [...]}."""
    hits = {"long": [], "latin": [], "passive": []}
    for line_no, prose in units(text):
        prose = clean(prose)
        for m in LATIN_RE.finditer(prose):
            hits["latin"].append((line_no, m.group(1), snippet(prose, m.start())))
        for s in sentences(prose):
            wc = word_count(s)
            if wc > LONG_LIMIT:
                hits["long"].append((line_no, wc, short(s)))
            for m in PASSIVE_RE.finditer(s):
                if m.group(1).lower() in NOT_PARTICIPLE:
                    continue
                hits["passive"].append((line_no, m.group(0), short(s)))
    return hits


def short(s, n=14):
    words = s.split()
    return " ".join(words[:n]) + (" …" if len(words) > n else "")


def snippet(prose, at, width=40):
    a = max(0, at - width)
    return ("…" if a else "") + prose[a:at + width].strip() + "…"


def main(argv):
    examples = DEFAULT_EXAMPLES
    files = []
    it = iter(argv)
    for a in it:
        if a == "--examples":
            try:
                examples = int(next(it))
            except (StopIteration, ValueError):
                print("ste-advisory: --examples needs a number; using the default", file=sys.stderr)
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            files.append(a)
    total, read = 0, 0
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            print(f"{path}: skipped ({exc.__class__.__name__})")
            continue
        read += 1
        hits = check_text(text)
        n = sum(len(v) for v in hits.values())
        total += n
        print(f"{path}: {len(hits['long'])} long, {len(hits['latin'])} latin, "
              f"{len(hits['passive'])} passive (hint)")
        for kind in ("long", "latin", "passive"):
            for line_no, what, text_ in hits[kind][:examples]:
                label = f"{what} words" if kind == "long" else repr(what)
                print(f"  {kind} L{line_no} ({label}): {text_}")
    print(f"STE_ADVISORY: {total} hints in {read} files")
    return 0


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as exc:  # advisory: never fail the caller
        print(f"ste-advisory: internal error ({exc}); no verdict")
        print("STE_ADVISORY: 0 hints in 0 files")
    sys.exit(0)
