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
