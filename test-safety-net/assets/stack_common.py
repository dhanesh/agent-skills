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

# What a manifest is worth, in units of "source files this stack claims". Big
# enough to be decisive against a scattering of stray files (a Python repo with
# one `.mjs` helper), small enough that a genuine several-hundred-file majority
# still outweighs a manifest.
MANIFEST_BONUS = 100

# How far below `root` a manifest still describes `root`. 2 covers a monorepo's
# `packages/<pkg>/<manifest>`.
MANIFEST_MAX_DEPTH = 2


def has_manifest(root: str, names) -> bool:
    """True when one of `names` sits at or near `root`, outside a template dir.

    Walks at most `MANIFEST_MAX_DEPTH` levels below `root` and prunes
    `SKIP_DIRS`, dotted directories and `TEMPLATE_DIRS`, so a vendored
    `node_modules/*/package.json` and a shipped scaffold's manifest both score
    nothing.
    """
    wanted = frozenset(names)
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
        if any(name in wanted for name in files):
            return True
    return False


def read_text(root: str, rel: str) -> str:
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""
