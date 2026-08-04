#!/usr/bin/env python3
"""Deterministic unified-diff structure for concept-first review.

Three jobs, all offline and stdlib-only:

  1. `parse_diff`     — classify every physical line of an immutable diff
                        (file header / hunk header / +,-,context source row)
                        and tag it with its file and hunk index.
  2. `declaration_rows`  — find import/include/require/use rows in any of the
                        supported languages. Scaffolding is removed from the
                        condensed diff automatically, so a plan never spends
                        coordinates on it.
  3. `detect_relocations`   — conservatively pair exact relocations of a block
                        between two different hunks, so both sides can be
                        forced to read as a move rather than a one-sided
                        deletion.

Coordinates everywhere are 1-based physical line numbers in the ORIGINAL
diff text and never shift.
"""

import re

# ── Line kinds ───────────────────────────────────────────────────────────────

FILE_HEADER = "file"
HUNK_HEADER = "hunk"
ADD = "add"
DEL = "del"
CONTEXT = "ctx"
NOEOL = "noeol"
OTHER = "other"

SOURCE_KINDS = (ADD, DEL, CONTEXT)

_HUNK_RE = re.compile(r"^@@+ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")
_DIFF_GIT_RE = re.compile(r"^diff --git ")
_OLD_PATH_RE = re.compile(r"^--- (?:a/)?(.*)$")
_NEW_PATH_RE = re.compile(r"^\+\+\+ (?:b/)?(.*)$")


class Row(object):
    """One physical line of the original diff."""

    __slots__ = ("no", "text", "kind", "file_idx", "hunk_idx", "path", "marker")

    def __init__(self, no, text, kind, file_idx, hunk_idx, path):
        self.no = no
        self.text = text
        self.kind = kind
        self.file_idx = file_idx
        self.hunk_idx = hunk_idx
        self.path = path
        if kind == ADD:
            self.marker = "+"
        elif kind == DEL:
            self.marker = "-"
        elif kind == CONTEXT:
            self.marker = " "
        else:
            self.marker = ""

    @property
    def source(self):
        """Text after the leading diff marker; '' for non-source rows."""
        if self.kind not in SOURCE_KINDS:
            return ""
        return self.text[1:] if self.text else ""

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Row(%d, %r, %s)" % (self.no, self.text, self.kind)


def parse_diff(text):
    """Split a unified diff into classified `Row`s.

    Handles `diff --git` sections and bare `---`/`+++` pairs. A `---`/`+++`
    line is a file header only outside a hunk body, so a deleted line that
    happens to start with `--` is never mistaken for metadata.
    """
    rows = []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    file_idx = -1
    hunk_idx = -1
    path = ""
    in_hunk = False

    for i, line in enumerate(lines):
        no = i + 1

        if _DIFF_GIT_RE.match(line):
            file_idx += 1
            in_hunk = False
            path = _path_from_diff_git(line)
            rows.append(Row(no, line, FILE_HEADER, file_idx, -1, path))
            continue

        if _HUNK_RE.match(line):
            if file_idx < 0:
                file_idx = 0
            hunk_idx += 1
            in_hunk = True
            rows.append(Row(no, line, HUNK_HEADER, file_idx, hunk_idx, path))
            continue

        if not in_hunk:
            m = _OLD_PATH_RE.match(line)
            if m:
                if file_idx < 0:
                    file_idx = 0
                    path = m.group(1)
                rows.append(Row(no, line, FILE_HEADER, file_idx, -1, path))
                continue
            m = _NEW_PATH_RE.match(line)
            if m:
                if file_idx < 0:
                    file_idx = 0
                if m.group(1) not in ("/dev/null", ""):
                    path = m.group(1)
                # Retro-tag the section's header rows with the resolved path.
                for r in reversed(rows):
                    if r.file_idx != file_idx:
                        break
                    if r.kind == FILE_HEADER:
                        r.path = path
                rows.append(Row(no, line, FILE_HEADER, file_idx, -1, path))
                continue
            kind = FILE_HEADER if _is_metadata(line) else OTHER
            rows.append(Row(no, line, kind, file_idx if file_idx >= 0 else 0, -1, path))
            continue

        # Inside a hunk body.
        if line.startswith("\\"):
            rows.append(Row(no, line, NOEOL, file_idx, hunk_idx, path))
        elif line.startswith("+"):
            rows.append(Row(no, line, ADD, file_idx, hunk_idx, path))
        elif line.startswith("-"):
            rows.append(Row(no, line, DEL, file_idx, hunk_idx, path))
        elif line.startswith(" ") or line == "":
            rows.append(Row(no, line if line else " ", CONTEXT, file_idx, hunk_idx, path))
        else:
            in_hunk = False
            kind = FILE_HEADER if _is_metadata(line) else OTHER
            rows.append(Row(no, line, kind, file_idx, -1, path))

    return rows


_METADATA_PREFIXES = (
    "index ",
    "old mode ",
    "new mode ",
    "deleted file mode ",
    "new file mode ",
    "similarity index ",
    "dissimilarity index ",
    "copy from ",
    "copy to ",
    "rename from ",
    "rename to ",
    "Binary files ",
    "GIT binary patch",
)


def _is_metadata(line):
    return line.startswith(_METADATA_PREFIXES)


def _path_from_diff_git(line):
    rest = line[len("diff --git "):].strip()
    parts = rest.split(" b/")
    if len(parts) == 2:
        return parts[1]
    toks = rest.split()
    if toks:
        return toks[-1].lstrip("b/")
    return ""


def index_diff(text):
    """Render the diff with a right-aligned coordinate column.

    Each row becomes `<width-aligned line number> │ <original row>`. The column
    is fixed-width so the diff markers stay vertically aligned and the eye can
    still read the change as a diff; the `│` makes it unmistakable where the
    coordinate ends and the source begins.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    width = max(3, len(str(len(lines))))
    return "\n".join(
        "%*d │ %s" % (width, i + 1, line) for i, line in enumerate(lines)
    )


# ── Scaffolding (import) detection ───────────────────────────────────────────

_EXT_LANG = {
    ".go": "go",
    ".py": "python",
    ".pyi": "python",
    ".js": "js",
    ".jsx": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".ts": "js",
    ".tsx": "js",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "c",
    ".cpp": "c",
    ".cxx": "c",
    ".hpp": "c",
    ".m": "c",
    ".java": "jvm",
    ".kt": "jvm",
    ".kts": "jvm",
    ".scala": "jvm",
    ".swift": "jvm",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
}


def language_of(path):
    for ext, lang in _EXT_LANG.items():
        if path.endswith(ext):
            return lang
    return "generic"


_GO_SPEC_RE = re.compile(r'^(?:[A-Za-z_.][\w.]*\s+)?"[^"]+"$')
_PY_IMPORT_RE = re.compile(r"^(?:import\s+\S|from\s+[\w.]+\s+import\b)")
_JS_IMPORT_RE = re.compile(r"^(?:import\b|export\s+.*\bfrom\b)")
_JS_REQUIRE_RE = re.compile(r"^(?:const|let|var)\s+.*=\s*require\s*\(")
_RUST_USE_RE = re.compile(r"^(?:pub\s+)?use\s+\S|^extern\s+crate\b")
_JVM_IMPORT_RE = re.compile(r"^import\s+\S")
_CS_USING_RE = re.compile(r"^using\s+[\w.]+\s*;")
_RUBY_REQUIRE_RE = re.compile(r'^(?:require|require_relative|load)\s+[\'"]')
_PHP_USE_RE = re.compile(r'^(?:use\s+\\?[\w\\]+|require(?:_once)?\s|include(?:_once)?\s)')
_C_INCLUDE_RE = re.compile(r"^#\s*include\b")


class _SideState(object):
    __slots__ = ("open_block",)

    def __init__(self):
        self.open_block = None


def _step(state, src, lang):
    """Advance one side's state machine; True when `src` is a dependency row."""
    s = src.strip()

    if state.open_block == "go":
        if s.startswith(")"):
            state.open_block = None
            return True
        if s == "" or s.startswith("//") or _GO_SPEC_RE.match(s):
            return True
        state.open_block = None
        return False

    if state.open_block == "paren":
        if s.startswith(")"):
            state.open_block = None
            return True
        return True

    if state.open_block == "brace":
        if s.startswith("}"):
            state.open_block = None
            return True
        return True

    if lang == "go":
        if s == "import (" or s.startswith("import ("):
            state.open_block = "go"
            return True
        if s.startswith("import "):
            return True
        return False

    if lang == "python":
        if _PY_IMPORT_RE.match(s):
            if s.rstrip().endswith("(") or (s.count("(") > s.count(")")):
                state.open_block = "paren"
            return True
        return False

    if lang == "js":
        if _JS_IMPORT_RE.match(s) or _JS_REQUIRE_RE.match(s):
            if s.count("{") > s.count("}"):
                state.open_block = "brace"
            return True
        return False

    if lang == "rust":
        if _RUST_USE_RE.match(s):
            if s.count("{") > s.count("}"):
                state.open_block = "brace"
            return True
        return False

    if lang == "c":
        return bool(_C_INCLUDE_RE.match(s))

    if lang == "jvm":
        return bool(_JVM_IMPORT_RE.match(s))

    if lang == "csharp":
        return bool(_CS_USING_RE.match(s))

    if lang == "ruby":
        return bool(_RUBY_REQUIRE_RE.match(s))

    if lang == "php":
        return bool(_PHP_USE_RE.match(s))

    # Generic fallback also catches import rows embedded in multiline test
    # fixtures, where the enclosing file's language says nothing useful.
    return bool(
        _PY_IMPORT_RE.match(s)
        or _JS_IMPORT_RE.match(s)
        or _JS_REQUIRE_RE.match(s)
        or _C_INCLUDE_RE.match(s)
        or _JVM_IMPORT_RE.match(s)
        or _RUST_USE_RE.match(s)
    )


def declaration_rows(rows):
    """Line numbers of import/include/require/use rows, in any supported language.

    A context row belongs to both sides of the hunk, so it advances both state
    machines — that is what lets an unchanged `import (` framing row be
    recognised inside a block whose members are being edited.
    """
    hit = set()
    states = {}
    lang_by_file = {}
    current_hunk = None

    for row in rows:
        if row.kind not in SOURCE_KINDS:
            if row.kind == HUNK_HEADER:
                current_hunk = row.hunk_idx
                states = {"-": _SideState(), "+": _SideState()}
            continue
        if row.hunk_idx != current_hunk:
            current_hunk = row.hunk_idx
            states = {"-": _SideState(), "+": _SideState()}
        if row.file_idx not in lang_by_file:
            lang_by_file[row.file_idx] = language_of(row.path or "")
        lang = lang_by_file[row.file_idx]

        sides = ["-", "+"] if row.marker == " " else [row.marker]
        matched = False
        for side in sides:
            if _step(states[side], row.source, lang):
                matched = True
        if matched:
            hit.add(row.no)
    return hit


# ── Move detection ───────────────────────────────────────────────────────────

MIN_MOVE_ROWS = 3
MIN_MOVE_NONSPACE = 48
MAX_MOVE_HINTS = 12

_WORDY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _normalize(src):
    return src.strip()


def _substantive(norm):
    """A row distinctive enough to anchor a move on its own."""
    if len(norm) < 8:
        return False
    words = _WORDY_RE.findall(norm)
    if len(words) < 2:
        return False
    return len(norm.replace(" ", "")) >= 8


def _indent(src):
    return len(src) - len(src.lstrip())


def detect_relocations(rows):
    """Pair exact block relocations between two different hunks.

    Conservative on purpose. A candidate needs a globally unique substantive
    anchor line appearing exactly once as a removal and once as an addition,
    byte-identical bodies after indentation normalisation, one constant indent
    delta across every non-blank row, at least MIN_MOVE_ROWS substantive rows,
    and at least MIN_MOVE_NONSPACE non-space bytes. Anything ambiguous or
    overlapping is dropped rather than guessed.
    """
    change = {}
    order = []
    for row in rows:
        if row.kind in (ADD, DEL):
            change[row.no] = row
            order.append(row.no)
    if not order:
        return []

    # Contiguous same-marker, same-hunk runs.
    run_of = {}
    runs = []
    i = 0
    while i < len(order):
        start = i
        row = change[order[i]]
        while (
            i + 1 < len(order)
            and order[i + 1] == order[i] + 1
            and change[order[i + 1]].marker == row.marker
            and change[order[i + 1]].hunk_idx == row.hunk_idx
        ):
            i += 1
        idx = len(runs)
        runs.append((order[start], order[i]))
        for n in range(order[start], order[i] + 1):
            run_of[n] = idx
        i += 1

    occurrences = {}
    for n in order:
        row = change[n]
        norm = _normalize(row.source)
        if not _substantive(norm):
            continue
        occurrences.setdefault(norm, []).append(row)

    anchors = []
    for norm, found in occurrences.items():
        if len(found) != 2:
            continue
        rem = [r for r in found if r.marker == "-"]
        add = [r for r in found if r.marker == "+"]
        if len(rem) == 1 and len(add) == 1:
            anchors.append((rem[0].no, add[0].no))
    anchors.sort()

    candidates = []
    seen = set()
    for rem_no, add_no in anchors:
        if rem_no not in run_of or add_no not in run_of:
            continue
        r_run = run_of[rem_no]
        a_run = run_of[add_no]
        if change[rem_no].hunk_idx == change[add_no].hunk_idx:
            continue
        span = _extend(change, runs, r_run, a_run, rem_no, add_no)
        if span is None:
            continue
        if span in seen:
            continue
        seen.add(span)
        candidates.append(span)

    candidates.sort()
    accepted = []
    used = set()
    for r_start, r_end, a_start, a_end in candidates:
        block = set(range(r_start, r_end + 1)) | set(range(a_start, a_end + 1))
        if block & used:
            continue
        used |= block
        accepted.append(
            {"origin": [r_start, r_end], "destination": [a_start, a_end]}
        )
    return accepted


def _extend(change, runs, r_run, a_run, rem_no, add_no):
    r_lo, r_hi = runs[r_run]
    a_lo, a_hi = runs[a_run]
    delta = _indent(change[add_no].source) - _indent(change[rem_no].source)

    def matches(rn, an):
        if rn < r_lo or rn > r_hi or an < a_lo or an > a_hi:
            return False
        rs = change[rn].source
        as_ = change[an].source
        if _normalize(rs) != _normalize(as_):
            return False
        if _normalize(rs) == "":
            return True
        return _indent(as_) - _indent(rs) == delta

    start_r, start_a = rem_no, add_no
    while matches(start_r - 1, start_a - 1):
        start_r -= 1
        start_a -= 1
    end_r, end_a = rem_no, add_no
    while matches(end_r + 1, end_a + 1):
        end_r += 1
        end_a += 1

    subst = 0
    nonspace = 0
    for n in range(start_r, end_r + 1):
        norm = _normalize(change[n].source)
        nonspace += len(norm.replace(" ", ""))
        if _substantive(norm):
            subst += 1
    if subst < MIN_MOVE_ROWS or nonspace < MIN_MOVE_NONSPACE:
        return None
    return (start_r, end_r, start_a, end_a)


def relocation_notes(moves, limit=MAX_MOVE_HINTS):
    """One line per detected relocation, capped so the briefing stays short."""
    out = []
    for mv in moves[:limit]:
        out.append(
            "lines %d-%d moved to lines %d-%d"
            % (mv["origin"][0], mv["origin"][1], mv["destination"][0], mv["destination"][1])
        )
    if len(moves) > limit:
        out.append("(%d further relocation(s) not listed)" % (len(moves) - limit))
    return out
