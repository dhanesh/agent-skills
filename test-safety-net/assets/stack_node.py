#!/usr/bin/env python3
r"""The node/TypeScript stack: what a JS or TS repo means by each interface name.

Everything here is language-specific and everything language-specific is here;
the stack-agnostic half -- churn, reference counting, coverage detection,
scoring, ranking, the CLI, the JSON shape -- stays in `rank_risk.py`, which
documents the interface the two meet at.

HEURISTIC BY DESIGN, AND THE DIRECTION IT FAILS IN. Node exposes no public
parser API, so there is no `ast` to reach for the way `stack_python` does
(multistack design, D1). Discovery here is a comment/string stripper plus
bracket matching, and Task 3 adds a precise path that drives the repo's own
`typescript` when it has one, falling back to `_units_heuristic` when it does
not. That makes every predicate below an approximation, and the architecture
that makes an approximation acceptable is the one the original design already
ruled: static triage is a FILTER and the runtime guard is the ENFORCEMENT. So
an imprecise heuristic is acceptable in the direction of MORE reported work --
a phantom unit costs the user a glance -- and never in the direction of less,
where the cost is the bug the missing test would have caught. Every judgement
call below is recorded with the direction it fails in.

NOT REGISTERED IN `rank_risk.STACKS` YET. `triage` is the fourteenth interface
name and it is Task 4's; a stack without it raises `AttributeError` inside
`rank()` for any repo it claims. Registration therefore lands with `triage`,
not here. Until then a node repo still falls through to `stack_python` and
reports zero units, which is the silent zero this whole change exists to close
-- stated plainly rather than half-closed.
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

from stack_common import SKIP_DIRS, read_text                        # noqa: E402

STACK_NAME = "node"

# Extensions this stack calls source. `.d.ts` is excluded (see
# `iter_source_files`), and so is everything a bundler would generate --
# `SKIP_DIRS` already carries `node_modules`, `dist`, `build` and `vendor`.
SOURCE_EXTS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts")

# Directory names that make everything under them a test. `__mocks__` is
# deliberately absent: a mock is neither a test nor a unit's home, and calling
# it a test would let it credit coverage it cannot provide.
TEST_DIRS = frozenset({"__tests__", "test", "tests", "spec", "specs",
                       "e2e", "cypress"})

# Directory names a project uses as the root of its source tree. Only used to
# line a test tree up with a source tree (`is_test_for`); never to decide what
# is source.
SRC_ROOTS = frozenset({"src", "lib", "app", "source"})

_TEST_FILE_RE = re.compile(r"(?:^|[.\-_])(?:test|spec)s?$")


# ── Identity ─────────────────────────────────────────────────────────────

def matches(root: str) -> bool:
    """True when `root` looks like a JS/TS repo.

    A manifest is the strong signal; a bare source file is the fallback, for
    the script directories and scaffolds that carry no `package.json`. Both
    are deliberately generous, because `rank_risk.detect_stack` takes the
    FIRST match and a repo that is genuinely node reporting as Python
    discovers zero units and reads as a clean result.

    The cost of that generosity is real and is why registration order matters:
    a mostly-Python repo holding one `.mjs` helper matches here too.
    """
    for name in ("package.json", "tsconfig.json", "jsconfig.json"):
        if os.path.isfile(os.path.join(root, name)):
            return True
    for _rel in iter_source_files(root):
        return True
    return False


# ── Files ────────────────────────────────────────────────────────────────

def is_test_path(rel: str) -> bool:
    """True for a path a JS test runner would collect rather than a unit's home.

    Covers the idioms jest, vitest and `node --test` collect by default:
    anything under `__tests__/`, `test/`, `tests/`, `spec/`, `e2e/` or
    `cypress/`, and any `*.test.*` / `*.spec.*` / `*-test.*` / `*_test.*`
    file.

    NARROW ON PURPOSE. This predicate fails in the dangerous direction when it
    is too WIDE, not too narrow: a source file mistaken for a test is dropped
    from discovery (a unit that vanishes), and a source file mistaken for a
    test is also read as coverage evidence (a gap that vanishes). Both are
    silent. A test file mistaken for source only adds a phantom unit and
    withholds one file's worth of coverage evidence, which is the survivable
    direction. So this recognises the conventional forms and guesses at
    nothing beyond them.
    """
    parts = rel.replace(os.sep, "/").split("/")
    if any(p in TEST_DIRS for p in parts[:-1]):
        return True
    return bool(_TEST_FILE_RE.search(_strip_ext(parts[-1])))


def iter_source_files(root: str, include_tests: bool = False):
    """Yield repo-relative paths of JS/TS source files, skipping vendor dirs. Sorted.

    `.d.ts` files are excluded. A declaration file has no bodies -- there is
    nothing in it a characterization test could pin, and its declarations name
    the implementation that lives in a real source file, which IS discovered.
    Including them would report a unit per declared symbol, every one of them
    untestable. The one thing that exclusion can hide is a package whose only
    implementation is a prebuilt bundle under `dist/`, which `SKIP_DIRS`
    already skips for the same reason: generated code is not what a person
    edits, and this ranker exists to protect what they edit.
    """
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name.endswith(".d.ts") or name.endswith(".d.mts") or name.endswith(".d.cts"):
                continue
            if not name.endswith(SOURCE_EXTS):
                continue
            rel = os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
            if not include_tests and is_test_path(rel):
                continue
            out.append(rel)
    return sorted(out)


def _strip_ext(name: str) -> str:
    for ext in SOURCE_EXTS:
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _dir_key(rel_dir: str, is_test: bool) -> str:
    """A directory reduced to the part a test tree and a source tree share."""
    parts = [p for p in rel_dir.replace(os.sep, "/").split("/") if p and p != "."]
    if is_test:
        parts = [p for p in parts if p not in TEST_DIRS]
    if parts and parts[0] in SRC_ROOTS:
        parts = parts[1:]
    return "/".join(parts)


def _test_subject(test_rel: str) -> str:
    """The module name a test file's own name claims to be about, or ""."""
    base = _strip_ext(os.path.basename(test_rel))
    m = re.match(r"^(.+?)[.\-_](?:test|spec)s?$", base)
    if m:
        return m.group(1)
    if _TEST_FILE_RE.search(base):
        return ""                    # `test.js`, `spec.ts`: names no subject
    return base                      # `__tests__/utils.ts`


def is_test_for(test_rel: str, src_rel: str) -> bool:
    """True when `test_rel` is positioned to be a test OF `src_rel`.

    Node has three idioms where Python has one, and `already_covered` needs
    all three or its strongest evidence route credits nothing:

      beside      src/utils.ts        <- src/utils.test.ts
      sibling dir src/utils.ts        <- src/__tests__/utils.test.ts
      mirrored    src/a/utils.ts      <- test/a/utils.test.ts

    RULING. Widening the POSITION rule alone would have re-opened the
    over-credit that the Python branch's fix round 4 closed. That fix rests on
    "the colliding sibling is by definition in a different directory", which is
    true only while this predicate means same-directory: the moment
    `src/__tests__/` and a mirrored `test/` tree count, one test file is
    positioned for two colliding sources at once, and the guard meant to catch
    that (`rivals`, via `path_pattern`) is this stack's WEAKEST predicate,
    because a node test importing `"../utils"` never spells out `src/utils`.
    So the widening is paid for by a NARROWING in the other axis: the test
    file's own name must name the module (`utils.test.ts` for `utils.ts`,
    `index.test.ts` for `index.ts`). Position and name together are still only
    position -- whether the test exercises a given unit is
    `reached_through_module`'s question, which resolves the import specifier
    against these same two paths and is the predicate that actually
    discriminates two same-named files.
    """
    subject = _test_subject(test_rel)
    if not subject:
        return False
    if subject != module_of(src_rel) and subject != _strip_ext(os.path.basename(src_rel)):
        return False
    return (_dir_key(os.path.dirname(test_rel), True)
            == _dir_key(os.path.dirname(src_rel), False))


# ── Naming ───────────────────────────────────────────────────────────────

def module_of(rel: str) -> str:
    """The module identity `rel` defines.

    The basename without its extension, except that `foo/index.ts` answers
    `foo`: an index file IS its directory as far as every importer is
    concerned, so `foo/index.ts` and `foo.ts` are the same specifier and must
    collide. Answering `index` instead would map every index file in a repo to
    one module string, making all of them ambiguous at once and pushing them
    onto the path-qualified route -- the route this stack is weakest on.

    Bare token, necessarily: the core feeds this to `name_pattern`, so a
    leading `.` or `@` would match nothing and credit nothing.
    """
    stem = _strip_ext(os.path.basename(rel))
    if stem == "index":
        parent = os.path.basename(os.path.dirname(rel))
        if parent:
            return parent
    return stem


def name_pattern(name: str):
    r"""`name` as a whole identifier, bounded the way JS bounds one.

    Not `\b`: `\b` is defined against `[A-Za-z0-9_]`, so it does not know `$`
    is an identifier character here and `\b\$fetch\b` matches nothing in
    `$fetch(1)`. Explicit lookaround including `$` is the whole fix.
    """
    return re.compile(r"(?<![A-Za-z0-9_$])%s(?![A-Za-z0-9_$])" % re.escape(name))


def path_pattern(rel: str):
    """A regex matching `rel` written as a module path, bounded at both ends.

    `src/utils.ts` -> `src[./]utils`; `src/foo/index.ts` -> `src[./]foo`,
    because that is the specifier an importer writes.

    EXPECT THIS TO MATCH RARELY, and understand which way that fails. Python
    import statements spell the package path, so `app.utils` appears verbatim
    in a test file. Node specifiers are relative: a test importing `"../utils"`
    never contains the string `src/utils`, so under a basename collision this
    route usually produces no evidence at all and the unit reads as uncovered.
    That is the under-credit direction -- a redundant test, never a hidden gap
    -- and it is why `is_test_for` plus `reached_through_module` carries
    essentially all of this stack's collision-case coverage, the opposite of
    Python where the same-directory route is the fallback.
    """
    stem = _strip_ext(rel.replace(os.sep, "/"))
    parts = [p for p in stem.split("/") if p]
    if len(parts) > 1 and parts[-1] == "index":
        parts = parts[:-1]
    if len(parts) < 2:
        return None
    body = r"[./]".join(re.escape(p) for p in parts)
    return re.compile(r"(?<![A-Za-z0-9_$.])%s(?![A-Za-z0-9_$])" % body)


# ── Lexing ───────────────────────────────────────────────────────────────

# `$` is an identifier character in JS, and a leading one is common enough
# (`$`, `$fetch`, `$state`) that omitting it would make those names invisible.
# The lookbehind is the same device `stack_python` uses instead of `\b`, and
# for the same reason: `2x` must not yield a reference to `x`.
IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_$])[A-Za-z_$][A-Za-z0-9_$]*")


def preceding_qualifier(text: str, start: int):
    """The receiver an identifier at `start` is a property OF, or None.

    None    -- the identifier stands on its own (`parse(...)`, `import parse`).
    "utils" -- it was written `utils.parse` or `utils?.parse`.
    ""      -- it is a property of something unnameable (`mk().parse`,
               `d["k"].parse`), which can never be shown to be the module.

    Optional chaining is the only addition to the Python rule: `a?.b` is `a.b`
    for this question, and treating the `?` as unnameable would drop a whole
    calling idiom into the "" bucket, which never counts.
    """
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0 or text[i] != ".":
        return None
    j = i - 1
    while j >= 0 and text[j].isspace():
        j -= 1
    if j >= 0 and text[j] == "?":
        j -= 1
        while j >= 0 and text[j].isspace():
            j -= 1
    end = j + 1
    while j >= 0 and (text[j].isalnum() or text[j] in "_$"):
        j -= 1
    qual = text[j + 1:end]
    return qual if qual and not qual[0].isdigit() else ""


# ── The comment/string stripper ──────────────────────────────────────────

# A `/` that starts a regex literal can only follow an operator or the start
# of a statement; after a value (`)`, `]`, an identifier, a number, a string)
# it is division. Getting this wrong in the "it is a regex" direction would
# blank real code, so the set is the conservative one.
_REGEX_PREV = frozenset("(,=:[!&|?{};+-*%^~<>") | {""}
_REGEX_PREV_WORDS = frozenset({"return", "typeof", "instanceof", "in", "of",
                               "new", "delete", "void", "throw", "case",
                               "do", "else", "yield", "await"})


@functools.lru_cache(maxsize=256)
def strip_noncode(text: str, keep_strings: bool = False) -> str:
    r"""`text` with comments and string literals blanked, offsets preserved.

    `keep_strings=True` leaves `'...'` and `"..."` bodies in place and blanks
    everything else. The import grammar needs it: a module specifier IS a
    string literal, so reading imports off the fully blanked text finds every
    `import ... from` and no module to attach it to -- which credits nothing,
    silently. Comments are still gone in that mode, so a commented-out import
    still binds nothing.

    Every blanked character becomes a space except newlines, which survive, so
    a position in the result is the same position in the original and line
    numbers still hold. Matching against the raw text instead would report a
    phantom unit for an `export function` inside a template literal and read a
    `//` in a `https://` URL as the start of a comment.

    Deliberately NOT a tokenizer. It tracks five states -- `'`, `"`, a
    template literal with its `${...}` interpolations, `//` and `/* */` -- and
    it tells a regex literal from division by the preceding token. Where it
    can be wrong, it is bounded so that it is wrong toward LEAVING TEXT
    VISIBLE (a phantom unit) rather than blanking code (a unit that vanishes):

      * `'` and `"` literals never cross a newline. JS permits a backslash
        line continuation inside one; a file using that gets one line read as
        code rather than as string, which can only add a phantom.
      * A regex literal must close on its own line, which is the language rule
        anyway. If it does not, the `/` is left as division and its contents
        stay visible.
      * A `/*` or a template literal with no terminator runs to end of file.
        Both are unterminated in the source too, so the file does not compile.

    Cached: `already_covered` re-reads the same handful of test files once per
    unit, and stripping them each time dominated the run.
    """
    n = len(text)
    out = list(text)
    i = 0
    prev = ""            # last significant code character
    prev_word = ""       # last identifier ending at `prev`, for regex detection
    modes = ["code"]     # top of stack: "code" or "template"
    interp = []          # bracket depth at each open `${`
    depth = 0

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]

        if modes[-1] == "template":
            if c == "\\":
                blank(i, min(i + 2, n))
                i += 2
                continue
            if c == "`":
                blank(i, i + 1)
                modes.pop()
                prev, prev_word = "`", ""
                i += 1
                continue
            if c == "$" and text[i + 1:i + 2] == "{":
                blank(i, i + 2)
                modes.append("code")
                interp.append(depth)
                depth += 1
                prev, prev_word = "{", ""
                i += 2
                continue
            blank(i, i + 1)
            i += 1
            continue

        # code
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
            prev, prev_word = "*", ""
            continue
        if c in "'\"":
            j = _quote_end(text, i, n)
            if j < 0:                       # unterminated on this line
                if not keep_strings:
                    blank(i, i + 1)
                i += 1
                prev, prev_word = "'", ""
                continue
            if not keep_strings:
                blank(i, j + 1)
            i = j + 1
            prev, prev_word = "'", ""
            continue
        if c == "`":
            blank(i, i + 1)
            modes.append("template")
            i += 1
            continue
        if c == "/" and (prev in _REGEX_PREV or prev_word in _REGEX_PREV_WORDS):
            j = _regex_end(text, i, n)
            if j >= 0:
                blank(i, j + 1)
                i = j + 1
                prev, prev_word = "/", ""
                continue
        if c in "{([":
            depth += 1
        elif c in "})]":
            depth = max(depth - 1, 0)
            if c == "}" and interp and len(modes) > 1 and depth == interp[-1]:
                interp.pop()
                modes.pop()
                blank(i, i + 1)
                prev, prev_word = "`", ""
                i += 1
                continue
        if not c.isspace():
            prev = c
            if c.isalnum() or c in "_$":
                m = _IDENT_AT.match(text, i)
                prev_word = m.group(0) if m else ""
            else:
                prev_word = ""
        i += 1
    return "".join(out)


def _quote_end(text: str, i: int, n: int) -> int:
    """Index of the closing quote of the literal opening at `i`, or -1."""
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


def _regex_end(text: str, i: int, n: int) -> int:
    """Index of the `/` closing the regex literal opening at `i`, or -1.

    A `/` inside a `[...]` character class does not close it, and a regex
    literal cannot span a line -- so an unterminated one means this `/` was
    division after all, and the caller leaves the text alone.
    """
    j = i + 1
    in_class = False
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return -1
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            return j if j > i + 1 else -1     # `//` is a comment, not an empty regex
        j += 1
    return -1


def _depths(text: str):
    """Bracket depth per index; an opener and its closer both carry the OUTER depth."""
    d = [0] * (len(text) + 1)
    cur = 0
    for i, ch in enumerate(text):
        if ch in "{([":
            d[i] = cur
            cur += 1
        elif ch in "})]":
            cur = max(cur - 1, 0)
            d[i] = cur
        else:
            d[i] = cur
    d[len(text)] = cur
    return d


_IDENT_AT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_IDENT_FULL = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _skip_ws(text: str, i: int) -> int:
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    return i


def _word_at(text: str, i: int):
    """(identifier at/after `i`, index just past it), or (None, i)."""
    i = _skip_ws(text, i)
    m = _IDENT_AT.match(text, i)
    return (m.group(0), m.end()) if m else (None, i)


def _match_bracket(text: str, depths, i: int) -> int:
    """Index of the bracket closing the one at `i`, or -1."""
    close = {"{": "}", "(": ")", "[": "]"}[text[i]]
    d = depths[i]
    for j in range(i + 1, len(text)):
        if text[j] == close and depths[j] == d:
            return j
    return -1


# ── Import/binding grammar ───────────────────────────────────────────────

_IMPORT_RE = re.compile(
    r"(?<![A-Za-z0-9_$.])import\s+(?P<clause>[^;'\"]*?)\s*from\s*['\"](?P<spec>[^'\"]+)['\"]")
_REQUIRE_RE = re.compile(
    r"(?<![A-Za-z0-9_$.])(?:const|let|var)\s+(?P<clause>\{[^}]*\}|[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*=\s*(?:await\s+)?(?:require|import)\s*\(\s*['\"](?P<spec>[^'\"]+)['\"]\s*\)")


def _specifier_matches(spec: str, module: str, src_rel: str, ref_rel: str) -> bool:
    """Does the import specifier `spec`, written in `ref_rel`, name `src_rel`?

    A RELATIVE specifier is resolved against the referencing file's own
    directory and compared to the defining file exactly -- extensionless, and
    with `foo` also matching `foo/index`. That exactness is the whole reason
    the interface hands these two functions their file context: without it,
    `"../utils"` is indistinguishable from any other `utils` in the repo, and
    a test for one package's `utils` credits every other package's.

    A NON-RELATIVE specifier (`@scope/pkg`, `#internal/x`, a `tsconfig`
    path alias, a bare package) cannot be resolved without reading the
    project's own resolution config, which this function is not given. It
    falls back to comparing the specifier's last segment to the module name.
    Note which way that fails: an alias like `@app/utils` matches any `utils`
    in the repo (over-credit, bounded by the caller's `rivals` check), while
    a cross-package import like `@app/a` does NOT match `a/src/utils.ts`
    (under-credit). The under-credit case is the common one.
    """
    src = _strip_ext(src_rel.replace(os.sep, "/"))
    if spec.startswith("."):
        base = os.path.dirname(ref_rel.replace(os.sep, "/"))
        target = os.path.normpath(os.path.join(base, spec)).replace(os.sep, "/")
        target = _strip_ext(target)
        return target == src or target + "/index" == src
    tail = _strip_ext(spec.rstrip("/").split("/")[-1])
    return tail == module


def _clause_bindings(clause: str):
    """(aliases, names) a single import clause binds, ignoring the specifier."""
    aliases, names = set(), set()
    clause = clause.strip()
    if not clause:
        return aliases, names                     # side-effect import
    brace = clause.find("{")
    head = clause[:brace] if brace >= 0 else clause
    body = ""
    if brace >= 0:
        end = clause.find("}", brace)
        body = clause[brace + 1:end if end >= 0 else len(clause)]
    for piece in head.split(","):
        piece = piece.strip().rstrip(",").strip()
        if not piece:
            continue
        parts = piece.split()
        if len(parts) >= 3 and parts[0] == "*" and parts[1] == "as":
            aliases.add(parts[2])                 # import * as utils
        elif len(parts) == 1 and _IDENT_FULL.match(parts[0]):
            aliases.add(parts[0])                 # default import
    for piece in body.split(","):
        parts = piece.strip().split()
        if not parts:
            continue
        if parts[0] == "type":
            continue                              # `import { type Foo }`: erased
        if len(parts) >= 3 and parts[1] == "as":
            cand = parts[2]
        elif len(parts) == 1:
            cand = parts[0]
        else:
            continue
        cand = cand.strip(":").strip()
        if _IDENT_FULL.match(cand):
            names.add(cand)
    return aliases, names


def module_bindings(module: str, text: str, *, src_rel: str, ref_rel: str) -> tuple:
    """(aliases, names) `text` binds for `module` by an actual import.

    `aliases` are the names the module object itself is bound to
    (`import * as utils`, `const utils = require("./utils")`, a default
    import); `names` are the members pulled out of it (`import { parse }`,
    `const { parse } = require(...)`).

    Reads the text with COMMENTS stripped and string literals kept, because
    the specifier is itself a string. A commented-out import therefore binds
    nothing -- the same ruling the Python stack reached from the other end,
    where its regexes are line-anchored. What that cannot rule out is an
    import statement quoted inside another string, which is the residue the
    Python stack carries too.
    """
    stripped = strip_noncode(text, keep_strings=True)
    aliases, names = set(), set()
    for rx in (_IMPORT_RE, _REQUIRE_RE):
        for m in rx.finditer(stripped):
            if not _specifier_matches(m.group("spec"), module, src_rel, ref_rel):
                continue
            a, n = _clause_bindings(m.group("clause"))
            aliases |= a
            names |= n
    return tuple(sorted(aliases)), tuple(sorted(names))


def reached_through_module(module: str, name: str, text: str, *,
                           src_rel: str, ref_rel: str) -> bool:
    """True when `text` CALLS `name` through an import of `module`.

    The call site is the point, carried across from the Python stack's fix
    round 7: matching the bare chain credited `jest.mock("../utils")` -- proof
    the unit is STUBBED, the opposite of coverage -- and a `// utils.parse`
    comment. Requiring `(` after the name closes both, and `new Name(` matches
    it too, which is how a test exercises an exported class.
    """
    aliases, names = module_bindings(module, text, src_rel=src_rel, ref_rel=ref_rel)
    if not aliases and not names:
        return False
    stripped = strip_noncode(text)
    esc = re.escape(name)
    if name in names and re.search(r"(?<![A-Za-z0-9_$.])%s\s*\(" % esc, stripped):
        return True
    return any(re.search(r"(?<![A-Za-z0-9_$.])%s\s*\??\s*\.\s*%s\s*\("
                         % (re.escape(alias), esc), stripped)
               for alias in aliases)


# ── Discovery ────────────────────────────────────────────────────────────

# Words after `export` that introduce something with no runtime body: a type,
# an interface, an ambient declaration, a namespace. None of them is callable,
# so none of them is a unit -- exactly as `stack_python` discovers defs and
# classes and not module-level constants.
_TYPE_ONLY = frozenset({"type", "interface", "declare", "namespace", "module",
                        "enum"})

_EXPORT_KW_RE = re.compile(r"(?<![A-Za-z0-9_$.])export(?![A-Za-z0-9_$])")
_CJS_HEAD_RE = re.compile(
    r"(?<![A-Za-z0-9_$.])(?:module\s*\.\s*exports|exports)(?![A-Za-z0-9_$])")
_DECL_RE = re.compile(
    r"(?<![A-Za-z0-9_$.])(?:(?:async\s+)?function\s*\*?\s*|class\s+)"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)")
_VARDECL_RE = re.compile(
    r"(?<![A-Za-z0-9_$.])(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)")
_STMT_KEYWORDS = ("export", "import", "const", "let", "var", "function",
                  "class", "module", "exports", "return", "async")


def discover_units(root: str):
    """Interface entry point: `(units, discovery_path)`.

    Always `"heuristic"` here. Task 3 adds the precise path -- the repo's own
    `typescript`, when it has one -- and this becomes the fallback. The label
    is reported rather than assumed because a run that silently degraded is
    not comparable to one that did not (multistack design, D1).
    """
    return _units_heuristic(root), "heuristic"


def _units_heuristic(root: str):
    """Exported top-level functions and classes in non-test source files, sorted by id.

    TOP LEVEL means bracket depth zero, which is what excludes a function
    nested inside an exported one: a test runner cannot address it by name, so
    it cannot be pinned on its own. That is the same rule `stack_python`
    applies by only walking `tree.body`.

    EXPORTED replaces Python's "non-underscore": JS states its public surface
    explicitly, so the `export` keyword IS the visibility rule and there is no
    convention left to read. A `_`-prefixed name that a file deliberately
    exports is therefore kept, unlike Python's -- it is on the public surface
    by the language's own answer, and dropping it would be a unit that
    vanishes.
    """
    units = []
    for rel in iter_source_files(root):
        units.extend(_units_in_text(rel, read_text(root, rel)))
    return sorted(units, key=lambda u: u["id"])


def _units_in_text(rel: str, text: str):
    """The units one file's text defines. Public for the tests and for Task 3."""
    stripped = strip_noncode(text)
    depths = _depths(stripped)
    defs = _local_defs(stripped, depths)
    found = []
    for m in _EXPORT_KW_RE.finditer(stripped):
        if depths[m.start()] == 0:
            found.extend(_parse_export(stripped, depths, defs, m.start(), m.end()))
    for m in _CJS_HEAD_RE.finditer(stripped):
        if depths[m.start()] == 0:
            found.extend(_parse_cjs(stripped, depths, defs, m.start(), m.end()))

    starts = _line_starts(stripped)
    units, seen = [], set()
    for name, kind, pos in sorted(found, key=lambda t: (t[2], t[0])):
        if name in seen:
            continue                # one file, one unit per exported name
        seen.add(name)
        units.append({
            "id": "%s::%s" % (rel, name),
            "path": rel,
            "name": name,
            "lineno": bisect.bisect_right(starts, pos),
            "kind": kind,
        })
    return units


def _line_starts(text: str):
    starts = [0]
    i = text.find("\n")
    while i >= 0:
        starts.append(i + 1)
        i = text.find("\n", i + 1)
    return starts


def _local_defs(text: str, depths) -> dict:
    """name -> (kind, position) for every top-level declaration in the file.

    Used to give a name its kind and its real line when the export names it
    somewhere else -- `export { parse }`, `module.exports = { parse }`,
    `exports.parse = parse`.
    """
    defs = {}
    for m in _DECL_RE.finditer(text):
        if depths[m.start()] != 0:
            continue
        kind = "class" if text[m.start():m.start() + 5] == "class" else "function"
        defs.setdefault(m.group("name"), (kind, m.start()))
    for m in _VARDECL_RE.finditer(text):
        if depths[m.start()] != 0:
            continue
        kind = _value_kind(text, depths, None, m.end())
        if kind:
            defs.setdefault(m.group("name"), (kind, m.start()))
    return defs


def _stmt_end(text: str, depths, pos: int) -> int:
    """Where the statement starting around `pos` stops, for value classification."""
    n = len(text)
    d0 = depths[pos] if pos < n else 0
    limit = min(n, pos + 2000)
    i = pos
    while i < limit:
        c = text[i]
        if depths[i] < d0:
            return i
        if c == ";" and depths[i] == d0:
            return i
        if c == "\n" and depths[i] == d0:
            j = _skip_ws(text, i)
            word = _IDENT_AT.match(text, j)
            if word and word.group(0) in _STMT_KEYWORDS:
                return i
        i += 1
    return limit


def _value_kind(text: str, depths, defs, pos: int):
    """The kind of the value assigned at/after `pos`, or None if not callable.

    `pos` is just past the declared name, so a TypeScript type annotation may
    still stand between here and the `=`. The assignment operator is found by
    skipping anything that is a comparison or an arrow, which is what lets
    `export const f: () => void = ...` be read correctly.
    """
    n = len(text)
    end = _stmt_end(text, depths, pos)
    d0 = depths[pos] if pos < n else 0
    eq = -1
    i = pos
    while i < end:
        c = text[i]
        if c == "=" and depths[i] == d0 and text[i + 1:i + 2] not in ("=", ">") \
                and (i == 0 or text[i - 1] not in "=!<>+-*/%&|^"):
            eq = i
            break
        i += 1
    if eq < 0:
        return None
    return _classify_value(text, depths, defs, eq + 1, end)


def _classify_value(text: str, depths, defs, vpos: int, end: int):
    """"function" | "class" | None for the value between `vpos` and `end`.

    Recognises what the plan named -- a function expression, an arrow, a class
    -- plus two forms it did not, each added because omitting it loses a unit
    rather than adding one:

      * a CALL expression (`export const router = makeRouter()`), which is how
        a factory-produced function is written. This over-reports the config
        objects built the same way, and that is the direction to be wrong in.
      * a bare identifier that resolves to a function or class declared in the
        same file (`exports.parse = parse`), which is CommonJS's normal shape.

    Everything else -- a number, a string, an object or array literal, `new
    X()` -- is a value, not a unit, exactly as `stack_python` discovers no
    module-level constants. The miss that leaves is a function produced by an
    expression this cannot read; Task 3's precise path closes it where the
    repo ships a toolchain.
    """
    v = _skip_ws(text, vpos)
    if v >= end:
        return None
    d0 = depths[v]
    arrow = text.find("=>", v, end)
    while arrow >= 0:
        if depths[arrow] == d0:
            return "function"
        arrow = text.find("=>", arrow + 2, end)
    word, after = _word_at(text, v)
    if word == "async":
        word, after = _word_at(text, after)
    if word == "function":
        return "function"
    if word == "class":
        return "class"
    if word in ("new", "await", "typeof", "void"):
        return None
    if word:
        j = _skip_ws(text, after)
        while j < end and text[j] == "." :          # a member chain: utils.make(...)
            _, j2 = _word_at(text, j + 1)
            j = _skip_ws(text, j2)
        if j < end and text[j] == "(":
            return "function"
        if defs and word in defs:
            return defs[word][0]
    return None


def _parse_export(text: str, depths, defs, stmt: int, pos: int):
    """The units one `export` statement introduces, as (name, kind, position)."""
    word, end = _word_at(text, pos)
    if word == "default":
        word, end = _word_at(text, end)
    if word in _TYPE_ONLY:
        return []
    if word == "abstract":
        word, end = _word_at(text, end)
    if word == "async":
        word, end = _word_at(text, end)
    if word == "function":
        i = _skip_ws(text, end)
        if i < len(text) and text[i] == "*":
            i += 1
        name, _ = _word_at(text, i)
        return [(name, "function", stmt)] if name else []
    if word == "class":
        name, _ = _word_at(text, end)
        return [(name, "class", stmt)] if name else []
    if word in ("const", "let", "var"):
        name, after = _word_at(text, end)
        if not name:
            return []
        kind = _value_kind(text, depths, defs, after)
        return [(name, kind, stmt)] if kind else []
    if word is None:
        i = _skip_ws(text, pos)
        if i < len(text) and text[i] == "{":
            close = _match_bracket(text, depths, i)
            if close < 0:
                return []
            nxt, _ = _word_at(text, close + 1)
            if nxt == "from":
                return []            # a re-export: the unit's home is that file
            return _export_list(text, defs, i, close, stmt)
    return []


def _export_list(text: str, defs, open_i: int, close_i: int, stmt: int):
    """`export { parse, format as fmt }` -- the EXPORTED name is the unit's name.

    A name with no local declaration still counts, at the export statement's
    own line and as a function. It is either a value re-exported from an
    import (whose home file this stack also discovers, so the duplicate is
    visible rather than hidden) or a form this reader cannot classify; both
    are cheaper as a phantom than as a miss.
    """
    out = []
    for piece in text[open_i + 1:close_i].split(","):
        parts = piece.split()
        if len(parts) >= 3 and parts[1] == "as":
            local, exported = parts[0], parts[2]
        elif len(parts) == 1:
            local = exported = parts[0]
        else:
            continue                             # `type Foo`, or unparseable
        if not _IDENT_FULL.match(exported) or exported == "default":
            continue
        d = defs.get(local)
        out.append((exported, d[0] if d else "function", d[1] if d else stmt))
    return out


def _parse_cjs(text: str, depths, defs, stmt: int, pos: int):
    """`module.exports.name = ...`, `exports.name = ...`, `module.exports = {...}`."""
    n = len(text)
    i = _skip_ws(text, pos)
    if i < n and text[i] == ".":
        name, after = _word_at(text, i + 1)
        if not name:
            return []
        j = _skip_ws(text, after)
        if not (j < n and text[j] == "=" and text[j + 1:j + 2] not in ("=", ">")):
            return []
        kind = _value_kind(text, depths, defs, after)
        return [(name, kind, stmt)] if kind else []
    if i < n and text[i] == "=" and text[i + 1:i + 2] not in ("=", ">"):
        j = _skip_ws(text, i + 1)
        if j < n and text[j] == "{":
            close = _match_bracket(text, depths, j)
            if close >= 0:
                return _object_keys(text, depths, defs, j, close, stmt)
    return []


def _object_keys(text: str, depths, defs, open_i: int, close_i: int, stmt: int):
    """The top-level keys of `module.exports = { parse, format: fmt }`.

    A QUOTED key (`{ "parse": parse }`) is invisible here: the stripper blanked
    it along with every other string literal, and unblanking one class of
    string to read it back would hand every string in the file the power to
    declare an export. The form is rare; the miss is recorded rather than
    traded for that.
    """
    out = []
    inner = depths[open_i] + 1
    for m in _IDENT_AT.finditer(text, open_i + 1, close_i):
        if depths[m.start()] != inner:
            continue
        k = m.start() - 1
        while k >= 0 and text[k].isspace():
            k -= 1
        if k < 0 or text[k] not in "{,":
            continue
        j = _skip_ws(text, m.end())
        if j > close_i or (j < close_i and text[j] not in ",:}"):
            continue          # j == close_i: the last key before `}`
        name = m.group(0)
        if text[j] == ":":
            kind = _classify_value(text, depths, defs, j + 1,
                                   _key_value_end(text, depths, j + 1, close_i))
            kind = kind or "function"
        else:
            d = defs.get(name)
            kind = d[0] if d else "function"
        out.append((name, kind, defs[name][1] if name in defs else stmt))
    return out


def _key_value_end(text: str, depths, vpos: int, close_i: int) -> int:
    """Where an object property's value stops: the next `,` at its own depth."""
    d0 = depths[_skip_ws(text, vpos)]
    for j in range(vpos, close_i):
        if text[j] == "," and depths[j] == d0:
            return j
    return close_i
