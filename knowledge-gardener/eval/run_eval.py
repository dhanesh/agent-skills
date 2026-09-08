#!/usr/bin/env python3
"""Gate-runnable outcome eval for knowledge-gardener (docs/eval-standard.md).

End-to-end: in a tempdir, builds a real git repo fixture and a
spec-conformant OKF v0.1 bundle pinning it (frontmatter written by hand,
mirroring exactly what feynman-walkthrough's okf.py emits), then runs the
shipped garden.py sweep against it and grades the outcomes:

  - a freshly pinned git subject reports FRESH (exit 0 while nothing is stale);
  - after a commit to the fixture repo, the subject reports STALE, the sweep
    names the changed file, and the exit code goes non-zero (gate-usable);
  - an external-URL source reports UNKNOWN;
  - negative fixture: a subject with corrupted frontmatter is reported as
    ERROR (no crash, non-zero exit);
  - a bundle-less directory yields NO_BUNDLES and exit 0;
  - the json subcommand emits parseable output that agrees with the sweep.

Offline, deterministic, stdlib-only, no repo writes (all scratch in a
tempdir, garden.py runs from a copy).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=eval@example.invalid", "-c", "user.name=eval"]
        + list(args),
        cwd=cwd, check=True, capture_output=True, text=True, timeout=30)


def rev_head(cwd):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def explainer(title, sources_yaml):
    return ("---\n"
            "type: Explainer\n"
            "title: %s\n"
            "description: Reference explainer for %s\n"
            "tags: [walkthrough, reference]\n"
            "timestamp: 2026-01-01T00:00:00Z\n"
            "created: 2026-01-01\n"
            "%s"
            "---\n\n# %s\n\n## In one paragraph\n\nFixture body.\n"
            % (title, title, sources_yaml, title))


def run_garden(garden_py, *argv):
    r = subprocess.run([sys.executable, garden_py] + list(argv),
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def main():
    tmp = tempfile.mkdtemp(prefix="gardener-eval-")
    try:
        garden_py = os.path.join(tmp, "garden.py")
        shutil.copy(os.path.join(SKILL, "assets", "garden.py"), garden_py)

        # ── Harness: git repo fixture + spec-conformant OKF bundle pinning it ─
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True,
                       capture_output=True)
        write(os.path.join(repo, "pipeline.py"),
              "def ingest():\n    return 'v1'\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "initial")
        head = rev_head(repo)

        root = os.path.join(tmp, "knowledge")
        write(os.path.join(root, "index.md"),
              '---\nokf_version: "0.1"\n---\n\n# Subjects\n\n'
              "* [Ingest pipeline](ingest-pipeline/) - reference explainer\n"
              "* [Design doc](design-doc/) - reference explainer\n")
        write(os.path.join(root, "log.md"),
              "# Directory Update Log\n\n## 2026-01-01\n"
              "* **Creation**: Established fixtures (eval harness).\n")
        write(os.path.join(root, "ingest-pipeline", "explainer.md"),
              explainer("Ingest pipeline",
                        "sources:\n- type: git\n  locator: %s\n"
                        "  fingerprint: %s\n  pinned: 2026-01-01\n"
                        % (repo, head)))
        write(os.path.join(root, "ingest-pipeline", "index.md"),
              "# Ingest pipeline\n\n* [Explainer](explainer.md)\n")
        write(os.path.join(root, "design-doc", "explainer.md"),
              explainer("Design doc",
                        "sources:\n- type: external\n"
                        "  locator: https://example.invalid/design-doc\n"
                        "  fingerprint: null\n  pinned: 2026-01-01\n"))
        write(os.path.join(root, "design-doc", "index.md"),
              "# Design doc\n\n* [Explainer](explainer.md)\n")

        # ── Grade 1: freshly pinned -> FRESH, external -> UNKNOWN, exit 0 ────
        code, out, err = run_garden(garden_py, "sweep", root)
        check("fresh pin: git subject reports FRESH",
              "SUBJECT: ingest-pipeline — FRESH" in out)
        check("external URL source reports UNKNOWN",
              "SUBJECT: design-doc — UNKNOWN" in out
              and "SOURCE: external https://example.invalid/design-doc — UNKNOWN"
              in out)
        check("nothing stale: sweep exits 0", code == 0,
              "exit %d" % code)

        # ── Grade 2: commit drift -> STALE, changed file named, exit != 0 ────
        write(os.path.join(repo, "pipeline.py"),
              "def ingest():\n    return 'v2'  # changed\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "change ingest")
        code, out, err = run_garden(garden_py, "sweep", root)
        check("after commit: subject reports STALE",
              "SUBJECT: ingest-pipeline — STALE" in out
              and "GARDEN_RESULT: STALE" in out)
        check("sweep names the changed file", "pipeline.py" in out)
        check("sweep shows pinned vs current SHA and a diff summary",
              "pinned %s" % head[:12] in out and "1 file changed" in out)
        check("stale sweep exits non-zero (gate-usable)", code != 0,
              "exit %d" % code)

        # ── Grade 3 (negative): corrupt frontmatter -> ERROR, not a crash ────
        write(os.path.join(root, "broken-notes", "explainer.md"),
              "---\ntype: Explainer\ntitle: Broken\n"
              "sources:\n- type: git\n# closing fence destroyed\n")
        code, out, err = run_garden(garden_py, "sweep", root)
        check("corrupt frontmatter reported as ERROR entry",
              "SUBJECT: broken-notes — ERROR" in out)
        check("corrupt subject does not crash the sweep",
              "Traceback" not in err and "GARDEN_RESULT:" in out,
              (err.strip().splitlines() or ["clean stderr"])[-1][:60])
        check("sweep with ERROR exits non-zero", code != 0, "exit %d" % code)

        # ── Grade 4: bundle-less directory -> finds nothing and says so ──────
        empty = os.path.join(tmp, "no-bundles-here")
        os.makedirs(os.path.join(empty, "src"))
        write(os.path.join(empty, "src", "README.md"), "# not a bundle\n")
        code, out, err = run_garden(garden_py, "sweep", empty)
        check("bundle-less directory: NO_BUNDLES reported, exit 0",
              "GARDEN_RESULT: NO_BUNDLES" in out and code == 0,
              "exit %d" % code)

        # ── Grade 5: json subcommand agrees with the sweep ───────────────────
        code, out, err = run_garden(garden_py, "json", root)
        try:
            data = json.loads(out)
            states = {s["subject"]: s["state"]
                      for b in data["bundles"] for s in b["subjects"]}
            ok = (data["result"] == "ERROR" and data["exit_code"] == 1
                  and states.get("ingest-pipeline") == "STALE"
                  and states.get("design-doc") == "UNKNOWN"
                  and states.get("broken-notes") == "ERROR"
                  and code == 1)
            detail = "states=%s" % sorted(states.items())
        except (ValueError, KeyError) as exc:
            ok, detail = False, "unparseable json: %s" % exc
        check("json output parseable and agrees with sweep states", ok,
              detail[:100])

        # ── Grade 6: the in-repo bundle layout (the one both skills recommend)
        # The previous fixtures put repo/ and knowledge/ side by side, so the
        # bundle was never inside the repo it pins — and the case that was
        # broken in production was invisible to the eval. okf.md calls the
        # in-repo bundle the natural layout for a codebase subject.
        inrepo = os.path.join(tmp, "inrepo")
        os.makedirs(inrepo)
        subprocess.run(["git", "init", "-q", inrepo], check=True, capture_output=True)
        write(os.path.join(inrepo, "app.py"), "print(1)\n")
        git(inrepo, "add", "-A")
        git(inrepo, "commit", "-q", "-m", "initial")
        pinned = rev_head(inrepo)

        nested = os.path.join(inrepo, "docs", "knowledge")
        write(os.path.join(nested, "index.md"),
              '---\nokf_version: "0.1"\n---\n\n# Subjects\n\n'
              "* [App](app/) - reference explainer\n")
        write(os.path.join(nested, "log.md"),
              "# Directory Update Log\n\n## 2026-01-01\n"
              "* **Creation**: Established fixture.\n")
        write(os.path.join(nested, "app", "explainer.md"),
              explainer("App",
                        "sources:\n- type: git\n  locator: %s\n"
                        "  fingerprint: %s\n  pinned: 2026-01-01\n"
                        % (inrepo, pinned)))
        git(inrepo, "add", "-A")
        git(inrepo, "commit", "-q", "-m", "add the bundle itself")

        code, out, err = run_garden(garden_py, "sweep", nested)
        check("in-repo bundle: committing the bundle keeps it FRESH (self-pin)",
              "GARDEN_RESULT: FRESH" in out and code == 0,
              "exit %d; %s" % (code, out.strip().splitlines()[-1][:60] if out.strip() else ""))

        # NEGATIVE control: a genuine source change must still be STALE, so the
        # self-pin filter cannot be satisfied by simply never reporting drift.
        write(os.path.join(inrepo, "app.py"), "print(2)\n")
        git(inrepo, "add", "-A")
        git(inrepo, "commit", "-q", "-m", "change the source")
        code, out, err = run_garden(garden_py, "sweep", nested)
        check("in-repo bundle: a real source change is still STALE",
              "GARDEN_RESULT: STALE" in out and "app.py" in out and code != 0,
              "exit %d" % code)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
