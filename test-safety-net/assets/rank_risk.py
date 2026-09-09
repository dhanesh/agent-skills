#!/usr/bin/env python3
r"""Rank a repo's untested units by risk, and triage how testable each one is.

Stdlib + git only. No network, no third-party packages, no call-graph service:
the blast-radius half is a deliberate static approximation, labelled as such in
the output, so this runs anywhere the repo does.

STACK-AGNOSTIC CORE. Churn, reference counting, coverage detection, scoring,
ranking, the CLI and the JSON shape live here and are shared by every stack.
Everything a language decides for itself -- which files are source, which are
tests, what a unit is, and what tier a unit lands in -- lives behind the stack
interface and is implemented once per stack (`stack_python.py`, and one module
per stack added beside it).

THE STACK INTERFACE. A stack module supplies exactly fourteen names. The rule
that decides membership: if answering the question requires READING A LANGUAGE,
it belongs to the stack; if it only orchestrates or scores, it stays here.

  identity
    STACK_NAME                                   str; the report's "stack" key
    evidence(root) -> int                        HOW MUCH of this repo is this
                                                 stack's: the non-test source
                                                 files it claims, plus a
                                                 manifest bonus. Never a
                                                 boolean -- see `detect_stack`.

  files
    iter_source_files(root, include_tests=False) sorted repo-relative paths
    is_test_path(rel) -> bool                    is this path a test file?
    is_test_for(test_rel, src_rel) -> bool       is it positioned as a test OF
                                                 that file?

  naming
    module_of(rel) -> str                        the module identity `rel`
                                                 defines -- the name other
                                                 files use to talk about it
    name_pattern(name) -> compiled re            `name` as a whole
                                                 identifier, bounded the way
                                                 THAT language bounds one
    path_pattern(rel) -> compiled re | None      `rel` written as a module
                                                 path, bounded at both ends;
                                                 None when it has no qualifier

  lexing
    IDENTIFIER_RE                                compiled re; what an
                                                 identifier IS
    preceding_qualifier(text, start)             the receiver an identifier is
                                                 an attribute OF: None (bare),
                                                 a name, or "" (unnameable)

  grammar
    module_bindings(module, text, *, src_rel, ref_rel) -> (aliases, names)
                                                 what `text` binds for
                                                 `module` by an actual import
    reached_through_module(module, name, text, *, src_rel, ref_rel) -> bool
                                                 does `text` CALL `name`
                                                 through such an import?
                                                 Both are given the DEFINING
                                                 file and the REFERENCING one:
                                                 a language whose imports name
                                                 a path (`from "../utils"`)
                                                 cannot resolve one without
                                                 both, and matching a
                                                 specifier's last segment
                                                 instead credits every
                                                 same-named file in the repo.

  analysis
    discover_units(root) -> (units, path)        the units, plus WHICH
                                                 discovery path ran
                                                 ("precise" | "heuristic")
    triage(root, unit) -> (tier, reason)         the FILTER -- never the
                                                 enforcement; the runtime
                                                 guard enforces the no-I/O
                                                 invariant

Six of those were named when the seam was cut; eight more followed, each from
a place this file was still reading Python without asking. The walk and the
test-file predicate, because `inbound_refs` and `already_covered` both iterate
the source tree and both must recognise a test file: hard-coding `test_*.py`
here would make a node repo's `*.test.ts` invisible to coverage detection,
which credits a tested unit with no coverage and ranks it as a gap. Naming and
lexing, because `os.path.splitext(basename(rel))[0]` and a `.`-only qualifier
are Python answers to questions every language answers differently. And the
binding grammar, because it is `already_covered`'s STRONGEST evidence -- the
same-directory route that recovered 105 units' worth of collision cases here.
A stack that inherited Python's `import X as Y` / `from X import Y` forms
would credit exactly nothing through that route and report a clean result,
which is the one failure this file must not have: guessing where it must ask.
And `name_pattern`, because `\b` is the same kind of guess: it is defined
against `[A-Za-z0-9_]`, so an export named `$fetch` could never be matched in
any test file and would read as an uncovered gap forever.
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

import stack_node                                                   # noqa: E402
import stack_python                                                 # noqa: E402
from stack_common import SKIP_DIRS, read_text                       # noqa: E402,F401

# ── Stack registry ───────────────────────────────────────────────────────
# HIGHEST EVIDENCE WINS, so registration ORDER carries no meaning at all and a
# stack can be appended without re-litigating what it sits in front of.
#
# The detector this replaced took the FIRST stack whose `matches(root)` was
# True, which answers "does any evidence exist" when the question a real repo
# poses is "what is this repo MOSTLY". Polyglot repos are the common case, not
# the exception: THIS repo holds 51 non-test `.py` files, 17 `.mjs`/`.ts`, and
# exactly one `package.json` -- which belongs to a Starlight scaffold that
# `starlight-handbook-kit` installs into somebody else's repo. A generous node
# `matches()` in front of Python therefore reclassified the ranker's own
# corpus as node, and a stack picked that way discovers the wrong units,
# triages them with the wrong tables, and reports it all as a clean result.
STACKS = [stack_node, stack_python]

# How close two stacks may be before the answer is "I do not know". Relative,
# so it scales with the size of the repo rather than firing on every small
# tree. A tie is REPORTED, never guessed: guessing between two stacks that
# scored the same is exactly the silent misclassification this detector exists
# to stop, and the caller can always settle it with `--stack`.
AMBIGUITY_MARGIN = 0.10


class AmbiguousStack(Exception):
    """Two stacks scored too close to call. Carries the evidence for the report."""

    def __init__(self, scores):
        self.scores = scores          # [(STACK_NAME, score)], best first
        detail = ", ".join("%s=%d" % (name, score) for name, score in scores)
        super().__init__("cannot tell which stack this repo is: " + detail)


def stack_evidence(root: str):
    """[(STACK_NAME, score)] for every registered stack, best first.

    The evidence behind a detection verdict, for reporting it. Ties broken by
    name so two runs on the same tree agree.
    """
    return sorted(((s.STACK_NAME, s.evidence(root)) for s in STACKS),
                  key=lambda t: (-t[1], t[0]))


def stack_by_name(name: str):
    """The registered stack called `name`, or None."""
    return next((s for s in STACKS if s.STACK_NAME == name), None)


def detect_stack(root: str, name: str = None):
    """The stack with the most evidence in `root`; Python when there is none.

    `name` overrides detection outright (the CLI's `--stack`), because a
    detector that weighs evidence can still be wrong about a repo whose owner
    knows better, and "wrong with no way to say so" is worse than wrong.

    Raises `AmbiguousStack` when the top two scores are within
    `AMBIGUITY_MARGIN` of each other. Nothing below the ranker guesses on the
    caller's behalf: `main` prints the scores and asks for `--stack`.
    """
    if name is not None:
        stack = stack_by_name(name)
        if stack is None:
            raise ValueError("unknown stack: %s (have: %s)"
                             % (name, ", ".join(s.STACK_NAME for s in STACKS)))
        return stack
    scores = stack_evidence(root)
    if not scores or scores[0][1] <= 0:
        # Nothing claims it. Python is the fallback, so an unplaceable
        # directory still gets a well-formed (empty) report rather than an
        # error -- the behaviour this had before evidence scoring.
        return stack_python
    runner_up = scores[1][1] if len(scores) > 1 else 0
    if runner_up > 0 and scores[0][1] - runner_up <= scores[0][1] * AMBIGUITY_MARGIN:
        raise AmbiguousStack(scores)
    return stack_by_name(scores[0][0])


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

_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _name_occurrences(stack, text: str):
    """(bare, attr) occurrence counters for one file's text.

    `bare[name]` counts occurrences that stand on their own; `attr[(recv,
    name)]` counts occurrences written `recv.name`. Splitting them is what
    lets `inbound_refs` count `sink.write` for module `sink` while refusing
    `buf.write` — see its docstring for why that distinction is load-bearing.

    WHAT an identifier is, and what counts as its receiver, are the stack's
    calls (`stack.IDENTIFIER_RE`, `stack.preceding_qualifier`). The counting
    is the same in every language; the lexing is not — `$` is an identifier
    character in JS and not in Python, and `?.` is a qualifier there and a
    syntax error here.
    """
    bare = collections.Counter()
    attr = collections.Counter()
    for m in stack.IDENTIFIER_RE.finditer(text):
        qual = stack.preceding_qualifier(text, m.start())
        if qual is None:
            bare[m.group(0)] += 1
        else:
            attr[(qual, m.group(0))] += 1
    return bare, attr


def _references_module(stack, module: str, text: str, path_tokens) -> bool:
    """True if `text`, or `path_tokens` (from the file's own path), plausibly names `module`.

    `module` is a defining file's module identity — `stack.module_of(rel)`,
    which for Python is the basename without `.py`. Shared by
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
      * `already_covered` requires PATH-qualified evidence (`stack.path_pattern`)
        when a basename is shared by more than one discovered file, and only
        falls back to this predicate when the basename is unique.
    """
    if stack.name_pattern(module).search(text):
        return True
    return module in path_tokens


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
        bare_counts[rel], attr_counts[rel] = _name_occurrences(stack, text)
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
        module = stack.module_of(own_path)

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
                if stack.module_of(rel) != module
                and _references_module(stack, module, file_texts[rel],
                                       path_tokens[rel])
            ]
        ref_files = module_reffiles_cache[module]

        total = occurrences(own_path, name, module)
        lines = file_lines.get(own_path, [])
        lineno = u["lineno"]
        if 1 <= lineno <= len(lines):
            def_bare, def_attr = _name_occurrences(stack, lines[lineno - 1])
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
    PATH-qualified evidence (`stack.path_pattern`: `app.utils` / `app/utils`, bounded
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
    is POSITIONED AS A TEST OF the defining file (`stack.is_test_for`; for
    Python, the same directory), and reaches the unit THROUGH
    an import of that module — `import harvest` then `harvest.<name>`,
    `import harvest as H` then `H.<name>`, or `from harvest import <name>`
    (`stack.reached_through_module`), AT A CALL SITE. Note what that is not: it is
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
        by_basename[stack.module_of(rel)].append(rel)
    covered = {}
    for u in units:
        name_pattern = stack.name_pattern(u["name"])
        module = stack.module_of(u["path"])
        siblings = by_basename.get(module, ())
        ambiguous = len(siblings) > 1
        qualified = stack.path_pattern(u["path"]) if ambiguous else None
        rivals = ()
        if ambiguous:
            rivals = tuple(p for p in (stack.path_pattern(r) for r in siblings
                                       if r != u["path"]) if p is not None)
        for rel in sorted(texts):
            text = texts[rel]
            if not name_pattern.search(text):
                continue
            if ambiguous:
                if qualified is not None and qualified.search(text):
                    pass                          # path-qualified: unambiguous
                elif (stack.is_test_for(rel, u["path"])
                        and stack.reached_through_module(
                            module, u["name"], text,
                            src_rel=u["path"], ref_rel=rel)
                        and not any(r.search(text) for r in rivals)):
                    pass                          # positioned as its test, and imports it
                else:
                    continue
            elif not _references_module(stack, module, text, path_tokens[rel]):
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
    p.add_argument("--stack", default=None,
                   choices=sorted(s.STACK_NAME for s in STACKS),
                   help="override stack detection (default: detect by evidence)")
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
    # WHICH STACK WON, AND ON WHAT EVIDENCE — always, not only when it is
    # close. The verdict decides which files are read, which units exist and
    # which marker tables triage them, so a run that picked the wrong one is
    # wrong everywhere at once while still printing a well-formed report. The
    # scores are the one thing that makes that visible without re-running.
    scores = stack_evidence(args.repo)
    try:
        stack = detect_stack(args.repo, args.stack)
    except AmbiguousStack as exc:
        sys.stderr.write("error: %s\n" % exc)
        sys.stderr.write("       re-run with --stack {%s} to choose.\n"
                         % ",".join(name for name, _ in scores))
        return 2
    sys.stderr.write("note: stack=%s (evidence: %s%s)\n"
                     % (stack.STACK_NAME,
                        ", ".join("%s=%d" % (n, v) for n, v in scores),
                        "; forced by --stack" if args.stack else ""))
    json.dump(rank(args.repo, args.since, args.top_n, stack), sys.stdout,
              indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
