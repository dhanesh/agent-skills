#!/usr/bin/env python3
"""Gate-runnable outcome eval for repo2skill (docs/eval-standard.md).

End-to-end check of the skill's promise: a skeleton produced by
assets/scaffold_skill.py passes this repo's real quality gates on the first
run. The harness scaffolds `demo-widget-audit` into a tempdir, then runs the
actual gate scripts (validate-skill.sh, prompting-playbook.sh,
frontmatter-standard.sh) against it via subprocess — read-only use of the
gates, all writes confined to the tempdir. Negative fixtures: an invalid
skill name (`Bad_Name`) is refused with nothing written, and a scaffold
with its README.md deleted fails validate-skill.sh.

Offline, deterministic, stdlib-only, no repo writes.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPO = os.path.dirname(SKILL)
GATES = os.path.join(REPO, "scripts", "gates")
SCAFFOLD = os.path.join(SKILL, "assets", "scaffold_skill.py")

NAME = "demo-widget-audit"

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


def run(cmd, cwd=REPO):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)


def run_gate(script, skill_dir):
    return run(["sh", os.path.join(GATES, script), skill_dir])


def main():
    if not os.path.isdir(GATES):
        check("repo gate scripts are present", False, f"missing {GATES}")
        print("EVAL_RESULT: FAIL (0/1 checks)")
        return 1

    tmp = tempfile.mkdtemp(prefix="repo2skill-eval-")
    try:
        # ── Harness: scaffold a fresh skill into the tempdir ────────────────
        r = run([sys.executable, SCAFFOLD, NAME, "--dir", tmp])
        dest = os.path.join(tmp, NAME)
        check("scaffolder generates a skeleton", r.returncode == 0
              and os.path.isfile(os.path.join(dest, "SKILL.md")),
              (r.stderr or r.stdout).strip()[-100:] if r.returncode else "")

        # ── Grade with the repo's REAL gates (read-only subprocess use) ─────
        r = run_gate("validate-skill.sh", dest)
        check("fresh scaffold passes validate-skill.sh",
              r.returncode == 0 and "VALIDATION_RESULT: PASS" in r.stdout,
              r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "no output")

        r = run_gate("prompting-playbook.sh", dest)
        check("fresh scaffold passes prompting-playbook.sh",
              r.returncode == 0 and "PLAYBOOK_RESULT: PASS" in r.stdout,
              r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "no output")

        r = run_gate("frontmatter-standard.sh", dest)
        check("fresh scaffold passes frontmatter-standard.sh",
              r.returncode == 0 and "FRONTMATTER_RESULT: PASS" in r.stdout,
              r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "no output")

        # ── Generated tooling stubs must themselves run green ───────────────
        snake = NAME.replace("-", "_")
        r = run([sys.executable, f"test_{snake}_smoke.py"],
                cwd=os.path.join(dest, "assets"))
        check("generated smoke suite runs green standalone", r.returncode == 0,
              "" if r.returncode == 0 else r.stderr.strip()[-100:])

        r = run([sys.executable, "run_eval.py"], cwd=os.path.join(dest, "eval"))
        check("generated eval stub speaks the eval protocol and passes",
              r.returncode == 0 and "EVAL_RESULT: PASS" in r.stdout,
              r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "no output")

        # ── Negative fixture 1: invalid name is refused, nothing written ────
        bad_dir = os.path.join(tmp, "neg-badname")
        os.makedirs(bad_dir)
        r = run([sys.executable, SCAFFOLD, "Bad_Name", "--dir", bad_dir])
        check("scaffolder refuses invalid name Bad_Name",
              r.returncode != 0 and os.listdir(bad_dir) == [],
              f"rc={r.returncode}")

        # ── Negative fixture 2: README-less scaffold fails validate ─────────
        os.remove(os.path.join(dest, "README.md"))
        r = run_gate("validate-skill.sh", dest)
        check("validate-skill.sh rejects scaffold with README.md deleted",
              r.returncode != 0 and "VALIDATION_RESULT: FAIL" in r.stdout,
              f"rc={r.returncode}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
