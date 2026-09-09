#!/usr/bin/env python3
"""Rank a repo's untested units by risk, and triage how testable each one is.

Stdlib + git only. No network, no third-party packages, no call-graph service:
the blast-radius half is a deliberate static approximation, labelled as such in
the output, so this runs anywhere the repo does.
"""
from __future__ import annotations

import argparse
import ast
import collections
import functools
import json
import os
import re
import subprocess
import sys

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
    path `iter_py_files` produces, silently reading that file's churn as 0).
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


def inbound_refs(root: str, units) -> dict:
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
    bare_counts = {}
    attr_counts = {}
    file_lines = {}
    file_texts = {}
    path_tokens = {}
    for rel in iter_py_files(root, include_tests=True):
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
                    "os.unsetenv"),
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


def already_covered(root: str, units) -> dict:
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

    The remaining failure mode is milder: a unit exercised only through a
    re-export (a test that reaches it via a different module's name and never
    mentions its own module) reads as uncovered and may get a duplicate test
    written for it. Under a shared basename that widens — a repo-root
    `utils.py` colliding with `pkg/utils.py` has no qualifier of its own, so it
    reads as uncovered outright. That direction is still safe — the worst case
    is a redundant test, not a hidden gap.
    """
    test_files = [rel for rel in iter_py_files(root, include_tests=True)
                  if _is_test_path(rel)]
    texts = {rel: read_text(root, rel) for rel in test_files}
    path_tokens = {rel: set(_PATH_TOKEN_RE.findall(rel)) for rel in test_files}
    basenames = collections.Counter(
        os.path.splitext(os.path.basename(rel))[0] for rel in iter_py_files(root))
    covered = {}
    for u in units:
        name_pattern = re.compile(r"\b%s\b" % re.escape(u["name"]))
        module = os.path.splitext(os.path.basename(u["path"]))[0]
        ambiguous = basenames[module] > 1
        qualified = _path_pattern(u["path"]) if ambiguous else None
        if ambiguous and qualified is None:
            continue                      # no evidence could name this file alone
        for rel in sorted(texts):
            text = texts[rel]
            if not name_pattern.search(text):
                continue
            if qualified is not None:
                if not qualified.search(text):
                    continue
            elif not _references_module(module, text, path_tokens[rel]):
                continue
            covered[u["id"]] = rel
            break
    return covered


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
        nothing, so everything is blocked — filesystem, clock and randomness
        as well as network, subprocess and DB — and any touch falsifies the
        classification. A Tier 2 candidate is blocked only on the
        UNCONTROLLED groups: its temp dir and frozen clock are the point of
        the test, not a violation.
      * PATCHED AT THE OS-LEVEL SYSCALL LAYER, not at the ergonomic wrappers.
        `os.open` is not the builtin `open`; `os.posix_spawn` never goes
        through `subprocess.Popen`; `mmap` and `io.FileIO` bypass Python file
        objects entirely. So the guard patches the `os` primitives, `io.FileIO`,
        `mmap.mmap` and `socket.socket`.
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


def _normalise(value, hi):
    return 0.0 if hi <= 0 else round(value / hi, 6)


def rank(root: str, since: str = "6 months ago", top_n: int = 10) -> dict:
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
    units = discover_units(root)
    churn_by_path = churn(root, since)
    refs = inbound_refs(root, units)
    covered = already_covered(root, units)

    rows = []
    for u in units:
        tier, reason = triage(root, u)
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
        "stack": "python",
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
