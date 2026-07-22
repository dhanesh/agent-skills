#!/usr/bin/env python3
"""Gate-runnable outcome eval for feynman-walkthrough (docs/eval-standard.md).

End-to-end over the skill's shipped persistence tooling: builds a synthetic
git repo in a tempdir, persists an OKF bundle INSIDE that repo with okf.py
(the recommended docs/knowledge placement), and grades the skill's
revisit-across-sessions promise deterministically:

  - init pins the repo and reports FRESH;
  - committing only the bundle itself stays FRESH (the self-pin fix — the
    act of persisting knowledge must not mark it stale);
  - a real source change flips to STALE and `diff` names the changed file;
  - `pin` re-fingerprints back to FRESH;
  - external sources report UNKNOWN;
  - negative fixture: a corrupted explainer frontmatter makes `status` fail
    gracefully (non-zero, clean message, no traceback);
  - spaced_schedule.py emits the documented expanding-interval dates for a
    fixed --start.

Offline, stdlib-only, deterministic, all writes under tempfile.mkdtemp().
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
OKF = os.path.join(SKILL, "assets", "okf.py")
SCHEDULE = os.path.join(SKILL, "assets", "spaced_schedule.py")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def run(argv, cwd):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          timeout=60, check=False)


def git(repo, *args):
    return run(["git", "-C", repo] + list(args), cwd=repo)


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    if shutil.which("git") is None:
        check("git available for the harness", False, "git not on PATH")
        print("EVAL_RESULT: FAIL (0/1 checks)")
        return 1

    tmp = tempfile.mkdtemp(prefix="feynman-eval-")
    try:
        # ── Harness: a synthetic repo the walkthrough "explained" ────────────
        repo = os.path.join(tmp, "ingest")
        write(os.path.join(repo, "src", "main.py"), "def ingest():\n    return 1\n")
        write(os.path.join(repo, "README.md"), "# Ingest pipeline\n")
        for args in (("init", "-q"), ("config", "user.email", "eval@example.test"),
                     ("config", "user.name", "Eval"), ("add", "."),
                     ("commit", "-q", "-m", "initial")):
            git(repo, *args)

        root = os.path.join(repo, "docs", "knowledge")
        okf = [sys.executable, OKF, "--root", root]

        # ── init a bundle pinning the repo itself ────────────────────────────
        r = run(okf + ["init", "Ingest Pipeline", "--source", repo], cwd=tmp)
        check("init persists an OKF subject pinning the repo",
              r.returncode == 0 and "OKF_CREATED:" in r.stdout
              and os.path.isfile(os.path.join(root, "ingest-pipeline",
                                              "explainer.md")),
              (r.stderr or r.stdout).strip()[-80:])

        r = run(okf + ["status", "ingest-pipeline"], cwd=tmp)
        check("status is FRESH right after init (bundle dirt is not drift)",
              r.returncode == 0 and "OKF_STATUS: ingest-pipeline FRESH" in r.stdout,
              r.stdout.strip()[-80:])

        # ── the load-bearing check: committing ONLY the bundle stays FRESH ───
        git(repo, "add", "docs/knowledge")
        git(repo, "commit", "-q", "-m", "persist walkthrough knowledge")
        r = run(okf + ["status", "ingest-pipeline"], cwd=tmp)
        check("committing only the bundle keeps status FRESH (self-pin fix)",
              r.returncode == 0 and "FRESH" in r.stdout,
              r.stdout.strip()[-80:])

        # ── a real source change is still detected ───────────────────────────
        write(os.path.join(repo, "src", "main.py"),
              "def ingest():\n    return 2\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "change ingest behavior")
        r = run(okf + ["status", "ingest-pipeline"], cwd=tmp)
        check("a source commit flips status to STALE (non-zero exit)",
              r.returncode == 1 and "OKF_STATUS: ingest-pipeline STALE" in r.stdout,
              r.stdout.strip()[-80:])

        r = run(okf + ["diff", "ingest-pipeline"], cwd=tmp)
        check("diff names the changed source file",
              r.returncode == 0 and "M\tsrc/main.py" in r.stdout
              and "PINNED: " in r.stdout and "CURRENT: " in r.stdout,
              r.stdout.strip()[-80:])
        check("diff excludes the bundle's own files from the change list",
              "docs/knowledge" not in
              r.stdout.split("CURRENT:", 1)[-1])

        # ── refresh flow: pin returns to FRESH ───────────────────────────────
        r = run(okf + ["pin", "ingest-pipeline"], cwd=tmp)
        pin_ok = r.returncode == 0 and "OKF_PINNED:" in r.stdout
        r = run(okf + ["status", "ingest-pipeline"], cwd=tmp)
        check("pin re-fingerprints back to FRESH",
              pin_ok and r.returncode == 0 and "FRESH" in r.stdout,
              r.stdout.strip()[-80:])

        # ── external sources cannot be fingerprinted: UNKNOWN ────────────────
        run(okf + ["init", "Design Doc",
                   "--source", "https://example.test/design"], cwd=tmp)
        r = run(okf + ["status", "design-doc"], cwd=tmp)
        check("external source reports UNKNOWN",
              r.returncode == 0 and "OKF_STATUS: design-doc UNKNOWN" in r.stdout,
              r.stdout.strip()[-80:])

        # ── negative fixture: corrupted explainer frontmatter ────────────────
        write(os.path.join(root, "design-doc", "explainer.md"),
              "---\ntitle: fence never closes\n\n# Broken\n")
        r = run(okf + ["status", "design-doc"], cwd=tmp)
        check("corrupt explainer frontmatter fails gracefully",
              r.returncode != 0 and "Traceback" not in (r.stderr + r.stdout)
              and "corrupt" in (r.stderr + r.stdout),
              (r.stderr or r.stdout).strip()[-80:])

        # ── recall track: documented expanding-interval dates ────────────────
        r = run([sys.executable, SCHEDULE, "--start", "2026-07-11",
                 "--reviews", "5", "--format", "tsv", "demo topic"], cwd=tmp)
        expected = ["1\t2026-07-12", "3\t2026-07-14", "7\t2026-07-18",
                    "16\t2026-07-27", "35\t2026-08-15"]
        got = [line.split("\t", 1)[1] for line in r.stdout.strip().splitlines()[1:]]
        check("spaced_schedule emits the documented 1/3/7/16/35 dates",
              r.returncode == 0 and got == expected,
              "got %s" % got)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
