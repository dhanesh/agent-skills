#!/usr/bin/env python3
"""Rank a repo's untested units by risk, and triage how testable each one is.

Stdlib + git only. No network, no third-party packages, no call-graph service:
the blast-radius half is a deliberate static approximation, labelled as such in
the output, so this runs anywhere the repo does.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build",
             "dist", ".tox", ".mypy_cache", ".pytest_cache", "vendor",
             "site-packages", ".eggs"}


def _is_test_path(rel: str) -> bool:
    base = os.path.basename(rel)
    parts = rel.replace(os.sep, "/").split("/")
    return (base.startswith("test_") or base.endswith("_test.py")
            or "tests" in parts or "test" in parts)


def iter_py_files(root: str, include_tests: bool = False):
    """Yield repo-relative paths of .py files, skipping vendor dirs. Sorted."""
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
            if not include_tests and _is_test_path(rel):
                continue
            out.append(rel)
    return sorted(out)


def read_text(root: str, rel: str) -> str:
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def discover_units(root: str):
    """Module-level defs and classes in non-test .py files, sorted by id.

    Only module-level names: a nested def is not independently addressable by a
    test runner, so it cannot be pinned on its own. `_`-prefixed names are
    private by convention and are exercised through their public callers.
    """
    units = []
    for rel in iter_py_files(root):
        try:
            tree = ast.parse(read_text(root, rel))
        except SyntaxError:
            continue                      # a file we cannot parse is not a unit we can pin
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name.startswith("_"):
                continue
            units.append({
                "id": f"{rel}::{node.name}",
                "path": rel,
                "name": node.name,
                "lineno": node.lineno,
                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
            })
    return sorted(units, key=lambda u: u["id"])


def churn(root: str, since: str = "6 months ago") -> dict:
    """Commits touching each repo-relative path within the window.

    Exact where git is present, and the best available proxy for "what a person
    keeps changing" — which is what an agent will touch next. Absent git or
    history, returns {} rather than raising: churn is one signal of two, and a
    tarball checkout must still get a ranking.
    """
    try:
        r = subprocess.run(
            ["git", "log", "--format=", "--name-only", f"--since={since}"],
            cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    counts: dict = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if line:
            counts[line] = counts.get(line, 0) + 1
    return counts


def inbound_refs(root: str, units) -> dict:
    """Approximate blast radius: whole-identifier references outside the definition file.

    APPROXIMATE, on purpose, and labelled as such wherever it surfaces. It counts
    identifier occurrences, so a mention in a comment or a docstring counts and a
    dynamic `getattr(mod, name)` call does not. A real call graph would do this
    better — the report says so and names it as an optional upgrade — but
    requiring one would make the skill undeployable in the repos that need it most.
    """
    texts = {rel: read_text(root, rel) for rel in iter_py_files(root, include_tests=True)}
    counts = {}
    for u in units:
        pattern = re.compile(r"\b%s\b" % re.escape(u["name"]))
        total = 0
        for rel, text in texts.items():
            if rel == u["path"]:
                continue                  # never count a unit's own definition site
            total += len(pattern.findall(text))
        counts[u["id"]] = total
    return counts
