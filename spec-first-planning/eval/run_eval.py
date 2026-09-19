#!/usr/bin/env python3
"""Gate-runnable outcome eval for spec-first-planning (docs/eval-standard.md).

End-to-end over the skill's deliverable contract: copies the skill into a
tempdir, writes fixture specs there (known-good AND known-bad), runs the
shipped linter and task compiler against them via subprocess, and grades
outcomes deterministically — the lint accepts a complete spec and rejects
each seeded defect; the compiler yields a total requirement↔task coverage
map with a verify step on every task, and reports uncovered requirements
with a non-zero exit; the shipped spec template, filled with fixture
content, produces a lint-clean spec. Stdlib-only, offline, no repo writes.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
