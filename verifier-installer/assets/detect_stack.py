#!/usr/bin/env python3
"""detect_stack.py — emit a deterministic JSON verifier-installation plan for a repo.

Part of the verifier-installer skill. Walks a repository tree offline (stdlib
only — no network, no subprocesses), detects the stacks present, records which
verifier rails (format / build / test / ci) already exist, and proposes what to
create for each missing rail. All lists are sorted and the JSON is dumped with
sorted keys, so the same tree always yields byte-identical output.

Usage:
    python3 detect_stack.py <repo-dir>

Exit codes: 0 on success (plan printed to stdout, even when the repo has
recoverable problems — those land in the plan's "errors" list), 2 on bad usage
or a missing directory.
"""

import json
import os
import re
import sys

RAILS = ("build", "ci", "format", "test")

# Directories never worth descending into (vendored deps, caches, VCS innards).
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", "target", ".tox", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".eggs",
}

# Highest-priority stack first: when several stacks supply the same rail, the
# earliest stack in this order wins. Deterministic by construction.
STACK_PRIORITY = ("make", "node", "python", "go", "rust")

LOCKFILE_NAMES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")

# Per-stack proposals for a missing rail: rail -> (command, file_to_create).
STACK_PROPOSALS = {
    "python": {
        # No "format" entry here: compileall checks syntax, not formatting,
        # and can never go red on a formatting violation. A python repo gets
        # a formatter-check proposal only when it already adopts one — see
        # _python_formatter() and its use in detect(). Otherwise the format
        # rail falls through to FALLBACK_PROPOSALS["format"] (the `make
        # format` placeholder), which stays red until the owner wires one.
        "build": ("python3 -m compileall -q -f .", "Makefile"),  # -f: without
        # it, compileall skips a file whose .pyc header (incl. whole-second
        # mtime) still matches, so a syntax error introduced in the same
        # second as the last run goes undetected (a false green).
        "test": ("python3 -m unittest discover -s tests", "tests/test_smoke.py"),
    },
    "node": {
        "format": ("npm run format", "package.json"),
        "build": ("npm run build", "package.json"),
        "test": ("npm test", "package.json"),
    },
    "go": {
        # gofmt -l lists misformatted files but exits 0 regardless — it never
        # fails the rail on its own. `test -z "$(...)"` fails whenever gofmt
        # lists anything, which is what a "format" rail must do.
        "format": ('test -z "$(gofmt -l .)"', "Makefile"),
        "build": ("go build ./...", "Makefile"),
        "test": ("go test ./...", "Makefile"),
    },
    "rust": {
        "format": ("cargo fmt --check", "Makefile"),
        "build": ("cargo build", "Makefile"),
        "test": ("cargo test", "Makefile"),
    },
}

# Fallback for a bare repo (or a stack with no mapping): a Makefile skeleton.
FALLBACK_PROPOSALS = {
    "format": ("make format", "Makefile"),
    "build": ("make build", "Makefile"),
    "test": ("make test", "Makefile"),
}

CI_PROPOSAL = (
    "push a branch / open a PR — the workflow runs the other rails",
    ".github/workflows/verify.yml",
)

NPM_PLACEHOLDER = "no test specified"


def _walk_files(root):
    """Sorted, /-separated relative paths of every file under root."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def _read(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _make_targets(text):
    """Top-level Makefile target names (rule lines, not variable assignments)."""
    targets = set()
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9_.-]*)\s*:{1,2}(?!=)", line)
        if m:
            targets.add(m.group(1))
    return targets


def _detect_make(root, fileset):
    """Return (present, rail->command) for a root Makefile."""
    if "Makefile" not in fileset:
        return False, {}
    targets = _make_targets(_read(root, "Makefile"))
    rails = {}
    for name in ("format", "fmt", "lint"):
        if name in targets:
            rails["format"] = "make %s" % name
            break
    if "build" in targets:
        rails["build"] = "make build"
    for name in ("test", "check"):
        if name in targets:
            rails["test"] = "make %s" % name
            break
    return True, rails


def _detect_node(root, fileset, errors):
    if "package.json" not in fileset:
        return False, {}
    rails = {}
    try:
        pkg = json.loads(_read(root, "package.json"))
        scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
        if not isinstance(scripts, dict):
            scripts = {}
    except ValueError:
        errors.append({
            "file": "package.json",
            "error": "invalid JSON — could not read scripts; treating as none",
        })
        scripts = {}
    test_script = scripts.get("test", "")
    if test_script and NPM_PLACEHOLDER not in test_script:
        rails["test"] = "npm test"
    if scripts.get("build"):
        rails["build"] = "npm run build"
    if scripts.get("format"):
        rails["format"] = "npm run format"
    elif scripts.get("lint"):
        rails["format"] = "npm run lint"
    return True, rails


def _detect_python(root, files, fileset):
    markers = [m for m in ("pyproject.toml", "setup.py", "setup.cfg") if m in fileset]
    reqs = [f for f in files if re.match(r"^requirements[A-Za-z0-9_.-]*\.txt$", f)]
    if not markers and not reqs:
        return False, {}
    rails = {}
    blob = ""
    for rel in markers + reqs + (["tox.ini"] if "tox.ini" in fileset else []):
        blob += _read(root, rel)
    has_pytest = "pytest.ini" in fileset or "pytest" in blob
    test_files = [
        f for f in files
        if re.search(r"(^|/)test_[^/]+\.py$", f) or f.endswith("_test.py")
    ]
    if has_pytest:
        rails["test"] = "python3 -m pytest"
    elif test_files:
        rails["test"] = "python3 -m unittest discover"
    return True, rails


# _python_formatter matches STRUCTURE, not bare words — a bare `\bruff\b` /
# `\bblack\b` scan (the original implementation) also matched a `# black
# compat` comment, a `description = "Paint it black"` string, an unrelated
# `black-magic==1.0` / `ruff-lint-only` requirement, and a lint-only
# `[tool.ruff.lint]` section with no formatter behind it. Each pattern below
# anchors to a specific structural position instead.
_RUFF_HEADER_RE = re.compile(r"(?m)^\s*(\[tool\.ruff(?:\.[A-Za-z0-9_]+)?\])")
_BLACK_HEADER_RE = re.compile(r"(?m)^\s*\[tool\.black(?:\.[A-Za-z0-9_]+)?\]")
# A requirement/dependency LINE: `ruff`, `ruff==1.0`, `ruff[cli]`, `ruff;
# python_version<"3.12"` all match; `black-magic==1.0` and `ruff-lint-only`
# do not, because the character right after the tool name is `-`, which is
# in neither the version/extras class nor end-of-line.
_REQUIREMENT_LINE_RE = re.compile(r"(?m)^\s*(ruff|black)\s*(\[[^\]]*\])?\s*([=<>!~;@]|$)")
# A quoted pyproject dependency STRING: `"ruff>=0.1"`, `'ruff'`, `"ruff[cli]"`.
_DEP_STRING_RE = re.compile(r"[\"'](ruff|black)\s*([=<>!~\[;]|[\"'])")
# A pre-commit hook entry naming the tool's repo or hook id specifically.
_PRECOMMIT_RE = re.compile(r"astral-sh/ruff-pre-commit|psf/black|id:\s*(ruff-format|black)\b")


def _strip_hash_comments(text):
    """Drop everything from an unquoted `#` to end of line.

    Coarse — it doesn't track whether the `#` is inside a quoted string —
    but that is the safe direction: at worst it under-detects, which
    declines to the honest placeholder rather than imposing a formatter
    nobody adopted.
    """
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _name_matches(pattern, blob, name):
    return any(m.group(1) == name for m in pattern.finditer(blob))


def _precommit_adopts(blob):
    """Return (ruff_adopted, black_adopted) from pre-commit hook evidence."""
    ruff = black = False
    for m in _PRECOMMIT_RE.finditer(blob):
        if m.group(0) == "astral-sh/ruff-pre-commit" or m.group(1) == "ruff-format":
            ruff = True
        if m.group(0) == "psf/black" or m.group(1) == "black":
            black = True
    return ruff, black


def _python_formatter(root, files, fileset):
    """The formatter-check command a python repo already adopts, else None.

    Detects adoption the same way a human would: a ruff/black config
    section, config file, pre-commit hook, or a pinned dependency — never a
    bare word, so a comment, a description string, or an unrelated package
    name can't trip it (see the pattern comments above). This skill MAY
    propose a formatter but SHOULD NOT impose one, so absent structural
    evidence it returns None and the caller falls back to the honest
    placeholder rather than guessing.
    """
    config_blob = _strip_hash_comments("".join(
        _read(root, f) for f in
        ("pyproject.toml", "setup.cfg", "tox.ini", ".pre-commit-config.yaml")
        if f in fileset))
    # Requirements are matched only at the repo root: `re.match` anchors to
    # the start of the (already root-relative) path, so a nested
    # requirements*.txt (e.g. a vendored subproject's) never matches here.
    # A miss just declines to the placeholder rather than proposing a
    # formatter for a dependency set that isn't this repo's own.
    req_blob = _strip_hash_comments("".join(
        _read(root, f) for f in files
        if re.match(r"^requirements[A-Za-z0-9_.-]*\.txt$", f)))
    blob = config_blob + "\n" + req_blob

    ruff_headers = _RUFF_HEADER_RE.findall(config_blob)
    # A `[tool.ruff.lint]`-only section configures rule selection, not
    # formatting — ruff's linter and formatter are separate subcommands, so
    # lint-only config must not read as "this repo runs ruff format". A
    # bare `[tool.ruff]` (settings shared by both) or an explicit
    # `[tool.ruff.format]` section both do count.
    ruff_non_lint_header = any(
        not h.startswith("[tool.ruff.lint") for h in ruff_headers)
    ruff_toml = "ruff.toml" in fileset or ".ruff.toml" in fileset
    ruff_precommit, black_precommit = _precommit_adopts(config_blob)
    ruff_dep = (_name_matches(_REQUIREMENT_LINE_RE, blob, "ruff")
                or _name_matches(_DEP_STRING_RE, blob, "ruff"))
    if ruff_non_lint_header or ruff_toml or ruff_precommit or ruff_dep:
        return "ruff format --check ."

    black_header = bool(_BLACK_HEADER_RE.search(config_blob))
    black_dep = (_name_matches(_REQUIREMENT_LINE_RE, blob, "black")
                 or _name_matches(_DEP_STRING_RE, blob, "black"))
    if black_header or black_precommit or black_dep:
        return "black --check ."
    return None


def detect(root):
    """Build the deterministic plan dict for the repo rooted at `root`."""
    files = _walk_files(root)
    fileset = set(files)
    errors = []
    stacks = set()
    per_stack = {}  # stack -> rail -> command
    # Per-call view of STACK_PROPOSALS: shared unless a stack's proposal set
    # depends on what THIS repo adopts (currently only python/format). Never
    # mutate the module-level STACK_PROPOSALS — that would leak across calls.
    stack_proposals = STACK_PROPOSALS

    present, rails = _detect_make(root, fileset)
    if present:
        stacks.add("make")
        per_stack["make"] = rails

    present, rails = _detect_node(root, fileset, errors)
    if present:
        stacks.add("node")
        per_stack["node"] = rails

    present, rails = _detect_python(root, files, fileset)
    if present:
        stacks.add("python")
        per_stack["python"] = rails
        adopted_formatter = _python_formatter(root, files, fileset)
        if adopted_formatter:
            stack_proposals = dict(STACK_PROPOSALS)
            stack_proposals["python"] = dict(STACK_PROPOSALS["python"])
            stack_proposals["python"]["format"] = (adopted_formatter, "Makefile")

    if "go.mod" in fileset:
        stacks.add("go")
        per_stack["go"] = {
            "format": 'test -z "$(gofmt -l .)"',  # gofmt -l lists but exits 0
            "build": "go build ./...",
            "test": "go test ./...",
        }
    if "Cargo.toml" in fileset:
        stacks.add("rust")
        per_stack["rust"] = {
            "format": "cargo fmt --check",
            "build": "cargo build",
            "test": "cargo test",
        }

    # Merge existing verifiers by fixed stack priority.
    existing = {rail: None for rail in RAILS}
    for stack in STACK_PRIORITY:
        for rail, cmd in per_stack.get(stack, {}).items():
            if existing[rail] is None:
                existing[rail] = cmd

    # A workflow only counts as the CI rail if it actually RUNS one of the rail
    # commands. Crediting the first .yml in sorted order meant a stale-bot,
    # dependabot or labeler workflow read as "CI installed" — and those are most
    # common in exactly the neglected repos this skill targets, so the agent
    # would report the rail done and leave the repo with no verify job at all.
    workflows = sorted(
        f for f in files
        if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))
    )
    rail_cmds = [c for c in existing.values() if c and c != existing.get("ci")]
    ci_note = None
    for wf in workflows:
        blob = _read(root, wf)
        if not blob:
            continue
        low = blob.lower()
        runs_a_rail = any(
            cmd.split()[0] in low and cmd.split()[-1].strip("-") in low
            for cmd in rail_cmds if cmd
        ) or any(kw in low for kw in (
            "make verify", "make test", "make check", "npm test", "npm run test",
            "pytest", "go test", "cargo test", "make build", "make gate",
        ))
        if runs_a_rail:
            existing["ci"] = wf
            break
    else:
        if workflows:
            # Be explicit: workflows exist, none of them verifies anything. The
            # right move is to extend one, not to add a competing file.
            ci_note = ("workflows exist but none runs a verifier command: "
                       + ", ".join(workflows) + " — extend one rather than adding another")

    missing = sorted(rail for rail in RAILS if existing[rail] is None)

    proposals = []
    for rail in missing:
        # `placeholder` starts True and is cleared only when a real,
        # stack-specific command is found below. A proposal that stays on
        # FALLBACK_PROPOSALS (the `make format`/`make build`/`make test`
        # skeleton, or a python format rail with no adopted formatter) is
        # never watched go red on a real violation — the plan says so
        # mechanically, rather than leaving "unproven" to an agent's memory.
        placeholder = True
        if rail == "ci":
            cmd, path = CI_PROPOSAL
            placeholder = False
        else:
            cmd, path = FALLBACK_PROPOSALS[rail]
            for stack in STACK_PRIORITY:
                if stack in stacks and rail in stack_proposals.get(stack, {}):
                    cmd, path = stack_proposals[stack][rail]
                    placeholder = False
                    break
        # `exists`/`action` rather than a bare `file_to_create`: the detector
        # already knows whether the path is in the tree (it detected the make
        # stack FROM the Makefile), so the machine-readable plan should carry
        # that fact instead of leaving a prose instruction — "never overwrite a
        # file the repo already has" — as the only thing between the agent and
        # a clobbered Makefile. `file_to_create` is kept as a deprecated alias
        # so an older consumer does not break.
        exists = path in fileset
        proposals.append({
            "rail": rail,
            "command": cmd,
            "file": path,
            "exists": exists,
            "action": "extend" if exists else "create",
            "file_to_create": path,
            "placeholder": placeholder,
        })
    proposals.sort(key=lambda p: (p["rail"], p["command"], p["file"]))

    return {
        "stacks": sorted(stacks),
        "existing_verifiers": existing,
        "ci_note": ci_note,
        "missing": missing,
        "proposals": proposals,
        "lockfiles": sorted(f for f in LOCKFILE_NAMES if f in fileset),
        "errors": sorted(errors, key=lambda e: (e["file"], e["error"])),
    }


def main(argv):
    if len(argv) != 2:
        print("usage: detect_stack.py <repo-dir>", file=sys.stderr)
        return 2
    root = argv[1]
    if not os.path.isdir(root):
        print("error: not a directory: %s" % root, file=sys.stderr)
        return 2
    print(json.dumps(detect(root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
