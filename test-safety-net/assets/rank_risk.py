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
#
# Markers are DOTTED-PATH PREFIXES matched against a call's RESOLVED target
# (see `_resolve` / `_import_alias_map`), not raw substrings of the source
# text. That is what makes `import socket as s; s.create_connection(...)`
# visible as network I/O (a substring scan cannot see through the alias, and
# an alias-derived substring like "s." is not safe — it is also a substring
# of "os."), and what stops a module-level data literal that merely SPELLS
# OUT a marker string (this table itself, included) from reading as
# behaviour: a string literal contains no `ast.Call`, so it never resolves.
CONTROLLABLE = {
    "filesystem": ("open", "pathlib", "os.path", "os.remove", "os.mkdir",
                   "shutil", "tempfile"),
    "clock": ("datetime", "time.time", "time.sleep", "date.today"),
    "randomness": ("random", "uuid.uuid4", "secrets"),
    "environment": ("os.environ", "os.getenv"),
}
UNCONTROLLABLE = {
    "network": ("requests", "urllib.request", "httpx", "socket", "aiohttp",
                "boto3", "urlopen"),
    "database": ("psycopg2", "sqlite3.connect", "pymongo", "MongoClient",
                 "create_engine"),
    "subprocess": ("subprocess", "os.system", "os.popen"),
}


def _import_alias_map(tree):
    """Local name -> canonical dotted path, from every import in the module.

        import socket                    -> {"socket": "socket"}
        import socket as s               -> {"s": "socket"}
        import os.path                   -> {"os": "os"}          (binds the root)
        from os import system            -> {"system": "os.system"}
        from os import system as run_cmd -> {"run_cmd": "os.system"}
        from requests import get         -> {"get": "requests.get"}

    Walks the whole tree, not just the module body, so a function-local
    import is resolved the same way as a module-level one.
    """
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _resolve(node, alias_map):
    """Resolve a Name/Attribute AST node to a dotted canonical path, or None.

    `ast.Name(id=n)` resolves through the alias map (falling back to `n`
    itself for an unaliased or local name); `ast.Attribute(value=v, attr=a)`
    resolves recursively to `resolve(v) + "." + a`. Anything else (a call
    result, a subscript, ...) is unresolvable and returns None rather than
    guessing.
    """
    if isinstance(node, ast.Name):
        return alias_map.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _resolve(node.value, alias_map)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _call_targets(node_or_nodes, alias_map):
    """Resolved dotted call targets for every `ast.Call` reachable from the
    given node (or list of nodes).

    Only a Call's func matters here — a bare data literal that merely NAMES
    a marker string contains no Call node, so it contributes nothing.
    """
    nodes = node_or_nodes if isinstance(node_or_nodes, list) else [node_or_nodes]
    targets = []
    for root in nodes:
        if root is None:
            continue
        for n in ast.walk(root):
            if isinstance(n, ast.Call):
                resolved = _resolve(n.func, alias_map)
                if resolved is not None:
                    targets.append(resolved)
    return targets


def _markers(names):
    """(group, marker, controllable) for every I/O marker matched by any
    resolved call target in `names`. Deterministic order: UNCONTROLLABLE
    groups before CONTROLLABLE groups, each iterated in sorted-group /
    fixed-marker-tuple order — so two runs on the same input agree.

    A name matches a marker when it equals the marker exactly or starts with
    `marker + "."` (the marker is always a dotted-path prefix).
    """
    def _hit(marker):
        return any(n == marker or n.startswith(marker + ".") for n in names)

    hits = []
    for group in sorted(UNCONTROLLABLE):
        for m in UNCONTROLLABLE[group]:
            if _hit(m):
                hits.append((group, m, False))
    for group in sorted(CONTROLLABLE):
        for m in CONTROLLABLE[group]:
            if _hit(m):
                hits.append((group, m, True))
    return hits


def _module_level_targets(tree, alias_map):
    """Resolved call targets for whatever runs at IMPORT time: statements
    outside any def/class, plus the parts of a def/class that Python
    evaluates at DEFINITION time rather than call time — decorator
    arguments, argument defaults, and class-body statements (but not method
    bodies, which run only when called, not when the class is defined).
    """
    targets = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            targets.extend(_call_targets(node.decorator_list, alias_map))
            targets.extend(_call_targets(node.args.defaults, alias_map))
            targets.extend(_call_targets(
                [d for d in node.args.kw_defaults if d is not None], alias_map))
        elif isinstance(node, ast.ClassDef):
            targets.extend(_call_targets(node.decorator_list, alias_map))
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue                # a method body runs only when called
                targets.extend(_call_targets(stmt, alias_map))
        else:
            targets.extend(_call_targets(node, alias_map))
    return targets


def _transitive_hits(node, alias_map, local_funcs, visited):
    """Hits for `node`'s own body, plus (recursively) hits from same-module
    module-level functions it calls — so a thin wrapper over a Tier-3 helper
    reads as Tier 3, not Tier 1. Only same-module, name-resolved calls are
    followed; cross-module analysis is out of scope.

    `visited` guards mutual/self recursion. Each hit is
    `(group, marker, controllable, via)` — `via` names the same-module
    function the hit was reached through, or None when the marker sits
    directly in `node`'s own body.
    """
    targets = _call_targets(node, alias_map)
    hits = [(g, m, c, None) for g, m, c in _markers(targets)]
    for name in sorted(set(targets)):
        if name in local_funcs and name not in visited:
            visited.add(name)
            for g, m, c, _via in _transitive_hits(local_funcs[name], alias_map,
                                                    local_funcs, visited):
                hits.append((g, m, c, name))
    return hits


def triage(root: str, unit) -> tuple:
    """Classify how testable a unit is. Returns (tier, reason).

    Resolves calls through this module's import-alias map — so a renamed
    import (`import socket as s`) is still visible as network I/O — and
    follows same-module function calls transitively, so a thin wrapper over
    a Tier-3 helper is Tier 3 too. Still a static, CONSERVATIVE
    approximation, not execution: it cannot see cross-module indirection,
    dynamic dispatch (`getattr`, `**kwargs`), or I/O reached only through a
    variable that happens to hold a function. The SKILL.md permits promoting
    a unit after inspection — but only by recording the promotion, never
    silently.
    """
    text = read_text(root, unit["path"])
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 4, "file does not parse; nothing in it can be pinned"

    alias_map = _import_alias_map(tree)

    # Tier 4 first: if importing the module does I/O, no unit in it is reachable.
    mod_hits = _markers(_module_level_targets(tree, alias_map))
    uncontrollable_mod = [h for h in mod_hits if not h[2]]
    if uncontrollable_mod:
        group, marker, _ = uncontrollable_mod[0]
        return 4, f"module does {group} I/O at import time ({marker}); not reachable"

    # The unit's own node.
    node = next((n for n in tree.body
                 if getattr(n, "name", None) == unit["name"]), None)
    if node is None:
        return 4, "unit not found on re-parse"

    local_funcs = {n.name: n for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    hits = _transitive_hits(node, alias_map, local_funcs, {unit["name"]})

    if not hits:
        return 1, "no I/O markers; directly callable"
    uncontrollable = [h for h in hits if not h[2]]
    if uncontrollable:
        group, marker, _, via = uncontrollable[0]
        if via:
            return 3, f"{group} I/O via {via} ({marker}); needs a seam"
        return 3, f"{group} I/O inside the unit ({marker}); needs a seam"
    group, marker, _, via = hits[0]
    if via:
        return 2, (f"{group} I/O via {via} ({marker}); "
                    f"pin at a wider boundary with {group} controlled")
    return 2, f"{group} I/O ({marker}); pin at a wider boundary with {group} controlled"
