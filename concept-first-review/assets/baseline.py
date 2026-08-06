#!/usr/bin/env python3
"""Derive a candidate `design-rules.json` from the repository as it is.

The authoring advice for a baseline has always been "write down the rules the
team already believes, not the rules you wish it followed" — because a baseline
that flags every existing file on day one gets deleted in a week. This module
does the derivation half of that mechanically.

It reads the tree, builds the dependency graph between directories, counts what
is imported from where, and measures the shapes already present. From that it
proposes rules, and for every proposal it reports the fact that actually decides
the question: **how many places in the codebase violate it today**. A rule with
zero current violations is free to adopt. A rule with forty is a migration, and
the person answering deserves to know which one they are agreeing to.

What it cannot derive is intent. An edge that does not exist today may be
forbidden or may simply not have come up yet, and no amount of scanning
distinguishes those. So the output is not a baseline — it is a set of
**questions with evidence attached**, shaped for the host agent's structured
question tool. The human answers; `apply_answers` writes the file.
"""

import json
import os
import re

import diffmodel
import signals as signalslib

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build", "target",
    "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    ".next", ".nuxt", "coverage", ".idea", ".vscode",
}
CODE_EXT = tuple(signalslib._NON_CODE_EXT)  # noqa: SLF001 - shared definition
MAX_FILE_BYTES = 400_000

_IMPORT_LINE_RE = re.compile(
    r"^\s*(?:from\s+[\w.]+\s+import\b|import\b|#\s*include\b|(?:pub\s+)?use\b"
    r"|(?:const|let|var)\s+.*=\s*require\s*\(|export\s+.*\bfrom\b|using\s+[\w.]+\s*;)"
)
_FN_RE = re.compile(
    r"^(?:pub(?:\([\w:]+\))?\s+)?(?:export\s+)?(?:default\s+)?"
    r"(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:async\s+)?"
    r"(?:def|fn|func|function)\s+([A-Za-z_$][\w$]*)"
)
_LOOP_RE = re.compile(r"^(?:for|while)\b|^(?:for|while)\s*\(")
# Full import targets, not just their first segment. `signals._module_of` keeps
# only the root for Rust, which is right for "is this third-party" and useless
# for "which directory does this reach into".
_TARGET_RE = {
    "python": re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\b|import\s+([\w.]+))"),
    "rust": re.compile(r"^\s*(?:pub\s+)?use\s+([\w:]+)"),
    "go": re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"'),
    "js": re.compile(r"""from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]"""),
    "jvm": re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)"),
    "csharp": re.compile(r"^\s*using\s+([\w.]+)\s*;"),
}
# Source roots a language convention hides from its own import paths: Rust
# `crate::storage` lives at `src/storage`, and the module path never says `src`.
_SOURCE_PREFIXES = ("", "src/", "lib/", "pkg/", "internal/", "app/", "packages/")
_CRATE_SELF = ("crate", "self", "super")


# ── Scanning ─────────────────────────────────────────────────────────────────

def _iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git"))
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if rel.startswith(".."):
                continue
            if diffmodel.language_of(rel) == "generic" or rel.lower().endswith(CODE_EXT):
                continue
            yield rel


def _import_target(line, lang, crate_name):
    """The full module path an import names, normalised toward a repo path."""
    pattern = _TARGET_RE.get(lang)
    if not pattern:
        return None
    m = pattern.search(line)
    if not m:
        return None
    raw = next((g for g in m.groups() if g), None)
    if not raw:
        return None
    parts = re.split(r"[.:/]+", raw.strip("./"))
    parts = [p for p in parts if p]
    while parts and (parts[0] in _CRATE_SELF or parts[0] == crate_name):
        parts.pop(0)
    return "/".join(parts) if parts else None


def _area_of(path):
    """The directory a rule would name. Top-level, or `top/second` when the top
    level is a bare container like `src` or `lib` that says nothing on its own."""
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    if parts[0] in ("src", "lib", "pkg", "internal", "app", "packages") and len(parts) > 2:
        return "%s/%s" % (parts[0], parts[1])
    return parts[0]


def scan(root):
    """Read the tree once. Returns the evidence every proposal is built from."""
    areas = {}
    edges = {}
    external = {}
    fn_params = []
    fn_rows = []
    max_depth = 0
    files = 0

    # A crate/package that imports itself by name is reaching inside, not out.
    crate_name = os.path.basename(os.path.abspath(root)).replace("-", "_")
    known = set()
    for rel in _iter_files(root):
        known.add(rel)
    for rel in sorted(known):
        full = os.path.join(root, rel)
        try:
            if os.path.getsize(full) > MAX_FILE_BYTES:
                continue
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().split("\n")
        except OSError:
            continue

        files += 1
        area = _area_of(rel)
        areas[area] = areas.get(area, 0) + 1
        lang = diffmodel.language_of(rel)

        stack = []
        for line in lines:
            if _IMPORT_LINE_RE.match(line):
                target = _resolve(_import_target(line, lang, crate_name), known)
                if target:
                    if target != area:
                        edges.setdefault((area, target), 0)
                        edges[(area, target)] += 1
                elif not line[:1].isspace():
                    # Only a top-level import declares a dependency. An indented
                    # one is pulling a name into a local scope — in Rust that is
                    # how enum variants are used, and counting it lists every
                    # local type as a third-party package.
                    module = signalslib._module_of(line, lang)  # noqa: SLF001
                    if module and signalslib._is_external(module, lang):  # noqa: SLF001
                        name = module.split("/")[0].split(".")[0].split("::")[0]
                        if name != crate_name:
                            external[name] = external.get(name, 0) + 1
                continue

            stripped = line.strip()
            if not stripped:
                continue
            indent = len(line) - len(line.lstrip())
            while stack and stack[-1][0] >= indent:
                stack.pop()
            depth = sum(1 for _, is_loop in stack if is_loop)
            opens_loop = bool(_LOOP_RE.match(stripped))
            if opens_loop:
                max_depth = max(max_depth, depth + 1)
            stack.append((indent, opens_loop))

            m = _FN_RE.match(stripped)
            if m:
                fn_params.append(stripped.count(",") + 1 if "(" in stripped
                                 and not re.search(r"\(\s*\)", stripped) else 0)

        fn_rows.extend(_function_lengths(lines))

    return {
        "root": root,
        "files": files,
        "areas": areas,
        "edges": {"%s->%s" % k: v for k, v in sorted(edges.items())},
        "external": external,
        "observed": {
            "max_loop_depth": max_depth,
            "params_p90": _percentile(fn_params, 90),
            "params_max": max(fn_params) if fn_params else 0,
            "function_rows_p90": _percentile(fn_rows, 90),
            "function_rows_max": max(fn_rows) if fn_rows else 0,
            "functions": len(fn_rows),
        },
    }


def _function_lengths(lines):
    out = []
    open_fn = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if open_fn is not None and indent <= open_fn[0]:
            out.append(open_fn[1])
            open_fn = None
        if _FN_RE.match(stripped):
            open_fn = (indent, 0)
        elif open_fn is not None:
            open_fn = (open_fn[0], open_fn[1] + 1)
    if open_fn is not None:
        out.append(open_fn[1])
    return out


def _percentile(values, pct):
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def _resolve(target, known):
    """Map an import target to an area of this repo, or None when it is external.

    An import path is not a file path at either end. It may carry a leading
    hosting domain or crate root the layout does not have (`github.com/org/pkg`),
    and it usually ends in the *symbol* being imported rather than a directory
    (`pub use executor::GqlExecutor`). So candidates are generated by dropping
    segments from both ends, and a surviving single segment is matched against
    module basenames — which is how a sibling module gets referred to in Rust and
    Go without any path at all.
    """
    if not target:
        return None
    parts = [p for p in target.split("/") if p]
    if not parts:
        return None

    candidates = []
    for start in range(len(parts)):
        for end in range(len(parts), start, -1):
            tail = "/".join(parts[start:end])
            for prefix in _SOURCE_PREFIXES:
                candidates.append(prefix + tail)

    for cand in candidates:
        if not cand:
            continue
        for path in known:
            if path == cand or path.startswith(cand + "/") or os.path.splitext(path)[0] == cand:
                return _area_of(path)

    for segment in parts:
        for path in known:
            if os.path.splitext(os.path.basename(path))[0] == segment:
                return _area_of(path)
    return None


# ── Proposing ────────────────────────────────────────────────────────────────

MIN_AREA_FILES = 3
MAX_LAYERS = 6
MAX_EDGE_CANDIDATES = 4
MAX_EDGE_QUESTIONS = 3


def propose(evidence):
    """Turn evidence into candidate rules, each with its cost of adoption."""
    areas = evidence["areas"]
    def rank(item):
        area, count = item
        # A test or example directory is a real place, but naming it as an
        # architectural layer crowds out the ones a rule would actually be about.
        nonprod = bool(signalslib._NONPROD_RE.search(area + "/"))  # noqa: SLF001
        return (nonprod, -count, area)

    layers = [a for a, n in sorted(areas.items(), key=rank)
              if n >= MIN_AREA_FILES and a != "(root)"][:MAX_LAYERS]

    edges = {}
    for key, count in evidence["edges"].items():
        src, dst = key.split("->", 1)
        if src in layers and dst in layers:
            edges[(src, dst)] = count

    candidates = []
    for src in layers:
        for dst in layers:
            if src == dst:
                continue
            forward = edges.get((src, dst), 0)
            reverse = edges.get((dst, src), 0)
            if forward == 0 and reverse > 0:
                # Traffic runs one way today. Forbidding the other direction
                # costs nothing now and is the rule most likely to be real.
                candidates.append({
                    "kind": "one-way", "from": src, "to": dst, "violations_today": 0,
                    "evidence": "%s imports %s %d time(s); %s never imports %s"
                                % (dst, src, reverse, src, dst),
                })
            elif forward and reverse and src < dst:
                # One cycle, asked once. Emitting both directions separately puts
                # the same decision to the user twice and offers no way to say
                # which way the dependency should run.
                candidates.append({
                    "kind": "cycle", "from": src, "to": dst,
                    "violations_today": min(forward, reverse),
                    "forward": forward, "reverse": reverse,
                    "evidence": "%s->%s %d time(s) and %s->%s %d time(s)"
                                % (src, dst, forward, dst, src, reverse),
                })
    def interest(c):
        # A cycle between two real layers is the finding worth an interruption,
        # even though adopting it costs a migration. "Source must not import
        # tests" is free and obvious, and asking it spends attention on nothing.
        nonprod = any(signalslib._NONPROD_RE.search(a + "/")  # noqa: SLF001
                      for a in (c["from"], c["to"]))
        is_cycle = c["violations_today"] > 0
        return (nonprod, not is_cycle, c["violations_today"], -_edge_weight(edges, c))

    candidates.sort(key=interest)

    externals = sorted(evidence["external"].items(), key=lambda kv: (-kv[1], kv[0]))
    observed = evidence["observed"]
    return {
        "layers": layers,
        "layer_files": {a: areas[a] for a in layers},
        "forbidden_edge_candidates": candidates[:MAX_EDGE_CANDIDATES],
        "external_roots": [{"name": n, "uses": c} for n, c in externals],
        "budget_candidates": {
            "max_loop_depth": {
                "observed_max": observed["max_loop_depth"],
                "proposed": max(1, observed["max_loop_depth"]),
            },
            "max_params": {
                "observed_p90": observed["params_p90"],
                "observed_max": observed["params_max"],
                "proposed": max(3, observed["params_p90"]),
            },
            "max_function_rows": {
                "observed_p90": observed["function_rows_p90"],
                "observed_max": observed["function_rows_max"],
                "proposed": max(20, observed["function_rows_p90"]),
            },
        },
        "files_scanned": evidence["files"],
    }


def _edge_weight(edges, cand):
    return edges.get((cand["to"], cand["from"]), 0)


# ── Questions ────────────────────────────────────────────────────────────────

MAX_QUESTIONS_PER_BATCH = 4
MAX_OPTIONS = 4
MAX_HEADER = 12


def to_questions(proposal):
    """Render the proposal as batches for the host agent's question tool.

    Shaped to that tool's limits — at most four questions per batch, two to four
    options each, short headers — so the agent can pass a batch straight through
    without reshaping it and without exceeding what the tool accepts.

    Only genuine choices become questions. Anything the evidence already settles
    is applied without asking; a question whose answer is obvious wastes the one
    resource this whole exchange is spending, which is the user's attention.
    """
    questions = []

    # The question tool takes at most four options, and a repo can easily have
    # six areas worth naming. Splitting beats truncating: a layer that is never
    # offered can never appear in a rule, so dropping one silently removes
    # findings the scan already paid for.
    areas = proposal["layers"]
    chunks = [areas[i:i + MAX_OPTIONS] for i in range(0, len(areas), MAX_OPTIONS)]
    for n, chunk in enumerate(chunks):
        questions.append({
            "header": "Layers" if len(chunks) == 1 else "Layers %d/%d" % (n + 1, len(chunks)),
            "question": "Only a named layer can appear in a dependency rule. Which of these "
                        "directories are architectural layers worth naming?",
            "multiSelect": True,
            "options": [
                {"label": area,
                 "description": "%d file(s). Naming it lets rules refer to it."
                                % proposal["layer_files"][area]}
                for area in chunk
            ],
        })

    for cand in proposal["forbidden_edge_candidates"][:MAX_EDGE_QUESTIONS]:
        src, dst = cand["from"], cand["to"]
        header = ("%s>%s" % (src[:5], dst[:5]))[:MAX_HEADER]
        if cand.get("kind") == "cycle":
            questions.append({
                "header": ("cyc %s" % src[:8])[:MAX_HEADER],
                "question": "`%s` and `%s` import each other: %s. Which direction should be "
                            "the rule?" % (src, dst, cand["evidence"]),
                "multiSelect": False,
                "options": [
                    {"label": "%s must not use %s" % (src, dst),
                     "description": "Commits to removing %d existing import(s)."
                                    % cand["forward"]},
                    {"label": "%s must not use %s" % (dst, src),
                     "description": "Commits to removing %d existing import(s)."
                                    % cand["reverse"]},
                    {"label": "Both directions are fine",
                     "description": "The cycle is intentional; no rule is recorded."},
                ],
            })
            continue
        questions.append({
            "header": header,
            "question": "Today %s. Should `%s` be forbidden from importing `%s`?"
                        % (cand["evidence"], src, dst),
            "multiSelect": False,
            "options": [
                {"label": "Yes, forbid it",
                 "description": "Nothing violates this today — free to adopt."},
                {"label": "No, it is allowed",
                 "description": "The direction is intentional; no rule is recorded."},
                {"label": "Not sure yet",
                 "description": "Left out. A wrong rule is worse than a missing one."},
            ],
        })

    roots = proposal["external_roots"]
    if roots:
        top = ", ".join(r["name"] for r in roots[:5])
        questions.append({
            "header": "Deps",
            "question": "This repository already uses %d dependency root(s): %s%s. Treat that "
                        "as the accepted set, so only a *new* one is flagged?"
                        % (len(roots), top, ", and others" if len(roots) > 5 else ""),
            "multiSelect": False,
            "options": [
                {"label": "Yes, freeze the list",
                 "description": "Existing dependencies pass; anything new asks at high severity."},
                {"label": "No, stay heuristic",
                 "description": "Every new dependency asks at medium, listed or not."},
            ],
        })

    budgets = proposal["budget_candidates"]
    fn = budgets["max_function_rows"]
    questions.append({
        "header": "Budgets",
        "question": "This codebase's 90th-percentile function is %d rows (longest %d) and "
                    "its deepest loop nest is %d. Where should the size budgets sit?"
                    % (fn["observed_p90"], fn["observed_max"],
                       budgets["max_loop_depth"]["observed_max"]),
        "multiSelect": False,
        "options": [
            {"label": "Match today's code",
             "description": "Budgets at the 90th percentile: only outliers ask a question."},
            {"label": "Tighter than today",
             "description": "Defaults (5 params, 60 rows, depth 1). More questions, more noise "
                            "at first."},
            {"label": "Skip budgets",
             "description": "No size questions at all; the other rules still apply."},
        ],
    })

    return [questions[i:i + MAX_QUESTIONS_PER_BATCH]
            for i in range(0, len(questions), MAX_QUESTIONS_PER_BATCH)]


# ── Applying ─────────────────────────────────────────────────────────────────

DEFAULT_BUDGETS = {"max_loop_depth": 1, "max_params": 5,
                   "max_function_rows": 60, "min_duplicate_rows": 6}


def apply_answers(proposal, answers):
    """Build `design-rules.json` from the proposal plus what the human chose.

    Unanswered questions are treated as declined. A baseline is a set of claims
    somebody has to stand behind, and silence is not agreement.
    """
    chosen_layers = answers.get("layers")
    if chosen_layers is None:
        chosen_layers = list(proposal["layers"])
    layers = {name: [name + "/"] for name in chosen_layers if name in proposal["layers"]}

    forbidden = []
    dropped = []
    edge_answers = answers.get("edges") or {}
    for cand in proposal["forbidden_edge_candidates"]:
        src, dst = cand["from"], cand["to"]
        choice = edge_answers.get("%s->%s" % (src, dst))
        # A one-way candidate takes true/false; a cycle takes the direction to
        # forbid, so the answer says which way the dependency should run.
        if choice is True:
            pair = [src, dst]
        elif isinstance(choice, str) and "->" in choice:
            a, b = choice.split("->", 1)
            pair = [a.strip(), b.strip()]
        else:
            continue
        if pair[0] in layers and pair[1] in layers:
            forbidden.append(pair)
        else:
            missing = [p for p in pair if p not in layers]
            dropped.append("%s -> %s: needs layer(s) %s, which were not adopted"
                           % (pair[0], pair[1], ", ".join(missing)))

    allowed = []
    if answers.get("freeze_dependencies"):
        allowed = [r["name"] for r in proposal["external_roots"]]

    mode = answers.get("budgets", "skip")
    if mode == "observed":
        cands = proposal["budget_candidates"]
        budgets = {
            "max_loop_depth": cands["max_loop_depth"]["proposed"],
            "max_params": cands["max_params"]["proposed"],
            "max_function_rows": cands["max_function_rows"]["proposed"],
            "min_duplicate_rows": DEFAULT_BUDGETS["min_duplicate_rows"],
        }
    elif mode == "strict":
        budgets = dict(DEFAULT_BUDGETS)
    else:
        budgets = {}

    rules = {
        "_comment": "Derived from %d scanned file(s), then confirmed by a human. "
                    "Edit freely; see references/design-baseline.md."
                    % proposal["files_scanned"],
        "layers": layers,
        "forbidden_edges": forbidden,
        "allowed_external": allowed,
    }
    if budgets:
        rules["budgets"] = budgets
    invariants = answers.get("invariants") or []
    if invariants:
        rules["invariants"] = invariants
    # A rule can only name an adopted layer. Answering a question and getting
    # nothing recorded, with no explanation, is worse than not asking.
    rules["_dropped"] = dropped
    return rules


def summarize(proposal):
    """A short human-readable account of what was found, before any questions."""
    lines = ["scanned %d code file(s)" % proposal["files_scanned"]]
    if proposal["layers"]:
        lines.append("candidate layers: " + ", ".join(
            "%s (%d)" % (a, proposal["layer_files"][a]) for a in proposal["layers"]))
    else:
        lines.append("no directory holds enough files to be worth naming as a layer")
    for cand in proposal["forbidden_edge_candidates"]:
        lines.append("  edge %s -> %s: %d violation(s) today — %s"
                     % (cand["from"], cand["to"], cand["violations_today"], cand["evidence"]))
    roots = proposal["external_roots"]
    lines.append("dependency roots in use: %d" % len(roots))
    obs = proposal["budget_candidates"]
    lines.append("observed shapes: p90 function %d rows, p90 params %d, deepest loop nest %d"
                 % (obs["max_function_rows"]["observed_p90"],
                    obs["max_params"]["observed_p90"],
                    obs["max_loop_depth"]["observed_max"]))
    return "\n".join(lines)


def write_proposal(directory, proposal, questions):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "proposal.json"), "w", encoding="utf-8") as fh:
        json.dump(proposal, fh, indent=2)
        fh.write("\n")
    with open(os.path.join(directory, "questions.json"), "w", encoding="utf-8") as fh:
        json.dump(questions, fh, indent=2)
        fh.write("\n")
