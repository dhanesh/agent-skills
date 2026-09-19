#!/usr/bin/env python3
"""Gate-runnable outcome eval for verifier-installer (docs/eval-standard.md).

Builds synthetic fixture repos in a tempdir — a python repo with pytest
config, a node repo with real scripts plus an existing CI workflow, a bare
repo with nothing, and a repo with a malformed package.json — then runs the
shipped detector against each and grades the emitted plans deterministically.

Negative fixtures: the bare repo must report every rail missing (and must NOT
claim existing verifiers); the repo with existing CI must NOT propose creating
a duplicate workflow; the malformed package.json must yield a graceful error
entry, not a crash. The write gate's autonomy-grant arm is graded against the
vendored checker: no grant must ask, a skill-attributed grant must be INVALID,
and step 2's gate text must stay a MUST NOT that only a covering grant lifts.
Offline, stdlib-only, no repo writes.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

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


# ── Autonomy grant (skill-contract autonomy-grant/v1) ───────────────────────
# Fixtures build a grant at run time, so they MUST satisfy the checker's floors:
# at least 2 distinct subjects, a lifetime of at most 7 days (A7), no
# require_signature, and no gate other than `ask` on an irreversible class (A8).
# The temp dirs sit outside any git repo, so the default-branch floor skips.
GRANT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"
GRANT_ID = "autonomy-grant-v1-20260919T120000Z-a1b2c3"
CONTRACT_CHECKER = os.path.join(SKILL, "assets", "contract_check.py")


def _now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_one_day():
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grant_fixture(root, asserted_by, gate_policy=None):
    """Write a spec, a plan and a grant pinning both (sha256) under `root`."""
    subjects = []
    for rel, content in (("docs/spec.md", "# Spec\n"),
                         ("docs/plan.json", '{"tasks": []}\n')):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        subjects.append({"name": rel,
                         "digest": {"sha256": hashlib.sha256(content.encode()).hexdigest()}})
    st = {"_type": "https://in-toto.io/Statement/v1",
          "subject": subjects,
          "predicateType": GRANT_KIND,
          "predicate": {"skillContract": "1", "id": GRANT_ID,
                        "wasAttributedTo": {"skill": "spec-first-planning", "version": "2.0.0"},
                        "generatedAtTime": _now_z(), "wasRevisionOf": None,
                        "payload": {"scope": {"repo": ".", "branch_pattern": "*"},
                                    "decisions": [{"id": "D1", "question": "q", "answer": "a",
                                                   "source": "s"}],
                                    "defaults": [],
                                    "gate_policy": gate_policy or {"local_reversible": "grant"},
                                    "budget": {}, "stop_on": [],
                                    "expires_at": _in_one_day(),
                                    "system_one": {"allowed": False}, "revoked": False},
                        "assertions": [{"test": "grant-accepted", "assertedBy": asserted_by,
                                        "result": {"outcome": "passed"},
                                        "command": ["{python}",
                                                    "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                                                    "--unattended", "docs/spec.md"]}]}}
    d = os.path.join(root, ".skill-contract", "envelopes")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, GRANT_ID + ".json"), "w", encoding="utf-8") as f:
        json.dump(st, f)


def _git_repo(root):
    """Make root its own git repo on a non-default branch (main holds one empty commit,
    HEAD is on factory/x), so check-grant's branch probes find root's .git rather than
    any repo enclosing TMPDIR. Without git, root stays a plain directory, which
    check-grant treats as outside git."""
    if shutil.which("git") is None:
        return
    for args in (["init", "-q", "-b", "main"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                  "commit", "-q", "--allow-empty", "-m", "x"],
                 ["checkout", "-q", "-b", "factory/x"]):
        subprocess.run(["git", "-C", root] + args, check=True, capture_output=True, timeout=60)


def _check_grant(root, action="local_reversible"):
    r = subprocess.run([sys.executable, "-I", CONTRACT_CHECKER, "check-grant", "--root", root,
                        "--action", action], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.strip()


def grant_checks(labels=("", "", "")):
    """The write gate's grant arm: NONE asks, a human grant covers, a skill one is INVALID."""
    with tempfile.TemporaryDirectory() as t:
        _git_repo(t)
        rc, out = _check_grant(t)
        check(labels[0] + "NEGATIVE: no grant -> check-grant exits 3 and the gate must ask",
              rc == 3 and "GRANT: NONE" in out, f"rc={rc} {out[-160:]}")
    with tempfile.TemporaryDirectory() as t:
        _git_repo(t)
        _grant_fixture(t, {"human": "Dana"})
        rc, out = _check_grant(t)
        check(labels[1] + "a human-accepted grant covers local_reversible and names its id",
              rc == 0 and "GRANT: COVERED" in out and GRANT_ID in out,
              f"rc={rc} {out[-160:]}")
    with tempfile.TemporaryDirectory() as t:
        _git_repo(t)
        _grant_fixture(t, {"skill": "spec-first-planning"})
        rc, out = _check_grant(t)
        check(labels[2] + "NEGATIVE: a skill-attributed grant is INVALID (exit 2)",
              rc == 2 and "GRANT: INVALID" in out, f"rc={rc} {out[-160:]}")


# Step 2's write gate: still a MUST NOT, lifted only by check-grant exit 0, and
# the report must name the grant. Graded on the step's own text, not the file.
GATE_MARKERS = ("MUST NOT write anything before this confirmation",
                "check-grant --root <repo> --action local_reversible",
                "exits 0", "MAY proceed", "MUST name the grant id and action class")


# Under a grant the install is scoped: the plan's proposals only, GitHub
# Actions, manifest errors reported not fixed, and nothing else lifted.
SCOPE_MARKERS = ("Under a grant you MUST install only the plan's proposals for the missing rails",
                 "GitHub Actions as the CI provider",
                 "MUST report manifest errors", "instead of fixing them",
                 "The grant lifts this confirmation and nothing more",
                 "stop and ask before fixing pre-existing debt still applies")
GATE_LINE = "Gate: confirmed by user | grant <id> (<class>)"


def grade_scope(text):
    start = text.find("2. **Confirm the plan with the user.**")
    end = text.find("3. **Install per the playbook.**", start)
    step = " ".join(text[start:end].split()) if start >= 0 and end > start else ""
    return [m for m in SCOPE_MARKERS if m not in step]


def grade_summary_gate_line(text):
    start = text.find("## Verifier loop installed: <repo>")
    end = text.find("```", start)
    return start >= 0 and GATE_LINE in text[start:end]


def grade_gate(text):
    start = text.find("2. **Confirm the plan with the user.**")
    end = text.find("3. **Install per the playbook.**", start)
    step = " ".join(text[start:end].split()) if start >= 0 and end > start else ""
    missing = [m for m in GATE_MARKERS if m not in step]
    return not missing, missing


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

        # NEGATIVE: the pyrepo fixture has no pyproject.toml/setup.cfg/tox.ini
        # dependency on ruff or black, so it must NOT be credited a proven
        # format rail. Bug 3: the format rail used to be byte-identical to
        # the build rail (both `python3 -m compileall -q .`) — a syntax
        # check that can never go red on a formatting violation. An
        # unformatted-but-syntactically-valid repo must get the honest
        # `make format` placeholder, not a false "format rail installed".
        fmt_props = [p for p in plan.get("proposals", []) if p.get("rail") == "format"]
        build_props = [p for p in plan.get("proposals", []) if p.get("rail") == "build"]
        fmt_cmd = fmt_props[0].get("command") if fmt_props else None
        build_cmd = build_props[0].get("command") if build_props else None
        check("NEGATIVE python fixture: format rail is not a syntax check "
              "(format rail != build rail; no formatter adopted)",
              len(fmt_props) == 1 and fmt_cmd == "make format"
              and fmt_cmd != build_cmd and "compileall" not in fmt_cmd,
              str(fmt_cmd))
        check("python fixture: build rail forces a fresh compileall pass (-f)",
              build_cmd is not None and "-f" in build_cmd.split(),
              str(build_cmd))

        # ── Fixture 1b: python repo that HAS adopted ruff ────────────────────
        pyruff = os.path.join(tmp, "pyruff")
        write(pyruff, "pyproject.toml",
              '[project]\nname = "demo"\n[tool.ruff]\nline-length = 100\n')
        r = run_detector(pyruff)
        ruff_plan = json.loads(r.stdout) if r.returncode == 0 else {}
        ruff_fmt = [p for p in ruff_plan.get("proposals", []) if p.get("rail") == "format"]
        check("python+ruff fixture: format rail proposes a real ruff check",
              len(ruff_fmt) == 1 and ruff_fmt[0].get("command") == "ruff format --check .",
              str(ruff_fmt))
        check("M3: adopted-formatter proposal is not marked a placeholder",
              bool(ruff_fmt) and ruff_fmt[0].get("placeholder") is False,
              str(ruff_fmt[0].get("placeholder")) if ruff_fmt else "no proposal")

        # NEGATIVE (I1): bare-word matching used to false-positive on a
        # comment, a description string, an unrelated package name, and a
        # lint-only ruff section. None of these is a real formatter
        # adoption, so all must still get the placeholder.
        falsepos = os.path.join(tmp, "falsepositives")
        write(falsepos, "pyproject.toml",
              '[project]\nname = "demo"\ndescription = "Paint it black"\n'
              '# black compat\n[tool.ruff.lint]\nselect = ["E"]\n')
        write(falsepos, "requirements.txt", "black-magic==1.0\nruff-lint-only==2.0\n")
        r = run_detector(falsepos)
        fp_plan = json.loads(r.stdout) if r.returncode == 0 else {}
        fp_fmt = [p for p in fp_plan.get("proposals", []) if p.get("rail") == "format"]
        check("NEGATIVE I1: comment/description/unrelated-package/lint-only-ruff "
              "are not read as an adopted formatter",
              len(fp_fmt) == 1 and fp_fmt[0].get("command") == "make format"
              and fp_fmt[0].get("placeholder") is True,
              str(fp_fmt))

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

        # ── Autonomy grant: the write gate honours check-grant, nothing else ─
        grant_checks()
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            skill_md = f.read()
        ok, missing = grade_gate(skill_md)
        check("step 2's gate is a MUST NOT lifted only by check-grant local_reversible exit 0",
              ok, f"missing: {missing}")
        check("NEGATIVE: grader flags a gate with the check-grant call stripped",
              not grade_gate(skill_md.replace("check-grant", "check-envelope"))[0])
        check("the read-only guardrail names a covering grant as the only other release",
              "(or a covering grant)" in skill_md)
        missing = grade_scope(skill_md)
        check("step 2 scopes a grant: proposals only, GitHub Actions, manifest errors "
              "reported, nothing else lifted", not missing, f"missing: {missing}")
        check("NEGATIVE: grader flags a step 2 with the grant-scope sentence stripped",
              bool(grade_scope(skill_md.replace("nothing more", "more"))))
        check("the install summary template carries the Gate line",
              grade_summary_gate_line(skill_md))
        check("NEGATIVE: grader flags a summary template with the Gate line stripped",
              not grade_summary_gate_line(skill_md.replace(GATE_LINE, "")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
