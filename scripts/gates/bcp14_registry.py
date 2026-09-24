#!/usr/bin/env python3
"""bcp14_registry.py: every BCP 14 keyword in a SKILL.md is classified in the register.

PP-7 (prompting-playbook.sh) checks that a SKILL.md declares BCP 14 and uses only
MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in capitals. It cannot see whether a
keyword was ever classified, so the register (docs/rfc2119/2026-09-19-classification.md)
fell behind the skills by dozens of sentences. This checker closes that gap. For every
top-level */SKILL.md body, with fenced code, inline code and the declaration sentence
skipped:

  (a) every capitalised keyword is covered by a register row in that skill's section;
  (b) the row's final level equals the strongest keyword it covers
      (MUST family > SHOULD family > MAY; "plain" covers none);
  (c) no lowercase "must" or "shall" appears as a word (RFC 8174 gives lowercase no
      normative meaning, so a lowercase one reads as a rule and carries none);
  (d) every skill has a section in the register, even one with no candidates.

How a row matches (the register's `line` column is informational, never read):
  - Text on both sides is normalised: markdown emphasis (*), backticks and backslash
    escapes are dropped, whitespace is collapsed, and a leading list, table or quote
    marker is ignored.
  - A row's quoted sentence ends at its first "…"; text after it is elided, so such a
    row is "truncated".
  - A row anchors wherever its prefix occurs in a block (a paragraph, list item,
    heading or table row), or where the block's tail is a prefix of the row's text.
  - A truncated row covers from its anchor to the end of the block. A full row covers
    to the end of the sentence its text ends in.
  - A keyword is assigned to the covering row anchored latest before it. A candidate
    counts once, at its strongest keyword: "MAY proceed … and MUST name" is one MUST
    row, as the register has always recorded it.

Usage: bcp14_registry.py [--root DIR] [--register FILE] [--counts]
Prints one FAIL: <skill> <reason>: <sentence prefix> line per problem, then
BCP14_RESULT: PASS or BCP14_RESULT: FAIL (n); exits 1 on failure, 2 on usage errors.
--counts prints BCP14_COUNTS: unregistered=<n> level=<n> lowercase=<n> section=<n>
and exits 0; scripts/ab-validate.py uses it to measure both trees with one checker.
stdlib only, offline, deterministic.
"""
import argparse
import os
import re
import sys

REGISTER = os.path.join("docs", "rfc2119", "2026-09-19-classification.md")
KEYWORD = re.compile(r"(?<![A-Za-z])(MUST NOT|MUST|SHOULD NOT|SHOULD|MAY)(?![A-Za-z])")
LOWER = re.compile(r"(?<![A-Za-z])([Mm]ust|[Ss]hall)(?![A-Za-z])")
DECL = re.compile(r"[^.]*BCP 14 \(RFC 2119, RFC 8174\)[^.]*all capitals\.?")
FENCE = re.compile(r"^\s*(```+|~~~+)")
QUOTE = re.compile(r"^\s*(?:>\s?)+")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
LEAD = re.compile(r"^(?:(?:[-+>|]|\d+[.)])\s*)+")
CODE_SPAN = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)", re.S)
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
RANK = {"plain": 0, "MAY": 1, "SHOULD": 2, "MUST": 3}
MIN_PREFIX = 10


def family(word):
    for f in ("MUST", "SHOULD", "MAY"):
        if f in word:
            return f
    return "plain" if word.strip().lower() == "plain" else None


def body_of(text):
    """The SKILL.md body: everything after the closing frontmatter ---."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[i + 1:]
    return lines


def blocks(lines):
    """Prose blocks: paragraphs, list items, headings and table rows, fences dropped."""
    out, cur, fence = [], [], ""

    def flush():
        if cur:
            out.append(" ".join(cur))
            cur.clear()

    for ln in lines:
        m = FENCE.match(ln)
        if m:
            tok = m.group(1)
            if not fence:
                flush()
                fence = tok
                continue
            if tok[0] == fence[0] and len(tok) >= len(fence):
                fence = ""
                continue
        if fence:
            continue
        s = QUOTE.sub("", ln).strip()
        if not s:
            flush()
        elif s.startswith("#") or s.startswith("|"):
            flush()
            out.append(s)
        elif LIST_ITEM.match(QUOTE.sub("", ln)):
            flush()
            cur.append(s)
        else:
            cur.append(s)
    flush()
    return out


def normalise(raw):
    """Return (text, masked): text with emphasis, backticks and escapes dropped and
    whitespace collapsed; masked is the same length with inline code and the BCP 14
    declaration replaced by NULs, so keyword and lowercase scans skip them."""
    skip = [False] * len(raw)
    for m in list(CODE_SPAN.finditer(raw)) + list(DECL.finditer(raw)):
        for i in range(m.start(), m.end()):
            skip[i] = True
    chars, flags, i = [], [], 0
    while i < len(raw):
        c = raw[i]
        if c in "*`":
            i += 1
            continue
        if c == "\\" and i + 1 < len(raw) and not raw[i + 1].isalnum() and not raw[i + 1].isspace():
            i += 1
            continue
        if c.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                flags.append(skip[i])
            i += 1
            continue
        chars.append(c)
        flags.append(skip[i])
        i += 1
    text = "".join(chars).strip()
    lead = len("".join(chars)) - len("".join(chars).lstrip())
    flags = flags[lead:lead + len(text)]
    m = LEAD.match(text)
    if m:
        text, flags = text[m.end():], flags[m.end():]
    masked = "".join("\0" if f else c for c, f in zip(text, flags))
    return text, masked


def row_prefix(sentence):
    """(normalised prefix, truncated?) for a register row's quoted sentence."""
    truncated = "…" in sentence
    head = sentence.split("…", 1)[0]
    text, _ = normalise(head)
    return text.strip(), truncated


def parse_register(path):
    """{skill: [row, ...]} where a row is a dict(id, sentence, final, prefix, truncated)."""
    sections, cur = {}, None
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.rstrip("\n")
            h = re.match(r"^## (\S+)", ln)
            if h:
                cur = sections.setdefault(h.group(1), [])
                continue
            if cur is None or not re.match(r"^\|\s*c\d+\s*\|", ln):
                continue
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", ln)[1:-1]]
            if len(cells) < 5:
                continue
            prefix, truncated = row_prefix(cells[2])
            cur.append({"id": cells[0], "sentence": cells[2], "final": cells[4],
                        "level": family(cells[4]), "prefix": prefix, "truncated": truncated})
    return sections


def sentence_around(text, pos):
    starts = [m.end() for m in SENTENCE_END.finditer(text, 0, pos)]
    start = starts[-1] if starts else 0
    m = SENTENCE_END.search(text, pos)
    end = m.end() if m else len(text)
    return text[start:end].strip()


def anchors(row, text):
    """[(start, end)] spans of this block that the row covers."""
    p = row["prefix"]
    if len(p) < MIN_PREFIX:
        return []
    spans, i = [], text.find(p)
    while i != -1:
        if row["truncated"]:
            end = len(text)
        else:
            e = i + len(p)
            if p[-1] in ".!?":
                end = e
            else:
                m = SENTENCE_END.search(text, e)
                end = m.end() if m else len(text)
        spans.append((i, end))
        i = text.find(p, i + 1)
    if not spans:
        # The block's tail is a prefix of the row: the row quotes past the block end
        # (a sentence that ends in a colon before a fenced command, say).
        for k in range(min(len(p), len(text)), MIN_PREFIX - 1, -1):
            if text.endswith(p[:k]) and p.startswith(text[len(text) - k:]):
                spans.append((len(text) - k, len(text)))
                break
    return spans


def check_skill(skill, skill_md, rows):
    """[(reason, sentence)] problems for one skill; rows is None when no section exists."""
    with open(skill_md, encoding="utf-8") as f:
        body = body_of(f.read())
    problems = []
    if rows is None:
        problems.append(("section", "no section in the register (add one, even with 0 candidates)"))
        rows = []
    for raw in blocks(body):
        text, masked = normalise(raw)
        for m in LOWER.finditer(masked):
            problems.append(("lowercase", "'%s' in: %s" % (m.group(1), sentence_around(text, m.start()))))
        hits = [(m.start(), m.group(1)) for m in KEYWORD.finditer(masked)]
        if not hits:
            continue
        spans = [(a, e, r) for r in rows for (a, e) in anchors(r, text)]
        covered = {}
        for pos, word in hits:
            owners = [(a, r) for a, e, r in spans if a <= pos < e]
            if not owners:
                problems.append(("unregistered", sentence_around(text, pos)))
                continue
            a, r = max(owners, key=lambda o: o[0])
            key = (id(r), a)
            prev = covered.get(key, (r, "plain", pos))
            best = word if RANK[family(word)] > RANK[family(prev[1])] else prev[1]
            covered[key] = (r, best, prev[2] if key in covered else pos)
        for (_, a), (r, best, pos) in sorted(covered.items(), key=lambda kv: kv[0][1]):
            want = family(best)
            if r["level"] != want:
                problems.append(("level", "row %s says %s, the text uses %s: %s"
                                 % (r["id"], r["final"], best, sentence_around(text, pos))))
    return problems


def run(root, register):
    if not os.path.isfile(register):
        print("FAIL: register %s not found" % register)
        return None
    sections = parse_register(register)
    skills = sorted(d for d in os.listdir(root)
                    if os.path.isfile(os.path.join(root, d, "SKILL.md")))
    if not skills:
        print("FAIL: no */SKILL.md under %s" % root)
        return None
    found = []
    for s in skills:
        seen = set()
        for reason, sentence in check_skill(s, os.path.join(root, s, "SKILL.md"), sections.get(s)):
            if (reason, sentence) not in seen:  # one line per sentence, not per keyword
                seen.add((reason, sentence))
                found.append((s, reason, sentence))
    return found


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--register")
    ap.add_argument("--counts", action="store_true")
    args = ap.parse_args(argv)
    register = args.register or os.path.join(args.root, REGISTER)
    found = run(args.root, register)
    if found is None:
        if args.counts:
            return 2
        print("BCP14_RESULT: FAIL (1)")
        return 1
    if args.counts:
        n = {k: 0 for k in ("unregistered", "level", "lowercase", "section")}
        for _, reason, _ in found:
            n[reason] += 1
        print("BCP14_COUNTS: " + " ".join("%s=%d" % kv for kv in n.items()))
        return 0
    for s, reason, sentence in found:
        short = sentence if len(sentence) <= 100 else sentence[:99] + "…"
        print("FAIL: %s %s: %s" % (s, reason, short))
    if found:
        print("BCP14_RESULT: FAIL (%d)" % len(found))
        return 1
    print("BCP14_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
