#!/usr/bin/env python3
"""Rank a repo's untested units by risk, and triage how testable each one is.

Stdlib + git only. No network, no third-party packages, no call-graph service:
the blast-radius half is a deliberate static approximation, labelled as such in
the output, so this runs anywhere the repo does.

STACK-AGNOSTIC CORE. Churn, reference counting, coverage detection, scoring,
ranking, the CLI and the JSON shape live here and are shared by every stack.
Everything a language decides for itself -- which files are source, which are
tests, what a unit is, and what tier a unit lands in -- lives behind the stack
interface and is implemented once per stack (`stack_python.py`, and one module
per stack added beside it).

THE STACK INTERFACE. A stack module supplies exactly six names:

    STACK_NAME                                   str; the report's "stack" key
    matches(root) -> bool                        is this repo of this stack?
    iter_source_files(root, include_tests=False) sorted repo-relative paths
    is_test_path(rel) -> bool                    is this path a test file?
    discover_units(root) -> (units, path)        the units, plus WHICH
                                                 discovery path ran
                                                 ("precise" | "heuristic")
    triage(root, unit) -> (tier, reason)         the FILTER -- never the
                                                 enforcement; the runtime
                                                 guard enforces the no-I/O
                                                 invariant

The walk and the test-file predicate are part of that interface, not local
Python details, because `inbound_refs` and `already_covered` both iterate the
source tree and both must recognise a test file. Hard-coding `test_*.py` here
would make a node repo's `*.test.ts` invisible to coverage detection, which
credits a tested unit with no coverage and ranks it as a gap -- guessing in the
one place this file must not guess.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys

# The shipped assets are a flat directory, not a package, and this module is
# loaded by path (`importlib.util.spec_from_file_location`) by both test suites
# and by `eval/run_eval.py`, whose `sys.path[0]` is NOT this directory. Make
# the siblings importable by name before importing them.
_ASSETS = os.path.dirname(os.path.abspath(__file__))
if _ASSETS not in sys.path:
    sys.path.insert(0, _ASSETS)

import stack_python                                                 # noqa: E402
from stack_common import SKIP_DIRS, read_text                       # noqa: E402,F401

# ── Stack registry ───────────────────────────────────────────────────────
# FIRST MATCH WINS, so a more specific stack must sit ahead of a less specific
# one. Python is both last and the fallback: a directory this ranker cannot
# place still gets a well-formed (empty) report rather than an error.
STACKS = [stack_python]


def detect_stack(root: str):
    """The first registered stack that claims `root`; Python if none does."""
    for stack in STACKS:
        if stack.matches(root):
            return stack
    return stack_python


# ── Compatibility surface ────────────────────────────────────────────────
# These names moved to `stack_python` when the stack interface was extracted.
# They stay reachable at their old address because `assets/test_io_guard.py`
# and `eval/run_eval.py` assert that the guard's intercept tables partition the
# FILTER's marker tables via `rank_risk.CONTROLLABLE` / `.UNCONTROLLABLE`, and
# `assets/test_rank_risk.py` drives Python discovery and triage through this
# module. The extraction changed no behaviour, so nothing reading them changed
# either. New code should reach them through a stack object instead.
CONTROLLABLE = stack_python.CONTROLLABLE
UNCONTROLLABLE = stack_python.UNCONTROLLABLE
triage = stack_python.triage


def discover_units(root: str, stack=None):
    """The discovered units alone, dropping the discovery-path label.

    The stack interface returns `(units, discovery_path)`; this preserves the
    single-value contract callers had before the label existed.
    """
    return (stack or detect_stack(root)).discover_units(root)[0]

def _git_prefix(root: str) -> str:
    """`root`'s path INSIDE its git repo, slash-terminated ("pkg/sub/"), or "".

    "" means either "root IS the repo root" or "not a git repo at all" — the
    two cases behave identically everywhere this is used, so they need no
    distinction. `main()` prints it as a scope note when it is non-empty.
    """
    try:
        r = subprocess.run(["git", "rev-parse", "--show-prefix"],
                           cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def churn(root: str, since: str = "6 months ago") -> dict:
    """Commits touching each path within the window, keyed RELATIVE TO `root`.

    The best available proxy for "what a person keeps changing" — which is
    what an agent will touch next — but not exact. It uses `-z` so a path
    containing a quote or non-ASCII byte comes back raw instead of git's
    default C-quoted escaping (which would otherwise fail to match the plain
    path `stack_python.iter_source_files` produces, silently reading that file's churn as 0).
    It also inherits git's default rename detection: a renamed file's history
    is attributed to its new path only, so pre-rename commits are not counted
    there. Absent git or history, returns {} rather than raising: churn is one
    signal of two, and a tarball checkout must still get a ranking.

    KEY SPACE (fix round 4). `git log --name-only` names files relative to the
    GIT REPO ROOT, while `discover_units` names them relative to the ANALYSED
    ROOT. The two agree only when you analyse the repo root — point the ranker
    at `<repo>/pkg/sub` and every join missed, so a file with five commits
    reported `churn: 0  score: 0.0`. Churn is the primary risk signal, so the
    whole ranking silently collapsed to zero with no warning. The repo-relative
    prefix is therefore stripped here, and paths outside the analysed subtree
    are dropped rather than folded in under a wrong key.
    """
    try:
        r = subprocess.run(
            ["git", "log", "--format=", "--name-only", "-z", f"--since={since}"],
            cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    prefix = _git_prefix(root)
    counts: dict = {}
    for part in r.stdout.split("\0"):
        if not part:
            continue
        if prefix:
            if not part.startswith(prefix):
                continue              # outside the analysed subtree
            part = part[len(prefix):]
        counts[part] = counts.get(part, 0) + 1
    return counts

_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*")
_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _preceding_qualifier(text: str, start: int):
    """The receiver an identifier at `start` is an attribute OF, or None.

    None  — the identifier stands on its own (`write(...)`, `import write`).
    "sink" — it was written `sink.write`, receiver a plain name.
    ""    — it is an attribute of something unnameable (`mk().write`,
            `d["k"].write`), which can never be shown to be the unit's module.

    Whitespace either side of the dot is skipped, so a wrapped chain
    (`foo()\n    .write(x)`) reads the same as the unwrapped form.
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


def _name_occurrences(text: str):
    """(bare, attr) occurrence counters for one file's text.

    `bare[name]` counts occurrences that stand on their own; `attr[(recv,
    name)]` counts occurrences written `recv.name`. Splitting them is what
    lets `inbound_refs` count `sink.write` for module `sink` while refusing
    `buf.write` — see its docstring for why that distinction is load-bearing.
    """
    bare = collections.Counter()
    attr = collections.Counter()
    for m in _IDENTIFIER_RE.finditer(text):
        qual = _preceding_qualifier(text, m.start())
        if qual is None:
            bare[m.group(0)] += 1
        else:
            attr[(qual, m.group(0))] += 1
    return bare, attr


def _references_module(module: str, text: str, path_tokens) -> bool:
    """True if `text`, or `path_tokens` (from the file's own path), plausibly names `module`.

    `module` is a defining file's basename without `.py`. Shared by
    `inbound_refs` and `already_covered` so the "does this OTHER file talk
    about that module" predicate cannot drift between the two call sites —
    they need the same answer to the same question for opposite reasons: one
    uses it to avoid crediting a unit with reach it does not have, the other
    to avoid crediting it with coverage it does not have.

    AMBIGUITY IS NOT THIS PREDICATE'S JOB (fix round 4). A bare basename is a
    weak identifier: `app/utils.py` and `lib/utils.py` are both "utils", and
    text naming either satisfies this predicate for both. Each caller settles
    that its own way, because the two need opposite treatments of the same
    ambiguity — and both were fixed against the same reproduction class:
      * `inbound_refs` drops every same-basename file from a module's
        reference-file list outright (RULING fix round 3, below).
      * `already_covered` requires PATH-qualified evidence (`_path_pattern`)
        when a basename is shared by more than one discovered file, and only
        falls back to this predicate when the basename is unique.
    """
    if re.search(r"\b%s\b" % re.escape(module), text):
        return True
    return module in path_tokens


def _path_pattern(rel: str):
    """A regex matching `rel` written as a module path — `app.utils` or
    `app/utils` for `app/utils.py` — or None when `rel` has no parent
    directory to qualify it with.

    This is the evidence `already_covered` demands when a basename is shared,
    and it is deliberately BOUNDED at both ends rather than a substring test:
    `myapp.utils` contains "app.utils", so a substring match would credit
    `app/utils.py` with a test that exercises `myapp/utils.py` — the
    OVER-crediting direction this file must never take. The trailing lookahead
    still permits a following `.`, so `app.utils.helper` and
    `from app.utils import helper` both match.

    Returns None for a repo-root file (`utils.py`), which has no qualifier to
    offer: under ambiguity such a unit reads as uncovered and gets ranked. That
    is the accepted under-credit — a redundant test, never a hidden gap.
    """
    stem = rel[:-3] if rel.endswith(".py") else rel
    parts = stem.split("/")
    if len(parts) < 2:
        return None
    body = r"[./]".join(re.escape(p) for p in parts)
    return re.compile(r"(?<![A-Za-z0-9_.])%s(?![A-Za-z0-9_])" % body)


def _module_bindings(module: str, text: str) -> tuple:
    """(aliases, names) the file binds for `module` by an actual import.

    `aliases` are the names the module object itself is bound to
    (`import harvest` -> "harvest"; `import harvest as H` -> "H"); `names` are
    the members pulled out of it (`from harvest import apply_markers`).

    This is `already_covered`'s SAME-DIRECTORY evidence, and it is deliberately
    stronger than the bare whole-identifier match used elsewhere in this file.
    A test file beside `harvest.py` that says `import harvest` and also
    contains the token `main` — because it ends with `unittest.main()`, or
    calls a DIFFERENT module's `main` through an alias — must not credit
    `harvest.py::main`, which has no test at all. Measured on this repo: the
    weaker form credited 6 units, 2 of them (`::main` twice) false; this form
    credits exactly the 4 genuine ones.

    What that buys is NARROWER evidence, not exact evidence, and the
    difference matters in the over-credit direction. Two things it does rule
    out, both measured: a bare token that the module never supplies, and — via
    `_reached_through_module`'s call-site requirement — a `mock.patch(
    "harvest.collect")` string or a `# harvest.collect` comment, each of which
    named the binding while proving nothing (the patch string proves the
    opposite: the unit is stubbed out). What it does NOT rule out is a
    call-SHAPED mention in a comment or a docstring: like every other
    predicate here, this one reads text, not a call graph, and the file says
    so under Reference counting. That residue is parity with the rest of the
    file, not a new class of error.
    """
    aliases, names = set(), set()
    esc = re.escape(module)
    for m in re.finditer(r"^[ \t]*import[ \t]+%s(?:[ \t]+as[ \t]+(\w+))?[ \t]*(?:#.*)?$"
                         % esc, text, re.M):
        aliases.add(m.group(1) or module)
    for m in re.finditer(r"^[ \t]*from[ \t]+\.?%s[ \t]+import[ \t]+(\(?[^()]*\)?)"
                         % esc, text, re.M):
        for piece in m.group(1).replace("(", " ").replace(")", " ").split(","):
            piece = piece.strip()
            if not piece:
                continue
            parts = piece.split()
            names.add(parts[-1] if len(parts) > 2 and parts[-2] == "as"
                      else parts[0])
    return tuple(sorted(aliases)), tuple(sorted(names))


def _reached_through_module(module: str, name: str, text: str) -> bool:
    """True when `text` CALLS `name` through an import of `module`.

    The call site is the point (fix round 7). Matching the chain `module.name`
    alone credited `mock.patch("harvest.collect")` — evidence that the unit was
    replaced by a stub, i.e. the opposite of coverage — and a `# harvest.collect`
    comment, so under a basename collision a unit with no test at all could
    read as covered. Requiring `(` after the name costs nothing measured (126
    credited units on this repo, byte-identical evidence files, before and
    after) and closes both.
    """
    aliases, names = _module_bindings(module, text)
    called = re.compile(r"(?<![A-Za-z0-9_.])%s[ \t]*\(" % re.escape(name))
    if name in names and called.search(text):
        return True
    return any(re.search(r"(?<![A-Za-z0-9_.])%s[ \t]*\.[ \t]*%s[ \t]*\("
                         % (re.escape(alias), re.escape(name)), text)
               for alias in aliases)


def inbound_refs(root: str, units, stack=None) -> dict:
    """Approximate blast radius: intra-module use, plus cross-file use from files naming the module.

    APPROXIMATE, on purpose, and labelled as such wherever it surfaces. It counts
    identifier occurrences, so a mention in a comment or a docstring counts and a
    dynamic `getattr(mod, name)` call does not. A real call graph would do this
    better — the report says so and names it as an optional upgrade — but
    requiring one would make the skill undeployable in the repos that need it most.

    RULING (fix round 2): counts an occurrence in a file when either (a) it
    is the unit's OWN file — every occurrence except those on the definition
    line itself (by exact line number, not a flat -1, so a decorated def or a
    same-line annotation is handled correctly); or (b) it is ANOTHER file
    that plausibly references the unit's MODULE (the defining file's
    basename, without `.py`) — via `_references_module`, the same predicate
    `already_covered` uses, so the two rules cannot drift apart.

    This replaces the earlier rule of crediting a unit with a bare name match
    ANYWHERE in the repo, which handed every same-named unit reach it did not
    have — e.g. 13 unrelated local `write` helpers each inheriting ~470
    references that belonged to entirely different files. The chief remaining
    failure mode is the opposite one, and deliberate: UNDER-counting. A
    caller that reaches a unit only through a re-export, without ever naming
    the unit's own module, stops counting. Understating reach is the
    direction this skill wants to be wrong in — inventing reach a unit does
    not have is exactly what produced the false 470s.

    RULING (fix round 4): an occurrence written `X.name` counts only when `X`
    IS the unit's own module. Bare `name`, `from mod import name` and
    `mod.name` count as before; `buf.write(...)` no longer does. The bug this
    closes: any file that imports a module AND separately calls a popular
    method name on some unrelated object (`write`/`read`/`get`/`run`/`close`/
    `update`/`send`) credited every one of those calls to the module's
    same-named unit. Reproduced at 3 references for a `sink.py::write` with
    ZERO callers — all three were `io.StringIO.write` — which then OUTRANKED
    the one unit in that file that did have a caller. A receiver that is not
    a plain name at all (`mk().write(...)`) can never be shown to be the
    module, so it does not count either.

    THE HONEST RESIDUAL, stated narrowly because the broad version of this
    claim ("understating, never inventing") was false and a review round
    caught it. Reach can still be invented by ONE remaining path: a BARE
    occurrence of the name inside a file that references the module, where
    that bare name actually means something else — a same-named function
    defined locally in that file, one imported from a DIFFERENT module
    (`import sink` and `from other import write` in the same file), or a
    mention in a comment or docstring. Resolving those needs per-file name
    binding, which is the scope-analysis boundary this approximation stops
    at. So: for the `mod.name` and `X.name` forms, reach is never invented;
    for bare occurrences it still can be, bounded to files that name the
    module. Every consumer sees `inbound_approx: True` beside the number.

    RULING (fix round 3): the per-module reference-file list is memoised by
    module BASENAME (see below) because many units share a basename — but a
    file whose OWN basename equals `module` is now excluded from that list
    entirely, not merely the current unit's own_path. Any file named X.py
    trivially satisfies the path half of `_references_module` against module
    "X" — against ITSELF — regardless of whether it ever mentions some OTHER
    X.py in a different directory. Two files can share a basename from
    different directories (`a/util.py` and `b/util.py`), and each such file's own path
    was wrongly earning it membership
    in the OTHER's reference list. Excluding same-basename files outright
    (rather than only the current unit's own_path) fixes that at its root,
    incidentally also always excludes own_path (whose basename equals module
    by construction), and keeps the cache shared purely by module string —
    still O(distinct modules), no per-unit variant needed.

    COST of that exclusion — a second, narrower UNDER-count stacked on the
    re-export one above, and accepted for the same reason. When `b/util.py`
    genuinely does import from `a/util.py`, its references to `a/util.py`'s
    units are not counted: `b/util.py` is dropped from the module's
    reference-file list by basename, before its text is ever read. Per-package
    `models.py` / `utils.py` / `config.py` make this common rather than a
    corner case, so a unit in one of them can read as less-reached than it is.
    The trade is deliberate and one-directional: real reach lost, self-earned
    reach eliminated — understating, never inventing, as everywhere else here.

    `__init__.py` is NOT an instance of the collision, despite being the first
    example a reader reaches for. `_PATH_TOKEN_RE` matches `[A-Za-z0-9]+`, so
    `pkg/__init__.py` yields the path tokens {pkg, init, py}; the module string
    is `__init__`, which equals none of them. Two packages' `__init__.py`
    files could never earn membership in each other's reference lists by path,
    with or without this exclusion.
    """
    stack = stack or detect_stack(root)
    bare_counts = {}
    attr_counts = {}
    file_lines = {}
    file_texts = {}
    path_tokens = {}
    for rel in stack.iter_source_files(root, include_tests=True):
        text = read_text(root, rel)
        bare_counts[rel], attr_counts[rel] = _name_occurrences(text)
        file_lines[rel] = text.splitlines()
        file_texts[rel] = text
        path_tokens[rel] = set(_PATH_TOKEN_RE.findall(rel))

    def occurrences(rel, name, module):
        """Occurrences of `name` in `rel` that could name `module`'s unit:
        every bare one, plus those written `module.name`. An occurrence
        written `something_else.name` is an attribute of another object and
        is not counted."""
        return bare_counts[rel][name] + attr_counts[rel][(module, name)]

    all_files = sorted(bare_counts)
    module_reffiles_cache = {}

    counts = {}
    for u in units:
        name = u["name"]
        own_path = u["path"]
        module = os.path.splitext(os.path.basename(own_path))[0]

        if module not in module_reffiles_cache:
            # Cached purely by module string, and shared by every unit whose
            # basename matches -- including across unrelated directories
            # (every __init__.py shares "__init__"). A file whose OWN
            # basename equals `module` is excluded from the candidate list
            # entirely, not just the current unit's own_path: any file named
            # X.py trivially satisfies the path half of _references_module
            # against module "X" -- against ITSELF -- regardless of whether
            # it ever mentions some OTHER X.py in a different directory. So
            # `a/util.py` and `b/util.py` must never "reference" each other's
            # module just by both being named util.py; only a file whose own
            # basename DIFFERS gets to earn membership via a genuine
            # name/path match. This also always excludes the current unit's
            # own_path, since own_path's basename equals module by
            # construction -- no separate per-unit lookup-time filter is
            # needed, and the cache stays keyed, and shared, purely by module.
            module_reffiles_cache[module] = [
                rel for rel in all_files
                if os.path.splitext(os.path.basename(rel))[0] != module
                and _references_module(module, file_texts[rel], path_tokens[rel])
            ]
        ref_files = module_reffiles_cache[module]

        total = occurrences(own_path, name, module)
        lines = file_lines.get(own_path, [])
        lineno = u["lineno"]
        if 1 <= lineno <= len(lines):
            def_bare, def_attr = _name_occurrences(lines[lineno - 1])
            total -= def_bare[name] + def_attr[(module, name)]
        for rel in ref_files:
            total += occurrences(rel, name, module)

        counts[u["id"]] = total
    return counts

def already_covered(root: str, units, stack=None) -> dict:
    """Unit id -> the test file naming BOTH it and its module. Keeps the ranking on what is NOT netted.

    Same whole-identifier approximation as inbound_refs. A test file counts as
    covering a unit only when it references the unit's NAME *and* its MODULE
    (the defining file's basename, without `.py`), via `_references_module` —
    the module as a whole-identifier match in the test file's text, or as a
    token in the test file's own path (so `tests/test_refund.py` counts for
    `payments/refund.py` even if the import uses an alias). Name-only
    matching was tried first and rejected: a common name like `apply`,
    `parse`, `run`, `validate` or `get` collides across modules, so a test
    covering one module's `apply` marked every OTHER module's `apply`
    covered too — hiding a genuinely untested unit from `ranked` entirely,
    which is the opposite of safe.

    FIX (round 4): a basename is only a usable identifier when it is UNIQUE.
    When more than one discovered file shares it — `app/utils.py` and
    `lib/utils.py` are both "utils" — the test file's `from app.utils import
    helper` satisfies the bare-basename rule for BOTH, so both were marked
    covered and `lib/utils.py::helper`, which has no test at all, vanished from
    `ranked` entirely (the reproduction reported two units discovered and every
    output bucket empty). Under that ambiguity the module half is raised to
    PATH-qualified evidence (`_path_pattern`: `app.utils` / `app/utils`, bounded
    at both ends), which no sibling can satisfy; the test file's own path tokens
    stop counting too, since `tests/test_utils.py` names both equally well. When
    the basename IS unique in the repo, nothing changes.

    Ambiguity is measured over the non-test `.py` files — the same set units are
    discovered from — so a repo whose basenames are all distinct (the common
    case) takes the looser rule throughout.

    FIX (round 6): path-qualified evidence alone was not merely strict, it was
    UNREPRESENTABLE for the dominant Python test idiom. A test file sitting
    beside the module it imports — `assets/test_ledger.py` doing
    `sys.path.insert(0, dirname(__file__)); import harvest` — can never emit
    the string `context-hygiene-kit.assets.harvest`, so under a collision
    CORRECT evidence had no way to be seen. Measured on this repo: 105 of 321
    units live in a colliding-basename file and the round-4 rule credited
    exactly ZERO of them, which put four genuinely-tested units back into
    `ranked` — one of them at the top, sending the user to write a test for a
    function that already has four call sites in a real suite. That is not the
    conservative direction; it is a broken predicate, and a fabricated gap
    costs the user's attention just as a hidden one costs their safety.

    So a SECOND kind of evidence is admitted, and only this one: the test file
    is in the SAME DIRECTORY as the defining file, and reaches the unit THROUGH
    an import of that module — `import harvest` then `harvest.<name>`,
    `import harvest as H` then `H.<name>`, or `from harvest import <name>`
    (`_reached_through_module`), AT A CALL SITE. Note what that is not: it is
    not the bare whole-identifier match used elsewhere here. The weaker form
    would credit `harvest.py::main` to a file whose only `main` is
    `unittest.main()` — 2 of 6 recovered units were exactly that — so the
    same-directory route uses the stronger predicate and recovers 4 units, all
    genuine. The call-site half was added in round 7: without it,
    `mock.patch("harvest.collect")` and a `# harvest.collect` comment both
    credited a unit that has no test, which is over-credit in the route this
    fix opened.

    The cross-directory over-credit round 4 closed stays closed, because the
    colliding sibling is by definition in a different directory. And the one
    way this could still over-credit — a same-directory test that actually
    exercises the SIBLING, path-qualified — is excluded explicitly: if the text
    path-qualifies any rival file of the same basename, the same-directory
    route is refused for this unit.

    The remaining failure mode is milder: a unit exercised only through a
    re-export (a test that reaches it via a different module's name and never
    mentions its own module) reads as uncovered and may get a duplicate test
    written for it. That direction is still safe — the worst case is a
    redundant test, not a hidden gap.
    """
    stack = stack or detect_stack(root)
    test_files = [rel for rel in stack.iter_source_files(root, include_tests=True)
                  if stack.is_test_path(rel)]
    texts = {rel: read_text(root, rel) for rel in test_files}
    path_tokens = {rel: set(_PATH_TOKEN_RE.findall(rel)) for rel in test_files}
    by_basename = collections.defaultdict(list)
    for rel in stack.iter_source_files(root):
        by_basename[os.path.splitext(os.path.basename(rel))[0]].append(rel)
    covered = {}
    for u in units:
        name_pattern = re.compile(r"\b%s\b" % re.escape(u["name"]))
        module = os.path.splitext(os.path.basename(u["path"]))[0]
        siblings = by_basename.get(module, ())
        ambiguous = len(siblings) > 1
        qualified = _path_pattern(u["path"]) if ambiguous else None
        rivals = ()
        if ambiguous:
            rivals = tuple(p for p in (_path_pattern(r) for r in siblings
                                       if r != u["path"]) if p is not None)
        home = os.path.dirname(u["path"])
        for rel in sorted(texts):
            text = texts[rel]
            if not name_pattern.search(text):
                continue
            if ambiguous:
                if qualified is not None and qualified.search(text):
                    pass                          # path-qualified: unambiguous
                elif (os.path.dirname(rel) == home
                        and _reached_through_module(module, u["name"], text)
                        and not any(r.search(text) for r in rivals)):
                    pass                          # beside the module it imports
                else:
                    continue
            elif not _references_module(module, text, path_tokens[rel]):
                continue
            covered[u["id"]] = rel
            break
    return covered

def _normalise(value, hi):
    return 0.0 if hi <= 0 else round(value / hi, 6)


def rank(root: str, since: str = "6 months ago", top_n: int = 10,
         stack=None) -> dict:
    """The plan: what to net, what cannot be netted, and what is already covered.

    `ranked`, `remainder` and `not_netted` partition the discovered units by
    triage tier and coverage — every unit appears in exactly one of those
    three. `covered` is NOT a fourth bucket in that partition: it is a flat
    INDEX, across ALL discovered units regardless of tier, of ids that
    `already_covered` matched to a test file. A unit that is both tier >= 3
    (so it lands in `not_netted`) and already covered by a test still appears
    in `covered` too — nothing is dropped, but a consumer should not assume
    the four keys partition cleanly.
    """
    stack = stack or detect_stack(root)
    # The discovery path ("precise" / "heuristic") is part of the stack
    # interface because a run that silently degraded is not comparable to
    # one that did not. Reporting it is a separate change; this one alters
    # no output, and Python never degrades.
    units, _discovery_path = stack.discover_units(root)
    churn_by_path = churn(root, since)
    refs = inbound_refs(root, units, stack)
    covered = already_covered(root, units, stack)

    rows = []
    for u in units:
        tier, reason = stack.triage(root, u)
        row = dict(u)
        row["churn"] = churn_by_path.get(u["path"], 0)
        row["inbound_refs"] = refs.get(u["id"], 0)
        row["inbound_approx"] = True
        row["tier"] = tier
        row["tier_reason"] = reason
        row["covered_by"] = covered.get(u["id"])
        rows.append(row)

    max_churn = max((r["churn"] for r in rows), default=0)
    max_refs = max((r["inbound_refs"] for r in rows), default=0)
    for r in rows:
        if r["tier"] >= 3 or r["covered_by"]:
            r["score"] = 0.0
            continue
        # Churn and reach are both weak alone: a file nobody calls but everyone
        # edits is churny noise; a stable widely-used helper rarely breaks. The
        # product favours units that are BOTH reached and moving, which is where
        # an agent is most likely to do damage. +1 keeps a zero on one axis from
        # annihilating a strong signal on the other.
        #
        # A consequence of the +1 floor, left in deliberately: a max-churn,
        # zero-refs unit can still score up to 1.0 and outrank a moderate-churn
        # unit with decent refs. That is intended, not a bug the +1 should be
        # tuned away — zero STATIC references very often means "entry point
        # the approximate reference counter cannot see" (a CLI dispatcher, a
        # `main`, a plugin hook invoked by name/registry) rather than "nothing
        # depends on it," while churn is the more reliable signal of the two.
        # Ranking the repo's hottest file highly under that ambiguity is the
        # right default. Each ranked row still carries its raw `churn` and
        # `inbound_refs`, so a human reviewing the list can see which signal
        # actually drove a given placement.
        r["score"] = round((_normalise(r["churn"], max_churn) + 1)
                           * (_normalise(r["inbound_refs"], max_refs) + 1) - 1, 6)

    netted = [r for r in rows if r["tier"] < 3 and not r["covered_by"]]
    netted.sort(key=lambda r: (-r["score"], r["id"]))   # ties broken by id: deterministic
    not_netted = sorted((r for r in rows if r["tier"] >= 3),
                        key=lambda r: (r["tier"], r["id"]))

    return {
        "root": os.path.abspath(root),
        "stack": stack.STACK_NAME,
        "window": since,
        "units_discovered": len(units),
        "ranked": netted[:top_n],
        "remainder": netted[top_n:],
        "not_netted": not_netted,
        "covered": sorted(covered),
    }

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Rank untested units by risk and triage their testability.")
    p.add_argument("repo", help="path to the repository to analyse")
    p.add_argument("--top-n", type=int, default=10,
                   help="how many units to net in this pass (default: 10)")
    p.add_argument("--since", default="6 months ago",
                   help="churn window, any git --since expression (default: 6 months ago)")
    args = p.parse_args(argv)
    if not os.path.isdir(args.repo):
        sys.stderr.write(f"error: not a directory: {args.repo}\n")
        return 2
    # Say so when the analysed root is not the git repo root. Churn is scoped
    # to that subtree (see `churn`), and a scope the caller did not intend is
    # the difference between "this code is stable" and "you looked at a
    # sixteenth of the history" — too big a difference to leave implicit.
    prefix = _git_prefix(args.repo)
    if prefix:
        sys.stderr.write(f"note: analysing a subdirectory of a git repo; churn is "
                         f"scoped to {prefix}\n")
    json.dump(rank(args.repo, args.since, args.top_n), sys.stdout,
              indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
