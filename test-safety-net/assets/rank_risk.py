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
    """`module_alias_map`, overridden by any import found directly within
    `node`'s OWN scope — walking `node`'s body but NEVER descending into a
    nested `FunctionDef`/`AsyncFunctionDef`/`ClassDef`, which is a scope of
    its own with its own local imports.

    Recomputed per node (a unit's own node, or a specific callee node
    during transitive chasing) so a local alias is visible within the exact
    scope that defines and uses it: `import subprocess as sp` used two
    lines later in the SAME function resolves (this repo's own
    `scripts/ab-validate.py::check_audit_guardrails` does exactly this) —
    but a bug one boundary deeper than that fix stayed for a review round:
    a blanket `ast.walk(node)` let a NESTED def's local import leak OUTWARD
    to the function that contains it (`import os as X` inside a nested
    `def inner():` inside `outer` was overwriting `outer`'s own, correct,
    module-level `X`), and — when `node` is a whole class scanned as one
    unit — let one METHOD's local import leak SIDEWAYS into a sibling
    method the same way. Both were real false-Tier-1s. Stopping at every
    nested def/class boundary (not just the outermost one) fixes both: a
    method's own local imports still resolve within that method itself
    (recursion into it starts fresh, with `node` as ITS OWN root), just
    never anywhere else.
    """
    local = dict(module_alias_map)

    def visit(n, is_root):
        if not is_root and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return                          # a scope of its own — do not descend
        if isinstance(n, ast.Import):
            for alias in n.names:
                if alias.asname:
                    local[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    local[root] = root
            return
        if isinstance(n, ast.ImportFrom) and n.module:
            for alias in n.names:
                local[alias.asname or alias.name] = f"{n.module}.{alias.name}"
            return
        for child in ast.iter_child_nodes(n):
            visit(child, False)

    visit(node, True)
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


def _callable_candidates(node_or_nodes, alias_map, local_classes, enclosing_class=None):
    """QUALIFIED same-module callable names a Call in this subtree might
    reach — used for CHASING into local functions/methods, not for marker
    matching.

    A `Name` call resolves through the alias map like `_call_targets` does
    (a plain top-level function's qualified name is just its own name). An
    `Attribute` call is chased ONLY when the receiver is plausibly a
    same-module class instance — NOT on a bare `.attr` regardless of
    receiver, which is what an earlier version did and which review found
    over-broad: `.get`/`.run`/`.read`/`.write`/`.close`/`.open`/`.connect`
    are everywhere, so a bare-name chase silently declines to test *any*
    method sharing a name with a same-module I/O helper, project-wide —
    `cfg.get('key')` on a plain dict must not chase into an unrelated
    module-level `get()`. Two receiver shapes ARE trusted, both requiring
    no variable-type tracking to recognize:
        `Client().fetch(u)` — receiver is a Call to a Name naming a
            module-level ClassDef — chased as `"Client.fetch"`.
        `self.fetch(u)`     — receiver is `self`, and this call sits
            inside a method — chased as `f"{enclosing_class}.fetch"`.
    Any other receiver (a plain variable, an attribute chain, a subscript,
    …) is left unresolved. That is the points-to-analysis boundary: seeing
    through it would require tracking what a variable actually holds, which
    is out of scope — the runtime guard is the backstop for what falls
    outside it.
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
                receiver = func.value
                if (isinstance(receiver, ast.Call)
                        and isinstance(receiver.func, ast.Name)
                        and receiver.func.id in local_classes):
                    names.append(f"{receiver.func.id}.{func.attr}")
                elif (enclosing_class is not None
                        and isinstance(receiver, ast.Name) and receiver.id == "self"):
                    names.append(f"{enclosing_class}.{func.attr}")
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
    """QUALIFIED name -> node for every same-module callable: a module-level
    function (qualified name is its own name), or a method of a module-level
    class (qualified name is `ClassName.method`).

    A flat, precisely-qualified index on purpose. `_callable_candidates`
    already resolves a method call to the SPECIFIC class it names — either
    `Client()` naming `Client` directly, or `self` inside a known enclosing
    class — so there is no longer a need to fan out to every same-named
    method across every class the way an earlier, bare-name-keyed version
    did (that over-flagging is exactly what `_callable_candidates`'s
    narrower receiver check now prevents upstream).
    """
    callables = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            callables[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    callables[f"{node.name}.{stmt.name}"] = stmt
    return callables


def _local_classes(tree):
    """Names of every module-level `ClassDef` — used to recognize the
    `Client()` receiver shape in `_callable_candidates` without any
    variable-type tracking: a Call to a bare Name that names one of these
    is trusted to be a fresh instance of a same-module class.
    """
    return {n.name for n in tree.body if isinstance(n, ast.ClassDef)}


def _hits_from(own_targets, own_candidates, module_alias_map, local_funcs,
                local_classes, visited):
    """Direct marker hits for `own_targets`, plus (recursively) hits from
    same-module callables any of `own_candidates` might reach — so a thin
    wrapper, a module-level `_boot()` call, or a `.fetch(...)` method
    dispatch all inherit the worst tier reachable from them.

    `own_candidates` are already QUALIFIED names (`_callable_candidates`
    resolves a method call to the specific class it names), so lookup
    against `local_funcs` is a direct key match — no more fan-out across
    every same-named method. `module_alias_map` is the MODULE-level alias
    map; each callee's own scan uses `_local_alias_map(callee_node,
    module_alias_map)` so that callee's own local imports resolve within
    its own body without leaking anywhere else, and — when the callee is a
    method — `_callable_candidates` is given its own class as
    `enclosing_class` so a `self.other_method(...)` inside it resolves to
    that SAME class, not any class with a same-named method. `visited`
    (keyed by qualified name) guards mutual/self recursion. Each hit is
    `(group, marker, controllable, via)` — `via` is the qualified
    same-module callable name (`function`, or `Class.method`) the hit was
    reached through, or None when the marker sits directly in the scanned
    region.
    """
    hits = [(g, m, c, None) for g, m, c in _markers(own_targets)]
    for qualified in sorted(set(own_candidates)):
        if qualified in local_funcs and qualified not in visited:
            visited.add(qualified)
            callee_node = local_funcs[qualified]
            callee_class = qualified.rsplit(".", 1)[0] if "." in qualified else None
            callee_alias_map = _local_alias_map(callee_node, module_alias_map)
            callee_targets = _call_targets(callee_node, callee_alias_map)
            callee_candidates = _callable_candidates(callee_node, callee_alias_map,
                                                       local_classes, callee_class)
            for g, m, c, _via in _hits_from(callee_targets, callee_candidates,
                                             module_alias_map, local_funcs,
                                             local_classes, visited):
                hits.append((g, m, c, qualified))
    return hits


_FileAnalysis = collections.namedtuple(
    "_FileAnalysis", "tree alias_map local_funcs local_classes tier4")


@functools.lru_cache(maxsize=None)
def _analyze_file(root, rel):
    """Parse `rel` once and precompute everything `triage()` needs for every
    unit in it — the import-alias map, the same-module callable index, the
    set of module-level class names, and whether the module itself is
    tainted at import time (checked TRANSITIVELY: `_STARTED = _boot()`
    inherits whatever `_boot` reaches, not just its own direct call target).

    Cached per (root, rel) for the life of the process: several units
    typically share a file, and re-parsing plus re-resolving aliases PER
    UNIT (rather than per file) was most of a `triage()` call's cost.
    """
    text = read_text(root, rel)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _FileAnalysis(None, None, None, None,
                              (4, "file does not parse; nothing in it can be pinned"))

    alias_map = _import_alias_map(tree)
    local_funcs = _local_callables(tree)
    local_classes = _local_classes(tree)
    regions = _module_level_regions(tree)
    mod_hits = _hits_from(_call_targets(regions, alias_map),
                           _callable_candidates(regions, alias_map, local_classes, None),
                           alias_map, local_funcs, local_classes, set())
    uncontrollable_mod = [h for h in mod_hits if not h[2]]
    tier4 = None
    if uncontrollable_mod:
        group, marker, _, via = uncontrollable_mod[0]
        if via:
            tier4 = (4, f"module does {group} I/O at import time via {via} "
                         f"({marker}); not reachable")
        else:
            tier4 = (4, f"module does {group} I/O at import time ({marker}); not reachable")
    return _FileAnalysis(tree, alias_map, local_funcs, local_classes, tier4)


def triage(root: str, unit) -> tuple:
    """Classify how testable a unit is. Returns (tier, reason).

    This is a FILTER, not the enforcement. It ranks candidates and declines
    the obvious hazards: it resolves calls through this module's
    import-alias map (scoped per lexical scope, so a local import neither
    leaks outward to an enclosing function nor sideways to a sibling
    function or method), follows same-module function and method calls
    transitively (a thin wrapper, a `.fetch(...)` dispatch through a
    same-module class, or a module-level `_boot()` call all inherit the
    worst tier reachable from them), and treats class bases/keywords and
    argument defaults as import-time code.

    The invariant "never writes a test that performs real I/O" is NOT
    enforced here — it CANNOT be, because static reachability in Python is
    undecidable from source alone. It is enforced at RUNTIME, during the
    proof: the candidate test's red->green run executes with the
    network/subprocess/DB entry points monkeypatched to raise, so a test
    that genuinely reaches real I/O fails loudly instead of passing, and its
    unit gets reclassified rather than netted. `triage` exists to keep that
    guard from firing often, not to replace it — which is why it does not
    and cannot attempt to see through dynamic dispatch (`getattr`,
    `**kwargs`), an unresolvable receiver (`cfg.get(...)` where `cfg` could
    be anything), or cross-module indirection: all three are the
    points-to-analysis boundary this filter stops at on purpose. The
    SKILL.md permits promoting a unit after inspection — but only by
    recording the promotion, never silently.
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

    enclosing_class = unit["name"] if unit["kind"] == "class" else None
    own_alias_map = _local_alias_map(node, analysis.alias_map)
    own_targets = _call_targets(node, own_alias_map)
    own_candidates = _callable_candidates(node, own_alias_map,
                                           analysis.local_classes, enclosing_class)
    hits = _hits_from(own_targets, own_candidates, analysis.alias_map,
                       analysis.local_funcs, analysis.local_classes, {unit["name"]})

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
