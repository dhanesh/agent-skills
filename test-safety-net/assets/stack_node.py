#!/usr/bin/env python3
r"""The node/TypeScript stack: what a JS or TS repo means by each interface name.

Everything here is language-specific and everything language-specific is here;
the stack-agnostic half -- churn, reference counting, coverage detection,
scoring, ranking, the CLI, the JSON shape -- stays in `rank_risk.py`, which
documents the interface the two meet at.

HEURISTIC BY DESIGN, AND THE DIRECTION IT FAILS IN. Node exposes no public
parser API, so there is no `ast` to reach for the way `stack_python` does
(multistack design, D1). Discovery here is a comment/string stripper plus
bracket matching, and Task 4 adds a precise path that drives the repo's own
`typescript` when it has one, falling back to `_units_heuristic` when it does
not. That makes every predicate below an approximation, and the architecture
that makes an approximation acceptable is the one the original design already
ruled: static triage is a FILTER and the runtime guard is the ENFORCEMENT. So
an imprecise heuristic is acceptable in the direction of MORE reported work --
a phantom unit costs the user a glance -- and never in the direction of less,
where the cost is the bug the missing test would have caught. Every judgement
call below is recorded with the direction it fails in.

REGISTERED IN `rank_risk.STACKS`, with all fourteen interface names. It could
not be registered before `triage` existed -- `rank()` calls it for every unit
-- and it could not be registered safely while detection took the FIRST stack
that claimed a repo, because this module claims any tree holding a `.mjs`
helper. Both are settled: `evidence` scores instead of claiming, and the
detector takes the highest score. What a node repo got before that is worth
naming, because it is the failure this stack exists to close: it fell through
to `stack_python`, discovered zero units, and printed a clean report.
"""
from __future__ import annotations

import bisect
import collections
import functools
import json
import os
import re
import subprocess
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

STACK_NAME = "node"

# Files that declare "this directory is a node/TypeScript project".
MANIFESTS = ("package.json", "tsconfig.json", "jsconfig.json", "deno.json",
             "deno.jsonc")

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

def evidence(root: str) -> int:
    """How much of `root` is node: non-test JS/TS files, plus a manifest bonus.

    THE PREDICATE THIS REPLACED WAS A BOOLEAN, AND THAT WAS THE BUG. It
    answered "is there any node evidence here at all", which is True of this
    very repo -- 17 `.mjs`/`.ts` files against 51 non-test `.py` ones, and a
    single `package.json` belonging to a scaffold this repo SHIPS. Under first
    match wins that boolean reclassified the ranker's own corpus as node. The
    question a polyglot repo actually poses is "what is this repo MOSTLY", and
    only a score can answer it.

    A manifest SCALES that count rather than adding to it, and this stack is
    where the difference bites: a `package.json` carrying nothing but a `lint`
    script is, in a Python repo, the commonest reason one exists at all. The
    rule itself lives in `stack_common.evidence_score` -- shared, so the two
    stacks cannot drift into weighing the same evidence differently -- and it
    scores zero when no source files stand behind the manifest.
    """
    return evidence_score(sum(1 for _rel in iter_source_files(root)),
                          root, MANIFESTS, classify_manifest)


# Keys whose presence means "this package declares how it is ENTERED or what it
# needs at RUNTIME" -- a claim about what the directory IS. `type: "module"` is
# in the list because it changes how every `.js` file in the tree executes,
# which nobody writes for a formatter.
_PKG_DECLARING_KEYS = ("main", "module", "exports", "bin", "browser",
                       "workspaces", "dependencies", "peerDependencies")

# Scripts that RUN or BUILD the package, as opposed to editing it. `prepare` is
# deliberately absent: it is husky's install hook and nothing else in most
# repos that have one.
_PKG_DECLARING_SCRIPTS = ("build", "start", "dev", "serve")

# Commands that are tooling even when they are spelled `build`. A Python repo
# whose `package.json` fronts a formatter with `"build": "prettier --write ."`
# is still a Python repo; a repo that compiles itself is not. First token of
# the command only -- `tsc && node scripts/bundle.js` declares on `tsc`.
_TOOLING_COMMANDS = frozenset({
    "prettier", "eslint", "husky", "lint-staged", "stylelint", "commitlint",
    "editorconfig-checker", "echo", "true", ":", "exit", "npm-run-all",
})

# Manifests whose ONLY reason to exist is to say "there is source here".
# Unlike `package.json`, nobody keeps a `tsconfig.json` in a Python repo to
# format Markdown with.
_SELF_DECLARING_MANIFESTS = frozenset({"tsconfig.json", "jsconfig.json",
                                       "deno.json", "deno.jsonc"})


def _first_token(command: str) -> str:
    """The executable a script runs, past `npx`, `env` and `VAR=1` prefixes."""
    for token in str(command).split():
        if "=" in token.split("/")[0]:
            continue                      # FOO=1 prefix
        if token in ("npx", "env", "cross-env", "sudo"):
            continue
        if token.startswith("-"):
            continue
        return os.path.basename(token)
    return ""


def classify_manifest(name: str, text: str) -> str:
    """`declaring` when this manifest says the repo IS node; `tooling` otherwise.

    THE QUESTION IS NOT "IS THERE A `package.json`" -- `has_manifest` answers
    that, and in a Python repo it answers yes for `husky` and `prettier`. It is
    "does this file declare how the repo RUNS": an entry point (`main`,
    `exports`, `bin`), runtime dependencies, a module system for the whole
    tree, or a build/start script that runs something other than a formatter.

    Everything else -- `{}`, a bare name and version, devDependencies plus a
    `prepare` hook -- is `tooling`, and scales nothing. That direction is the
    conservative one for THIS stack: the failure it prevents (three JavaScript
    files outvoting eight Python ones) is a wrong repo ranked end to end, while
    the failure it risks (a node package that declares nothing at all losing to
    a larger scattering of another language) still leaves `--stack` and the
    stderr evidence line, and is a shape almost nothing real has.

    Unparseable JSON falls back to a scan of the same key names rather than to
    either verdict: a manifest with a trailing comma is not JSON and is still
    somebody's package.
    """
    if name in _SELF_DECLARING_MANIFESTS:
        return "declaring"
    if name != "package.json":
        return "tooling"          # not a name this stack knows: never declares
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if not isinstance(data, dict):
        # Text fallback: the key list, not a guess. `"main":` in the raw bytes
        # is a declaration even when the file around it will not parse.
        low = text.lower()
        if any('"%s"' % key in low for key in _PKG_DECLARING_KEYS):
            return "declaring"
        return "declaring" if '"type"' in low and '"module"' in low else "tooling"
    for key in _PKG_DECLARING_KEYS:
        value = data.get(key)
        if value:                 # an empty dict/string declares nothing
            return "declaring"
    if data.get("type") == "module":
        return "declaring"
    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        for script in _PKG_DECLARING_SCRIPTS:
            command = scripts.get(script)
            if command and _first_token(command) not in _TOOLING_COMMANDS:
                return "declaring"
    return "tooling"


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
    prev_idx = -2        # its index, so an identifier is only extended when contiguous
    prev_word = ""       # last identifier ENDING at `prev`, for regex detection
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
            if c.isalnum() or c in "_$":
                # The identifier ENDING here, accumulated as the word is walked.
                # It used to be `_IDENT_AT.match(text, i)` -- the identifier
                # STARTING here -- so by the last character of `return` the
                # word was "n", `_REGEX_PREV_WORDS` never matched anything, and
                # every `return /re/` in the repo was read as division. That
                # left the regex text visible, and a `{` inside one (a
                # quantifier, or `/\{[A-Za-z]/`) then opened a bracket that
                # never closed: bracket depth stayed above zero for the REST OF
                # THE FILE, so every export below it stopped being top level and
                # vanished. Silently fewer units, which is the one direction
                # this reader is not allowed to be wrong in.
                prev_word = (prev_word + c
                             if prev_word and prev_idx == i - 1 else c)
            else:
                prev_word = ""
            prev = c
            prev_idx = i
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


def discover_units(root: str, precise: bool = True):
    """Interface entry point: `(units, discovery_path)`.

    Two paths, and the label says which one ran (multistack design, D1),
    because a run that silently degraded is not comparable to one that did
    not. `"precise"` means the repo's OWN `typescript` parsed these files;
    `"heuristic"` means the stripper-and-brackets reader did.

    THE HEURISTIC IS THE ONE THAT ALWAYS RUNS. Precision is an upgrade, never
    a dependency: every way the toolchain path can fail ends here, in the
    reader that needs nothing installed. A precise path that could fail a run
    would be worse than no precise path at all -- it would make the skill's
    answer depend on whether somebody had run `npm ci` today.

    `precise=False` (`--no-precise` on the CLI) declines the toolchain path
    without trying it, and the label then honestly says `"heuristic"`. THE
    REASON IT EXISTS: the precise path `require`s
    `<analysed repo>/node_modules/typescript/lib/typescript.js` in a node
    process, which is the ANALYSED REPO'S OWN CODE, executing. Everything else
    this skill does to a target tree reads it. An agent handed a repo nobody
    has vetted is entitled to say no to that, and to be told it is happening --
    `references/parameters.md` and `references/stacks.md` say so where a user
    reaches them.
    """
    units = _units_precise(root) if precise else None
    if units is None:
        return _units_heuristic(root), "heuristic"
    return units, "precise"


# ── The precise path: the repo's own typescript, or nothing ──────────────
#
# NEVER `npx tsc`: on a miss that DOWNLOADS, which turns a read-only analysis
# into a network install of an unpinned compiler. This resolves a file that is
# already on disk and `require`s it, or declines. There is no third option and
# no package runner anywhere in this module -- a test asserts that by scanning
# the source for one.

# How long the toolchain gets. A hard bound, not a courtesy: `subprocess.run`
# kills the child when it expires, so the worst case is this many seconds
# followed by the heuristic, never a hung skill.
PRECISE_TIMEOUT = 20

# The walker, run with `node -e`. It reads {"files": [...]} on stdin and writes
# {"units": [...], "unreadable": n} on stdout, and it is deliberately ES5-flat:
# it has to run under whatever node the repo's toolchain came with.
#
# WHAT IT BUYS OVER THE HEURISTIC is exactly the list Task 2 recorded as
# deliberate misses -- destructured exports (`export const { a, b } = make()`),
# quoted object keys (`module.exports = { "parse": parse }`), a whole-module
# `module.exports = fn`, and TypeScript's `export =` -- plus it drops the
# heuristic's phantoms, because a constant in an exports object is a
# `PropertyAssignment` with a literal initialiser rather than a word that
# looked like a name. On a real tree it must therefore return MORE units than
# the heuristic; fewer means something is filtering rather than parsing.
#
# WHAT IT DELIBERATELY KEEPS FROM THE HEURISTIC, so the two stay comparable:
# the file set is `iter_source_files` (never a tsconfig `include`, which
# disagrees about `.d.ts`, `SKIP_DIRS` and test files), a call expression
# counts as a function (the factory form), `new X()` does not, and an
# anonymous `export default` yields nothing because a test cannot import a
# name that does not exist.
_PRECISE_JS = r""""use strict";
// Reads {"files": [<repo-relative path>, ...]} on stdin, writes
// {"units": [{path, name, kind, lineno}], "unreadable": n} on stdout.
var fs = require("fs");
var path = require("path");
var tsLib = process.argv[1];
var root = process.argv[2];
var ts = require(tsLib);
var SK = ts.SyntaxKind;
var input = JSON.parse(fs.readFileSync(0, "utf8"));
var units = [];
var unreadable = 0;

function scriptKind(rel) {
  var ext = path.extname(rel).toLowerCase();
  if (ext === ".tsx") return ts.ScriptKind.TSX;
  if (ext === ".ts" || ext === ".mts" || ext === ".cts") return ts.ScriptKind.TS;
  return ts.ScriptKind.JSX;   // .js/.jsx/.mjs/.cjs — JSX is the permissive superset
}

function modsOf(node) {
  var m = node.modifiers || [];
  var out = [];
  for (var i = 0; i < m.length; i++) out.push(m[i].kind);
  return out;
}

function each(file) {
  var src, text;
  try {
    text = fs.readFileSync(path.join(root, file), "utf8");
    src = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, scriptKind(file));
  } catch (e) { unreadable++; return; }

  function line(node) {
    try { return src.getLineAndCharacterOfPosition(node.getStart(src)).line + 1; }
    catch (e) { return 1; }
  }

  var locals = Object.create(null);
  var seen = Object.create(null);

  function unwrap(e) {
    while (e && (e.kind === SK.ParenthesizedExpression
                 || e.kind === SK.AsExpression
                 || e.kind === SK.SatisfiesExpression
                 || e.kind === SK.TypeAssertionExpression
                 || e.kind === SK.NonNullExpression)) e = e.expression;
    return e;
  }

  function valueKind(e) {
    e = unwrap(e);
    if (!e) return null;
    if (e.kind === SK.ArrowFunction || e.kind === SK.FunctionExpression) return "function";
    if (e.kind === SK.ClassExpression) return "class";
    if (e.kind === SK.CallExpression) return "function";
    if (e.kind === SK.Identifier) return locals[e.text] ? locals[e.text].kind : null;
    return null;
  }

  function declKind(d) {
    if (d.type && (d.type.kind === SK.FunctionType || d.type.kind === SK.ConstructorType)) return "function";
    return valueKind(d.initializer);
  }

  function produced(e) {
    e = unwrap(e);
    return !!e && (e.kind === SK.CallExpression || e.kind === SK.Identifier
                   || e.kind === SK.PropertyAccessExpression || e.kind === SK.AwaitExpression);
  }

  function bindingNames(name, out) {
    for (var i = 0; i < name.elements.length; i++) {
      var el = name.elements[i];
      if (!el.name) continue;                       // an array hole
      if (el.name.kind === SK.Identifier) out.push(el.name.text);
      else if (el.name.elements) bindingNames(el.name, out);
    }
    return out;
  }

  function emit(name, kind, lineno) {
    if (!name || seen[name]) return;
    seen[name] = 1;
    units.push({path: file, name: name, kind: kind, lineno: lineno});
  }

  function propName(p) {
    var n = p.name;
    if (!n) return null;
    if (n.kind === SK.Identifier || n.kind === SK.StringLiteral
        || n.kind === SK.NoSubstitutionTemplateLiteral || n.kind === SK.NumericLiteral) return n.text;
    return null;                                    // computed key: unaddressable
  }

  // Pass 1: what this file declares at the top level, for kind/line lookups.
  for (var i = 0; i < src.statements.length; i++) {
    var st = src.statements[i];
    if (st.kind === SK.FunctionDeclaration && st.name) locals[st.name.text] = {kind: "function", line: line(st)};
    else if (st.kind === SK.ClassDeclaration && st.name) locals[st.name.text] = {kind: "class", line: line(st)};
    else if (st.kind === SK.VariableStatement) {
      var ds = st.declarationList.declarations;
      for (var j = 0; j < ds.length; j++) {
        if (ds[j].name.kind !== SK.Identifier) continue;
        var k = declKind(ds[j]);
        if (k) locals[ds[j].name.text] = {kind: k, line: line(st)};
      }
    }
  }

  function objectMembers(obj) {
    for (var i = 0; i < obj.properties.length; i++) {
      var p = obj.properties[i];
      var nm = propName(p);
      if (p.kind === SK.MethodDeclaration) { if (nm) emit(nm, "function", line(p)); continue; }
      if (p.kind === SK.ShorthandPropertyAssignment) {
        var l = locals[p.name.text];
        if (l) emit(p.name.text, l.kind, l.line);
        continue;
      }
      if (p.kind === SK.PropertyAssignment && nm) {
        var kk = valueKind(p.initializer);
        if (!kk) continue;
        var e = unwrap(p.initializer);
        var loc = (e && e.kind === SK.Identifier && locals[e.text]) ? locals[e.text].line : line(p);
        emit(nm, kk, loc);
      }
    }
  }

  function memberPath(e) {
    if (e.kind === SK.Identifier) return e.text;
    if (e.kind === SK.PropertyAccessExpression) {
      var b = memberPath(e.expression);
      return b ? b + "." + e.name.text : null;
    }
    if (e.kind === SK.ElementAccessExpression && e.argumentExpression
        && e.argumentExpression.kind === SK.StringLiteral) {
      var b2 = memberPath(e.expression);
      return b2 ? b2 + "." + e.argumentExpression.text : null;
    }
    return null;
  }

  // Pass 2: the exports.
  for (var i2 = 0; i2 < src.statements.length; i2++) {
    var s = src.statements[i2];
    var kinds = modsOf(s);
    var isExport = kinds.indexOf(SK.ExportKeyword) >= 0;
    if (kinds.indexOf(SK.DeclareKeyword) >= 0) continue;   // no runtime body to pin

    if (s.kind === SK.FunctionDeclaration) {
      if (isExport && s.name && s.body) emit(s.name.text, "function", line(s));
    } else if (s.kind === SK.ClassDeclaration) {
      if (isExport && s.name) emit(s.name.text, "class", line(s));
    } else if (s.kind === SK.VariableStatement && isExport) {
      var dds = s.declarationList.declarations;
      for (var j2 = 0; j2 < dds.length; j2++) {
        var d = dds[j2];
        if (d.name.kind === SK.Identifier) {
          var k2 = declKind(d);
          if (k2) emit(d.name.text, k2, line(s));
        } else if (produced(d.initializer)) {
          var names = bindingNames(d.name, []);
          for (var n2 = 0; n2 < names.length; n2++) emit(names[n2], "function", line(s));
        }
      }
    } else if (s.kind === SK.ExportDeclaration) {
      if (s.moduleSpecifier || s.isTypeOnly || !s.exportClause || !s.exportClause.elements) continue;
      for (var e2 = 0; e2 < s.exportClause.elements.length; e2++) {
        var sp = s.exportClause.elements[e2];
        if (sp.isTypeOnly) continue;
        var localName = (sp.propertyName || sp.name).text;
        var l2 = locals[localName];
        emit(sp.name.text, l2 ? l2.kind : "function", l2 ? l2.line : line(s));
      }
    } else if (s.kind === SK.ExportAssignment) {
      var ex = unwrap(s.expression);
      if (!ex) continue;
      if (ex.kind === SK.Identifier) {
        var l3 = locals[ex.text];
        if (l3) emit(ex.text, l3.kind, l3.line);
      } else if ((ex.kind === SK.FunctionExpression || ex.kind === SK.ClassExpression) && ex.name) {
        emit(ex.name.text, ex.kind === SK.ClassExpression ? "class" : "function", line(s));
      }
    } else if (s.kind === SK.ExpressionStatement && s.expression.kind === SK.BinaryExpression
               && s.expression.operatorToken.kind === SK.EqualsToken) {
      var lhs = memberPath(s.expression.left);
      if (!lhs) continue;
      var rhs = unwrap(s.expression.right);
      if (lhs === "module.exports" || lhs === "exports") {
        if (rhs.kind === SK.ObjectLiteralExpression) objectMembers(rhs);
        else if (rhs.kind === SK.Identifier) {
          var l4 = locals[rhs.text];
          if (l4) emit(rhs.text, l4.kind, l4.line);
        } else if ((rhs.kind === SK.FunctionExpression || rhs.kind === SK.ClassExpression) && rhs.name) {
          emit(rhs.name.text, rhs.kind === SK.ClassExpression ? "class" : "function", line(s));
        }
      } else if (lhs.indexOf("module.exports.") === 0 || lhs.indexOf("exports.") === 0) {
        var nm2 = lhs.split(".").pop();
        var k4 = valueKind(rhs);
        if (k4) emit(nm2, k4, line(s));
      }
    }
  }
}

for (var f = 0; f < input.files.length; f++) each(input.files[f]);
process.stdout.write(JSON.stringify({units: units, unreadable: unreadable}));
"""


def _typescript_lib(root: str):
    """The repo's own `typescript`, or None.

    Only what is already installed, and only `lib/typescript.js` -- the entry
    point every 3.x-5.x release ships at that path. A pnpm store is reached
    through the symlink node itself would follow; a Yarn PnP tree resolves
    nothing here and gets the heuristic, which is the correct answer rather
    than a reason to start guessing at zip-backed resolution.
    """
    lib = os.path.join(root, "node_modules", "typescript", "lib", "typescript.js")
    return lib if os.path.isfile(lib) else None


def _units_from_payload(raw, files):
    """Unit dicts from the walker's JSON, or None if ANY row is malformed.

    ALL OR NOTHING on purpose. Dropping the rows it could not read would let
    the precise path report FEWER units than the heuristic while still calling
    itself precise -- a silent under-report wearing the label of the better
    path. Declining hands the run back to a reader that works.
    """
    if not isinstance(raw, list):
        return None
    wanted = frozenset(files)
    out = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        rel, name = item.get("path"), item.get("name")
        kind, lineno = item.get("kind"), item.get("lineno")
        if not isinstance(rel, str) or not isinstance(name, str) or not name:
            return None
        if kind not in ("function", "class"):
            return None
        if isinstance(lineno, bool) or not isinstance(lineno, int) or lineno < 1:
            return None
        if rel not in wanted:
            return None               # a file we did not ask about is not a file we walked
        out.append({"id": "%s::%s" % (rel, name), "path": rel, "name": name,
                    "lineno": lineno, "kind": kind})
    return sorted(out, key=lambda u: u["id"])


def _decline_reason(payload, files):
    """Why this precise run is not usable, or None if it is.

    The walker has always COUNTED the files it could not parse (`unreadable`,
    the `catch` in `_PRECISE_JS`) and always written the number on stdout. The
    consumer used to read `payload["units"]` and never read it -- so a broken
    toolchain produced an empty `units` list, which is not `None`, nothing
    declined, and the run reported `discovery: "precise"` with ZERO units while
    the heuristic would have found five. Zero units reads as "nothing here is
    worth testing": the silent zero, wearing the label the report tells an
    agent to trust more.

    `_units_from_payload` already refuses to half-trust a payload ROW by row.
    This is the same rule at the level of the RUN, which is where its own
    docstring always claimed it applied: the precise path may not report fewer
    units than the heuristic while still calling itself precise.

    Two conditions, both all-or-nothing:

      * ANY unreadable file. Nine of ten files parsed is a degraded run, and a
        degraded run relabelled `precise` is worse than an honest heuristic
        one.
      * NO units at all over a NON-EMPTY file set. A toolchain can report no
        failures and still return nothing -- an API shimmed to a no-op, a
        version whose `SyntaxKind` numbering this walker does not share. An
        empty tree is a different thing and is not a degraded run, so the file
        set is part of the test.
    """
    unreadable = payload.get("unreadable")
    if isinstance(unreadable, int) and not isinstance(unreadable, bool) and unreadable > 0:
        return "%d file%s of %d unreadable" % (
            unreadable, "" if unreadable == 1 else "s", len(files))
    units = payload.get("units")
    if files and isinstance(units, list) and not units:
        return "no units over %d source file%s" % (
            len(files), "" if len(files) == 1 else "s")
    return None


def _units_precise(root: str, ts_lib=None, timeout: int = PRECISE_TIMEOUT,
                   node_exe: str = "node"):
    """The units the repo's own typescript sees, or None to fall back.

    NONE IS NOT AN ERROR, it is the whole contract: no toolchain, no node, a
    non-zero exit, output this cannot parse, or a walk that outran `timeout`
    all return None, and `discover_units` runs the heuristic instead. Nothing
    in here may raise into a run.

    A toolchain that was FOUND and then failed is the surprising case, so it
    says so on stderr; simply not having one is ordinary and is reported by
    the `discovery` label alone.
    """
    ts_lib = ts_lib or _typescript_lib(root)
    if ts_lib is None:
        return None
    files = list(iter_source_files(root))
    if not files:
        return None                   # nothing to walk: do not pay for a process
    try:
        proc = subprocess.run(
            [node_exe, "-e", _PRECISE_JS, "--", ts_lib, os.path.abspath(root)],
            input=json.dumps({"files": files}), capture_output=True, text=True,
            timeout=timeout)
    except subprocess.TimeoutExpired:
        _precise_declined("timed out after %ss" % timeout)
        return None
    except (OSError, ValueError):
        # No `node` on PATH, or an environment that cannot spawn one.
        _precise_declined("could not run %s" % node_exe)
        return None
    if proc.returncode != 0:
        _precise_declined("exit %d: %s"
                          % (proc.returncode, proc.stderr.strip().splitlines()[-1]
                              if proc.stderr.strip() else "no diagnostic"))
        return None
    try:
        payload = json.loads(proc.stdout)
        if not isinstance(payload, dict):
            raise TypeError("payload is not an object")
        reason = _decline_reason(payload, files)
        if reason is not None:
            _precise_declined(reason)
            return None
        units = _units_from_payload(payload["units"], files)
    except (ValueError, TypeError, KeyError, IndexError):
        _precise_declined("unparseable output")
        return None
    if units is None:
        _precise_declined("malformed unit in the output")
        return None
    return units


def _precise_declined(why: str):
    """Say that a toolchain we FOUND did not answer. One line, stderr, never raises."""
    try:
        sys.stderr.write("note: node precise discovery declined (%s); "
                         "using the heuristic reader\n" % why)
    except Exception:                 # a closed or replaced stderr must not fail a run
        pass


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


def _found_exports(stripped: str, depths, defs):
    """(name, kind, position) for every export form in already-stripped text.

    Split out of `_units_in_text` so `triage` can ask WHERE a unit is without
    re-deriving it: the two must agree on a unit's position exactly, or triage
    would read a different span of the file than discovery named.
    """
    found = []
    for m in _EXPORT_KW_RE.finditer(stripped):
        if depths[m.start()] == 0:
            found.extend(_parse_export(stripped, depths, defs, m.start(), m.end()))
    for m in _CJS_HEAD_RE.finditer(stripped):
        if depths[m.start()] == 0:
            found.extend(_parse_cjs(stripped, depths, defs, m.start(), m.end()))
    return found


def _units_in_text(rel: str, text: str):
    """The units one file's text defines. Public for the tests and for Task 4."""
    stripped = strip_noncode(text)
    depths = _depths(stripped)
    defs = _local_defs(stripped, depths)
    found = _found_exports(stripped, depths, defs)

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



def _position_of_line(code: str, depths, lineno):
    """Offset of the first code character on `lineno`, or None.

    TOP LEVEL ONLY: a position at bracket depth > 0 is not a statement this
    reader can span, and guessing at one would hand `_unit_span` a region
    belonging to something else. None then means "not found", which triage
    already knows how to answer honestly.
    """
    if not isinstance(lineno, int) or isinstance(lineno, bool) or lineno < 1:
        return None
    starts = _line_starts(code)
    if lineno > len(starts):
        return None
    pos = _skip_ws(code, starts[lineno - 1])
    if pos >= len(code) or depths[pos] != 0:
        return None
    return pos

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


def _assign_op(text: str, depths, pos: int, end: int) -> int:
    """Index of the `=` that assigns the declaration starting at `pos`, or -1.

    `pos` is just past the declared name, so a TypeScript type annotation may
    still stand between here and the `=`. Anything that is a comparison or an
    arrow is skipped, which is what lets `export const f: () => void = ...` be
    read correctly.
    """
    d0 = depths[pos] if pos < len(text) else 0
    i = pos
    while i < end:
        c = text[i]
        if c == "=" and depths[i] == d0 and text[i + 1:i + 2] not in ("=", ">") \
                and (i == 0 or text[i - 1] not in "=!<>+-*/%&|^"):
            return i
        i += 1
    return -1


def _value_kind(text: str, depths, defs, pos: int):
    """The kind of the value assigned at/after `pos`, or None if not callable."""
    end = _stmt_end(text, depths, pos)
    eq = _assign_op(text, depths, pos, end)
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
        if j > close_i or (j < close_i and text[j] not in ",:}("):
            continue          # j == close_i: the last key before `}`
        name = m.group(0)
        if j < close_i and text[j] == "(":
            kind = "function"           # method shorthand: `{ parse(s) {...} }`
        elif text[j] == ":":
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


# ── I/O markers ──────────────────────────────────────────────────────────
#
# The vocabulary of "this unit touches the world", split the way the original
# design splits it: CONTROLLABLE groups are the ones a test can point somewhere
# safe (a temp dir, a frozen clock, a seeded random, a set environment variable)
# and UNCONTROLLABLE groups are the ones it cannot, which need a seam in the
# code before a characterization test is honest.
#
# TWO VOCABULARIES IN ONE TABLE, because JavaScript has two. A marker is either
# a MODULE SPECIFIER (`fs`, `child_process`, `axios`) or a CODE PATH
# (`Math.random`, `process.env`, `fetch`). Both resolve to the same dotted
# strings before matching: an import binds its locals to `<specifier>.<member>`
# (`import { spawn } from "child_process"` binds `spawn` ->
# `child_process.spawn`), and an unimported chain is matched as written. So
# `spawn(cmd)`, `cp.spawn(cmd)` after `import * as cp`, and
# `require("child_process").spawn(cmd)` are one marker hit, not three rules.
#
# `node:` IS NOT A SEPARATE SPELLING. `_canon` strips the prefix from both
# sides before matching, so `fs` covers `node:fs` and the table carries ONE
# entry per marker. Listing both spellings would have made the table look
# exhaustive where it was merely repetitive -- and the Python branch's two
# worst bugs were both a table that listed one member of a family and not its
# twin (`os.remove` marked, `os.rename` not; `os.execv` marked, `os.execlp`
# not). A test pins that both spellings tier identically.
#
# WHAT MAKES THE BUILTIN HALF EXHAUSTIVE rather than remembered: a derived test
# in `test_stack_node.py` walks node's own `module.builtinModules` and fails
# unless every module in it is either marked here or allow-listed there with a
# reason. The third-party half (`axios`, `pg`, `prisma`) can never be
# exhaustive and is not claimed to be -- it is a convenience over the runtime
# guard, which is the enforcement.
CONTROLLABLE = {
    # `path` is deliberately absent, and that is why node needs no equivalent
    # of `stack_python.IMPORT_TIME_INERT`: node's path algebra lives in a
    # module of its own that touches nothing, where Python's `os.path.join`
    # sits under the same `os.` prefix as `os.stat` and had to be exempted by
    # name at import time.
    "filesystem": ("fs", "fs/promises", "fsPromises", "fs-extra", "graceful-fs",
                   # A WASI instance is handed a preopened directory: its whole
                   # purpose is giving a guest module real filesystem access.
                   "wasi",
                   # Writes trace files to the working directory.
                   "trace_events",
                   # `v8` is otherwise pure (serialize, heap statistics); this
                   # one entry writes a file, so it is marked by name rather
                   # than flooring every unit that reads a heap statistic.
                   "v8.writeHeapSnapshot"),
    "clock": ("Date.now", "new Date", "setTimeout", "setInterval", "setImmediate",
              "performance.now", "perf_hooks", "process.hrtime", "process.uptime",
              "timers", "timers/promises"),
    # Hashing, HMAC and cipher construction in `crypto` are pure functions of
    # their input and stay unmarked; the entry points that draw entropy or
    # generate a key are the nondeterministic ones and are marked by name.
    "randomness": ("Math.random", "crypto.randomUUID", "crypto.randomBytes",
                   "crypto.randomInt", "crypto.randomFill", "crypto.randomFillSync",
                   "crypto.getRandomValues", "crypto.generateKey",
                   "crypto.generateKeySync", "crypto.generateKeyPair",
                   "crypto.generateKeyPairSync", "crypto.generatePrime",
                   "crypto.generatePrimeSync", "crypto.randomUUIDSync"),
    # `os` is marked whole rather than by member: every one of its exports
    # reports the state of the machine the test happens to run on (hostname,
    # tmpdir, cpus, network interfaces, EOL), which is the same thing
    # `process.env` is and is controlled the same way.
    "environment": ("process.env", "process.argv", "process.argv0",
                    "process.cwd", "process.chdir", "process.umask", "os"),
}
UNCONTROLLABLE = {
    # `inspector` opens the V8 debug protocol on a port; `dns` resolves against
    # whatever resolver the machine has. Both are as much network as `http` is,
    # and both were absent from the first draft of this table -- exactly the
    # enumeration hole the derived test exists to close.
    "network": ("http", "https", "http2", "net", "tls", "dgram",
                "dns", "dns/promises", "quic", "inspector", "inspector/promises",
                "fetch", "XMLHttpRequest", "WebSocket", "EventSource",
                "axios", "got", "undici", "node-fetch", "superagent", "request"),
    # `cluster` forks worker PROCESSES and `worker_threads` starts threads that
    # run a file of their own; neither goes through `child_process`, so a guard
    # patching that module would never see them -- the same shape as
    # `os.posix_spawn` bypassing `subprocess.Popen` on the Python side.
    "subprocess": ("child_process", "cluster", "worker_threads",
                   "spawn", "exec", "execSync", "spawnSync", "fork",
                   "execFile", "execFileSync"),
    # `sqlite` is node's own built-in database module (`node:sqlite`); the rest
    # are the drivers a repo actually has. That half of the list is best
    # effort, unlike the builtins, and says so.
    "database": ("sqlite", "pg", "mysql", "mysql2", "mongodb", "MongoClient",
                 "redis", "ioredis", "sqlite3", "better-sqlite3", "knex",
                 "prisma", "PrismaClient", "sequelize", "typeorm", "mongoose"),
}


def _canon(name: str) -> str:
    """`node:fs` and `fs` are the same module. Strip the prefix from both sides."""
    return name[5:] if name.startswith("node:") else name


def _marker_hit(marker: str, name: str) -> bool:
    """True when the resolved `name` is at or under `marker`.

    Three forms, because a JS name is a module path as well as a call chain:
    equal (`fetch`), a member of it (`fs.readFileSync` under `fs`), or a
    submodule of it (`fs/promises` under `fs`).
    """
    m, n = _canon(marker), _canon(name)
    return n == m or n.startswith(m + ".") or n.startswith(m + "/")


def _markers(names):
    """(group, marker, controllable) for every marker any resolved name hits.

    Deterministic order, mirroring `stack_python._markers`: UNCONTROLLABLE
    groups before CONTROLLABLE ones, each in sorted-group / table order -- so
    the reason a unit is given is the same on every run.
    """
    hits = []
    for group in sorted(UNCONTROLLABLE):
        for m in UNCONTROLLABLE[group]:
            if any(_marker_hit(m, n) for n in names):
                hits.append((group, m, False))
    for group in sorted(CONTROLLABLE):
        for m in CONTROLLABLE[group]:
            if any(_marker_hit(m, n) for n in names):
                hits.append((group, m, True))
    return hits


# ── Resolving names to markers ───────────────────────────────────────────

def _bind_clause(alias: dict, clause: str, spec: str):
    """Add what one import/require clause binds to `alias`: local -> canonical."""
    clause = clause.strip()
    if not clause:
        return                                    # side-effect import
    brace = clause.find("{")
    head = clause[:brace] if brace >= 0 else clause
    body = ""
    if brace >= 0:
        end = clause.find("}", brace)
        body = clause[brace + 1:end if end >= 0 else len(clause)]
    for piece in head.split(","):
        parts = piece.strip().rstrip(",").split()
        if len(parts) >= 3 and parts[0] == "*" and parts[1] == "as":
            alias.setdefault(parts[2], spec)      # import * as fs
        elif len(parts) == 1 and _IDENT_FULL.match(parts[0]):
            alias.setdefault(parts[0], spec)      # default import
    for piece in body.split(","):
        parts = piece.strip().split()
        if not parts or parts[0] == "type":
            continue                              # `import { type Foo }`: erased
        if len(parts) >= 3 and parts[1] == "as":
            member, local = parts[0], parts[2]
        elif len(parts) == 1:
            member = local = parts[0]
        else:
            continue
        local = local.strip(":").strip()
        if _IDENT_FULL.match(local) and _IDENT_FULL.match(member):
            alias.setdefault(local, spec + "." + member)


def _alias_map(ks: str, lo: int, hi: int) -> dict:
    """Local name -> canonical `<specifier>[.<member>]`, for imports in [lo, hi).

    Read off the COMMENT-STRIPPED, STRING-KEPT text, because a module specifier
    IS a string literal -- the same reason `module_bindings` reads that text.
    A commented-out import binds nothing.

    Scoped by range on purpose, mirroring `stack_python._local_alias_map`: a
    `const fs = require("fs")` inside one function must not rewrite what `fs`
    means for the rest of the file.
    """
    alias = {}
    for rx in (_IMPORT_RE, _REQUIRE_RE):
        for m in rx.finditer(ks, lo, hi):
            _bind_clause(alias, m.group("clause"), m.group("spec"))
    return alias


_SPEC_IN_CALL_RE = re.compile(r"""['"]([^'"]+)['"]""")


def _chain_at(code: str, ks: str, i: int, hi: int, alias: dict):
    """(resolved dotted name, index just past it, is-a-call) for the chain at `i`.

    The head is resolved through `alias`, so `fs.readFileSync` after
    `import fs from "node:fs"` comes back as `node:fs.readFileSync`. A
    `require("...")` / `import("...")` head resolves to the specifier itself,
    which is what catches the inline `require("child_process").execSync(cmd)`
    form that binds no local name at all -- the specifier is read out of `ks`
    at the same offsets, which is the point of the stripper preserving them.
    """
    head = _IDENT_AT.match(code, i)
    if not head:
        return "", i, False
    word, j = head.group(0), head.end()
    root = alias.get(word, word)
    k = _skip_ws(code, j)
    if word in ("require", "import") and k < len(code) and code[k] == "(":
        close = code.find(")", k)
        if close > 0:
            spec = _SPEC_IN_CALL_RE.search(ks, k + 1, close)
            if spec:
                root, j = spec.group(1), close + 1
    parts = [root]
    while True:
        k = _skip_ws(code, j)
        if k < len(code) and code[k] == "?":
            k = _skip_ws(code, k + 1)
        if k >= len(code) or code[k] != ".":
            break
        nxt = _IDENT_AT.match(code, _skip_ws(code, k + 1))
        if not nxt:
            break
        parts.append(nxt.group(0))
        j = nxt.end()
    tail = _skip_ws(code, j)
    return ".".join(parts), j, tail < len(code) and code[tail] == "("


def _scan(code: str, ks: str, lo: int, hi: int, alias: dict):
    """(names, calls) for the region [lo, hi) of already-stripped `code`.

    `names` are resolved dotted chains to match against the marker tables;
    `calls` are the bare identifiers called there, which is how a unit reaches
    a same-file helper.

    EVERY OCCURRENCE COUNTS, not only a call. `stack_python` resolves
    `ast.Call` targets alone, but `process.env.HOME` and `fsPromises.constants`
    are real touches of the world with no call node in sight, and a reader with
    no parse tree cannot tell a method reference from an invocation anyway. It
    over-reports (naming `fs` without using it tiers the unit 2), which is the
    direction this whole stack is allowed to be wrong in.
    """
    names, calls = [], set()
    i = lo
    while i < hi:
        m = IDENTIFIER_RE.search(code, i, hi)
        if not m:
            break
        start, word = m.start(), m.group(0)
        i = m.end()
        if preceding_qualifier(code, start) is not None:
            continue                    # a property; its chain head owns it
        if word == "new":
            # `new Date()` / `new MongoClient(url)`: the constructor is scanned
            # as its own head too, so this only has to add the `new X` spelling
            # the clock group needs to tell `new Date` from a bare `Date`.
            nxt = _IDENT_AT.match(code, _skip_ws(code, m.end()))
            if nxt:
                sub, _end, _called = _chain_at(code, ks, nxt.start(), hi, alias)
                if sub:
                    names.append("new " + sub)
            continue
        chain, _end, called = _chain_at(code, ks, start, hi, alias)
        if not chain:
            continue
        names.append(chain)
        if called and "." not in chain and "/" not in chain:
            calls.add(word)
    return names, calls


# ── Regions: what a unit is, and what runs at import time ────────────────

def _unit_span(code: str, depths, start: int):
    """(start, end) of the code a unit occupies, from its statement's start.

    A function or class body is its brace-matched block; a declaration with no
    block (`export const f = (x) => x * 2;`) is its statement.
    """
    n = len(code)
    d0 = depths[start]
    i = start
    while i < n:
        c = code[i]
        if depths[i] < d0:
            return start, i
        if c == "{" and depths[i] == d0:
            close = _match_bracket(code, depths, i)
            return start, (close + 1 if close >= 0 else n)
        if c == ";" and depths[i] == d0:
            return start, i
        if c == "\n" and depths[i] == d0:
            j = _skip_ws(code, i)
            word = _IDENT_AT.match(code, j)
            if word and word.group(0) in _STMT_KEYWORDS:
                return start, i
        i += 1
    return start, n


def _is_callable_literal(code: str, depths, pos: int) -> bool:
    """True when the value assigned at/after `pos` is a function or class BODY.

    Narrower than `_value_kind`, and the difference matters exactly once: a
    CALL expression (`export const cfg = loadConfig()`) is discovered as a unit,
    because a factory-produced function is still a function -- but the call
    itself runs when the module is imported, so its span must stay part of the
    import-time region. Treating it as a body was the node equivalent of
    reporting `_CFG = json.load(open(...))` as "directly callable".
    """
    end = _stmt_end(code, depths, pos)
    eq = _assign_op(code, depths, pos, end)
    if eq < 0:
        return False
    v = _skip_ws(code, eq + 1)
    if v >= end:
        return False
    d0 = depths[v]
    arrow = code.find("=>", v, end)
    while arrow >= 0:
        if depths[arrow] == d0:
            return True
        arrow = code.find("=>", arrow + 2, end)
    word, after = _word_at(code, v)
    if word == "async":
        word, after = _word_at(code, after)
    return word in ("function", "class")


def _callable_bodies(code: str, depths) -> dict:
    """name -> (start, end) for every top-level function/class body in the file.

    What `triage` follows a same-file call into, and what the import-time scan
    cuts out: a function's body does not run because the module was imported.
    A CLASS body is cut out whole, unlike `stack_python`'s, because a JS class
    field initialiser runs at construction time, not at class-definition time
    -- the language differs here and the region rule follows it.
    """
    bodies = {}
    for m in _DECL_RE.finditer(code):
        if depths[m.start()] == 0:
            bodies.setdefault(m.group("name"), _unit_span(code, depths, m.start()))
    for m in _VARDECL_RE.finditer(code):
        if depths[m.start()] == 0 and _is_callable_literal(code, depths, m.end()):
            bodies.setdefault(m.group("name"), _unit_span(code, depths, m.start()))
    return bodies


def _module_region(code: str, ks: str, depths, bodies) -> str:
    """`code` with everything that does NOT run at import time blanked out.

    Blanked: every top-level function/class body, and the import and require
    STATEMENTS themselves. The second is the node counterpart of
    `stack_python._module_level_regions` skipping `ast.Import` -- `import fs
    from "node:fs"` performs no filesystem I/O, and reading the specifier as a
    marker hit would floor every unit in every file that imports anything.

    What survives is what actually executes on import: top-level calls,
    initialisers, and IIFEs.
    """
    out = list(code)

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    for lo, hi in bodies.values():
        blank(lo, hi)
    for rx in (_IMPORT_RE, _REQUIRE_RE):
        for m in rx.finditer(ks):
            if depths[m.start()] == 0:
                blank(m.start(), m.end())
    return "".join(out)


# ── Triage ───────────────────────────────────────────────────────────────

_FileAnalysis = collections.namedtuple(
    "_FileAnalysis", "code ks depths bodies alias positions tier4 import_floor")


@functools.lru_cache(maxsize=None)
def _analyze_file(root: str, rel: str):
    """Strip and index `rel` once, for every unit in it.

    Cached per (root, rel) like the Python stack's: several units share a file,
    and stripping plus re-indexing per UNIT rather than per file was most of
    the cost.
    """
    text = read_text(root, rel)
    code = strip_noncode(text)
    ks = strip_noncode(text, keep_strings=True)
    depths = _depths(code)
    defs = _local_defs(code, depths)
    bodies = _callable_bodies(code, depths)
    alias = _alias_map(ks, 0, len(ks))
    positions = {}
    for name, _kind, pos in _found_exports(code, depths, defs):
        positions.setdefault(name, pos)

    region = _module_region(code, ks, depths, bodies)
    names, calls = _scan(region, ks, 0, len(region), alias)
    mod_hits = _hits_from(names, calls, code, ks, bodies, alias, set())

    tier4 = None
    uncontrollable_mod = [h for h in mod_hits if not h[2]]
    if uncontrollable_mod:
        group, marker, _c, via = uncontrollable_mod[0]
        via_txt = f" via {via}" if via else ""
        tier4 = (4, f"module does {group} I/O at import time{via_txt} "
                    f"({marker}); not reachable")
    # Import-time CONTROLLABLE I/O floors every unit in the file at Tier 3, for
    # the reason the Python stack records: the harness takes the error on
    # `import mod` before any test body runs, and the Tier 2 controls live in
    # fixtures, which run strictly after the module under test is imported. A
    # boundary already crossed at import cannot be controlled from one.
    import_floor = None
    controllable_mod = [h for h in mod_hits if h[2]]
    if controllable_mod:
        group, marker, _c, via = controllable_mod[0]
        via_txt = f" via {via}" if via else ""
        import_floor = (3, f"module does {group} I/O at import time{via_txt} "
                           f"({marker}); a fixture runs too late to control it — "
                           f"needs a seam")
    return _FileAnalysis(code, ks, depths, bodies, alias, positions,
                          tier4, import_floor)


def _hits_from(names, calls, code, ks, bodies, alias, visited):
    """Marker hits for one region, plus (recursively) the same-file helpers it calls.

    A thin wrapper inherits the worst tier reachable from it, exactly as
    `stack_python._hits_from` does: `export function save(x) { return _write(x); }`
    is not Tier 1 just because the `fs` call is one hop away. Each hit is
    `(group, marker, controllable, via)`, where `via` is the same-file callee it
    was reached through, or None when the marker sits in the scanned region.

    ONLY BARE CALLS ARE FOLLOWED (`_write(x)`, `new Ledger()`). A method call on
    a value this reader cannot resolve is the points-to boundary the filter
    stops at on purpose -- and the runtime guard, not this, is the enforcement.
    """
    hits = [(g, m, c, None) for g, m, c in _markers(names)]
    for name in sorted(calls):
        if name not in bodies or name in visited:
            continue
        visited.add(name)
        lo, hi = bodies[name]
        local = dict(alias)
        local.update(_alias_map(ks, lo, hi))
        sub_names, sub_calls = _scan(code, ks, lo, hi, local)
        for g, m, c, _via in _hits_from(sub_names, sub_calls, code, ks, bodies,
                                         local, visited):
            hits.append((g, m, c, name))
    return hits


def _tier_from_hits(hits) -> tuple:
    """(tier, reason) from a unit's own marker hits, before any module-level floor.

    The tier rules are the Python stack's, word for word, because they are a
    property of the design and not of the language: 1 nothing, 2 controllable
    only, 3 uncontrollable inside the unit, 4 uncontrollable at the boundary
    (which for both stacks means at import time, where no fixture can reach).
    """
    if not hits:
        return 1, "no I/O markers; directly callable"
    uncontrollable = [h for h in hits if not h[2]]
    if uncontrollable:
        group, marker, _c, via = uncontrollable[0]
        if via:
            return 3, f"{group} I/O via {via} ({marker}); needs a seam"
        return 3, f"{group} I/O inside the unit ({marker}); needs a seam"
    group, marker, _c, via = hits[0]
    if via:
        return 2, (f"{group} I/O via {via} ({marker}); "
                    f"pin at a wider boundary with {group} controlled")
    return 2, f"{group} I/O ({marker}); pin at a wider boundary with {group} controlled"


def triage(root: str, unit) -> tuple:
    """Classify how testable a unit is. Returns (tier, reason).

    A FILTER, NEVER THE ENFORCEMENT -- and for this stack that sentence carries
    more weight than it does for Python, because there is no parse tree here.
    The reader below resolves imports, follows same-file calls, and reads
    marker chains out of comment- and string-stripped text; it cannot see
    through a method call on an unresolved receiver, a re-exported binding, or
    a value returned by a factory. The invariant "never writes a test that
    performs real I/O" is enforced at RUNTIME by the node guard, loaded with
    `--require` before the module under test is ever imported.

    Where it is wrong, it is wrong toward a HIGHER tier: every occurrence of a
    marker counts, not only a call, and a `new Ledger()` inherits from the
    whole class body rather than the constructor alone. A tier that is too
    conservative costs a unit its place in the ranking; a tier that is too
    generous is a test that does real I/O, which is the failure this skill
    exists to prevent.

    The one thing this does NOT mirror from the Python stack is a
    file-does-not-parse verdict. There is no parser to fail: a file this reader
    cannot make sense of yields no units at all rather than units it then
    declines, so the "unit not found" branch below is the only way that shows
    up.
    """
    analysis = _analyze_file(root, unit["path"])
    if analysis.tier4 is not None:
        return analysis.tier4
    pos = analysis.positions.get(unit["name"])
    if pos is None:
        # A unit the PRECISE path found and this reader cannot name -- a
        # destructured export, a quoted key, a `module.exports = fn`. Its
        # statement is still right there, so place it by the line discovery
        # recorded and read the markers in it. Without this every precise-only
        # unit lands at Tier 4 and is reported as unnettable, which would make
        # the better discovery path produce the worse plan.
        pos = _position_of_line(analysis.code, analysis.depths, unit.get("lineno"))
        if pos in analysis.positions.values():
            # That line already belongs to an export this reader CAN name, so
            # the unit is not there -- it is a stale id from a file that
            # changed between discovery and triage. Declining is the honest
            # answer; triaging it against somebody else's statement is not.
            pos = None
    if pos is None:
        return 4, "unit not found on re-read"

    lo, hi = _unit_span(analysis.code, analysis.depths, pos)
    local = dict(analysis.alias)
    local.update(_alias_map(analysis.ks, lo, hi))
    names, calls = _scan(analysis.code, analysis.ks, lo, hi, local)
    hits = _hits_from(names, calls, analysis.code, analysis.ks, analysis.bodies,
                       local, {unit["name"]})

    tier, reason = _tier_from_hits(hits)
    # A FLOOR, applied last and only upward: a unit already at or above it
    # keeps its own reason, which names something more specific.
    if analysis.import_floor is not None and tier < analysis.import_floor[0]:
        return analysis.import_floor
    return tier, reason
