#!/usr/bin/env python3
"""Gate-runnable outcome eval for ai-migration-operating-model
(docs/eval-standard.md).

End-to-end over the skill's deliverable contract: copies the skill into a
tempdir, builds a synthetic migration scenario there — a known-good
Migration Control Pack, seeded-defect packs, and old/new golden-output
corpora — then runs the shipped tooling via subprocess and grades outcomes
deterministically. The pack linter accepts the good pack and rejects each
seeded defect with the specific message; the parity differ passes identical
corpora, catches a seeded behavioral break (the "judge can fail" proof),
tolerates declared noise/float drift, and flags a missing scenario.
Stdlib-only, offline, no repo writes.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

GOOD_PACK = {
    "RULEBOOK.md": textwrap.dedent(
        """\
        # Rulebook — payments-service Python -> TypeScript

        - MR1: Money amounts must map to the Decimal wrapper type, never the
          plain number type.
        - MR2: Date arithmetic must preserve the source system's UTC handling.
        - MR3: Repository methods must return typed result objects, never raw
          database rows.
        """
    ),
    "DEPENDENCY_MAP.md": textwrap.dedent(
        """\
        1. Utility libraries (no internal dependencies)
        2. Domain models
        3. Service layer
        4. API handlers
        """
    ),
    "GAP_INVENTORY.md": textwrap.dedent(
        """\
        - G1: Is loan.amount stored in minor or major currency units?
        - G2: Nullable customer.email semantics — decision: keep optional,
          normalize empty string to null.
        """
    ),
    "PORTABILITY_TEST_PLAN.md": textwrap.dedent(
        """\
        - S1: Repayment schedule for account 42 on 2024-01-31 — run both
          systems, diff via parity_check.sh.
        - S2: Fee calculation for a zero-balance account — outputs must match
          after normalization.
        """
    ),
    "parity_check.sh": textwrap.dedent(
        """\
        #!/bin/sh
        old_tool run "$1" > old.json
        new_tool run "$1" > new.json
        python3 parity_diff.py old.json new.json --ignore generated_at
        """
    ),
    "AGENT_WORK_QUEUE.md": textwrap.dedent(
        """\
        - src/models/loan.py [migrated]
        - src/services/schedule.py [parity-fail]
        - src/api/handlers.py [pending]
        """
    ),
    "REVIEWER_PROMPTS.md": textwrap.dedent(
        """\
        - Attack money precision: did any Decimal become a float?
        - Attack timezone drift: does date math still assume UTC?
        - Attack nullability: were optional fields silently defaulted?
        - Attack error handling and the idempotency of retries.
        """
    ),
    "PHASE_GATES.md": textwrap.dedent(
        """\
        1. Judge exists: parity_check.sh catches a deliberately broken case.
        2. Rulebook stress-tested on the pilot slice.
        3. Bulk migration compiles and unit suites are green.
        4. Behavioral parity green across all golden scenarios.
        5. Rollout and rollback plan approved.
        """
    ),
}

# A golden scenario the old system produced: a repayment schedule.
OLD_SCHEDULE = {
    "account": 42,
    "currency": "INR",
    "installments": [
        {"due": "2024-02-29", "principal": 5000, "fee": 100.0},
        {"due": "2024-03-31", "principal": 5000, "fee": 100.0},
    ],
    "generated_at": "2024-01-31T00:00:00Z",
}

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def write_pack(root, overrides=None, drop=()):
    files = dict(GOOD_PACK)
    if overrides:
        files.update(overrides)
    for name in drop:
        files.pop(name, None)
    os.makedirs(root, exist_ok=True)
    for name, content in files.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            f.write(content)
    return root


def write_corpus(root, mutate=None, drop=()):
    """Write the golden corpus; mutate hooks in a per-scenario edit."""
    os.makedirs(root, exist_ok=True)
    scenarios = {
        "schedule-42.json": json.loads(json.dumps(OLD_SCHEDULE)),
        "fee-zero-balance.json": {"account": 7, "fee": 0.0,
                                  "generated_at": "2024-01-31T09:00:00Z"},
    }
    for name in drop:
        scenarios.pop(name, None)
    for name, obj in scenarios.items():
        if mutate:
            mutate(name, obj)
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            json.dump(obj, f)
    return root


def main():
    tmp = tempfile.mkdtemp(prefix="aimom-eval-")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        dst = os.path.join(tmp, "skill")
        shutil.copytree(
            SKILL, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        lint_py = os.path.join(dst, "assets", "control_pack_lint.py")
        parity_py = os.path.join(dst, "assets", "parity_diff.py")

        def run(script, *args):
            return subprocess.run(
                [sys.executable, script, *args],
                capture_output=True, text=True, timeout=30, env=env,
            )

        # ── Control pack, positive arm ───────────────────────────────────────
        good = write_pack(os.path.join(tmp, "pack-good"))
        r = run(lint_py, good)
        check("linter accepts a complete control pack",
              r.returncode == 0 and "PACK_RESULT: PASS" in r.stdout,
              r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "")

        # ── Control pack, seeded defects must be rejected ────────────────────
        r = run(lint_py, write_pack(os.path.join(tmp, "pack-norb"),
                                    drop=("RULEBOOK.md",)))
        check("linter rejects a pack with no rulebook",
              r.returncode == 1
              and "FAIL: missing artifact: RULEBOOK.md" in r.stdout)

        r = run(lint_py, write_pack(
            os.path.join(tmp, "pack-gap"),
            overrides={"GAP_INVENTORY.md":
                       "- G1: The amount field is probably cents.\n"}))
        check("linter rejects an unexamined gap (neither '?' nor decision)",
              r.returncode == 1
              and "neither an open question" in r.stdout)

        r = run(lint_py, write_pack(
            os.path.join(tmp, "pack-queue"),
            overrides={"AGENT_WORK_QUEUE.md":
                       "- src/models/loan.py [done]\n"}))
        check("linter rejects a non-machine-readable queue status",
              r.returncode == 1 and "unknown status 'done'" in r.stdout)

        r = run(lint_py, write_pack(
            os.path.join(tmp, "pack-gates"),
            overrides={"PHASE_GATES.md":
                       "1. Kickoff meeting held.\n"
                       "2. Parity green.\n"
                       "3. Rollback approved.\n"}))
        check("linter enforces judge-first phase gates",
              r.returncode == 1
              and "gate 1 must establish the judge" in r.stdout)

        r = run(lint_py, write_pack(
            os.path.join(tmp, "pack-review"),
            overrides={"REVIEWER_PROMPTS.md":
                       "- Please look at the diff for style.\n"}))
        check("linter rejects vague reviewer prompts",
              r.returncode == 1
              and "failure-mode categories covered" in r.stdout)

        # ── Parity judge, positive arm ───────────────────────────────────────
        old_dir = write_corpus(os.path.join(tmp, "old-out"))

        def faithful(name, obj):
            # The new system regenerates timestamps and re-derives fees with
            # float arithmetic — declared noise/tolerance, not behavior drift.
            obj["generated_at"] = "2026-07-22T00:00:00Z"
            for inst in obj.get("installments", []):
                inst["fee"] = inst["fee"] + 1e-10
        new_ok = write_corpus(os.path.join(tmp, "new-ok"), mutate=faithful)
        r = run(parity_py, old_dir, new_ok,
                "--ignore", "generated_at", "--tolerance", "1e-9")
        check("parity passes a faithful migration "
              "(noise ignored, drift within tolerance)",
              r.returncode == 0 and "PARITY_RESULT: PASS" in r.stdout,
              r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "")

        # ── Parity judge, the judge can fail (negative arm) ──────────────────
        def broken(name, obj):
            obj["generated_at"] = "2026-07-22T00:00:00Z"
            if obj.get("installments"):
                obj["installments"][1]["fee"] = 102.0  # behavioral break
        new_bad = write_corpus(os.path.join(tmp, "new-bad"), mutate=broken)
        r = run(parity_py, old_dir, new_bad,
                "--ignore", "generated_at", "--tolerance", "1e-9")
        check("judge catches a seeded behavioral break and names the path",
              r.returncode == 1
              and "$.installments[1].fee" in r.stdout
              and "PARITY_RESULT: FAIL" in r.stdout)

        new_missing = write_corpus(os.path.join(tmp, "new-missing"),
                                   mutate=faithful,
                                   drop=("fee-zero-balance.json",))
        r = run(parity_py, old_dir, new_missing,
                "--ignore", "generated_at", "--tolerance", "1e-9")
        check("judge flags a golden scenario missing from the new system",
              r.returncode == 1
              and "scenario missing in new" in r.stdout)

        # Without the declared tolerance the same drift must fail: proof the
        # pass above came from the declaration, not from a differ that
        # cannot see small numeric changes.
        r = run(parity_py, old_dir, new_ok, "--ignore", "generated_at")
        check("tolerance is load-bearing (same corpora fail at zero tolerance)",
              r.returncode == 1 and "PARITY_RESULT: FAIL" in r.stdout)

        # ── The pack's own grammar examples round-trip through the linter ────
        # references/control-pack.md promises the documented grammar is what
        # the linter enforces; the GOOD_PACK fixture above IS that grammar.
        plan = GOOD_PACK["PORTABILITY_TEST_PLAN.md"]
        check("test plan fixture wires plan to judge (names parity_check.sh)",
              "parity_check.sh" in plan)

        # ── Invocation surface: the model's four parts are separately callable
        with open(os.path.join(dst, "SKILL.md"), encoding="utf-8") as f:
            skill_md = f.read()
        inv = re.search(r"^## Invocations$(.*?)(?=^## )", skill_md,
                        re.MULTILINE | re.DOTALL)
        modes = ("economics", "judge", "pack", "qualify")
        check("SKILL.md documents the four invocation modes "
              "(economics/judge/pack/qualify)",
              inv is not None
              and all("`%s" % m in inv.group(1) for m in modes),
              "section %s" % ("found" if inv else "missing"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
