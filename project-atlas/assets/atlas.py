#!/usr/bin/env python3
"""project-atlas — index every project on a machine into one durable SQLite file.

Scans filesystem roots for project directories (a `.git` checkout or a recognised
toolchain manifest), records path/name/git/language/toolchain metadata into a
single SQLite database, indexes each project's README and docs for full-text
search, and generates a Litestream 0.5.x configuration that replicates that
database to S3, Cloudflare R2, Backblaze B2, or a local directory.

Design constraints this file exists to hold (see references/replication.md for the
grounded citations behind each one):

  * The index is opened in WAL mode on every connection and refuses to run in any
    other journal mode, because Litestream replicates from the WAL and forces
    `PRAGMA journal_mode = wal` on the database it is given.
  * FTS5 is probed at runtime, never assumed. Without it, `search` degrades to a
    LIKE scan that prints the same lines and exits 0.
  * Generated configs use Litestream's singular `replica:` mapping; the
    `replicas:` list is deprecated upstream and multiple replicas per database
    are no longer supported.
  * An R2 endpoint is always emitted with an explicit `https://` scheme, which is
    what Litestream's R2 provider detection keys on.
  * Credentials are emitted as `${ENV_VAR}` expansions. No secret is ever written.

Stdlib only — no pip, no network at scan/search/stats/doctor time.

    python3 atlas.py scan [ROOT ...]        # index; default root is $HOME
    python3 atlas.py search QUERY           # full-text / LIKE search
    python3 atlas.py stats                  # inventory summary
    python3 atlas.py doctor                 # capability probe
    python3 atlas.py litestream-config ...  # emit replication config + runbook
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json as jsonlib
import os
import shutil
import sqlite3
import subprocess
import sys

SCHEMA_VERSION = 1

# Litestream series whose config grammar this generator targets. Recorded so a
# future maintainer can tell whether a Litestream upgrade invalidates the output.
LITESTREAM_SERIES = "0.5.x"

DEFAULT_SYNC_INTERVAL = "10s"

#: Directory names never descended into. Vendor and build trees hold thousands of
#: manifests that are not projects anyone is looking for.
PRUNE_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", ".jj",
        "node_modules", "bower_components", "vendor",
        ".venv", "venv", ".tox", ".nox", "__pycache__", ".mypy_cache",
        ".pytest_cache", ".ruff_cache",
        "target", "dist", "build", "out", ".next", ".nuxt", ".output",
        ".gradle", ".idea", ".vscode", ".cache", ".terraform",
        "Pods", "DerivedData",
    }
)

#: Strong markers: presence makes the directory a project root.
#: filename -> (language or None, toolchain label)
STRONG_MARKERS = {
    "package.json": ("javascript", "npm"),
    "deno.json": ("typescript", "deno"),
    "pyproject.toml": ("python", "pyproject"),
    "setup.py": ("python", "setuptools"),
    "requirements.txt": ("python", "pip"),
    "Pipfile": ("python", "pipenv"),
    "Cargo.toml": ("rust", "cargo"),
    "go.mod": ("go", "gomod"),
    "Gemfile": ("ruby", "bundler"),
    "pom.xml": ("java", "maven"),
    "build.gradle": ("java", "gradle"),
    "build.gradle.kts": ("kotlin", "gradle"),
    "composer.json": ("php", "composer"),
    "mix.exs": ("elixir", "mix"),
    "Package.swift": ("swift", "swiftpm"),
    "pubspec.yaml": ("dart", "pub"),
    "CMakeLists.txt": ("c++", "cmake"),
    "flake.nix": (None, "nix"),
}

#: Weak markers: recorded as toolchains, but never enough on their own to call a
#: directory a project — otherwise every scratch folder with a Makefile lands in
#: the index.
WEAK_MARKERS = {
    "Makefile": (None, "make"),
    "Justfile": (None, "just"),
    "justfile": (None, "just"),
    "Dockerfile": (None, "docker"),
    "docker-compose.yml": (None, "compose"),
    "Taskfile.yml": (None, "task"),
    ".pre-commit-config.yaml": (None, "pre-commit"),
}

EXT_LANGUAGES = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".java": "java",
    ".kt": "kotlin", ".swift": "swift", ".c": "c", ".h": "c",
    ".cc": "c++", ".cpp": "c++", ".hpp": "c++", ".cs": "c#",
    ".php": "php", ".ex": "elixir", ".exs": "elixir", ".dart": "dart",
    ".sh": "shell", ".zsh": "shell", ".lua": "lua", ".sql": "sql",
    ".scala": "scala", ".hs": "haskell", ".ml": "ocaml", ".nix": "nix",
    ".r": "r", ".jl": "julia", ".zig": "zig",
}

#: Hard cap on indexed documentation text per project, in bytes. A monorepo with a
#: 40 MB docs tree must not be able to dominate the database or the FTS index.
DOC_BYTE_CAP = 65536

#: Hard cap on files walked per project when censusing languages and size.
FILE_CENSUS_CAP = 20000

DOC_DIRS = ("docs", "doc")
DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")


class AtlasError(Exception):
    """Any condition that should exit non-zero with a readable message."""


# ── time ─────────────────────────────────────────────────────────────────────
# ATLAS_NOW pins the clock so tests and evals stay deterministic; production runs
# just use UTC now.

def now_iso() -> str:
    pinned = os.environ.get("ATLAS_NOW")
    if pinned:
        return pinned
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


# ── database ─────────────────────────────────────────────────────────────────

def default_db_path() -> str:
    env = os.environ.get("ATLAS_DB")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(base, "project-atlas", "atlas.db")


def fts5_available(conn: sqlite3.Connection | None = None) -> bool:
    """True when this SQLite build can create an FTS5 table.

    Probed, never assumed: the authoritative statement that FTS5 ships in every
    build could not be grounded (see docs/project-atlas research validation), and
    distribution builds do vary.
    """
    own = conn is None
    if own:
        conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._atlas_fts_probe USING fts5(x)")
        conn.execute("DROP TABLE temp._atlas_fts_probe")
        return True
    except sqlite3.Error:
        return False
    finally:
        if own:
            conn.close()


def connect(db_path: str, *, create: bool = True) -> sqlite3.Connection:
    """Open the index in WAL mode, or fail.

    Litestream forces `PRAGMA journal_mode = wal` on the database it replicates
    and errors when the mode does not take. Discovering that at replication time
    means a silently unreplicated index, so the failure is pulled forward to here.
    """
    db_path = os.path.abspath(os.path.expanduser(db_path))
    parent = os.path.dirname(db_path)
    if create and parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise AtlasError("cannot create database directory %s: %s" % (parent, exc))
    if not create and not os.path.exists(db_path):
        raise AtlasError("no index at %s — run `atlas.py scan` first" % db_path)
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        raise AtlasError("cannot open %s: %s" % (db_path, exc))
    conn.row_factory = sqlite3.Row
    try:
        mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    except sqlite3.Error as exc:
        conn.close()
        raise AtlasError("cannot set WAL on %s: %s" % (db_path, exc))
    if str(mode).lower() != "wal":
        conn.close()
        raise AtlasError(
            "journal_mode is %r, not 'wal' — Litestream replicates from the WAL and "
            "cannot replicate this database (is the directory read-only, or on a "
            "filesystem without shared-memory support?)" % (mode,)
        )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def journal_mode(db_path: str) -> str | None:
    """Report the on-disk journal mode without changing it."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    except sqlite3.Error:
        return None
    try:
        return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    except sqlite3.Error:
        return None
    finally:
        conn.close()


DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY,
    path          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    vcs           TEXT,
    git_remote    TEXT,
    git_branch    TEXT,
    git_head      TEXT,
    git_head_at   TEXT,
    languages     TEXT,
    toolchains    TEXT,
    file_count    INTEGER,
    size_bytes    INTEGER,
    first_seen    TEXT NOT NULL,
    last_scanned  TEXT NOT NULL,
    missing_since TEXT
);
CREATE INDEX IF NOT EXISTS projects_name_idx ON projects(name);
CREATE INDEX IF NOT EXISTS projects_missing_idx ON projects(missing_since);
CREATE TABLE IF NOT EXISTS docs (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    body       TEXT NOT NULL
);
"""

FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5("
    "body, content='docs', content_rowid='project_id')"
)


def init_schema(conn: sqlite3.Connection) -> bool:
    """Create the schema. Returns True when the FTS5 index exists afterwards."""
    conn.executescript(DDL)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    has_fts = fts5_available(conn)
    if has_fts:
        try:
            conn.execute(FTS_DDL)
        except sqlite3.Error:
            has_fts = False
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('fts5', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("1" if has_fts else "0",),
    )
    conn.commit()
    return has_fts


def has_fts_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs_fts'"
    ).fetchone()
    return row is not None


# ── discovery ────────────────────────────────────────────────────────────────

def classify(entries: set[str]) -> tuple[list[str], list[str], bool]:
    """Return (languages, toolchains, is_project_root) for one directory listing."""
    languages: list[str] = []
    toolchains: list[str] = []
    strong = ".git" in entries
    for marker, (lang, tool) in STRONG_MARKERS.items():
        if marker in entries:
            strong = True
            if lang and lang not in languages:
                languages.append(lang)
            if tool not in toolchains:
                toolchains.append(tool)
    for marker, (lang, tool) in WEAK_MARKERS.items():
        if marker in entries:
            if lang and lang not in languages:
                languages.append(lang)
            if tool not in toolchains:
                toolchains.append(tool)
    if ".git" in entries and "git" not in toolchains:
        toolchains.insert(0, "git")
    return languages, toolchains, strong


def find_projects(roots, max_depth: int = 8, nested: bool = False):
    """Yield absolute project-root paths under each root.

    Never descends into a name from PRUNE_DIRS. By default a project root is not
    descended into either — one checkout is one project, and a monorepo's inner
    packages are noise in a machine-wide inventory. `nested=True` keeps walking.
    """
    seen: set[str] = set()
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            continue
        root_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in PRUNE_DIRS)
            if dirpath.rstrip(os.sep).count(os.sep) - root_depth >= max_depth:
                dirnames[:] = []
            entries = set(filenames) | set(dirnames)
            if os.path.isdir(os.path.join(dirpath, ".git")):
                entries.add(".git")
            _, _, is_project = classify(entries)
            if is_project:
                real = os.path.realpath(dirpath)
                if real not in seen:
                    seen.add(real)
                    yield dirpath
                if not nested:
                    dirnames[:] = []


# ── metadata capture ─────────────────────────────────────────────────────────

def _git(path: str, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", path] + list(args),
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def git_metadata(path: str) -> dict:
    """Origin remote, branch, HEAD id and HEAD commit time — empty on any failure.

    A bare/empty repo, a detached HEAD, a missing git binary and a repo with no
    remote are all normal states on a real machine; none of them may abort a scan.
    """
    if not os.path.isdir(os.path.join(path, ".git")):
        return {}
    remote = _git(path, "remote", "get-url", "origin")
    if not remote:
        first = _git(path, "remote").splitlines()
        if first:
            remote = _git(path, "remote", "get-url", first[0].strip())
    return {
        "vcs": "git",
        "git_remote": remote,
        "git_branch": _git(path, "rev-parse", "--abbrev-ref", "HEAD"),
        "git_head": _git(path, "rev-parse", "HEAD"),
        "git_head_at": _git(path, "log", "-1", "--format=%cI"),
    }


def census(path: str, cap: int = FILE_CENSUS_CAP) -> tuple[list[str], int, int]:
    """Extension census over the project tree: (languages, file_count, size_bytes)."""
    counts: dict[str, int] = {}
    files = 0
    size = 0
    for dirpath, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        for fn in filenames:
            files += 1
            ext = os.path.splitext(fn)[1].lower()
            lang = EXT_LANGUAGES.get(ext)
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
            try:
                size += os.lstat(os.path.join(dirpath, fn)).st_size
            except OSError:
                pass
            if files >= cap:
                ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
                return [lang for lang, _ in ranked[:3]], files, size
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [lang for lang, _ in ranked[:3]], files, size


def _read_text(path: str, budget: int) -> str:
    if budget <= 0:
        return ""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(budget)
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace")


def doc_body(path: str, name: str, cap: int = DOC_BYTE_CAP) -> str:
    """The searchable document for a project, capped at `cap` bytes.

    Path and name lead so a path fragment is searchable, then README, then
    top-level markdown, then docs/ — most identifying material first, because the
    cap truncates the tail.
    """
    parts = [path, name]
    used = sum(len(p.encode("utf-8")) + 1 for p in parts)
    try:
        entries = sorted(os.listdir(path))
    except OSError:
        entries = []

    def take(filepath: str) -> bool:
        nonlocal used
        remaining = cap - used
        if remaining <= 0:
            return False
        text = _read_text(filepath, remaining)
        if text:
            parts.append(text)
            used += len(text.encode("utf-8")) + 1
        return used < cap

    for entry in entries:
        if entry.lower().startswith("readme"):
            if not take(os.path.join(path, entry)):
                break
    if used < cap:
        for entry in entries:
            if entry.lower().startswith("readme"):
                continue
            if entry.endswith(DOC_SUFFIXES) and os.path.isfile(os.path.join(path, entry)):
                if not take(os.path.join(path, entry)):
                    break
    if used < cap:
        for docdir in DOC_DIRS:
            full = os.path.join(path, docdir)
            if not os.path.isdir(full):
                continue
            try:
                docfiles = sorted(os.listdir(full))
            except OSError:
                continue
            stop = False
            for entry in docfiles:
                fp = os.path.join(full, entry)
                if entry.endswith(DOC_SUFFIXES) and os.path.isfile(fp):
                    if not take(fp):
                        stop = True
                        break
            if stop:
                break

    body = "\n".join(parts)
    raw = body.encode("utf-8")
    if len(raw) > cap:
        body = raw[:cap].decode("utf-8", errors="ignore")
    return body


def inspect(path: str) -> dict:
    """Everything recorded about one project root."""
    try:
        entries = set(os.listdir(path))
    except OSError as exc:
        raise AtlasError("cannot read %s: %s" % (path, exc))
    if os.path.isdir(os.path.join(path, ".git")):
        entries.add(".git")
    marker_langs, toolchains, _ = classify(entries)
    census_langs, file_count, size_bytes = census(path)
    languages = list(marker_langs)
    for lang in census_langs:
        if lang not in languages:
            languages.append(lang)
    name = os.path.basename(os.path.abspath(path)) or path
    record = {
        "path": os.path.abspath(path),
        "name": name,
        "vcs": None,
        "git_remote": "",
        "git_branch": "",
        "git_head": "",
        "git_head_at": "",
        "languages": ",".join(languages),
        "toolchains": ",".join(toolchains),
        "file_count": file_count,
        "size_bytes": size_bytes,
    }
    record.update(git_metadata(path))
    record["_doc"] = doc_body(record["path"], name)
    return record


# ── scan ─────────────────────────────────────────────────────────────────────

UPSERT = """
INSERT INTO projects (path, name, vcs, git_remote, git_branch, git_head, git_head_at,
                      languages, toolchains, file_count, size_bytes,
                      first_seen, last_scanned, missing_since)
VALUES (:path, :name, :vcs, :git_remote, :git_branch, :git_head, :git_head_at,
        :languages, :toolchains, :file_count, :size_bytes, :now, :now, NULL)
ON CONFLICT(path) DO UPDATE SET
    name = excluded.name,
    vcs = excluded.vcs,
    git_remote = excluded.git_remote,
    git_branch = excluded.git_branch,
    git_head = excluded.git_head,
    git_head_at = excluded.git_head_at,
    languages = excluded.languages,
    toolchains = excluded.toolchains,
    file_count = excluded.file_count,
    size_bytes = excluded.size_bytes,
    last_scanned = excluded.last_scanned,
    missing_since = NULL
"""


def _under(path: str, roots) -> bool:
    path = os.path.abspath(path)
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root)).rstrip(os.sep)
        if path == root or path.startswith(root + os.sep):
            return True
    return False


def cmd_scan(args) -> int:
    roots = args.roots or [os.path.expanduser("~")]
    for root in roots:
        if not os.path.isdir(os.path.expanduser(root)):
            raise AtlasError("not a directory: %s" % root)
    found = list(find_projects(roots, max_depth=args.depth, nested=args.nested))

    if args.dry_run:
        for path in found:
            print("WOULD_INDEX: %s" % path)
        print("SCAN_RESULT: DRY-RUN (%d project(s) under %d root(s))"
              % (len(found), len(roots)))
        return 0

    conn = connect(args.db)
    try:
        has_fts = init_schema(conn)
        stamp = now_iso()
        existing = {
            row["path"]: row["id"]
            for row in conn.execute("SELECT id, path FROM projects")
        }
        new_count = 0
        for path in found:
            record = inspect(path)
            body = record.pop("_doc")
            record["now"] = stamp
            if record["path"] not in existing:
                new_count += 1
            conn.execute(UPSERT, record)
            pid = conn.execute(
                "SELECT id FROM projects WHERE path = ?", (record["path"],)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO docs(project_id, body) VALUES(?, ?) "
                "ON CONFLICT(project_id) DO UPDATE SET body = excluded.body",
                (pid, body),
            )

        # Tombstone, never delete: a project that vanished is a fact worth keeping,
        # and only rows under the roots just scanned may be marked.
        seen_paths = {os.path.abspath(p) for p in found}
        missing_now = 0
        for path, pid in existing.items():
            if path in seen_paths or not _under(path, roots):
                continue
            if os.path.isdir(path):
                continue
            row = conn.execute(
                "SELECT missing_since FROM projects WHERE id = ?", (pid,)
            ).fetchone()
            if row["missing_since"] is None:
                conn.execute(
                    "UPDATE projects SET missing_since = ? WHERE id = ?", (stamp, pid)
                )
            missing_now += 1

        if has_fts:
            conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
        conn.commit()
        total = conn.execute("SELECT count(*) FROM projects").fetchone()[0]
    finally:
        conn.close()

    print("SCAN_RESULT: OK (indexed=%d new=%d missing=%d total=%d engine=%s db=%s)"
          % (len(found), new_count, missing_now, total,
             "fts5" if has_fts else "like", os.path.abspath(args.db)))
    return 0


# ── search ───────────────────────────────────────────────────────────────────

SELECT_COLS = (
    "p.id, p.path, p.name, p.languages, p.toolchains, p.git_remote, p.git_branch, "
    "p.git_head_at, p.missing_since"
)


def search(conn: sqlite3.Connection, query: str, limit: int, use_fts: bool):
    """Return (rows, engine). Falls back to LIKE whenever FTS5 cannot serve."""
    if use_fts and has_fts_table(conn):
        try:
            rows = conn.execute(
                "SELECT %s FROM docs_fts JOIN projects p ON p.id = docs_fts.rowid "
                "WHERE docs_fts MATCH ? ORDER BY rank LIMIT ?" % SELECT_COLS,
                (query, limit),
            ).fetchall()
            return rows, "fts5"
        except sqlite3.Error:
            # A malformed MATCH expression is a user typo, not a reason to fail —
            # fall through to the substring engine rather than exiting non-zero.
            pass
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = conn.execute(
        "SELECT %s FROM docs JOIN projects p ON p.id = docs.project_id "
        "WHERE docs.body LIKE ? ESCAPE '\\' ORDER BY p.path LIMIT ?" % SELECT_COLS,
        (like, limit),
    ).fetchall()
    return rows, "like"


def cmd_search(args) -> int:
    conn = connect(args.db, create=False)
    try:
        rows, engine = search(conn, args.query, args.limit, use_fts=not args.no_fts)
    finally:
        conn.close()
    if args.json:
        print(jsonlib.dumps(
            {"engine": engine, "query": args.query,
             "hits": [dict(r) for r in rows]}, indent=2, sort_keys=True))
        return 0
    for row in rows:
        flag = " [MISSING]" if row["missing_since"] else ""
        detail = row["languages"] or row["toolchains"] or "-"
        print("HIT: %s — %s [%s]%s" % (row["path"], row["name"], detail, flag))
    print("SEARCH_RESULT: %d hit(s) (engine=%s)" % (len(rows), engine))
    return 0


# ── stats ────────────────────────────────────────────────────────────────────

def cmd_stats(args) -> int:
    conn = connect(args.db, create=False)
    try:
        total = conn.execute("SELECT count(*) FROM projects").fetchone()[0]
        missing = conn.execute(
            "SELECT count(*) FROM projects WHERE missing_since IS NOT NULL"
        ).fetchone()[0]
        git_backed = conn.execute(
            "SELECT count(*) FROM projects WHERE vcs = 'git'"
        ).fetchone()[0]
        langs: dict[str, int] = {}
        for row in conn.execute("SELECT languages FROM projects"):
            for lang in (row["languages"] or "").split(","):
                if lang:
                    langs[lang] = langs.get(lang, 0) + 1
        size = conn.execute(
            "SELECT coalesce(sum(size_bytes), 0) FROM projects"
        ).fetchone()[0]
    finally:
        conn.close()
    top = sorted(langs.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    if args.json:
        print(jsonlib.dumps(
            {"projects": total, "missing": missing, "git": git_backed,
             "bytes": size, "languages": dict(top)}, indent=2, sort_keys=True))
        return 0
    for lang, count in top:
        print("LANG: %-12s %d" % (lang, count))
    print("STATS_RESULT: projects=%d git=%d missing=%d bytes=%d"
          % (total, git_backed, missing, size))
    return 0


# ── doctor ───────────────────────────────────────────────────────────────────

CAPABILITIES = ("fts5", "wal", "litestream", "git")


def probe(db_path: str) -> dict:
    db_path = os.path.abspath(os.path.expanduser(db_path))
    exists = os.path.exists(db_path)
    report = {
        "db": db_path,
        "db_exists": exists,
        "journal_mode": journal_mode(db_path) if exists else None,
        "fts5": fts5_available(),
        "litestream": shutil.which("litestream"),
        "git": shutil.which("git"),
        "python": "%d.%d.%d" % sys.version_info[:3],
        "sqlite": sqlite3.sqlite_version,
        "projects": None,
        "missing": None,
    }
    if exists:
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
            report["projects"] = conn.execute(
                "SELECT count(*) FROM projects").fetchone()[0]
            report["missing"] = conn.execute(
                "SELECT count(*) FROM projects WHERE missing_since IS NOT NULL"
            ).fetchone()[0]
            conn.close()
        except sqlite3.Error:
            pass
    return report


def capability_ok(report: dict, name: str) -> bool:
    if name == "fts5":
        return bool(report["fts5"])
    if name == "wal":
        return report["journal_mode"] == "wal"
    if name == "litestream":
        return bool(report["litestream"])
    if name == "git":
        return bool(report["git"])
    raise AtlasError("unknown capability %r (known: %s)"
                     % (name, ", ".join(CAPABILITIES)))


def cmd_doctor(args) -> int:
    report = probe(args.db)
    required = []
    for item in args.require or []:
        required.extend(part.strip() for part in item.split(",") if part.strip())
    failures = [name for name in required if not capability_ok(report, name)]
    if args.json:
        report["required"] = required
        report["failed"] = failures
        print(jsonlib.dumps(report, indent=2, sort_keys=True))
        return 1 if failures else 0
    print("CHECK: fts5 — %s" % ("OK" if report["fts5"] else "MISSING (search falls back to LIKE)"))
    print("CHECK: journal_mode — %s" % (report["journal_mode"] or "NO-DB"))
    print("CHECK: litestream — %s" % (report["litestream"] or "MISSING (not on PATH)"))
    print("CHECK: git — %s" % (report["git"] or "MISSING (no git metadata will be recorded)"))
    print("CHECK: index — %s (%s project(s), %s missing)"
          % (report["db"], report["projects"], report["missing"]))
    if failures:
        print("DOCTOR_RESULT: FAIL (missing: %s)" % ", ".join(failures))
        return 1
    print("DOCTOR_RESULT: PASS")
    return 0


# ── litestream config generation ─────────────────────────────────────────────

# Environment-variable NAMES that the generated config references. These are
# identifiers, never values — the values stay in the operator's environment.
KEY_ID_VAR = "LITESTREAM_ACCESS_KEY_ID"
SECRET_VAR = "LITESTREAM_SECRET_ACCESS_KEY"  # scan-leaks:ignore — env var name, not a secret

R2_HOST = "r2.cloudflarestorage.com"


def ensure_https(endpoint: str) -> str:
    """Attach an explicit https:// scheme.

    Litestream detects Cloudflare R2 (and applies signed payloads, concurrency=2,
    and checksums-off) from the endpoint URL, and its provider doc states the
    scheme must be present for that detection to fire. A bare host silently
    disables the preset, so this is not cosmetic.
    """
    endpoint = endpoint.strip()
    if endpoint.startswith("https://"):
        return endpoint
    if endpoint.startswith("http://"):
        return "https://" + endpoint[len("http://"):]
    return "https://" + endpoint


def replica_url(args) -> str:
    bucket = args.bucket
    path = args.path.strip("/")
    base = "s3://%s/%s" % (bucket, path) if path else "s3://%s" % bucket
    params = []
    if args.target == "r2":
        if not args.endpoint and not args.account_id:
            raise AtlasError("--target r2 needs --account-id (or an explicit --endpoint)")
        endpoint = args.endpoint or "%s.%s" % (args.account_id, R2_HOST)
        params.append("endpoint=" + ensure_https(endpoint))
        params.append("region=" + (args.region or "auto"))
    elif args.target == "b2":
        if not args.endpoint:
            raise AtlasError("--target b2 needs --endpoint (e.g. s3.us-west-004.backblazeb2.com)")
        params.append("endpoint=" + ensure_https(args.endpoint))
        # Grounded in Litestream's provider-compatibility doc: B2 needs both.
        params.append("sign-payload=true")
        params.append("force-path-style=true")
        if args.region:
            params.append("region=" + args.region)
    elif args.target == "s3":
        params.append("region=" + (args.region or "us-east-1"))
    else:  # custom S3-compatible endpoint
        if not args.endpoint:
            raise AtlasError("--target custom needs --endpoint")
        params.append("endpoint=" + ensure_https(args.endpoint))
        if args.region:
            params.append("region=" + args.region)
    return base + ("?" + "&".join(params) if params else "")


def render_config(args) -> tuple[str, str]:
    """Return (yaml_text, restore_target).

    The generated document uses Litestream 0.5.x's singular `replica:` mapping.
    The `replicas:` list form is marked deprecated in Litestream's config structs
    and the loader rejects more than one entry, so emitting a list would be
    legacy syntax at best.
    """
    db_path = os.path.abspath(os.path.expanduser(args.db))
    interval = args.sync_interval or DEFAULT_SYNC_INTERVAL
    lines = [
        "# project-atlas — Litestream %s configuration (generated; no secrets inside)" % LITESTREAM_SERIES,
        "# Credentials are read from the environment at load time:",
        "#   export %s=..." % KEY_ID_VAR,
        "#   export %s=..." % SECRET_VAR,
    ]
    if args.target == "file":
        if not args.replica_path:
            raise AtlasError("--target file needs --replica-path")
        restore_target = "file://" + os.path.abspath(os.path.expanduser(args.replica_path))
        lines += [
            "",
            "dbs:",
            "  - path: %s" % db_path,
            "    replica:",
            "      path: %s" % os.path.abspath(os.path.expanduser(args.replica_path)),
            "      sync-interval: %s" % interval,
        ]
    else:
        url = replica_url(args)
        restore_target = url
        lines += [
            "",
            "access-key-id: ${%s}" % KEY_ID_VAR,
            "secret-access-key: ${%s}" % SECRET_VAR,
            "",
            "dbs:",
            "  - path: %s" % db_path,
            "    replica:",
            "      url: %s" % url,
            "      sync-interval: %s" % interval,
        ]
    return "\n".join(lines) + "\n", restore_target


def runbook(config_path: str, db_path: str, restore_target: str) -> str:
    return "\n".join([
        "# start replicating (leave running; a systemd unit or launchd job is the usual home)",
        "litestream replicate -config %s" % config_path,
        "",
        "# restore onto a fresh machine, latest transaction",
        'litestream restore -o %s "%s"' % (db_path, restore_target),
        "",
        "# restore to a point in time instead",
        'litestream restore -timestamp 2026-08-01T00:00:00Z -o %s "%s"'
        % (db_path, restore_target),
        "",
        "# check what would happen without writing anything",
        'litestream restore -dry-run -o %s "%s"' % (db_path, restore_target),
    ])


def cmd_litestream_config(args) -> int:
    text, restore_target = render_config(args)
    # A generated config that embeds a live secret is a leak with a long tail —
    # it lands in dotfiles, backups, and screenshots. Refuse rather than warn.
    for var in ("AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID",
                KEY_ID_VAR, SECRET_VAR, "R2_SECRET_ACCESS_KEY"):
        value = os.environ.get(var)
        if value and len(value) >= 8 and value in text:
            raise AtlasError("refusing to emit a config containing the value of $%s" % var)
    db_path = os.path.abspath(os.path.expanduser(args.db))
    if args.out:
        out_path = os.path.abspath(os.path.expanduser(args.out))
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("WROTE: %s" % out_path)
        print(runbook(out_path, db_path, restore_target))
    else:
        # YAML on stdout so it pipes; the runbook on stderr so it does not.
        sys.stdout.write(text)
        sys.stderr.write(runbook("<config-path>", db_path, restore_target) + "\n")
    return 0


# ── cli ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas.py",
        description="Index local projects into a durable, searchable, replicated SQLite database.",
    )
    parser.add_argument("--db", default=None,
                        help="index path (default: $ATLAS_DB or XDG data dir)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_db_flag(p):
        # SUPPRESS so an unset subcommand flag does not clobber the global one —
        # `atlas.py --db X scan` and `atlas.py scan --db X` both work.
        p.add_argument("--db", default=argparse.SUPPRESS,
                       help="index path (may also be given before the subcommand)")

    scan = sub.add_parser("scan", help="discover and index project roots")
    scan.add_argument("roots", nargs="*", help="directories to scan (default: $HOME)")
    scan.add_argument("--depth", type=int, default=8, help="max directory depth (default: 8)")
    scan.add_argument("--nested", action="store_true",
                      help="keep descending inside a project root (monorepo packages)")
    scan.add_argument("--dry-run", action="store_true", help="list roots, write nothing")
    add_db_flag(scan)
    scan.set_defaults(func=cmd_scan)

    find = sub.add_parser("search", help="search indexed projects")
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=20)
    find.add_argument("--no-fts", action="store_true", help="force the LIKE engine")
    find.add_argument("--json", action="store_true")
    add_db_flag(find)
    find.set_defaults(func=cmd_search)

    stats = sub.add_parser("stats", help="summarise the index")
    stats.add_argument("--json", action="store_true")
    add_db_flag(stats)
    stats.set_defaults(func=cmd_stats)

    doctor = sub.add_parser("doctor", help="probe capabilities the skill depends on")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--require", action="append",
                        help="capability that must be present (%s); repeatable or comma-separated"
                             % "|".join(CAPABILITIES))
    add_db_flag(doctor)
    doctor.set_defaults(func=cmd_doctor)

    cfg = sub.add_parser("litestream-config",
                         help="emit a Litestream %s config and restore runbook" % LITESTREAM_SERIES)
    cfg.add_argument("--target", required=True,
                     choices=("s3", "r2", "b2", "custom", "file"))
    cfg.add_argument("--bucket", default="", help="bucket name (object-storage targets)")
    cfg.add_argument("--path", default="project-atlas", help="key prefix inside the bucket")
    cfg.add_argument("--account-id", default="", help="Cloudflare account id (r2)")
    cfg.add_argument("--endpoint", default="", help="S3-compatible endpoint host or URL")
    cfg.add_argument("--region", default="", help="region (default: us-east-1 for s3, auto for r2)")
    cfg.add_argument("--replica-path", default="", help="destination directory (file target)")
    cfg.add_argument(
        "--sync-interval",
        default=DEFAULT_SYNC_INTERVAL,  # scan-leaks:ignore — constant ref, not a credential
        help="explicit sync interval (Litestream's own default is 1s)")
    cfg.add_argument("--out", default="", help="write the config here instead of stdout")
    add_db_flag(cfg)
    cfg.set_defaults(func=cmd_litestream_config)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "db", None) is None:
        args.db = default_db_path()
    if getattr(args, "target", None) in ("s3", "r2", "b2", "custom") and not args.bucket:
        print("ERROR: --bucket is required for --target %s" % args.target, file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except AtlasError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
