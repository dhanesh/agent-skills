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
import shlex
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

ENTITY_KINDS = ("symbol", "file", "module", "referent")


class OntologyError(ValueError):
    """A write violated the predicate ontology — unknown verb, or a subject/object
    whose entity kind falls outside the predicate's domain/range. The message always
    names the allowed set so a probabilistic caller can self-correct and retry."""


# ─────────────────────────────────────────────────────────────────────────────
# Ontology — the predicate vocabulary, with RDFS-style domain/range per verb.
#
# The neuro-symbolic guardrail: the agent side of the system is probabilistic
# (markers, `wm observe`), so every triple is validated against this vocabulary
# BEFORE it enters the ledger — an unknown verb or a semantically impossible
# pairing (a referent `imports` a file) is rejected with the allowed set, never
# silently stored. Deterministic emitters (build scanner, exec observer) only
# use verbs from this table, so validation costs them nothing.
#
# `domain`/`range` are ORDERED: the first kind doubles as the stub kind when a
# triple names an entity the model has never seen (rdfs:domain/range used as
# type inference — `x depends_on stripe/api` stubs the object as a referent,
# not a mis-typed file). A known entity's recorded kind is authoritative and is
# what domain/range are checked against.
#
# Projects extend the vocabulary DELIBERATELY via `<db-dir>/ontology.json`
# ({"verb": {"domain": [...], "range": [...]}}) or `wm ontology --add` —
# extension is an explicit act, never a side effect of a marker.
# ─────────────────────────────────────────────────────────────────────────────
ONTOLOGY_CORE = {
    # structural (build scanner)
    "imports":    {"domain": ("file", "module"), "range": ("module", "file")},
    "includes":   {"domain": ("file",), "range": ("file",)},
    "references": {"domain": ("file",), "range": ("file",)},
    "depends_on": {"domain": ("file", "module"), "range": ("referent",)},
    "provides":   {"domain": ("file", "module"), "range": ("symbol",)},
    # behavioural (exec observer)
    "executes":   {"domain": ("referent", "file"), "range": ("file",)},
    "reads":      {"domain": ("referent", "file", "symbol"), "range": ("file", "referent")},
    "writes":     {"domain": ("symbol", "file", "referent"), "range": ("file", "referent")},
    # semantic (agent markers / CLI)
    "calls":      {"domain": ("symbol", "file", "module"), "range": ("symbol", "file", "module")},
    "uses":       {"domain": ("symbol", "file", "module"), "range": ("symbol", "module", "referent", "file")},
    "implements": {"domain": ("symbol", "file", "module"), "range": ("symbol", "referent", "file")},
    "realizes":   {"domain": ("symbol", "file", "module"), "range": ("referent",)},
    "owned_by":   {"domain": ("symbol", "file", "module", "referent"), "range": ("referent", "symbol")},
}

# ─────────────────────────────────────────────────────────────────────────────
# Execution channel — observe BEHAVIOUR, not just mutation.
#
# An edit hook only sees files change; "what executes what" is a property of the
# system *running*. This channel parses the agent's Bash commands (structurally,
# never their output) into `executes`/`reads` edges backed by `runtime` evidence
# — an OBSERVATION kind, so it raises observed_conf and NEVER normative_conf
# (the core invariant holds: watching something run proves it happens, not that
# it is correct). The ONLY way execution touches normative_conf is a recognised
# *verifier* command's exit status: green → `test` oracle 'supports' (→ validated),
# red → 'refutes' (→ contradicted). Verifier recognition is a configurable regex,
# never a hardcoded build tool — `make` matches only when its target is a verifier.
# ─────────────────────────────────────────────────────────────────────────────

# argv[0] basenames that RUN a target given as an argument (interpreter/wrapper).
RUNNERS = {
    "bash", "sh", "zsh", "dash", "ksh",
    "python", "python2", "python3", "uv", "uvx", "pipx",
    "node", "nodejs", "deno", "bun", "ts-node", "tsx",
    "ruby", "perl", "php", "Rscript", "lua",
    "npm", "npx", "pnpm", "yarn", "make", "just", "task",
    "go", "cargo", "gradle", "mvn", "dotnet", "java",
}
# Pipeline/list operator chars that separate command segments (quote-aware; see _split_segments).
_SEG_OPS = {";", "&", "|"}
# Leading tokens that wrap/precede the real command (stripped before reading argv[0]).
# The shell control words if/elif/while/until/then/do/else are each FOLLOWED by a command,
# so stripping them exposes the real argv[0] (`if bash x.sh` → `bash x.sh`).
_CMD_WRAPPERS = {"sudo", "env", "time", "command", "exec", "nohup", "nice", "xargs",
                 "then", "do", "if", "elif", "while", "until", "else"}
# Segment argv[0] tokens that run NO program: loop/conditional HEADERS and test builtins.
# Their operands are loop vars, patterns, or test operands — data, not executed files —
# so `for f in a.sh b.sh` must NOT emit `for executes a.sh` edges.
_NON_EXEC_HEADS = {"for", "select", "case", "in", "esac", "done", "fi", "test", "[", "[["}
# Target extensions treated as executable code (→ `executes`); others (config/data) → `reads`.
_EXEC_EXTS = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx",
              ".rb", ".go", ".pl", ".php", ".lua", ".r"}
# Default verifier signal — build-tool-agnostic; override with $WM_VERIFIER_RE.
DEFAULT_VERIFIER_RE = (
    r"(?:\btests?\b|\bspecs?\b|\bcheck\b|\blint\b|\btypecheck\b|"
    r"\bpytest\b|\bunittest\b|\bjest\b|\bvitest\b|\bmocha\b|\brspec\b|"
    r"\bphpunit\b|\btox\b|\bnox\b|\bshellcheck\b|\bgotest\b|"
    r"_test\.|\.test\.|_spec\.|\.spec\.)"
)


def verifier_re_from_env():
    """Compiled verifier regex — $WM_VERIFIER_RE overrides the default. Falls back to
    the default on a bad user pattern (never breaks the hook)."""
    pat = os.environ.get("WM_VERIFIER_RE") or DEFAULT_VERIFIER_RE
    try:
        return re.compile(pat, re.IGNORECASE)
    except re.error:
        return re.compile(DEFAULT_VERIFIER_RE, re.IGNORECASE)


def _predicate_for(target: str) -> str:
    """`executes` for code targets, `reads` for config/data targets."""
    ext = os.path.splitext(target)[1].lower()
    return "executes" if (ext in _EXEC_EXTS or ext == "") else "reads"


def _split_segments(command):
    """Quote-aware split of a shell command into segments; each is a list of
    (value, quoted) argv tokens. `value` has any surrounding quotes stripped (for path /
    arg matching); `quoted` flags a quoted token so verifier detection can treat its
    contents as data, not a command word.

    Pipeline/list operators (`; & | && ||`) and real newlines separate segments; the same
    characters INSIDE quotes do NOT (so `grep '^(a|b):' f` stays one segment). Backslash
    line-continuations are collapsed first so a multi-line invocation isn't mis-split on
    the bare newline. Non-posix lex (keeps quote chars) → best-effort, never a real shell.
    """
    command = re.sub(r"\\\r?\n", " ", command or "")
    segments = []
    for line in command.split("\n"):        # a real newline still separates statements
        if not line.strip():
            continue
        try:
            lex = shlex.shlex(line, posix=False, punctuation_chars=True)
            lex.whitespace_split = True
            toks = list(lex)
        except ValueError:
            toks = line.split()
        cur = []
        for t in toks:
            if t and set(t) <= _SEG_OPS:     # a run of ; & | (e.g. '|', '&&', ';') → separator
                if cur:
                    segments.append(cur)
                    cur = []
                continue
            quoted = bool(t) and t[0] in ("'", '"')
            val = t[1:-1] if (quoted and len(t) >= 2 and t[-1] == t[0]) else t
            cur.append((val, quoted))
        if cur:
            segments.append(cur)
    return segments


def _strip_wrappers(vals):
    """Drop leading env-assignments (FOO=bar) and command wrappers (sudo/env/time/…)."""
    while vals and (
        (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", vals[0]) and not vals[0].startswith(("/", "."))) or
        vals[0] in _CMD_WRAPPERS
    ):
        vals = vals[1:]
    return vals


def _segment_edges(seg, is_repo_file):
    """(subject, subject_kind, object) execution edges from ONE quote-aware segment."""
    vals = _strip_wrappers([v for v, _q in seg])
    if not vals or vals[0] in _NON_EXEC_HEADS:
        return []                              # loop/conditional header or test builtin → no exec
    argv0 = vals[0]
    base = os.path.basename(argv0)
    # option-looking args (leading '-') are dropped; is_repo_file rejects non-paths anyway.
    targets = [t for t in vals[1:] if not t.startswith("-") and is_repo_file(t)]
    if base in RUNNERS:
        subj, subj_kind = base, "referent"
    elif is_repo_file(argv0):
        subj, subj_kind = argv0, "file"
    else:
        subj, subj_kind = base, "referent"     # external tool with repo-file args
    return [(subj, subj_kind, t) for t in targets if t != argv0]


def _segment_is_verifier(seg, verifier_re):
    """True iff THIS segment is a verifier invocation. Quoted args are data, not signal
    (so `grep '^(lint|check):' f` is not a verifier), and a verifier verb in ANOTHER
    segment can't leak in — `grep … ; shellcheck --version` promotes nothing for grep."""
    return bool(verifier_re.search(" ".join(v for v, q in seg if not q)))


def _is_verifier_invocation(command, verifier_re):
    """True iff ANY segment of the command is a verifier invocation. Whole-command
    pre-filter only; the precise gate is the per-segment check in observe_execution."""
    return any(_segment_is_verifier(seg, verifier_re) for seg in _split_segments(command))


def parse_exec_edges(command, is_repo_file):
    """Parse a shell command into (subject, subject_kind, object) execution edges.

    Structural only — reads argv, never command output. `is_repo_file(token)` decides
    whether a token names a real repo file (injected for testability). Returns edges:
      - runner (referent) → repo-file target        e.g. `bash reaper.sh`      → (bash, reaper.sh)
      - repo-file argv[0] → repo-file target         e.g. `./deploy.sh cfg.yaml`
      - external tool     → repo-file target         e.g. `kubectl apply -f ns.yaml`
    Commands with no repo-file target (e.g. `ls`, `kubectl get pods`) yield nothing —
    high precision, low noise. The subject_kind distinguishes a repo file from a tool.
    Segmentation is quote-aware (see `_split_segments`).
    """
    edges, seen = [], set()
    for seg in _split_segments(command):
        for key in _segment_edges(seg, is_repo_file):
            if key not in seen:
                seen.add(key)
                edges.append(key)
    return edges


# Tool-input fields that name a file the agent is working with, across tools/MCP.
_FILE_INPUT_KEYS = ("file_path", "path", "notebook_path", "filePath", "filename", "file")
_URL_INPUT_KEYS = ("url", "uri")
_URL_RE = re.compile(r"^https?://([^/\s]+)(/[^\s?#]*)?", re.IGNORECASE)


def _url_referent(url):
    """A stable referent name for an external URL — host + first path segment
    (e.g. https://api.stripe.com/v1/refunds → api.stripe.com/v1). None if not a URL."""
    m = _URL_RE.match(url or "")
    if not m:
        return None
    host = m.group(1)
    seg = (m.group(2) or "").strip("/").split("/", 1)[0]
    return f"{host}/{seg}" if seg else host


def _repo_rel(path, root):
    """Repo-relative path if `path` is a real file inside `root`, else None.
    Keeps capture scoped to the world (the repo), never littering external files."""
    if not path or not isinstance(path, str):
        return None
    try:
        ap = os.path.abspath(path)
    except (OSError, ValueError):
        return None
    if not os.path.isfile(ap):
        return None
    root = os.path.abspath(root)
    if ap != root and not ap.startswith(root + os.sep):
        return None
    return os.path.relpath(ap, root)


def extract_exec_from_hook(raw):
    """Pull (command, exit_code) out of a PostToolUse[Bash] hook JSON payload.

    exit_code is best-effort — Claude Code payloads vary — and None when unknown, in
    which case the verifier oracle is skipped (observation is still recorded)."""
    try:
        d = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return "", None
    if not isinstance(d, dict):
        return "", None
    command = ((d.get("tool_input") or {}).get("command") or "")
    resp = d.get("tool_response")
    ec = None
    if isinstance(resp, dict):
        for k in ("exit_code", "exitCode", "returncode", "returnCode", "code", "status"):
            v = resp.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, int):
                ec = v
                break
            if isinstance(v, str) and v.lstrip("-").isdigit():
                ec = int(v)
                break
        if ec is None and resp.get("is_error") is True:
            ec = 1
    return command, ec


def extract_tool_from_hook(raw):
    """Pull (tool_name, tool_input, exit_code) out of ANY PostToolUse hook payload.
    exit_code is best-effort (Bash only, usually)."""
    try:
        d = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return "", {}, None
    if not isinstance(d, dict):
        return "", {}, None
    tool_name = d.get("tool_name") or ""
    tool_input = d.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    _, ec = extract_exec_from_hook(raw)
    return tool_name, tool_input, ec


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
    # Tier-2 languages
    ".kt": "kotlin", ".kts": "kotlin", ".swift": "swift", ".dart": "dart",
    ".scala": "scala", ".sc": "scala", ".ex": "elixir", ".exs": "elixir",
})
# Dependency MANIFESTS (Tier-3), detected by exact filename → precise `depends_on` edges.
BUILD_MANIFEST_FILES = {
    "package.json": "npm-manifest", "composer.json": "composer",
    "pyproject.toml": "pyproject", "pipfile": "pipfile", "cargo.toml": "cargo",
    "go.mod": "go-mod", "pom.xml": "maven", "chart.yaml": "helm-chart",
    ".gitlab-ci.yml": "gitlab-ci", "packages.config": "csproj",
}
MANIFEST_LANGS = set(BUILD_MANIFEST_FILES.values()) | {"gradle", "pip-requirements", "csproj"}
# Tier-2 stdlib prefixes / system modules skipped as external deps.
KOTLIN_STDLIB = {"kotlin", "kotlinx", "java", "javax", "jakarta"}
SCALA_STDLIB = {"scala", "java", "javax"}
SWIFT_SYSTEM = {"Foundation", "UIKit", "SwiftUI", "Combine", "Swift", "Dispatch",
                "CoreData", "CoreGraphics", "CoreLocation", "MapKit", "AVFoundation",
                "os", "XCTest", "Testing"}
ELIXIR_STDLIB = {"Enum", "Map", "String", "List", "Keyword", "Logger", "GenServer",
                 "Application", "Supervisor", "Process", "Task", "Agent", "IO", "Kernel",
                 "Integer", "Float", "Stream", "Regex", "File", "Path", "System", "Registry"}
# Files with no/ambiguous extension, matched by (lowercased) basename or path.
BUILD_SPECIAL_FILENAMES = {
    "dockerfile": "dockerfile", "containerfile": "dockerfile",
    "makefile": "make", "gnumakefile": "make",
    "gemfile": "ruby", "rakefile": "ruby",
}
# Languages whose edges come ONLY from the language extractor (running the generic
# file-reference scan on real code would add noisy string-literal edges).
CODE_LANGS = {"python", "javascript", "typescript", "ruby", "go", "rust",
              "java", "c", "cpp", "csharp", "php",
              "kotlin", "swift", "dart", "scala", "elixir"}
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
        if "." in p.split("/")[0]:            # has a domain → local module OR external (decided at link)
            out.append(("imports", p, "go"))
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


# ── Tier-2 languages (Kotlin, Swift, Dart, Scala, Elixir) ────────────────────────
def _extract_kotlin(text, rel):
    out = []
    for imp in re.findall(r"^\s*import\s+([\w.]+(?:\.\*)?)", text, re.M):
        imp = imp[:-2] if imp.endswith(".*") else imp
        segs = imp.split(".")
        if segs[0] in KOTLIN_STDLIB:
            continue
        out.append(("depends_on", ".".join(segs[:2]), "external"))
    return out


def _extract_swift(text, rel):
    out = []
    for m in re.findall(r"^\s*import\s+(?:class\s+|struct\s+|func\s+|enum\s+)?([A-Za-z_]\w*)", text, re.M):
        if m not in SWIFT_SYSTEM:
            out.append(("depends_on", m, "external"))
    return out


def _extract_dart(text, rel):
    out = []
    for spec in re.findall(r"""(?:import|export)\s+['"]([^'"]+)['"]""", text):
        if spec.startswith("dart:"):
            continue
        if spec.startswith("package:"):
            out.append(("depends_on", spec[len("package:"):].split("/")[0], "external"))
        else:
            out.append(("imports", spec, "file"))       # relative .dart file
    return out


def _extract_scala(text, rel):
    out = []
    for imp in re.findall(r"^\s*import\s+([\w.]+)", text, re.M):
        segs = imp.split(".")
        if segs[0] in SCALA_STDLIB:
            continue
        out.append(("depends_on", ".".join(segs[:2]), "external"))
    return out


def _extract_elixir(text, rel):
    out, seen = [], set()
    for m in re.findall(r"^\s*(?:alias|import|use|require)\s+([A-Z][\w.]*)", text, re.M):
        top = m.split(".")[0]
        if top in ELIXIR_STDLIB or top in seen:
            continue
        seen.add(top)
        out.append(("depends_on", top, "external"))
    return out


# ── Tier-3 dependency manifests (precise declared dependencies) ──────────────────
def _toml_load(text):
    try:
        import tomllib
        return tomllib.loads(text)
    except Exception:
        return None


def _json_load(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_npm(text, rel):
    d = _json_load(text) or {}
    out = []
    for k in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        for name in (d.get(k) or {}):
            out.append(("depends_on", name, "external"))
    return out


def _extract_composer(text, rel):
    d = _json_load(text) or {}
    out = []
    for k in ("require", "require-dev"):
        for name in (d.get(k) or {}):
            if name == "php" or name.startswith("ext-"):
                continue
            out.append(("depends_on", name, "external"))
    return out


def _extract_pyproject(text, rel):
    d = _toml_load(text)
    out = []
    if not d:
        return out
    proj = d.get("project", {}) or {}
    for dep in proj.get("dependencies", []) or []:
        m = re.match(r"[A-Za-z0-9._-]+", dep)
        if m:
            out.append(("depends_on", m.group(0), "external"))
    for grp in (proj.get("optional-dependencies", {}) or {}).values():
        for dep in grp:
            m = re.match(r"[A-Za-z0-9._-]+", dep)
            if m:
                out.append(("depends_on", m.group(0), "external"))
    for name in (d.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}):
        if name.lower() != "python":
            out.append(("depends_on", name, "external"))
    return out


def _extract_pip_requirements(text, rel):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-", "git+", "http", ".", "/")):
            continue
        m = re.match(r"[A-Za-z0-9._-]+", line)
        if m:
            out.append(("depends_on", m.group(0), "external"))
    return out


def _extract_pipfile(text, rel):
    d = _toml_load(text)
    out = []
    if d:
        for sec in ("packages", "dev-packages"):
            for name in (d.get(sec, {}) or {}):
                out.append(("depends_on", name, "external"))
    return out


def _extract_cargo(text, rel):
    d = _toml_load(text)
    out = []
    if d:
        for sec in ("dependencies", "dev-dependencies", "build-dependencies"):
            for name in (d.get(sec, {}) or {}):
                out.append(("depends_on", name, "external"))
    return out


def _extract_gomod(text, rel):
    out = []
    for blk in re.findall(r"require\s*\(([^)]*)\)", text, re.S):
        for line in blk.splitlines():
            m = re.match(r"\s*(\S+)\s+v", line)
            if m:
                out.append(("depends_on", m.group(1), "external"))
    for m in re.findall(r"^\s*require\s+([^\s(]+)\s+v", text, re.M):
        out.append(("depends_on", m, "external"))
    return out


def _extract_maven(text, rel):
    out = []
    for dep in re.findall(r"<dependency>(.*?)</dependency>", text, re.S):
        g = re.search(r"<groupId>([^<]+)</groupId>", dep)
        a = re.search(r"<artifactId>([^<]+)</artifactId>", dep)
        if g and a:
            out.append(("depends_on", f"{g.group(1).strip()}:{a.group(1).strip()}", "external"))
    return out


def _extract_csproj(text, rel):
    # .NET project file / packages.config → precise NuGet artifacts + local project refs
    out = []
    for m in re.findall(r'<PackageReference\s+[^>]*?Include="([^"]+)"', text):
        out.append(("depends_on", m, "external"))
    for m in re.findall(r'<package\s+[^>]*?id="([^"]+)"', text):     # packages.config
        out.append(("depends_on", m, "external"))
    for m in re.findall(r'<ProjectReference\s+[^>]*?Include="([^"]+)"', text):
        out.append(("references", m.replace("\\", "/"), "file"))     # local project reference
    return out


def _extract_gradle(text, rel):
    out = []
    for m in re.findall(
            r"""\b(?:implementation|api|compileOnly|runtimeOnly|testImplementation|"""
            r"""androidTestImplementation|kapt|ksp|annotationProcessor)\s*[\(\s]\s*"""
            r"""["']([\w.\-]+:[\w.\-]+)(?::[\w.\-]+)?["']""", text):
        out.append(("depends_on", m, "external"))
    return out


def _extract_helm(text, rel):
    return [("depends_on", n, "external")
            for n in re.findall(r"""-\s*name:\s*["']?([\w.\-]+)""", text)]


def _extract_gitlab(text, rel):
    out = []
    for img in re.findall(r"""^\s*image:\s*["']?([^\s"'#]+)""", text, re.M | re.I):
        out.append(("depends_on", img, "external"))
    for inc in re.findall(r"""^\s*-?\s*local:\s*["']?([^\s"'#]+)""", text, re.M):
        out.append(("references", inc, "file"))
    return out


BUILD_EXTRACTORS = {
    "python": _extract_python, "javascript": _extract_js, "typescript": _extract_js,
    "ruby": _extract_ruby, "go": _extract_go, "rust": _extract_rust,
    "java": _extract_java, "c": _extract_c, "cpp": _extract_c,
    "csharp": _extract_csharp, "php": _extract_php,
    "kotlin": _extract_kotlin, "swift": _extract_swift, "dart": _extract_dart,
    "scala": _extract_scala, "elixir": _extract_elixir,
    "dockerfile": _extract_dockerfile, "compose": _extract_compose,
    "github-actions": _extract_actions, "terraform": _extract_terraform,
    "make": _extract_make, "shell": _extract_shell,
    # manifests
    "npm-manifest": _extract_npm, "composer": _extract_composer,
    "pyproject": _extract_pyproject, "pip-requirements": _extract_pip_requirements,
    "pipfile": _extract_pipfile, "cargo": _extract_cargo, "go-mod": _extract_gomod,
    "maven": _extract_maven, "gradle": _extract_gradle, "helm-chart": _extract_helm,
    "gitlab-ci": _extract_gitlab, "csproj": _extract_csproj,
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
        self.ontology_path = (None if path == ":memory:"
                              else os.path.join(os.path.dirname(os.path.abspath(path)), "ontology.json"))
        self._ontology = None
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

    # ── ontology (predicate vocabulary + domain/range guardrails) ───────────────
    def ontology(self) -> dict:
        """Core vocabulary merged with the project's `ontology.json` extension (if any).
        Malformed extension entries are dropped, never fatal — a broken extension file
        must not take the guardrail (or a hook) down with it."""
        if self._ontology is None:
            onto = {k: dict(v) for k, v in ONTOLOGY_CORE.items()}
            if self.ontology_path and os.path.isfile(self.ontology_path):
                try:
                    with open(self.ontology_path) as fh:
                        ext = json.load(fh)
                except (OSError, json.JSONDecodeError, ValueError):
                    ext = {}
                for pred, spec in (ext or {}).items():
                    if not isinstance(spec, dict):
                        continue
                    dom = tuple(k for k in spec.get("domain", ()) if k in ENTITY_KINDS)
                    rng = tuple(k for k in spec.get("range", ()) if k in ENTITY_KINDS)
                    if pred and dom and rng:
                        onto[str(pred)] = {"domain": dom, "range": rng}
            self._ontology = onto
        return self._ontology

    def extend_ontology(self, predicate, domain, range_) -> dict:
        """Deliberately add (or override) a predicate in the project vocabulary.
        Persists to `ontology.json` next to the DB so the extension survives sessions."""
        if not self.ontology_path:
            raise OntologyError("no ontology path for an in-memory model — use a file-backed DB")
        dom = tuple(k for k in domain if k in ENTITY_KINDS)
        rng = tuple(k for k in range_ if k in ENTITY_KINDS)
        if not predicate or not dom or not rng:
            raise OntologyError(
                f"ontology extension needs a predicate plus domain/range kinds from {list(ENTITY_KINDS)}")
        ext = {}
        if os.path.isfile(self.ontology_path):
            try:
                with open(self.ontology_path) as fh:
                    ext = json.load(fh) or {}
            except (OSError, json.JSONDecodeError, ValueError):
                ext = {}
        ext[predicate] = {"domain": list(dom), "range": list(rng)}
        with open(self.ontology_path, "w") as fh:
            json.dump(ext, fh, indent=2)
        self._ontology = None  # reload on next use
        return {"predicate": predicate, "domain": list(dom), "range": list(rng)}

    def _predicate_spec(self, predicate) -> dict:
        spec = self.ontology().get(predicate)
        if spec is None:
            raise OntologyError(
                f"unknown predicate '{predicate}' — allowed: {', '.join(sorted(self.ontology()))}. "
                f"To extend the vocabulary deliberately: "
                f"`wm ontology --add {predicate} --domain <kinds> --range <kinds>`.")
        return spec

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
        if default_kind in ("symbol", "file"):
            kind = "file" if ("/" in token or "." in token and default_kind == "file") else default_kind
        else:
            kind = default_kind    # explicit referent/module hint beats the path heuristic
        path = token if "/" in token else None
        return self.upsert_entity(kind, token, path=path)

    # ── interactions ───────────────────────────────────────────────────────────
    def add_interaction(self, subject, predicate, obj, subj_kind="symbol", obj_kind="symbol") -> int:
        # Ontology guardrail: validate the triple BEFORE anything enters the ledger.
        # Unknown entities are stubbed with an ontology-informed kind (domain/range as
        # type inference); a KNOWN entity's recorded kind must satisfy domain/range.
        spec = self._predicate_spec(predicate)
        sid = self._resolve_entity(subject, subj_kind if subj_kind in spec["domain"] else spec["domain"][0])
        oid = self._resolve_entity(obj, obj_kind if obj_kind in spec["range"] else spec["range"][0])
        for eid, allowed, role, token in ((sid, spec["domain"], "subject", subject),
                                          (oid, spec["range"], "object", obj)):
            kind = self.conn.execute("SELECT kind FROM entity WHERE id=?", (eid,)).fetchone()["kind"]
            if kind not in allowed:
                raise OntologyError(
                    f"'{predicate}' {role} must be one of {list(allowed)}, but '{token}' is a "
                    f"{kind} — rejected (semantically impossible triple). Pass an explicit "
                    f"--{'subj' if role == 'subject' else 'obj'}-kind, pick a predicate whose "
                    f"{'domain' if role == 'subject' else 'range'} fits, or extend the ontology.")
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

    # ── execution channel (observe behaviour) ───────────────────────────────────
    def observe_execution(self, command, exit_code=None, verifier_re=None,
                          is_repo_file=None, agent="exec-hook") -> dict:
        """Record what a Bash command executed/read as `runtime` OBSERVATION evidence.

        observed_conf rises; normative_conf never does — unless the command is a
        recognised verifier, in which case its exit status writes `test` oracle
        evidence (0 → supports/validated, non-0 → refutes/contradicted) on the code
        edges it ran. Structural parse only; command output is never inspected."""
        if is_repo_file is None:
            is_repo_file = lambda t: bool(t) and os.path.isfile(t)   # noqa: E731
        vre = verifier_re if verifier_re is not None else verifier_re_from_env()
        counts = {"edges": 0, "runtime": 0, "oracle": 0}
        seen = set()
        # Per SEGMENT: the exit status may only promote edges whose OWN segment is the
        # verifier — a verifier verb in a sibling segment (`grep … ; shellcheck --version`)
        # must not validate the grep edge.
        for seg in _split_segments(command):
            seg_is_verifier = _segment_is_verifier(seg, vre)
            for subj, subj_kind, obj in _segment_edges(seg, is_repo_file):
                if (subj, subj_kind, obj) in seen:
                    continue
                seen.add((subj, subj_kind, obj))
                pred = _predicate_for(obj)
                if subj_kind == "referent":
                    self.upsert_entity("referent", subj)
                else:
                    self.upsert_entity("file", subj, path=subj)
                self.upsert_entity("file", obj, path=obj)
                iid = self.add_interaction(subj, pred, obj, subj_kind=subj_kind, obj_kind="file")
                self.add_evidence("interaction", iid, "runtime", f"{subj}->{obj}",
                                  polarity="supports", agent=agent, activity="exec", weight=0.6)
                counts["edges"] += 1
                counts["runtime"] += 1
                # Verifier oracle: only a recognised verifier's exit status moves normative_conf,
                # and only on code (`executes`) edges the verifier segment actually ran.
                if seg_is_verifier and exit_code is not None and pred == "executes":
                    pol = "supports" if int(exit_code) == 0 else "refutes"
                    self.add_evidence("interaction", iid, "test", f"verifier:{obj}",
                                      polarity=pol, agent=agent, activity="verifier-run", weight=0.8)
                    counts["oracle"] += 1
        self.conn.commit()
        return counts

    # ── universal tool-call capture (observe the world through ANY tool) ─────────
    def observe_tool(self, tool_name, tool_input, exit_code=None, root=None) -> dict:
        """Register the entities/edges a tool call reveals — from ANY tool, not just
        edits. Reads the tool INPUT only (trusted, agent-authored); never its output.

        - Bash → delegates to observe_execution (behaviour edges + verifier oracle).
        - Edit/Write/MultiEdit/Read/Grep/Glob/Notebook*/MCP/etc. naming a repo file →
          register that file as an entity (a node sighting: it is part of the world).
          Observation only — no invented edge (markers/build/exec add the edges).
        - WebFetch/WebSearch/… url → register an external `referent` the agent consulted.
        No markers, no env, no config: purely what the agent's actions reveal."""
        root = root or os.getcwd()
        counts = {"files": 0, "referents": 0, "edges": 0}
        tn, ti = (tool_name or ""), (tool_input or {})
        if not isinstance(ti, dict):
            return counts
        if tn == "Bash":
            ex = self.observe_execution(ti.get("command", "") or "", exit_code=exit_code)
            counts["edges"] += ex["edges"]
            return counts
        # Any tool naming a repo file → register the node (working-set membership).
        seen = set()
        for k in _FILE_INPUT_KEYS:
            rel = _repo_rel(ti.get(k), root)
            if rel and rel not in seen:
                seen.add(rel)
                self.upsert_entity("file", rel, path=rel)
                counts["files"] += 1
        # MultiEdit-style / batched inputs sometimes carry a list of file targets.
        for k in ("edits", "files", "paths"):
            v = ti.get(k)
            if isinstance(v, list):
                for item in v:
                    cand = item.get("file_path") if isinstance(item, dict) else item
                    rel = _repo_rel(cand, root)
                    if rel and rel not in seen:
                        seen.add(rel)
                        self.upsert_entity("file", rel, path=rel)
                        counts["files"] += 1
        # URLs → external referents (the real-world things the code/agent depends on).
        for k in _URL_INPUT_KEYS:
            ref = _url_referent(ti.get(k))
            if ref:
                self.upsert_entity("referent", ref)
                counts["referents"] += 1
        if counts["files"] or counts["referents"]:
            self.conn.commit()
        return counts

    def bootstrap(self, root=".", max_files=5000) -> dict:
        """First-run seeding: if the model is empty, build it from the repo. Idempotent —
        a no-op once seeded. Lets a hook create + populate .world-model with zero manual
        steps (no install --seed, no env)."""
        n = self.conn.execute("SELECT COUNT(*) c FROM entity").fetchone()["c"]
        if n > 0:
            return {"seeded": False, "entities": n}
        stats = self.build_from_repo(root, max_files=max_files)
        self.conn.commit()
        return {"seeded": True, "build": stats.get("build", {})}

    # ── constraints ────────────────────────────────────────────────────────────
    def add_constraint(self, name, kind, message_tmpl, scope_predicate=None,
                       params=None, severity="violation") -> int:
        if scope_predicate:
            self._predicate_spec(scope_predicate)   # a constraint scoped to an unknown verb can never fire
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
        go_module, go_pkg = self._go_index(collected, root)   # go.mod module path + package→files
        ctx = {
            "relset": relset,
            "basename": basename_index,
            "mod": self._python_module_index(relset),
            "java": self._java_class_index(collected),   # FQN -> rel (reads `package` decls)
            "go_module": go_module,
            "go_pkg": go_pkg,
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
    def _go_index(collected, root):
        """(module_path, {package_import_path: [rel .go files]}) from a root go.mod, so a Go
        import of the module's own package resolves to the local files that comprise it."""
        module = None
        gomod = os.path.join(root, "go.mod")
        if os.path.exists(gomod):
            try:
                m = re.search(r"^module\s+(\S+)", open(gomod, errors="replace").read(), re.M)
                if m:
                    module = m.group(1)
            except OSError:
                pass
        pkg = {}
        if module:
            for rel, _ap, lang in collected:
                if lang != "go":
                    continue
                d = os.path.dirname(rel).replace(os.sep, "/")
                imp = module if d == "" else f"{module}/{d}"   # a Go package == its directory
                pkg.setdefault(imp, []).append(rel)
        return module, pkg

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
        """Language tag for a repo file, by special filename / manifest / path / extension."""
        base = os.path.basename(rel).lower()
        norm = "/" + rel.replace(os.sep, "/")
        # dependency manifests (exact filename / pattern) take precedence over extension
        if base in BUILD_MANIFEST_FILES:
            return BUILD_MANIFEST_FILES[base]
        if base.startswith("requirements") and base.endswith(".txt"):
            return "pip-requirements"
        if base.endswith(".gradle") or base.endswith(".gradle.kts"):
            return "gradle"
        if base.endswith(".csproj") or base.endswith(".fsproj") or base.endswith(".vbproj"):
            return "csproj"
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
        # non-code, non-manifest files also get the generic file-reference scan (docs/config)
        if lang not in CODE_LANGS and lang not in MANIFEST_LANGS:
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

    def _link_go(self, rel, target, ctx, counters):
        """A Go import: if it belongs to this module (per go.mod), link to the local .go files
        of that package (file→file); otherwise it's an external module dependency."""
        mod = ctx.get("go_module")
        if mod and (target == mod or target.startswith(mod + "/")):
            n = 0
            for f in ctx["go_pkg"].get(target, []):
                if f == rel:
                    continue
                iid = self.add_interaction(rel, "imports", f, subj_kind="file", obj_kind="file")
                self.add_evidence("interaction", iid, "static", f"{rel}: import {target}",
                                  agent="build", activity="build_from_repo", weight=0.6)
                n += 1
            return n
        if counters["deps"] >= BUILD_MAX_REFS_PER_FILE:
            return 0
        name = _norm_go_pkg(target)
        ref_id = self.upsert_entity("referent", name)
        rsym = self.conn.execute("SELECT symbol_id FROM entity WHERE id=?", (ref_id,)).fetchone()["symbol_id"]
        iid = self.add_interaction(rel, "depends_on", rsym, subj_kind="file", obj_kind="referent")
        self.add_evidence("interaction", iid, "static", f"{rel} depends_on {name}",
                          agent="build", activity="build_from_repo", weight=0.5)
        counters["deps"] += 1
        return 1

    def _link(self, rel, predicate, target, kind, ctx, counters):
        """Create one structural edge: a file→file edge if the target resolves to a scanned
        file, else (for dependency kinds) a file→referent `depends_on` edge."""
        target = (target or "").strip()
        if not target:
            return 0
        if kind == "go":
            return self._link_go(rel, target, ctx, counters)
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


def cmd_exec(wm, a):
    """Observe a Bash execution (runtime evidence + verifier oracle). Thin wrapper
    over WorldModel.observe_execution; --from-hook reads the PostToolUse JSON on stdin."""
    command, exit_code = a.command, a.exit_code
    if a.from_hook:
        command, exit_code = extract_exec_from_hook(sys.stdin.read())
    if not command or not command.strip():
        print(json.dumps({"edges": 0, "runtime": 0, "oracle": 0}))
        return 0
    counts = wm.observe_execution(command, exit_code=exit_code, verifier_re=verifier_re_from_env())
    wm.evaluate_constraints()
    wm.conn.commit()
    if a.digest and counts["edges"]:
        try:
            os.makedirs(os.path.dirname(a.digest) or ".", exist_ok=True)
            with open(a.digest, "w") as fh:
                fh.write(wm.digest())
        except OSError:
            pass
    print(json.dumps(counts))
    return 0


def cmd_observe_tool(wm, a):
    """Universal capture: register the entities/edges any tool call reveals.
    --from-hook reads the PostToolUse JSON payload from stdin (how the hook calls it)."""
    tool_name, tool_input, exit_code = a.tool_name, {}, a.exit_code
    if a.from_hook:
        tool_name, tool_input, exit_code = extract_tool_from_hook(sys.stdin.read())
    elif a.tool_input:
        try:
            tool_input = json.loads(a.tool_input)
        except json.JSONDecodeError:
            tool_input = {}
    if not tool_name:
        print(json.dumps({"files": 0, "referents": 0, "edges": 0}))
        return 0
    counts = wm.observe_tool(tool_name, tool_input, exit_code=exit_code)
    if a.digest and (counts["files"] or counts["referents"] or counts["edges"]):
        try:
            os.makedirs(os.path.dirname(a.digest) or ".", exist_ok=True)
            with open(a.digest, "w") as fh:
                fh.write(wm.digest())
        except OSError:
            pass
    print(json.dumps(counts))
    return 0


def cmd_bootstrap(wm, a):
    """First-run seeding — build the repo model if empty. Idempotent."""
    res = wm.bootstrap(a.path, max_files=a.max_files)
    print(json.dumps(res))
    return 0


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


def cmd_ontology(wm, a):
    """List the predicate vocabulary, or deliberately extend it (`--add`)."""
    if a.add:
        dom = [x.strip() for x in (a.domain or "").split(",") if x.strip()]
        rng = [x.strip() for x in (a.range_ or "").split(",") if x.strip()]
        print(json.dumps(wm.extend_ontology(a.add, dom, rng)))
        return 0
    print(json.dumps({p: {"domain": list(s["domain"]), "range": list(s["range"])}
                      for p, s in sorted(wm.ontology().items())}, indent=2))
    return 0


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

    ex = sub.add_parser("exec", help="observe a Bash execution (runtime evidence + verifier oracle)")
    ex.add_argument("--command", help="the shell command that ran")
    ex.add_argument("--exit-code", dest="exit_code", type=int, default=None,
                    help="exit status (enables the verifier oracle; omit if unknown)")
    ex.add_argument("--from-hook", dest="from_hook", action="store_true",
                    help="read the PostToolUse[Bash] JSON payload from stdin instead")
    ex.add_argument("--digest", help="refresh this digest file after observing")
    ex.set_defaults(func=cmd_exec)

    ot = sub.add_parser("observe-tool", help="universal capture: register what any tool call reveals")
    ot.add_argument("--tool-name", dest="tool_name", help="the tool that ran (e.g. Read, WebFetch)")
    ot.add_argument("--tool-input", dest="tool_input", help="the tool input as a JSON object")
    ot.add_argument("--exit-code", dest="exit_code", type=int, default=None)
    ot.add_argument("--from-hook", dest="from_hook", action="store_true",
                    help="read the PostToolUse JSON payload from stdin instead")
    ot.add_argument("--digest", help="refresh this digest file after observing")
    ot.set_defaults(func=cmd_observe_tool)

    bs = sub.add_parser("bootstrap", help="first-run: build the repo model if empty (idempotent)")
    bs.add_argument("path", nargs="?", default=".", help="repo root to scan (default: cwd)")
    bs.add_argument("--max-files", dest="max_files", type=int, default=5000)
    bs.set_defaults(func=cmd_bootstrap)

    bd = sub.add_parser("build", help="repo-wide seed: register files + structural edges (observation-only)")
    bd.add_argument("path", nargs="?", default=".", help="repo root to scan (default: cwd)")
    bd.add_argument("--max-files", dest="max_files", type=int, default=5000,
                    help="cap files scanned; surplus is logged, never silently dropped")
    bd.add_argument("--prune", action="store_true",
                    help="soft-invalidate build-origin edges for files that vanished "
                         "(never touches agent-observed/validated facts; never hard-deletes)")
    bd.set_defaults(func=cmd_build)

    on = sub.add_parser("ontology", help="list the predicate vocabulary, or extend it deliberately")
    on.add_argument("--add", help="predicate to add/override in the project vocabulary")
    on.add_argument("--domain", help="comma-separated subject kinds (symbol,file,module,referent)")
    on.add_argument("--range", dest="range_", help="comma-separated object kinds")
    on.set_defaults(func=cmd_ontology)

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
    except OntologyError as e:
        # Structured rejection, not a traceback — the message names the allowed
        # vocabulary so a probabilistic caller can self-correct and retry.
        print(json.dumps({"error": "ontology_violation", "detail": str(e)}))
        return 2
    finally:
        wm.close()


if __name__ == "__main__":
    sys.exit(main())
