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
        "format": ("python3 -m compileall -q .", "Makefile"),
        "build": ("python3 -m compileall -q .", "Makefile"),
        "test": ("python3 -m unittest discover -s tests", "tests/test_smoke.py"),
    },
    "node": {
        "format": ("npm run format", "package.json"),
        "build": ("npm run build", "package.json"),
        "test": ("npm test", "package.json"),
    },
    "go": {
        "format": ("gofmt -l .", "Makefile"),
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


def detect(root):
    """Build the deterministic plan dict for the repo rooted at `root`."""
    files = _walk_files(root)
    fileset = set(files)
    errors = []
    stacks = set()
    per_stack = {}  # stack -> rail -> command

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

    if "go.mod" in fileset:
        stacks.add("go")
        per_stack["go"] = {
            "format": "gofmt -l .",
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

    workflows = [
        f for f in files
        if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))
    ]
    if workflows:
        existing["ci"] = workflows[0]

    missing = sorted(rail for rail in RAILS if existing[rail] is None)

    proposals = []
    for rail in missing:
        if rail == "ci":
            cmd, path = CI_PROPOSAL
        else:
            cmd, path = FALLBACK_PROPOSALS[rail]
            for stack in STACK_PRIORITY:
                if stack in stacks and rail in STACK_PROPOSALS.get(stack, {}):
                    cmd, path = STACK_PROPOSALS[stack][rail]
                    break
        proposals.append({"rail": rail, "command": cmd, "file_to_create": path})
    proposals.sort(key=lambda p: (p["rail"], p["command"], p["file_to_create"]))

    return {
        "stacks": sorted(stacks),
        "existing_verifiers": existing,
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
