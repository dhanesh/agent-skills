#!/usr/bin/env python3
"""world_model.py — a persistent, SQLite-backed world model for a coding agent.

Stores ENTITIES (symbols / files / modules / real-world referents), the
INTERACTIONS between them, the CONSTRAINTS that should hold, and PROV-style
EVIDENCE — and, for every interaction and constraint, keeps TWO independent
confidence axes plus a validation status:

  observed_conf   how sure are we the relationship EXISTS (we saw it)      [epistemic]
  normative_conf  how sure are we the relationship is CORRECT/intended     [oracle-backed]
  validation      unverified | validated | contradicted | stale

The load-bearing invariant: **code observation never raises normative_conf**.
A file sighting can drive observed_conf to 1.0 while normative_conf stays 0 and
status stays `unverified`. Only oracle evidence (test / ci / doc / human) raises
normative confidence. This is the anti "code-is-ground-truth" guarantee.

Stdlib only (sqlite3 + argparse + json + datetime). No pip, no network.

CLI:  python3 world_model.py <command> ...   (see `wm.py` for the friendly name)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# Tunables — one block, so the epistemics are configurable without touching logic
# ─────────────────────────────────────────────────────────────────────────────
TAU_VALIDATE = 0.60          # normative_conf at/above which validation becomes 'validated'
DECAY_PER_SWEEP = 0.98       # multiplicative decay applied to stale observation evidence

# Evidence kinds and which axis they move.
OBSERVATION_KINDS = {"file_loc", "static", "runtime", "commit", "agent_assert"}
ORACLE_KINDS = {"test", "ci", "doc", "human"}
ALL_EVIDENCE_KINDS = OBSERVATION_KINDS | ORACLE_KINDS

# Entrenchment rank of a fact = max rank over its supporting evidence.
# Higher = surrendered LAST when two facts conflict (AGM epistemic entrenchment).
ENTRENCHMENT_RANK = {
    "human": 4,
    "test": 3, "ci": 3,
    "doc": 2,
    "static": 1, "runtime": 1, "file_loc": 1, "commit": 1,
    "agent_assert": 0,
}

VALIDATIONS = ("unverified", "validated", "contradicted", "stale")

# Repo-wide `build` (seeding) — deterministic structural scan, observation-only.
BUILD_IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".world-model", ".venv",
                     "venv", "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
                     ".idea", ".vscode", ".tox", ".next", "target", "vendor"}
BUILD_LANG_BY_EXT = {
    ".py": "python", ".sh": "shell", ".bash": "shell", ".js": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go",
    ".rs": "rust", ".rb": "ruby", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".php": "php", ".md": "markdown", ".json": "json",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".cfg": "ini", ".ini": "ini",
    ".sql": "sql", ".mk": "make",
}
BUILD_MAX_BYTES = 1_000_000
BUILD_MAX_REFS_PER_FILE = 40

# Extra extensions for language-aware extraction (beyond BUILD_LANG_BY_EXT above).
BUILD_LANG_BY_EXT.update({
    ".mjs": "javascript", ".cjs": "javascript", ".rake": "ruby", ".gemspec": "ruby",
    ".tf": "terraform", ".tfvars": "terraform", ".hcl": "hcl",
    ".cc": "cpp", ".cxx": "cpp", ".hh": "cpp", ".hxx": "cpp", ".ipp": "cpp",
})
# Files with no/ambiguous extension, matched by (lowercased) basename or path.
BUILD_SPECIAL_FILENAMES = {
    "dockerfile": "dockerfile", "containerfile": "dockerfile",
    "makefile": "make", "gnumakefile": "make",
    "gemfile": "ruby", "rakefile": "ruby",
}
# Languages whose edges come ONLY from the language extractor (running the generic
# file-reference scan on real code would add noisy string-literal edges).
CODE_LANGS = {"python", "javascript", "typescript", "ruby", "go", "rust",
              "java", "c", "cpp", "csharp", "php"}
JAVA_STDLIB_PREFIXES = {"java", "javax", "jakarta", "sun", "jdk"}
CS_STDLIB_PREFIXES = {"System", "Microsoft"}   # Microsoft.* is often a real dep, but noisy → skip by default
PHP_APP_NAMESPACES = {"App", "Tests", "Test", "Database"}
# Python top-level modules we don't record as external dependencies (stdlib noise).
PY_STDLIB = {
    "os", "sys", "re", "json", "math", "time", "datetime", "typing", "collections",
    "itertools", "functools", "subprocess", "pathlib", "argparse", "logging", "sqlite3",
    "abc", "io", "enum", "dataclasses", "unittest", "tempfile", "shutil", "random",
    "hashlib", "base64", "copy", "string", "threading", "asyncio", "http", "urllib",
    "socket", "struct", "csv", "xml", "html", "contextlib", "warnings", "traceback",
    "inspect", "importlib", "glob", "ast", "textwrap", "operator", "uuid", "secrets",
    "decimal", "fractions", "statistics", "queue", "signal", "select", "gc", "weakref",
}
RUST_INTERNAL = {"crate", "super", "self", "std", "core", "alloc"}


def _norm_js_pkg(spec):
    if spec.startswith("@"):
        return "/".join(spec.split("/")[:2])
    return spec.split("/")[0]


def _norm_go_pkg(p):
    parts = p.split("/")
    return "/".join(parts[:3]) if len(parts) >= 3 and "." in parts[0] else p


def _toplevel(target):
    return re.split(r"[./]", target)[0]


# ── per-language structural extractors: text -> [(predicate, target, kind)] ──────
# kind ∈ {'file' (path), 'module' (may resolve local, else external dep),
#         'module_local' (relative import; local-only), 'external' (always a dependency)}
def _extract_python(text, rel):
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    pkg_parts = os.path.dirname(rel).split(os.sep) if os.path.dirname(rel) else []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in PY_STDLIB:
                    out.append(("imports", a.name, "module"))
        elif isinstance(node, ast.ImportFrom):
            if node.level:                    # relative import → resolve against this package
                base = pkg_parts[:len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
                dotted = ".".join(base + (node.module.split(".") if node.module else []))
                out.append(("imports", dotted, "module_local"))
                for a in node.names:
                    out.append(("imports", f"{dotted}.{a.name}", "module_local"))
            elif node.module and node.module.split(".")[0] not in PY_STDLIB:
                out.append(("imports", node.module, "module"))
                for a in node.names:
                    out.append(("imports", f"{node.module}.{a.name}", "module"))
    return out


def _extract_js(text, rel):
    specs = []
    specs += re.findall(r"""\bfrom\s*['"]([^'"]+)['"]""", text)          # import/export ... from 'x'
    specs += re.findall(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)""", text)
    specs += re.findall(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)""", text)  # dynamic import
    specs += re.findall(r"""^\s*import\s+['"]([^'"]+)['"]""", text, re.M)  # bare `import 'x'`
    out, seen = [], set()
    for spec in specs:
        if spec in seen:
            continue
        seen.add(spec)
        if spec.startswith(".") or spec.startswith("/"):
            out.append(("imports", spec, "file"))
        else:
            out.append(("depends_on", _norm_js_pkg(spec), "external"))
    return out


def _extract_ruby(text, rel):
    out = []
    for t in re.findall(r"""require_relative\s+['"]([^'"]+)['"]""", text):
        out.append(("imports", t, "file"))
    for t in re.findall(r"""(?<![\w_])require\s+['"]([^'"]+)['"]""", text):
        out.append(("imports", t, "module"))
    for t in re.findall(r"""^\s*gem\s+['"]([^'"]+)['"]""", text, re.M):     # Gemfile / gemspec
        out.append(("depends_on", t, "external"))
    return out


def _extract_go(text, rel):
    paths = []
    for block in re.findall(r"import\s*\(([^)]*)\)", text, re.S):
        paths += re.findall(r'"([^"]+)"', block)
    paths += re.findall(r'import\s+(?:[\w.]+\s+)?"([^"]+)"', text)
    out, seen = [], set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        if "." in p.split("/")[0]:            # has a domain in the first segment → external
            out.append(("depends_on", _norm_go_pkg(p), "external"))
    return out


def _extract_rust(text, rel):
    out = []
    for name in re.findall(r"^\s*(?:pub\s+)?mod\s+([A-Za-z_]\w*)\s*;", text, re.M):
        out.append(("includes", name, "file"))
    seen = set()
    for seg in re.findall(r"^\s*(?:pub\s+)?use\s+([A-Za-z_]\w*)", text, re.M):
        if seg in RUST_INTERNAL or seg in seen:
            continue
        seen.add(seg)
        out.append(("depends_on", seg, "external"))
    return out


def _extract_dockerfile(text, rel):
    out = []
    for img in re.findall(r"^\s*FROM\s+(\S+)", text, re.I | re.M):
        out.append(("depends_on", img, "external"))
    for argline in re.findall(r"^\s*(?:COPY|ADD)\s+(.+)$", text, re.I | re.M):
        if "--from=" in argline:
            continue
        toks = [t for t in argline.split() if not t.startswith("--")]
        if len(toks) >= 2 and not toks[0].startswith(("http", "\"", "$")) and "*" not in toks[0]:
            out.append(("references", toks[0], "file"))
    return out


def _extract_compose(text, rel):        # docker-compose / k8s / any yaml with image:
    return [("depends_on", m, "external")
            for m in re.findall(r"""^\s*image:\s*["']?([^\s"'#]+)""", text, re.M | re.I)]


def _extract_actions(text, rel):
    out = []
    for u in re.findall(r"""^\s*-?\s*uses:\s*["']?([^\s"'#]+)""", text, re.M):
        if u.startswith(".") or u.startswith("/"):
            out.append(("references", u, "file"))
        else:
            out.append(("depends_on", u, "external"))
    return out


def _extract_terraform(text, rel):
    out = []
    for s in re.findall(r'source\s*=\s*"([^"]+)"', text):
        if s.startswith(".") or s.startswith("/"):
            out.append(("references", s, "file"))
        else:
            out.append(("depends_on", s, "external"))
    return out


def _extract_make(text, rel):
    out = []
    for line in re.findall(r"^\s*[-]?include\s+(.+)$", text, re.M):
        for f in line.split():
            out.append(("references", f, "file"))
    return out


def _extract_shell(text, rel):
    return [("includes", m[1], "file")
            for m in re.findall(r"""^\s*(?:source|\.)\s+(["']?)([\w./$\-]+\.(?:sh|bash))\1""",
                                text, re.M)]


def _extract_java(text, rel):
    # `import a.b.C;` → resolve to a repo class (kind 'java') else external artifact.
    out = []
    for imp in re.findall(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", text, re.M):
        out.append(("imports", imp[:-2] if imp.endswith(".*") else imp, "java"))
    return out


def _extract_c(text, rel):
    out = []
    for q in re.findall(r'#\s*include\s+"([^"]+)"', text):          # local header
        out.append(("includes", q, "file"))
    for a in re.findall(r"#\s*include\s+<([^>]+)>", text):          # system/library header
        if "/" in a:                                               # e.g. <boost/asio.hpp>, <gtest/gtest.h>
            out.append(("depends_on", a.split("/")[0], "external"))
        # bare <stdio.h>/<vector> stdlib headers are skipped (noise)
    return out


def _extract_csharp(text, rel):
    out = []
    for u in re.findall(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", text, re.M):
        if " = " in u:                                             # `using X = A.B;` alias — skip
            continue
        out.append(("imports", u, "csharp"))
    return out


def _extract_php(text, rel):
    out = []
    for m in re.findall(r"""(?:require|include)(?:_once)?\s*\(?\s*['"]([^'"]+\.php)['"]""", text):
        out.append(("includes", m, "file"))                        # local include (reliable)
    for u in re.findall(r"^\s*use\s+\\?([\\\w]+)", text, re.M):     # namespace import
        out.append(("imports", u, "php_ns"))
    return out


BUILD_EXTRACTORS = {
    "python": _extract_python, "javascript": _extract_js, "typescript": _extract_js,
    "ruby": _extract_ruby, "go": _extract_go, "rust": _extract_rust,
    "java": _extract_java, "c": _extract_c, "cpp": _extract_c,
    "csharp": _extract_csharp, "php": _extract_php,
    "dockerfile": _extract_dockerfile, "compose": _extract_compose,
    "github-actions": _extract_actions, "terraform": _extract_terraform,
    "make": _extract_make, "shell": _extract_shell,
}


def _external_name(kind, target):
    """Map an unresolved import to an external dependency name (or None to skip stdlib)."""
    if kind == "external":
        return target
    if kind == "module":                       # python / ruby → top-level package
        return _toplevel(target)
    if kind == "java":
        segs = target.split(".")
        if segs[0] in JAVA_STDLIB_PREFIXES:
            return None
        return ".".join(segs[:2])              # coarse artifact group (precise deps need pom/gradle)
    if kind == "csharp":
        segs = target.split(".")
        if segs[0] in CS_STDLIB_PREFIXES:
            return None
        return ".".join(segs[:2])
    if kind == "php_ns":
        segs = target.split("\\")
        if segs[0] in PHP_APP_NAMESPACES:
            return None
        return segs[0]                         # vendor namespace
    return _toplevel(target)


# Kinds that first try to resolve to a local file, and kinds eligible to become external deps.
BUILD_LOCAL_KINDS = {"file", "module", "module_local", "java"}
BUILD_EXTERNAL_KINDS = {"external", "module", "java", "csharp", "php_ns"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS entity (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('symbol','file','module','referent')),
  symbol_id    TEXT UNIQUE,
  name         TEXT NOT NULL,
  path         TEXT,
  lang         TEXT,
  entity_type  TEXT,
  attrs        TEXT,
  first_seen   TEXT NOT NULL,
  last_seen    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_path ON entity(path);
CREATE INDEX IF NOT EXISTS idx_entity_name ON entity(name);

CREATE TABLE IF NOT EXISTS interaction (
  id             INTEGER PRIMARY KEY,
  subject_id     INTEGER NOT NULL REFERENCES entity(id),
  predicate      TEXT NOT NULL,
  object_id      INTEGER NOT NULL REFERENCES entity(id),
  observed_conf  REAL NOT NULL DEFAULT 0.0,
  normative_conf REAL NOT NULL DEFAULT 0.0,
  validation     TEXT NOT NULL DEFAULT 'unverified' CHECK (validation IN
                   ('unverified','validated','contradicted','stale')),
  entrenchment   INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  invalidated_at TEXT,
  UNIQUE (subject_id, predicate, object_id)
);
CREATE INDEX IF NOT EXISTS idx_int_subject ON interaction(subject_id);
CREATE INDEX IF NOT EXISTS idx_int_object  ON interaction(object_id);

CREATE TABLE IF NOT EXISTS constraint_ (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  kind            TEXT NOT NULL CHECK (kind IN
                    ('functional','cardinality','forbids','requires','disjoint','type','range','value_set','invariant')),
  scope_predicate TEXT,
  params          TEXT,
  severity        TEXT NOT NULL DEFAULT 'violation' CHECK (severity IN ('violation','warning','info')),
  message_tmpl    TEXT NOT NULL,
  observed_conf   REAL NOT NULL DEFAULT 0.0,
  normative_conf  REAL NOT NULL DEFAULT 0.0,
  validation      TEXT NOT NULL DEFAULT 'unverified' CHECK (validation IN
                    ('unverified','validated','contradicted','stale')),
  entrenchment    INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  invalidated_at  TEXT
);

CREATE TABLE IF NOT EXISTS evidence (
  id            INTEGER PRIMARY KEY,
  fact_kind     TEXT NOT NULL CHECK (fact_kind IN ('interaction','constraint')),
  fact_id       INTEGER NOT NULL,
  evidence_kind TEXT NOT NULL CHECK (evidence_kind IN
                  ('test','ci','doc','file_loc','commit','human','static','runtime','agent_assert')),
  ref           TEXT NOT NULL,
  polarity      TEXT NOT NULL DEFAULT 'supports' CHECK (polarity IN ('supports','refutes')),
  agent         TEXT,
  activity      TEXT,
  weight        REAL NOT NULL DEFAULT 0.5,
  observed_at   TEXT NOT NULL,
  UNIQUE (fact_kind, fact_id, evidence_kind, ref, polarity)
);
CREATE INDEX IF NOT EXISTS idx_evidence_fact ON evidence(fact_kind, fact_id);

CREATE TABLE IF NOT EXISTS contradiction (
  id            INTEGER PRIMARY KEY,
  constraint_id INTEGER REFERENCES constraint_(id),
  detected_by   TEXT NOT NULL,
  severity      TEXT NOT NULL,
  message       TEXT NOT NULL,
  proposed_fix  TEXT,
  detected_at   TEXT NOT NULL,
  resolved_at   TEXT,
  resolution    TEXT CHECK (resolution IN ('retract','supersede','accept_both','fixed_code','defer')),
  dedup_key     TEXT
);
CREATE INDEX IF NOT EXISTS idx_contra_open ON contradiction(resolved_at);

CREATE TABLE IF NOT EXISTS contradiction_member (
  contradiction_id INTEGER NOT NULL REFERENCES contradiction(id),
  fact_kind        TEXT NOT NULL,
  fact_id          INTEGER NOT NULL,
  PRIMARY KEY (contradiction_id, fact_kind, fact_id)
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS entity_fts USING fts5(
  name, path, entity_type, content='entity', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS entity_ai AFTER INSERT ON entity BEGIN
  INSERT INTO entity_fts(rowid, name, path, entity_type)
  VALUES (new.id, new.name, coalesce(new.path,''), coalesce(new.entity_type,''));
END;
CREATE TRIGGER IF NOT EXISTS entity_ad AFTER DELETE ON entity BEGIN
  INSERT INTO entity_fts(entity_fts, rowid, name, path, entity_type)
  VALUES ('delete', old.id, old.name, coalesce(old.path,''), coalesce(old.entity_type,''));
END;
CREATE TRIGGER IF NOT EXISTS entity_au AFTER UPDATE ON entity BEGIN
  INSERT INTO entity_fts(entity_fts, rowid, name, path, entity_type)
  VALUES ('delete', old.id, old.name, coalesce(old.path,''), coalesce(old.entity_type,''));
  INSERT INTO entity_fts(rowid, name, path, entity_type)
  VALUES (new.id, new.name, coalesce(new.path,''), coalesce(new.entity_type,''));
END;
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def noisy_or(weights) -> float:
    """1 - Π(1 - w): independent-evidence fusion. More sightings → higher, saturating."""
    p = 1.0
    for w in weights:
        p *= (1.0 - max(0.0, min(1.0, w)))
    return round(1.0 - p, 6)


def _evidence_source(ref) -> str:
    """The correlated 'source' of an evidence pointer — the file/path portion before ':'
    (e.g. 'auth/hash.py:14' → 'auth/hash.py'), else the whole ref. Two sightings that share
    a source are treated as correlated, not independent (TruthFinder copying-source intuition)."""
    head = (ref or "").split(":", 1)[0]
    return head if ("/" in head or "." in head) else (ref or "")


def grouped_noisy_or(pairs) -> float:
    """Fuse (source, weight) evidence: CORRELATED within a source (take the max, so repeated
    sightings of the same file don't inflate), INDEPENDENT across sources (noisy-OR). This
    dampens the naive noisy-OR's over-count when evidence is not independent."""
    best = {}
    for src, w in pairs:
        best[src] = max(best.get(src, 0.0), w)
    return noisy_or(best.values())


class WorldModel:
    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.has_fts = False
        self._init()

    def _init(self):
        self.conn.executescript(SCHEMA)
        try:
            self.conn.executescript(FTS_SCHEMA)
            self.has_fts = True
        except sqlite3.OperationalError:
            self.has_fts = False  # graceful LIKE fallback (§ retrieval)
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ── entities ──────────────────────────────────────────────────────────────
    def upsert_entity(self, kind, name, symbol_id=None, path=None, lang=None,
                      entity_type=None, attrs=None) -> int:
        if symbol_id is None:
            symbol_id = self._default_symbol_id(kind, name, path, entity_type)
        ts = now()
        cur = self.conn.execute("SELECT id FROM entity WHERE symbol_id=?", (symbol_id,))
        row = cur.fetchone()
        if row:
            self.conn.execute(
                "UPDATE entity SET last_seen=?, name=?, path=coalesce(?,path), "
                "lang=coalesce(?,lang), entity_type=coalesce(?,entity_type) WHERE id=?",
                (ts, name, path, lang, entity_type, row["id"]))
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO entity(kind,symbol_id,name,path,lang,entity_type,attrs,first_seen,last_seen)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (kind, symbol_id, name, path, lang, entity_type,
             json.dumps(attrs) if attrs else None, ts, ts))
        return cur.lastrowid

    # SCIP descriptor suffixes (github.com/sourcegraph/scip): '/' namespace, '#' type,
    # '().' method, '.' term. We follow SCIP's `<scheme> <package> <descriptor>+` grammar
    # SHAPE so ids are parseable/greppable and closer to conformant; the `package` field is
    # the placeholder '.' (SCIP's missing-value token) until a real manager/name/version is
    # resolved (the full-conformance upgrade path noted in the design spec).
    _SCIP_SUFFIX = {"class": "#", "type": "#", "interface": "#", "struct": "#",
                    "enum": "#", "method": "().", "function": "()."}

    @classmethod
    def _default_symbol_id(cls, kind, name, path, entity_type=None):
        if kind == "file":
            return f"wml . {path or name}/"          # file as a namespace descriptor
        if kind == "module":
            return f"wml . {name}/"
        if kind == "referent":
            return f"wml-referent . {name}/"         # external scheme for real-world referents
        # symbol: <path-namespace><name><descriptor-suffix>; v1 resolves by name-within-file
        ns = f"{path}/" if path else ""
        suffix = cls._SCIP_SUFFIX.get((entity_type or "").lower(), ".")
        return f"wml . {ns}{name}{suffix}"

    def _resolve_entity(self, token, default_kind="symbol"):
        """Accept a symbol_id, a name, or a path; create a stub if unknown."""
        cur = self.conn.execute("SELECT id FROM entity WHERE symbol_id=?", (token,))
        r = cur.fetchone()
        if r:
            return r["id"]
        cur = self.conn.execute(
            "SELECT id FROM entity WHERE name=? OR path=? ORDER BY last_seen DESC LIMIT 1",
            (token, token))
        r = cur.fetchone()
        if r:
            return r["id"]
        kind = "file" if ("/" in token or "." in token and default_kind == "file") else default_kind
        path = token if "/" in token else None
        return self.upsert_entity(kind, token, path=path)

    # ── interactions ───────────────────────────────────────────────────────────
    def add_interaction(self, subject, predicate, obj, subj_kind="symbol", obj_kind="symbol") -> int:
        sid = self._resolve_entity(subject, subj_kind)
        oid = self._resolve_entity(obj, obj_kind)
        ts = now()
        cur = self.conn.execute(
            "SELECT id FROM interaction WHERE subject_id=? AND predicate=? AND object_id=?",
            (sid, predicate, oid))
        r = cur.fetchone()
        if r:
            self.conn.execute(
                "UPDATE interaction SET updated_at=?, invalidated_at=NULL WHERE id=?",
                (ts, r["id"]))
            return r["id"]
        cur = self.conn.execute(
            "INSERT INTO interaction(subject_id,predicate,object_id,created_at,updated_at)"
            " VALUES (?,?,?,?,?)", (sid, predicate, oid, ts, ts))
        return cur.lastrowid

    # ── constraints ────────────────────────────────────────────────────────────
    def add_constraint(self, name, kind, message_tmpl, scope_predicate=None,
                       params=None, severity="violation") -> int:
        ts = now()
        cur = self.conn.execute("SELECT id FROM constraint_ WHERE name=?", (name,))
        r = cur.fetchone()
        if r:
            self.conn.execute(
                "UPDATE constraint_ SET kind=?, scope_predicate=?, params=?, severity=?, "
                "message_tmpl=?, updated_at=?, invalidated_at=NULL WHERE id=?",
                (kind, scope_predicate, json.dumps(params or {}), severity, message_tmpl, ts, r["id"]))
            return r["id"]
        cur = self.conn.execute(
            "INSERT INTO constraint_(name,kind,scope_predicate,params,severity,message_tmpl,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (name, kind, scope_predicate, json.dumps(params or {}), severity, message_tmpl, ts, ts))
        return cur.lastrowid

    # ── evidence (the only thing that moves confidence) ─────────────────────────
    def add_evidence(self, fact_kind, fact_id, evidence_kind, ref, polarity="supports",
                     agent=None, activity=None, weight=0.5) -> int:
        if evidence_kind not in ALL_EVIDENCE_KINDS:
            raise ValueError(f"unknown evidence_kind {evidence_kind!r}")
        ts = now()
        try:
            cur = self.conn.execute(
                "INSERT INTO evidence(fact_kind,fact_id,evidence_kind,ref,polarity,agent,activity,weight,observed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (fact_kind, fact_id, evidence_kind, ref, polarity, agent, activity, weight, ts))
            eid = cur.lastrowid
        except sqlite3.IntegrityError:
            # same evidence pointer already present — refresh its weight/time, stay idempotent
            self.conn.execute(
                "UPDATE evidence SET weight=max(weight,?), observed_at=? "
                "WHERE fact_kind=? AND fact_id=? AND evidence_kind=? AND ref=? AND polarity=?",
                (weight, ts, fact_kind, fact_id, evidence_kind, ref, polarity))
            eid = self.conn.execute(
                "SELECT id FROM evidence WHERE fact_kind=? AND fact_id=? AND evidence_kind=? AND ref=? AND polarity=?",
                (fact_kind, fact_id, evidence_kind, ref, polarity)).fetchone()["id"]
        self.derive(fact_kind, fact_id)
        return eid

    # ── confidence derivation (deterministic, pure over evidence) ───────────────
    def derive(self, fact_kind, fact_id):
        table = "interaction" if fact_kind == "interaction" else "constraint_"
        frow = self.conn.execute(f"SELECT * FROM {table} WHERE id=?", (fact_id,)).fetchone()
        if frow is None:
            return
        # A soft-invalidated (stale) fact is frozen — do not re-derive over it.
        if frow["invalidated_at"] is not None:
            return
        ev = self.conn.execute(
            "SELECT evidence_kind, polarity, weight, ref FROM evidence WHERE fact_kind=? AND fact_id=?",
            (fact_kind, fact_id)).fetchall()

        obs_pairs, norm_pairs, refute_w, ranks = [], [], [], [0]
        for e in ev:
            k, pol, w, ref = e["evidence_kind"], e["polarity"], e["weight"], e["ref"]
            if pol == "refutes":
                refute_w.append(w)
                continue
            src = _evidence_source(ref)
            if k in OBSERVATION_KINDS:
                obs_pairs.append((src, w))
            if k in ORACLE_KINDS:
                norm_pairs.append((src, w))
            ranks.append(ENTRENCHMENT_RANK.get(k, 0))

        # grouped fusion: correlated within a source, independent across (dampens over-count)
        observed = grouped_noisy_or(obs_pairs)
        refute_mass = noisy_or(refute_w)
        normative = round(grouped_noisy_or(norm_pairs) * (1.0 - refute_mass), 6)
        entrench = max(ranks)

        open_contra = self.conn.execute(
            "SELECT 1 FROM contradiction_member m JOIN contradiction c ON c.id=m.contradiction_id"
            " WHERE m.fact_kind=? AND m.fact_id=? AND c.resolved_at IS NULL LIMIT 1",
            (fact_kind, fact_id)).fetchone()

        if open_contra or refute_mass > 0:
            validation = "contradicted"
        elif normative >= TAU_VALIDATE:
            validation = "validated"
        else:
            validation = "unverified"

        self.conn.execute(
            f"UPDATE {table} SET observed_conf=?, normative_conf=?, validation=?, "
            f"entrenchment=?, updated_at=? WHERE id=?",
            (observed, normative, validation, entrench, now(), fact_id))

    # ── soft invalidation (stale sweep) ─────────────────────────────────────────
    def mark_stale_for_path(self, path):
        """Interactions anchored on `path` whose evidence no longer includes a fresh
        sighting are soft-invalidated (never hard-deleted). Called by the post hook
        when a file changed and the edge was not re-observed this turn."""
        rows = self.conn.execute(
            "SELECT DISTINCT i.id FROM interaction i JOIN entity e ON e.id=i.subject_id "
            "WHERE e.path=? AND i.invalidated_at IS NULL", (path,)).fetchall()
        ts = now()
        n = 0
        for r in rows:
            self.conn.execute(
                "UPDATE interaction SET invalidated_at=?, validation='stale', updated_at=? WHERE id=?",
                (ts, ts, r["id"]))
            n += 1
        return n

    # ── contradiction detection ─────────────────────────────────────────────────
    def evaluate_constraints(self):
        """Evaluate every live constraint over live interactions. Opens contradiction
        rows (with a proposed fix) for violations; dedups against open ones."""
        opened = []
        cons = self.conn.execute(
            "SELECT * FROM constraint_ WHERE invalidated_at IS NULL").fetchall()
        for c in cons:
            params = json.loads(c["params"] or "{}")
            kind = c["kind"]
            if kind in ("functional", "cardinality"):
                opened += self._eval_cardinality(c, params)
            elif kind == "forbids":
                opened += self._eval_forbids(c, params)
            elif kind == "disjoint":
                opened += self._eval_disjoint(c, params)
            elif kind == "requires":
                opened += self._eval_requires(c, params)
            # type/range/value_set/invariant: reserved for later validators
        return opened

    def _live_interactions(self, predicate=None):
        q = ("SELECT i.*, se.name AS subj_name, se.path AS subj_path, "
             "oe.name AS obj_name, oe.path AS obj_path "
             "FROM interaction i JOIN entity se ON se.id=i.subject_id "
             "JOIN entity oe ON oe.id=i.object_id WHERE i.invalidated_at IS NULL")
        args = []
        if predicate:
            q += " AND i.predicate=?"
            args.append(predicate)
        return self.conn.execute(q, args).fetchall()

    def _eval_cardinality(self, c, params):
        maxc = int(params.get("maxCount", 1))
        pred = c["scope_predicate"]
        groups = {}
        for i in self._live_interactions(pred):
            groups.setdefault(i["subject_id"], []).append(i)
        out = []
        for sid, ivs in groups.items():
            objs = {i["object_id"]: i for i in ivs}
            if len(objs) > maxc:
                members = [("interaction", i["id"]) for i in objs.values()]
                subj = ivs[0]["subj_name"]
                vals = ", ".join(sorted({i["obj_name"] for i in objs.values()}))
                msg = self._render(c["message_tmpl"], subject=subj, values=vals, predicate=pred)
                out += self._open_contradiction(c, members, msg)
        return out

    def _eval_forbids(self, c, params):
        pats = [p.lower() for p in params.get("patterns", [])]
        pred = c["scope_predicate"]
        out = []
        for i in self._live_interactions(pred):
            hay = (i["obj_name"] or "").lower()
            hit = next((p for p in pats if p in hay), None)
            if hit:
                msg = self._render(c["message_tmpl"], subject=i["subj_name"],
                                   object=i["obj_name"], predicate=i["predicate"], matched=hit)
                out += self._open_contradiction(c, [("interaction", i["id"])], msg)
        return out

    def _eval_disjoint(self, c, params):
        pa, pb = params.get("predicate_a"), params.get("predicate_b")
        by_pair = {}
        for i in self._live_interactions():
            if i["predicate"] in (pa, pb):
                by_pair.setdefault((i["subject_id"], i["object_id"]), {})[i["predicate"]] = i
        out = []
        for _, d in by_pair.items():
            if pa in d and pb in d:
                members = [("interaction", d[pa]["id"]), ("interaction", d[pb]["id"])]
                msg = self._render(c["message_tmpl"], subject=d[pa]["subj_name"],
                                   object=d[pa]["obj_name"], predicate_a=pa, predicate_b=pb)
                out += self._open_contradiction(c, members, msg)
        return out

    def _eval_requires(self, c, params):
        """Every subject with scope_predicate must also have `companion` predicate."""
        pred = c["scope_predicate"]
        companion = params.get("companion")
        if not companion:
            return []
        have_companion = {i["subject_id"] for i in self._live_interactions(companion)}
        out = []
        for i in self._live_interactions(pred):
            if i["subject_id"] not in have_companion:
                msg = self._render(c["message_tmpl"], subject=i["subj_name"],
                                   predicate=pred, companion=companion)
                out += self._open_contradiction(c, [("interaction", i["id"])], msg)
        return out

    @staticmethod
    def _render(tmpl, **kw):
        try:
            return tmpl.format(**kw)
        except (KeyError, IndexError):
            return tmpl

    def _open_contradiction(self, c, members, message):
        dedup = f"{c['id']}:" + ",".join(sorted(f"{k}#{i}" for k, i in members))
        exists = self.conn.execute(
            "SELECT id FROM contradiction WHERE dedup_key=? AND resolved_at IS NULL",
            (dedup,)).fetchone()
        if exists:
            return []
        fix = self._propose_fix(members, c)
        cur = self.conn.execute(
            "INSERT INTO contradiction(constraint_id,detected_by,severity,message,proposed_fix,detected_at,dedup_key)"
            " VALUES (?,?,?,?,?,?,?)",
            (c["id"], "constraint_eval", c["severity"], message, fix, now(), dedup))
        cid = cur.lastrowid
        for fk, fi in members:
            self.conn.execute(
                "INSERT OR IGNORE INTO contradiction_member(contradiction_id,fact_kind,fact_id) VALUES (?,?,?)",
                (cid, fk, fi))
        # flip members to 'contradicted'
        for fk, fi in members:
            self.derive(fk, fi)
        return [cid]

    def _propose_fix(self, members, c):
        """Rank members by (entrenchment asc, normative_conf asc); the least-entrenched
        / least-trusted is the thing to change (AGM minimal change)."""
        detailed = []
        for fk, fi in members:
            table = "interaction" if fk == "interaction" else "constraint_"
            r = self.conn.execute(f"SELECT * FROM {table} WHERE id=?", (fi,)).fetchone()
            if fk == "interaction":
                se = self.conn.execute("SELECT * FROM entity WHERE id=?", (r["subject_id"],)).fetchone()
                oe = self.conn.execute("SELECT * FROM entity WHERE id=?", (r["object_id"],)).fetchone()
                label = f"{se['name']} {r['predicate']} {oe['name']}"
                loc = se["path"] or oe["path"] or "?"
            else:
                label, loc = r["name"], "constraint"
            detailed.append((r["entrenchment"], r["normative_conf"], label, loc))
        detailed.sort(key=lambda t: (t[0], t[1]))
        weakest = detailed[0]
        return (f"Change the least-supported member: `{weakest[2]}` (at {weakest[3]}; "
                f"entrenchment={weakest[0]}, normative_conf={weakest[1]}). "
                f"Constraint `{c['name']}` [{c['severity']}].")

    def resolve_contradiction(self, cid, resolution):
        self.conn.execute(
            "UPDATE contradiction SET resolved_at=?, resolution=? WHERE id=?",
            (now(), resolution, cid))
        for m in self.conn.execute(
                "SELECT fact_kind, fact_id FROM contradiction_member WHERE contradiction_id=?",
                (cid,)).fetchall():
            self.derive(m["fact_kind"], m["fact_id"])

    # ── retrieval (pre-call hook + `wm query`) ──────────────────────────────────
    def _reachable_nodes(self, seed_ids, max_depth=1):
        """Entity ids within `max_depth` hops of any seed, over LIVE interactions, either
        direction. Depth-capped recursive CTE; UNION dedups so cycles can't loop forever."""
        if not seed_ids:
            return set()
        seed_union = " UNION ALL ".join("SELECT ? AS node, 0 AS depth" for _ in seed_ids)
        sql = f"""
        WITH RECURSIVE reach(node, depth) AS (
          {seed_union}
          UNION
          SELECT CASE WHEN e.subject_id = r.node THEN e.object_id ELSE e.subject_id END,
                 r.depth + 1
          FROM reach r JOIN interaction e
            ON (e.subject_id = r.node OR e.object_id = r.node)
           AND e.invalidated_at IS NULL
          WHERE r.depth < ?
        )
        SELECT DISTINCT node FROM reach
        """
        rows = self.conn.execute(sql, list(seed_ids) + [max_depth]).fetchall()
        return {row["node"] for row in rows}

    def query_touching(self, token, budget=25, hops=1):
        """Return facts in the `hops`-hop neighborhood of a file/symbol, partitioned
        validated/unverified/contradicted. `hops=1` is the direct-incident neighborhood;
        `hops=2` pulls one more ring via the recursive-CTE walk (see `_reachable_nodes`)."""
        ent_ids = self._entities_for_token(token)
        result = {"validated": [], "unverified": [], "contradicted": [], "token": token}
        if not ent_ids:
            return result
        reached = self._reachable_nodes(ent_ids, max_depth=hops) or set(ent_ids)
        qmarks = ",".join("?" * len(reached))
        reached_list = list(reached)
        # induced-subgraph edges: both endpoints within the neighborhood, ranked by trust
        rows = self.conn.execute(
            f"SELECT i.*, se.name AS s, oe.name AS o FROM interaction i "
            f"JOIN entity se ON se.id=i.subject_id JOIN entity oe ON oe.id=i.object_id "
            f"WHERE i.invalidated_at IS NULL AND i.subject_id IN ({qmarks}) AND i.object_id IN ({qmarks}) "
            f"ORDER BY i.normative_conf DESC", reached_list + reached_list).fetchall()
        for r in rows:
            item = {"fact": f"{r['s']} {r['predicate']} {r['o']}",
                    "observed": r["observed_conf"], "normative": r["normative_conf"]}
            bucket = r["validation"] if r["validation"] in result else "unverified"
            if len(result[bucket]) < budget:
                result[bucket].append(item)
        # open contradictions whose message mentions the token or a touched entity
        contras = self.conn.execute(
            "SELECT c.* FROM contradiction c WHERE c.resolved_at IS NULL "
            "ORDER BY c.detected_at DESC LIMIT 200").fetchall()
        names = {self.conn.execute("SELECT name FROM entity WHERE id=?", (e,)).fetchone()["name"]
                 for e in ent_ids}
        for c in contras:
            if token in c["message"] or any(n in c["message"] for n in names):
                result["contradicted"].append(
                    {"contradiction": c["message"], "fix": c["proposed_fix"], "id": c["id"],
                     "severity": c["severity"]})
        return result

    def _entities_for_token(self, token):
        ids = [r["id"] for r in self.conn.execute(
            "SELECT id FROM entity WHERE symbol_id=? OR name=? OR path=?",
            (token, token, token)).fetchall()]
        if ids:
            return ids
        if self.has_fts:
            try:
                q = '"' + token.replace('"', '""') + '"'
                return [r["id"] for r in self.conn.execute(
                    "SELECT rowid AS id FROM entity_fts WHERE entity_fts MATCH ? LIMIT 50", (q,)).fetchall()]
            except sqlite3.OperationalError:
                pass
        like = f"%{token}%"
        return [r["id"] for r in self.conn.execute(
            "SELECT id FROM entity WHERE name LIKE ? OR path LIKE ? LIMIT 50", (like, like)).fetchall()]

    # ── repo-wide build / seeding (deterministic, observation-only) ─────────────
    def _prune_vanished_build_edges(self, root):
        """Soft-invalidate build-origin edges whose anchored file no longer exists on disk
        (deleted/renamed). Only edges whose evidence is EXCLUSIVELY build-origin are touched —
        any edge the agent has observed or validated (i.e. carries non-`build` evidence) is
        left alone, so pruning can never destroy agent work. Never hard-deletes (soft-invalidate
        → `stale`), and never prunes an edge merely because a file was skipped by the scan (it
        checks the filesystem, not the scan set)."""
        rows = self.conn.execute(
            "SELECT i.id, se.path AS s_path, oe.path AS o_path "
            "FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id "
            "WHERE i.invalidated_at IS NULL "
            "  AND EXISTS (SELECT 1 FROM evidence e WHERE e.fact_kind='interaction' "
            "              AND e.fact_id=i.id AND e.agent='build') "
            "  AND NOT EXISTS (SELECT 1 FROM evidence e WHERE e.fact_kind='interaction' "
            "                  AND e.fact_id=i.id AND COALESCE(e.agent,'') <> 'build')"
        ).fetchall()
        ts = now()
        pruned = 0
        for r in rows:
            s_gone = r["s_path"] and not os.path.exists(os.path.join(root, r["s_path"]))
            o_gone = r["o_path"] and not os.path.exists(os.path.join(root, r["o_path"]))
            if s_gone or o_gone:
                self.conn.execute(
                    "UPDATE interaction SET invalidated_at=?, validation='stale', updated_at=? WHERE id=?",
                    (ts, ts, r["id"]))
                pruned += 1
        return pruned

    def build_from_repo(self, root=".", max_files=5000, prune=False):
        """Walk a repository once and seed the model: register source files as entities and
        record STRUCTURAL interactions with LANGUAGE-AWARE extraction — local imports/includes
        as file→file edges (`imports`/`includes`/`references`) and external dependencies as
        file→referent `depends_on` edges. Covers Python, Ruby, JavaScript/TypeScript, Go, Rust,
        and the DevOps stack (Dockerfile, docker-compose/K8s, GitHub Actions, Terraform, Make,
        shell). Everything is recorded as OBSERVATION only — observed_conf rises, but
        normative_conf stays 0 and validation stays 'unverified' (a declared dependency is a
        sighting, never a correctness judgement). Idempotent; re-running refreshes. No invented
        facts: a file→file edge is added only when the target resolves to a real scanned file;
        external `depends_on` targets are literal declarations in the source (FROM/uses/import)."""
        root = os.path.abspath(root)

        # 1) enumerate registerable source files (skip vendored/build dirs, binaries, huge files)
        collected = []
        for dirpath, dirs, fnames in os.walk(root):
            dirs[:] = [d for d in dirs if d not in BUILD_IGNORE_DIRS]
            for fn in fnames:
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                lang = self._detect_lang(rel)
                if lang is None:
                    continue
                ap = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(ap) > BUILD_MAX_BYTES:
                        continue
                except OSError:
                    continue
                collected.append((rel, ap, lang))
        collected.sort()
        dropped = 0
        if len(collected) > max_files:
            dropped = len(collected) - max_files
            collected = collected[:max_files]

        # Snapshot row counts so we can report ADDED-vs-UPDATED. Re-running never
        # duplicates: entities key on symbol_id, interactions on (subject,predicate,object),
        # evidence on (fact,kind,ref,polarity) — every write is an upsert, nothing is deleted.
        def _n(t):
            return self.conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        before_e, before_i, before_ev = _n("entity"), _n("interaction"), _n("evidence")

        relset = {rel for rel, _, _ in collected}
        basename_index = {}
        for rel, _, _ in collected:
            basename_index.setdefault(os.path.basename(rel), []).append(rel)
        ctx = {
            "relset": relset,
            "basename": basename_index,
            "mod": self._python_module_index(relset),
            "java": self._java_class_index(collected),   # FQN -> rel (reads `package` decls)
        }

        # 2) register entities
        for rel, _ap, lang in collected:
            self.upsert_entity("file", os.path.basename(rel), path=rel, lang=lang)

        # 3) structural edges (language-aware)
        edges = 0
        for rel, ap, lang in collected:
            try:
                text = open(ap, "r", errors="replace").read()
            except OSError:
                continue
            edges += self._seed_edges(rel, text, lang, ctx)

        # Optional: soft-invalidate build-origin edges for files that vanished (opt-in).
        pruned = self._prune_vanished_build_edges(root) if prune else 0

        stats = self.consolidate()
        stats["build"] = {
            "files_registered": len(collected), "edges": edges, "dropped_files": dropped,
            # deltas prove re-runs only add/update: on an unchanged repo both are 0.
            "entities_added": _n("entity") - before_e,
            "entities_updated": len(collected) - (_n("entity") - before_e),
            "interactions_added": _n("interaction") - before_i,
            "evidence_added": _n("evidence") - before_ev,
            "pruned_stale_edges": pruned,
            "root": root,
        }
        if dropped:
            print(f"world-model build: capped at {max_files} files; {dropped} not scanned "
                  f"(raise --max-files to include them)", file=sys.stderr)
        return stats

    @staticmethod
    def _python_module_index(relset):
        """dotted-module -> rel path, so `import a.b` can resolve to a real file."""
        idx = {}
        for rel in relset:
            if not rel.endswith(".py"):
                continue
            parts = rel[:-3].split(os.sep)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                idx[".".join(parts)] = rel
        return idx

    @staticmethod
    def _java_class_index(collected):
        """fully-qualified Java class name -> rel path, from each file's `package` decl, so
        `import com.foo.Bar;` resolves to the file defining that class (source root agnostic)."""
        idx = {}
        for rel, ap, lang in collected:
            if lang != "java":
                continue
            try:
                head = open(ap, "r", errors="replace").read(4096)
            except OSError:
                continue
            m = re.search(r"^\s*package\s+([\w.]+)\s*;", head, re.M)
            cls = os.path.splitext(os.path.basename(rel))[0]
            fqn = f"{m.group(1)}.{cls}" if m else cls
            idx[fqn] = rel
        return idx

    @staticmethod
    def _detect_lang(rel):
        """Language tag for a repo file, by special filename / path / extension (or None)."""
        base = os.path.basename(rel).lower()
        norm = "/" + rel.replace(os.sep, "/")
        if base in BUILD_SPECIAL_FILENAMES:
            return BUILD_SPECIAL_FILENAMES[base]
        if base.startswith("dockerfile.") or base.startswith("containerfile."):
            return "dockerfile"
        ext = os.path.splitext(base)[1]
        if ext in (".yml", ".yaml"):
            if "/.github/workflows/" in norm:
                return "github-actions"
            if base.startswith("docker-compose") or base.startswith("compose"):
                return "compose"
        return BUILD_LANG_BY_EXT.get(ext)

    def _seed_edges(self, rel, text, lang, ctx):
        """Run the language extractor for `lang`, resolve each edge to a real file or an
        external dependency referent, and record it (observation-only)."""
        counters = {"deps": 0}
        edges = 0
        extractor = BUILD_EXTRACTORS.get(lang)
        if extractor:
            seen = set()
            for predicate, target, kind in extractor(text, rel):
                key = (predicate, target, kind)
                if key in seen:
                    continue
                seen.add(key)
                edges += self._link(rel, predicate, target, kind, ctx, counters)
        # non-code files also get the generic file-reference scan (docs/config/scripts)
        if lang not in CODE_LANGS:
            edges += self._seed_file_references(rel, text, ctx["relset"], ctx["basename"])
        return edges

    def _resolve_target_file(self, rel, target, kind, ctx):
        """Resolve an import/reference target to a real scanned file, or None."""
        relset, basename_index = ctx["relset"], ctx["basename"]
        if kind == "java":
            cand = ctx["java"].get(target)
            return cand if cand and cand != rel else None
        if kind in ("module", "module_local"):
            cand = ctx["mod"].get(target)
            if cand and cand != rel:
                return cand
            base = target.replace(".", "/")
            for e in (".py", ".rb"):
                for p in (base + e, os.path.join(base, "__init__" + e)):
                    if p in relset and p != rel:
                        return p
            return None
        # kind == 'file': a path, possibly relative to the importing file
        importer_dir = os.path.dirname(rel)
        t = target.strip().strip("'\"")
        bases = [os.path.normpath(os.path.join(importer_dir, t)),
                 os.path.normpath(t.lstrip("/"))]
        # Prefer the importer's OWN extension so a Rust `mod helper` resolves to helper.rs,
        # not a sibling helper.rb, when same-named files of different languages coexist.
        own = os.path.splitext(rel)[1].lower()
        default = [".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".rb", ".rs",
                   ".go", ".py", ".sh", ".bash", ".tf", ".mk",
                   ".h", ".hpp", ".hh", ".c", ".cpp", ".cc", ".php", ".java"]
        exts = [""] + ([own] if own in default else []) + [e for e in default if e != own]
        idx_files = ("index.js", "index.ts", "mod.rs", "__init__.py")
        for b in bases:
            for e in exts:
                c = b + e
                if c in relset and c != rel:
                    return c
            for idx in idx_files:
                c = os.path.join(b, idx)
                if c in relset and c != rel:
                    return c
        name = os.path.basename(t)
        if "." in name and name in basename_index and len(basename_index[name]) == 1 \
                and basename_index[name][0] != rel:
            return basename_index[name][0]
        return None

    def _link(self, rel, predicate, target, kind, ctx, counters):
        """Create one structural edge: a file→file edge if the target resolves to a scanned
        file, else (for dependency kinds) a file→referent `depends_on` edge."""
        target = (target or "").strip()
        if not target:
            return 0
        tgt_file = None
        if kind in BUILD_LOCAL_KINDS:
            tgt_file = self._resolve_target_file(rel, target, kind, ctx)
        if tgt_file:
            iid = self.add_interaction(rel, predicate, tgt_file, subj_kind="file", obj_kind="file")
            self.add_evidence("interaction", iid, "static", f"{rel}: {predicate} {target}",
                              agent="build", activity="build_from_repo", weight=0.6)
            return 1
        if kind not in BUILD_EXTERNAL_KINDS:       # module_local / unresolved file → skip
            return 0
        name = _external_name(kind, target)
        if not name or counters["deps"] >= BUILD_MAX_REFS_PER_FILE:
            return 0
        ref_id = self.upsert_entity("referent", name)
        rsym = self.conn.execute("SELECT symbol_id FROM entity WHERE id=?", (ref_id,)).fetchone()["symbol_id"]
        iid = self.add_interaction(rel, "depends_on", rsym, subj_kind="file", obj_kind="referent")
        self.add_evidence("interaction", iid, "static", f"{rel} depends_on {name}",
                          agent="build", activity="build_from_repo", weight=0.5)
        counters["deps"] += 1
        return 1

    def _seed_file_references(self, rel, text, relset, basename_index):
        """Non-Python files: link to another repo file they mention by path or unique basename
        (shell hook -> script, Makefile -> gate, SKILL.md -> asset, ...)."""
        import re as _re
        n = 0
        seen = set()
        for m in _re.findall(r"[\w./$\-]*[\w\-]+\.(?:py|sh|bash|js|ts|md|json|ya?ml|toml|sql|mk)", text):
            name = os.path.basename(m.replace("$KIT_HOME/", "").replace("${CLAUDE_PROJECT_DIR}/", ""))
            tgt = None
            # exact repo-relative path?
            cand_rel = os.path.normpath(m).lstrip("./")
            if cand_rel in relset and cand_rel != rel:
                tgt = cand_rel
            elif name in basename_index and len(basename_index[name]) == 1 and basename_index[name][0] != rel:
                tgt = basename_index[name][0]   # unambiguous basename only (avoid false links)
            if tgt and tgt not in seen:
                seen.add(tgt)
                iid = self.add_interaction(rel, "references", tgt, subj_kind="file", obj_kind="file")
                self.add_evidence("interaction", iid, "static", f"{rel} -> {name}",
                                  agent="build", activity="build_from_repo", weight=0.5)
                n += 1
                if n >= BUILD_MAX_REFS_PER_FILE:
                    break
        return n

    # ── consolidation (Stop hook) ───────────────────────────────────────────────
    def consolidate(self):
        """Full pass: re-derive live facts, evaluate constraints, refresh nothing else.
        Returns a stats dict."""
        for r in self.conn.execute("SELECT id FROM interaction WHERE invalidated_at IS NULL").fetchall():
            self.derive("interaction", r["id"])
        for r in self.conn.execute("SELECT id FROM constraint_ WHERE invalidated_at IS NULL").fetchall():
            self.derive("constraint", r["id"])
        opened = self.evaluate_constraints()
        self.conn.commit()
        return {"contradictions_opened": len(opened), **self.stats()}

    def stats(self):
        def counts(table):
            rows = self.conn.execute(
                f"SELECT validation, COUNT(*) n FROM {table} WHERE invalidated_at IS NULL GROUP BY validation"
            ).fetchall()
            d = {v: 0 for v in VALIDATIONS}
            for r in rows:
                d[r["validation"]] = r["n"]
            return d
        open_contra = self.conn.execute(
            "SELECT COUNT(*) n FROM contradiction WHERE resolved_at IS NULL").fetchone()["n"]
        return {
            "entities": self.conn.execute("SELECT COUNT(*) n FROM entity").fetchone()["n"],
            "interactions": counts("interaction"),
            "constraints": counts("constraint_"),
            "open_contradictions": open_contra,
        }

    def project_rules(self, budget=6):
        """Validated, project-wide constraints (not tied to one file) that a coding agent
        should honor whenever it edits code — surfaced in every pre-call so a rule stated
        once is enforced everywhere, not just where an edge already exists."""
        rows = self.conn.execute(
            "SELECT name, kind, scope_predicate, params, severity, message_tmpl, normative_conf "
            "FROM constraint_ WHERE invalidated_at IS NULL AND validation='validated' "
            "ORDER BY CASE severity WHEN 'violation' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, "
            "normative_conf DESC LIMIT ?", (budget,)).fetchall()
        out = []
        for r in rows:
            params = r["params"] or "{}"
            pats = ""
            try:
                p = json.loads(params)
                if p.get("patterns"):
                    pats = f" — forbids: {', '.join(p['patterns'])}"
                elif p.get("maxCount") is not None:
                    pats = f" — max {p['maxCount']} per {r['scope_predicate']}"
            except json.JSONDecodeError:
                pass
            out.append({"name": r["name"], "kind": r["kind"], "severity": r["severity"],
                        "scope": r["scope_predicate"], "detail": pats})
        return out

    def precall(self, tokens, budget=8):
        """Markdown summary for the pre-call hook: what we believe about the files/
        symbols about to be touched, partitioned ✓validated / ?unverified / ✗contradicted,
        plus validated project-wide rules that apply to any edit."""
        seen = set()
        val, unv, con = [], [], []
        for tok in tokens:
            res = self.query_touching(tok, budget=budget)
            for x in res["validated"]:
                if x["fact"] not in seen:
                    seen.add(x["fact"]); val.append(x)
            for x in res["unverified"]:
                if x["fact"] not in seen:
                    seen.add(x["fact"]); unv.append(x)
            for x in res["contradicted"]:
                key = x.get("contradiction") or x.get("fact")
                if key not in seen:
                    seen.add(key); con.append(x)
        rules = self.project_rules()
        if not (val or unv or con or rules):
            return ""
        L = [f"world-model — what we already believe about: {', '.join(tokens)}", ""]
        if rules:
            L.append("◆ VALIDATED PROJECT RULES (apply to any edit):")
            for r in rules:
                L.append(f"  - [{r['severity']}] {r['name']} ({r['kind']}{r['detail']})")
        if con:
            L.append("✗ CONTRADICTED (resolve before trusting this code):")
            for x in con[:budget]:
                if "contradiction" in x:
                    L.append(f"  - [{x.get('severity','?')}] {x['contradiction']}")
                    if x.get("fix"):
                        L.append(f"      fix: {x['fix']}")
                else:
                    L.append(f"  - {x['fact']} (normative={x['normative']})")
        if unv:
            L.append("? UNVERIFIED (observed in code, NOT oracle-backed — do not assume correct):")
            for x in unv[:budget]:
                L.append(f"  - {x['fact']}  (observed={x['observed']}, normative={x['normative']})")
        if val:
            L.append("✓ VALIDATED (backed by tests/docs/human):")
            for x in val[:budget]:
                L.append(f"  - {x['fact']}  (normative={x['normative']})")
        total = len(val) + len(unv) + len(con)
        shown = min(len(con), budget) + min(len(unv), budget) + min(len(val), budget)
        if total > shown:
            L.append(f"  … {total - shown} more not shown (budget {budget}/partition).")
        return "\n".join(L) + "\n"

    def digest(self):
        s = self.stats()
        lines = ["# world-model digest", "", f"_generated {now()}_", ""]
        iv = s["interactions"]
        lines.append(f"- interactions: {iv['validated']} validated · {iv['unverified']} unverified "
                     f"· {iv['contradicted']} contradicted · {iv['stale']} stale")
        lines.append(f"- open contradictions: {s['open_contradictions']}")
        contras = self.conn.execute(
            "SELECT * FROM contradiction WHERE resolved_at IS NULL ORDER BY "
            "CASE severity WHEN 'violation' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, detected_at DESC "
            "LIMIT 20").fetchall()
        if contras:
            lines += ["", "## Open contradictions (fix these to raise normative correctness)"]
            for c in contras:
                lines.append(f"- **[{c['severity']}]** {c['message']}")
                if c["proposed_fix"]:
                    lines.append(f"  - fix: {c['proposed_fix']}")
        unv = self.conn.execute(
            "SELECT i.*, se.name s, oe.name o FROM interaction i "
            "JOIN entity se ON se.id=i.subject_id JOIN entity oe ON oe.id=i.object_id "
            "WHERE i.validation='unverified' AND i.invalidated_at IS NULL "
            "ORDER BY i.observed_conf DESC LIMIT 15").fetchall()
        if unv:
            lines += ["", "## Newest unverified (observed, not oracle-backed — treat with suspicion)"]
            for r in unv:
                lines.append(f"- {r['s']} {r['predicate']} {r['o']}  "
                             f"(observed={r['observed_conf']}, normative={r['normative_conf']})")
        return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _db_path(args):
    return args.db or os.environ.get("WM_DB") or os.path.join(".world-model", "model.db")


def _parse_evidence_flag(val):
    """--by test:tests/x.py::t  ->  ('test', 'tests/x.py::t')"""
    if ":" in val:
        kind, ref = val.split(":", 1)
    else:
        kind, ref = "agent_assert", val
    return kind, ref


def _anchor_from_evidence(wm, iid, evidence):
    """If evidence is a file:line / path, record it as the subject entity's path so
    proposed fixes and pre-call retrieval point at a real location."""
    if not evidence:
        return
    file_part = evidence.split(":", 1)[0]
    if "/" in file_part or "." in file_part:
        sid = wm.conn.execute("SELECT subject_id FROM interaction WHERE id=?", (iid,)).fetchone()["subject_id"]
        wm.conn.execute("UPDATE entity SET path=coalesce(path,?) WHERE id=?", (file_part, sid))


def cmd_observe(wm, a):
    iid = wm.add_interaction(a.subject, a.predicate, a.object,
                             subj_kind=a.subj_kind, obj_kind=a.obj_kind)
    ref = a.evidence or f"{a.subject}->{a.object}"
    kind = "file_loc" if (a.evidence and (":" in a.evidence or "/" in a.evidence)) else "agent_assert"
    _anchor_from_evidence(wm, iid, a.evidence)
    wm.add_evidence("interaction", iid, kind, ref, weight=a.conf, agent="wm-cli", activity="observe")
    wm.conn.commit()
    print(json.dumps({"interaction_id": iid}))


def cmd_constraint(wm, a):
    params = json.loads(a.params) if a.params else {}
    cid = wm.add_constraint(a.name, a.kind, a.message, scope_predicate=a.predicate,
                            params=params, severity=a.severity)
    if a.assert_valid:
        wm.add_evidence("constraint", cid, "human", "asserted", weight=0.9, agent="human")
    wm.conn.commit()
    print(json.dumps({"constraint_id": cid}))


def _select_interaction(wm, sel):
    parts = sel.split(",") if "," in sel else sel.split(":")
    if len(parts) == 3:
        s, p, o = parts
        sid = wm._resolve_entity(s)
        oid = wm._resolve_entity(o)
        r = wm.conn.execute(
            "SELECT id FROM interaction WHERE subject_id=? AND predicate=? AND object_id=?",
            (sid, p, oid)).fetchone()
        return r["id"] if r else None
    return int(sel)  # bare id


def cmd_validate(wm, a):
    iid = _select_interaction(wm, a.interaction)
    if iid is None:
        print(json.dumps({"error": "interaction not found"})); return 1
    kind, ref = _parse_evidence_flag(a.by)
    if kind not in ORACLE_KINDS:
        print(json.dumps({"error": f"validate needs an oracle kind {sorted(ORACLE_KINDS)}, got {kind}"})); return 1
    wm.add_evidence("interaction", iid, kind, ref, polarity="supports",
                    agent="wm-cli", activity="validate", weight=a.weight)
    wm.conn.commit()
    r = wm.conn.execute("SELECT normative_conf, validation FROM interaction WHERE id=?", (iid,)).fetchone()
    print(json.dumps({"interaction_id": iid, "normative_conf": r["normative_conf"], "validation": r["validation"]}))


def cmd_refute(wm, a):
    iid = _select_interaction(wm, a.interaction)
    if iid is None:
        print(json.dumps({"error": "interaction not found"})); return 1
    kind, ref = _parse_evidence_flag(a.by)
    wm.add_evidence("interaction", iid, kind, ref, polarity="refutes",
                    agent="wm-cli", activity="refute", weight=a.weight)
    wm.conn.commit()
    r = wm.conn.execute("SELECT normative_conf, validation FROM interaction WHERE id=?", (iid,)).fetchone()
    print(json.dumps({"interaction_id": iid, "normative_conf": r["normative_conf"], "validation": r["validation"]}))


def cmd_map(wm, a):
    ref_id = wm.upsert_entity("referent", a.to)
    # realizes-edge from the symbol to the referent; reuse add_interaction via symbol_id
    iid = wm.add_interaction(a.symbol, "realizes",
                             wm.conn.execute("SELECT symbol_id FROM entity WHERE id=?", (ref_id,)).fetchone()["symbol_id"],
                             obj_kind="referent")
    wm.add_evidence("interaction", iid, "agent_assert", f"{a.symbol}->{a.to}", agent="wm-cli", activity="map")
    wm.conn.commit()
    print(json.dumps({"interaction_id": iid, "referent": a.to}))


def cmd_contradictions(wm, a):
    if a.touching:
        res = wm.query_touching(a.touching)
        print(json.dumps(res["contradicted"], indent=2)); return
    q = "SELECT * FROM contradiction"
    if a.open:
        q += " WHERE resolved_at IS NULL"
    q += " ORDER BY detected_at DESC LIMIT 100"
    out = [{"id": c["id"], "severity": c["severity"], "message": c["message"],
            "fix": c["proposed_fix"], "resolved": c["resolved_at"]} for c in wm.conn.execute(q).fetchall()]
    print(json.dumps(out, indent=2))


def cmd_resolve(wm, a):
    wm.resolve_contradiction(a.id, a.as_)
    wm.conn.commit()
    print(json.dumps({"resolved": a.id, "resolution": a.as_}))


def cmd_query(wm, a):
    print(json.dumps(wm.query_touching(a.touching), indent=2))


def cmd_precall(wm, a):
    sys.stdout.write(wm.precall(a.touching, budget=a.budget))


def cmd_touch(wm, a):
    """Register a touched file (deterministic post-call skeleton). Never invents edges."""
    eid = wm.upsert_entity("file", a.path, path=a.path)
    wm.conn.commit()
    print(json.dumps({"entity_id": eid, "path": a.path}))


def cmd_build(wm, a):
    """Repo-wide world building: deterministic structural scan, observation-only."""
    stats = wm.build_from_repo(a.path, max_files=a.max_files, prune=a.prune)
    wm.conn.commit()
    b = stats["build"]
    print(json.dumps(stats, indent=2))
    extra = f"; pruned {b['pruned_stale_edges']} vanished-file edge(s)" if a.prune else ""
    print(f"built: {b['files_registered']} files, {b['edges']} structural edges "
          f"(all observed/unverified — validate with tests/docs to raise correctness){extra}",
          file=sys.stderr)


def cmd_stats(wm, a):
    print(json.dumps(wm.stats(), indent=2))


def cmd_consolidate(wm, a):
    print(json.dumps(wm.consolidate(), indent=2))


def cmd_digest(wm, a):
    sys.stdout.write(wm.digest())


def cmd_export(wm, a):
    out = {"entities": [dict(r) for r in wm.conn.execute("SELECT * FROM entity").fetchall()],
           "interactions": [dict(r) for r in wm.conn.execute("SELECT * FROM interaction").fetchall()],
           "constraints": [dict(r) for r in wm.conn.execute("SELECT * FROM constraint_").fetchall()],
           "contradictions": [dict(r) for r in wm.conn.execute("SELECT * FROM contradiction").fetchall()]}
    print(json.dumps(out, indent=2))


def build_parser():
    p = argparse.ArgumentParser(prog="wm", description="coding-agent world model (SQLite)")
    p.add_argument("--db", help="path to model.db (default .world-model/model.db or $WM_DB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("observe", help="record an interaction (observed, unverified)")
    o.add_argument("subject"); o.add_argument("predicate"); o.add_argument("object")
    o.add_argument("--evidence"); o.add_argument("--conf", type=float, default=0.6)
    o.add_argument("--subj-kind", dest="subj_kind", default="symbol")
    o.add_argument("--obj-kind", dest="obj_kind", default="symbol")
    o.set_defaults(func=cmd_observe)

    c = sub.add_parser("constraint", help="assert a constraint")
    c.add_argument("name"); c.add_argument("kind"); c.add_argument("message")
    c.add_argument("--predicate"); c.add_argument("--params")
    c.add_argument("--severity", default="violation")
    c.add_argument("--assert-valid", dest="assert_valid", action="store_true",
                   help="attach human evidence that the constraint itself is desired")
    c.set_defaults(func=cmd_constraint)

    v = sub.add_parser("validate", help="raise normative confidence via oracle evidence")
    v.add_argument("interaction", help="'subj,pred,obj' or interaction id")
    v.add_argument("--by", required=True, help="test:<id> | ci:<run> | doc:<path> | human")
    v.add_argument("--weight", type=float, default=0.8)
    v.set_defaults(func=cmd_validate)

    r = sub.add_parser("refute", help="record refuting evidence (→ contradicted)")
    r.add_argument("interaction"); r.add_argument("--by", required=True)
    r.add_argument("--weight", type=float, default=0.8)
    r.set_defaults(func=cmd_refute)

    m = sub.add_parser("map", help="map a code symbol to a real-world referent")
    m.add_argument("symbol"); m.add_argument("--to", required=True)
    m.set_defaults(func=cmd_map)

    cc = sub.add_parser("contradictions", help="list contradictions + proposed fixes")
    cc.add_argument("--open", action="store_true"); cc.add_argument("--touching")
    cc.set_defaults(func=cmd_contradictions)

    rs = sub.add_parser("resolve", help="resolve a contradiction")
    rs.add_argument("id", type=int)
    rs.add_argument("--as", dest="as_", required=True,
                    choices=["retract", "supersede", "accept_both", "fixed_code", "defer"])
    rs.set_defaults(func=cmd_resolve)

    q = sub.add_parser("query", help="what the pre-call hook shows for a file/symbol")
    q.add_argument("--touching", required=True); q.set_defaults(func=cmd_query)

    pc = sub.add_parser("precall", help="markdown pre-call summary for touched files/symbols")
    pc.add_argument("touching", nargs="+", help="file paths and/or symbol names")
    pc.add_argument("--budget", type=int, default=8)
    pc.set_defaults(func=cmd_precall)

    tp = sub.add_parser("touch", help="register a touched file (post-call skeleton)")
    tp.add_argument("path"); tp.set_defaults(func=cmd_touch)

    bd = sub.add_parser("build", help="repo-wide seed: register files + structural edges (observation-only)")
    bd.add_argument("path", nargs="?", default=".", help="repo root to scan (default: cwd)")
    bd.add_argument("--max-files", dest="max_files", type=int, default=5000,
                    help="cap files scanned; surplus is logged, never silently dropped")
    bd.add_argument("--prune", action="store_true",
                    help="soft-invalidate build-origin edges for files that vanished "
                         "(never touches agent-observed/validated facts; never hard-deletes)")
    bd.set_defaults(func=cmd_build)

    sub.add_parser("stats", help="validated/unverified/contradicted counts").set_defaults(func=cmd_stats)
    sub.add_parser("consolidate", help="re-derive + evaluate constraints (Stop hook)").set_defaults(func=cmd_consolidate)
    sub.add_parser("digest", help="markdown digest").set_defaults(func=cmd_digest)
    sub.add_parser("export", help="dump the whole model as JSON").set_defaults(func=cmd_export)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    wm = WorldModel(_db_path(args))
    try:
        rc = args.func(wm, args)
        return rc or 0
    finally:
        wm.close()


if __name__ == "__main__":
    sys.exit(main())
