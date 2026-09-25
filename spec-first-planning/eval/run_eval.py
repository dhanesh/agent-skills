#!/usr/bin/env python3
"""Gate-runnable outcome eval for spec-first-planning (docs/eval-standard.md).

End-to-end over the skill's deliverable contract: copies the skill into a
tempdir, writes fixture specs there (known-good AND known-bad), runs the
shipped linter and task compiler against them via subprocess, and grades
outcomes deterministically — the lint accepts a complete spec and rejects
each seeded defect; the compiler yields a total requirement↔task coverage
map with a verify step on every task, and reports uncovered requirements
with a non-zero exit; the shipped spec template, filled with fixture
content, produces a lint-clean spec. The autonomy-grant arm (2.0.0) runs the
lint modes, write_grant.py, check-grant and revoke-grant on NEGATIVE and
positive fixtures, validates references/unattended.md's answers.json example
against write_grant.py, and checks the SKILL.md gate text. The waves arm
(2.1.0) runs a diamond [after: ...] spec through spec_to_tasks.py --waves
(3 waves, the right critical path) and its --envelope payload (T4's
depends_on), then checks that a cycle, an unknown id, and a malformed id
in an [after: ...] hint each fail spec_lint.py with the specific message
(NEGATIVE fixtures). Stdlib-only, offline, no repo writes.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

REQUIRED_SECTIONS = (
    "Problem",
    "Users",
    "Goals",
    "Non-goals",
    "Constraints",
    "Required truths",
    "Requirements",
    "Acceptance criteria",
    "Open questions",
)

FIXTURE_TITLE = "CSV export for saved reports"
FIXTURE_BODIES = {
    "Problem": (
        "Analysts cannot get report data out of the dashboard; they re-type\n"
        "numbers into spreadsheets by hand, losing time and introducing errors."
    ),
    "Users": "- Data analysts exporting weekly reports",
    "Goals": "- Saved reports downloadable as CSV from the report page",
    "Non-goals": "- Excel (.xlsx) export\n- Scheduled email delivery",
    "Constraints": (
        "- B1 [invariant]: No exported row may differ from the on-screen table.\n"
        "- T1 [boundary]: Export of a 10000-row report finishes within 5 seconds."
    ),
    "Required truths": (
        "- RT1 [SPECIFICATION_READY]: The CSV writer reproduces every row and "
        "column exactly. (parent: OUTCOME; maps_to: B1; reqs: R1, R2; "
        "confidence: 0.8; check: python3 tests/compare_export.py "
        "fixtures/report.json export.csv)\n"
        "- RT2 [SPECIFICATION_READY]: The export path stays within the time "
        "budget at scale. (parent: RT1; maps_to: T1; reqs: R3; "
        "confidence: 0.7; check: python3 tests/bench_export.py --rows 10000 "
        "--max-seconds 5)"
    ),
    "Requirements": (
        '- R1: The report page must offer a "Download CSV" action for every '
        "saved report. [where: web/reports/]\n"
        "- R2: The exported CSV must contain the same rows and columns as the "
        "on-screen table, in the same order.\n"
        "- R3: Export of a 10000-row report must complete within 5 seconds."
    ),
    "Acceptance criteria": (
        "- R1: Open any saved report; the page shows a Download CSV control "
        "and clicking it downloads a .csv file.\n"
        "- R2: `python3 tests/compare_export.py fixtures/report.json "
        "export.csv` exits 0 (row/column parity).\n"
        "- R3: Timing the export endpoint with a 10000-row fixture reports "
        "under 5 seconds."
    ),
    "Open questions": "- (none)",
}

# ── Autonomy-grant arm fixtures (copied verbatim from assets/test_spec_lint.py,
# Tasks 3-4): LIGHT is a light-pass spec; FULL is converged and decision-closed.
LIGHT = """# Spec: Export

## Problem
Users cannot export rows.

## Users
- analysts

## Goals
- export works

## Non-goals
- PDF

## Constraints
- B1 [invariant]: No row is lost.
- T1 [boundary]: Export finishes within 10 s for 10000 rows.

## Required truths
- RT1 [SPECIFICATION_READY]: Every row reaches the file. (parent: OUTCOME; maps_to: B1; reqs: R1; confidence: 0.8; check: python3 -m pytest -k rows)
- RT2 [NOT_SATISFIED]: The writer streams. (parent: RT1; maps_to: T1; reqs: R1; confidence: 0.6; check: python3 bench.py --max 10)

## Requirements
- R1: The export must include every row.

## Acceptance criteria
- R1: run `python3 -m pytest -k rows`, expect exit 0.

## Open questions
"""

# FULL ends its criterion with a [cmd: ...] hint: --unattended needs one on every criterion.
FULL = LIGHT.replace("RT2 [NOT_SATISFIED]", "RT2 [SPECIFICATION_READY]").replace(
    "expect exit 0.\n", "expect exit 0. [cmd: {python} -m pytest -k rows]\n") + """
## Tensions
- TN1 [trade_off]: Streaming vs. atomic write. (between: B1, T1; status: resolved; strategy: Partition)

## Solution options
- OPT-A: Stream rows to a temp file, rename at end. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2)
- OPT-B: Build in memory, then write. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1)
Recommended: OPT-A — satisfies every RT at the lowest complexity.

## Iterations
- I1: constrained, tensioned, anchored; chose OPT-A.

## Decisions
- D1: May the export add a dependency? -> no (source: sweep)
"""

# R1 -> T1 (no deps); R2, R3 both [after: R1] -> T2, T3; R4 [after: R2, R3] -> T4.
# A diamond: wave 1 = [T1], wave 2 = [T2, T3], wave 3 = [T4], critical path
# T1 -> T2 -> T4 (tied with T1 -> T3 -> T4; T2 sorts first numerically).
# Deliberately minimal (just Requirements + Acceptance criteria): --waves and
# --envelope don't call spec_lint.lint, so the other required sections aren't
# needed here — see spec_to_tasks.py's derive_plan.
DIAMOND = """# Spec: pipeline

## Requirements
- R1: The base step must run first.
- R2: The second step must run after the base step. [after: R1]
- R3: The third step must run after the base step. [after: R1]
- R4: The final step must run after both prior steps. [after: R2, R3]

## Acceptance criteria
- R1: run `true`, expect exit 0.
- R2: run `true`, expect exit 0.
- R3: run `true`, expect exit 0.
- R4: run `true`, expect exit 0.
"""


def _now_z():
    """RFC 3339 UTC 'now', to the second (runtime fixture helper)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_one_day():
    """RFC 3339 UTC one day from now: inside write_grant's 7-day lifetime cap."""
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def build_spec(bodies=None, title=FIXTURE_TITLE, drop=()):
    """Assemble a fixture spec directly (independent of the template)."""
    merged = dict(FIXTURE_BODIES)
    if bodies:
        merged.update(bodies)
    parts = ["# Spec: %s" % title]
    for name in REQUIRED_SECTIONS:
        if name in drop:
            continue
        parts.append("")
        parts.append("## %s" % name)
        parts.append(merged[name])
    return "\n".join(parts) + "\n"


def fill_template(template_text, title, bodies):
    """Fill the shipped template's sections with fixture content."""
    out = []
    current = None
    for line in template_text.splitlines():
        h2 = re.match(r"^##\s+(.+?)\s*$", line)
        if h2:
            current = h2.group(1)
            out.append(line)
            if current in bodies:
                out.append(bodies[current])
            continue
        if line.startswith("# "):
            out.append("# Spec: %s" % title)
            current = None
            continue
        if current is not None and current in bodies:
            continue  # drop the template's placeholder/guidance lines
        out.append(line)
    return "\n".join(out) + "\n"


# ── Autonomy-grant arm (2.0.0): the planning-loop lint modes, write_grant.py,
# check-grant, and the SKILL.md text that wires them together. ASSETS is the
# *copied* skill's assets/ dir (set in main), so `-I` runs never write
# __pycache__ into the repo.
ASSETS = None


def lint(text, *flags):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "spec.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "spec_lint.py"), *flags, p],
                              capture_output=True, text=True, timeout=60).returncode


def write_grant(repo, policy=None, answers=None):
    subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "spec_to_tasks.py"),
                    os.path.join(repo, "docs", "spec.md"), "--envelope", repo],
                   capture_output=True, text=True, timeout=60, check=True)
    plan = [os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(repo, ".skill-contract"))
            for f in fs if f.startswith("task-plan-")][0]
    ans = os.path.join(repo, "answers.json")
    if answers is None:
        answers = {"branch_pattern": "*", "gate_policy": policy,
                   "expires_at": _in_one_day()}
    with open(ans, "w", encoding="utf-8") as f:
        json.dump(answers, f)
    return subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "write_grant.py"),
                           "--root", repo, "--spec", "docs/spec.md", "--plan", plan,
                           "--answers", ans, "--accepted-by", "Dana"],
                          capture_output=True, text=True, timeout=60)


def check_grant(repo, action="local_reversible"):
    return subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "contract_check.py"),
                           "check-grant", "--root", repo, "--action", action],
                          capture_output=True, text=True, timeout=60)


def fresh_repo():
    repo = tempfile.mkdtemp(prefix="sfp-grant-")
    os.makedirs(os.path.join(repo, "docs"))
    with open(os.path.join(repo, "docs", "spec.md"), "w", encoding="utf-8") as f:
        f.write(FULL)
    return repo


def _lint(text, *flags):
    """Like the module-level `lint()` below but returns the full CompletedProcess,
    not just the exit code — the waves-arm NEGATIVE checks need to confirm the
    specific FAIL message fired, not just that *some* issue was found (DIAMOND
    is missing sections other than Requirements/Acceptance criteria, so it would
    fail lint for unrelated reasons too)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "spec.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "spec_lint.py"), *flags, p],
                              capture_output=True, text=True, timeout=60)


def waves_arm():
    """[after: ...] / depends_on / --waves outcome checks (2.1.0): a diamond spec
    schedules into 3 waves with the right critical path and depends_on in the
    task-plan payload; a cyclic, unknown-id, or malformed-id [after: ...] hint
    each fail spec_lint.py with the specific message (NEGATIVE fixtures)."""
    tasks_py = os.path.join(ASSETS, "spec_to_tasks.py")

    with tempfile.TemporaryDirectory() as d:
        spec_path = os.path.join(d, "spec.md")
        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(DIAMOND)
        r = subprocess.run([sys.executable, "-I", tasks_py, spec_path, "--waves"],
                           capture_output=True, text=True, timeout=30)
        check("--waves schedules a diamond [after: ...] spec into 3 waves with "
              "the right critical path",
              r.returncode == 0
              and "WAVE 1: T1" in r.stdout and "WAVE 2: T2 T3" in r.stdout
              and "WAVE 3: T4" in r.stdout and "CRITICAL_PATH: T1 -> T2 -> T4" in r.stdout
              and "WAVES_RESULT: PASS (3 wave(s))" in r.stdout,
              r.stdout.strip()[-160:])

    repo = tempfile.mkdtemp(prefix="sfp-waves-")
    try:
        os.makedirs(os.path.join(repo, "docs"))
        with open(os.path.join(repo, "docs", "spec.md"), "w", encoding="utf-8") as f:
            f.write(DIAMOND)
        r = subprocess.run([sys.executable, "-I", tasks_py, os.path.join(repo, "docs", "spec.md"),
                            "--envelope", repo], capture_output=True, text=True, timeout=30)
        env_paths = [ln[len("ENVELOPE: "):] for ln in r.stdout.splitlines()
                     if ln.startswith("ENVELOPE: ")]
        depends_on = None
        if r.returncode == 0 and env_paths and os.path.isfile(env_paths[0]):
            with open(env_paths[0], encoding="utf-8") as f:
                statement = json.load(f)
            payload_tasks = statement.get("predicate", {}).get("payload", {}).get("tasks", [])
            t4 = next((t for t in payload_tasks if t.get("id") == "T4"), None)
            depends_on = t4.get("depends_on") if t4 else None
        check("the task-plan envelope's payload carries T4's depends_on (T2, T3)",
              depends_on == ["T2", "T3"], "depends_on=%s" % depends_on)
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    cyclic = DIAMOND.replace("[after: R1]", "[after: R1, R4]", 1)
    r = _lint(cyclic)
    check("NEGATIVE: a cycle among [after: ...] hints fails spec_lint.py",
          r.returncode != 0 and "after: hints have a cycle" in r.stdout,
          r.stdout.strip()[-100:])

    unknown = DIAMOND.replace(
        "- R4: The final step must run after both prior steps. [after: R2, R3]",
        "- R4: The final step must run after both prior steps. [after: R2, R9]",
    )
    r = _lint(unknown)
    check("NEGATIVE: an [after: ...] hint naming an unknown requirement fails spec_lint.py",
          r.returncode != 0 and "unknown requirement R9" in r.stdout,
          r.stdout.strip()[-100:])

    malformed = DIAMOND.replace(
        "- R4: The final step must run after both prior steps. [after: R2, R3]",
        "- R4: The final step must run after both prior steps. [after: R2, R3x]",
    )
    r = _lint(malformed)
    check("NEGATIVE: a malformed [after: ...] id fails spec_lint.py",
          r.returncode != 0 and "malformed id 'R3x'" in r.stdout,
          r.stdout.strip()[-100:])


def grant_arm():
    check("NEGATIVE: an Open question blocks --unattended",
          lint(FULL.replace("## Open questions\n", "## Open questions\n- Which delimiter?\n"), "--unattended") == 1, "")
    check("NEGATIVE: a PARTIAL truth blocks --converged",
          lint(FULL.replace("RT2 [SPECIFICATION_READY]", "RT2 [PARTIAL]"), "--converged") == 1, "")
    check("NEGATIVE: a truth with no check fails the light lint",
          lint(LIGHT.replace("; check: python3 -m pytest -k rows)", ")")) == 1, "")
    check("NEGATIVE: a constraint no truth maps to fails the light lint",
          lint(LIGHT.replace("maps_to: T1;", "maps_to: B1;")) == 1, "")
    check("NEGATIVE: the light pass (LIGHT) is not converged",
          lint(LIGHT) == 0 and lint(LIGHT, "--converged") == 1, "")
    check("FULL is unattended-ready", lint(FULL, "--unattended") == 0, "")
    no_cmd = FULL.replace(" [cmd: {python} -m pytest -k rows]", "")
    check("NEGATIVE: a criterion without a [cmd: ...] hint blocks --unattended "
          "(it still converges)",
          lint(no_cmd, "--unattended") == 1 and lint(no_cmd, "--converged") == 0, "")
    r = _lint(FULL.replace("[cmd: {python} -m", "[cmd: python3 -m"))
    check("NEGATIVE: a [cmd: ...] hint that breaks the command rule (python3) fails the lint",
          r.returncode == 1 and "(C6)" in r.stdout, r.stdout.strip()[-120:])
    r = _lint(FULL.replace("[cmd: {python} -m pytest -k rows]", '[cmd: {python} -c "oops]'))
    check("NEGATIVE: a [cmd: ...] hint that does not parse fails the lint",
          r.returncode == 1 and "does not parse" in r.stdout, r.stdout.strip()[-120:])
    repo = fresh_repo()
    try:
        r = subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "spec_to_tasks.py"),
                            os.path.join(repo, "docs", "spec.md"), "--envelope", repo],
                           capture_output=True, text=True, timeout=60)
        paths = [ln[len("ENVELOPE: "):] for ln in r.stdout.splitlines()
                 if ln.startswith("ENVELOPE: ")]
        cmd = None
        if r.returncode == 0 and paths:
            with open(paths[0], encoding="utf-8") as f:
                cmd = json.load(f)["predicate"]["payload"]["tasks"][0]["verify"][0]["command"]
        check("the [cmd: ...] hint becomes the task-plan verify step's command",
              cmd == ["{python}", "-m", "pytest", "-k", "rows"], "command=%s" % (cmd,))
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    for policy, want in (({"merge": "auto"}, 1), ({"merge": "grant"}, 1),
                         ({"local_reversible": "grant"}, 0)):
        repo = fresh_repo()
        try:
            r = write_grant(repo, policy)
            check("write_grant %s -> exit %d" % (policy, want), r.returncode == want,
                  (r.stdout + r.stderr).strip()[-120:])
            if want == 0:
                c = check_grant(repo)
                check("the written grant covers local_reversible", c.returncode == 0, c.stdout.strip())
                c = check_grant(repo, "merge")
                check("NEGATIVE: the same grant still asks for merge (exit 3)",
                      c.returncode == 3, c.stdout.strip())
                rv = subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "contract_check.py"),
                                     "revoke-grant", "--root", repo],
                                    capture_output=True, text=True, timeout=60)
                c = check_grant(repo)
                check("NEGATIVE: after the documented revoke command, check-grant asks (exit 3)",
                      rv.returncode == 0 and c.returncode == 3, (rv.stdout + c.stdout).strip())
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    base = {"branch_pattern": "factory/*", "gate_policy": {"local_reversible": "grant"}}
    for label, answers in (
            ("an 8-day expiry (the 7-day cap)",
             dict(base, expires_at=(datetime.now(timezone.utc) + timedelta(days=8))
                  .strftime("%Y-%m-%dT%H:%M:%SZ"))),
            ("an expiry that is not in the future", dict(base, expires_at=_now_z())),
            ("a require_signature key (A8: no signing)",
             dict(base, expires_at=_in_one_day(), require_signature="SIGNED"))):
        repo = fresh_repo()
        try:
            r = write_grant(repo, answers=answers)
            check("NEGATIVE: write_grant refuses %s" % label, r.returncode == 1,
                  (r.stdout + r.stderr).strip()[-120:])
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    # The answers.json example in references/unattended.md is the one agents copy:
    # it MUST carry exactly the 7 keys write_grant.py accepts and be accepted by it.
    skill_dir = os.path.dirname(ASSETS)
    ref_path = os.path.join(skill_dir, "references", "unattended.md")
    check("references/unattended.md exists", os.path.isfile(ref_path), "")
    ref = ""
    if os.path.isfile(ref_path):
        with open(ref_path, encoding="utf-8") as f:
            ref = f.read()
    blocks = re.findall(r"```json\n(.*?)```", ref, re.S)
    example = None
    try:
        example = json.loads(blocks[0]) if blocks else None
    except ValueError:
        pass
    keys = sorted(example) if isinstance(example, dict) else []
    check("unattended.md's answers.json example has exactly the 7 write_grant keys",
          keys == sorted(["branch_pattern", "gate_policy", "expires_at", "budget",
                          "stop_on", "defaults", "system_one"]), "keys=%s" % keys)
    if isinstance(example, dict):
        repo = fresh_repo()
        try:
            r = write_grant(repo, answers=dict(example, expires_at=_in_one_day()))
            check("write_grant accepts unattended.md's answers.json example",
                  r.returncode == 0, (r.stdout + r.stderr).strip()[-120:])
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    check("unattended.md explains the 7-day cap, the default-branch floor and factory/*",
          "7 days" in ref and "default branch" in ref and "factory/*" in ref
          and "pre-mortem" in ref.lower(), "")

    with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8") as f:
        skill_md = f.read()
    check("SKILL.md's handoff calls check-grant --action local_reversible",
          "check-grant" in skill_md and "--action local_reversible" in skill_md, "")
    check("SKILL.md waits for the yes before write_grant.py and gives revoke-grant",
          "write_grant.py" in skill_md and "revoke-grant" in skill_md
          and "MUST wait for the user's explicit yes" in skill_md, "")
    check("SKILL.md provides autonomy-grant/v1 in its contract block",
          re.search(r'```json skill-contract\n\{"provides": \[[^]]*autonomy-grant/v1', skill_md)
          is not None, "")
    check("SKILL.md says irreversible actions always ask and pins pushes to the current branch",
          all(c in skill_md for c in ("`merge`", "`deploy`", "`spend`", "`external_message`",
                                      "`delete`", "always ask"))
          and "MUST push only the current branch" in skill_md, "")
    check("NEGATIVE: SKILL.md offers no signing step (A8)",
          "ssh-keygen" not in skill_md and "require_signature" not in skill_md
          and "SIGNED" not in skill_md, "")


def main():
    tmp = tempfile.mkdtemp(prefix="sfp-eval-")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        dst = os.path.join(tmp, "skill")
        shutil.copytree(
            SKILL, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        lint_py = os.path.join(dst, "assets", "spec_lint.py")
        tasks_py = os.path.join(dst, "assets", "spec_to_tasks.py")
        global ASSETS
        ASSETS = os.path.join(dst, "assets")

        def run(script, content, *flags):
            path = os.path.join(tmp, "spec.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return subprocess.run(
                [sys.executable, script, path, *flags],
                capture_output=True, text=True, timeout=30, env=env,
            )

        # ── Positive arm: the complete good spec ─────────────────────────────
        good = build_spec()
        r = run(lint_py, good)
        check("lint accepts a complete spec", r.returncode == 0
              and "LINT_RESULT: PASS" in r.stdout, r.stdout.strip()[-60:])

        r = run(tasks_py, good, "--json")
        plan = None
        try:
            plan = json.loads(r.stdout)
        except ValueError:
            pass
        check("compiler exits 0 with parseable JSON on the good spec",
              r.returncode == 0 and plan is not None)
        total = (
            plan is not None
            and plan["uncovered"] == []
            and sorted(plan["coverage"]) == ["R1", "R2", "R3"]
            and all(plan["coverage"][rid] for rid in plan["coverage"])
        )
        check("coverage map is total (every requirement -> a task)", total,
              "" if plan is None else "coverage=%s" % plan["coverage"])
        check("every task carries a non-empty verify step",
              plan is not None and plan["tasks"]
              and all(t.get("verify", "").strip() for t in plan["tasks"]),
              "" if plan is None else "%d task(s)" % len(plan["tasks"]))

        r = run(tasks_py, good)
        check("markdown plan names verify steps and passes coverage",
              r.returncode == 0 and "- Verify:" in r.stdout
              and "TASKS_RESULT: PASS (3/3 requirements" in r.stdout)

        # ── Negative arm: seeded defects must be rejected ────────────────────
        r = run(lint_py, build_spec(drop=("Non-goals",)))
        check("lint rejects a spec missing Non-goals", r.returncode != 0
              and "Non-goals" in r.stdout)

        r = run(lint_py, build_spec(drop=("Constraints", "Required truths")))
        check("lint rejects an attended spec missing Constraints/Required truths "
              "(light-pass rules, always on)", r.returncode != 0
              and "Constraints" in r.stdout and "Required truths" in r.stdout)

        unmapped = build_spec(bodies={
            "Required truths": FIXTURE_BODIES["Required truths"].replace(
                "maps_to: T1;", "maps_to: B1;",
            )
        })
        r = run(lint_py, unmapped)
        check("lint rejects a constraint (T1) with no required truth mapping to it",
              r.returncode != 0 and "no required truth mapping" in r.stdout)

        vague = build_spec(bodies={
            "Requirements": FIXTURE_BODIES["Requirements"].replace(
                "- R3: Export of a 10000-row report must complete within 5 seconds.",
                "- R3: The export must be fast.",
            )
        })
        r = run(lint_py, vague)
        check("lint flags a vague requirement with no metric (R3 'fast')",
              r.returncode != 0 and "vague term 'fast'" in r.stdout)

        no_crit = build_spec(bodies={
            "Acceptance criteria": "\n".join(
                ln for ln in FIXTURE_BODIES["Acceptance criteria"].splitlines()
                if not ln.startswith("- R2:")
            )
        })
        r = run(lint_py, no_crit)
        check("lint rejects a requirement with no acceptance criterion",
              r.returncode != 0 and "R2 has no acceptance criterion" in r.stdout)

        # Doctored spec: R2 has no criterion => no derivable task for it.
        r = run(tasks_py, no_crit, "--json")
        doctored = None
        try:
            doctored = json.loads(r.stdout)
        except ValueError:
            pass
        check("compiler reports uncovered R2 and exits non-zero",
              r.returncode != 0 and doctored is not None
              and doctored["uncovered"] == ["R2"]
              and doctored["coverage"]["R2"] == [],
              "" if doctored is None else "uncovered=%s" % doctored["uncovered"])

        # ── skill-contract arm: the task-plan envelope (docs/skill-contract/SPEC.md) ──
        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, "docs"))
        spec_path = os.path.join(repo, "docs", "spec.md")
        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(good)
        r = subprocess.run([sys.executable, tasks_py, spec_path, "--envelope", repo],
                           capture_output=True, text=True, timeout=30, env=env)
        env_paths = [ln[len("ENVELOPE: "):] for ln in r.stdout.splitlines()
                     if ln.startswith("ENVELOPE: ")]
        env_path = env_paths[0] if env_paths else ""
        check("--envelope writes a task-plan envelope",
              r.returncode == 0 and os.path.isfile(env_path), r.stderr.strip()[-80:])

        cc_py = os.path.join(dst, "assets", "contract_check.py")

        def contract(*args):
            out = subprocess.run([sys.executable, cc_py, *args],
                                 capture_output=True, text=True, timeout=30, env=env)
            try:
                return out.returncode, json.loads(out.stdout.splitlines()[0])
            except (ValueError, IndexError):
                return out.returncode, None

        rc, rep = contract("check-envelope", env_path, "--root", repo, "--json")
        check("the envelope passes the vendored checker; its claims are CLAIMED, not PROVEN",
              rc == 0 and rep is not None and rep["stale"] == []
              and rep["claims"] == {"spec-lint": "CLAIMED", "coverage-total": "CLAIMED"},
              "" if rep is None else "claims=%s" % rep["claims"])

        with open(spec_path, "a", encoding="utf-8") as f:
            f.write("\n")
        rc, rep = contract("check-envelope", env_path, "--root", repo, "--json")
        check("negative: editing the spec afterwards marks the envelope STALE",
              rc == 0 and rep is not None and rep["stale"] == ["docs/spec.md"])

        sys.path.insert(0, os.path.join(dst, "assets"))
        import spec_to_tasks as stt  # noqa: E402  (the copied skill's own module)
        import contract_check as cc  # noqa: E402
        malformed = {"title": "x", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
                     "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "t",
                                "verify": "a; b"}]}
        check("negative: a payload with a joined verify string is rejected",
              bool(stt.payload_errors(malformed)))
        with open(env_path, encoding="utf-8") as f:
            statement = json.load(f)
        try:
            cc.write_envelope(repo, statement)
            overwritten = True
        except FileExistsError:
            overwritten = False
        check("negative: an existing envelope id is never overwritten", not overwritten)

        # ── Template arm: the shipped skeleton is actually usable ────────────
        tpl_path = os.path.join(dst, "references", "spec-template.md")
        with open(tpl_path, encoding="utf-8") as f:
            template = f.read()
        headings = re.findall(r"^##\s+(.+?)\s*$", template, re.MULTILINE)
        check("template ships every required section",
              [h for h in headings if h in REQUIRED_SECTIONS]
              == list(REQUIRED_SECTIONS), "headings=%s" % headings)

        filled = fill_template(template, FIXTURE_TITLE, FIXTURE_BODIES)
        r = run(lint_py, filled)
        check("template filled with fixture content lints clean",
              r.returncode == 0 and "LINT_RESULT: PASS" in r.stdout,
              r.stdout.strip()[-60:])

        # I2 (review round 1): the template's raw example Constraints and
        # Required truths bullets must themselves satisfy the light grammar
        # and traceability rules (lint_light, rules 6-9) once their <...>
        # placeholders are swapped for sample text — not just when the whole
        # section is replaced wholesale by fill_template above. A naive global
        # placeholder swap breaks unrelated rules (e.g. the Requirements
        # placeholder loses its coincidental "must"), so this checks
        # lint_light's own rules directly rather than the full lint().
        import spec_lint as sl  # noqa: E402  (already on sys.path; the copied skill's own module)
        swapped = re.sub(r"<[^>]+>", "sample text", template)
        grammar_issues = sl.lint_light(sl.parse_spec(swapped))
        check("template's Constraints/Required truths examples satisfy the "
              "light grammar rules once placeholders are swapped for sample text",
              grammar_issues == [], "issues=%s" % grammar_issues)

        grant_arm()
        waves_arm()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
