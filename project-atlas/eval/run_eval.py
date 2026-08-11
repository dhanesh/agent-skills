#!/usr/bin/env python3
"""Gate-runnable outcome eval for project-atlas (docs/eval-standard.md).

End-to-end, in a tempdir: build a synthetic developer machine (a git checkout
with a remote and a commit, a manifest-only project, a vendor tree that must be
invisible, a scratch directory that must not count as a project, and a project
whose README is far larger than the index cap), run the shipped atlas.py against
it, and grade the outcomes the skill actually promises:

  index      — the right roots are found, vendor/scratch noise is not
  durability — re-scanning does not duplicate; a vanished project is tombstoned,
               not deleted; the index is left in WAL, which Litestream requires
  search     — README, docs/ and path text are findable, on the FTS5 engine and
               on the LIKE fallback alike, with identical hits
  cap        — an oversized README is truncated to the documented byte cap and
               still matches a term from its head
  config     — a generated Litestream config satisfies every constraint the
               research validation pinned (singular `replica:`, https R2
               endpoint, explicit sync-interval, no literal secrets, restore
               command emitted)

Negative fixtures (an eval that cannot fail is not an eval):

  - a hand-written known-bad config — the deprecated `replicas:` list, a bare R2
    endpoint, an inline credential, no sync-interval — must be REJECTED by the
    same grader that accepts the generated one;
  - `litestream-config --target r2` with neither account id nor endpoint must
    exit non-zero rather than emit a config that cannot work;
  - `search` against a nonexistent index must exit non-zero;
  - `doctor --require <unknown-capability>` must exit non-zero;
  - a directory whose only marker is a Makefile must NOT be indexed.

Offline, deterministic, stdlib-only, no repo writes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ATLAS = os.path.join(SKILL, "assets", "atlas.py")

_checks: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))
    return bool(ok)


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def git(cwd: str, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=eval@example.invalid", "-c", "user.name=eval"]
        + list(args),
        cwd=cwd, check=True, capture_output=True, text=True, timeout=30,
    )


def atlas(*args: str, env=None):
    proc_env = dict(os.environ)
    proc_env.setdefault("ATLAS_NOW", "2026-01-01T00:00:00+00:00")
    if env:
        proc_env.update(env)
    return subprocess.run(
        [sys.executable, ATLAS] + list(args),
        capture_output=True, text=True, timeout=120, env=proc_env,
    )


# ── harness ──────────────────────────────────────────────────────────────────

BIG_README = "MARKERWORD stripe webhook retries\n" + ("filler line of prose\n" * 20000)


def build_machine(root: str) -> dict:
    """A synthetic machine: two real projects, plus noise that must stay out."""
    payments = os.path.join(root, "src", "payments")
    write(os.path.join(payments, "README.md"), "# payments\n\nStripe webhook retry queue.\n")
    write(os.path.join(payments, "pyproject.toml"), "[project]\nname = 'payments'\n")
    write(os.path.join(payments, "app", "main.py"), "print('hi')\n")
    git(payments, "init", "-q", ".")
    git(payments, "remote", "add", "origin", "https://example.invalid/payments.git")
    git(payments, "add", "-A")
    git(payments, "commit", "-qm", "initial")

    ingest = os.path.join(root, "src", "ingest")
    write(os.path.join(ingest, "go.mod"), "module ingest\n")
    write(os.path.join(ingest, "main.go"), "package main\n")
    write(os.path.join(ingest, "docs", "design.md"), "kafka consumer group rebalancing\n")

    archive = os.path.join(root, "src", "archive")
    write(os.path.join(archive, "package.json"), '{"name":"archive"}\n')
    write(os.path.join(archive, "README.md"), BIG_README)

    # Noise: a vendor tree and a scratch directory carrying only a weak marker.
    write(os.path.join(root, "src", "node_modules", "left-pad", "package.json"), "{}\n")
    write(os.path.join(ingest, "vendor", "dep", "go.mod"), "module dep\n")
    write(os.path.join(root, "src", "scratch", "Makefile"), "all:\n\t@true\n")
    return {"payments": payments, "ingest": ingest, "archive": archive}


# ── config grader (model-free) ───────────────────────────────────────────────

def grade_config(text: str) -> list[str]:
    """Deterministic checks a Litestream 0.5.x config for this skill must pass.

    Each rule traces to a finding in docs/project-atlas/2026-08-11-research-validation.md.
    Returns the list of problems; empty means acceptable.
    """
    problems = []
    if not re.search(r"^dbs:$", text, re.M):
        problems.append("no dbs: block")
    if not re.search(r"^  - path: /", text, re.M):
        problems.append("no absolute database path under dbs:")
    if not re.search(r"^    replica:$", text, re.M):
        problems.append("no singular 'replica:' mapping (Litestream 0.5.x shape)")
    if re.search(r"^\s*replicas:", text, re.M):
        problems.append("uses the deprecated 'replicas:' list")
    if not re.search(r"^      sync-interval: \S+", text, re.M):
        problems.append("no explicit sync-interval (would inherit Litestream's 1s default)")
    if not re.search(r"^      (url|path): \S+", text, re.M):
        problems.append("replica has neither url: nor path:")
    for endpoint in re.findall(r"endpoint=([^&\s]+)", text):
        if not endpoint.startswith("https://"):
            problems.append("endpoint %r lacks the https:// scheme R2 detection needs"
                            % endpoint)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for key in ("access-key-id:", "secret-access-key:"):
            if stripped.startswith(key):
                value = stripped[len(key):].strip()
                if not (value.startswith("${") and value.endswith("}")):
                    problems.append("%s is a literal, not an environment expansion" % key)
    return problems


# The known-bad fixture. The credential-shaped value is assembled at runtime so
# this file carries no secret-shaped literal for the repo's leak scanner to trip on.
def bad_config() -> str:
    fake_id = "AKIA" + "EXAMPLE" * 3
    fake_value = "NOT-A-REAL-VALUE-" + "EXAMPLE" * 3
    return "\n".join([
        "dbs:",
        "  - path: /home/dev/.local/share/project-atlas/atlas.db",
        "    replicas:",
        "      - url: s3://atlas/index?endpoint=acct123.r2.cloudflarestorage.com",
        "        access-key-id: %s" % fake_id,
        "        secret-access-key: %s" % fake_value,
    ]) + "\n"


# ── eval ─────────────────────────────────────────────────────────────────────

def main() -> int:
    if not os.path.exists(ATLAS):
        print("CHECK: atlas.py present — FAIL (%s missing)" % ATLAS)
        print("EVAL_RESULT: FAIL (0/1 checks)")
        return 1
    if shutil.which("git") is None:
        print("CHECK: git available — FAIL (git is required to build the fixture)")
        print("EVAL_RESULT: FAIL (0/1 checks)")
        return 1

    tmp = tempfile.mkdtemp(prefix="project-atlas-eval-")
    try:
        machine = build_machine(tmp)
        db = os.path.join(tmp, "index", "atlas.db")
        root = os.path.join(tmp, "src")

        # ── index ────────────────────────────────────────────────────────────
        first = atlas("scan", "--db", db, root)
        check("scan runs clean", first.returncode == 0,
              (first.stderr or first.stdout).strip()[:160])

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        paths = [r["path"] for r in conn.execute("SELECT path FROM projects ORDER BY path")]
        check("three real project roots indexed", len(paths) == 3, "found %d" % len(paths))
        check("vendor trees excluded",
              not any("node_modules" in p or "/vendor/" in p for p in paths),
              ", ".join(os.path.basename(p) for p in paths))
        check("NEGATIVE: Makefile-only directory is not a project",
              not any(p.endswith("scratch") for p in paths))

        row = conn.execute("SELECT * FROM projects WHERE path = ?",
                           (machine["payments"],)).fetchone()
        check("git metadata captured",
              bool(row) and row["vcs"] == "git"
              and row["git_remote"] == "https://example.invalid/payments.git"
              and len(row["git_head"] or "") == 40 and bool(row["git_head_at"]),
              "remote=%s head=%s" % (row["git_remote"], (row["git_head"] or "")[:7]))
        check("language and toolchain detected",
              "python" in (row["languages"] or "") and "pyproject" in (row["toolchains"] or ""),
              "%s / %s" % (row["languages"], row["toolchains"]))
        conn.close()

        # ── durability ───────────────────────────────────────────────────────
        atlas("scan", "--db", db, root)
        conn = sqlite3.connect(db)
        total = conn.execute("SELECT count(*) FROM projects").fetchone()[0]
        conn.close()
        check("re-scan is idempotent", total == 3, "%d rows after second scan" % total)

        shutil.rmtree(machine["ingest"])
        atlas("scan", "--db", db, root)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        gone = conn.execute("SELECT * FROM projects WHERE path = ?",
                            (machine["ingest"],)).fetchone()
        still = conn.execute("SELECT count(*) FROM projects").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        check("vanished project is tombstoned, not deleted",
              gone is not None and gone["missing_since"] is not None and still == 3,
              "rows=%d missing_since=%s" % (still, gone and gone["missing_since"]))
        check("index left in WAL for Litestream", str(mode).lower() == "wal", "mode=%s" % mode)

        # ── search ───────────────────────────────────────────────────────────
        fts = atlas("search", "--db", db, "--json", "stripe")
        payload = json.loads(fts.stdout) if fts.returncode == 0 else {"hits": []}
        fts_paths = sorted(h["path"] for h in payload.get("hits", []))
        check("README text is searchable",
              fts.returncode == 0 and machine["payments"] in fts_paths,
              "engine=%s hits=%d" % (payload.get("engine"), len(fts_paths)))

        like = atlas("search", "--db", db, "--json", "--no-fts", "stripe")
        like_payload = json.loads(like.stdout) if like.returncode == 0 else {"hits": []}
        like_paths = sorted(h["path"] for h in like_payload.get("hits", []))
        check("LIKE fallback returns the same hits and exits 0",
              like.returncode == 0 and like_paths == fts_paths
              and like_payload.get("engine") == "like",
              "fts=%s like=%s" % (len(fts_paths), len(like_paths)))

        by_path = atlas("search", "--db", db, "--json", "archive")
        by_path_hits = json.loads(by_path.stdout).get("hits", []) if by_path.returncode == 0 else []
        check("path fragments are searchable",
              any(h["path"] == machine["archive"] for h in by_path_hits),
              "%d hit(s)" % len(by_path_hits))

        # ── doc cap ──────────────────────────────────────────────────────────
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        doc = conn.execute(
            "SELECT body FROM docs JOIN projects p ON p.id = docs.project_id "
            "WHERE p.path = ?", (machine["archive"],)).fetchone()
        conn.close()
        stored = len((doc["body"] if doc else "").encode("utf-8"))
        check("oversized README truncated to the documented cap",
              0 < stored <= 65536 < len(BIG_README.encode("utf-8")),
              "%d bytes stored from %d" % (stored, len(BIG_README.encode("utf-8"))))
        head = atlas("search", "--db", db, "--json", "MARKERWORD")
        head_hits = json.loads(head.stdout).get("hits", []) if head.returncode == 0 else []
        check("truncated document still matches a term from its head",
              any(h["path"] == machine["archive"] for h in head_hits),
              "%d hit(s)" % len(head_hits))

        # ── generated configuration ──────────────────────────────────────────
        gen = atlas("litestream-config", "--db", db, "--target", "r2",
                    "--bucket", "atlas-index", "--account-id", "acct123",
                    env={"AWS_SECRET_ACCESS_KEY": "REALSECRETVALUE0123456789"})
        problems = grade_config(gen.stdout) if gen.returncode == 0 else ["generator failed"]
        check("generated R2 config passes every config rule",
              gen.returncode == 0 and not problems, "; ".join(problems)[:200])
        check("R2 endpoint carries the https scheme",
              "endpoint=https://acct123.r2.cloudflarestorage.com" in gen.stdout)
        check("no literal secret reaches the config",
              "REALSECRETVALUE0123456789" not in gen.stdout + gen.stderr)
        restore_lines = [l for l in gen.stderr.splitlines()
                         if l.startswith("litestream restore")]
        check("restore runbook names the same replica url",
              bool(restore_lines)
              and all("acct123.r2.cloudflarestorage.com" in l for l in restore_lines),
              "%d restore line(s)" % len(restore_lines))

        s3 = atlas("litestream-config", "--db", db, "--target", "s3",
                   "--bucket", "atlas-index", "--region", "eu-west-1")
        check("generated S3 config passes every config rule",
              s3.returncode == 0 and not grade_config(s3.stdout),
              "; ".join(grade_config(s3.stdout))[:200])

        # ── negative fixtures ────────────────────────────────────────────────
        bad = grade_config(bad_config())
        check("NEGATIVE: known-bad config is rejected by the grader",
              len(bad) >= 4, "%d problem(s): %s" % (len(bad), "; ".join(bad)[:160]))
        check("NEGATIVE: grader names the deprecated replicas list",
              any("replicas" in p for p in bad))
        check("NEGATIVE: grader names the bare R2 endpoint",
              any("https://" in p for p in bad))
        check("NEGATIVE: grader names the inline credential",
              any("literal" in p for p in bad))

        incomplete = atlas("litestream-config", "--db", db, "--target", "r2",
                           "--bucket", "atlas-index")
        check("NEGATIVE: r2 without account id or endpoint is refused",
              incomplete.returncode != 0 and "ERROR" in incomplete.stderr,
              "exit=%d" % incomplete.returncode)

        no_index = atlas("search", "--db", os.path.join(tmp, "absent.db"), "anything")
        check("NEGATIVE: search on a missing index exits non-zero",
              no_index.returncode != 0, "exit=%d" % no_index.returncode)

        bogus = atlas("doctor", "--db", db, "--require", "telepathy")
        check("NEGATIVE: unknown required capability exits non-zero",
              bogus.returncode != 0 and "unknown capability" in bogus.stderr,
              "exit=%d" % bogus.returncode)

        missing_bin = atlas("doctor", "--db", db, "--require", "litestream",
                            env={"PATH": os.path.join(tmp, "empty-bin")})
        check("NEGATIVE: doctor fails when a required binary is absent",
              missing_bin.returncode == 1, "exit=%d" % missing_bin.returncode)

        # ── the good-fixture side of the grader ──────────────────────────────
        doctor = atlas("doctor", "--db", db, "--json")
        report = json.loads(doctor.stdout) if doctor.returncode == 0 else {}
        check("doctor reports the capabilities the skill depends on",
              all(k in report for k in ("fts5", "journal_mode", "litestream", "git"))
              and report.get("journal_mode") == "wal"
              and report.get("projects") == 3,
              "projects=%s mode=%s" % (report.get("projects"), report.get("journal_mode")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for c in _checks if c)
    total_checks = len(_checks)
    if passed == total_checks:
        print("EVAL_RESULT: PASS (%d/%d checks)" % (passed, total_checks))
        return 0
    print("EVAL_RESULT: FAIL (%d/%d checks)" % (passed, total_checks))
    return 1


if __name__ == "__main__":
    sys.exit(main())
