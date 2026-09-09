#!/usr/bin/env python3
"""File helpers shared by the ranker and by every stack module.

A LEAF on purpose: it imports nothing else from this directory, which is what
keeps `rank_risk -> stack_* -> stack_common` acyclic. `rank_risk.py` is loaded
by path (`importlib.util.spec_from_file_location`) rather than imported by
name, so a stack module that reached back into it for these two names would
re-execute the ranker mid-import instead of finding it in `sys.modules`.

`SKIP_DIRS` and `read_text` moved here unchanged from `rank_risk.py`;
`rank_risk` re-exports them, so `rank_risk.SKIP_DIRS` and `rank_risk.read_text`
still resolve. The manifest rule below was added when stack detection stopped
taking the first vote and started weighing evidence: every stack scores itself
the same way, so the rule that decides what a manifest is worth belongs here
rather than once per stack.
"""
from __future__ import annotations

import fnmatch
import os

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build",
             "dist", ".tox", ".mypy_cache", ".pytest_cache", "vendor",
             "site-packages", ".eggs"}

# ── Manifest evidence, shared by every stack's `evidence(root)` ──────────
#
# A manifest is the one piece of evidence that outweighs file counts: a repo
# that declares itself a node package IS a node package, whatever else is in
# the tree. Two rules bound that, and both exist because the detector this
# replaced -- first match wins, on "does any evidence exist" -- answered the
# wrong question and reclassified THIS repo (51 non-test `.py` files against
# 17 `.mjs`/`.ts`) as node on the strength of one `package.json` that is not
# even its own.
#
# 1. AT OR NEAR THE ANALYSED ROOT. A manifest is a claim about the directory it
#    sits in. Two levels down still counts, because a monorepo's
#    `packages/api/package.json` is a real claim about a real package; ten
#    levels down is a claim about something else.
# 2. NOT INSIDE A TEMPLATE DIRECTORY. A manifest under `assets/`, `templates/`,
#    `fixtures/`, `examples/` or `testdata/` describes a scaffold this repo
#    SHIPS or a fixture its tests build -- code authored here that is not what
#    this repo IS. This repo's only `package.json` sits at
#    `starlight-handbook-kit/assets/templates/scaffold/package.json`: a
#    Starlight site the skill installs into someone else's repo.
TEMPLATE_DIRS = {"assets", "template", "templates", "fixture", "fixtures",
                 "example", "examples", "testdata", "__fixtures__", "golden"}

# What a manifest is worth: it SCALES a stack's claim, it does not add to it.
#
# The additive form this replaced (`files + 100`) was wrong in the mainline
# case, and reproducibly so: 40 `.py` files, 5 `.js` files and a root
# `package.json` holding a `prettier` script scored node 105 against python 40,
# so the skill ranked five files and ignored forty. A root `package.json` for
# formatting or git hooks is ordinary in a Python repo, which makes that shape
# common rather than a corner. Any additive bonus large enough to carry the
# case it was written for -- a two-file node package that a handful of vendored
# Python scripts must not outvote -- is by construction large enough to swamp a
# real majority, because "decisive against a scattering" and "swamped by a
# scattering" are the same number seen from two sides.
#
# Multiplying cannot do that. A multiplier applied to a small claim stays
# small, and when BOTH stacks declare themselves it appears on both sides and
# cancels, leaving the file counts to decide -- which is what a polyglot repo
# that honestly declares both halves wants.
#
# THE CONSTANTS WERE CHOSEN AGAINST `CASE_TABLE` in `test_stack_node.py`,
# twelve real repo shapes with the verdict each must reach, and they are pinned
# from BOTH sides by that table. Lower them and a one-file node package with a
# real `package.json` loses to six vendored Python scripts (row N1). Raise them
# and row 12 flips: ten declared frontend files outvote forty backend ones.
#
# The upper bound MOVED when a manifest's content started deciding. It used to
# be row 1 -- 40 `.py`, 5 `.js`, a tooling `package.json` -- which no pair in
# `0..8 x 2..6` can break any more, because that manifest now scales nothing at
# all. Row 12 exists to replace the bound rather than leave the table pinned
# from one side only. Change a constant only with the whole table in front of
# you.
MANIFEST_FLOOR = 5
MANIFEST_MULTIPLIER = 2

# How far below `root` a manifest still describes `root`. 2 covers a monorepo's
# `packages/<pkg>/<manifest>`.
MANIFEST_MAX_DEPTH = 2


def iter_manifests(root: str, names):
    """Yield `(relative path, matched key)` for each of `names` at or near `root`.

    Walks at most `MANIFEST_MAX_DEPTH` levels below `root` and prunes
    `SKIP_DIRS`, dotted directories and `TEMPLATE_DIRS`, so a vendored
    `node_modules/*/package.json` and a shipped scaffold's manifest are never
    yielded at all.

    AN ENTRY IN `names` IS A FILENAME OR A GLOB, and the second kind is why
    this yields the KEY THAT MATCHED rather than the basename. Exact names
    yield themselves, so nothing changed for them. A glob containing `/` is
    matched against the manifest's repo-relative PATH
    (`requirements/*.txt` -> `requirements/base.txt`); one without is matched
    against the basename (`requirements-*.txt`). Either way the caller's
    `classify` receives the SPEC, not the file's own name -- `base.txt` says
    nothing about its format, `requirements/*.txt` says everything.

    The reason the glob forms exist at all: `MANIFESTS` used to be exact
    filenames at the root, and Django's near-universal split-requirements
    layout (`requirements/base.txt`, `requirements/dev.txt`) matched none of
    them. A conventional Django repo therefore earned NO manifest credit while
    the `package.json` it has for its frontend assets earned node's, and a
    30-module Python service with 20 JS files was detected as node and ranked
    the JavaScript.
    """
    exact, globs = set(), []
    for spec in names:
        (globs.append(spec) if ("*" in spec or "?" in spec or "[" in spec)
         else exact.add(spec))
    for base, dirs, files in os.walk(root):
        rel = os.path.relpath(base, root).replace(os.sep, "/")
        depth = 0 if rel == "." else rel.count("/") + 1
        if depth >= MANIFEST_MAX_DEPTH:
            dirs[:] = []
        else:
            dirs[:] = sorted(d for d in dirs
                             if d not in SKIP_DIRS
                             and d not in TEMPLATE_DIRS
                             and not d.startswith("."))
        for name in sorted(files):
            path = ("%s/%s" % (rel, name) if rel != "." else name)
            if name in exact:
                yield path, name
                continue
            for spec in globs:
                if fnmatch.fnmatchcase(path if "/" in spec else name, spec):
                    yield path, spec
                    break


def has_manifest(root: str, names) -> bool:
    """True when one of `names` sits at or near `root`, outside a template dir.

    The EXISTENCE question, kept separate from the content one on purpose:
    `declaring_manifest` is what scoring asks, and a test that wants to prove
    the classifier gave up -- rather than the walk -- asserts this one is still
    True. See `test_a_tooling_only_manifest_scales_nothing`.
    """
    for _rel, _name in iter_manifests(root, names):
        return True
    return False


def declaring_manifest(root: str, names, classify) -> bool:
    """True when a manifest at or near `root` DECLARES the stack, not just its tooling.

    EXISTENCE WAS DOING WORK CONTENT SHOULD DO, and that is the whole reason
    this function is not `has_manifest`. A root `package.json` is the commonest
    thing in a Python repo that has one -- `husky`, `prettier`, `lint-staged`
    -- and under an existence rule it scaled node's claim by the same factor as
    a `package.json` with an entry point and runtime dependencies. Eight `.py`
    files, three `.js` files and a husky manifest scored node 16 to python 8:
    the skill ranked three files and ignored eight. NO `(files + floor) *
    multiplier` SHAPE CAN FIX THAT, because the file counts are identical to a
    fresh node project's; only the manifest can tell them apart.

    `classify(filename, text) -> "declaring" | "tooling"` is the stack's own,
    because only the stack knows its formats. It is a REQUIRED argument rather
    than an optional one so that a stack added later cannot inherit the
    existence rule by forgetting to pass it.

    ANY declaring manifest wins: a monorepo whose root `package.json` holds
    only `workspaces` is declared by `packages/api/package.json` one level
    down, and a repo that keeps a tooling manifest beside a real one is
    declared by the real one.
    """
    for rel, name in iter_manifests(root, names):
        if classify(name, read_text(root, rel)) == "declaring":
            return True
    return False


def read_text(root: str, rel: str) -> str:
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def evidence_score(n_files: int, root: str, manifests, classify) -> int:
    """A stack's evidence: its source files, scaled when it declares itself.

    ZERO WHEN THERE ARE NO SOURCE FILES AT ALL, manifest or not. A manifest
    with nothing behind it must not win a repo the stack would then discover no
    units in -- a `package.json` carrying one `lint` script, or a
    `requirements.txt` beside a pure-TypeScript tree. The multiplier scales a
    real claim; it never manufactures one, which is why this is a guard clause
    and not a `max(files, 1)`.

    ONLY A DECLARING MANIFEST SCALES. `classify` decides which those are; see
    `declaring_manifest`, and `MANIFEST_BODIES` in `test_stack_node.py` for the
    two shapes every case-table row is built from.

    `MANIFEST_FLOOR` is what makes a SMALL declared project beat a scattering
    of another language's scripts: two `.ts` files and a `package.json` are a
    node repo even beside three Python helpers, and without a floor the
    multiplier on a claim of 2 cannot say so.
    """
    if not n_files:
        return 0
    if not declaring_manifest(root, manifests, classify):
        return n_files
    return (n_files + MANIFEST_FLOOR) * MANIFEST_MULTIPLIER
