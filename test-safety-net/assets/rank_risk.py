#!/usr/bin/env python3
"""Rank a repo's untested units by risk, and triage how testable each one is.

Stdlib + git only. No network, no third-party packages, no call-graph service:
the blast-radius half is a deliberate static approximation, labelled as such in
the output, so this runs anywhere the repo does.
"""
from __future__ import annotations

import ast
import collections
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

    The best available proxy for "what a person keeps changing" — which is
    what an agent will touch next — but not exact. It uses `-z` so a path
    containing a quote or non-ASCII byte comes back raw instead of git's
    default C-quoted escaping (which would otherwise fail to match the plain
    path `iter_py_files` produces, silently reading that file's churn as 0).
    It also inherits git's default rename detection: a renamed file's history
    is attributed to its new path only, so pre-rename commits are not counted
    there. Absent git or history, returns {} rather than raising: churn is one
    signal of two, and a tarball checkout must still get a ranking.
    """
    try:
        r = subprocess.run(
            ["git", "log", "--format=", "--name-only", "-z", f"--since={since}"],
            cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    counts: dict = {}
    for part in r.stdout.split("\0"):
        if part:
            counts[part] = counts.get(part, 0) + 1
    return counts


_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*")


def inbound_refs(root: str, units) -> dict:
    """Approximate blast radius: whole-identifier references outside the definition file.

    APPROXIMATE, on purpose, and labelled as such wherever it surfaces. It counts
    identifier occurrences, so a mention in a comment or a docstring counts and a
    dynamic `getattr(mod, name)` call does not. A real call graph would do this
    better — the report says so and names it as an optional upgrade — but
    requiring one would make the skill undeployable in the repos that need it most.

    Each file's identifiers are tokenised once into a per-file Counter, summed
    into a global Counter; a unit's count is the global total for its name minus
    that name's count in the unit's own defining file. This is O(total bytes +
    units) rather than O(units x total bytes) — the naive per-unit regex scan
    does not survive on a repo with thousands of units.
    """
    per_file_counts = {}
    global_counts: collections.Counter = collections.Counter()
    for rel in iter_py_files(root, include_tests=True):
        text = read_text(root, rel)
        counter = collections.Counter(_IDENTIFIER_RE.findall(text))
        per_file_counts[rel] = counter
        global_counts.update(counter)

    counts = {}
    for u in units:
        own_file_count = per_file_counts.get(u["path"], {}).get(u["name"], 0)
        counts[u["id"]] = global_counts.get(u["name"], 0) - own_file_count
    return counts


# I/O markers, grouped by whether the boundary can be CONTROLLED in a test.
# Controllable -> tier 2 (pin at a wider boundary, with the boundary named).
# Uncontrollable without a seam -> tier 3 (report the seam; write nothing).
CONTROLLABLE = {
    "filesystem": ("open(", "pathlib.", "os.path.", "os.remove", "os.mkdir",
                   "shutil.", "tempfile."),
    "clock": ("datetime.now", "datetime.utcnow", "time.time", "time.sleep",
              "date.today"),
    "randomness": ("random.", "uuid.uuid4", "secrets."),
    "environment": ("os.environ", "os.getenv"),
}
UNCONTROLLABLE = {
    "network": ("requests.", "urllib.request", "httpx.", "socket.", "aiohttp.",
                "boto3.", "urlopen("),
    "database": ("psycopg2.", "sqlite3.connect", "pymongo.", "MongoClient",
                 "create_engine", "cursor()"),
    "subprocess": ("subprocess.", "os.system", "os.popen"),
}


def _markers(text):
    """(group, marker) for every I/O marker present in `text`. Deterministic order."""
    hits = []
    for group in sorted(UNCONTROLLABLE):
        for m in UNCONTROLLABLE[group]:
            if m in text:
                hits.append((group, m, False))
    for group in sorted(CONTROLLABLE):
        for m in CONTROLLABLE[group]:
            if m in text:
                hits.append((group, m, True))
    return hits


def _module_level_source(tree, lines):
    """Source of statements OUTSIDE any def/class — what runs on import.

    Only statements containing a Call or an Attribute can perform I/O at
    import time. A module-level data literal that merely NAMES an I/O
    marker — a driver allowlist, a settings table, this module's own
    marker constants — is data, not behaviour, and must not condemn every
    unit in the file to Tier 4.
    """
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Import, ast.ImportFrom)):
            continue
        if not any(isinstance(n, (ast.Call, ast.Attribute)) for n in ast.walk(node)):
            continue
        seg = lines[node.lineno - 1: getattr(node, "end_lineno", node.lineno)]
        out.extend(seg)
    return "\n".join(out)


def triage(root: str, unit) -> tuple:
    """Classify how testable a unit is. Returns (tier, reason).

    The ranker is deliberately CONSERVATIVE: it reads text, not semantics, so a
    call that is actually behind an injected parameter still reads as I/O. The
    SKILL.md permits promoting a unit after inspection — but only by recording
    the promotion, never silently.
    """
    text = read_text(root, unit["path"])
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 4, "file does not parse; nothing in it can be pinned"
    lines = text.splitlines()

    # Tier 4 first: if importing the module does I/O, no unit in it is reachable.
    mod_hits = _markers(_module_level_source(tree, lines))
    uncontrollable_mod = [h for h in mod_hits if not h[2]]
    if uncontrollable_mod:
        group, marker, _ = uncontrollable_mod[0]
        return 4, f"module does {group} I/O at import time ({marker}); not reachable"

    # The unit's own span.
    node = next((n for n in tree.body
                 if getattr(n, "name", None) == unit["name"]), None)
    if node is None:
        return 4, "unit not found on re-parse"
    span = "\n".join(lines[node.lineno - 1: getattr(node, "end_lineno", node.lineno)])

    hits = _markers(span)
    if not hits:
        return 1, "no I/O markers; directly callable"
    uncontrollable = [h for h in hits if not h[2]]
    if uncontrollable:
        group, marker, _ = uncontrollable[0]
        return 3, f"{group} I/O inside the unit ({marker}); needs a seam"
    group, marker, _ = hits[0]
    return 2, f"{group} I/O ({marker}); pin at a wider boundary with {group} controlled"
