#!/usr/bin/env python3
"""Gate-runnable outcome eval for agent-ready-rails (docs/eval-standard.md).

Harness: builds two synthetic fixture repos in a tempdir — a "railed" repo
carrying evidence for all six Build rails, and a "bare" code-only repo — plus
two nuance fixtures (a CI workflow that never runs tests; a malformed
.claude/settings.json). Skill tooling: runs assets/collect_evidence.py over
each. Grader: model-free checks that the collector's evidence matches the
scenario, including the mandatory negative fixtures. As a third, read-only
fixture it points the collector at this repo itself and asserts it finds the
real CI workflow, CLAUDE.md, and CODEOWNERS. Offline, deterministic, no repo
writes.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPO = os.path.dirname(SKILL)
COLLECTOR = os.path.join(SKILL, "assets", "collect_evidence.py")
RAILS = ("R-verifiers", "R-ci", "R-house-style", "R-context",
         "R-scoped-tools", "R-checkpoints")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


def write(root, rel, content=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def run_collector(target):
    proc = subprocess.run([sys.executable, COLLECTOR, target],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return None, proc.stderr.strip()[-120:]
    try:
        return json.loads(proc.stdout), ""
    except ValueError as e:
        return None, f"bad JSON: {e}"


def kinds(result, rail):
    return {e["kind"] for e in result[rail]["evidence"]}


def build_railed(root):
    write(root, "pytest.ini", "[pytest]\naddopts = -q\n")
    write(root, "tests/test_app.py", "def test_ok():\n    assert True\n")
    write(root, "Makefile", "test:\n\tpytest -q\n\nlint:\n\truff check .\n")
    write(root, "ruff.toml", "line-length = 100\n")
    write(root, ".editorconfig", "root = true\n")
    write(root, ".github/workflows/ci.yml",
          "name: ci\non: [push]\njobs:\n  t:\n    steps:\n      - run: make test\n")
    write(root, "CLAUDE.md", "# working in this repo\n")
    write(root, "README.md", "# app\n")
    write(root, "docs/architecture.md", "# map\n")
    write(root, ".claude/settings.json",
          json.dumps({"permissions": {"allow": ["Bash(make test)"], "deny": ["Bash(rm -rf *)"]}}))
    write(root, ".github/CODEOWNERS", "* @owner\n")
    write(root, ".github/PULL_REQUEST_TEMPLATE.md", "## Summary\n")
    write(root, ".gitignore", "__pycache__\n*.pyc\ndist\n")


def build_bare(root):
    write(root, "main.py", "print('hello')\n")
    write(root, "pkg/util.py", "def f():\n    return 1\n")
    write(root, "notes.txt", "scratch\n")


EXPECTED_RAILED_KINDS = {
    "R-verifiers": {"test-config", "test-dir", "make-target", "lint-config"},
    "R-ci": {"workflow-runs-tests"},
    "R-house-style": {"lint-config", "editorconfig"},
    "R-context": {"agent-context-file", "readme", "docs-dir"},
    "R-scoped-tools": {"claude-settings"},
    "R-checkpoints": {"codeowners", "pr-template", "gitignore"},
}


def main():
    tmp = tempfile.mkdtemp(prefix="rails-eval-")
    try:
        # ── Fixture 1: railed repo — every rail present, expected kinds ──
        railed = os.path.join(tmp, "railed")
        build_railed(railed)
        result, err = run_collector(railed)
        check("collector runs on railed fixture", result is not None, err)
        if result:
            for rail in RAILS:
                expected = EXPECTED_RAILED_KINDS[rail]
                got = kinds(result, rail)
                check(f"railed: {rail} present with expected evidence kinds",
                      result[rail]["present"] and expected <= got,
                      f"kinds={sorted(got)}")
            r2, _ = run_collector(railed)
            check("collector output is deterministic across runs",
                  r2 == result)

        # ── Fixture 2 (negative): bare repo — every rail absent ─────────
        bare = os.path.join(tmp, "bare")
        build_bare(bare)
        result, err = run_collector(bare)
        check("collector runs on bare fixture", result is not None, err)
        if result:
            for rail in RAILS:
                check(f"bare: {rail} absent (negative)",
                      not result[rail]["present"] and result[rail]["evidence"] == [],
                      f"got {len(result[rail]['evidence'])} item(s)")

        # ── Fixture 3 (nuance): workflow that never runs tests ──────────
        noci = os.path.join(tmp, "noci")
        write(noci, ".github/workflows/release.yml",
              "name: release\njobs:\n  r:\n    steps:\n      - run: ./publish.sh\n")
        result, err = run_collector(noci)
        check("collector runs on no-test-workflow fixture", result is not None, err)
        if result:
            check("no-test workflow: R-ci evidence is workflow-no-tests (nuance)",
                  result["R-ci"]["present"]
                  and kinds(result, "R-ci") == {"workflow-no-tests"},
                  f"kinds={sorted(kinds(result, 'R-ci'))}")

        # ── Fixture 4 (negative): malformed settings JSON — no crash ────
        badjson = os.path.join(tmp, "badjson")
        write(badjson, ".claude/settings.json", "{ definitely not json")
        result, err = run_collector(badjson)
        check("malformed settings.json: collector exits 0 (graceful)",
              result is not None, err)
        if result:
            entries = result["R-scoped-tools"]["evidence"]
            check("malformed settings.json: graceful settings-error entry",
                  [e["kind"] for e in entries] == ["settings-error"]
                  and "invalid JSON" in entries[0]["detail"],
                  entries[0]["detail"][:60] if entries else "no entry")

        # ── Fixture 5: this repo itself, read-only ──────────────────────
        result, err = run_collector(REPO)
        check("collector runs read-only over agent-skills repo", result is not None, err)
        if result:
            ci_paths = {e["path"] for e in result["R-ci"]["evidence"]}
            check("agent-skills: finds CI workflow running tests",
                  any("skill-gates" in p for p in ci_paths)
                  and "workflow-runs-tests" in kinds(result, "R-ci"),
                  f"paths={sorted(ci_paths)}")
            ctx_paths = {e["path"] for e in result["R-context"]["evidence"]}
            check("agent-skills: finds CLAUDE.md", "CLAUDE.md" in ctx_paths)
            cp_paths = {e["path"] for e in result["R-checkpoints"]["evidence"]}
            check("agent-skills: finds CODEOWNERS",
                  any(p.endswith("CODEOWNERS") for p in cp_paths),
                  f"paths={sorted(cp_paths)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
