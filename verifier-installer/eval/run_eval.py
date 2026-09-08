#!/usr/bin/env python3
"""Gate-runnable outcome eval for verifier-installer (docs/eval-standard.md).

Builds synthetic fixture repos in a tempdir — a python repo with pytest
config, a node repo with real scripts plus an existing CI workflow, a bare
repo with nothing, and a repo with a malformed package.json — then runs the
shipped detector against each and grades the emitted plans deterministically.

Negative fixtures: the bare repo must report every rail missing (and must NOT
claim existing verifiers); the repo with existing CI must NOT propose creating
a duplicate workflow; the malformed package.json must yield a graceful error
entry, not a crash. Offline, stdlib-only, no repo writes.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
DETECTOR = os.path.join(SKILL, "assets", "detect_stack.py")

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


def run_detector(repo):
    r = subprocess.run([sys.executable, DETECTOR, repo],
                       capture_output=True, text=True, timeout=30)
    return r


def main():
    tmp = tempfile.mkdtemp(prefix="vinst-eval-")
    try:
        # ── Fixture 1: python repo with pytest config, no CI ────────────────
        py = os.path.join(tmp, "pyrepo")
        write(py, "pyproject.toml",
              '[project]\nname = "demo"\n[tool.pytest.ini_options]\n')
        write(py, "tests/test_demo.py", "def test_ok():\n    assert True\n")

        r = run_detector(py)
        plan = json.loads(r.stdout) if r.returncode == 0 else {}
        check("python fixture: detector exits 0 with valid JSON",
              r.returncode == 0 and bool(plan),
              (r.stderr or "").strip()[-80:] if r.returncode else "")
        check("python fixture: names the python stack",
              plan.get("stacks") == ["python"], str(plan.get("stacks")))
        check("python fixture: pytest recognized as the test verifier",
              plan.get("existing_verifiers", {}).get("test") == "python3 -m pytest")
        ci_props = [p for p in plan.get("proposals", [])
                    if p.get("rail") == "ci"]
        check("python fixture: proposes creating the verify workflow",
              len(ci_props) == 1 and
              ci_props[0].get("file_to_create") == ".github/workflows/verify.yml")

        # ── Fixture 2: node repo with real scripts + existing workflow ──────
        nd = os.path.join(tmp, "nodrepo")
        write(nd, "package.json", json.dumps({
            "name": "demo",
            "scripts": {"test": "node --test", "build": "node --check index.js"},
        }))
        write(nd, "package-lock.json", "{}")
        write(nd, ".github/workflows/ci.yml",
              "on: push\njobs:\n  t:\n    steps:\n      - run: npm test\n")

        r = run_detector(nd)
        plan = json.loads(r.stdout) if r.returncode == 0 else {}
        check("node fixture: names the node stack",
              plan.get("stacks") == ["node"], str(plan.get("stacks")))
        ev = plan.get("existing_verifiers", {})
        check("node fixture: package.json scripts credited as verifiers",
              ev.get("test") == "npm test" and ev.get("build") == "npm run build")
        check("node fixture: existing workflow credited as the ci rail",
              ev.get("ci") == ".github/workflows/ci.yml", str(ev.get("ci")))
        dup = [p for p in plan.get("proposals", [])
               if p.get("rail") == "ci"
               or ".github/workflows" in p.get("file_to_create", "")]
        check("NEGATIVE node fixture: no duplicate workflow proposed",
              dup == [], str(dup))

        # NEGATIVE: a workflow that verifies nothing must NOT be credited. A
        # stale-bot / dependabot / labeler workflow is common in exactly the
        # neglected repos this skill targets, and crediting the first .yml in
        # sorted order made the agent report CI done and leave the repo with no
        # verify job at all.
        sd = os.path.join(tmp, "stalebot")
        write(sd, "requirements.txt", "requests\n")
        write(sd, ".github/workflows/stale.yml",
              "on:\n  schedule:\n    - cron: '0 0 * * *'\n"
              "jobs:\n  stale:\n    steps:\n      - uses: actions/stale@v9\n")
        r = run_detector(sd)
        splan = json.loads(r.stdout) if r.returncode == 0 else {}
        sev = splan.get("existing_verifiers", {})
        check("NEGATIVE: a workflow running no verifier is not the ci rail",
              sev.get("ci") is None and "ci" in splan.get("missing", []),
              str(sev.get("ci")))
        check("NEGATIVE: the plan names the non-verifying workflow to extend",
              "stale.yml" in (splan.get("ci_note") or ""),
              (splan.get("ci_note") or "")[:60])

        # The plan must carry create-vs-extend, not leave prose as the only
        # guard against clobbering a file the repo already has.
        ed = os.path.join(tmp, "existing-makefile")
        write(ed, "Makefile", "help:\n\t@echo hi\n")
        write(ed, "requirements.txt", "requests\n")
        r = run_detector(ed)
        eplan = json.loads(r.stdout) if r.returncode == 0 else {}
        props = eplan.get("proposals", [])
        mk = [p for p in props if p.get("file") == "Makefile"]
        check("proposals mark an existing target file as `extend`, not `create`",
              bool(mk) and all(p.get("action") == "extend" and p.get("exists") is True
                               for p in mk),
              str([(p.get("file"), p.get("action")) for p in props]))

        # ── Fixture 3 (negative): bare repo with nothing ────────────────────
        bare = os.path.join(tmp, "barerepo")
        os.makedirs(bare)

        r = run_detector(bare)
        plan = json.loads(r.stdout) if r.returncode == 0 else {}
        check("NEGATIVE bare fixture: all four rails reported missing",
              plan.get("missing") == ["build", "ci", "format", "test"],
              str(plan.get("missing")))
        ev = plan.get("existing_verifiers", {"x": "sentinel"})
        check("NEGATIVE bare fixture: claims no existing verifiers",
              all(v is None for v in ev.values()), str(ev))
        rails = sorted(p.get("rail") for p in plan.get("proposals", []))
        check("bare fixture: proposals cover every missing rail",
              rails == ["build", "ci", "format", "test"], str(rails))

        # ── Fixture 4 (negative): malformed package.json ────────────────────
        bad = os.path.join(tmp, "badrepo")
        write(bad, "package.json", '{"scripts": {broken')

        r = run_detector(bad)
        graceful = r.returncode == 0
        plan = json.loads(r.stdout) if graceful else {}
        errs = plan.get("errors", [])
        check("NEGATIVE malformed fixture: reported gracefully, no crash",
              graceful and len(errs) == 1 and errs[0].get("file") == "package.json",
              str(errs) if graceful else f"exit {r.returncode}")
        check("NEGATIVE malformed fixture: broken scripts credited nowhere",
              plan.get("existing_verifiers", {}).get("test") is None)

        # ── Determinism: two runs over the same tree are byte-identical ─────
        a, b = run_detector(nd), run_detector(nd)
        check("determinism: repeated runs emit byte-identical plans",
              a.stdout == b.stdout and a.returncode == b.returncode == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
