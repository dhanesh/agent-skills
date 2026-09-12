#!/usr/bin/env python3
r"""The Go stack: what a Go repo means by each interface name.

Everything here is language-specific and everything language-specific is here;
the stack-agnostic half -- churn, reference counting, coverage detection,
scoring, ranking, the CLI, the JSON shape -- stays in `rank_risk.py`, which
documents the interface the two meet at.

WHAT MAKES GO DIFFERENT FROM THE OTHER TWO, in the order it bites:

  * A PACKAGE IS A DIRECTORY. Every `.go` file in it shares one scope, so a
    same-package caller in a sibling file needs no import and names nothing.
    `module_of` is the directory, `scope_files` is the directory, and triage
    chases helpers across the directory.
  * Imports name a PATH (`"example.com/m/internal/billing"`), not a bare
    token, which makes path-qualified coverage evidence the COMMON case here
    rather than the rare one it is for node.
  * Visibility is spelled by case: an exported name starts with an uppercase
    letter, so there is no `export` keyword and no `_` convention to read.
  * A real parser exists in the standard library (`go/ast`). Discovery is
    still hybrid (multistack design, D1): the heuristic reader in this file
    always works, and the precise path runs a `go/ast` helper under the
    machine's own `go` when there is one.

The runtime enforcement is `io_guard_go.py`, which injects hooks into the
standard library through `go test -overlay` (the 2026-09-12 amendment to the
multistack design). Everything here is the FILTER.
"""
from __future__ import annotations

import bisect
import functools
import os
import re
import sys

# The shipped assets are a flat directory, not a package, and callers load
# them by path from directories that are not this one. Make the siblings
# importable by name before importing them. NEVER import `rank_risk` here: it
# is loaded by path and never lands in `sys.modules`, so importing it back
# would execute a second copy of the ranker mid-import.
_ASSETS = os.path.dirname(os.path.abspath(__file__))
if _ASSETS not in sys.path:
    sys.path.insert(0, _ASSETS)

from stack_common import (SKIP_DIRS, evidence_score, has_manifest,   # noqa: E402,F401
                          read_text)

STACK_NAME = "go"

# `go.work` names the modules of a workspace; `go.mod` names one module.
MANIFESTS = ("go.mod", "go.work")

# Directories the `go` tool itself never builds, beyond the vendor and VCS
# directories `SKIP_DIRS` already carries. A directory starting with `_` or `.`
# is ignored by the go tool too, and is pruned by prefix below.
_GO_SKIP_DIRS = frozenset({"testdata"})


# ── Identity ─────────────────────────────────────────────────────────────

def evidence(root: str) -> int:
    """How much of `root` is Go: non-test `.go` files, scaled by a manifest.

    The rule lives in `stack_common.evidence_score`, shared, so the three
    stacks cannot drift into weighing the same evidence on different scales.
    Tool-pinning files (`//go:build tools`) are not source (see
    `iter_source_files`), so a Python repo that keeps its linters in a
    `tools/` module scores 0 here -- the Go twin of the husky `package.json`.
    """
    return evidence_score(sum(1 for _rel in iter_source_files(root)),
                          root, MANIFESTS, classify_manifest)


_MODULE_DIRECTIVE = re.compile(r"(?m)^[ \t]*module[ \t]+\S")


def classify_manifest(name: str, text: str) -> str:
    """`declaring` when this manifest says the repo IS Go; `tooling` otherwise.

    A `go.mod` declares a module by its `module` directive, which every real
    one has; a file with none is not a module file at all. There is no
    tooling-only `go.mod` worth a content rule, because the tooling case is
    decided by the source files instead: a tools module's only `.go` file is
    constrained `//go:build tools`, which `iter_source_files` excludes, and
    `evidence_score` is 0 with no source behind the manifest.
    """
    if name == "go.work":
        return "declaring"
    if name == "go.mod":
        code = strip_noncode(text, keep_strings=True)
        return "declaring" if _MODULE_DIRECTIVE.search(code) else "tooling"
    return "tooling"              # not a name this stack knows: never declares


# ── Files ────────────────────────────────────────────────────────────────

# The two constraints that mean "never part of this package's build". Every
# other constraint (`linux`, `amd64`, a custom tag) marks code a person edits
# and ships, so it stays source.
_EXCLUDING_CONSTRAINT = re.compile(r"^//go:build[ \t]+(?:ignore|tools)[ \t]*$")
_LEGACY_EXCLUDING = re.compile(r"^//[ \t]*\+build[ \t]+(?:ignore|tools)[ \t]*$")


@functools.lru_cache(maxsize=4096)
def _constraint_excludes(path: str, mtime_ns: int, size: int) -> bool:
    """True when a file is constrained out of every build.

    Go reads build constraints only in the comment block ABOVE the package
    clause, so the scan stops at the first line that is the clause; the same
    `//go:build ignore` further down is an ordinary comment. Keyed on mtime
    and size so a file edited mid-process is re-read.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.startswith("package ") or s == "package":
                    return False
                if _EXCLUDING_CONSTRAINT.match(s) or _LEGACY_EXCLUDING.match(s):
                    return True
    except OSError:
        return False
    return False


def _excluded(path: str) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    return _constraint_excludes(path, st.st_mtime_ns, st.st_size)


def is_test_path(rel: str) -> bool:
    """True for a `_test.go` file, or anything under `testdata/`.

    Both are the go tool's own rules, not conventions: `go build` never
    compiles a `_test.go` file into a package, and never enters `testdata/`.
    """
    parts = rel.replace(os.sep, "/").split("/")
    return parts[-1].endswith("_test.go") or "testdata" in parts[:-1]


def iter_source_files(root: str, include_tests: bool = False):
    """Repo-relative `.go` paths the go tool would build, sorted.

    Pruned exactly as `go` prunes: `vendor/`, `testdata/`, and any directory
    or file whose name starts with `_` or `.`. Files constrained
    `//go:build ignore` or `//go:build tools` are not source either -- the
    first is a `go run` script, the second a tool-pin that exists only to
    appear in `go.mod`.
    """
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs
                         if d not in SKIP_DIRS and d not in _GO_SKIP_DIRS
                         and not d.startswith((".", "_")))
        for name in sorted(files):
            if not name.endswith(".go") or name.startswith((".", "_")):
                continue
            rel = os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
            if not include_tests and is_test_path(rel):
                continue
            if _excluded(os.path.join(base, name)):
                continue
            out.append(rel)
    return sorted(out)


def is_test_for(test_rel: str, src_rel: str) -> bool:
    """True when `test_rel` sits in `src_rel`'s package directory.

    The whole rule, because Go allows nothing else: a test for a package lives
    in that package's directory, as either the package itself (white-box) or
    `<pkg>_test` (black-box). POSITION ONLY -- `reached_through_module` is
    what says the test exercises a given unit.
    """
    return os.path.dirname(test_rel) == os.path.dirname(src_rel)


def scope_files(rel: str, all_files) -> list:
    """Every `.go` file in `rel`'s package directory, tests included.

    A Go package's files share one scope: `parse(s)` in `a.go` resolves to
    the `parse` declared in `b.go` with no import at all. So a caller in a
    sibling file is exactly as much the unit's own scope as the defining
    file, and a white-box test calling it bare is a caller too. Answering
    `[rel]` here -- the python/node answer -- would drop every such caller
    from `inbound_refs`, and those are the commonest call sites Go has.
    """
    d = os.path.dirname(rel)
    out = {f for f in all_files if f.endswith(".go") and os.path.dirname(f) == d}
    out.add(rel)
    return sorted(out)


# ── Naming ───────────────────────────────────────────────────────────────

def module_of(rel: str) -> str:
    """The package a file belongs to, as importers name it: its directory.

    The basename of the directory, because that is the last element of the
    import path and, by overwhelming convention, the package name a caller
    writes (`billing.Refund`). A package whose `package` clause differs from
    its directory reads as un-referenced, which UNDER-credits.

    A repo-root file answers its own stem. Its package's real name is the
    last element of the module path in `go.mod`, which this function cannot
    see -- it is given a path, not a root -- and a stem under-credits too,
    where an empty string would match everywhere.
    """
    d = os.path.dirname(rel.replace(os.sep, "/"))
    if d:
        return os.path.basename(d)
    return os.path.splitext(os.path.basename(rel))[0]


_ID = r"[A-Za-z0-9_]"


def name_pattern(name: str):
    r"""`name` as a whole identifier -- and, for a method, WITH its type.

    A plain name is bounded the way Go bounds an identifier (ASCII; a Unicode
    identifier reads as uncovered, the safe direction).

    A method unit is named `T.M`, and matching `M` alone would be an
    over-credit machine: `buf.String()`, `w.Write(`, `c.Close()` appear in
    nearly every Go test file, so a same-directory test calling
    `bytes.Buffer.String` would cover every `T.String` in the package. The
    pattern therefore requires BOTH the type as a whole word AND a call of
    `.M(`. Anchored at `\A` so a failed search costs one scan rather than one
    per starting position.
    """
    if "." in name:
        recv, meth = name.split(".", 1)
        return re.compile(r"(?s)\A(?=.*(?<!%s)%s(?!%s))(?=.*\.\s*%s\s*(?:\[[^\]\n]*\])?\s*\()"
                          % (_ID, re.escape(recv), _ID, re.escape(meth)))
    return re.compile(r"(?<!%s)%s(?!%s)" % (_ID, re.escape(name), _ID))


def path_pattern(rel: str):
    """`rel`'s package directory as it appears inside an import path.

    Bounded by the import path's own delimiters: a `"` or `/` before it and
    the closing `"` after. `internal/billing` therefore matches in
    `"example.com/m/internal/billing"` and in `"internal/billing"`, and not in
    `.../myinternal/billing`, `.../internal/billingx` or the SUBpackage
    `.../internal/billing/sub` -- each of which is a different package whose
    test would otherwise credit this one.

    None for a repo-root file, whose import path is the module path this
    function cannot see.
    """
    d = os.path.dirname(rel.replace(os.sep, "/"))
    if not d:
        return None
    return re.compile(r'(?<=["/])%s(?=")' % re.escape(d))


# ── Lexing ───────────────────────────────────────────────────────────────

IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*")


def preceding_qualifier(text: str, start: int):
    """The receiver an identifier at `start` is a selector OF, or None.

    None     -- the identifier stands on its own (`Refund(...)`).
    "billing" -- it was written `billing.Refund`.
    ""       -- it is a selector on something unnameable (`mk().Refund`).

    Python's rule byte for byte: Go's selector is `.` and nothing else.
    """
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0 or text[i] != ".":
        return None
    j = i - 1
    while j >= 0 and text[j].isspace():
        j -= 1
    end = j + 1
    while j >= 0 and (text[j].isalnum() or text[j] == "_"):
        j -= 1
    qual = text[j + 1:end]
    return qual if qual and not qual[0].isdigit() else ""


# ── The comment/string stripper ──────────────────────────────────────────

@functools.lru_cache(maxsize=256)
def strip_noncode(text: str, keep_strings: bool = False) -> str:
    """`text` with comments and literals blanked, every offset preserved.

    Five states: `//` and `/* */` comments, `"..."` interpreted strings,
    `` `...` `` raw strings and `'...'` runes. Go has no regex literals and
    no string interpolation, so this is node's stripper with two states fewer
    and nothing to guess about division.

    Blanked characters become spaces except newlines, so a position here is
    the same position in the original and line numbers hold.
    `keep_strings=True` keeps string and raw-string bodies -- an import path
    IS a string literal -- and still blanks comments, so a commented-out
    import binds nothing.

    Where it can be wrong, it is wrong toward leaving text VISIBLE: an
    interpreted string or rune never crosses a newline (the language forbids
    it, so an unterminated one blanks only its opening quote), and an
    unterminated comment or raw string runs to end of file, which does not
    compile either.
    """
    n = len(text)
    out = list(text)

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = text[i]
        if c == "/" and text[i + 1:i + 2] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and text[i + 1:i + 2] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
            continue
        if c == "`":
            j = text.find("`", i + 1)
            j = n if j < 0 else j + 1
            if not keep_strings:
                blank(i, j)
            i = j
            continue
        if c in "\"'":
            j = _quoted_end(text, i, n)
            if j < 0:
                if not keep_strings:
                    blank(i, i + 1)
                i += 1
                continue
            if not keep_strings:
                blank(i, j + 1)
            i = j + 1
            continue
        i += 1
    return "".join(out)


def _quoted_end(text: str, i: int, n: int) -> int:
    """Index of the quote closing the literal opening at `i`, or -1."""
    q = text[i]
    j = i + 1
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return -1
        if c == q:
            return j
        j += 1
    return -1


# ── Import/binding grammar ───────────────────────────────────────────────

_IMPORT_KW = re.compile(r"(?m)^[ \t]*import\b")
_IMPORT_SPEC = re.compile(
    r"""(?:(?P<alias>[A-Za-z_][A-Za-z0-9_]*|\.)[ \t]+)?(?:"(?P<path>[^"\n]+)"|`(?P<raw>[^`]+)`)""")


def _imports(text: str):
    """[(alias or None, import path)] for every import declaration in `text`.

    Read off the comment-stripped, string-kept text: an import path is a
    string literal, and a commented-out import must bind nothing.
    """
    ks = strip_noncode(text, keep_strings=True)
    out = []
    for m in _IMPORT_KW.finditer(ks):
        i = m.end()
        while i < len(ks) and ks[i] in " \t":
            i += 1
        if i < len(ks) and ks[i] == "(":
            close = ks.find(")", i)
            body = ks[i + 1:close if close >= 0 else len(ks)]
            for spec in _IMPORT_SPEC.finditer(body):
                out.append((spec.group("alias"), spec.group("path") or spec.group("raw")))
        else:
            spec = _IMPORT_SPEC.match(ks, i)
            if spec:
                out.append((spec.group("alias"), spec.group("path") or spec.group("raw")))
    return out


def _names_package_dir(path: str, src_dir: str) -> bool:
    """Does import `path` name the package in directory `src_dir`?

    It does when the path IS that directory or ENDS WITH `/` plus it -- the
    module path in front is whatever `go.mod` declares, which this is not
    given, and the directory is the part of an import path the repo itself
    decides. A root package (`src_dir == ""`) is imported by the bare module
    path and cannot be recognised: it credits nothing, the under-credit
    direction.
    """
    if not src_dir:
        return False
    return path == src_dir or path.endswith("/" + src_dir)


def module_bindings(module: str, text: str, *, src_rel: str, ref_rel: str) -> tuple:
    """(aliases, names) `text` binds for `src_rel`'s package.

    `aliases` are the names the package is referred to by (`b` for
    `b "…/billing"`, `billing` for an unaliased import). `names` is `("*",)`
    when every exported name is in scope bare: a dot import, or -- the case
    that matters most -- a file in the SAME DIRECTORY, which is the same
    package and imports nothing. A same-directory `<pkg>_test` file imports
    the package by path like anyone else, so both routes are read and joined.

    A blank import (`_`) binds nothing: it runs `init` and names no member.
    """
    src_dir = os.path.dirname(src_rel.replace(os.sep, "/"))
    aliases, names = set(), set()
    for alias, path in _imports(text):
        if not _names_package_dir(path, src_dir):
            continue
        if alias == "_":
            continue
        if alias == ".":
            names.add("*")
        else:
            aliases.add(alias or os.path.basename(src_dir))
    if os.path.dirname(ref_rel.replace(os.sep, "/")) == src_dir:
        names.add("*")
    return tuple(sorted(aliases)), tuple(sorted(names))


_TYPE_ARGS = r"(?:\[[^\]\n]*\])?"


def reached_through_module(module: str, name: str, text: str, *,
                           src_rel: str, ref_rel: str) -> bool:
    """True when `text` CALLS `name` through a binding of `src_rel`'s package.

    The call site is the point, as both other stacks ruled: a function VALUE
    (`var f = b.Refund`, handed to a stub) and a mention inside a string are
    not evidence the unit ran. Type arguments are allowed between the name
    and its `(`, since `b.Map[int](xs)` is a call.

    A method unit `T.M` is reached when the text names `T` as a whole word and
    calls `.M(` -- receivers are values, and this reader does no type
    inference, so requiring the type is what stops `buf.String()` from
    crediting `Report.String`.
    """
    aliases, names = module_bindings(module, text, src_rel=src_rel, ref_rel=ref_rel)
    if not aliases and not names:
        return False
    code = strip_noncode(text)
    if "." in name:
        recv, meth = name.split(".", 1)
        if not re.search(r"(?<!%s)%s(?!%s)" % (_ID, re.escape(recv), _ID), code):
            return False
        return bool(re.search(r"\.\s*%s\s*%s\s*\(" % (re.escape(meth), _TYPE_ARGS), code))
    esc = re.escape(name)
    if "*" in names and re.search(r"(?<![A-Za-z0-9_.])%s\s*%s\s*\(" % (esc, _TYPE_ARGS), code):
        return True
    return any(re.search(r"(?<![A-Za-z0-9_.])%s\s*\.\s*%s\s*%s\s*\("
                         % (re.escape(alias), esc, _TYPE_ARGS), code)
               for alias in aliases)


# ── Discovery ────────────────────────────────────────────────────────────

def discover_units(root: str, precise: bool = True):
    """Interface entry point: `(units, discovery_path)`.

    The heuristic reader below is the one that always runs; the `go/ast`
    precise path is an upgrade layered on it, never a dependency, and the
    label says which one produced the units (multistack design, D1).
    """
    return _units_heuristic(root), "heuristic"


# Go's own definition of a generated file: this line, before the first
# non-comment, non-blank text -- which in practice means above the package
# clause. The same words lower down are an ordinary comment.
_GENERATED = re.compile(r"^// Code generated .* DO NOT EDIT\.$")


def _is_generated(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("package ") or s == "package":
            return False
        if _GENERATED.match(s):
            return True
    return False


def _units_heuristic(root: str):
    """Exported top-level functions and methods, in non-test, non-generated files.

    EXPORTED means an uppercase first letter -- Go's own visibility rule, so
    there is no convention to guess at. A method counts only when its
    receiver type is exported too: nothing outside the package can name an
    unexported type, so its methods are reached through whatever exported
    function hands one out, and that function is the unit.

    Types themselves are not units. A Go type has no constructor body --
    `NewX` is an ordinary function, and discovered as one -- so a type's
    behaviour lives in its methods, which are.
    """
    units = []
    for rel in iter_source_files(root):
        text = read_text(root, rel)
        if _is_generated(text):
            continue
        units.extend(_units_in_text(rel, text))
    return sorted(units, key=lambda u: u["id"])


def _units_in_text(rel: str, text: str):
    """The units one file's text declares, in file order."""
    code = strip_noncode(text)
    depths = _depths(code)
    starts = _line_starts(code)
    units, seen = [], set()
    for name, recv, pos in _func_decls(code, depths):
        if not _exported(name) or (recv is not None and not _exported(recv)):
            continue
        full = "%s.%s" % (recv, name) if recv else name
        if full in seen:
            continue
        seen.add(full)
        units.append({
            "id": "%s::%s" % (rel, full),
            "path": rel,
            "name": full,
            "lineno": bisect.bisect_right(starts, pos),
            "kind": "method" if recv else "function",
        })
    return units


def _exported(name: str) -> bool:
    return bool(name) and name[0].isupper()


# A declaration starts at column 0. That is gofmt's layout, and gofmt is
# close enough to universal that a reader built on it misses almost nothing;
# what it does miss (hand-indented top-level code) is exactly what the
# precise `go/ast` path exists to catch.
#
# WHAT THE ANCHOR DOES NOT DO, measured rather than assumed: it is not what
# keeps `type HandlerFunc func(int) error` or `var F = func(n int) error {}`
# out of the units. Unanchored, both still fail `_func_decls`'s SHAPE check
# -- a function type or literal never has a name followed by `(` after its
# parameter list -- and the test pinning them stays green. Only removing the
# anchor AND the shape check together turns it red. So the anchor is
# belt-and-braces over the shape check (plus fewer candidates to parse),
# and its one real cost is the indented case above.
_FUNC_AT = re.compile(r"(?m)^func\b")
_IDENT_AT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IDENT_FULL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _func_decls(code: str, depths):
    """(name, receiver type or None, position) for each top-level `func`.

    Reads `func Name(`, `func Name[T any](`, and every receiver form --
    `(r T)`, `(r *T)`, `(*T)`, `(s *Set[K])`. A top-level `func(` with no
    name is a literal, not a declaration, and is skipped.
    """
    out = []
    n = len(code)
    for m in _FUNC_AT.finditer(code):
        pos = m.start()
        if depths[pos] != 0:
            continue
        i = _skip_ws(code, m.end())
        recv = None
        if i < n and code[i] == "(":
            close = _match_bracket(code, depths, i)
            if close < 0:
                continue
            recv = _receiver_type(code[i + 1:close])
            if recv is None:
                continue
            i = _skip_ws(code, close + 1)
        word = _IDENT_AT.match(code, i)
        if not word:
            continue
        j = _skip_ws(code, word.end())
        if j < n and code[j] == "[":
            close = _match_bracket(code, depths, j)
            if close < 0:
                continue
            j = _skip_ws(code, close + 1)
        if j < n and code[j] == "(":
            out.append((word.group(0), recv, pos))
    return out


def _receiver_type(inner: str):
    """`r *Report` -> `Report`; `*Set[K, V]` -> `Set`; None when unreadable.

    Type arguments are cut first, because `Set[K, V]` contains a space that
    would otherwise split the type from itself.
    """
    head = inner.split("[", 1)[0].split()
    if not head:
        return None
    tok = head[-1].lstrip("*")
    return tok if _IDENT_FULL.match(tok) else None


def _depths(code: str):
    """Bracket depth per index; an opener and its closer both carry the OUTER depth."""
    d = [0] * (len(code) + 1)
    cur = 0
    for i, ch in enumerate(code):
        if ch in "{([":
            d[i] = cur
            cur += 1
        elif ch in "})]":
            cur = max(cur - 1, 0)
            d[i] = cur
        else:
            d[i] = cur
    d[len(code)] = cur
    return d


def _match_bracket(code: str, depths, i: int) -> int:
    """Index of the bracket closing the one at `i`, or -1."""
    close = {"{": "}", "(": ")", "[": "]"}[code[i]]
    d = depths[i]
    for j in range(i + 1, len(code)):
        if code[j] == close and depths[j] == d:
            return j
    return -1


def _skip_ws(code: str, i: int) -> int:
    n = len(code)
    while i < n and code[i].isspace():
        i += 1
    return i


def _line_starts(code: str):
    starts = [0]
    i = code.find("\n")
    while i >= 0:
        starts.append(i + 1)
        i = code.find("\n", i + 1)
    return starts


# ── I/O markers ──────────────────────────────────────────────────────────
#
# A marker is an IMPORT PATH, optionally followed by a member chain:
# `os.ReadFile`, `os/exec`, `net/http.DefaultClient`. Every chain in a unit is
# resolved through its file's imports before matching, so `f.Getenv` after
# `import f "os"`, `Getenv` after `import . "os"`, and `os.Getenv` are one
# marker, not three rules. `_marker_hit` is node's rule without the `node:`
# prefix: equal, a member of it, or a sub-path of it.
#
# The groups are the seven every stack shares. WHICH SIDE OF THE LINE THEY
# SIT ON is the language's to say, and Go says one thing differently:
# `randomness` is UNCONTROLLABLE. Since Go 1.24 `rand.Seed` is a no-op unless
# the process runs with GODEBUG=randseednop=0, and the top-level `math/rand`
# and `math/rand/v2` functions draw from a source no test can seed. A unit
# using them needs a seam -- an injected `*rand.Rand` -- before a
# characterization test of it is honest. A SEEDED `rand.New(rand.NewSource(1))`
# is plain computation, which is why the table lists the top-level functions
# by name instead of marking the packages whole.
#
# `clock` stays controllable because `testing/synctest` (Go 1.25+) fakes it;
# on an older toolchain a clock unit is a "could not prove", not a Tier 2 pin.
CONTROLLABLE = {
    "filesystem": ("os.Open", "os.OpenFile", "os.Create", "os.ReadFile", "os.WriteFile",
                   "os.ReadDir", "os.Stat", "os.Lstat", "os.Mkdir", "os.MkdirAll",
                   "os.MkdirTemp", "os.CreateTemp", "os.Remove", "os.RemoveAll",
                   "os.Rename", "os.Link", "os.Symlink", "os.Readlink", "os.Chmod",
                   "os.Chown", "os.Lchown", "os.Chtimes", "os.Truncate", "os.DirFS",
                   "os.CopyFS", "os.OpenRoot", "os.OpenInRoot", "os.Getwd", "os.Chdir",
                   # `ioutil.ReadAll(r)` reads a READER, not a file, so the
                   # package is marked by its filesystem members only.
                   "io/ioutil.ReadFile", "io/ioutil.WriteFile", "io/ioutil.ReadDir",
                   "io/ioutil.TempDir", "io/ioutil.TempFile",
                   "path/filepath.Walk", "path/filepath.WalkDir", "path/filepath.Glob",
                   "path/filepath.EvalSymlinks", "path/filepath.Abs",
                   # Reads the zoneinfo database from disk.
                   "time.LoadLocation"),
    "clock": ("time.Now", "time.Since", "time.Until", "time.Sleep", "time.After",
              "time.AfterFunc", "time.NewTimer", "time.NewTicker", "time.Tick"),
    "environment": ("os.Getenv", "os.LookupEnv", "os.Environ", "os.Setenv", "os.Unsetenv",
                    "os.Clearenv", "os.ExpandEnv", "os.Hostname", "os.Getpid", "os.Getppid",
                    "os.Getuid", "os.Geteuid", "os.Getgid", "os.Getegid", "os.Getgroups",
                    "os.Executable", "os.Args", "os.TempDir", "os.UserHomeDir",
                    "os.UserCacheDir", "os.UserConfigDir", "os.Getpagesize"),
}
UNCONTROLLABLE = {
    "randomness": tuple("math/rand." + n for n in (
                      "ExpFloat64", "Float32", "Float64", "Int", "Int31", "Int31n", "Int63",
                      "Int63n", "Intn", "NormFloat64", "Perm", "Read", "Seed", "Shuffle",
                      "Uint32", "Uint64"))
                  + tuple("math/rand/v2." + n for n in (
                      "ExpFloat64", "Float32", "Float64", "Int", "Int32", "Int32N", "Int64",
                      "Int64N", "IntN", "N", "NormFloat64", "Perm", "Shuffle", "Uint",
                      "Uint32", "Uint32N", "Uint64", "Uint64N", "UintN"))
                  + ("crypto/rand", "hash/maphash.MakeSeed"),
    "network": tuple("net." + n for n in (
                   "Dial", "DialIP", "DialTCP", "DialTimeout", "DialUDP", "DialUnix",
                   "FileConn", "FileListener", "FilePacketConn", "InterfaceAddrs",
                   "InterfaceByIndex", "InterfaceByName", "Interfaces", "Listen", "ListenIP",
                   "ListenMulticastUDP", "ListenPacket", "ListenTCP", "ListenUDP",
                   "ListenUnix", "ListenUnixgram", "LookupAddr", "LookupCNAME", "LookupHost",
                   "LookupIP", "LookupMX", "LookupNS", "LookupPort", "LookupSRV", "LookupTXT",
                   "Dialer", "Resolver", "ListenConfig"))
               + ("net/http.Get", "net/http.Head", "net/http.Post", "net/http.PostForm",
                  "net/http.DefaultClient", "net/http.Client", "net/http.ListenAndServe",
                  "net/http.ListenAndServeTLS", "net/http.Serve", "net/http.ServeTLS",
                  "net/rpc", "net/smtp", "crypto/tls.Dial", "crypto/tls.DialWithDialer",
                  "crypto/tls.Listen", "google.golang.org/grpc"),
    # `plugin.Open` loads and runs a shared object: code from outside the
    # process image, which is the subprocess group's hazard in-process.
    "subprocess": ("os/exec", "os.StartProcess", "os.FindProcess", "plugin.Open"),
    # The standard library's own entry point, then the drivers a repo actually
    # has. That second half is best effort and says so: every driver bottoms
    # out in `net` or the filesystem, which the guard sees whatever this says.
    "database": ("database/sql.Open", "database/sql.OpenDB", "github.com/jackc/pgx",
                 "github.com/lib/pq", "go.mongodb.org/mongo-driver",
                 "github.com/redis/go-redis", "github.com/go-redis/redis", "gorm.io/gorm",
                 "github.com/jmoiron/sqlx", "github.com/mattn/go-sqlite3", "modernc.org/sqlite",
                 "go.etcd.io/bbolt", "github.com/dgraph-io/badger"),
}

# Markers that are VARIABLES, not functions. The import-time scan counts calls
# only -- `var client = http.DefaultClient` takes a pointer and does no I/O --
# so a package-level read of one of these would slip through without this.
_VARIABLE_MARKERS = ("os.Args",)


def _names(spec: str):
    return spec.split()


# EVERY EXPORTED NAME OF PACKAGE `syscall`, on darwin and on linux, and the
# group each one is. The filter applies it to `syscall.<Name>` and to
# `golang.org/x/sys/unix.<Name>`, whose names mirror it; `io_guard_go.py`
# applies it to decide which `syscall` functions get a hook. ONE COPY, used by
# both layers, so the two cannot disagree about what a syscall is.
#
# Captured 2026-09-12 from `GOOS=darwin|linux go doc -all syscall` on go
# 1.26.7 (272 names), and pinned by a DERIVED test in `test_stack_go.py` that
# re-reads the installed toolchain and fails on any name missing here.
#
#   fd     an operation on a descriptor that is already open. The OPEN was the
#          I/O and is classified; the read/write/close that follow are not,
#          or printing to stdout would be filesystem I/O.
#   stdin  `Read`, which the guard fires on only for fd 0.
#   pure   no system state at all -- conversions and parsers.
#   raw    an unclassifiable raw syscall: statically declined (Tier 4).
SYSCALL_GROUPS = {}
for _group, _spec in (
        ("filesystem", "Access Acct Chdir Chflags Chmod Chown Chroot Creat Exchangedata "
                       "Faccessat Fallocate Fchdir Fchflags Fchmod Fchmodat Fchown Fchownat "
                       "Fstatat Futimesat Getcwd Getdents Getdirentries Getfsstat Getwd "
                       "Getxattr InotifyAddWatch InotifyInit InotifyInit1 InotifyRmWatch "
                       "Lchown Link Listxattr Lstat Mkdir Mkdirat Mkfifo Mknod Mknodat Mount "
                       "Open Openat Pathconf PivotRoot ReadDirent Readlink Removexattr Rename "
                       "Renameat Revoke Rmdir Setxattr Stat Statfs Symlink Sync Truncate "
                       "Undelete Unlink Unlinkat Unmount Utime Utimes UtimesNano"),
        ("network", "Accept Accept4 AttachLsf Bind BindToDevice BpfBuflen BpfDatalink "
                    "BpfHeadercmpl BpfInterface BpfStats BpfTimeout CheckBpfVersion Connect "
                    "DetachLsf FlushBpf Getpeername Getsockname GetsockoptByte "
                    "GetsockoptICMPv6Filter GetsockoptInet4Addr GetsockoptInt GetsockoptIPMreq "
                    "GetsockoptIPMreqn GetsockoptIPv6Mreq GetsockoptIPv6MTUInfo GetsockoptUcred "
                    "Listen LsfSocket NetlinkRIB Recvfrom Recvmsg RouteRIB SetBpf SetBpfBuflen "
                    "SetBpfDatalink SetBpfHeadercmpl SetBpfImmediate SetBpfInterface "
                    "SetBpfPromisc SetBpfTimeout SetLsfPromisc Sendmsg SendmsgN Sendto "
                    "SetsockoptByte SetsockoptICMPv6Filter SetsockoptInet4Addr SetsockoptInt "
                    "SetsockoptIPMreq SetsockoptIPMreqn SetsockoptIPv6Mreq SetsockoptLinger "
                    "SetsockoptString SetsockoptTimeval Shutdown Socket Socketpair"),
        ("subprocess", "Exec ForkExec Kill PtraceAttach PtraceCont PtraceDetach "
                       "PtraceGetEventMsg PtraceGetRegs PtracePeekData PtracePeekText "
                       "PtracePokeData PtracePokeText PtraceSetOptions PtraceSetRegs "
                       "PtraceSingleStep PtraceSyscall Reboot Setprivexec StartProcess Tgkill "
                       "Unshare Wait4"),
        ("environment", "Clearenv Environ Getegid Getenv Geteuid Getgid Getgroups Getpgid "
                        "Getpgrp Getpid Getppid Getpriority Getrlimit Getrusage Getsid Gettid "
                        "Getuid Issetugid Klogctl Setdomainname Setegid Setenv Seteuid Setfsgid "
                        "Setfsuid Setgid Setgroups Sethostname Setlogin Setpgid Setpriority "
                        "Setregid Setresgid Setresuid Setreuid Setrlimit Setsid Setuid Sysctl "
                        "SysctlUint32 Sysinfo Umask Uname Unsetenv"),
        ("clock", "Adjtime Adjtimex Gettimeofday Nanosleep Settimeofday Time Times"),
        ("fd", "Close CloseOnExec Dup Dup2 Dup3 EpollCreate EpollCreate1 EpollCtl EpollWait "
               "FcntlFlock Fdatasync Flock Fpathconf Fstat Fstatfs Fsync Ftruncate Futimes "
               "Getdtablesize Getpagesize Kevent Kqueue Madvise Mlock Mlockall Mmap Mprotect "
               "Munlock Munlockall Munmap Pause Pipe Pipe2 Pread Pwrite Seek Select Sendfile "
               "SetKevent SetNonblock Splice SyncFileRange Tee Write Exit"),
        ("stdin", "Read"),
        ("pure", "BpfJump BpfStmt BytePtrFromString ByteSliceFromString CmsgLen CmsgSpace "
                 "LsfJump LsfStmt NsecToTimespec NsecToTimeval ParseDirent ParseNetlinkMessage "
                 "ParseNetlinkRouteAttr ParseRoutingMessage ParseRoutingSockaddr "
                 "ParseSocketControlMessage ParseUnixCredentials ParseUnixRights "
                 "SlicePtrFromStrings StringBytePtr StringByteSlice StringSlicePtr "
                 "TimespecToNsec TimevalToNsec UnixCredentials UnixRights"),
        ("raw", "AllThreadsSyscall AllThreadsSyscall6 RawSyscall RawSyscall6 Syscall Syscall6 "
                "Syscall9")):
    for _n in _names(_spec):
        SYSCALL_GROUPS[_n] = _group
del _group, _spec, _n

GROUPS = tuple(sorted(set(CONTROLLABLE) | set(UNCONTROLLABLE)))
_SYSCALL_PREFIXES = ("syscall", "golang.org/x/sys/unix")

# Declined before any marker is read, because NEITHER layer can see what they
# do -- the Go counterpart of Python's `os.exec*` static decline. A raw
# syscall names a number, not an operation; a cgo call runs C, where no Go
# hook reaches. An `x/sys/unix` member this table does not know is declined
# the same way: that package's surface is far wider than `syscall`'s, and a
# name nobody classified is exactly the case this exists for.
STATIC_DECLINE = {
    "raw syscall": tuple("%s.%s" % (p, n) for p in _SYSCALL_PREFIXES
                         for n in sorted(k for k, g in SYSCALL_GROUPS.items() if g == "raw"))
                   + ("golang.org/x/sys/unix.SyscallNoError",
                      "golang.org/x/sys/unix.RawSyscallNoError"),
    "cgo": ("cgo:C",),
}


def _marker_hit(marker: str, name: str) -> bool:
    return name == marker or name.startswith(marker + ".") or name.startswith(marker + "/")


def _syscall_member(name: str):
    """(prefix, member) when `name` is a `syscall`/`x/sys/unix` selector, else None."""
    for prefix in _SYSCALL_PREFIXES:
        if name.startswith(prefix + "."):
            return prefix, name[len(prefix) + 1:].split(".", 1)[0]
    return None


def _markers(names):
    """(group, marker, controllable) for every hit, statically-declined kinds first.

    `controllable` is None for a static decline. Deterministic order -- the
    decline kinds, then UNCONTROLLABLE groups, then CONTROLLABLE, each sorted
    -- so the reason a unit is given is the same on every run.
    """
    names = sorted(set(names))
    hits = []
    for kind in sorted(STATIC_DECLINE):
        for m in STATIC_DECLINE[kind]:
            if any(_marker_hit(m, n) for n in names):
                hits.append((kind, m.replace("cgo:", ""), None))
    for n in names:
        sm = _syscall_member(n)
        if sm and sm[0] == "golang.org/x/sys/unix" and sm[1] not in SYSCALL_GROUPS:
            hits.append(("raw syscall", "%s.%s" % sm, None))
    for table, controllable in ((UNCONTROLLABLE, False), (CONTROLLABLE, True)):
        for group in sorted(table):
            for m in table[group]:
                if any(_marker_hit(m, n) for n in names):
                    hits.append((group, m, controllable))
            for n in names:
                sm = _syscall_member(n)
                if sm and SYSCALL_GROUPS.get(sm[1]) == group:
                    hits.append((group, "%s.%s" % sm, controllable))
    return hits


# ── Resolving a file's names ─────────────────────────────────────────────

_VERSION_ELEM = re.compile(r"^v[0-9]+$")


def _default_package_name(path: str) -> str:
    """The name an unaliased import binds: `math/rand/v2` -> `rand`.

    Go binds the PACKAGE's declared name, which this cannot read, so it takes
    the convention that name follows: the last path element, skipping a major
    version suffix (`/v2`, `gopkg.in/yaml.v3`) and taking what follows a
    dash (`go-redis` -> `redis`, `go-sqlite3` -> `sqlite3`). Where a package
    breaks the convention its selectors resolve to nothing -- the filter
    under-reads, and the guard, which reads no names at all, still sees it.
    """
    elems = [e for e in path.split("/") if e]
    if not elems:
        return path
    last = elems[-1]
    if _VERSION_ELEM.match(last) and len(elems) > 1:
        last = elems[-2]
    last = re.sub(r"\.v[0-9]+$", "", last)
    if "-" in last:
        last = last.rsplit("-", 1)[-1]
    return last


def _file_aliases(text: str):
    """(alias -> import path, [dot-imported paths]) for one file."""
    alias, dots = {}, []
    for a, path in _imports(text):
        if a == "_":
            continue
        if a == ".":
            dots.append(path)
            continue
        alias[a or _default_package_name(path)] = path
    return alias, dots


_GO_KEYWORDS = frozenset({
    "break", "case", "chan", "const", "continue", "default", "defer", "else",
    "fallthrough", "for", "func", "go", "goto", "if", "import", "interface", "map",
    "package", "range", "return", "select", "struct", "switch", "type", "var"})


def _called_at(code: str, j: int, hi: int) -> bool:
    """True when the expression ending at `j` is called: `(` next, type arguments allowed."""
    t = _skip_ws(code, j)
    if t < hi and code[t] == "[":
        close = code.find("]", t)
        if 0 <= close < hi:
            t = _skip_ws(code, close + 1)
    return t < hi and code[t] == "("


def _scan(code: str, lo: int, hi: int, alias: dict, dots, recv=None):
    """(entries, calls) for the region [lo, hi) of stripped `code`.

    `entries` are `(resolved chain, called)` pairs for marker matching. Every
    occurrence is kept, not only calls -- `os.Args` is a real read with no
    call in sight -- and each caller filters as its rule needs.

    `calls` are the same-package callables the region may reach:
      * `helper` for a bare `helper(`;
      * `T.M` for `s.M(` where `s` is the enclosing method's receiver (`recv`
        is `(var, Type)`);
      * `("?", "M")` for any other `x.M(` -- resolved later, and only when
        exactly ONE type in the package defines `M` (`_unambiguous_methods`).
    """
    entries, calls = [], set()
    i = lo
    while i < hi:
        m = IDENTIFIER_RE.search(code, i, hi)
        if not m:
            break
        start, word = m.start(), m.group(0)
        i = m.end()
        qual = preceding_qualifier(code, start)
        if qual is not None:
            # A selector. Its chain head owns the marker; all this adds is the
            # method call on an unnameable receiver (`Store{}.flush()`).
            if qual == "" and _called_at(code, m.end(), hi):
                calls.add(("?", word))
            continue
        if word in _GO_KEYWORDS:
            continue
        parts, j = [word], m.end()
        while True:
            k = _skip_ws(code, j)
            if k < hi and code[k] == ".":
                nxt = _IDENT_AT.match(code, _skip_ws(code, k + 1))
                if not nxt or nxt.start() >= hi:
                    break
                parts.append(nxt.group(0))
                j = nxt.end()
            else:
                break
        called = _called_at(code, j, hi)
        tail = ".".join(parts[1:])
        if word == "C" and alias.get("C") == "C":
            entries.append(("cgo:C" + ("." + tail if tail else ""), called))
        elif word in alias:
            entries.append((alias[word] + ("." + tail if tail else ""), called))
        else:
            entries.append((".".join(parts), called))
            for path in dots:
                entries.append((path + "." + ".".join(parts), called))
        if called and len(parts) == 1:
            calls.add(word)
        elif called and len(parts) == 2 and word not in alias:
            if recv and word == recv[0]:
                calls.add("%s.%s" % (recv[1], parts[1]))
            else:
                calls.add(("?", parts[1]))
    return entries, calls


# ── Package analysis ─────────────────────────────────────────────────────

def _body_span(code: str, depths, pos: int):
    """(start, end) of the declaration at `pos`: signature through its body.

    The body is the first `{` at `pos`'s OWN depth -- skipping the braces of
    an `interface{}` or `struct{...}` result type -- and before the newline
    at that depth that ends a bodyless declaration (an assembly-implemented
    func). None when there is no body.

    RELATIVE to `pos`'s depth, not to 0: a function literal nested inside a
    composite literal (`map[string]func(){"k": func() {...}}`) sits at depth
    1, and a scan keyed to depth 0 walked straight past its body.
    """
    n = len(code)
    d0 = depths[pos]
    i = pos
    while i < n:
        c = code[i]
        if depths[i] < d0:
            return None
        if depths[i] == d0 and c == "\n":
            return None
        if depths[i] == d0 and c == "{":
            k = i - 1
            while k >= 0 and code[k].isspace():
                k -= 1
            word_end = k + 1
            while k >= 0 and (code[k].isalnum() or code[k] == "_"):
                k -= 1
            if code[k + 1:word_end] in ("interface", "struct"):
                close = _match_bracket(code, depths, i)
                if close < 0:
                    return None
                i = close + 1
                continue
            close = _match_bracket(code, depths, i)
            return (pos, close + 1 if close >= 0 else n)
        i += 1
    return None


def _receiver_var(inner: str):
    """`s *Store` -> `s`; `*Store` -> None (an unnamed receiver binds nothing)."""
    head = inner.split("[", 1)[0].split()
    return head[0] if len(head) == 2 and _IDENT_FULL.match(head[0]) else None


class _File:
    __slots__ = ("rel", "code", "depths", "alias", "dots", "decls")

    def __init__(self, rel, text):
        self.rel = rel
        self.code = strip_noncode(text)
        self.depths = _depths(self.code)
        self.alias, self.dots = _file_aliases(text)
        # qualified name -> (start, end, receiver (var, Type) or None)
        self.decls = {}
        for m in _FUNC_AT.finditer(self.code):
            pos = m.start()
            if self.depths[pos] != 0:
                continue
            i = _skip_ws(self.code, m.end())
            recv = None
            if i < len(self.code) and self.code[i] == "(":
                close = _match_bracket(self.code, self.depths, i)
                if close < 0:
                    continue
                inner = self.code[i + 1:close]
                rtype = _receiver_type(inner)
                if rtype is None:
                    continue
                recv = (_receiver_var(inner), rtype)
                i = _skip_ws(self.code, close + 1)
            word = _IDENT_AT.match(self.code, i)
            if not word:
                continue
            span = _body_span(self.code, self.depths, pos)
            if span is None:
                continue
            key = "%s.%s" % (recv[1], word.group(0)) if recv else word.group(0)
            if key == "init" and recv is None:
                key = "init#%d" % pos        # several `init`s may coexist
            self.decls.setdefault(key, (span[0], span[1], recv))


class _Package:
    __slots__ = ("files", "index", "methods", "tier4", "floor")


@functools.lru_cache(maxsize=None)
def _package_dirs(root: str):
    """dir -> [non-test .go files], for the whole tree, once per root."""
    out = {}
    for rel in iter_source_files(root):
        out.setdefault(os.path.dirname(rel), []).append(rel)
    return out


def _unambiguous_methods(files):
    """Method name -> the single `T.M` that defines it, across the package."""
    owners = {}
    for f in files:
        for key, (_lo, _hi, recv) in f.decls.items():
            if recv:
                owners.setdefault(key.split(".", 1)[1], set()).add(key)
    return {m: next(iter(ks)) for m, ks in owners.items() if len(ks) == 1}


def _resolve_calls(calls, pkg):
    out = set()
    for c in calls:
        if isinstance(c, tuple):
            q = pkg.methods.get(c[1])
            if q:
                out.add(q)
        else:
            out.add(c)
    return out


def _hits_from(entries, calls, pkg, visited):
    """Marker hits for one region, plus those reached through same-package calls.

    A callee is looked up in the PACKAGE index -- any file in the directory --
    and scanned with its OWN file's imports, because Go imports are
    file-scoped: `os` in `a.go` says nothing about what `os` means in `b.go`.
    Each hit is `(group, marker, controllable, via)`.
    """
    hits = [(g, m, c, None) for g, m, c in _markers([e for e, _called in entries])]
    for q in sorted(_resolve_calls(calls, pkg)):
        if q not in pkg.index or q in visited:
            continue
        visited.add(q)
        f, lo, hi, recv = pkg.index[q]
        sub_entries, sub_calls = _scan(f.code, lo, hi, f.alias, f.dots, recv)
        for g, m, c, _via in _hits_from(sub_entries, sub_calls, pkg, visited):
            hits.append((g, m, c, q))
    return hits


def _import_region(f: _File):
    """`f`'s code with every function body and import declaration blanked.

    What survives is what runs when the package is initialised: package-level
    `var` initializers. A function LITERAL assigned at package level
    (`var h = func() { ... }`) does not run then, so its body is blanked too
    -- otherwise every handler table in the repo would floor its package.

    A LITERAL, NOT A TYPE. In `map[string]func() string{"x": boot()}` the
    first `func` is part of the map's TYPE, and the `{` after it opens the
    map's VALUE -- where `boot()` really does run at import. Blanking from
    every `func` would hide that call. A literal stands where an expression
    starts, after one of `= ( , : {`; a function type follows `]` or a name.
    """
    out = list(f.code)

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    for lo, hi, _recv in f.decls.values():
        blank(lo, hi)
    for m in _IMPORT_KW.finditer(f.code):
        i = _skip_ws(f.code, m.end())
        if i < len(f.code) and f.code[i] == "(":
            close = _match_bracket(f.code, f.depths, i)
            blank(m.start(), (close + 1) if close >= 0 else len(f.code))
        else:
            eol = f.code.find("\n", m.end())
            blank(m.start(), eol if eol >= 0 else len(f.code))
    code = "".join(out)
    depths = _depths(code)
    for m in re.finditer(r"(?<![A-Za-z0-9_])func\b", code):
        k = m.start() - 1
        while k >= 0 and code[k].isspace():
            k -= 1
        if k < 0 or code[k] not in "=(,:{":
            continue                        # a function TYPE, not a literal
        span = _body_span(code, depths, m.start())
        if span:
            blank(span[0], span[1])
    return "".join(out)


@functools.lru_cache(maxsize=None)
def _analyze_package(root: str, pkg_dir: str) -> _Package:
    """Index and floor one package directory, once, for every unit in it."""
    pkg = _Package()
    pkg.files = {}
    for rel in _package_dirs(root).get(pkg_dir, []):
        pkg.files[rel] = _File(rel, read_text(root, rel))
    pkg.index = {}
    for f in pkg.files.values():
        for key, (lo, hi, recv) in f.decls.items():
            if not key.startswith("init#"):
                pkg.index.setdefault(key, (f, lo, hi, recv))
    pkg.methods = _unambiguous_methods(pkg.files.values())

    mod_hits = []
    for f in sorted(pkg.files.values(), key=lambda x: x.rel):
        region = _import_region(f)
        entries, calls = _scan(region, 0, len(region), f.alias, f.dots)
        entries = [(e, c) for e, c in entries
                   if c or any(_marker_hit(v, e) for v in _VARIABLE_MARKERS)]
        mod_hits += _hits_from(entries, calls, pkg, set())
        for key, (lo, hi, recv) in f.decls.items():
            if key.startswith("init#"):
                e2, c2 = _scan(f.code, lo, hi, f.alias, f.dots, recv)
                mod_hits += [(g, m, c, v or "init") for g, m, c, v
                             in _hits_from(e2, c2, pkg, set())]

    pkg.tier4, pkg.floor = None, None
    hard = [h for h in mod_hits if h[2] is not True]
    if hard:
        group, marker, _c, via = hard[0]
        pkg.tier4 = (4, "package does %s I/O at import time%s (%s); not reachable"
                        % (group, " via " + via if via else "", marker))
    soft = [h for h in mod_hits if h[2] is True]
    if soft:
        group, marker, _c, via = soft[0]
        pkg.floor = (3, "package does %s I/O at import time%s (%s); a fixture runs "
                        "too late to control it — needs a seam"
                        % (group, " via " + via if via else "", marker))
    return pkg


def _tier_from_hits(hits) -> tuple:
    """(tier, reason) from a unit's own hits, before the package floor."""
    if not hits:
        return 1, "no I/O markers; directly callable"
    declined = [h for h in hits if h[2] is None]
    if declined:
        kind, marker, _c, via = declined[0]
        return 4, ("%s (%s)%s: neither the filter nor the guard can see what it does"
                   % (kind, marker, " via " + via if via else ""))
    uncontrollable = [h for h in hits if h[2] is False]
    if uncontrollable:
        group, marker, _c, via = uncontrollable[0]
        if via:
            return 3, "%s I/O via %s (%s); needs a seam" % (group, via, marker)
        return 3, "%s I/O inside the unit (%s); needs a seam" % (group, marker)
    group, marker, _c, via = hits[0]
    if via:
        return 2, ("%s I/O via %s (%s); pin at a wider boundary with %s controlled"
                   % (group, via, marker, group))
    return 2, "%s I/O (%s); pin at a wider boundary with %s controlled" % (group, marker, group)


def triage(root: str, unit) -> tuple:
    """Classify how testable a unit is. Returns (tier, reason).

    A FILTER, NEVER THE ENFORCEMENT. It resolves each file's imports, chases
    same-package calls across the whole directory (a bare `helper(`, the
    receiver's own methods, and a method name exactly one type defines), and
    floors every unit in a package whose initialisation does I/O -- because
    every file's `init` and package-level `var` runs when the package is
    imported, before any test body or fixture. What it cannot see -- an
    interface method whose implementation is chosen at run time, a function
    value, reflection, a method two types define -- is the points-to boundary
    it stops at on purpose. `io_guard_go.py` is the enforcement.

    Wrong toward a HIGHER tier wherever it is wrong: every occurrence of a
    marker inside a unit counts, not only a call.
    """
    pkg = _analyze_package(root, os.path.dirname(unit["path"]))
    if pkg.tier4 is not None:
        return pkg.tier4
    f = pkg.files.get(unit["path"])
    decl = f.decls.get(unit["name"]) if f else None
    if decl is None:
        return 4, "unit not found on re-read"
    lo, hi, recv = decl
    entries, calls = _scan(f.code, lo, hi, f.alias, f.dots, recv)
    hits = _hits_from(entries, calls, pkg, {unit["name"]})
    tier, reason = _tier_from_hits(hits)
    if pkg.floor is not None and tier < pkg.floor[0]:
        return pkg.floor
    return tier, reason
