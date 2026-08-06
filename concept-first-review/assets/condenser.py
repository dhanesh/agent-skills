#!/usr/bin/env python3
"""Apply a condensation plan to an immutable diff.

The reviewer never writes the condensed diff. They write a *plan* — coordinates
into the original saying which rows to drop, which contiguous runs to collapse,
and which spans inside a single row to trim — and this module applies it.

The asymmetry is the point. The plan language has verbs for deleting and for
shortening; it has no verb for writing something new. So whatever comes out is
derivable from what went in, and the condensed diff can be trusted the way a
compiler's output is trusted rather than the way a summary is trusted.

Rules enforced here, each rejected with a message that says what to change:

  declarations  dependency rows are dropped for you; a collapse may not span
                both those and executable rows.
  trim          `new` must reproduce `old` with each cut span marked `...`/`…`.
  collapse      >= 2 adjacent rows, one hunk, one marker; this module supplies
                the placeholder, the plan supplies only coordinates.
  symmetry      relocated code is treated identically on both sides, so a move
                can never be presented as a one-sided deletion.
  dependency    a collapse may not bury a definition a surviving row still uses.
  block         (Python) a collapse may not consume a decorator or a def/class
                header on the added or context side.
  parity        (Python) triple-quote parity within a hunk is preserved.
  structure     file and hunk metadata follows its content in and out, so
                orphaned headers cannot occur.
"""

import re

import diffmodel
from diffmodel import ADD, CONTEXT, DEL, FILE_HEADER, HUNK_HEADER, SOURCE_KINDS

ELLIPSIS = ("...", "…")
MAX_PREVIEW_BYTES = 24000

KEEP = "K"
REMOVED = "X"
FOLD_START = "Fs"
FOLD_BODY = "F"


class Result(object):
    def __init__(self):
        self.errors = []
        self.notes = []
        self.condensed = ""
        self.stats = {}

    @property
    def ok(self):
        return not self.errors


_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


def parse_range(spec):
    """`"14-17"` or `"14"` -> `(14, 17)` / `(14, 14)`. None when malformed."""
    if isinstance(spec, int):
        return (spec, spec) if spec >= 1 else None
    if not isinstance(spec, str):
        return None
    m = _RANGE_RE.match(spec)
    if not m:
        return None
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    if start < 1 or end < start:
        return None
    return start, end


def is_trim_of(span, replacement):
    """True when `replacement` is `span` with each cut marked by an ellipsis.

    Implemented by turning the replacement into an anchored pattern where every
    ellipsis becomes a wildcard. A full match proves that every character of the
    replacement came from the source, in order — the property that makes a
    condensed row trustworthy.
    """
    if not span or replacement == span:
        return False
    if not any(e in replacement for e in ELLIPSIS):
        return False
    parts = re.split(r"\.\.\.|…", replacement)
    pattern = ".*?".join(re.escape(p) for p in parts)
    return re.fullmatch(pattern, span, re.S) is not None


def _split_edits(plan, errors):
    """Ingest the ordered `edits` list into per-operation buckets."""
    drops, collapses, trims = [], [], []
    edits = plan.get("edits")
    if edits is None:
        errors.append("plan: missing the `edits` array (use [] when nothing needs condensing)")
        return drops, collapses, trims
    if not isinstance(edits, list):
        errors.append("plan: `edits` must be an array of operations")
        return drops, collapses, trims

    for i, edit in enumerate(edits):
        where = "edits[%d]" % i
        if not isinstance(edit, dict):
            errors.append("%s: each edit is an object with an `op` field" % where)
            continue
        op = edit.get("op")
        if op in ("drop", "collapse"):
            span = parse_range(edit.get("lines"))
            if span is None:
                errors.append(
                    "%s: `lines` must be a line number or an \"N-M\" range, got %r"
                    % (where, edit.get("lines"))
                )
                continue
            (drops if op == "drop" else collapses).append(span)
        elif op == "trim":
            try:
                line_no = int(edit["line"])
            except (KeyError, TypeError, ValueError):
                errors.append("%s: a trim needs an integer `line`" % where)
                continue
            span = edit.get("from")
            replacement = edit.get("to")
            if not isinstance(span, str) or not isinstance(replacement, str):
                errors.append("%s: a trim needs string `from` and `to` fields" % where)
                continue
            trims.append((line_no, span, replacement, where))
        else:
            errors.append(
                "%s: unknown op %r — expected \"drop\", \"collapse\", or \"trim\"" % (where, op)
            )
    return drops, collapses, trims


def compile_plan(diff_text, plan, rows=None, declarations=None, moves=None):
    """Apply `plan` to `diff_text`. Returns a `Result`; never raises on bad plans."""
    res = Result()
    rows = rows if rows is not None else diffmodel.parse_diff(diff_text)
    declarations = declarations if declarations is not None else diffmodel.declaration_rows(rows)
    moves = moves if moves is not None else diffmodel.detect_relocations(rows)

    by_no = {r.no: r for r in rows}
    last = max(by_no) if by_no else 0
    errors = res.errors

    if not isinstance(plan, dict):
        errors.append("plan: expected a JSON object carrying an `edits` array")
        return res

    drops, collapses, trims = _split_edits(plan, errors)

    for label, ranges in (("drop", drops), ("collapse", collapses)):
        for start, end in ranges:
            if end > last:
                errors.append(
                    "%s %d-%d: outside the diff (valid lines are 1-%d)" % (label, start, end, last)
                )

    # ── Treatment map ────────────────────────────────────────────────────────
    treat = {}
    for start, end in drops:
        for n in range(max(start, 1), min(end, last) + 1):
            treat[n] = REMOVED

    fold_rows = {}
    for start, end in collapses:
        if end - start < 1:
            errors.append(
                "collapse %d: a collapse covers at least two adjacent rows; use drop or trim "
                "for a single row" % start
            )
            continue
        span = [by_no.get(n) for n in range(start, end + 1)]
        if any(r is None for r in span):
            continue
        _check_collapse(span, start, end, declarations, errors)
        for n in range(start, end + 1):
            if n in fold_rows:
                errors.append("collapse %d-%d: overlaps another collapse at line %d" % (start, end, n))
            fold_rows[n] = (start, end)
        treat[start] = FOLD_START
        for n in range(start + 1, end + 1):
            treat[n] = FOLD_BODY

    replace_text = {}
    for line_no, span, replacement, where in trims:
        row = by_no.get(line_no)
        if row is None:
            errors.append("%s: line %d is outside the diff (valid lines are 1-%d)"
                          % (where, line_no, last))
            continue
        if row.kind not in SOURCE_KINDS:
            errors.append(
                "%s: line %d is %s — only a +, -, or context row can be trimmed"
                % (where, line_no, row.kind)
            )
            continue
        if line_no in treat:
            errors.append(
                "%s: line %d is already dropped or collapsed; keep one edit, not both"
                % (where, line_no)
            )
            continue
        occurrences = row.source.count(span)
        if occurrences == 0:
            errors.append(
                "%s: `from` text %r is not on line %d after the diff marker"
                % (where, span, line_no)
            )
            continue
        if occurrences > 1:
            errors.append(
                "%s: `from` text %r appears %d times on line %d; widen it until it is unique"
                % (where, span, occurrences, line_no)
            )
            continue
        if not is_trim_of(span, replacement):
            errors.append(
                "%s: `to` text %r is not a trim of `from` text %r — mark each cut span with "
                "... or …, and introduce no characters that were not already there"
                % (where, replacement, span)
            )
            continue
        replace_text[line_no] = (span, replacement)

    _check_symmetry(moves, by_no, treat, replace_text, declarations, errors)
    _check_dependencies(collapses, by_no, treat, declarations, errors)
    _check_python_blocks(collapses, by_no, errors)
    _check_quote_parity(rows, treat, replace_text, declarations, errors)

    _note_one_sided_hunks(rows, treat, declarations, res.notes)

    ignored = sum(1 for n in treat if n in declarations)
    if ignored:
        res.notes.append(
            "%d coordinate(s) landed on dependency rows, which come out anyway; aim them at "
            "executable rows instead." % ignored
        )

    res.condensed, res.stats = _render(rows, treat, replace_text, fold_rows, declarations)
    res.stats["errors"] = len(errors)
    return res


def _check_collapse(span, start, end, declarations, errors):
    markers = {r.marker for r in span}
    if any(r.kind not in SOURCE_KINDS for r in span):
        # A range that leaves its hunk necessarily swallows the next @@ header,
        # so this one message covers both "folded metadata" and "crossed a hunk".
        errors.append(
            "collapse %d-%d: covers a hunk header or file metadata; collapse only +, -, or "
            "context rows, and stay inside one hunk" % (start, end)
        )
        return
    if len(markers) > 1:
        errors.append(
            "collapse %d-%d: mixes diff markers %s; a collapse covers one marker at a time"
            % (start, end, "/".join(sorted(markers)))
        )
    declared = [r.no for r in span if r.no in declarations]
    if declared and len(declared) != len(span):
        errors.append(
            "collapse %d-%d: spans both dependency rows (already gone) and executable rows; "
            "collapse only the executable range" % (start, end)
        )


def _treatment_vector(lo, hi, by_no, treat, replace_text, declarations):
    codes = []
    for n in range(lo, hi + 1):
        if n in declarations:
            continue
        row = by_no.get(n)
        if row is None or row.kind not in SOURCE_KINDS:
            continue
        code = treat.get(n, KEEP)
        if n in replace_text:
            code = "R:" + replace_text[n][1].strip()
        codes.append(code)
    return codes


def _check_symmetry(moves, by_no, treat, replace_text, declarations, errors):
    for mv in moves:
        r_lo, r_hi = mv["origin"]
        a_lo, a_hi = mv["destination"]
        left = _treatment_vector(r_lo, r_hi, by_no, treat, replace_text, declarations)
        right = _treatment_vector(a_lo, a_hi, by_no, treat, replace_text, declarations)
        if left != right:
            errors.append(
                "relocation lines %d-%d -> lines %d-%d: the two sides are edited differently "
                "(origin %s, destination %s). Apply the same treatment to both so the change "
                "presents as a move rather than a deletion."
                % (r_lo, r_hi, a_lo, a_hi, left, right)
            )


# `(?![=>])` keeps a match arm or arrow function (`Expression::Foo => {`,
# `x => x + 1`) from reading as an assignment to its left-hand name.
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?(?::=|=)(?![=>])")


def _check_dependencies(collapses, by_no, treat, declarations, errors):
    hidden = {}
    for start, end in collapses:
        span = [by_no.get(n) for n in range(start, end + 1)]
        span = [r for r in span if r is not None and r.kind in SOURCE_KINDS]
        if not span:
            continue
        indents = [len(r.source) - len(r.source.lstrip()) for r in span if r.source.strip()]
        if not indents:
            continue
        base = min(indents)
        for row in span:
            src = row.source
            if not src.strip() or len(src) - len(src.lstrip()) != base:
                continue
            m = _ASSIGN_RE.match(src.strip())
            if m and len(m.group(1)) >= 3:
                hidden.setdefault((row.file_idx, m.group(1)), (start, end))

    if not hidden:
        return
    for (file_idx, name), (start, end) in sorted(hidden.items(), key=lambda kv: kv[1]):
        pattern = re.compile(r"\b%s\b" % re.escape(name))
        for row in by_no.values():
            if row.file_idx != file_idx or row.kind not in SOURCE_KINDS:
                continue
            if row.no in declarations or treat.get(row.no, KEEP) != KEEP:
                continue
            if pattern.search(row.source):
                errors.append(
                    "collapse %d-%d: buries the definition of `%s` while surviving line %d "
                    "still uses it. Keep the definition and collapse its interior, or drop the "
                    "use as well." % (start, end, name, row.no)
                )
                break


_DECORATOR_RE = re.compile(r"^@[A-Za-z_]")
_OWNER_RE = re.compile(r"^(?:async\s+def|def|class)\s")


def _check_python_blocks(collapses, by_no, errors):
    for start, end in collapses:
        span = [by_no.get(n) for n in range(start, end + 1)]
        span = [r for r in span if r is not None and r.kind in SOURCE_KINDS]
        if not span:
            continue
        if diffmodel.language_of(span[0].path or "") != "python":
            continue
        if span[0].marker == "-":
            continue
        for row in span:
            s = row.source.strip()
            if _DECORATOR_RE.match(s):
                errors.append(
                    "collapse %d-%d: consumes decorator `%s` at line %d. A decorator and the "
                    "definition it modifies are one unit — keep the header and collapse the body."
                    % (start, end, s.split("(")[0], row.no)
                )
                break
            if _OWNER_RE.match(s):
                errors.append(
                    "collapse %d-%d: consumes the block header at line %d (`%s`). Keep the "
                    "header visible and collapse only its indented body."
                    % (start, end, row.no, s[:48])
                )
                break


def _check_quote_parity(rows, treat, replace_text, declarations, errors):
    buckets = {}
    for row in rows:
        if row.kind not in SOURCE_KINDS or row.hunk_idx < 0:
            continue
        if diffmodel.language_of(row.path or "") != "python":
            continue
        key = (row.hunk_idx, row.marker)
        before, after = buckets.get(key, (0, 0))
        count = row.source.count('"""') + row.source.count("'''")
        before += count
        if row.no in declarations or treat.get(row.no, KEEP) != KEEP:
            pass
        elif row.no in replace_text:
            old, new = replace_text[row.no]
            after += row.source.replace(old, new).count('"""') + row.source.replace(old, new).count("'''")
        else:
            after += count
        buckets[key] = (before, after)

    for (hunk_idx, marker), (before, after) in sorted(buckets.items()):
        if before % 2 == after % 2:
            continue
        errors.append(
            "hunk %d (%s rows): the plan changes triple-quote parity (%d before, %d after), "
            "which would leave a surviving multiline string unterminated. Keep the opening and "
            "closing delimiters and collapse what is between them."
            % (hunk_idx + 1, marker.strip() or "context", before, after)
        )


def _note_one_sided_hunks(rows, treat, declarations, notes):
    """Warn when a replacement survives as a deletion with nothing replacing it.

    A hunk carrying both `-` and `+` rows is a replacement. Condensing it down to
    only its removed side leaves the reader watching code disappear and never
    learning what took its place — the same lie the relocation rule forbids
    across hunks, which nothing previously caught within one.

    Advisory rather than a rejection: keeping one anchor from each side is a
    legitimate and common condensation, and only the reviewer can say whether the
    surviving anchors carry the point.
    """
    sides = {}
    for row in rows:
        if row.kind not in (ADD, DEL) or row.no in declarations:
            continue
        total, kept = sides.setdefault(row.hunk_idx, {"+": [0, 0], "-": [0, 0]})[row.marker], None
        total[0] += 1
        if treat.get(row.no, KEEP) in (KEEP, FOLD_START):
            total[1] += 1

    stranded = []
    for hunk_idx, marks in sorted(sides.items()):
        add_total, add_kept = marks["+"]
        del_total, del_kept = marks["-"]
        if not add_total or not del_total:
            continue  # a pure addition or a pure deletion has no other side
        if add_kept == 0 and del_kept > 0:
            stranded.append((hunk_idx + 1, "removed", del_kept))
        elif del_kept == 0 and add_kept > 0:
            stranded.append((hunk_idx + 1, "added", add_kept))

    for hunk_no, side, kept in stranded[:8]:
        other = "added" if side == "removed" else "removed"
        notes.append(
            "hunk %d replaces code, but only its %s side survives (%d row(s)); every %s row "
            "is hidden. A reader sees the change as one-directional. Keep an anchor from "
            "both sides." % (hunk_no, side, kept, other)
        )
    if len(stranded) > 8:
        notes.append("...and %d further hunk(s) condensed to one side only."
                     % (len(stranded) - 8))


def _render(rows, treat, replace_text, fold_rows, declarations):
    """Emit the condensed diff. Metadata follows its content automatically."""
    kept_source = {}
    for row in rows:
        if row.kind not in SOURCE_KINDS:
            continue
        if row.no in declarations:
            continue
        code = treat.get(row.no, KEEP)
        if code in (REMOVED, FOLD_BODY):
            continue
        kept_source.setdefault(row.hunk_idx, []).append(row)

    live_hunks = {h for h, v in kept_source.items() if v}
    live_files = set()
    for row in rows:
        if row.hunk_idx in live_hunks and row.hunk_idx >= 0:
            live_files.add(row.file_idx)

    out = []
    visible_changed = 0
    for row in rows:
        if row.kind == FILE_HEADER:
            if row.file_idx in live_files:
                out.append(row.text)
            continue
        if row.kind == HUNK_HEADER:
            if row.hunk_idx in live_hunks:
                out.append(row.text)
            continue
        if row.kind not in SOURCE_KINDS:
            if row.hunk_idx in live_hunks:
                out.append(row.text)
            continue
        if row.no in declarations:
            continue
        code = treat.get(row.no, KEEP)
        if code in (REMOVED, FOLD_BODY):
            continue
        if code == FOLD_START:
            start, end = fold_rows[row.no]
            indents = []
            for n in range(start, end + 1):
                src = rows[n - 1].source if n - 1 < len(rows) else ""
                if src.strip():
                    indents.append(len(src) - len(src.lstrip()))
            pad = " " * (min(indents) if indents else 0)
            out.append("%s%s..." % (row.marker, pad))
            if row.marker in "+-":
                visible_changed += 1
            continue
        text = row.text
        if row.no in replace_text:
            old, new = replace_text[row.no]
            text = row.marker + row.source.replace(old, new, 1)
        out.append(text)
        if row.marker in "+-":
            visible_changed += 1

    total_rows = sum(1 for r in rows if r.kind in (ADD, DEL))
    total_files = len({r.file_idx for r in rows if r.kind in (ADD, DEL)})
    stats = {
        "total_rows": total_rows,
        "kept_rows": visible_changed,
        "total_files": total_files,
        "kept_files": len(live_files),
        "declaration_rows": len(declarations),
        "density_pct": round(100.0 * visible_changed / total_rows, 1) if total_rows else 0.0,
    }
    return "\n".join(out) + ("\n" if out else ""), stats


DENSITY_NUDGE_PCT = 70


def feedback(res):
    """Density figures, notes, and every rejection — what the reviewer sees."""
    lines = []
    s = res.stats
    if s:
        lines.append(
            "density: %d of %d changed rows kept (%.1f%%) across %d of %d files; "
            "%d dependency row(s) removed for you"
            % (
                s.get("kept_rows", 0),
                s.get("total_rows", 0),
                s.get("density_pct", 0.0),
                s.get("kept_files", 0),
                s.get("total_files", 0),
                s.get("declaration_rows", 0),
            )
        )
        if s.get("total_rows") and s.get("density_pct", 0) > DENSITY_NUDGE_PCT:
            lines.append(
                "density is a prompt to look again, not a target to hit: one more pass over "
                "repetition and test setup, then keep whatever is distinct or uncertain."
            )
    for note in res.notes:
        lines.append("note: " + note)
    for err in res.errors:
        lines.append("REJECTED: " + err)
    return "\n".join(lines)


def truncate(text, limit=MAX_PREVIEW_BYTES):
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [preview truncated at %d bytes] ...\n" % limit
