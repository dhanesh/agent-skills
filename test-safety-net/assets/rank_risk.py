#!/usr/bin/env python3
"""Rank a repo's untested units by risk, and triage how testable each one is.

Stdlib + git only. No network, no third-party packages, no call-graph service:
the blast-radius half is a deliberate static approximation, labelled as such in
the output, so this runs anywhere the repo does.
"""
from __future__ import annotations

import ast
import collections
import functools
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
    """Local name -> canonical dotted path, from this MODULE's top-level
    import statements only (`tree.body`, not `ast.walk`).

        import socket                    -> {"socket": "socket"}
        import socket as s               -> {"s": "socket"}
        import os.path                   -> {"os": "os"}          (binds the root)
        from os import system            -> {"system": "os.system"}
        from os import system as run_cmd -> {"run_cmd": "os.system"}
        from requests import get         -> {"get": "requests.get"}

    Scoped to the module body ON PURPOSE: an `import X as name` INSIDE a
    function body must not rewrite what `name` means for the rest of the
    file. A prior version walked the whole tree with `ast.walk` and let an
    unrelated function's local `import collections.abc as requests` shadow
    a real, module-level `requests.get(...)` call elsewhere in the same
    file — turning genuinely dangerous code invisible. That was the most
    dangerous defect a review round found: a false Tier 1, not merely an
    imprecise one.
    """
    aliases = {}
    for node in tree.body:
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


def _local_alias_map(node, module_alias_map):
    """`module_alias_map`, overridden by any import found ANYWHERE inside
    `node`'s own subtree (including a nested closure within it).

    Recomputed per node so a function-local alias is visible WITHIN the
    function that defines and uses it (`import subprocess as sp` used two
    lines later in the same function is real, common code, and must resolve
    — this repo's own `scripts/ab-validate.py::check_audit_guardrails` does
    exactly this) while still never leaking to an unrelated sibling
    function (`_import_alias_map`'s module-only scope is what stops that).
    Each caller passes its own `node`, so the override never crosses a
    function boundary it doesn't already own.
    """
    local = dict(module_alias_map)
    for n in ast.walk(node):
        if isinstance(n, ast.Import):
            for alias in n.names:
                if alias.asname:
                    local[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    local[root] = root
        elif isinstance(n, ast.ImportFrom) and n.module:
            for alias in n.names:
                local[alias.asname or alias.name] = f"{n.module}.{alias.name}"
    return local


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
    given node (or list of nodes) — used for MARKER matching, which needs
    the full dotted path (`requests.get`), not just a bare method name.

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


def _callable_candidates(node_or_nodes, alias_map):
    """Bare same-module callable names a Call in this subtree might reach —
    used for CHASING into local functions/methods, not for marker matching.

    A `Name` call resolves through the alias map like `_call_targets` does.
    An `Attribute` call contributes its ATTRIBUTE NAME ALONE, regardless of
    whether the receiver resolves: `Client().fetch(u)`'s receiver is a
    freshly-constructed instance with no resolvable name at all, but the
    method name `fetch` is still visible — and that is what same-module
    method chasing keys on.
    """
    nodes = node_or_nodes if isinstance(node_or_nodes, list) else [node_or_nodes]
    names = []
    for root in nodes:
        if root is None:
            continue
        for n in ast.walk(root):
            if not isinstance(n, ast.Call):
                continue
            func = n.func
            if isinstance(func, ast.Name):
                names.append(alias_map.get(func.id, func.id))
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return names


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


def _is_main_guard(node):
    """True for `if __name__ == "__main__":` (either operand order).

    This is THE standard Python idiom written specifically so its body does
    NOT run at import time — it is the single most common top-level `if` in
    any runnable script. Recognizing it is a fixed, well-known syntactic
    special case, not general points-to analysis: without it, the module-
    taint check would flag nearly every script-shaped file in a typical repo
    (its `main()` call, and everything `main` transitively reaches) as
    import-time I/O, which is false and was observed to taint 128 of this
    repo's own 296 units before this check was added.
    """
    test = node.test
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)):
        return False
    operands = (test.left, test.comparators[0])
    names = {n.id for n in operands if isinstance(n, ast.Name)}
    consts = {c.value for c in operands if isinstance(c, ast.Constant)}
    return "__name__" in names and "__main__" in consts


def _module_level_regions(tree):
    """AST nodes that run at IMPORT time: everything at module level outside
    a def/class body, plus the parts of a def/class that Python evaluates at
    DEFINITION time rather than call time — decorator arguments, argument
    defaults, class BASES and KEYWORDS (`metaclass=...` lives in keywords;
    both execute at class-CREATION time, i.e. import time), and class-body
    statements (but not method bodies, which run only when called). The
    `if __name__ == "__main__":` guard is explicitly excluded — see
    `_is_main_guard`.
    """
    regions = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            regions.extend(node.decorator_list)
            regions.extend(node.args.defaults)
            regions.extend(d for d in node.args.kw_defaults if d is not None)
        elif isinstance(node, ast.ClassDef):
            regions.extend(node.decorator_list)
            regions.extend(node.bases)
            regions.extend(node.keywords)
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue                # a method body runs only when called
                regions.append(stmt)
        elif isinstance(node, ast.If) and _is_main_guard(node):
            continue                        # never runs when the module is imported
        else:
            regions.append(node)
    return regions


def _local_callables(tree):
    """Bare call-name -> [(qualified_name, node), ...] for every same-module
    callable that name might mean: a module-level function (qualified name
    is just its own name), or a method of a module-level class (qualified
    name is `ClassName.method`).

    Keyed by the BARE method name on purpose: an attribute call like
    `.fetch(...)` cannot know which class's `fetch` it means — the receiver
    may not even resolve (see `_callable_candidates`) — so every same-module
    callable with that name is a candidate, and the worst tier among them
    wins. Over-flagging when two classes happen to share a method name is
    accepted: declining to write a test for something safe costs nothing;
    missing a real socket does not.
    """
    callables = collections.defaultdict(list)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            callables[node.name].append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    callables[stmt.name].append((f"{node.name}.{stmt.name}", stmt))
    return dict(callables)


def _hits_from(own_targets, own_candidates, module_alias_map, local_funcs, visited):
    """Direct marker hits for `own_targets`, plus (recursively) hits from
    same-module callables any of `own_candidates` might reach — so a thin
    wrapper, a module-level `_boot()` call, or a `.fetch(...)` method
    dispatch all inherit the worst tier reachable from them.

    `module_alias_map` is the MODULE-level alias map; each callee's own
    scan uses `_local_alias_map(callee_node, module_alias_map)` so that
    callee's own local imports resolve within its own body without leaking
    anywhere else. `visited` (keyed by bare candidate name) guards
    mutual/self recursion. Each hit is `(group, marker, controllable, via)`
    — `via` is the qualified same-module callable name (`function`, or
    `Class.method`) the hit was reached through, or None when the marker
    sits directly in the scanned region.
    """
    hits = [(g, m, c, None) for g, m, c in _markers(own_targets)]
    for name in sorted(set(own_candidates)):
        if name in local_funcs and name not in visited:
            visited.add(name)
            for qualified, callee_node in local_funcs[name]:
                callee_alias_map = _local_alias_map(callee_node, module_alias_map)
                callee_targets = _call_targets(callee_node, callee_alias_map)
                callee_candidates = _callable_candidates(callee_node, callee_alias_map)
                for g, m, c, _via in _hits_from(callee_targets, callee_candidates,
                                                 module_alias_map, local_funcs, visited):
                    hits.append((g, m, c, qualified))
    return hits


_FileAnalysis = collections.namedtuple("_FileAnalysis", "tree alias_map local_funcs tier4")


@functools.lru_cache(maxsize=None)
def _analyze_file(root, rel):
    """Parse `rel` once and precompute everything `triage()` needs for every
    unit in it — the import-alias map, the same-module callable index, and
    whether the module itself is tainted at import time (checked
    TRANSITIVELY: `_STARTED = _boot()` inherits whatever `_boot` reaches,
    not just its own direct call target).

    Cached per (root, rel) for the life of the process: several units
    typically share a file, and re-parsing plus re-resolving aliases PER
    UNIT (rather than per file) was most of a `triage()` call's cost.
    """
    text = read_text(root, rel)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _FileAnalysis(None, None, None,
                              (4, "file does not parse; nothing in it can be pinned"))

    alias_map = _import_alias_map(tree)
    local_funcs = _local_callables(tree)
    regions = _module_level_regions(tree)
    mod_hits = _hits_from(_call_targets(regions, alias_map),
                           _callable_candidates(regions, alias_map),
                           alias_map, local_funcs, set())
    uncontrollable_mod = [h for h in mod_hits if not h[2]]
    tier4 = None
    if uncontrollable_mod:
        group, marker, _, via = uncontrollable_mod[0]
        if via:
            tier4 = (4, f"module does {group} I/O at import time via {via} "
                         f"({marker}); not reachable")
        else:
            tier4 = (4, f"module does {group} I/O at import time ({marker}); not reachable")
    return _FileAnalysis(tree, alias_map, local_funcs, tier4)


def triage(root: str, unit) -> tuple:
    """Classify how testable a unit is. Returns (tier, reason).

    A FILTER, not the sole enforcement. It resolves calls through this
    module's import-alias map, follows same-module function AND method
    calls transitively (so a thin wrapper, a `.fetch(...)` dispatch, or a
    module-level `_boot()` call all inherit the worst tier reachable from
    them), and treats class bases/keywords and argument defaults as
    import-time code. It is still a static approximation — Python
    reachability is undecidable from source alone, so it cannot see
    cross-module indirection, dynamic dispatch (`getattr`, `**kwargs`), or
    I/O reached only through a variable that happens to hold a function.

    The actual backstop against writing a test that performs real I/O is a
    RUNTIME guard elsewhere in this skill: the candidate test's red->green
    proof runs with the network/subprocess/DB entry points monkeypatched to
    raise, so a test that genuinely reaches real I/O fails loudly instead of
    passing, and its unit gets reclassified. `triage` exists to keep that
    guard from firing often — it narrows the field, it does not have to be
    airtight on its own. The SKILL.md permits promoting a unit after
    inspection — but only by recording the promotion, never silently.
    """
    analysis = _analyze_file(root, unit["path"])
    if analysis.tree is None:
        return analysis.tier4
    if analysis.tier4 is not None:
        return analysis.tier4

    # The unit's own node.
    node = next((n for n in analysis.tree.body
                 if getattr(n, "name", None) == unit["name"]), None)
    if node is None:
        return 4, "unit not found on re-parse"

    own_alias_map = _local_alias_map(node, analysis.alias_map)
    own_targets = _call_targets(node, own_alias_map)
    own_candidates = _callable_candidates(node, own_alias_map)
    hits = _hits_from(own_targets, own_candidates, analysis.alias_map,
                       analysis.local_funcs, {unit["name"]})

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
