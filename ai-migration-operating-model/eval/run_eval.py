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

        # ── Parallel-ready judge: capacity, sharding, impact, mutants ────────
        probe_py = os.path.join(dst, "assets", "capacity_probe.py")
        r = run(probe_py, "--host-cpus", "10", "--vm-cpus", "4", "--vm-gib",
                "6", "--free-disk-gib", "80")
        check("capacity probe sizes lanes from the VM, not host cores",
              r.returncode == 0 and "LANES: 3 (parallel: limited by VM CPUs"
              in r.stdout, r.stdout.strip().splitlines()[-1])
        r = run(probe_py, "--host-cpus", "10", "--vm-cpus", "8", "--vm-gib",
                "16", "--free-disk-gib", "2")
        check("capacity probe goes serial on low disk (negative arm)",
              "LANES: 1 (serial: free disk" in r.stdout)

        golden = os.path.join(tmp, "e2e-golden")
        for suite, n in (("pay", 3), ("fees", 2), ("kyc", 1)):
            for i in range(n):
                os.makedirs(golden, exist_ok=True)
                with open(os.path.join(golden, "%s__s%d.json" % (suite, i)),
                          "w", encoding="utf-8") as f:
                    json.dump({"id": i, "topic": "evt-l{lane}"}, f)
        cap = os.path.join(tmp, "capture.py")
        with open(cap, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""\
                import json, os, sys
                golden, suite, out, lane, broken = sys.argv[1:6]
                if suite == broken:
                    sys.exit(4)
                for name in sorted(os.listdir(golden)):
                    if name.startswith(suite + "__"):
                        with open(os.path.join(golden, name)) as fh:
                            v = json.load(fh)
                        v["topic"] = v["topic"].replace("-l{lane}", "-l" + lane)
                        with open(os.path.join(out, name), "w") as fh:
                            json.dump(v, fh)
                """))
        shard_py = os.path.join(dst, "assets", "e2e_shard.py")

        def shard(broken="none", *extra):
            return run(shard_py, "--golden", golden,
                       "--out-dir", os.path.join(tmp, "e2e-runs"),
                       "--capture-cmd", "%s %s %s {suite} {out} {lane} %s"
                       % (sys.executable, cap, golden, broken),
                       "--lanes", "2", "--no-lock", *extra)
        r = shard("none", "--lane-echo=-l{lane}")
        check("sharded e2e merges lanes and judges them as one corpus",
              r.returncode == 0
              and "PARITY_RESULT: PASS (6 scenario(s), 0 diff(s))" in r.stdout)
        r = shard("fees", "--lane-echo=-l{lane}")
        check("a lane whose capture failed is judged missing, not skipped",
              r.returncode == 1 and "scenario missing in new" in r.stdout)
        r = shard("none")
        check("lane-echo normalisation is load-bearing (fails without it)",
              r.returncode == 1 and "PARITY_RESULT: FAIL" in r.stdout)

        repo = os.path.join(tmp, "impact-repo")
        files = {"internal/app/app.go": "w", "internal/pay/h.go": "p",
                 "internal/fees/h.go": "f", "go.mod": "m",
                 "judge/golden/pay__a.json": "{}",
                 "judge/golden/fees__a.json": "{}"}
        for rel, body in files.items():
            os.makedirs(os.path.dirname(os.path.join(repo, rel)), exist_ok=True)
            with open(os.path.join(repo, rel), "w", encoding="utf-8") as f:
                f.write(body)
        cfg = os.path.join(repo, "impact.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"manifest": "out/manifest.json",
                       "track": ["internal/*", "go.mod", "judge/*"],
                       "full_on": ["internal/app/", "go.mod"],
                       "suite_files": [r"^judge/golden/(?P<suite>[a-z]+)__"],
                       "units": [r"^(?P<unit>internal/[^/]+)/[^/]+\.go$"],
                       "golden": "judge/golden",
                       "deps_cmd": "echo internal/{suite}"}, f)
        impact_py = os.path.join(dst, "assets", "impact.py")
        r = run(impact_py, "--config", cfg, "suites")
        check("impact selection fails closed to ALL with no baseline",
              r.stdout.strip() == "ALL")
        red = os.path.join(tmp, "red-status.json")
        with open(red, "w", encoding="utf-8") as f:
            json.dump({"verdict": "PARITY_RESULT: FAIL (2 diff(s))",
                       "suites": "all"}, f)
        r = run(impact_py, "--config", cfg, "record", "--status", red)
        check("impact refuses a baseline from a red run (negative arm)",
              r.returncode == 1 and "not green" in r.stderr)
        green = os.path.join(tmp, "green-status.json")
        with open(green, "w", encoding="utf-8") as f:
            json.dump({"verdict": "PARITY_RESULT: PASS (2 scenario(s), "
                       "0 diff(s))", "suites": "all"}, f)
        run(impact_py, "--config", cfg, "record", "--status", green)
        with open(os.path.join(repo, "internal/fees/h.go"), "w") as f:
            f.write("f2")
        r = run(impact_py, "--config", cfg, "suites")
        check("impact selects only the suite whose package changed",
              r.stdout.strip() == "fees", r.stdout.strip())
        with open(os.path.join(repo, "internal/app/app.go"), "w") as f:
            f.write("rewired")
        r = run(impact_py, "--config", cfg, "suites")
        check("impact fails closed to ALL on a wiring change",
              r.stdout.strip() == "ALL")

        src = os.path.join(tmp, "legacy")
        os.makedirs(os.path.join(src, "pay"))
        with open(os.path.join(src, "pay", "fees.py"), "w") as f:
            f.write("RATE = 5\n\ndef fee(x):\n    return x * RATE\n\n"
                    "def unused():\n    return 1\n")
        cov = os.path.join(tmp, "cov.json")
        with open(cov, "w") as f:
            json.dump({"pay/fees.py": {"1": [""], "3": [""],
                                       "4": ["pay__a"], "6": [""]}}, f)
        mut = os.path.join(tmp, "mutants.json")
        with open(mut, "w") as f:
            json.dump({"body": ["pay/fees.py", "return x * RATE", "return x"],
                       "const": ["pay/fees.py", "RATE = 5", "RATE = 6"]}, f)
        sel_py = os.path.join(dst, "assets", "mutant_select.py")
        r = run(sel_py, "--coverage", cov, "--mutants", mut, "--src-root", src)
        check("mutants run on reaching scenarios; import-time code gets all",
              r.returncode == 0 and "MUTANT: body subset 1 scenario(s)"
              in r.stdout and "MUTANT: const full" in r.stdout)
        with open(mut, "w") as f:
            json.dump({"dead": ["pay/fees.py", "return 1", "return 2"]}, f)
        r = run(sel_py, "--coverage", cov, "--mutants", mut, "--src-root", src)
        check("a mutant no scenario reaches is rejected (negative arm)",
              r.returncode == 1 and "REJECTED" in r.stdout)

        r = run(parity_py, old_dir, new_ok, "--ignore", "generated_at",
                "--tolerance", "1e-9", "--require", "cache=cache_writes")
        check("the verdict line counts unrecorded effect classes",
              r.returncode == 0 and "unjudged: 2)" in r.stdout)
        r = run(parity_py, old_dir, new_ok, "--ignore", "generated_at",
                "--tolerance", "1e-9", "--require", "cache=cache_writes",
                "--fail-on-unjudged")
        check("an unjudged class fails the coverage gate (negative arm)",
              r.returncode == 1)

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
