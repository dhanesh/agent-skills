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
