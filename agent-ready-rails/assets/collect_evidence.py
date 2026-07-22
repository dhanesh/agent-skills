#!/usr/bin/env python3
"""collect_evidence.py — deterministic, read-only evidence collector for the
six Build rails of the agent-ready-rails audit (R1–R6 in SKILL.md).

Walks a target repository and emits JSON evidence per rail:

    {
      "R-verifiers":    {"evidence": [{"kind": ..., "path": ..., "detail": ...}], "present": bool},
      "R-ci":           {...},
      "R-house-style":  {...},
      "R-context":      {...},
      "R-scoped-tools": {...},
      "R-checkpoints":  {...}
    }

IMPORTANT SCOPE: this tool COLLECTS AND FLAGS observable evidence only. It
does not score. The auditing agent still grades each rail 0/1/2, judges
severity, and verifies the evidence by reading the cited files (collector =
evidence, agent = judgment). Absence of evidence here is a strong "absent"
signal; presence is a lead to verify, never an automatic pass.

Read-only: never writes to the target repo. Deterministic: output is fully
sorted; two runs over the same tree produce byte-identical JSON.

Usage: python3 collect_evidence.py [--rails R-ci,R-context] <repo-dir>
Exit codes: 0 ok | 2 usage/bad target.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

RAILS = ("R-verifiers", "R-ci", "R-house-style", "R-context",
         "R-scoped-tools", "R-checkpoints")

# Directories never worth walking into (vendored/derived trees).
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
             "dist", "build", "target", "vendor", ".mypy_cache", ".ruff_cache",
             ".pytest_cache", ".next", ".cache"}

# ── R-verifiers: test configs/dirs, format/lint configs, build files ─────────

TEST_CONFIGS = {"pytest.ini", "tox.ini", "noxfile.py", "conftest.py",
                "jest.config.js", "jest.config.ts", "vitest.config.ts",
                "vitest.config.js", "karma.conf.js", "phpunit.xml"}
TEST_DIR_NAMES = {"tests", "test", "spec", "__tests__"}
LINT_CONFIGS = {".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml",
                ".eslintrc.yaml", "eslint.config.js", "eslint.config.mjs",
                ".flake8", "ruff.toml", ".ruff.toml", ".pylintrc",
                "mypy.ini", ".golangci.yml", ".golangci.yaml", "clippy.toml",
                ".rubocop.yml", "biome.json", "tslint.json"}
FORMAT_CONFIGS = {".prettierrc", ".prettierrc.js", ".prettierrc.json",
                  ".prettierrc.yml", ".prettierrc.yaml", "prettier.config.js",
                  ".clang-format", "rustfmt.toml", ".rustfmt.toml",
                  ".style.yapf", ".black.toml"}
BUILD_FILES = {"pyproject.toml", "package.json", "go.mod", "Cargo.toml",
               "setup.py", "setup.cfg", "build.gradle", "build.gradle.kts",
               "pom.xml", "CMakeLists.txt", "Gemfile", "mix.exs"}
MAKEFILES = {"Makefile", "makefile", "GNUmakefile", "justfile", "Justfile",
             "Taskfile.yml", "Taskfile.yaml"}
# Make/just targets that constitute a verify loop.
VERIFY_TARGET_RE = re.compile(
    r"^(test|tests|check|lint|fmt|format|verify|gate|ci|build|typecheck)\s*:",
    re.MULTILINE)

# Commands inside CI workflows that actually run tests.
CI_TEST_RE = re.compile(
    r"\b(pytest|python3? -m pytest|python3? -m unittest|unittest|tox|nox"
    r"|npm (run )?test|yarn test|pnpm test|jest|vitest|mocha"
    r"|go test|cargo test|make (test|gate|check|verify)"
    r"|mvn (test|verify)|gradle(w)? (test|check)|rspec|bundle exec rspec"
    r"|dotnet test|mix test|bun test)\b")

# ── R-house-style ────────────────────────────────────────────────────────────

STYLE_DOC_RE = re.compile(r"^(CONTRIBUTING|STYLE|CODE_STYLE|CODESTYLE|CONVENTIONS)"
                          r"(\.(md|rst|txt))?$", re.IGNORECASE)

# ── R-context ────────────────────────────────────────────────────────────────

CONTEXT_FILES = {"CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules"}

# ── R-checkpoints ────────────────────────────────────────────────────────────

CODEOWNER_LOCATIONS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
PR_TEMPLATE_RE = re.compile(r"^pull_request_template(\.md)?$", re.IGNORECASE)
GITIGNORE_JUNK = ("__pycache__", "node_modules", "*.pyc", ".env", "dist",
                  "build", "target", "*.log", ".DS_Store")


def _read(path, limit=262144):
    """Best-effort text read; returns '' on any error (collector never crashes)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _ev(kind, path, detail=""):
    return {"kind": kind, "path": path, "detail": detail}


def collect(root):
    """Collect evidence for all six rails from the tree rooted at ``root``.

    Returns {rail: {"evidence": [...sorted...], "present": bool}}.
    """
    root = os.path.abspath(root)
    ev = {rail: [] for rail in RAILS}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        depth = 0 if not rel_dir else rel_dir.count(os.sep) + 1
        if depth > 6:
            dirnames[:] = []
            continue
        at_root = rel_dir == ""

        # Directory-name evidence.
        for d in dirnames:
            rel = os.path.join(rel_dir, d) if rel_dir else d
            if d in TEST_DIR_NAMES and depth <= 1:
                ev["R-verifiers"].append(_ev("test-dir", rel, "test directory"))
            if d == "docs" and at_root:
                ev["R-context"].append(_ev("docs-dir", rel, "documentation tree"))

        for name in sorted(filenames):
            rel = os.path.join(rel_dir, name) if rel_dir else name
            full = os.path.join(dirpath, name)

            # ── R-verifiers ──────────────────────────────────────────────
            if name in TEST_CONFIGS:
                ev["R-verifiers"].append(_ev("test-config", rel, name))
            if name in BUILD_FILES and depth <= 1:
                detail = name
                if name == "package.json":
                    try:
                        pkg = json.loads(_read(full) or "{}")
                        scripts = sorted((pkg.get("scripts") or {}).keys())
                        detail = "scripts: " + (", ".join(scripts) if scripts else "none")
                    except (ValueError, AttributeError):
                        detail = "unparseable package.json"
                elif name == "pyproject.toml":
                    text = _read(full)
                    hits = sorted(t for t in ("pytest", "ruff", "black", "mypy")
                                  if ("[tool.%s" % t) in text)
                    detail = ("tool sections: " + ", ".join(hits)) if hits else "no test/lint tool sections"
                ev["R-verifiers"].append(_ev("build-file", rel, detail))
            if name in MAKEFILES and depth <= 1:
                targets = sorted(set(VERIFY_TARGET_RE.findall(_read(full))))
                detail = ("verify targets: " + ", ".join(targets)) if targets \
                    else "no test/lint/verify targets detected"
                ev["R-verifiers"].append(_ev("make-target", rel, detail))
            if name in LINT_CONFIGS:
                ev["R-verifiers"].append(_ev("lint-config", rel, name))
                ev["R-house-style"].append(_ev("lint-config", rel, name))
            if name in FORMAT_CONFIGS:
                ev["R-verifiers"].append(_ev("format-config", rel, name))
                ev["R-house-style"].append(_ev("format-config", rel, name))

            # ── R-ci ─────────────────────────────────────────────────────
            is_workflow = (rel_dir.replace(os.sep, "/") == ".github/workflows"
                           and name.endswith((".yml", ".yaml")))
            is_other_ci = at_root and name in (".gitlab-ci.yml", "Jenkinsfile",
                                               ".travis.yml", "azure-pipelines.yml")
            is_circle = rel_dir.replace(os.sep, "/") == ".circleci" and name == "config.yml"
            if is_workflow or is_other_ci or is_circle:
                text = _read(full)
                if CI_TEST_RE.search(text):
                    ev["R-ci"].append(_ev("workflow-runs-tests", rel,
                                          "test command detected in CI config"))
                else:
                    ev["R-ci"].append(_ev("workflow-no-tests", rel,
                                          "CI config present but no test command detected"))

            # ── R-house-style ────────────────────────────────────────────
            if name == ".editorconfig":
                ev["R-house-style"].append(_ev("editorconfig", rel, ".editorconfig"))
            if STYLE_DOC_RE.match(name) and depth <= 1:
                ev["R-house-style"].append(_ev("style-doc", rel, name))

            # ── R-context ────────────────────────────────────────────────
            if name in CONTEXT_FILES:
                ev["R-context"].append(_ev("agent-context-file", rel, name))
            if name.lower() == "readme.md":
                if at_root:
                    ev["R-context"].append(_ev("readme", rel, "root README"))
                else:
                    ev["R-context"].append(_ev("per-dir-readme", rel,
                                               "README in %s/" % rel_dir.replace(os.sep, "/")))

            # ── R-scoped-tools ───────────────────────────────────────────
            is_settings = (rel_dir.replace(os.sep, "/") == ".claude"
                           and name.startswith("settings") and name.endswith(".json"))
            if is_settings:
                text = _read(full)
                try:
                    cfg = json.loads(text or "{}")
                    perms = cfg.get("permissions") or {}
                    allow = perms.get("allow") or []
                    deny = perms.get("deny") or []
                    detail = "permissions: %d allow, %d deny" % (len(allow), len(deny))
                    if not perms:
                        detail = "no permissions block"
                    ev["R-scoped-tools"].append(_ev("claude-settings", rel, detail))
                except ValueError as e:
                    ev["R-scoped-tools"].append(_ev("settings-error", rel,
                                                    "invalid JSON: %s" % e))
            if name == ".mcp.json" and at_root:
                text = _read(full)
                try:
                    cfg = json.loads(text or "{}")
                    servers = sorted((cfg.get("mcpServers") or {}).keys())
                    detail = "mcp servers: " + (", ".join(servers) if servers else "none")
                    ev["R-scoped-tools"].append(_ev("mcp-config", rel, detail))
                except ValueError as e:
                    ev["R-scoped-tools"].append(_ev("settings-error", rel,
                                                    "invalid JSON: %s" % e))

            # ── R-checkpoints ────────────────────────────────────────────
            if rel.replace(os.sep, "/") in CODEOWNER_LOCATIONS:
                ev["R-checkpoints"].append(_ev("codeowners", rel, "review routing"))
            if PR_TEMPLATE_RE.match(name) and rel_dir.replace(os.sep, "/") in ("", ".github"):
                ev["R-checkpoints"].append(_ev("pr-template", rel, name))
            if (rel_dir.replace(os.sep, "/") == ".github/PULL_REQUEST_TEMPLATE"
                    and name.endswith(".md")):
                ev["R-checkpoints"].append(_ev("pr-template", rel, name))
            if name == ".gitignore" and at_root:
                text = _read(full)
                covered = sorted(p for p in GITIGNORE_JUNK if p in text)
                detail = ("covers: " + ", ".join(covered)) if covered \
                    else "present but covers no common junk patterns"
                ev["R-checkpoints"].append(_ev("gitignore", rel, detail))
            if name == "settings.yml" and rel_dir.replace(os.sep, "/") == ".github":
                ev["R-checkpoints"].append(_ev("branch-protection-hint", rel,
                                               "probot settings.yml (may declare branch protection)"))

    out = {}
    for rail in RAILS:
        seen = set()
        items = []
        for item in sorted(ev[rail], key=lambda e: (e["kind"], e["path"], e["detail"])):
            key = (item["kind"], item["path"])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
        out[rail] = {"evidence": items, "present": bool(items)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic, read-only evidence collector for the six "
                    "agent-ready-rails Build rails. It collects and flags "
                    "observable evidence only — the auditing agent still "
                    "verifies each item by reading it, and still grades "
                    "score/severity itself (collector = evidence, agent = judgment).")
    ap.add_argument("target", help="path to the repository to scan (read-only)")
    ap.add_argument("--rails", default="",
                    help="comma-separated subset of rails to emit "
                         "(default: all six; e.g. --rails R-ci,R-context)")
    ap.add_argument("--indent", type=int, default=2,
                    help="JSON indent (default 2)")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.target):
        print("error: not a directory: %s" % args.target, file=sys.stderr)
        return 2

    result = collect(args.target)
    if args.rails:
        wanted = [r.strip() for r in args.rails.split(",") if r.strip()]
        unknown = sorted(set(wanted) - set(RAILS))
        if unknown:
            print("error: unknown rail(s): %s (valid: %s)"
                  % (", ".join(unknown), ", ".join(RAILS)), file=sys.stderr)
            return 2
        result = {r: result[r] for r in RAILS if r in wanted}

    json.dump(result, sys.stdout, indent=args.indent, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
