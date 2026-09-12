#!/usr/bin/env python3
r"""The Rust stack: what a Rust repo means by each interface name.

Everything here is language-specific and everything language-specific is here;
the stack-agnostic half -- churn, reference counting, coverage detection,
scoring, ranking, the CLI, the JSON shape -- stays in `rank_risk.py`, which
documents the interface the two meet at. The spec is
`docs/superpowers/specs/2026-09-12-test-safety-net-rust-design.md`.

WHAT MAKES RUST DIFFERENT FROM THE OTHER THREE, in the order it bites:

  * A CRATE IS THE UNIT OF VISIBILITY, and a `tests/` file is a crate of its
    own. It reaches the library only through the library's public paths
    (`use calcx::calc::pure;`), and `crate::` inside it means the TEST crate.
  * A MODULE IS A FILE (`src/calc/add.rs` is `calc::add`, `src/calc/mod.rs` is
    `calc`), so `scope_files` is `[rel]`, as for python and node.
  * The lexer has shapes no other stack has: NESTED block comments, raw
    strings whose delimiter is chosen per literal (`r##"…"##`), and a `'` that
    is a char literal in `'a'` but a LIFETIME in `&'a str`.
  * A method is named `Type::method` (go's is `T.M`), and is credited only
    through a value bound to its type -- the rule go's review round forced.

THE CRATE INDEX -- how this stack avoids guessing a crate it cannot see.
The interface passes no root: `module_of(rel)`, `is_test_path(rel)`,
`is_test_for`, `path_pattern`, `name_pattern`, `module_bindings` and
`reached_through_module` are all given repo-relative paths only. Go meets the
same wall and under-credits: its `module_of` cannot read `go.mod`'s module
path, so a repo-root package answers its file stem. Rust meets it harder,
because a crate's `use` name (`[package] name`, `-` -> `_`, or `[lib] name`)
is in `Cargo.toml` and usually differs from anything in the path.

So the walk records what it saw. `_index(root)` reads every `Cargo.toml`
under `root` (line by line: `[section]` headers and `key = "value"` lines,
since python 3.10 has no `tomllib`), keeps the result in `_CRATES` keyed by
`os.path.realpath(root)`, and marks that root CURRENT (`_CURRENT_ROOT`).
`iter_source_files(root)` and `discover_units(root)` call it, and
`crate_of(root, rel)` and `reachability(root, unit)` use it directly. Every
root-less function above reads the CURRENT index -- the root walked last,
which is the root every
`rank_risk` pass is working on, because every pass walks before it asks.

When there is no index, or `rel` lies under no indexed crate, the FALLBACK
derives the crate directory from the path -- the prefix before its first
`src` or `tests` segment -- and treats the crate NAME as unknown. Every form
that needs the name then binds or credits nothing: `use <crate>::…`, a
`<crate>::` qualifier, `path_pattern`, which returns None, and
`module_of("src/lib.rs")`, which answers `"lib"`.
`crate::` and `super::` still work inside the one derived crate. The fallback
therefore only ever under-credits, which is what the interface requires.

The runtime guard is the ENFORCEMENT; everything here is the FILTER. A reader
may be wrong toward more reported work, never toward less.
"""
from __future__ import annotations

import bisect
import collections
import functools
import os
import posixpath
import re
import sys

# The shipped assets are a flat directory, not a package, and callers load
# them by path from directories that are not this one. Make the siblings
# importable by name before importing them. NEVER import `rank_risk` here: it
# is loaded by path and never lands in `sys.modules`, so importing it back
# would execute a second copy of the ranker mid-import.
_ASSETS = os.path.dirname(os.path.abspath(__file__))
if _ASSETS not in sys.path:
    sys.path.insert(0, _ASSETS)

from stack_common import SKIP_DIRS, evidence_score, read_text     # noqa: E402

STACK_NAME = "rust"

MANIFESTS = ("Cargo.toml",)

# Cargo's build output. `vendor/` is already in `SKIP_DIRS`.
_RUST_SKIP_DIRS = frozenset({"target"})

# Directories and files that belong to a crate but are not LIBRARY source,
# pruned only when they sit directly in a crate root: `src/examples/` is an
# ordinary module directory.
_CRATE_ROOT_SKIP_DIRS = frozenset({"examples", "benches"})
_CRATE_ROOT_SKIP_FILES = frozenset({"build.rs"})


# ── Identity ─────────────────────────────────────────────────────────────

def evidence(root: str) -> int:
    """How much of `root` is Rust: non-test `.rs` files, scaled by a manifest.

    The rule lives in `stack_common.evidence_score`, shared, so every stack
    weighs the same evidence on the same scale.
    """
    return evidence_score(sum(1 for _rel in iter_source_files(root)),
                          root, MANIFESTS, classify_manifest)


def classify_manifest(name: str, text: str) -> str:
    """`declaring` when a `Cargo.toml` has a `[package]` or `[workspace]` table.

    Anything else -- a `Cargo.toml` holding only `[profile.release]`, or a
    `[package]` that is only a comment -- is `tooling`: it declares nothing
    cargo would build.
    """
    if name != "Cargo.toml":
        return "tooling"              # not a name this stack knows: never declares
    tables = {t for t, _kv in _toml_tables(text)}
    return "declaring" if tables & {"package", "workspace"} else "tooling"


# ── Crates: `Cargo.toml`, read line by line ──────────────────────────────

CrateInfo = collections.namedtuple("CrateInfo", "dir name lib bins")
CrateInfo.__doc__ = """One package: repo-relative paths, and its name as `use` spells it.

    dir   the package directory ("" at the repo root)
    name  `[lib] name`, else `[package] name` with `-` -> `_`
    lib   the library root file, or None when the package has no library
    bins  the binary root files, sorted
"""

# The crate index (see the module docstring). `_CRATES[realpath(root)]` is
# `{crate dir: CrateInfo}`; `_CURRENT_ROOT` is the realpath the root-less
# functions answer for, or None when nothing has been walked.
_CRATES = {}
_CURRENT_ROOT = None

_TOML_HEADER = re.compile(r"^\[(\[)?\s*([A-Za-z0-9_.\-]+)\s*\](?(1)\])\s*(?:#.*)?$")
_TOML_KEYVAL = re.compile(
    r"""^([A-Za-z0-9_\-]+)\s*=\s*(?:"((?:[^"\\\n]|\\.)*)"|'([^'\n]*)')""")


def _toml_tables(text: str):
    """[(table name, {key: string value})] in file order; `[[x]]` repeats.

    Only `[section]` / `[[section]]` headers and `key = "value"` (or
    `'value'`) lines are read -- every value this stack needs is a string --
    and everything else is skipped: arrays, inline tables, multi-line
    strings, dotted keys. A key before any header lands in table "".

    EVERY line starting with `[` opens a table (ruling R6). One this reader
    cannot name -- a quoted key such as `[target.'cfg(unix)'.dependencies.x]`
    -- opens a table named None, which no lookup asks for, so its keys are
    ignored rather than landing in the table before it (where a `path = …`
    used to overwrite `[lib] path`). Killing test:
    `TestCrates.test_a_quoted_table_header_opens_an_ignored_table`.
    """
    tables = [("", {})]
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("["):
            h = _TOML_HEADER.match(s)
            tables.append((h.group(2) if h else None, {}))
            continue
        kv = _TOML_KEYVAL.match(s)
        if kv:
            value = kv.group(2)
            if value is None:
                value = kv.group(3)
            else:
                value = value.replace('\\"', '"').replace("\\\\", "\\")
            tables[-1][1][kv.group(1)] = value
    return tables


def _join(d: str, path: str) -> str:
    return posixpath.normpath(posixpath.join(d, path.replace("\\", "/")))


def _read_crate(root: str, d: str, text: str):
    """The CrateInfo a `Cargo.toml` in repo-relative dir `d` declares, or None."""
    tables = _toml_tables(text)
    package = next((kv for t, kv in tables if t == "package"), None)
    if package is None or not package.get("name"):
        return None
    lib_t = next((kv for t, kv in tables if t == "lib"), {})
    name = lib_t.get("name") or package["name"].replace("-", "_")

    def exists(rel):
        return os.path.isfile(os.path.join(root, rel))

    if lib_t.get("path"):
        lib = _join(d, lib_t["path"])
    else:
        lib = _join(d, "src/lib.rs")
        lib = lib if exists(lib) else None
    bins = {_join(d, kv["path"]) for t, kv in tables if t == "bin" and kv.get("path")}
    main = _join(d, "src/main.rs")
    if exists(main):
        bins.add(main)
    bin_dir = os.path.join(root, d, "src", "bin")
    if os.path.isdir(bin_dir):
        for entry in sorted(os.listdir(bin_dir)):
            if entry.endswith(".rs") and os.path.isfile(os.path.join(bin_dir, entry)):
                bins.add(_join(d, "src/bin/" + entry))
            elif os.path.isfile(os.path.join(bin_dir, entry, "main.rs")):
                bins.add(_join(d, "src/bin/%s/main.rs" % entry))
    return CrateInfo(dir=d, name=name, lib=lib, bins=tuple(sorted(bins)))


def _prune(dirs):
    """Directories no walk here enters: vendored, built, hidden, or `_`-private."""
    return sorted(x for x in dirs
                  if x not in SKIP_DIRS and x not in _RUST_SKIP_DIRS
                  and not x.startswith((".", "_")))


def _index(root: str, refresh: bool = True) -> dict:
    """`{crate dir: CrateInfo}` for every package under `root`; marks `root` current.

    `refresh=False` reuses an index already built for this root -- what
    `crate_of` wants when asked about many files in a row. A walk
    (`iter_source_files`, `discover_units`) always refreshes, so
    an edited `Cargo.toml` is seen by the next pass.
    """
    global _CURRENT_ROOT
    real = os.path.realpath(root)
    if refresh or real not in _CRATES:
        crates = {}
        for base, dirs, files in os.walk(root):
            dirs[:] = _prune(dirs)
            if "Cargo.toml" in files:
                d = os.path.relpath(base, root).replace(os.sep, "/")
                d = "" if d == "." else d
                info = _read_crate(root, d, read_text(root, posixpath.join(d, "Cargo.toml")))
                if info is not None:
                    crates[d] = info
        _CRATES[real] = crates
    _CURRENT_ROOT = real
    return _CRATES[real]


# MEASURED 2026-09-13, macOS arm64, rustup 1.29.1, before choosing the line
# reader. None of it gates the design: this module never runs cargo, so the
# ranker works on a machine with none and `--no-precise` has nothing to
# decline. It is recorded for anyone who later proposes calling cargo.
#   * `RUSTUP_AUTO_INSTALL=0 cargo metadata --no-deps --offline
#     --format-version 1` in a crate with NO `Cargo.lock` wrote none, exit 0,
#     on cargo 1.92.0 (344c4567c 2025-10-21) and cargo 1.82.0 (8f40fc59f
#     2024-08-21).
#   * The same against a STALE lock (a path dependency bumped 0.1.0 -> 0.2.0
#     after locking) left `Cargo.lock` byte-identical, exit 0, on both. With
#     `--locked` it also exits 0: `--no-deps` never resolves, so `--locked`
#     has nothing to refuse.
#   * `RUSTUP_AUTO_INSTALL=0 rustc -V` under a `rust-toolchain.toml` pinning
#     an uninstalled 1.70.0 exits 1 ("toolchain '1.70.0-aarch64-apple-darwin'
#     is not installed") in 0.016s, with `~/.rustup/toolchains` and
#     `~/.rustup/downloads` unchanged: rustup 1.29.1 honours the variable, so
#     the guard needs no `rustup toolchain list` pre-check.
def crate_of(root: str, rel: str):
    """The CrateInfo of the nearest package at or above `rel`, or None.

    Reads `root`'s index (built on first use, reused after) and marks `root`
    current, like every other entry point that is given a root. A workspace
    root with no `[package]` is not a crate, so a member's file finds its
    member; a nested package is nearer than the one around it.
    """
    return _nearest(_index(root, refresh=False), _norm(rel))


def _norm(rel: str) -> str:
    return rel.replace(os.sep, "/")


def _nearest(crates: dict, rel: str):
    d = posixpath.dirname(rel)
    while True:
        if d in crates:
            return crates[d]
        if not d:
            return None
        d = posixpath.dirname(d)


def _current_crate(rel: str):
    """`rel`'s CrateInfo in the CURRENT index, or None (no index, or no package)."""
    crates = _CRATES.get(_CURRENT_ROOT) if _CURRENT_ROOT else None
    return _nearest(crates, rel) if crates else None


def _crate_dir(rel: str):
    """`rel`'s crate directory: the current index's, else derived from the path.

    The derived dir is the prefix before the first `src` or `tests` segment,
    and None when there is neither -- a file no rule can place in a crate.
    """
    rel = _norm(rel)
    info = _current_crate(rel)
    if info is not None:
        return info.dir
    parts = rel.split("/")
    for i, seg in enumerate(parts[:-1]):
        if seg in ("src", "tests"):
            return "/".join(parts[:i])
    return None


def _crate_name(rel: str):
    """The `use` name of `rel`'s crate, or None when the index does not know it."""
    info = _current_crate(_norm(rel))
    return info.name if info is not None else None


def _within(rel: str, d: str) -> str:
    """`rel` relative to crate dir `d`."""
    return rel[len(d) + 1:] if d else rel


# ── Files ────────────────────────────────────────────────────────────────

def is_test_path(rel: str) -> bool:
    """True when the segment directly under `rel`'s crate root is `tests`.

    Cargo's own rule: each `tests/*.rs` is an integration-test crate, and
    `tests/common/mod.rs` is a module of those. A module NAMED `tests` inside
    `src/` is library code. The crate root comes from the current index,
    else the path (see the module docstring).
    """
    rel = _norm(rel)
    d = _crate_dir(rel)
    if d is None:
        return False
    parts = _within(rel, d).split("/")
    return len(parts) > 1 and parts[0] == "tests"


def iter_source_files(root: str, include_tests: bool = False):
    """Repo-relative `.rs` paths of library and binary source, sorted.

    Builds the crate index for `root` first and marks it current (see the
    module docstring), so every root-less answer that follows is `root`'s.

    Pruned everywhere: `SKIP_DIRS`, `target/`, and any directory starting
    with `.` or `_`. Pruned at a crate root only: `examples/`, `benches/` and
    `build.rs` -- code a crate ships beside its library, not in it. `tests/`
    files are yielded only with `include_tests`.
    """
    return _source_files(root, _index(root), include_tests)


def _source_files(root: str, crates: dict, include_tests: bool = False):
    """`iter_source_files` against an index the caller has already built."""
    out = []
    for base, dirs, files in os.walk(root):
        d = os.path.relpath(base, root).replace(os.sep, "/")
        d = "" if d == "." else d
        at_crate_root = d in crates
        dirs[:] = [x for x in _prune(dirs)
                   if not (at_crate_root and x in _CRATE_ROOT_SKIP_DIRS)]
        for name in sorted(files):
            if not name.endswith(".rs") or name.startswith((".", "_")):
                continue
            if at_crate_root and name in _CRATE_ROOT_SKIP_FILES:
                continue
            rel = posixpath.join(d, name) if d else name
            if not include_tests and is_test_path(rel):
                continue
            out.append(rel)
    return sorted(out)


def is_test_for(test_rel: str, src_rel: str) -> bool:
    """True when both files sit in one crate directory (`crate_of(...).dir`).

    POSITION ONLY: `tests/it.rs` is positioned for every file of its package
    and for no other package's. Whether it exercises a given unit is
    `reached_through_module`'s question. The crate directory comes from the
    current index, else the path.
    """
    a, b = _crate_dir(test_rel), _crate_dir(src_rel)
    return a is not None and a == b


def scope_files(rel: str, all_files) -> list:
    """`[rel]`: a Rust module is its file.

    A sibling file is another module, reached only through a path or a `use`,
    so its bare occurrences are not this file's scope -- unlike go, where the
    whole directory is one package scope.
    """
    return [rel]


# ── Naming ───────────────────────────────────────────────────────────────

def _roots(rel: str):
    """(crate dir, lib root, bin roots) for `rel`, or None outside every crate.

    From the current index; in the fallback, cargo's defaults by path shape:
    `src/lib.rs`, `src/main.rs`, `src/bin/…`.
    """
    d = _crate_dir(rel)
    if d is None:
        return None
    info = _current_crate(rel)
    if info is not None:
        return d, info.lib, frozenset(info.bins)
    return d, _join(d, "src/lib.rs"), frozenset()


def _is_bin_root(rel: str, d: str, bins) -> bool:
    """A binary's ROOT file: a `[[bin]] path`, `src/main.rs`, `src/bin/<x>.rs`
    or `src/bin/<x>/main.rs` -- never a submodule under `src/bin/<x>/`."""
    parts = _within(rel, d).split("/")
    return (rel in bins or parts == ["src", "main.rs"]
            or (parts[:2] == ["src", "bin"]
                and (len(parts) == 3 or (len(parts) == 4 and parts[3] == "main.rs"))))


def _module_path(rel: str):
    """(segments, in_lib) of `rel` inside its crate, or None.

    `src/calc/add.rs` -> `(["calc", "add"], True)`; `src/calc/mod.rs` ->
    `(["calc"], True)`; the lib root -> `([], True)`; a binary root ->
    `([], False)`; a submodule of a multi-file binary,
    `src/bin/tool/helper.rs` -> `(["helper"], False)`. None for anything
    outside the library's source directory -- `tests/`, a script, a file in
    no crate.

    Task 3 narrowed the bin rule (carried from Task 2's review): every file
    under `src/bin/` used to be a root with path `[]`, so `use super::*` in
    `main.rs`'s test module bound `helper.rs`. Killing test:
    `TestBinaryModulePaths.test_a_bin_submodule_is_not_a_bin_root`.
    """
    rel = _norm(rel)
    roots = _roots(rel)
    if roots is None:
        return None
    d, lib, bins = roots
    if rel == lib:
        return [], True
    if _is_bin_root(rel, d, bins):
        return [], False
    inner = _within(rel, d).split("/")
    if inner[:2] == ["src", "bin"] and len(inner) > 3:
        parts = inner[3:]
        parts[-1] = parts[-1][:-len(".rs")]
        if parts[-1] == "mod":
            parts = parts[:-1]
        return (parts, False) if parts else None
    if is_test_path(rel):
        return None
    base = posixpath.dirname(lib) if lib else _join(d, "src")
    if not rel.startswith(base + "/" if base else ""):
        return None
    parts = _within(rel, base)[:-len(".rs")].split("/")
    if parts[-1] == "mod":
        parts = parts[:-1]
    return (parts, True) if parts else None


def module_of(rel: str) -> str:
    """The name other files use for `rel`'s module: its path's last segment.

    `src/calc/add.rs` -> `add`; `src/calc/mod.rs` -> `calc`; the lib root ->
    the CRATE name, which is what a test writes (`calcx::pure`); `src/main.rs`
    -> `main`. The crate name comes from the current index; with none, the
    lib root answers `"lib"`, its stem, which under-credits rather than
    matching a name that was never read.
    """
    rel = _norm(rel)
    roots = _roots(rel)
    if roots is not None and rel == roots[1]:
        return _crate_name(rel) or "lib"
    stem = posixpath.splitext(posixpath.basename(rel))[0]
    if stem == "mod":
        parent = posixpath.basename(posixpath.dirname(rel))
        if parent:
            return parent
    return stem


def path_pattern(rel: str):
    r"""`rel`'s module path behind its crate's name, bounded at both ends:
    `(?<![A-Za-z0-9_:])calcx::calc::add(?![A-Za-z0-9_])`.

    CONTROLLER RULING R5 (fix round 1) overrides the brief's
    `(?:crate::|<crate>::)?calc::add`. With the prefix optional, the bare
    tail matched inside ANOTHER crate's use-group (`use otherx::{calc::add};`),
    and the core takes a path-qualified hit as sufficient on its own
    (`rank_risk.already_covered`), so that was an over-credit. The crate
    prefix is now REQUIRED at every segment count, and it is `<crate>::`
    only: the core reads this pattern against `tests/` files, each a crate of
    its own, where `crate::calc::add` names the TEST crate's module rather
    than this one. A use-group of THIS crate (`use calcx::{calc::add}`) is
    credited through `module_bindings`, not here. Killing tests:
    `TestNaming.test_another_crates_use_group_is_not_this_path` and
    `test_path_pattern_is_the_module_path_bounded_at_both_ends`.

    None when the crate name is unknown (no index: python returns None for a
    path it cannot qualify, the same way), for a crate root (`src/lib.rs`, a
    binary root), and for anything outside the library's source.
    """
    mp = _module_path(rel)
    name = _crate_name(rel)
    if not mp or not mp[0] or not mp[1] or not name:
        return None
    body = "::".join(re.escape(s) for s in [name] + mp[0])
    return re.compile(r"(?<![A-Za-z0-9_:])%s(?![A-Za-z0-9_])" % body)


_ID = r"[A-Za-z0-9_]"
_TURBOFISH = r"(?:\s*::\s*<[^<>;]*>)?"


def name_pattern(name: str, module: str = None):
    r"""`name` as a whole identifier -- or, for a method, as a CALL ON ITS TYPE.

    A plain name is word-bounded (ASCII; a Unicode identifier reads as
    uncovered, the safe direction).

    A method unit is named `Type::method` and gets `_MethodPattern`, which
    answers the same `.search(text)` the core asks of every pattern. `module`
    is the unit's module name (`module_of`); it, and the crate name when the
    current index holds exactly one crate (`_sole_crate_names`), are the
    only qualifiers `Type` may carry.
    """
    if "::" in name:
        recv, meth = name.rsplit("::", 1)
        return _MethodPattern(recv, meth, module)
    return re.compile(r"(?<!%s)%s(?!%s)" % (_ID, re.escape(name), _ID))


def _sole_crate_names():
    """`[name]` when the current index holds exactly ONE crate, else `[]`.

    Fix round 1, finding 1: every indexed name used to qualify `Type`, so
    with `calcx` and `otherx` indexed, `otherx::Report::new()` credited
    calcx's `Report::total`. `name_pattern` is given no path and cannot tell
    which crate the unit is in, so with more than one crate it offers no
    crate name at all -- under-credit, the direction the spec binds. Killing
    test: `TestMethodCreditInAWorkspace.test_a_second_crates_name_qualifies_nothing`.
    """
    crates = _CRATES.get(_CURRENT_ROOT) if _CURRENT_ROOT else None
    return [c.name for c in crates.values()] if crates and len(crates) == 1 else []


class _MethodPattern:
    """`Type::m` is exercised in a file only where `m` is called on a VALUE of `Type`.

    Go's rule, carried over with Rust's binding forms. A call `x.m(` counts
    when `x` is bound to `Type` in the SAME fn body -- `let x = Type::new(…)`,
    `let x = Type { … }`, `let x = Type::default()`, `let x: Type`, or a
    `x: &Type` / `x: Type` parameter -- or the call is direct:
    `Type::m(`, `(&Type { … }).m(`, `Type::new(…).m(`.

    `Type` may be qualified only by the unit's own `module`
    (`calc::Type`, `calcx::calc::Type`, `crate::calc::Type`) or by the crate
    name when the current index holds exactly one crate (`calcx::Type`, for
    a re-export; see `_sole_crate_names`). The three shapes go's second
    review broke are NEGATIVE tests in `TestMethodCredit`: any qualifier
    accepted (`other::Type`), a longer name binding (`TypeConfig::new`), and
    a binding carried from one fn into the next. This reads text, not types,
    so a value that reaches the test some other way reads as uncovered: a
    redundant test at worst, never a hidden gap.

    `via`, when given, REPLACES those qualifiers with a required one: `Type`
    must be written through one of these aliases of its module (`c::Type`),
    never bare. That is `reached_through_module`'s route for a file that
    imported the module but not the type (fix round 1, finding 2).

    Killing test for "accept any qualifier":
    `test_another_crates_type_of_the_same_name_credits_nothing`.
    """

    def __init__(self, recv, meth, module=None, via=None):
        R, M = re.escape(recv), re.escape(meth)
        if via:
            q = r"(?:%s)\s*::\s*" % "|".join(re.escape(a) for a in via)
        else:
            heads = ["crate"] + [re.escape(n) for n in _sole_crate_names()]
            quals = []
            if module:
                quals.append(r"(?:(?:%s)\s*::\s*(?:[A-Za-z_]\w*\s*::\s*)*)?%s\s*::\s*"
                             % ("|".join(heads), re.escape(module)))
            if len(heads) > 1:
                quals.append(r"(?:%s)\s*::\s*" % "|".join(heads[1:]))
            q = "(?:%s)?" % "|".join(quals) if quals else ""
        ident = r"(?:r#)?([A-Za-z_][A-Za-z0-9_]*)"
        ref = r"&?\s*(?:'[A-Za-z_]\w*\s+)?(?:mut\s+)?"
        let = r"(?<![A-Za-z0-9_])let\s+(?:mut\s+)?%s" % ident
        self.pattern = "%s::%s" % (recv, meth)
        self._call = r"\s*\.\s*%s%s\s*\(" % (M, _TURBOFISH)
        # `(?<![A-Za-z0-9_:.])` before the qualified type: a path is only ever
        # the one `q` spells, never `other::Type` with `other::` outside it.
        t = r"(?<![A-Za-z0-9_:.])%s%s(?![A-Za-z0-9_])" % (q, R)
        ctor = r"%s%s\s*::\s*(?:new|default)\s*\(" % (t, _TURBOFISH)
        self._binders = [re.compile(p) for p in (
            r"%s\s*=\s*%s%s" % (let, ref, ctor),
            r"%s\s*=\s*%s%s\s*\{" % (let, ref, t),
            r"%s\s*:\s*%s%s" % (let, ref, t),
            r"(?<![A-Za-z0-9_.:])(?:mut\s+)?%s\s*:\s*%s%s(?:\s*<[^<>]*>)?\s*[,)]"
            % (ident, ref, t),
        )]
        self._direct = [re.compile(p) for p in (
            r"%s%s\s*::\s*%s%s\s*\(" % (t, _TURBOFISH, M, _TURBOFISH),
            r"%s\s*\{[^{}]*\}\s*\)?%s" % (t, self._call),
            r"%s[^()]*\)%s" % (ctor, self._call),
        )]
        self._any_call = re.compile(r"(?:\.|::)\s*%s%s\s*\(" % (M, _TURBOFISH))

    def search(self, text, *_args):
        code = strip_noncode(text)
        if not self._any_call.search(code):
            return None
        for region in _fn_regions(code):
            m = self._search_region(region)
            if m:
                return m
        return None

    def _search_region(self, code):
        for rx in self._direct:
            m = rx.search(code)
            if m:
                return m
        names = set()
        for rx in self._binders:
            names.update(rx.findall(code))
        for name in sorted(names):
            m = re.search(r"(?<![A-Za-z0-9_.:])%s%s" % (re.escape(name), self._call), code)
            if m:
                return m
        return None


_FN_AT = re.compile(r"(?<![A-Za-z0-9_])fn\s+(?:r#)?[A-Za-z_][A-Za-z0-9_]*")


def _fn_regions(code: str):
    """Each fn item of stripped `code`, signature through body.

    A binding counts only inside the fn that makes it. A fn with no body (a
    trait declaration ending in `;`) is no region; a file with no fn at all
    is one region.
    """
    regions = []
    n = len(code)
    for m in _FN_AT.finditer(code):
        j, depth = m.end(), 0
        while j < n:
            ch = code[j]
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif depth <= 0 and ch in "{;":
                break
            j += 1
        if j >= n or code[j] != "{":
            continue
        regions.append(code[m.start():_brace_end(code, j)])
    return regions or [code]


def _brace_end(code: str, i: int) -> int:
    """Index just past the `}` closing the `{` at `i` (end of text if none)."""
    depth = 0
    for j in range(i, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(code)


# ── Lexing ───────────────────────────────────────────────────────────────

IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_])(?:r#)?[A-Za-z_][A-Za-z0-9_]*")


def preceding_qualifier(text: str, start: int):
    """The receiver an identifier at `start` is a path segment or field OF, or None.

    None    -- the identifier stands on its own (`pure(1)`), or follows a
               range (`0..n`), which is not a selector.
    "calc"  -- it was written `calc::pure` or `calc.pure`.
    ""      -- it follows something unnameable (`foo()::x`, `v[0].x`,
               `Vec::<u8>::new`).
    """
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i >= 1 and text[i - 1:i + 1] == "::":
        i -= 2
    elif i >= 0 and text[i] == "." and not (i >= 1 and text[i - 1] == "."):
        i -= 1
    else:
        return None
    while i >= 0 and text[i].isspace():
        i -= 1
    end = i + 1
    while i >= 0 and (text[i].isalnum() or text[i] == "_"):
        i -= 1
    qual = text[i + 1:end]
    return qual if qual and not qual[0].isdigit() else ""


# ── The comment/string stripper ──────────────────────────────────────────

_RAW_OPEN = re.compile(r'[bc]?r(#*)"')
_PLAIN_OPEN = re.compile(r'[bc]"')
_CHAR_ESCAPE = re.compile(r"\\(?:x[0-9A-Fa-f]{2}|u\{[0-9A-Fa-f_]{1,6}\}|[nrt\\0'\"])")


@functools.lru_cache(maxsize=256)
def strip_noncode(text: str, keep_strings: bool = False) -> str:
    """`text` with comments and literals blanked, every offset preserved.

    Blanked: `//` to the end of the line (`///` and `//!` included); block
    comments, which NEST (`/* /* */ */` is one comment); `"…"` with escapes,
    which may span lines; raw strings `r"…"`, `r#"…"#`, `r##"…"##`, closed
    only by a `"` followed by as many `#` as opened them; byte and C strings
    (`b"…"`, `br#"…"#`, `c"…"`); and char literals `'a'`, `'\\n'`,
    `'\\u{1F600}'`, `'\\''`, `b'a'`.

    A LIFETIME (`'a`, `'static`) or a loop label is NOT a char literal and
    stays code. The rule: a `'` followed by one char, or one escape, and then
    a `'` is a char literal; anything else is a lifetime.

    Blanked characters become spaces except newlines, so a position here is
    the same position in the original and line numbers hold.
    `keep_strings=True` keeps string and char bodies (an `#[path = "…"]`
    value is a string) and still blanks comments.

    Where it can be wrong, it is wrong toward leaving text VISIBLE: an
    unterminated `"` blanks only itself; an unterminated block comment or raw
    string runs to end of file, which does not compile either.
    """
    n = len(text)
    out = list(text)

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = text[i]
        if c == "/" and text[i + 1:i + 2] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and text[i + 1:i + 2] == "*":
            j = _block_comment_end(text, i, n)
            blank(i, j)
            i = j
            continue
        if c in "rbc" and (i == 0 or not _is_ident(text[i - 1])):
            j = _prefixed_literal_end(text, i, n)
            if j > 0:
                if not keep_strings:
                    blank(i, j)
                i = j
                continue
        if c == '"':
            j = _string_end(text, i + 1, n)
            if j < 0:
                if not keep_strings:
                    blank(i, i + 1)
                i += 1
                continue
            if not keep_strings:
                blank(i, j)
            i = j
            continue
        if c == "'":
            j = _char_end(text, i, n)
            if j > 0:
                if not keep_strings:
                    blank(i, j)
                i = j
                continue
        i += 1
    return "".join(out)


def _is_ident(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _block_comment_end(text: str, i: int, n: int) -> int:
    """Index just past the `*/` closing the NESTED comment opening at `i`.

    Killing test for "drop nested-comment depth": `test_block_comments_nest`.
    """
    depth, j = 0, i
    while j < n:
        if text.startswith("/*", j):
            depth += 1
            j += 2
        elif text.startswith("*/", j):
            depth -= 1
            j += 2
            if depth == 0:
                return j
        else:
            j += 1
    return n


def _string_end(text: str, j: int, n: int) -> int:
    """Index just past the `"` closing a string whose body starts at `j`, or -1."""
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j + 1
        j += 1
    return -1


def _prefixed_literal_end(text: str, i: int, n: int) -> int:
    """End of a raw, byte or C literal starting at `i`, or -1 when there is none.

    A raw string closes only at `"` plus as many `#` as opened it, and has no
    escapes. Killing test for "drop the r# raw-string rule":
    `test_raw_strings_with_hashes`. `r#ident` (a raw identifier) opens no
    literal: no `"` follows the hashes.
    """
    m = _RAW_OPEN.match(text, i)
    if m:
        close = '"' + m.group(1)
        j = text.find(close, m.end())
        return n if j < 0 else j + len(close)
    if _PLAIN_OPEN.match(text, i):
        return _string_end(text, i + 2, n)
    if text.startswith("b'", i):
        return _char_end(text, i + 1, n)
    return -1


def _char_end(text: str, i: int, n: int) -> int:
    """End of the char literal whose `'` is at `i`, or -1 when it is a lifetime.

    One char or one escape, then `'`. Killing test for "treat 'a as a char
    literal": `test_lifetimes_are_code_not_char_literals`.
    """
    j = i + 1
    if j >= n or text[j] in "'\n":
        return -1
    if text[j] == "\\":
        m = _CHAR_ESCAPE.match(text, j)
        if not m:
            return -1
        j = m.end()
    else:
        j += 1
    return j + 1 if j < n and text[j] == "'" else -1


# ── Import/binding grammar ───────────────────────────────────────────────

_USE_KW = re.compile(r"(?<![A-Za-z0-9_])use\s+")
_USE_TOKEN = re.compile(r"(?:r#)?[A-Za-z_][A-Za-z0-9_]*|::|[{},*]")
_INLINE_MOD = re.compile(r"(?<![A-Za-z0-9_])mod\s+(?:r#)?([A-Za-z_][A-Za-z0-9_]*)\s*\{")


def _use_leaves(body: str):
    """[(path segments, binding)] a `use` tree binds; binding None = last segment.

    `a::{b, c as d, e::*}` -> `[(["a","b"], None), (["a","c"], "d"),
    (["a","e"], "*")]`. A leading `::` is dropped; `r#` is dropped from
    every segment.
    """
    toks = [t[2:] if t.startswith("r#") else t for t in _USE_TOKEN.findall(body)]
    out = []

    def tree(i, prefix):
        segs = list(prefix)
        while i < len(toks):
            t = toks[i]
            if t == "::":
                i += 1
            elif t == "*":
                out.append((segs, "*"))
                return i + 1
            elif t == "{":
                i += 1
                while i < len(toks) and toks[i] != "}":
                    i = i + 1 if toks[i] == "," else tree(i, segs)
                return i + 1
            elif t in (",", "}"):
                break
            else:
                segs = segs + [t]
                i += 1
                if i + 1 < len(toks) and toks[i] == "as":
                    out.append((segs, toks[i + 1]))
                    return i + 2
        if len(segs) > len(prefix):
            out.append((segs, None))
        return i

    tree(0, [])
    return out


def _inline_mods(code: str):
    """[(name, start, end)] for every inline `mod name { … }` in stripped code."""
    return [(m.group(1), m.start(), _brace_end(code, m.end() - 1))
            for m in _INLINE_MOD.finditer(code)]


def _bin_key(rel: str, d: str) -> str:
    """The binary crate a bin-tree file belongs to: `src/bin/<name>`, or the root file."""
    parts = _within(rel, d).split("/")
    if len(parts) > 3 and parts[:2] == ["src", "bin"]:
        return _join(d, "src/bin/" + parts[2])
    return rel


def _same_crate(src_rel: str, ref_rel: str) -> bool:
    """Is `crate::` in `ref_rel` the crate `src_rel` is compiled into?

    The same package directory; `ref_rel` not a `tests/` file (every
    integration test is a crate of its own, where `crate::` means itself);
    and the same TARGET. Fix round 1, finding 3: the lib root and a bin root
    both have module path `[]`, so `use super::*;` in `src/lib.rs`'s test
    module bound every name of `src/main.rs`. A library file and a binary
    file are now never one crate, and two binary files are one only under
    the same bin root. Killing test:
    `TestBindingGrammar.test_the_lib_root_is_not_a_bin_roots_crate`.
    """
    d = _crate_dir(src_rel)
    if d is None or d != _crate_dir(ref_rel) or is_test_path(ref_rel):
        return False
    a, b = _module_path(src_rel), _module_path(ref_rel)
    if a is None or b is None or a[1] != b[1]:
        return False
    return a[1] or _bin_key(src_rel, d) == _bin_key(ref_rel, d)


def module_bindings(module: str, text: str, *, src_rel: str, ref_rel: str) -> tuple:
    """(aliases, names) `text`'s `use` declarations bind for `src_rel`'s module.

    `use <crate>::a::b;` -> alias `b` when `a::b` IS the module; `use
    <crate>::a::{b, c as d};` -> names `b`, `d` when `a` is; `use
    <crate>::a::*;` -> name `"*"`. Paths starting `crate::`, `self::`,
    `super::` or a bare segment resolve only when `ref_rel` is in the same
    crate (`_same_crate`), against `ref_rel`'s own module path plus the
    inline `mod` blocks around the `use` -- so `use super::*;` inside
    `src_rel`'s own `#[cfg(test)] mod tests` binds `"*"`, and the same line
    in another file's test module binds nothing.

    `<crate>` is the name the CURRENT crate index holds for `src_rel`'s crate;
    with no index it is unknown and those paths bind nothing. A path that
    resolves to any other module binds nothing: the predicate may
    under-credit, never over-credit. Comments and strings are stripped
    first, so a commented-out `use` binds nothing.

    `names` holds the names BOUND, so `{helper as run}` reports `run`.
    Which item that name IS stays in `_bindings`' (leaf, bound) pairs, and
    `reached_through_module` reads those (ruling R4).
    """
    aliases, items, glob = _bindings(text, src_rel, ref_rel)
    names = {bound for _leaf, bound in items} | ({"*"} if glob else set())
    return tuple(sorted(aliases)), tuple(sorted(names))


def _bindings(text: str, src_rel: str, ref_rel: str):
    """(aliases, {(leaf, bound)}, glob): `module_bindings` before it is flattened.

    `leaf` is the name the module DEFINES and `bound` the name the `use`
    binds it to -- `{helper as run}` is `("helper", "run")`. Kept apart so a
    renamed import credits only the item it imported (ruling R4).
    """
    src_rel, ref_rel = _norm(src_rel), _norm(ref_rel)
    target = _module_path(src_rel)
    if target is None:
        return set(), set(), False
    src_mp, in_lib = target
    cname = _crate_name(src_rel) if in_lib else None
    same = _same_crate(src_rel, ref_rel)
    ref_mp = _module_path(ref_rel) if same else None
    code = strip_noncode(text)
    mods = _inline_mods(code) if ref_mp is not None else []
    aliases, items, glob = set(), set(), False
    for m in _USE_KW.finditer(code):
        semi = code.find(";", m.end())
        body = code[m.end():semi if semi >= 0 else len(code)]
        cur = None
        if ref_mp is not None:
            cur = ref_mp[0] + [nm for nm, a, b in mods if a < m.start() < b]
        for segs, binding in _use_leaves(body):
            path = _resolve(segs, cname, same, cur)
            if path is None or binding == "_":
                continue
            if binding == "*":
                if path == src_mp:
                    glob = True
                continue
            if path and path[-1] == "self":
                path = path[:-1]
            bound = binding or (path[-1] if path else None)
            if not bound:
                continue
            if path == src_mp:
                aliases.add(bound)
            elif path[:-1] == src_mp and path:
                items.add((path[-1], bound))
    return aliases, items, glob


def _resolve(segs, cname, same, cur):
    """`segs` as a module path inside the unit's crate, or None when it is not one."""
    if not segs:
        return None
    head = segs[0]
    if head == "crate":
        return list(segs[1:]) if same else None
    if cname and head == cname:
        return list(segs[1:])
    if cur is None:
        return None
    path = list(cur)
    rest = list(segs)
    while rest and rest[0] in ("self", "super"):
        if rest.pop(0) == "super":
            if not path:
                return None
            path.pop()
    return path + rest


def reached_through_module(module: str, name: str, text: str, *,
                           src_rel: str, ref_rel: str) -> bool:
    """True when `text` CALLS `name` through a `use` binding of `src_rel`'s module.

    `alias::name(`; or a bare call after a `use` of THAT item -- `name(` for
    `{name}` or `*`, `go(` for `{name as go}`. The call site is the point,
    as every other stack ruled: a fn VALUE (`let f = pure;`) and a mention
    inside a string are not evidence the unit ran. A turbofish is allowed
    between the name and its `(`.

    CONTROLLER RULING R4 (fix round 1): a bare call credits only when the
    `use` leaf IS `name` (or the binding is `*`). `{helper as run}` binds
    the NAME `run` to `helper`, and `run()` then calls `helper`; crediting
    it to the unit `run` was an over-credit. Killing test:
    `TestBindingGrammar.test_a_renamed_import_credits_only_the_name_it_imported`.

    A method unit `Type::m` is reached when `m` is called on a value bound to
    `Type` -- `_MethodPattern`, the same rule the core applies everywhere
    else. Fix round 1, finding 2: a bare `Type` counts only when the file
    imported `Type` itself (`{Type}`, `{Type as T}`, or `*`); after only an
    alias of the module it must be written `alias::Type`. Before, ANY
    binding of the module unlocked a bare `Type`, so `use calcx::calc::helper;
    use otherx::Report;` reached calc's `Report::total`. Killing test:
    `test_a_method_needs_its_type_imported_not_just_the_module`.
    """
    aliases, items, glob = _bindings(text, src_rel, ref_rel)
    if not aliases and not items and not glob:
        return False
    if "::" in name:
        recv, meth = name.rsplit("::", 1)
        pats = [_MethodPattern(recv, meth, module)] if glob else []
        pats += [_MethodPattern(bound, meth, module)
                 for leaf, bound in sorted(items) if leaf == recv]
        if aliases:
            pats.append(_MethodPattern(recv, meth, module, via=sorted(aliases)))
        return any(p.search(text) for p in pats)
    code = strip_noncode(text)
    callees = ({name} if glob else set()) | {bound for leaf, bound in items if leaf == name}
    if any(re.search(r"(?<![A-Za-z0-9_.:])%s%s\s*\(" % (re.escape(c), _TURBOFISH), code)
           for c in sorted(callees)):
        return True
    esc = re.escape(name)
    return any(re.search(r"(?<![A-Za-z0-9_.:])%s\s*::\s*%s%s\s*\("
                         % (re.escape(alias), esc, _TURBOFISH), code)
               for alias in aliases)


# ── Discovery ────────────────────────────────────────────────────────────
#
# An ITEM READER over `strip_noncode` output, not a line matcher. A region
# (a file, an inline `mod { }`, an inherent `impl { }`) is read one item at a
# time: attributes, visibility, qualifiers, keyword, then the item runs to
# the first `;` or `{` outside parentheses and brackets -- a `{` opens its
# body, which is skipped whole unless it is a module or an inherent impl.
# That is the declaration-position anchor `stack_go._FUNC_AT` gives go: a
# `fn` counts only where an item may START (never column 0, so an indented
# method is found), so `fn` in a type (`pub type F = fn(u32);`), in a fn
# body, in a trait, in a `macro_rules!` body or in an `extern` block is never
# a unit. Comments and literals are blank before any of this runs.

_WORD_AT = re.compile(r"(?:r#)?[A-Za-z_][A-Za-z0-9_]*")
_QUALIFIERS = frozenset({"const", "async", "unsafe", "safe", "extern", "default"})
_PATH_ATTR = re.compile(r'\s*path\s*=\s*"((?:[^"\\\n]|\\.)*)"\s*')
_IMPL_TOKEN = re.compile(r"->|::|(?:r#)?[A-Za-z_][A-Za-z0-9_]*|\S")

_Head = collections.namedtuple("_Head", "cfg_test inner_test path vis kw kw_pos after")
_Fn = collections.namedtuple("_Fn", "name recv pos vis chain")
_ModDecl = collections.namedtuple("_ModDecl", "name vis path chain cfg_test")
_UseDecl = collections.namedtuple("_UseDecl", "body vis chain")
_Scan = collections.namedtuple("_Scan", "fns mods uses types inline")
_Scan.__doc__ = """What one file declares. A `chain` is the inline `mod`s around an
    item, outermost first, as `((name, vis), ...)`; `vis` is `"pub"`,
    `"pub(<restriction>)"`, or `""` for private.

    fns     every fn item with a body, at item position: `_Fn`
    mods    every out-of-line `mod name;`: `_ModDecl`
    uses    every `use` item outside an impl: `_UseDecl`
    types   `{(chain names, name): vis}` for struct, enum, union, type
    inline  the chain of every inline `mod name { }`, itself included
"""


def discover_units(root: str, precise: bool = True):
    """Interface entry point: `(units, "heuristic")`.

    A unit is a fn item or an inherent-impl method declared `pub` or
    `pub(<restriction>)`, with a body, in a library or binary source file
    (`_source_files`; `tests/`, `examples/`, `benches/`, `build.rs`,
    `target/` and vendored code never are). `#[cfg(test)]` items are skipped
    whole: an inline test module, a test-only fn or impl, and a file that
    only a `#[cfg(test)] mod x;` declares.

    UNREACHABLE UNITS ARE STILL UNITS. A `pub(crate)` fn, a `pub fn` in a
    private module and a binary's fns are ranked and reported; `reachability`
    says why a `tests/` crate cannot name them, and triage turns that into
    Tier 3. A private fn is not a unit: nothing outside its module can call
    it, so the fn that does is the unit.

    `precise` is accepted and ignored: Rust has no precise path (see the
    spec), so the label is always `heuristic`, as the report must say. The
    crate index is rebuilt for `root` first and `root` marked current (ruling
    R3), so every root-less answer that follows is `root`'s.

    A unit's `name` is the fn's name, or `Type::method`; an inline module's
    name joins its MODULE path (which `reachability` reads), not the unit
    name. Two units of one name in one file -- the same fn in two inline
    modules, one method in two impls -- keep the first, as go does.
    """
    crates = _index(root)
    units = []
    for rel in _source_files(root, crates):
        if _declared_only_for_test(root, crates, rel):
            continue
        units.extend(_units_in_text(rel, read_text(root, rel)))
    return sorted(units, key=lambda u: u["id"]), "heuristic"


def _units_in_text(rel: str, text: str):
    """The units one file's text declares, in file order."""
    scan = _scan_file(text)
    starts = _line_starts(text)
    units, seen = [], set()
    for f in scan.fns:
        if not f.vis:
            continue
        name = _fn_name(f)
        if name in seen:
            continue
        seen.add(name)
        units.append({
            "id": "%s::%s" % (rel, name),
            "path": rel,
            "name": name,
            "lineno": bisect.bisect_right(starts, f.pos),
            "kind": "method" if f.recv else "function",
        })
    return units


def _fn_name(f) -> str:
    return "%s::%s" % (f.recv, f.name) if f.recv else f.name


def _line_starts(text: str):
    return [0] + [m.end() for m in re.finditer("\n", text)]


@functools.lru_cache(maxsize=512)
def _scan_file(text: str):
    """The `_Scan` of one file's text (cached; callers must not mutate it)."""
    code = strip_noncode(text)
    ks = strip_noncode(text, keep_strings=True)
    out = _Scan([], [], [], {}, [])
    _scan(code, ks, 0, len(code), (), None, out)
    return out


def _scan(code, ks, lo, hi, chain, recv, out):
    """Read the items of `code[lo:hi]` into `out`.

    `chain` is the inline modules around the region; `recv` is the type of
    the inherent impl whose body this is, else None. `ks` is the same text
    with literals kept, read only for a `#[path = "…"]` value.
    """
    i = lo
    while True:
        i = _skip_ws(code, i, hi)
        if i >= hi:
            return
        h = _item_head(code, ks, i, hi)
        if h is None or h.inner_test:          # `#![cfg(test)]`: the region is test-only
            return
        if h.kw == "use":                      # a use tree holds `{`: it ends at `;`
            j = code.find(";", h.after, hi)
            j = hi if j < 0 else j
            if recv is None and not h.cfg_test:
                out.uses.append(_UseDecl(code[h.after:j], h.vis, chain))
            i = j + 1
            continue
        j = _item_stop(code, h.after, hi)
        block = None
        if j < hi and code[j] == "{":
            end = _brace_end(code, j)
            block = (j + 1, min(end - 1, hi))
            nxt = end
        else:
            nxt = j + 1
        if h.kw == "mod" and block is None:
            if recv is None:                   # a cfg(test) `mod x;` is recorded as one
                _record_mod_decl(code, h, chain, out)
        elif not h.cfg_test:
            _record(code, ks, h, block, j, chain, recv, out)
        i = nxt


def _item_head(code, ks, i, hi):
    """The `_Head` of the item starting at `i`, or None when it is unreadable.

    Reads outer attributes (`#[cfg(test)]`, `#[path = "…"]`), inner ones
    (`#![cfg(test)]`), the visibility -- `pub`, or `pub(<…>)` compacted to
    `pub(crate)`, `pub(in crate::x)` -- and the qualifiers `const`, `async`,
    `unsafe`, `safe`, `extern "…"` (the ABI string is already blank) and
    `default`. `kw` is the word that follows them, or None.

    Killing test for "drop the #[cfg(test)] skip":
    `TestHeuristicDiscovery.test_a_cfg_test_module_is_skipped_whole`.
    """
    cfg_test = inner_test = False
    path = None
    while i < hi and code[i] == "#":
        k = _skip_ws(code, i + 1, hi)
        inner = k < hi and code[k] == "!"
        if inner:
            k = _skip_ws(code, k + 1, hi)
        if k >= hi or code[k] != "[":
            break
        close = _close(code, k, hi)
        if close < 0:
            return None
        if re.sub(r"\s+", "", code[k + 1:close]) == "cfg(test)":
            if inner:
                inner_test = True
            else:
                cfg_test = True
        m = _PATH_ATTR.fullmatch(ks[k + 1:close])
        if m and not inner:
            path = m.group(1)
        i = _skip_ws(code, close + 1, hi)
    vis = ""
    m = _WORD_AT.match(code, i)
    if m and m.group() == "pub":
        i = _skip_ws(code, m.end(), hi)
        if i < hi and code[i] == "(":
            close = _close(code, i, hi)
            if close < 0:
                return None
            inside = re.sub(r"\s+", " ", code[i + 1:close].strip())
            vis = "pub(%s)" % re.sub(r"\s*::\s*", "::", inside)
            i = _skip_ws(code, close + 1, hi)
        else:
            vis = "pub"
        m = _WORD_AT.match(code, i)
    while m and m.group() in _QUALIFIERS:
        i = _skip_ws(code, m.end(), hi)
        m = _WORD_AT.match(code, i)
    if m:
        return _Head(cfg_test, inner_test, path, vis, m.group(), m.start(), m.end())
    return _Head(cfg_test, inner_test, path, vis, None, i, i)


def _record(code, ks, h, block, stop, chain, recv, out):
    """Record one item: a fn, an inline module, an inherent impl, or a type."""
    kw = h.kw
    if kw == "fn":
        name = _WORD_AT.match(code, _skip_ws(code, h.after, stop))
        if name and block is not None:         # no body: a declaration, not a unit
            out.fns.append(_Fn(name.group(), recv, h.kw_pos, h.vis, chain))
        return
    if recv is not None:
        return
    if kw == "mod" and block is not None:
        name = _WORD_AT.match(code, _skip_ws(code, h.after, stop))
        if name:
            sub = chain + ((_bare(name.group()), h.vis),)
            out.inline.append(sub)
            _scan(code, ks, block[0], block[1], sub, None, out)
    elif kw == "impl" and block is not None:
        recv_type = _inherent_type(code[h.after:stop])
        if recv_type:
            _scan(code, ks, block[0], block[1], chain, recv_type, out)
    elif kw in ("struct", "enum", "union", "type"):
        name = _WORD_AT.match(code, _skip_ws(code, h.after, stop))
        if name:
            out.types.setdefault((_names(chain), name.group()), h.vis)


def _record_mod_decl(code, h, chain, out):
    name = _WORD_AT.match(code, _skip_ws(code, h.after, len(code)))
    if name:
        out.mods.append(_ModDecl(_bare(name.group()), h.vis, h.path, chain, h.cfg_test))


def _inherent_type(header: str):
    """The type an `impl` header implements on, or None for a trait impl.

    `header` runs from after `impl` to its `{`. The impl's own generics are
    skipped; then a `for` outside angle brackets makes it a trait impl
    (`impl<T> From<T> for W<T>`, `unsafe impl Send for T`). A `for<'a>`
    inside the generics or after `where` is a higher-ranked bound, not a
    trait. The type is the last path segment before its generic arguments:
    `crate::a::Wrapper<T>` -> `Wrapper`. A negative impl, `dyn`, a tuple, a
    slice or a reference is None. Killing test for "treat `impl Trait for
    T` as inherent": `TestHeuristicDiscovery.test_a_trait_impl_method_is_not_a_unit`.
    """
    toks = _IMPL_TOKEN.findall(header)
    i, depth = 0, 0
    if toks and toks[0] == "<":
        while i < len(toks):
            depth += {"<": 1, ">": -1}.get(toks[i], 0)
            i += 1
            if depth == 0:
                break
    name, depth = None, 0
    for t in toks[i:]:
        if t == "<":
            depth += 1
        elif t == ">":
            depth -= 1
        elif depth > 0 or t in ("->", "::"):
            continue
        elif t == "where":
            break
        elif t in ("for", "!", "(", "[", "&", "*", "dyn"):
            return None
        elif _WORD_AT.fullmatch(t):
            name = t
    return name


def _item_stop(code, i, hi):
    """Index of the first `;` or `{` at paren/bracket depth 0 from `i`, or `hi`."""
    depth = 0
    while i < hi:
        ch = code[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth <= 0 and ch in ";{":
            return i
        i += 1
    return hi


def _close(code, i, hi):
    """Index of the bracket closing the one at `i` (same kind only), or -1."""
    o = code[i]
    c = {"(": ")", "[": "]", "{": "}"}[o]
    depth = 0
    for j in range(i, hi):
        if code[j] == o:
            depth += 1
        elif code[j] == c:
            depth -= 1
            if depth == 0:
                return j
    return -1


def _skip_ws(code, i, hi):
    while i < hi and code[i].isspace():
        i += 1
    return i


def _bare(name: str) -> str:
    """`r#type` -> `type`: the name a module's FILE is spelled with."""
    return name[2:] if name.startswith("r#") else name


def _names(chain) -> tuple:
    return tuple(n for n, _vis in chain)


# ── Reachability: can a `tests/` crate name this unit? ───────────────────
#
# THE MODULE TREE. From the crate's lib root, `mod name;` resolves to
# `name.rs` or `name/mod.rs` in the declaring file's module directory -- its
# own directory for a crate root, a `mod.rs` or a `#[path]` file, else its
# directory plus its stem -- with inline `mod`s around the declaration as
# further directories. `#[path = "…"]` is relative to the declaring file's
# directory, or, inside inline modules, to that module directory. Each
# binary root gets the same walk, recording only which files it reached.
#
# THE PUBLIC SET. A module is public when it is the crate root, a `pub mod`
# in a public module, or re-exported from a public module: `pub use m;`
# (named) or `pub use m::*;` (glob: every `pub` item and `pub mod` of `m`).
# A named `pub use m::item;` in a public module exports that one item. A
# `pub use` counts only in a public module, or when the name it binds is
# itself exported (a re-export chain). `pub(crate) use` never counts. Paths
# are `crate::…`, `self::…`, `super::…` or relative to the declaring module.
#
# The rule is the FILTER's, and it errs one way: a false "unreachable" only
# reports, and a false "reachable" writes a test that does not compile (NO
# BUILD). Where this reader is unsure it does not claim reach.

_Tree = collections.namedtuple("_Tree", "lib_files bin_files test_files mods pub_mods exported")

# `{realpath(root): (crate index object, {crate dir: _Tree})}`. A tree is
# reused only while the index it was built against is the one `_CRATES`
# holds, so the next walk (`_index` refreshes) rebuilds it.
_TREES = {}

_MAX_MODULE_DEPTH = 64


def reachability(root: str, unit: dict):
    """(reachable, why) for `unit`: can a `tests/` crate of its package name it?

    `why` is "" when it can. Otherwise, the first of:

      "binary-only"                   the file is compiled only into a binary
                                      (or the package has no library)
      "pub(crate)" (or pub(super)…)   the unit's own visibility
      "type Hidden is private"        a method whose type, declared beside
                                      its impl, is not `pub`
      "module calc::inner is private" the first module on the path that is not
                                      public (or "... is pub(crate)")
      "no mod declares src/x.rs"      a file the crate never compiles
      "cfg(test)"                     a file only a `#[cfg(test)] mod` declares

    A file declared more than once is reachable when any declaration is.
    """
    rel = _norm(unit["path"])
    info = crate_of(root, rel)
    if info is None:
        return False, "%s is in no crate" % rel
    tree = _tree(root, info)
    decls = tree.lib_files.get(rel)
    if not decls:
        if (rel in tree.bin_files or info.lib is None
                or _within(rel, info.dir).startswith("src/bin/")):
            return False, "binary-only"
        if rel in tree.test_files:
            return False, "cfg(test)"
        return False, "no mod declares %s" % rel
    text = read_text(root, rel)
    scan = _scan_file(text)
    fn = _declaration(scan, text, unit)
    if fn is None:
        return False, "no declaration of %s at line %s" % (unit["name"], unit.get("lineno"))
    if fn.vis != "pub":
        return False, fn.vis or "private"
    inner = _names(fn.chain)
    if fn.recv:
        type_vis = scan.types.get((inner, fn.recv))
        if type_vis is not None and type_vis != "pub":
            return False, "type %s is %s" % (fn.recv, type_vis or "private")
    item = fn.recv or fn.name
    why = ""
    for mp in decls:
        full = mp + inner
        if full in tree.pub_mods or (full, item) in tree.exported:
            return True, ""
        why = why or _first_closed(tree, full)
    return False, why


def _declaration(scan, text, unit):
    """The `_Fn` `unit` was discovered from: its name at its line, else its name."""
    starts = _line_starts(text)
    named = [f for f in scan.fns if _fn_name(f) == unit["name"]]
    for f in named:
        if bisect.bisect_right(starts, f.pos) == unit.get("lineno"):
            return f
    return named[0] if named else None


def _first_closed(tree, full) -> str:
    for k in range(1, len(full) + 1):
        prefix = full[:k]
        if prefix not in tree.pub_mods:
            return "module %s is %s" % ("::".join(prefix),
                                       tree.mods.get(prefix) or "private")
    return "not exported"


def _declared_only_for_test(root, crates, rel) -> bool:
    """True for a file only a `#[cfg(test)] mod x;` declares."""
    info = _nearest(crates, rel)
    if info is None:
        return False
    tree = _tree(root, info)
    return rel in tree.test_files and rel not in tree.lib_files and rel not in tree.bin_files


def _tree(root: str, info):
    real = os.path.realpath(root)
    crates = _CRATES.get(real)
    held = _TREES.get(real)
    if held is None or held[0] is not crates:
        held = (crates, {})
        _TREES[real] = held
    if info.dir not in held[1]:
        held[1][info.dir] = _build_tree(root, info)
    return held[1][info.dir]


def _build_tree(root: str, info):
    lib_files, mods, uses, test_files = {}, {(): "pub"}, [], set()
    if info.lib:
        _walk(root, info.lib, lib_files, mods, uses, test_files)
    bin_files = {}
    for b in info.bins:
        _walk(root, b, bin_files, {}, [], set())
    pub_mods, exported = _publish(mods, uses)
    return _Tree(lib_files, frozenset(bin_files), frozenset(test_files),
                 mods, pub_mods, exported)


# MEASURED 2026-09-13, cargo 1.92.0 (344c4567c 2025-10-21), before relying on
# it: a file loaded by `#[path = "x/y.rs"] pub mod z;` resolves its own
# `pub mod w;` to `src/x/w.rs` -- beside it, as a `mod.rs` would -- and with
# `w.rs` moved to `src/x/y/w.rs` the build fails (E0583, "create file
# src/x/w.rs"). `mod outer { #[path = "q.rs"] pub mod p; }` in `src/lib.rs`
# resolves to `src/outer/q.rs`. Hence `child_mod_rs = True` for a `#[path]`
# target, and the inline chain as directories under the declaring file's.
def _walk(root, start, files, mods, uses, test_files):
    """Follow `mod` declarations from the crate root `start`.

    Fills `files` (`{rel: [module path]}`), `mods` (`{module path: vis}`),
    `uses` (`[(declaring module path, _UseDecl)]`) and `test_files`.
    """
    stack, seen = [(start, (), True)], set()
    while stack:
        rel, mp, mod_rs = stack.pop()
        if (rel, mp) in seen or len(mp) > _MAX_MODULE_DEPTH or not _is_file(root, rel):
            continue
        seen.add((rel, mp))
        files.setdefault(rel, []).append(mp)
        scan = _scan_file(read_text(root, rel))
        for chain in scan.inline:
            mods[mp + _names(chain)] = chain[-1][1]
        for u in scan.uses:
            uses.append((mp + _names(u.chain), u))
        here = posixpath.dirname(rel)
        own = here if mod_rs else posixpath.join(
            here, posixpath.splitext(posixpath.basename(rel))[0])
        for m in scan.mods:
            inner = _names(m.chain)
            d = posixpath.join(own, *inner) if inner else own
            if m.path is not None:
                target, child_mod_rs = _join(d if inner else here, m.path), True
            else:
                flat, nested = _join(d, m.name + ".rs"), _join(d, m.name + "/mod.rs")
                if _is_file(root, flat):
                    target, child_mod_rs = flat, False
                elif _is_file(root, nested):
                    target, child_mod_rs = nested, True
                else:
                    continue
            if m.cfg_test:
                test_files.add(target)
                continue
            child = mp + inner + (m.name,)
            mods[child] = m.vis
            stack.append((target, child, child_mod_rs))


def _is_file(root: str, rel: str) -> bool:
    """A file inside `root` -- a `#[path]` never leads the walk out of it."""
    return (rel != ".." and not rel.startswith("../") and not posixpath.isabs(rel)
            and os.path.isfile(os.path.join(root, rel)))


def _publish(mods: dict, uses):
    """(public module paths, exported (module path, item)) -- to a fixed point.

    `resolved` holds each unrestricted `pub use` leaf as (declaring module,
    bound name or None for a glob, target path). A leaf takes effect once its
    declaring module is public, or once the name it binds is itself exported:
    `pub use a::X;` at the root makes `a`'s own `pub use self::b::X;` count.
    A glob's chain is not followed (under-credit). Killing tests: "count a
    private mod as public" -- `TestReachability.test_a_private_mod_is_not_reachable`;
    "ignore `pub use`" -- `test_a_named_pub_use_reaches_only_what_it_names`
    and `test_a_glob_pub_use_reaches_every_pub_item`.
    """
    resolved = []
    for mp, u in uses:
        if u.vis != "pub":
            continue
        for segs, binding in _use_leaves(u.body):
            path = _use_target(segs, mp)
            if not path or binding == "_":
                continue
            if binding == "*":
                resolved.append((mp, None, tuple(path)))
                continue
            if path[-1] == "self":
                path = path[:-1]
            if not path:
                continue
            name = binding or path[-1]
            resolved.append((mp, name, tuple(path)))
    pub, exported = {()}, set()
    changed = True
    while changed:
        changed = False
        for m, vis in mods.items():
            if m and vis == "pub" and m not in pub and m[:-1] in pub:
                pub.add(m)
                changed = True
        for mp, name, target in resolved:
            if mp not in pub and (name is None or (mp, name) not in exported):
                continue
            if name is None or target in mods:
                if target in mods and target not in pub:
                    pub.add(target)
                    changed = True
            elif (target[:-1], target[-1]) not in exported:
                exported.add((target[:-1], target[-1]))
                changed = True
    return frozenset(pub), frozenset(exported)


def _use_target(segs, mp):
    """A `use` path as a module path of this crate, or None when it leaves it."""
    if not segs:
        return None
    if segs[0] == "crate":
        return list(segs[1:])
    path, rest = list(mp), list(segs)
    while rest and rest[0] in ("self", "super"):
        if rest.pop(0) == "super":
            if not path:
                return None
            path.pop()
    return path + rest
