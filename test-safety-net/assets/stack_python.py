#!/usr/bin/env python3
"""The Python stack: discovery and testability triage, behind the stack interface.

Everything here is language-specific and everything language-specific is here.
The stack-agnostic half -- churn, reference counting, coverage detection,
scoring, ranking, the CLI, the JSON shape -- stays in `rank_risk.py` and is
shared by every stack; the interface those two meet at is documented there.

Python is the only stack whose discovery is ALWAYS precise: `ast` ships in the
stdlib, so there is no toolchain to be missing and no heuristic path to
degrade to (multistack design, D1). Every other stack has both.

`triage` here is a FILTER, never the enforcement. The invariant "never writes a
test that performs real I/O" is enforced at runtime by `io_guard.py` during the
red->green proof. See `triage`'s own docstring, which records what four review
rounds established about that split.

Everything below moved out of `rank_risk.py` unchanged.
"""
from __future__ import annotations

import ast
import collections
import functools
import os
import re
import sys

# The shipped assets are a flat directory, not a package, and callers load
# them by path from directories that are not this one. Make the siblings
# importable by name before importing them.
_ASSETS = os.path.dirname(os.path.abspath(__file__))
if _ASSETS not in sys.path:
    sys.path.insert(0, _ASSETS)

from stack_common import (SKIP_DIRS, evidence_score, has_manifest,   # noqa: E402,F401
                          read_text)

STACK_NAME = "python"

# Files that declare "this directory is a Python project". `setup.py` is a
# source file too and so is counted twice; that is right -- it is both.
MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
             "Pipfile", "environment.yml")


def evidence(root: str) -> int:
    """How much of `root` is Python: non-test `.py` files, scaled by a manifest.

    The whole rule lives in `stack_common.evidence_score`, so the two stacks
    cannot drift into weighing the same evidence differently -- a detector
    whose stacks disagree about what a manifest is worth is not comparing
    scores, it is comparing scales.

    Python is also `rank_risk.detect_stack`'s fallback, so a score of 0 here
    never means "this repo cannot be ranked".
    """
    return evidence_score(sum(1 for _rel in iter_source_files(root)),
                          root, MANIFESTS)


def is_test_path(rel: str) -> bool:
    """True for a path pytest/unittest would collect rather than a unit's home."""
    base = os.path.basename(rel)
    parts = rel.replace(os.sep, "/").split("/")
    return (base.startswith("test_") or base.endswith("_test.py")
            or "tests" in parts or "test" in parts)


def iter_source_files(root: str, include_tests: bool = False):
    """Yield repo-relative paths of .py files, skipping vendor dirs. Sorted."""
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
            if not include_tests and is_test_path(rel):
                continue
            out.append(rel)
    return sorted(out)


def is_test_for(test_rel: str, src_rel: str) -> bool:
    """True when `test_rel` is positioned to be a test OF `src_rel`.

    Python's idiom is a single one: the test file sits BESIDE the module it
    imports (`assets/test_harvest.py` next to `assets/harvest.py`), which is
    what makes `sys.path.insert(0, dirname(__file__)); import harvest`
    resolve. So same directory is the whole rule, and it is exactly the rule
    `already_covered` applied inline before this became an interface name.

    POSITION ONLY, never proof. This says the file is placed where a test of
    `src_rel` would be placed; whether it actually exercises a given unit is
    `reached_through_module`'s question, and `already_covered` asks both.
    Stacks whose tests live in a sibling directory (`__tests__/`) or a
    mirrored tree (`test/` against `src/`) widen this predicate; none of that
    is expressible through `is_test_path`, which says only WHETHER a path is
    a test, never WHAT it tests.
    """
    return os.path.dirname(test_rel) == os.path.dirname(src_rel)


# ── Naming ───────────────────────────────────────────────────────────────

def module_of(rel: str) -> str:
    """The module identity `rel` defines: its basename without `.py`.

    This is the key `inbound_refs` and `already_covered` both group by, so it
    must be the name OTHER files would use to talk about `rel`. For Python
    that is the basename; a stack whose specifiers are extensionless, path
    relative, or where a directory index file stands for its directory
    answers differently, which is why the core asks rather than computes it.
    """
    return os.path.splitext(os.path.basename(rel))[0]


def name_pattern(name: str):
    r"""A regex matching `name` as a whole identifier, for either call site.

    The core needs this for two questions -- "does this test file mention the
    unit's NAME" (`rank_risk.already_covered`) and "does this file plausibly
    name that MODULE" (`rank_risk._references_module`) -- and both were built
    inline as `re.compile(r"\b%s\b" % re.escape(...))` until a stack whose
    identifiers are not Python's arrived. `\b` is defined against
    `[A-Za-z0-9_]`, so it does not know `$` is an identifier character in JS:
    `re.search(r"\b\$fetch\b", "$fetch(1)")` is False, and a `$`-named
    export would read as uncovered in every test file forever. That is the
    silent zero this file exists to avoid, so the pattern is the stack's to
    build.

    Python's answer is `\b%s\b`, byte-for-byte what the core built before.
    """
    return re.compile(r"\b%s\b" % re.escape(name))


def path_pattern(rel: str):
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


# ── Lexing ───────────────────────────────────────────────────────────────

IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*")


def preceding_qualifier(text: str, start: int):
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


# ── Import/binding grammar ───────────────────────────────────────────────

def module_bindings(module: str, text: str, *, src_rel: str, ref_rel: str) -> tuple:
    """(aliases, names) the file binds for `module` by an actual import.

    `src_rel` is the file that DEFINES the module; `ref_rel` is the file
    `text` was read from. Python needs neither: an `import` statement names a
    module by exactly the bare token `module_of` returns, so the token is the
    whole question and this implementation ignores both paths. They are in the
    signature for the stacks whose import statements name a PATH instead --
    node's `from "../utils"` is only resolvable against the referencing file's
    own directory, and a stack that could not see it would be reduced to
    comparing a specifier's last segment, which cannot tell `../utils` from
    any other `utils` in the repo. Passing them is therefore the core's job,
    not a stack's to reconstruct.

    `aliases` are the names the module object itself is bound to
    (`import harvest` -> "harvest"; `import harvest as H` -> "H"); `names` are
    the members pulled out of it (`from harvest import apply_markers`).

    This is `already_covered`'s SAME-DIRECTORY evidence, and it is deliberately
    stronger than the bare whole-identifier match the core uses elsewhere
    (`rank_risk._references_module`).
    A test file beside `harvest.py` that says `import harvest` and also
    contains the token `main` — because it ends with `unittest.main()`, or
    calls a DIFFERENT module's `main` through an alias — must not credit
    `harvest.py::main`, which has no test at all. Measured on this repo: the
    weaker form credited 6 units, 2 of them (`::main` twice) false; this form
    credits exactly the 4 genuine ones.

    What that buys is NARROWER evidence, not exact evidence, and the
    difference matters in the over-credit direction. Two things it does rule
    out, both measured: a bare token that the module never supplies, and — via
    `reached_through_module`'s call-site requirement — a `mock.patch(
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


def reached_through_module(module: str, name: str, text: str, *,
                           src_rel: str, ref_rel: str) -> bool:
    """True when `text` CALLS `name` through an import of `module`.

    `src_rel` (the defining file) and `ref_rel` (the file `text` came from)
    are ignored here for the reason `module_bindings` records: Python's import
    statements name the bare module token, so the paths add nothing. A stack
    with path specifiers resolves them against `ref_rel` and compares to
    `src_rel`.

    The call site is the point (fix round 7). Matching the chain `module.name`
    alone credited `mock.patch("harvest.collect")` — evidence that the unit was
    replaced by a stub, i.e. the opposite of coverage — and a `# harvest.collect`
    comment, so under a basename collision a unit with no test at all could
    read as covered. Requiring `(` after the name costs nothing measured (126
    credited units on this repo, byte-identical evidence files, before and
    after) and closes both.
    """
    aliases, names = module_bindings(module, text, src_rel=src_rel, ref_rel=ref_rel)
    called = re.compile(r"(?<![A-Za-z0-9_.])%s[ \t]*\(" % re.escape(name))
    if name in names and called.search(text):
        return True
    return any(re.search(r"(?<![A-Za-z0-9_.])%s[ \t]*\.[ \t]*%s[ \t]*\("
                         % (re.escape(alias), re.escape(name)), text)
               for alias in aliases)


def discover_units(root: str):
    """Interface entry point: `(units, discovery_path)`.

    The label is always "precise" for Python -- `ast` is stdlib, so discovery
    can never silently degrade to a heuristic the way a stack that shells out
    to an absent toolchain can. Callers compare runs by it (multistack design,
    D1), so it is reported rather than assumed.
    """
    return _discover_units(root), "precise"


def _discover_units(root: str):
    """Module-level defs and classes in non-test .py files, sorted by id.

    Only module-level names: a nested def is not independently addressable by a
    test runner, so it cannot be pinned on its own. `_`-prefixed names are
    private by convention and are exercised through their public callers.
    """
    units = []
    for rel in iter_source_files(root):
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
    # Raw file-descriptor access sits UNDERNEATH the builtin `open` — `os.open`
    # is not `open`, and a runtime guard patching the builtin never sees it —
    # but it is still CONTROLLABLE: a test can point an fd at a temp dir the
    # same way it points `open` at one.
    #
    # THE WHOLE FAMILY, not a sample of it (fix round 5, finding C3). An
    # earlier table listed `os.remove` and `os.mkdir` but not `os.rename`;
    # `os.open`/`read`/`write` but not `os.listdir`/`scandir`/`walk`/`stat`.
    # So `os.listdir(p)` — a real read that fails against a missing path —
    # classified Tier 1 "no I/O markers; directly callable" while the
    # equally-mutating `os.remove` classified Tier 2. That is the same
    # enumeration hole C2 found in the exec/spawn family, in the family the
    # runtime guard has to mirror: `assets/io_guard.py` intercepts what this
    # table names, so a name missing here is a name missing from BOTH layers.
    # Two derived tests now pin it (see `test_rank_risk.py`): one derives the
    # path-taking primitives from CPython's own `os.supports_*` sets, one
    # derives the pure-Python wrappers from `os.py` itself, and both fail if
    # the table falls behind.
    "filesystem": ("open", "io.open", "io.open_code", "codecs.open",
                   "pathlib", "os.path", "glob", "fileinput",
                   "shutil", "tempfile", "io.FileIO", "mmap.mmap",
                   # `pkgutil.get_data` reads a real file through the LOADER
                   # (`SourceFileLoader.get_data` -> `_io.open_code`), naming
                   # neither `open` nor any `os` primitive. Round 6 found it
                   # missing from this table AND unpatched by the guard -- the
                   # same both-layers miss as C2 and C3 -- so the two go in
                   # together: `io_guard.py` now patches `_io` as well.
                   "pkgutil",
                   # fd-level data movement
                   "os.open", "os.write", "os.read", "os.close", "os.fdopen",
                   "os.pread", "os.pwrite", "os.preadv", "os.pwritev",
                   "os.readv", "os.writev", "os.sendfile",
                   "os.fsync", "os.fdatasync", "os.ftruncate", "os.truncate",
                   # directory + metadata reads
                   "os.listdir", "os.scandir", "os.walk", "os.fwalk",
                   "os.stat", "os.lstat", "os.fstat", "os.statvfs",
                   "os.fstatvfs", "os.access", "os.pathconf", "os.fpathconf",
                   "os.readlink",
                   # mutation
                   "os.remove", "os.unlink", "os.rename", "os.renames",
                   "os.replace", "os.mkdir", "os.makedirs", "os.rmdir",
                   "os.removedirs", "os.link", "os.symlink", "os.mkfifo",
                   "os.mknod", "os.chdir", "os.fchdir", "os.chroot",
                   "os.chmod", "os.fchmod", "os.lchmod",
                   "os.chown", "os.fchown", "os.lchown",
                   "os.chflags", "os.lchflags", "os.utime",
                   "os.getxattr", "os.setxattr", "os.listxattr",
                   "os.removexattr"),
    "clock": ("datetime", "time.time", "time.sleep", "date.today"),
    "randomness": ("random", "uuid.uuid4", "secrets", "os.urandom"),
    "environment": ("os.environ", "os.getenv", "os.getenvb", "os.putenv",
                    "os.reload_environ", "os.unsetenv"),
}
UNCONTROLLABLE = {
    "network": ("requests", "urllib.request", "httpx", "socket", "aiohttp",
                "boto3", "urlopen"),
    "database": ("psycopg2", "sqlite3.connect", "pymongo", "MongoClient",
                 "create_engine"),
    # spawn/exec/fork bypass `subprocess.Popen` entirely, so a guard patching
    # `subprocess` never sees them — and `os.exec*` REPLACES THE PROCESS IMAGE,
    # taking every in-process monkeypatch with it, so no runtime guard can
    # survive one at all. These are the calls the filter must decline
    # statically because the guard structurally cannot reach them. Declined
    # (tier 3, not 2): no seam makes spawning a process safe to pin.
    # The WHOLE family, not a sample of it. An earlier table listed 11 of
    # these and missed 11 more, so `os.execlp`, `os.posix_spawnp` and
    # `os.spawnlp` read as Tier 1 "directly callable" while the near-identical
    # `os.execv` read as Tier 3. Every name `dir(os)` exposes under the
    # exec/spawn/fork families is listed, and a test DERIVES that set at
    # runtime and fails if the table falls behind again.
    "subprocess": ("subprocess", "os.system", "os.popen", "os.startfile",
                   "os.posix_spawn", "os.posix_spawnp",
                   "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
                   "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe",
                   "os.execl", "os.execle", "os.execlp", "os.execlpe",
                   "os.execv", "os.execve", "os.execvp", "os.execvpe",
                   "os.fork", "os.forkpty"),
}

# Call targets that are PURE COMPUTATION even though they sit under a marker
# prefix. Used ONLY when deciding whether a module does I/O at IMPORT time
# (`_analyze_file`), never when tiering a unit — inside a function body
# `os.path.join` still marks the unit as filesystem-adjacent Tier 2, which is
# the pre-existing, tested behaviour.
#
# Why the exception exists: `HERE = os.path.dirname(os.path.abspath(__file__))`
# is the commonest module-level statement there is, and it touches nothing —
# it is string algebra over `__file__`. Flooring on it moved 138 of this
# repo's own 305 units out of the net, which is the same over-flagging
# `_is_main_guard` exists to prevent. Inertness is judged per CALL, not per
# marker, so `os.path.exists` (a real stat) still floors while `os.path.join`
# does not.
IMPORT_TIME_INERT = ("os.path.join", "os.path.dirname", "os.path.basename",
                     "os.path.abspath", "os.path.normpath", "os.path.split",
                     "os.path.splitext", "os.path.relpath")


def _drop_inert(targets):
    """`targets` minus the calls that perform no I/O at import time."""
    return [t for t in targets
            if not any(t == i or t.startswith(i + ".") for i in IMPORT_TIME_INERT)]


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


def _callable_candidates(node_or_nodes, alias_map, local_classes, local_methods,
                          enclosing_class=None):
    """QUALIFIED same-module callable names a Call in this subtree might
    reach — used for CHASING into local functions/methods, not for marker
    matching.

    A `Name` call resolves through the alias map like `_call_targets` does
    (a plain top-level function's qualified name is just its own name). An
    `Attribute` call is chased ONLY when the receiver is plausibly a
    same-module class instance — NOT on a bare `.attr` regardless of
    receiver, which is what an earlier version did and which review found
    over-broad: `.get`/`.run`/`.read`/`.write`/`.close`/`.open`/`.connect`
    are everywhere, so a bare-name chase over EVERY same-module callable
    silently declines to test *any* method sharing a name with a
    same-module I/O helper, project-wide — `cfg.get('key')` on a plain dict
    must not chase into an unrelated module-level `get()`. Four receiver
    shapes ARE trusted, none of which needs variable-type tracking:
        `Client().fetch(u)` — receiver is a Call to a Name naming a
            module-level ClassDef — chased as `"Client.fetch"`.
        `Client.fetch(u)`   — receiver is a bare Name that IS a
            module-level ClassDef (static/classmethod dispatch, or an
            explicit unbound call) — chased as `"Client.fetch"`.
        `self.fetch(u)`     — receiver is `self`, and this call sits
            inside a method — chased as `f"{enclosing_class}.fetch"`.
        `w.fetch(u)`        — receiver is anything else, but `fetch` is a
            method of EXACTLY ONE module-level class in this file, so the
            resolution is unambiguous — chased as that one class's
            `"Client.fetch"`.

    That last shape is what makes the filter see the overwhelmingly common
    "instance held in a variable" case: a plain parameter
    (`def cmd(wm, a): wm.build(...)`, this repo's own world_model.py CLI
    dispatchers) or a module-level singleton (`_c = Client()` then
    `_c.fetch(u)`). Restricting it to CLASS METHODS — never module-level
    functions — and only when the name resolves to a single class is what
    keeps it from re-introducing the `cfg.get('key')` collision: a bare
    `.get` chases nothing unless some module-level class actually defines a
    `get` method, and nothing at all if two of them do.

    Ambiguity is deliberately NOT resolved by fanning out to every matching
    class: that is the over-flagging an earlier bare-name version produced.
    Two classes defining `fetch` means the receiver's type genuinely
    matters, which is points-to analysis — out of scope. That, and any
    receiver whose attribute matches no module-level class method at all,
    is where this filter stops on purpose; the runtime guard is the
    backstop for what falls outside it.
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
                elif isinstance(receiver, ast.Name) and receiver.id in local_classes:
                    names.append(f"{receiver.id}.{func.attr}")
                elif (enclosing_class is not None
                        and isinstance(receiver, ast.Name) and receiver.id == "self"):
                    names.append(f"{enclosing_class}.{func.attr}")
                elif func.attr in local_methods:
                    names.append(local_methods[func.attr])
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


def _unambiguous_methods(tree):
    """Bare method name -> the single `"Class.method"` that defines it, for
    every method name owned by EXACTLY ONE module-level class in this file.

    This is what lets the filter chase `w.fetch(u)` where `w` is a plain
    parameter, or `_c.fetch(u)` where `_c = Client()` is a module-level
    singleton — the two shapes that carry an instance in an ordinary
    variable, and between them most real method dispatch. Neither receiver
    is statically typed, but if `fetch` is defined by one class and one
    class only, there is nothing to disambiguate: the resolution is forced.

    Two rules keep this from becoming the over-broad bare-name chase a
    review round removed:
      * CLASS METHODS ONLY. A module-level `def get(...)` is never a
        candidate for a bare `.get`, so `cfg.get('key')` on a plain dict
        stays unchased (and Tier 1) even when the file happens to define a
        module-level `get()` that shells out.
      * ONE OWNER ONLY. If two module-level classes both define `fetch`,
        the name is dropped entirely rather than fanned out to both —
        picking between them needs the receiver's type, which is the
        points-to analysis this filter does not do.
    """
    owners = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owners.setdefault(stmt.name, set()).add(node.name)
    return {name: f"{next(iter(classes))}.{name}"
            for name, classes in sorted(owners.items()) if len(classes) == 1}


def _hits_from(own_targets, own_candidates, module_alias_map, local_funcs,
                local_classes, local_methods, visited, drop_inert=False):
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

    `drop_inert` is set only by the import-time scan: it strips the calls that
    are pure computation despite matching a marker prefix (see
    `IMPORT_TIME_INERT`), at every level of the chase, so a module-level
    `_HERE = _locate()` whose helper only joins paths does not read as
    import-time I/O.
    """
    if drop_inert:
        own_targets = _drop_inert(own_targets)
    hits = [(g, m, c, None) for g, m, c in _markers(own_targets)]
    for qualified in sorted(set(own_candidates)):
        if qualified in local_funcs and qualified not in visited:
            visited.add(qualified)
            callee_node = local_funcs[qualified]
            callee_class = qualified.rsplit(".", 1)[0] if "." in qualified else None
            callee_alias_map = _local_alias_map(callee_node, module_alias_map)
            callee_targets = _call_targets(callee_node, callee_alias_map)
            callee_candidates = _callable_candidates(callee_node, callee_alias_map,
                                                       local_classes, local_methods,
                                                       callee_class)
            for g, m, c, _via in _hits_from(callee_targets, callee_candidates,
                                             module_alias_map, local_funcs,
                                             local_classes, local_methods, visited,
                                             drop_inert):
                hits.append((g, m, c, qualified))
    return hits


_FileAnalysis = collections.namedtuple(
    "_FileAnalysis",
    "tree alias_map local_funcs local_classes local_methods tier4 import_floor")


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
        return _FileAnalysis(None, None, None, None, None,
                              (4, "file does not parse; nothing in it can be pinned"),
                              None)

    alias_map = _import_alias_map(tree)
    local_funcs = _local_callables(tree)
    local_classes = _local_classes(tree)
    local_methods = _unambiguous_methods(tree)
    regions = _module_level_regions(tree)
    mod_hits = _hits_from(_call_targets(regions, alias_map),
                           _callable_candidates(regions, alias_map, local_classes,
                                                 local_methods, None),
                           alias_map, local_funcs, local_classes, local_methods, set(),
                           drop_inert=True)
    uncontrollable_mod = [h for h in mod_hits if not h[2]]
    tier4 = None
    if uncontrollable_mod:
        group, marker, _, via = uncontrollable_mod[0]
        if via:
            tier4 = (4, f"module does {group} I/O at import time via {via} "
                         f"({marker}); not reachable")
        else:
            tier4 = (4, f"module does {group} I/O at import time ({marker}); not reachable")
    # Import-time CONTROLLABLE I/O floors every unit in the file at Tier 3.
    # `_CFG = json.load(open("/etc/app/config.json"))` was reported Tier 1
    # "directly callable", which is wrong twice: importing the module performs
    # real file I/O, so the harness takes an IOError on `import cfg` before a
    # single test body runs, and the routine Tier 2 controls (a temp dir, a
    # frozen clock) live in FIXTURES, which the spec notes run strictly AFTER
    # the module under test is imported. A boundary already crossed at import
    # cannot be controlled from one -- so per the spec's tier table this is
    # "no honest boundary without adding code": Tier 3, needs a seam (make the
    # load lazy). It is also the tier the runtime guard reclassifies to when it
    # trips at import, so filter and guard now agree instead of disagreeing.
    controllable_mod = [h for h in mod_hits if h[2]]
    import_floor = None
    if controllable_mod:
        group, marker, _, via = controllable_mod[0]
        via_txt = f" via {via}" if via else ""
        import_floor = (3, f"module does {group} I/O at import time{via_txt} "
                            f"({marker}); a fixture runs too late to control it — "
                            f"needs a seam")
    return _FileAnalysis(tree, alias_map, local_funcs, local_classes,
                          local_methods, tier4, import_floor)


def triage(root: str, unit) -> tuple:
    """Classify how testable a unit is. Returns (tier, reason).

    This is a FILTER, not the enforcement. It ranks candidates and declines
    the obvious hazards: it resolves calls through this module's
    import-alias map (scoped per lexical scope, so a local import neither
    leaks outward to an enclosing function nor sideways to a sibling
    function or method), follows same-module function and method calls
    transitively (a thin wrapper, a `.fetch(...)` dispatch through a
    same-module class — whether the receiver is a fresh `Client()`, `self`,
    the class name itself, or an ordinary variable whose method name only
    one module-level class defines — or a module-level `_boot()` call all
    inherit the worst tier reachable from them), and treats class
    bases/keywords and argument defaults as import-time code.

    The invariant "never writes a test that performs real I/O" is NOT
    enforced here — it CANNOT be, because static reachability in Python is
    undecidable from source alone. It is enforced at RUNTIME, during the
    proof, by the guard the spec specifies:

      * TIER-AWARE, not one blanket block. A Tier 1 candidate claims to touch
        nothing, so ALL SEVEN groups are blocked — the four controllable ones
        (filesystem, clock, randomness, ENVIRONMENT) as well as network,
        subprocess and database — and any touch falsifies the classification.
        A Tier 2 candidate is blocked only on the UNCONTROLLED groups: its temp
        dir and frozen clock are the point of the test, not a violation. Seven,
        not six: this sentence has now twice drifted by dropping `environment`,
        so the authority is `io_guard.blocked_groups(1)`, which returns
        `io_guard.GROUPS`, which is built from the group names in THIS file's
        CONTROLLABLE/UNCONTROLLABLE tables. `test_io_guard.py` asserts that
        identity, so the two cannot disagree without failing the gate.
      * PATCHED AT THE LOWEST LAYER REACHABLE FROM PYTHON, not at the ergonomic
        wrappers. `os.open` is not the builtin `open`; `pathlib` reaches
        neither; `os.posix_spawn` never goes through `subprocess.Popen`;
        `os.listdir` and `os.stat` pass through no fd a data-level patch ever
        sees. THE PATCH LIST IS NOT REPEATED HERE. Every earlier attempt to
        restate it went stale — the version that stood in this docstring named
        four targets and would have missed `builtins.open`, every `pathlib`
        read and the whole directory-and-metadata family. It lives in
        `assets/io_guard.py` (`arm`, and the `FILTER_MARKER_INTERCEPTS` /
        `PARTIALLY_INTERCEPTED` / `NOT_INTERCEPTED` tables), and those tables
        are asserted to partition THIS file's marker tables exactly, so a
        marker added here with no guard-layer intercept fails the gate rather
        than opening a silent two-layer hole.
      * LOADED AS A PYTEST PLUGIN (`-p`), never written into the target repo
        as a `conftest.py` — a plugin loads BEFORE collection, which is what
        arms it ahead of `import unit_module` and therefore ahead of any I/O
        the module does at import time; it cannot collide with a `conftest.py`
        the repo already has, and it leaves nothing behind, so invariant 1
        ("never modifies source") needs no carve-out. The tier reaches it by
        environment variable, read at plugin import: one proof run, one unit,
        one tier.
      * RAISING ITS OWN EXCEPTION TYPE, so the proof has three outcomes rather
        than two: an `AssertionError` is the RED half of red->green, while the
        guard's exception — at ANY point, red run or green run — means the
        CLASSIFICATION is wrong, so the unit is reclassified Tier 3 and the
        test is discarded regardless of red or green.

    `triage` exists to keep that guard from firing often, not to replace it —
    which is why it does not and cannot attempt to see through dynamic
    dispatch (`getattr`, `**kwargs`), a receiver whose method name resolves to
    no module-level class (`cfg.get(...)` on a plain dict) or to more than one
    of them, or cross-module indirection: all three are the points-to-analysis
    boundary this filter stops at on purpose. The split runs both ways, and
    one case is the filter's alone: `os.execv` and its family REPLACE THE
    PROCESS IMAGE, taking every in-process patch with them, so no runtime
    guard can survive one. Those are declined statically here — the whole
    family, derived from `dir(os)` and pinned by a test — and that is not
    redundancy with the guard, it is the one case the guard cannot reach. The
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
                                           analysis.local_classes,
                                           analysis.local_methods, enclosing_class)
    hits = _hits_from(own_targets, own_candidates, analysis.alias_map,
                       analysis.local_funcs, analysis.local_classes,
                       analysis.local_methods, {unit["name"]})

    tier, reason = _tier_from_hits(hits)
    # A FLOOR, applied last and only upward: a unit already at or above it
    # keeps its OWN reason, which names something more specific (and more
    # severe) than the module's import-time I/O does.
    if analysis.import_floor is not None and tier < analysis.import_floor[0]:
        return analysis.import_floor
    return tier, reason


def _tier_from_hits(hits) -> tuple:
    """(tier, reason) from a unit's own marker hits, before any module-level floor."""
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
