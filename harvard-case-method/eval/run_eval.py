#!/usr/bin/env python3
"""Gate-runnable outcome eval for harvard-case-method (docs/eval-standard.md).

The skill's end-to-end promise is a *decision-forcing* case: the learner cannot
see the outcome until a complete decision is on record, and a case that leaks
the ending, invents figures, or studies a winner alone never gets sealed in the
first place. This eval builds two synthetic case studies in a tempdir and grades
that promise through the shipped CLI:

  TREATMENT — a case written the way the skill prescribes:
    lints clean, seals, refuses to reveal before a commit, refuses an
    incomplete decision, then reveals exactly the sealed B-case.

  CONTROL (negative fixture) — the *same* company written the way an AI
    answers "analyze X using the Harvard case study method": the whole story
    told backwards with the ending in hand. It must fail L1/L2/L4/L5 and be
    refused at `seal`.

Plus a tamper fixture (a swapped seal fails its checksum) and a stage fixture
(the ordering never runs backwards).

Offline, stdlib-only, deterministic, all writes under tempfile.mkdtemp().
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CASEKIT = os.path.join(SKILL, "assets", "casekit.py")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL",
                                " (%s)" % detail if detail else ""))


def kit(root, *args):
    return subprocess.run([sys.executable, CASEKIT, "--root", root] + list(args),
                          capture_output=True, text=True, timeout=60, check=False)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ── Harness: the two arms ────────────────────────────────────────────────────

DECISION_DATE = "2011-06-30"

# A-case as the skill prescribes: knowable on 2011-06-30, sourced, comparator named.
TREATMENT_CASE = """# Northwind Payments — case as of 2011-06-30

## The Situation

Accepting card payments online requires a merchant account that underwriters take
weeks to approve.[^1] Developers integrating the incumbent gateways report multi-day
integration work.[^1]

## The Protagonist

Two founders with no banking relationships, choosing how to reach developers before
their runway ends.

## What Is Known

The incumbent gateways charge roughly 2.9% plus a fixed fee per transaction.[^2]
The company has 4 employees and no bank sponsor.[^1]

## What Is Uncertain

Whether developers will route real money through an unknown startup, and whether a
sponsor bank will take the fraud exposure of instant onboarding.

## Comparators

- A rival launched the same year with an identical instant-onboarding pitch, lost its
  sponsor bank after a fraud spike, and wound down.[^3]
- An incumbent processor tried a self-serve tier and abandoned it.[^3]

## The Decision

Underwrite instantly and carry the fraud risk, or resell an incumbent's onboarding
and stay safe but slow. What should the founders do?

## Sources

[^1]: https://example.test/contemporaneous-reporting
[^2]: https://example.test/published-rate-card
[^3]: https://example.test/2019/sector-retrospective — a retrospective, cited only.
"""

TREATMENT_REVEAL = """# Reveal (B-case)

## What They Decided

They underwrote instantly and absorbed the fraud losses.

## What Happened

Fraud losses ran ahead of plan for six quarters before the risk models caught up. A
sponsor bank nearly withdrew in 2013.

## What Was Unknowable

Whether the sponsor bank would hold. It very nearly did not.

## Sources

[^1]: https://example.test/2019/sector-retrospective
"""

# The default an AI produces for "analyze X using the HBS case study method":
# the whole arc, ending known, figures from memory, one winner studied alone.
CONTROL_CASE = """# Northwind Payments — case as of 2011-06-30

## The Situation

Online payments were broken and nobody had fixed developer onboarding.

## The Protagonist

Two founders who saw what everyone else missed.

## What Is Known

They bet on instant underwriting and it turned out to be right. By 2014 they were
processing 20 billion in annualized volume and went on to define the category. Today
the company is worth 95 billion.

## What Is Uncertain

Nothing much — the strategy was sound.

## The Decision

They decided to underwrite instantly.

## Sources

[^1]: https://example.test/blog
"""

COMPLETE_DECISION = """# My decision

## Decision

Underwrite instantly, capped at a low per-merchant exposure.

## Reasoning

The onboarding delay is the actual customer pain; reselling leaves it in place.

## Disconfirming evidence

The comparator died doing exactly this, and its sponsor bank pulled out.

## What would change my mind

A sponsor bank refusing the instant-onboarding structure at any exposure cap.
"""

INCOMPLETE_DECISION = """# My decision

## Decision

Underwrite instantly.

## Reasoning

It is the bigger opportunity.
"""


def main():
    tmp = tempfile.mkdtemp(prefix="hcm-eval-")
    root = os.path.join(tmp, "cases")
    try:
        # ── CONTROL: the retrospective narration must be refused ─────────────
        r = kit(root, "new", "control", "--company", "Northwind Payments",
                "--decision-date", DECISION_DATE)
        check("control case scaffolds", r.returncode == 0, r.stderr.strip()[:80])
        write(os.path.join(root, "control", "case.md"), CONTROL_CASE)

        r = kit(root, "lint", "control")
        out = r.stdout
        failed = {line.split()[1] for line in out.splitlines()
                  if line.startswith("LINT:") and "— FAIL" in line}
        check("control: hindsight-narrated case fails lint",
              r.returncode == 1 and "LINT_RESULT: FAIL" in out, out.strip()[-70:])
        check("control: future date (2014) caught by L1", "L1" in failed, str(sorted(failed)))
        check("control: outcome language caught by L2", "L2" in failed, str(sorted(failed)))
        check("control: unsourced figures caught by L4", "L4" in failed, str(sorted(failed)))
        check("control: missing comparator caught by L5", "L5" in failed, str(sorted(failed)))

        r = kit(root, "seal", "control")
        check("control: seal is refused while lint fails",
              r.returncode == 2 and "fails lint" in r.stderr, r.stderr.strip()[:80])
        check("control: stays at stage drafting",
              json.loads(read(os.path.join(root, "control", "state.json")))["stage"] == "drafting")

        # ── TREATMENT: the prescribed case runs the full ordering ────────────
        kit(root, "new", "treatment", "--company", "Northwind Payments",
            "--decision-date", DECISION_DATE)
        tdir = os.path.join(root, "treatment")
        write(os.path.join(tdir, "case.md"), TREATMENT_CASE)
        write(os.path.join(tdir, "reveal.md"), TREATMENT_REVEAL)

        r = kit(root, "lint", "treatment")
        check("treatment: prescribed case passes every lint rule",
              r.returncode == 0 and "LINT_RESULT: PASS" in r.stdout,
              "; ".join(l for l in r.stdout.splitlines() if "FAIL" in l)[:100])

        r = kit(root, "seal", "treatment")
        check("treatment: seals once lint is clean", r.returncode == 0, r.stderr.strip()[:80])
        check("treatment: plaintext reveal is removed",
              not os.path.exists(os.path.join(tdir, "reveal.md")))
        sealed = read(os.path.join(tdir, "reveal.sealed"))
        check("treatment: outcome is not readable in the sealed blob",
              "What Happened" not in sealed and "fraud losses" not in sealed.lower())

        r = kit(root, "reveal", "treatment")
        check("treatment: reveal is refused before any decision is committed",
              r.returncode == 2 and "commit a decision before revealing" in r.stderr,
              r.stderr.strip()[:80])
        check("treatment: refused reveal leaks nothing to stdout",
              "What Happened" not in r.stdout and "sponsor bank" not in r.stdout.lower())

        write(os.path.join(tdir, "decision.md"), INCOMPLETE_DECISION)
        r = kit(root, "commit", "treatment")
        check("treatment: a decision with no falsifier is refused",
              r.returncode == 2 and "what would change my mind" in r.stderr,
              r.stderr.strip()[:90])

        write(os.path.join(tdir, "decision.md"), COMPLETE_DECISION)
        r = kit(root, "commit", "treatment")
        check("treatment: a complete decision commits", r.returncode == 0, r.stderr.strip()[:80])
        state = json.loads(read(os.path.join(tdir, "state.json")))
        check("treatment: the committed decision is fingerprinted",
              state["stage"] == "committed" and len(state.get("decision_sha256", "")) == 64)

        r = kit(root, "commit", "treatment")
        check("treatment: the decision cannot be swapped after commit",
              r.returncode == 2 and "already has a committed decision" in r.stderr)

        r = kit(root, "reveal", "treatment")
        check("treatment: reveal now returns exactly the sealed B-case",
              r.returncode == 0 and r.stdout.strip() == TREATMENT_REVEAL.strip(),
              r.stderr.strip()[:80])
        check("treatment: stage advances to revealed",
              json.loads(read(os.path.join(tdir, "state.json")))["stage"] == "revealed")

        # ── Tamper fixture: a swapped ending fails its checksum ──────────────
        kit(root, "new", "tamper", "--company", "Northwind Payments",
            "--decision-date", DECISION_DATE)
        xdir = os.path.join(root, "tamper")
        write(os.path.join(xdir, "case.md"), TREATMENT_CASE)
        write(os.path.join(xdir, "reveal.md"), TREATMENT_REVEAL)
        write(os.path.join(xdir, "decision.md"), COMPLETE_DECISION)
        kit(root, "seal", "tamper")
        kit(root, "commit", "tamper")
        write(os.path.join(xdir, "reveal.sealed"),
              base64.b64encode(b"# Reveal\n\nA flattering ending.\n").decode("ascii") + "\n")
        r = kit(root, "reveal", "tamper")
        check("tamper: a swapped seal is rejected by checksum",
              r.returncode == 2 and "checksum" in r.stderr, r.stderr.strip()[:80])
        check("tamper: the substituted text is not printed",
              "flattering" not in r.stdout)

        # ── Ordering fixture: commit cannot precede seal ─────────────────────
        kit(root, "new", "ordering", "--company", "Northwind Payments",
            "--decision-date", DECISION_DATE)
        write(os.path.join(root, "ordering", "decision.md"), COMPLETE_DECISION)
        r = kit(root, "commit", "ordering")
        check("ordering: commit is refused before the reveal is sealed",
              r.returncode == 2 and "seal the reveal" in r.stderr, r.stderr.strip()[:80])

        r = kit(root, "status", "ordering")
        check("status reports the stage and the next step",
              r.returncode == 0 and "DRAFTING" in r.stdout and "next:" in r.stdout)

        r = kit(root, "status", "no-such-case")
        check("an unknown case fails cleanly, without a traceback",
              r.returncode == 2 and "Traceback" not in r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed, total = sum(_checks), len(_checks)
    print("EVAL_RESULT: %s (%d/%d checks)"
          % ("PASS" if passed == total else "FAIL", passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
