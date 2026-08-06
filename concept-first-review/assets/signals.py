#!/usr/bin/env python3
"""Deterministic review signals: the questions a diff cannot answer for itself.

The condensed diff makes a change *legible*. Signals make it *interrogable*. Each
signal is an observation plus the question a reviewer should put to the author —
never a verdict. Heuristics route attention; they do not rule.

Two tiers:

  heuristic   works on any repo with no configuration (nested loops, I/O in a
              loop, swallowed errors, new external dependencies, public API
              deltas, schema and migration edits, concurrency primitives).
  baselined   with a `design-rules.json` present, layering and dependency-
              direction claims become checkable facts instead of guesses:
              "core/ must not import web/" is either violated or it is not.

Deliberate limits, stated rather than hidden:
  * nesting is inferred from indentation inside a contiguous run of added rows,
    so a loop opened outside the diff is not seen;
  * name-based matching cannot resolve aliases or dynamic dispatch;
  * a signal is evidence for a question, not a defect.
"""

import json
import os
import re
import sys

import diffmodel
from diffmodel import ADD, DEL, SOURCE_KINDS

HIGH = "high"
MEDIUM = "medium"
LOW = "low"

_SEV_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}

_TEST_PATH_RE = re.compile(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(spec|test)\.[jt]sx?$")
# Tests, examples, benchmarks and fixtures. Code here still gets reviewed, but a
# choice made in it has a smaller blast radius by construction, so every signal
# from such a path drops one severity level.
_NONPROD_RE = re.compile(
    r"(^|/)(tests?|spec|__tests__|examples?|benches?|benchmarks?|fixtures?|testdata)/"
    r"|(^|/)test_[^/]*$|_test\.[a-z]+$|_bench\.[a-z]+$|\.(spec|test|bench)\.[jt]sx?$"
)
_MIGRATION_RE = re.compile(r"(^|/)migrations?/|(^|/)migrate/|\.sql$")
_SCHEMA_RE = re.compile(r"\.(proto|graphql|gql|avsc|thrift)$|openapi|swagger|schema\.json$")
_CONFIG_RE = re.compile(r"\.(ya?ml|toml|ini|env|properties)$|(^|/)config/|Dockerfile|(^|/)helm/")

_LOOP_RE = re.compile(r"^(?:for|while)\b|^(?:for|while)\s*\(|\.forEach\s*\(|\bdo\s*\{")
# I/O evidence, deliberately narrow. A bare `get(`/`load(`/`read(` is far more
# often a dict lookup or an in-memory accessor than a round trip, and a detector
# that fires on those teaches reviewers to ignore it.
_IO_RE = re.compile(
    r"\b(?:execute|executemany|urlopen|fetch)\s*\("
    r"|\.(?:query|execute|executemany|fetchone|fetchall|fetch_all|save|commit)\s*\("
    r"|\b(?:requests|httpx|session|client|http|conn|cursor|db)\.\w+\s*\("
    r"|\bopen\s*\(|\bsubprocess\.|\bos\.(?:stat|listdir|walk|remove)\s*\("
    r"|\bfind_one\s*\(|\bfindOne\s*\("
)
# Membership testing, deliberately NOT matching a `for x in xs:` loop header —
# that is iteration, not a scan.
_LINEAR_SCAN_RE = re.compile(
    r"^(?:if|elif|while|assert|return)\b.*\b(?:not\s+)?in\s+[A-Za-z_][\w.]*"
    r"|\.includes\s*\(|\.indexOf\s*\(|\.index\s*\(|\bcontains\s*\("
)
_ROUTE_RE = re.compile(
    r"@(?:app|router|blueprint|bp)\.(?:route|get|post|put|patch|delete)\b"
    r"|\b(?:app|router|r|mux)\.(?:Get|Post|Put|Patch|Delete|HandleFunc|get|post|put|patch|delete)\s*\("
    r"|@(?:Get|Post|Put|Delete|Request)Mapping\b"
)
_SORT_RE = re.compile(r"\bsort(?:ed)?\s*\(|\.sort\s*\(|sort\.Slice\s*\(|\.OrderBy\s*\(")
_APPEND_RE = re.compile(r"\.append\s*\(|\.push\s*\(|\bappend\s*\(|\.add\s*\(|\.insert\s*\(|\.Add\s*\(")
_AWAIT_RE = re.compile(r"\bawait\b")
_SPAWN_RE = re.compile(
    r"\bgo\s+(?:func\b|\w[\w.]*\s*\()"          # go func(...) / go handler(...)
    r"|\bthreading\.Thread\b|\bThread\s*\(|\bnew\s+Thread\b"
    r"|asyncio\.create_task|\btokio::spawn\b|\bthread::spawn\b|\bspawn\s*\("
    r"|Promise\.all"
)
_LOCK_RE = re.compile(r"\b(?:Mutex|RWMutex|Lock|RLock|acquire|synchronized|sync\.)\b")
# Swallowing means the failure disappears. `.unwrap()` and `.expect()` do the
# opposite — they abort loudly — so they belong to err.abort, not here. Getting
# that wrong turns every idiomatic Rust test into a high-severity finding.
_SWALLOW_RE = re.compile(
    r"except[^:]*:\s*(?:pass|continue)\b|except\s*:|catch\s*\([^)]*\)\s*\{\s*\}"
    r"|_\s*=\s*\w*err\w*\b|\blet\s+_\s*=|\bif\s+err\s*!=\s*nil\s*\{\s*\}"
)
# An `except:`/`catch {` whose body on the following added row does nothing.
_SWALLOW_OPENER_RE = re.compile(r"^(?:except\b[^:]*:|\}?\s*catch\s*\([^)]*\)\s*\{|rescue\b.*)$")
_SWALLOW_BODY_RE = re.compile(r"^(?:pass|continue|\}|//.*|#.*)$")
_PANIC_RE = re.compile(
    r"\bpanic\s*\(|os\.Exit\s*\(|sys\.exit\s*\(|process\.exit\s*\("
    r"|\.expect\s*\(|\.unwrap\s*\(\s*\)"
)
_RESILIENCE_RE = re.compile(r"\b(?:retry|retries|backoff|timeout|deadline|circuit_?breaker|WithTimeout|max_attempts)\b", re.I)
_SUBPROCESS_RE = re.compile(r"\bsubprocess\.|os\.system\s*\(|exec\.Command\s*\(|child_process|Runtime\.getRuntime\(\)\.exec|\beval\s*\(")
_DESERIALIZE_RE = re.compile(r"\bpickle\.loads?\s*\(|yaml\.load\s*\(|Marshal\.load|ObjectInputStream|unserialize\s*\(")
_NETWORK_RE = re.compile(r"\b(?:requests|httpx|urllib|http\.Client|axios|fetch)\b|\.Get\s*\(\"http|socket\.")
_PATHJOIN_RE = re.compile(r"os\.path\.join\s*\(|filepath\.Join\s*\(|path\.join\s*\(")
_AUTHZ_RE = re.compile(r"\b(?:is_admin|has_permission|authorize|authorise|check_?auth|require_?role|can_|acl|rbac)\b", re.I)
_FLAG_RE = re.compile(r"\b(?:feature_?flag|is_enabled|getenv|os\.environ|process\.env|LookupEnv|ConfigMap)\b", re.I)
_DDL_RE = re.compile(r"\b(?:CREATE TABLE|ALTER TABLE|DROP TABLE|ADD COLUMN|DROP COLUMN|CREATE INDEX)\b", re.I)
_GLOBAL_MUT_RE = re.compile(r"^(?:var\s+\w+\s*=|[A-Za-z_]\w*\s*=\s*(?:\[\]|\{\}|dict\(|list\(|make\())")
_RECURSION_HINT_RE = re.compile(r"^(?:async\s+)?(?:def|func|function|fn)\s+([A-Za-z_]\w*)")
_REGEX_LITERAL_RE = re.compile(r"(?:re\.(?:compile|match|search|sub)|regexp\.MustCompile|new RegExp)\s*\(\s*[r]?['\"]([^'\"]{4,})['\"]")
_NESTED_QUANT_RE = re.compile(r"\([^)]*[+*][^)]*\)\s*[+*]")

# Exported / public symbol declarations, per language family.
_PUBLIC_DECL = {
    "go": re.compile(r"^(?:func|type|var|const)\s+(?:\([^)]*\)\s*)?([A-Z]\w*)"),
    "python": re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z][\w]*)"),
    "js": re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)"),
    "rust": re.compile(r"^pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type)\s+(\w+)"),
    "jvm": re.compile(r"^\s*public\s+(?:static\s+)?[\w<>\[\], ]+\s+(\w+)\s*\("),
    "csharp": re.compile(r"^\s*public\s+(?:static\s+)?[\w<>\[\], ]+\s+(\w+)\s*\("),
}

_PY_STDLIB = getattr(sys, "stdlib_module_names", frozenset())
# Node ships these; an import of one is not a dependency decision. The `node:`
# prefix says so explicitly, but plenty of code still writes the bare name.
_NODE_BUILTINS = frozenset("""
assert async_hooks buffer child_process cluster console constants crypto dgram
diagnostics_channel dns domain events fs http http2 https inspector module net
os path perf_hooks process punycode querystring readline repl stream string_decoder
timers tls trace_events tty url util v8 vm wasi worker_threads zlib
""".split())
_KNOWN_STD_PREFIXES = (
    "std", "core", "alloc", "java.", "javax.", "kotlin.", "System.",
)


# Files whose contents are prose or data, never executable statements. Running
# code-shaped detectors over them reads English as code: `eval (` in a design note
# becomes a call to eval, and a YAML comment listing languages becomes a goroutine.
_NON_CODE_EXT = (
    ".md", ".markdown", ".rst", ".txt", ".adoc", ".org",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".json", ".lock", ".csv", ".tsv",
)


_GENERATED_RE = re.compile(
    r"(^|/)(node_modules|vendor|dist|build|third_party)/"
    r"|\.bundle\.[jt]s$|\.min\.[jt]s$|\.generated\.[\w]+$"
    r"|_pb2\.py$|\.pb\.go$|\.pb\.cc$|_generated\.[\w]+$|\.lock$"
)


def _is_generated(path):
    """Machine-produced output. It is a result of the change, not the change."""
    return bool(_GENERATED_RE.search(path or ""))


def _is_prose(path):
    """True for a file whose rows are prose or data rather than statements.

    Configuration still gets its own path-based signals (`cfg.changed`,
    `data.contract`); what it does not get is the statement-level detectors.
    """
    return (path or "").lower().endswith(_NON_CODE_EXT) or _is_generated(path)


def _is_statement_start(src):
    """False for a continuation row of a multi-line expression.

    A row closing more brackets than it opens is the tail of something that
    began above the row, so indentation says nothing useful about its nesting.
    """
    s = src.strip()
    if not s:
        return False
    opens = sum(s.count(c) for c in "([{")
    closes = sum(s.count(c) for c in ")]}")
    return closes <= opens


_DAMP = {HIGH: MEDIUM, MEDIUM: LOW, LOW: LOW}


def _signal(sid, severity, path, line, evidence, question):
    # A choice made in a test, example, or benchmark reaches fewer people than the
    # same choice in production code, so it asks a quieter question. Without this,
    # a test suite full of idiomatic assertions drowns the real findings.
    if _NONPROD_RE.search(path or ""):
        severity = _DAMP[severity]
    return {
        "id": sid,
        "severity": severity,
        "file": path,
        "line": line,
        "evidence": evidence.strip()[:160],
        "question": question,
    }


# ── Baseline ─────────────────────────────────────────────────────────────────

DEFAULT_BASELINE = {
    "layers": {},
    "forbidden_edges": [],
    "allowed_external": [],
    # max_loop_depth is the deepest loop nesting a change may introduce without
    # owing the reviewer an explanation. 1 means "any two-level nest gets asked
    # about"; raise it for repos where nested iteration is genuinely routine.
    "budgets": {
        "max_loop_depth": 1,
        # A function past this many parameters is usually carrying more than one
        # responsibility, or has grown an options bag that wants to be a type.
        "max_params": 5,
        # Rows in a single added function body before its size becomes a question.
        "max_function_rows": 60,
        # Identical normalised rows repeated in the change before it reads as
        # copy-paste rather than coincidence.
        "min_duplicate_rows": 6,
    },
    "invariants": [],
}


def load_baseline(path):
    """Read a design-rules.json baseline. Missing file -> heuristics only."""
    if not path or not os.path.exists(path):
        return dict(DEFAULT_BASELINE)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    merged = dict(DEFAULT_BASELINE)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_BASELINE})
    budgets = dict(DEFAULT_BASELINE["budgets"])
    budgets.update(data.get("budgets") or {})
    merged["budgets"] = budgets
    merged["_configured"] = True
    return merged


def _layer_of(path, layers):
    best = None
    best_len = -1
    for name, prefixes in (layers or {}).items():
        for prefix in prefixes:
            if path.startswith(prefix) and len(prefix) > best_len:
                best, best_len = name, len(prefix)
    return best


# ── Structure helpers ────────────────────────────────────────────────────────

def _loop_depths(entries):
    """Loop nesting depth per added row, inferred from indentation.

    `entries` is an ordered list of (line_no, source) for one contiguous run of
    added rows. Only loops opened *within the run* are visible; a loop opened
    outside the diff is invisible, which is why the derived signal asks a
    question rather than asserting complexity.
    """
    depths = {}
    stack = []  # (indent, is_loop)
    for line_no, src in entries:
        stripped = src.strip()
        if not stripped:
            depths[line_no] = len([1 for _, is_loop in stack if is_loop])
            continue
        indent = len(src) - len(src.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        depths[line_no] = len([1 for _, is_loop in stack if is_loop])
        opens_loop = bool(_LOOP_RE.search(stripped)) and _is_statement_start(stripped)
        stack.append((indent, opens_loop))
    return depths


def _added_runs(rows):
    """Contiguous runs of added rows, grouped by file path."""
    runs = []
    current = []
    current_path = None
    for row in rows:
        if row.kind == ADD:
            if current and row.no != current[-1][0] + 1:
                runs.append((current_path, current))
                current = []
            current_path = row.path
            current.append((row.no, row.source))
        else:
            if current:
                runs.append((current_path, current))
                current = []
    if current:
        runs.append((current_path, current))
    return runs


def _module_of(src, lang):
    s = src.strip()
    if lang == "python":
        m = re.match(r"^from\s+([\w.]+)\s+import\b", s) or re.match(r"^import\s+([\w.]+)", s)
        return m.group(1) if m else None
    if lang == "go":
        m = re.search(r'"([^"]+)"', s)
        return m.group(1) if m else None
    if lang == "js":
        m = re.search(r"""from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]""", s)
        if m:
            return m.group(1) or m.group(2)
        return None
    if lang == "rust":
        m = re.match(r"^(?:pub\s+)?use\s+([\w:]+)", s)
        return m.group(1).split("::")[0] if m else None
    if lang == "c":
        m = re.search(r'#\s*include\s*[<"]([^>"]+)[>"]', s)
        return m.group(1) if m else None
    if lang in ("jvm", "csharp"):
        m = re.match(r"^(?:import|using)\s+([\w.]+)", s)
        return m.group(1) if m else None
    return None


def _module_as_path(module, lang):
    """Best-effort module -> repo-path form, so layer prefixes can match it.

    Dotted namespaces (Python, JVM, C#) become slash paths; already-pathlike
    module strings (Go, JS) only lose their relative prefix. It is a heuristic,
    which is why an unmatched module simply yields no layering signal rather
    than a wrong one.
    """
    m = module.lstrip("./")
    if lang in ("python", "jvm", "csharp"):
        return m.replace(".", "/")
    if lang == "rust":
        return m.replace("::", "/")
    return m


def _is_external(module, lang):
    if not module:
        return False
    if module.startswith((".", "/", "./", "../")):
        return False
    if lang == "python":
        root = module.split(".")[0]
        return root not in _PY_STDLIB
    if lang == "go":
        first = module.split("/")[0]
        return "." in first  # a dotted first segment means a hosted module path
    if lang == "js":
        if module.startswith((".", "/", "@/", "~", "node:", "bun:")):
            return False
        return module.split("/")[0] not in _NODE_BUILTINS
    if lang == "rust":
        # crate/self/super are this crate; std/core/alloc are the language.
        return module not in ("std", "core", "alloc", "crate", "self", "super")
    if module.startswith(_KNOWN_STD_PREFIXES):
        return False
    return True


# ── Extraction ───────────────────────────────────────────────────────────────

def extract(rows, baseline=None, repo_root=None):
    """Return the full signal list for a parsed diff, most severe first.

    `repo_root` is optional and enables only the checks that cannot be answered
    from the diff alone — today, whether a cited path actually exists.
    """
    baseline = baseline or dict(DEFAULT_BASELINE)
    declarations = diffmodel.declaration_rows(rows)
    out = []

    out.extend(_dependency_signals(rows, declarations, baseline, repo_root))
    out.extend(_api_signals(rows))
    out.extend(_algorithm_signals(rows, declarations, baseline))
    out.extend(_runtime_signals(rows, declarations))
    out.extend(_contract_signals(rows))
    out.extend(_blast_radius_signals(rows))
    out.extend(_fit_signals(rows, declarations, baseline))
    out.extend(_agentic_signals(rows, declarations, baseline, repo_root))

    out.sort(key=lambda s: (_SEV_ORDER.get(s["severity"], 3), s["file"] or "", s["line"]))
    return out


def _self_package_name(repo_root):
    """What this repository calls itself in its own import paths.

    A crate or module importing itself from its tests and examples — `use
    graph_d::Graph`, `import myproj/internal/x` — is reaching inside, not taking
    on a dependency. Without this, freezing an allowlist flags every one of them.
    """
    if not repo_root:
        return None
    return os.path.basename(os.path.abspath(repo_root)).replace("-", "_")


def _dependency_signals(rows, declarations, baseline, repo_root=None):
    out = []
    self_name = _self_package_name(repo_root)
    # Modules the file already imported before this change. A formatter sweep
    # rewrites every import row, so without this every reformatted import reads
    # as a brand-new dependency.
    already = set()
    for row in rows:
        if row.kind == DEL and row.no in declarations:
            mod = _module_of(row.source, diffmodel.language_of(row.path or ""))
            if mod:
                already.add((row.file_idx, mod))
    layers = baseline.get("layers") or {}
    forbidden = {(a, b) for a, b in (baseline.get("forbidden_edges") or [])}
    allowed_external = set(baseline.get("allowed_external") or [])
    configured = baseline.get("_configured")

    for row in rows:
        if row.kind != ADD or row.no not in declarations:
            continue
        lang = diffmodel.language_of(row.path or "")
        module = _module_of(row.source, lang)
        if not module:
            continue
        # An indented Rust `use` (e.g. `use RangeKey::*;` inside a match) brings an
        # item already reachable into local scope. That is a readability choice, not
        # a dependency decision.
        if lang == "rust" and row.source[:1].isspace():
            continue
        if (row.file_idx, module) in already:
            continue
        root_segment = module.split("/")[0].split(".")[0].split("::")[0]
        if self_name and root_segment.replace("-", "_") == self_name:
            continue

        if _is_external(module, lang):
            root = module.split("/")[0].split(".")[0]
            if configured and allowed_external and root not in allowed_external:
                out.append(_signal(
                    "dep.new-external", HIGH, row.path, row.no, row.source,
                    "`%s` is a new third-party dependency and is not on the allowed list in "
                    "design-rules.json. Is the capability worth the dependency, and who owns "
                    "upgrading it?" % module,
                ))
            elif not configured:
                out.append(_signal(
                    "dep.new-external", MEDIUM, row.path, row.no, row.source,
                    "`%s` looks like a new third-party dependency. What does it buy that the "
                    "standard library or an existing dependency does not?" % module,
                ))

        if layers:
            src_layer = _layer_of(row.path or "", layers)
            dst_layer = _layer_of(_module_as_path(module, lang), layers)
            if src_layer and dst_layer and src_layer != dst_layer:
                if (src_layer, dst_layer) in forbidden:
                    out.append(_signal(
                        "dep.layering", HIGH, row.path, row.no, row.source,
                        "layer `%s` must not depend on `%s` (design-rules.json forbids that "
                        "edge). Is this an intended change to the layering, or an accident?"
                        % (src_layer, dst_layer),
                    ))
                else:
                    out.append(_signal(
                        "dep.new-edge", LOW, row.path, row.no, row.source,
                        "new dependency edge `%s` -> `%s`. Does the layering still hold?"
                        % (src_layer, dst_layer),
                    ))
    return out


_WS_RE = re.compile(r"\s+")
_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
_MAX_DECL_ROWS = 40


def _declaration_tokens(rows, start):
    """Identifier sequence of a declaration and the rows it was reformatted over.

    Comparing identifiers rather than text is what separates a real signature
    change from a formatter sweep. Layout, trailing commas, quote style and a
    leading union pipe all disappear; a renamed function, an added parameter or
    a changed type shows up immediately.

    The span is the whole contiguous same-marker run starting at the declaration.
    That run only exists on both sides when the declaration row itself changed,
    so a body-only edit never reaches this comparison.
    """
    first = rows[start]
    parts = [first.source]
    i = start + 1
    while i < len(rows) and i - start < _MAX_DECL_ROWS:
        nxt = rows[i]
        if nxt.kind != first.kind or nxt.hunk_idx != first.hunk_idx or nxt.no != rows[i - 1].no + 1:
            break
        parts.append(nxt.source)
        i += 1
    return tuple(_IDENT_RE.findall(" ".join(parts)))


def _api_signals(rows):
    added = {}
    removed = {}
    for i, row in enumerate(rows):
        if row.kind not in (ADD, DEL):
            continue
        lang = diffmodel.language_of(row.path or "")
        pattern = _PUBLIC_DECL.get(lang)
        if not pattern:
            continue
        m = pattern.match(row.source.strip())
        if not m:
            continue
        name = m.group(1)
        if lang == "python" and name.startswith("_"):
            continue
        bucket = added if row.kind == ADD else removed
        bucket.setdefault((row.path, name), (row, _declaration_tokens(rows, i)))

    out = []
    for row in rows:
        if row.kind != ADD or not _ROUTE_RE.search(row.source):
            continue
        out.append(_signal(
            "api.route-added", HIGH, row.path, row.no, row.source,
            "a new externally reachable entry point. Who is allowed to call it, what validates "
            "its input, and what does it cost to serve?",
        ))

    for (path, name), (row, tokens) in sorted(added.items()):
        if (path, name) in removed:
            if removed[(path, name)][1] == tokens:
                continue  # same identifiers, different layout: a formatter moved it
            out.append(_signal(
                "api.signature-changed", HIGH, path, row.no, row.source,
                "public `%s` changed shape. Who calls it, and is every caller updated in this "
                "change or covered by a compatibility path?" % name,
            ))
        else:
            out.append(_signal(
                "api.surface-added", MEDIUM, path, row.no, row.source,
                "`%s` is new public surface. Does it need to be public, and is its contract "
                "(errors, nil/None, ownership, thread-safety) stated anywhere?" % name,
            ))
    for (path, name), (row, _tokens) in sorted(removed.items()):
        if (path, name) in added:
            continue
        out.append(_signal(
            "api.surface-removed", HIGH, path, row.no, row.source,
            "public `%s` is gone. Is anything outside this change still calling it?" % name,
        ))
    return out


def _algorithm_signals(rows, declarations, baseline):
    out = []
    max_depth = int((baseline.get("budgets") or {}).get("max_loop_depth", 1))

    for path, entries in _added_runs(rows):
        if _is_prose(path):
            continue
        entries = [(n, s) for n, s in entries if n not in declarations]
        if not entries:
            continue
        depths = _loop_depths(entries)
        # A function declared in this run and called later in the run is not
        # recursion unless the call is inside that function's own body. Track the
        # enclosing declaration by indentation and drop it when the run leaves it.
        enclosing = None

        for line_no, src in entries:
            s = src.strip()
            if not s:
                continue
            depth = depths.get(line_no, 0)
            in_loop = depth >= 1

            if _LOOP_RE.search(s) and _is_statement_start(s) and depth >= max_depth:
                out.append(_signal(
                    "algo.nested-loop", HIGH, path, line_no, s,
                    "loop nested %d deep. What bounds each level, and what happens at the "
                    "largest input this will really see?" % (depth + 1),
                ))
            if in_loop and _IO_RE.search(s) and not _LOOP_RE.search(s):
                out.append(_signal(
                    "algo.io-in-loop", HIGH, path, line_no, s,
                    "an I/O or query call inside a loop is the N+1 shape. Can it be batched, "
                    "or is the iteration count small and bounded?",
                ))
            if in_loop and _AWAIT_RE.search(s):
                out.append(_signal(
                    "algo.await-in-loop", MEDIUM, path, line_no, s,
                    "awaiting inside a loop serialises what could be concurrent. Is the "
                    "ordering required, or is this accidental latency?",
                ))
            if in_loop and not _LOOP_RE.search(s) and _LINEAR_SCAN_RE.search(s):
                out.append(_signal(
                    "algo.linear-scan-in-loop", MEDIUM, path, line_no, s,
                    "a linear membership test inside a loop makes this quadratic. Would a set "
                    "or map index be the honest data structure here?",
                ))
            if in_loop and _SORT_RE.search(s):
                out.append(_signal(
                    "algo.sort-in-loop", MEDIUM, path, line_no, s,
                    "sorting inside a loop. Can the sort be hoisted, or is the collection "
                    "genuinely different each iteration?",
                ))
            if in_loop and _APPEND_RE.search(s):
                out.append(_signal(
                    "algo.unbounded-growth", LOW, path, line_no, s,
                    "a collection grows inside a loop. What caps its size — and what is the "
                    "memory cost at the worst realistic input?",
                ))
            indent = len(src) - len(src.lstrip())
            decl = _RECURSION_HINT_RE.match(s)
            if decl:
                enclosing = (decl.group(1), indent)
            elif enclosing and indent <= enclosing[1]:
                enclosing = None
            elif enclosing and re.search(
                # A bare call, or a call on self/this. `a.cmp(b)` inside `fn cmp`
                # is dispatch on another receiver, not recursion — that pattern is
                # unavoidable in trait impls where the trait fixes the method name.
                r"(?:(?<![.\w])|\b(?:self|this)\.)%s\s*\(" % re.escape(enclosing[0]), s
            ):
                out.append(_signal(
                    "algo.recursion", MEDIUM, path, line_no, s,
                    "`%s` calls itself. What is the termination argument, and how deep can "
                    "it go on real input?" % enclosing[0],
                ))
            m = _REGEX_LITERAL_RE.search(s)
            if m and _NESTED_QUANT_RE.search(m.group(1)):
                out.append(_signal(
                    "algo.regex-backtracking", MEDIUM, path, line_no, s,
                    "nested quantifiers in a regex can backtrack catastrophically. Is this "
                    "pattern ever applied to input an outsider controls?",
                ))
    return out


def _runtime_signals(rows, declarations):
    out = []
    added = [r for r in rows if r.kind == ADD and r.no not in declarations and not _is_prose(r.path)]
    next_added = {}
    for i, row in enumerate(added[:-1]):
        next_added[row.no] = added[i + 1]

    for row in added:
        s = row.source.strip()
        if not s:
            continue
        path = row.path

        if _SWALLOW_OPENER_RE.match(s):
            follower = next_added.get(row.no)
            if follower and _SWALLOW_BODY_RE.match(follower.source.strip()):
                out.append(_signal(
                    "err.swallowed", HIGH, path, row.no, s,
                    "an error path is discarded. Is this failure genuinely uninteresting, or is "
                    "a real fault about to become a silent wrong answer?",
                ))

        if _SPAWN_RE.search(s):
            out.append(_signal(
                "conc.spawned", HIGH, path, row.no, s,
                "new concurrency. What owns this task's lifetime, what happens to its errors, "
                "and what shared state does it touch?",
            ))
        if _LOCK_RE.search(s):
            out.append(_signal(
                "conc.lock", MEDIUM, path, row.no, s,
                "a lock enters the picture. What invariant does it protect, and what is the "
                "lock ordering relative to the others in this system?",
            ))
        if _GLOBAL_MUT_RE.match(s) and row.source[:1] not in (" ", "\t"):
            out.append(_signal(
                "state.global-mutable", MEDIUM, path, row.no, s,
                "module-level mutable state. Who writes it, from which goroutine/thread/request, "
                "and does its lifetime match the process?",
            ))
        if _SWALLOW_RE.search(s):
            out.append(_signal(
                "err.swallowed", HIGH, path, row.no, s,
                "an error path is discarded. Is this failure genuinely uninteresting, or is a "
                "real fault about to become a silent wrong answer?",
            ))
        if _PANIC_RE.search(s):
            out.append(_signal(
                "err.abort", MEDIUM, path, row.no, s,
                "this aborts rather than returns. Is the caller a program entry point, or does "
                "this take down work that could have been failed gracefully?",
            ))
        if _RESILIENCE_RE.search(s):
            out.append(_signal(
                "err.resilience", LOW, path, row.no, s,
                "retry/timeout behaviour changed. What is the resulting worst-case latency, and "
                "is the operation being retried idempotent?",
            ))
        if _SUBPROCESS_RE.search(s):
            out.append(_signal(
                "bound.exec", HIGH, path, row.no, s,
                "the change reaches outside the process. Where does every argument come from, "
                "and can any of it be influenced by an untrusted caller?",
            ))
        if _DESERIALIZE_RE.search(s):
            out.append(_signal(
                "bound.deserialize", HIGH, path, row.no, s,
                "deserialisation of structured input. Is the source trusted, and is a safe "
                "loader available instead?",
            ))
        if _NETWORK_RE.search(s):
            out.append(_signal(
                "bound.network", MEDIUM, path, row.no, s,
                "a new network boundary. What are its timeout, retry, and failure semantics, "
                "and what does the caller see when it is down?",
            ))
        if _PATHJOIN_RE.search(s):
            out.append(_signal(
                "bound.path", LOW, path, row.no, s,
                "a filesystem path is constructed. Can any component come from user input, and "
                "is traversal outside the intended root possible?",
            ))
        if _AUTHZ_RE.search(s):
            out.append(_signal(
                "bound.authz", HIGH, path, row.no, s,
                "an authorisation decision moved. Which callers reach this path, and is the "
                "check on every one of them?",
            ))
    return out


def _contract_signals(rows):
    out = []
    seen_files = set()
    for row in rows:
        path = row.path or ""
        if not path:
            continue
        if row.kind not in (ADD, DEL):
            continue
        if path not in seen_files:
            seen_files.add(path)
            if _MIGRATION_RE.search(path):
                out.append(_signal(
                    "data.migration", HIGH, path, row.no, path,
                    "a schema migration. Is it reversible, is it safe to run while the old code "
                    "is still serving, and how long does it lock?",
                ))
            elif _SCHEMA_RE.search(path):
                out.append(_signal(
                    "data.contract", HIGH, path, row.no, path,
                    "a wire/schema contract changed. Are old and new producers and consumers "
                    "compatible during rollout in both directions?",
                ))
            elif _CONFIG_RE.search(path):
                out.append(_signal(
                    "cfg.changed", LOW, path, row.no, path,
                    "configuration changed. Does every environment have a value, and what is "
                    "the behaviour when it is missing?",
                ))
        if row.kind == ADD and _DDL_RE.search(row.source):
            out.append(_signal(
                "data.ddl", HIGH, path, row.no, row.source,
                "DDL in the change. What is the migration order relative to the code deploy?",
            ))
        if row.kind == ADD and _FLAG_RE.search(row.source):
            out.append(_signal(
                "cfg.flag", LOW, path, row.no, row.source,
                "a flag or environment lookup. What is the default, and who removes this branch "
                "once it has settled?",
            ))
    return out


def _blast_radius_signals(rows):
    files = {}
    for row in rows:
        if row.kind in (ADD, DEL) and row.path:
            files.setdefault(row.path, 0)
            files[row.path] += 1
    if not files:
        return []

    out = []
    test_files = [p for p in files if _TEST_PATH_RE.search(p)]
    source_files = [p for p in files if p not in test_files]
    top_dirs = {p.split("/")[0] for p in files if "/" in p}

    public_changed = any(
        _PUBLIC_DECL.get(diffmodel.language_of(r.path or ""), None)
        and _PUBLIC_DECL[diffmodel.language_of(r.path or "")].match(r.source.strip())
        for r in rows
        if r.kind == ADD and r.path and not _TEST_PATH_RE.search(r.path)
    )
    if not test_files and source_files:
        if public_changed:
            out.append(_signal(
                "radius.untested-surface", HIGH, None, 0,
                "%d source file(s) changed, 0 test files" % len(source_files),
                "public surface was declared or redeclared with no test file touched. Is the "
                "new behaviour covered somewhere, or is it being taken on trust?",
            ))
        else:
            out.append(_signal(
                "radius.untested-change", MEDIUM, None, 0,
                "%d source file(s) changed, 0 test files" % len(source_files),
                "behaviour changed and no test file moved with it. What would have caught a "
                "mistake in this change?",
            ))
    if len(top_dirs) >= 4:
        out.append(_signal(
            "radius.spread", MEDIUM, None, 0,
            "touches %d top-level areas: %s" % (len(top_dirs), ", ".join(sorted(top_dirs)[:8])),
            "the change spans several areas. Is this one concept that genuinely cuts across "
            "them, or several changes that would review better apart?",
        ))
    return out


def summarize(sigs):
    """Counts by severity, for the report header and the completeness check."""
    counts = {HIGH: 0, MEDIUM: 0, LOW: 0}
    for s in sigs:
        counts[s["severity"]] = counts.get(s["severity"], 0) + 1
    return {"total": len(sigs), "by_severity": counts}


# ── Fit: does the change belong here, and is it the right size? ──────────────
#
# Machine-written code fails differently from hand-written code. It is rarely
# wrong in the small; it is much more often the wrong *size* — an abstraction
# with one user, a helper nothing calls, a block pasted four times, a comment
# restating the line beneath it. These detectors look for those shapes.
#
# Every one of them is a question about proportion, and proportion is a
# judgment. They mark where to look; `references/fit-and-scope.md` is where the
# actual reasoning lives.

_FN_DECL_RE = re.compile(
    r"^(?:pub(?:\([\w:]+\))?\s+)?(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+)?"
    r"(?:static\s+)?(?:async\s+)?(?:def|fn|func|function)\s+([A-Za-z_$][\w$]*)"
)
_ABSTRACTION_RE = re.compile(
    r"^(?:pub\s+|export\s+)?(?:abstract\s+)?(?:trait|interface|protocol)\s+([A-Za-z_]\w*)"
    r"|^(?:export\s+)?abstract\s+class\s+([A-Za-z_]\w*)"
    r"|^class\s+([A-Za-z_]\w*)\s*\(\s*(?:Protocol|ABC)\s*\)"
)
_IMPLEMENTS_RE = re.compile(
    r"^impl\s+(?:<[^>]*>\s*)?([A-Za-z_]\w*)(?:<[^>]*>)?\s+for\b"
    r"|\bimplements\s+([A-Za-z_][\w,\s]*)"
    r"|^class\s+\w+\s*\(\s*([A-Za-z_]\w*)\s*\)"
)
_DELEGATE_RE = re.compile(r"^(?:return\s+)?[\w.]*\(?[\w.]+\s*\([^;{}]*\)\s*[;?]?$")
_COMMENT_RE = re.compile(r"^\s*(?://+|#|--)\s*(.*)$")
_WORD_RE = re.compile(r"[A-Za-z]{3,}")
# Function words carry no information either way, so they neither prove nor
# disprove that a comment is restating the line beneath it.
_STOPWORDS = frozenset("""
the and for with from into that this then than but not are was were will its
our all any each new set get use used using via per out off own same such
""".split())
_PRIVATE_HINT = {"python": "_", "js": "_", "jvm": "_", "csharp": "_"}
# Names a runtime, trait, or framework calls for you. None of them has a caller
# spelled out in the source, so "nothing calls this" says nothing about them.
_PROTOCOL_NAMES = frozenset("""
eq ne cmp partial_cmp hash fmt clone drop default next from into try_from as_ref
deref index len iter new main run serialize deserialize to_string toString
__init__ __repr__ __str__ __eq__ __hash__ __enter__ __exit__ __iter__ __next__
setUp tearDown setup teardown constructor render componentDidMount
""".split())
_TEST_ATTR_RE = re.compile(r"^\s*(?:#\[\s*(?:test|tokio::test|bench)|@(?:pytest|Test|test)\b|@Test\b)")


def _added_functions(rows):
    """Every function declared on an added row, with its body extent.

    The body runs from the declaration to the first later added row whose indent
    returns to the declaration's own — the same indentation model the loop-depth
    detector uses, and it carries the same limit: a body the diff only partly
    touches is only partly seen.
    """
    out = []
    added = [r for r in rows if r.kind == ADD and not _is_prose(r.path)]
    for i, row in enumerate(added):
        m = _FN_DECL_RE.match(row.source.strip())
        if not m:
            continue
        indent = len(row.source) - len(row.source.lstrip())
        body = []
        for nxt in added[i + 1:]:
            if nxt.path != row.path or nxt.hunk_idx != row.hunk_idx:
                break
            if not nxt.source.strip():
                body.append(nxt)
                continue
            if len(nxt.source) - len(nxt.source.lstrip()) <= indent:
                break
            body.append(nxt)
        out.append({"row": row, "name": m.group(1), "indent": indent, "body": body})
    return out


def _param_count(rows, decl_row):
    """Parameters in a declaration, counting commas at bracket depth one."""
    tokens = []
    depth = 0
    started = False
    idx = [r.no for r in rows].index(decl_row.no) if decl_row.no <= len(rows) else 0
    for row in rows[idx:idx + 30]:
        if row.kind != ADD or (row.no != decl_row.no and row.hunk_idx != decl_row.hunk_idx):
            break
        for ch in row.source:
            if ch == "(":
                depth += 1
                started = True
                continue
            if ch == ")":
                depth -= 1
                if started and depth == 0:
                    text = "".join(tokens)
                    if not text.strip():
                        return 0
                    return text.count(",") + 1
                continue
            if started and depth == 1:
                tokens.append(ch)
        if started and depth == 0:
            break
    return 0


def _fit_signals(rows, declarations, baseline):
    budgets = baseline.get("budgets") or {}
    max_params = int(budgets.get("max_params", 5))
    max_rows = int(budgets.get("max_function_rows", 60))
    min_dup = int(budgets.get("min_duplicate_rows", 6))

    out = []
    functions = _added_functions(rows)
    out.extend(_duplicate_block_signals(rows, declarations, min_dup))
    out.extend(_shape_signals(rows, functions, max_params, max_rows))
    out.extend(_speculation_signals(rows, functions))
    out.extend(_restating_comment_signals(rows))
    out.extend(_scope_signals(rows))
    return out


def _duplicate_block_signals(rows, declarations, min_dup):
    """Regions of identical normalised code appearing more than once (DRY).

    Matching is exact once whitespace is collapsed, which keeps precision at the
    cost of recall: a block pasted and then renamed will not be caught. That is
    the deliberate trade — a fuzzy clone detector fires on any two functions
    built the same way, and this family only earns attention by being quiet.

    Overlapping windows are merged into one region per duplicated span. Without
    that, a 40-row copied file reports 35 times and the finding buries itself.
    """
    added = [
        r for r in rows
        if r.kind == ADD and r.no not in declarations
        and not _is_prose(r.path) and r.source.strip()
    ]
    norm = [_WS_RE.sub(" ", r.source.strip()) for r in added]

    seen = {}
    hits = []  # (window_start_index, first_occurrence_row)
    for i in range(len(added) - min_dup + 1):
        window = added[i:i + min_dup]
        if any(window[k + 1].no != window[k].no + 1 for k in range(len(window) - 1)):
            continue
        if len(set(norm[i:i + min_dup])) < 3:
            continue  # a run of near-identical lines is repetition, not duplication
        key = "\n".join(norm[i:i + min_dup])
        if key in seen:
            hits.append((i, seen[key]))
        else:
            seen[key] = window[0]

    # Merge consecutive windows into one region.
    out = []
    idx = 0
    while idx < len(hits):
        start_i, first = hits[idx]
        end_i = start_i
        j = idx
        while j + 1 < len(hits) and hits[j + 1][0] == hits[j][0] + 1:
            j += 1
            end_i = hits[j][0]
        span_rows = (end_i - start_i) + min_dup
        head = added[start_i]
        elsewhere = (
            "%s:%d" % (first.path, first.no) if first.path != head.path
            else "line %d of the same file" % first.no
        )
        out.append(_signal(
            "fit.duplicate-block", HIGH, head.path, head.no, head.source,
            "these %d rows already appear at %s. Is this a shared helper waiting to be "
            "named, or are the two copies genuinely allowed to drift apart?"
            % (span_rows, elsewhere),
        ))
        idx = j + 1

    if len(out) > 12:
        extra = len(out) - 12
        out = out[:12]
        out.append(_signal(
            "fit.duplicate-block", MEDIUM, None, 0,
            "%d further duplicated region(s) not listed" % extra,
            "duplication is pervasive rather than local in this change. Is a whole file or "
            "directory being kept in sync by hand?",
        ))
    return out


def _shape_signals(rows, functions, max_params, max_rows):
    """Size and shape of what was added (SRP, ISP, KISS)."""
    out = []
    for fn in functions:
        row = fn["row"]
        body = [b for b in fn["body"] if b.source.strip()]

        params = _param_count(rows, row)
        if params > max_params:
            out.append(_signal(
                "fit.wide-signature", MEDIUM, row.path, row.no, row.source,
                "`%s` takes %d parameters. Is it doing one job, or has an options object "
                "gone unnamed?" % (fn["name"], params),
            ))

        if len(body) > max_rows:
            out.append(_signal(
                "fit.long-function", MEDIUM, row.path, row.no, row.source,
                "`%s` adds %d rows in one function. What are the two or three things it "
                "does, and do they want separate names?" % (fn["name"], len(body)),
            ))

        exported = bool(re.match(r"^\s*(?:pub\b|export\b|public\b)", row.source)) or (
            diffmodel.language_of(row.path or "") == "go" and fn["name"][:1].isupper()
        )
        if not exported and len(body) == 1 and _DELEGATE_RE.match(body[0].source.strip()):
            out.append(_signal(
                "fit.pass-through", LOW, row.path, row.no, row.source,
                "`%s` only forwards to something else. Does the extra name earn its place, "
                "or is it indirection for its own sake?" % fn["name"],
            ))
    return out


def _speculation_signals(rows, functions):
    """Structure built for a caller that does not exist yet (YAGNI)."""
    out = []
    body_text = " ".join(
        r.source for r in rows if r.kind in (ADD, DEL) and not _is_prose(r.path)
    )

    by_no = {r.no: r for r in rows}
    for fn in functions:
        name = fn["row"] and fn["name"]
        row = fn["row"]
        lang = diffmodel.language_of(row.path or "")

        # A method is reached through a receiver this matching cannot follow, and
        # a trait or test function is called by the language rather than by name.
        if fn["indent"] > 0:
            continue
        if name in _PROTOCOL_NAMES or name.startswith("test") or _NONPROD_RE.search(row.path or ""):
            continue
        prev = by_no.get(row.no - 1)
        if prev is not None and _TEST_ATTR_RE.match(prev.source or ""):
            continue

        private_prefix = _PRIVATE_HINT.get(lang)
        is_private = (
            (private_prefix and name.startswith(private_prefix))
            or (lang == "rust" and not row.source.strip().startswith("pub"))
            or (lang == "go" and name[:1].islower())
        )
        if not is_private:
            continue  # public surface may have callers this diff cannot see
        uses = len(re.findall(r"\b%s\s*\(" % re.escape(name), body_text))
        if uses <= 1:  # the declaration itself
            out.append(_signal(
                "fit.unreferenced-addition", MEDIUM, row.path, row.no, row.source,
                "`%s` is internal and nothing in this change calls it. Is there a caller "
                "elsewhere, or was it written for a need that has not arrived?" % name,
            ))

    for row in rows:
        if row.kind != ADD or _is_prose(row.path):
            continue
        m = _ABSTRACTION_RE.match(row.source.strip())
        if not m:
            continue
        name = next((g for g in m.groups() if g), None)
        if not name:
            continue
        implementors = set()
        for other in rows:
            if other.kind != ADD:
                continue
            im = _IMPLEMENTS_RE.search(other.source.strip())
            if im and name in " ".join(g for g in im.groups() if g):
                implementors.add(other.no)
        if len(implementors) == 1:
            out.append(_signal(
                "fit.abstraction-for-one", MEDIUM, row.path, row.no, row.source,
                "`%s` is introduced with exactly one implementation. What is the second one, "
                "and if there isn't one yet, what does the indirection buy today?" % name,
            ))
    return out


def _restating_comment_signals(rows):
    """A comment whose words are all already in the line beneath it."""
    out = []
    added = [r for r in rows if r.kind == ADD and not _is_prose(r.path)]
    for i, row in enumerate(added[:-1]):
        m = _COMMENT_RE.match(row.source)
        if not m:
            continue
        nxt = added[i + 1]
        if nxt.no != row.no + 1 or _COMMENT_RE.match(nxt.source) or not nxt.source.strip():
            continue
        words = {w.lower() for w in _WORD_RE.findall(m.group(1))} - _STOPWORDS
        if len(words) < 2:
            continue
        code = {w.lower() for w in _WORD_RE.findall(nxt.source)}
        if words <= code:
            out.append(_signal(
                "fit.restating-comment", LOW, row.path, row.no, row.source.strip(),
                "this comment says what the next line already says. Is there a reason for "
                "the code that could be written here instead?",
            ))
    return out


def _scope_signals(rows):
    """Edits that look incidental to whatever the change is actually for."""
    counts = {}
    for row in rows:
        if row.kind in (ADD, DEL) and row.path:
            counts[row.path] = counts.get(row.path, 0) + 1
    if len(counts) < 8:
        return []
    drive_by = sorted(p for p, n in counts.items() if n <= 2)
    if len(drive_by) < 3:
        return []
    return [_signal(
        "fit.drive-by-edits", MEDIUM, None, 0,
        "%d file(s) changed by 2 rows or fewer: %s" % (len(drive_by), ", ".join(drive_by[:6])),
        "several files are touched barely at all. Are those edits part of this change's "
        "purpose, or unrelated repairs that would review and revert better on their own?",
    )]


# ── Agentic residue: what long-running autonomous work leaves behind ─────────
#
# A change produced over many turns by an agent that plans, executes, verifies
# and repairs has a characteristic sediment. Context is compacted between
# segments, so the second half of a session does not remember the first. When a
# check fails the cheapest repair is often to silence it. When the work is
# blocked the cheapest output is a large adjacent change rather than a small
# admission. When completion has to be demonstrated, a test that asserts what
# the code already does demonstrates it.
#
# None of these is unique to agents — a person under deadline produces the same
# shapes. They are worth their own family because they cluster, and because they
# attack the two things a review is supposed to certify: that the change is
# correct, and that someone can maintain it next year.

_SUPPRESSION_RE = re.compile(
    r"#\s*noqa\b|#\s*type:\s*ignore|#\s*pragma:\s*no\s*cover|#\s*pylint:\s*disable"
    r"|#\s*rubocop:disable|#\s*phpcs:ignore|\bNOSONAR\b"
    r"|eslint-disable|biome-ignore|oxlint-disable|deno-lint-ignore"
    r"|@ts-ignore|@ts-expect-error|//\s*nolint|//\s*lint:ignore"
    r"|#!?\[allow\(|@SuppressWarnings|#\s*fmt:\s*off|//\s*prettier-ignore|@Suppress\b"
    r"|swiftlint:disable|checkstyle:off"
)
# A suppression that explains itself is a decision; a bare one is a reflex. Both
# are worth a question, but only one of them is worth interrupting for.
_SUPPRESSION_REASON_RE = re.compile(r":\s*\S+(?:\s+\S+){3,}\s*$")
# The marker words are conventionally shouted, and lowercasing them costs
# precision: `xxx` is a placeholder in half the prompt templates ever written,
# and `hack` is an ordinary English word.
_MARKER_RE = re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b")
_STUB_RE = re.compile(
    r"NotImplementedError|NotImplementedException|unimplemented!|todo!\("
    r"|not\s+implemented|notImplemented", re.I,
)
_TAUTOLOGY_RE = re.compile(
    r"assert\s+True\b|assert\s+1\s*==\s*1|assertTrue\s*\(\s*True\s*\)"
    r"|expect\s*\(\s*true\s*\)\s*\.\s*toBe\s*\(\s*true\s*\)"
    r"|assert_eq!\s*\(\s*true\s*,\s*true\s*\)"
    r"|assert\s+([A-Za-z_][\w.]*)\s*==\s*\1\s*$"
    r"|expect\s*\(\s*([A-Za-z_][\w.]*)\s*\)\s*\.\s*toBe\s*\(\s*\2\s*\)"
)
_ASSERT_RE = re.compile(r"\bassert\w*\s*[!(\s]|\bexpect\s*\(|\.should\b|\brequire\.\w+\(")
_TEST_FN_RE = re.compile(
    r"^(?:async\s+)?(?:def|fn|func|function)\s+(test\w*|\w*_test)\s*\("
    r"|^\s*(?:it|test)\s*\(\s*['\"]"
)
_SLEEP_RE = re.compile(
    r"\btime\.sleep\s*\(|\bsleep\s*\(|thread::sleep|Thread\.sleep"
    r"|setTimeout\s*\(|await\s+delay\s*\(|time\.Sleep\s*\("
)
_MOCK_SETUP_RE = re.compile(r"\b(?:mock|Mock|patch|stub|spy|jest\.fn|MagicMock|sinon)\b")
_CALL_ASSERT_RE = re.compile(
    r"assert_called|assert_has_calls|toHaveBeenCalled|verify\s*\(|\.calledWith\b"
)
_COMMENTED_CODE_RE = re.compile(
    r"^\s*(?://|#)\s*(?:"
    r"(?:if|for|while|return|import|from|let|const|var|def|fn|func|class|await|try)\b.*"
    r"|[\w.\[\]]+\s*=[^=].*"
    r"|[\w.]+\([^)]*\)\s*[;{]?\s*"
    r")$"
)
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACCESS_VERBS = ("get", "fetch", "load", "read", "retrieve", "find", "lookup", "obtain")
_PATH_REF_RE = re.compile(r"(?:^|[\s(`'\"])([\w][\w./-]*/[\w./-]+\.[A-Za-z0-9]{1,6})")


def _agentic_signals(rows, declarations, baseline, repo_root=None):
    out = []
    out.extend(_suppression_signals(rows))
    out.extend(_unfinished_signals(rows))
    out.extend(_test_quality_signals(rows))
    out.extend(_leftover_signals(rows))
    out.extend(_naming_drift_signals(rows))
    out.extend(_narration_signals(rows))
    if repo_root:
        out.extend(_dangling_reference_signals(rows, repo_root))
    return out


def _suppression_signals(rows):
    """A check fired and the change turned the check off."""
    out = []
    for row in rows:
        if row.kind != ADD or _is_prose(row.path):
            continue
        if not _SUPPRESSION_RE.search(row.source):
            continue
        explained = bool(_SUPPRESSION_REASON_RE.search(row.source.rstrip()))
        if explained:
            out.append(_signal(
                "agentic.suppressed-warning", MEDIUM, row.path, row.no, row.source,
                "a check is silenced here, with a stated reason. Does the reason hold, and "
                "is the suppression scoped to just this case?",
            ))
        else:
            out.append(_signal(
                "agentic.suppressed-warning", HIGH, row.path, row.no, row.source,
                "a tool reported something here and this silences it without saying why. Was "
                "the finding wrong, or was it right and inconvenient?",
            ))
    return out


def _unfinished_signals(rows):
    """Work declared incomplete inside a change presented as complete."""
    out = []
    for row in rows:
        if row.kind != ADD or _is_prose(row.path):
            continue
        if _MARKER_RE.search(row.source) or _STUB_RE.search(row.source):
            out.append(_signal(
                "agentic.unfinished-work", HIGH, row.path, row.no, row.source,
                "this marks work that is not done, inside a change offered as done. Which part "
                "of the original request does it correspond to, and who is expected to finish it?",
            ))
    return out


def _test_quality_signals(rows):
    """Tests that demonstrate completion without establishing correctness."""
    out = []
    added = [r for r in rows if r.kind == ADD]

    for row in added:
        if _TAUTOLOGY_RE.search(row.source):
            out.append(_signal(
                "agentic.tautological-test", HIGH, row.path, row.no, row.source,
                "this assertion cannot fail. What behaviour was it meant to pin down, and "
                "would the test still pass if that behaviour were removed?",
            ))
        if _is_test_path(row.path) and _SLEEP_RE.search(row.source):
            out.append(_signal(
                "agentic.sleep-in-test", MEDIUM, row.path, row.no, row.source,
                "a sleep in a test usually means a race nobody has named. What is it waiting "
                "for, and can that be waited on directly?",
            ))

    # A test function whose whole body asserts nothing.
    by_no = {r.no: r for r in added}
    for row in added:
        if not _is_test_path(row.path) or not _TEST_FN_RE.match(row.source.strip()):
            continue
        indent = len(row.source) - len(row.source.lstrip())
        body, n = [], row.no + 1
        while n in by_no:
            nxt = by_no[n]
            if nxt.source.strip() and len(nxt.source) - len(nxt.source.lstrip()) <= indent:
                break
            body.append(nxt)
            n += 1
        if body and not any(_ASSERT_RE.search(b.source) for b in body):
            out.append(_signal(
                "agentic.assertionless-test", HIGH, row.path, row.no, row.source.strip(),
                "this test runs code and checks nothing. What would it catch that simply "
                "calling the function would not?",
            ))

    mock_files = {}
    for row in added:
        if not _is_test_path(row.path):
            continue
        seen = mock_files.setdefault(row.path, {"setup": 0, "calls": 0, "row": row})
        if _MOCK_SETUP_RE.search(row.source):
            seen["setup"] += 1
        if _CALL_ASSERT_RE.search(row.source):
            seen["calls"] += 1
    for path, seen in sorted(mock_files.items()):
        if seen["calls"] >= 3 and seen["setup"] >= 3:
            out.append(_signal(
                "agentic.implementation-coupled-test", MEDIUM, path, seen["row"].no,
                "%d mock setup(s) and %d call assertion(s)" % (seen["setup"], seen["calls"]),
                "these tests assert how the code works rather than what it does. Will they "
                "survive a refactor that keeps the behaviour?",
            ))
    return out


def _is_test_path(path):
    return bool(_TEST_PATH_RE.search(path or "") or _NONPROD_RE.search(path or ""))


def _leftover_signals(rows):
    """Code that was written, abandoned, and commented out rather than removed."""
    out = []
    runs = []
    current = []
    for row in rows:
        if (row.kind == ADD and not _is_prose(row.path)
                and _COMMENTED_CODE_RE.match(row.source)):
            if current and row.no == current[-1].no + 1 and row.path == current[-1].path:
                current.append(row)
            else:
                if len(current) >= 2:
                    runs.append(current)
                current = [row]
        else:
            if len(current) >= 2:
                runs.append(current)
            current = []
    if len(current) >= 2:
        runs.append(current)

    for run in runs[:8]:
        out.append(_signal(
            "agentic.commented-out-code", MEDIUM, run[0].path, run[0].no,
            run[0].source.strip(),
            "%d rows of code are added commented out. Is this an approach that was abandoned "
            "mid-change? Version control already remembers it." % len(run),
        ))
    return out


def _naming_drift_signals(rows):
    """One concept given two names inside a single change.

    A long session compacts its own context, so the half that writes `fetchUser`
    may not remember the half that wrote `getUser`. Two names for one operation
    is the cheapest observable trace of that seam.
    """
    concepts = {}
    for row in rows:
        if row.kind != ADD or _is_prose(row.path):
            continue
        m = _FN_DECL_RE.match(row.source.strip())
        if not m:
            continue
        name = m.group(1)
        words = [w.lower() for w in _CAMEL_SPLIT_RE.split(name.replace("_", " ")).__iter__()]
        words = [w for part in words for w in part.split()]
        if len(words) < 2:
            continue
        verb, rest = words[0], "".join(words[1:])
        if verb not in _ACCESS_VERBS or not rest:
            continue
        concepts.setdefault(rest, {})[verb] = row

    out = []
    for noun, verbs in sorted(concepts.items()):
        if len(verbs) < 2:
            continue
        first = sorted(verbs.values(), key=lambda r: r.no)[0]
        names = ", ".join("`%s%s`" % (v, noun) for v in sorted(verbs))
        out.append(_signal(
            "agentic.naming-drift", MEDIUM, first.path, first.no, first.source.strip(),
            "this change introduces %s for what looks like one operation. Are they genuinely "
            "different, or did one half of the work not know about the other?" % names,
        ))
    return out


def _narration_signals(rows):
    """Prose produced alongside the change, measured against the change itself."""
    prose = sum(1 for r in rows if r.kind == ADD and _is_prose(r.path))
    code = sum(1 for r in rows if r.kind == ADD and not _is_prose(r.path))
    if code < 20 or prose < 100 or prose < code:
        return []
    return [_signal(
        "agentic.narration-heavy", LOW, None, 0,
        "%d rows of prose against %d rows of code" % (prose, code),
        "the change carries more documentation than code. Does the prose describe what "
        "shipped, or what was planned? Documentation that outruns the code goes stale first.",
    )]


def _dangling_reference_signals(rows, repo_root):
    """A path cited in added prose or comments that does not exist in the tree.

    Only runs with `--repo`, because it is the one check here that cannot be done
    from the diff alone. Fabricated citations are the failure mode that most
    directly attacks a review's usefulness as a final answer: they read as
    evidence and are not.
    """
    out = []
    seen = set()
    changed = {r.path for r in rows if r.path}
    for row in rows:
        if row.kind != ADD:
            continue
        text = row.source
        if not _is_prose(row.path):
            m = _COMMENT_RE.match(text)
            if not m:
                continue
            text = m.group(1)
        for ref in _PATH_REF_RE.findall(text):
            ref = ref.rstrip(".,);:")
            if ref in seen or ref in changed or "://" in ref:
                continue
            if os.path.exists(os.path.join(repo_root, ref)):
                continue
            seen.add(ref)
            out.append(_signal(
                "agentic.dangling-reference", MEDIUM, row.path, row.no, row.source.strip(),
                "`%s` is cited here and is not in the repository. Was it renamed, is it "
                "arriving in another change, or was it never there?" % ref,
            ))
    return out[:10]
