#!/usr/bin/env python3
"""Gate-runnable outcome eval for harvard-case-method (docs/eval-standard.md).

The skill's end-to-end promise is that a real business decision comes out the
other side *decided and scoreable*: a brief that survives an evidence lint, a
decision record complete across the Decision Quality chain, and dated forecasts
that later resolve into a calibration profile. This eval drives a synthetic
product decision through the shipped CLI and grades that promise.

  TREATMENT — the decision as the skill prescribes: three real options, sourced
    figures, a two-case reference class, a complete DQ record with a premortem,
    a falsifier and forecasts. Must lint clean, commit, resolve, and produce a
    Brier score that matches an independently computed value.

  CONTROL (negative fixture) — the same decision reasoned the way it goes
    without the skill: two options ("do it / don't"), figures from memory, one
    comparator, no premortem, no falsifier, confidence as prose. Must be
    refused at both gates.

  MODE fixture — a live decision must NOT be run through the hindsight
    machinery (seal/reveal are refused), while a drill case must.

  SCORING fixture — Brier and calibration arithmetic checked against
    hand-computed values.

  RICE arm — a sourced, single-period sheet validates and ranks by the formula;
    a sheet with mixed periods, off-scale impact, percentage confidence or
    unsourced reach is refused AND left unranked, because a score built from a
    bad input out-ranks every honest row.

  PLAN arm — a reconciled, cross-checked plan passes; a fairytale (drivers from
    conviction, sizings 25x apart, unstaffed hockey-stick growth, no stated
    failure condition) fails on every count with the divergence quantified.
    Negative fixture: a plan formula containing a call is refused, not evaluated.

Offline, stdlib-only, deterministic, all writes under tempfile.mkdtemp().
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CASEKIT = os.path.join(SKILL, "assets", "casekit.py")
RIGOR = os.path.join(SKILL, "assets", "rigor.py")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL",
                                " (%s)" % detail if detail else ""))


def kit(root, *args):
    return subprocess.run([sys.executable, CASEKIT, "--root", root] + list(args),
                          capture_output=True, text=True, timeout=60, check=False)


def rig(*args):
    return subprocess.run([sys.executable, RIGOR] + list(args),
                          capture_output=True, text=True, timeout=60, check=False)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


QUESTION = "Should we move fee financing to a flat platform fee?"
BY = "2026-09-30"

GOOD_BRIEF = """# Decision brief

## The Decision

Should we move fee financing to a flat platform fee? Decide by 2026-09-30.

## Situation

Institutions push back on variable pricing during renewal negotiations.

## Options on the Table

- Flat platform fee per enrolled student
- Keep the rate spread with volume tiers
- Hybrid: a flat floor plus a capped spread

## Evidence

Renewal churn ran at 12% last cycle.[^1]
Average contract value is $48,000.[^1]

## Reference Class

- A peer moved to flat pricing and lost 2 of 9 anchor institutions.[^2]
- A second peer kept spread pricing and held renewals flat.[^2]

## Open Uncertainties

Whether procurement reads a flat fee as a price rise regardless of total cost.

## Sources

[^1]: https://example.test/renewals
[^2]: https://example.test/peers
"""

# Two options, memory figures, one comparator, no stated uncertainty.
BAD_BRIEF = """# Decision brief

## The Decision

We should probably move to a flat platform fee.

## Situation

Pricing feels messy.

## Options on the Table

- Move to a flat fee
- Do not move to a flat fee

## Evidence

Churn is around 12% and contracts average $48,000.

## Reference Class

- A peer did this and it worked out.

## Open Uncertainties

## Sources

[^1]: https://example.test/blog
"""

GOOD_DECISION = """# Decision record

## Frame
Pricing structure for the 2027 renewal cycle. Contract length is out of scope.

## Alternatives Considered
- Flat platform fee
- Rate spread with volume tiers
- Hybrid: flat floor plus capped spread
- Defer a cycle and instrument procurement objections first

## Values
Renewal retention over near-term margin.

## Reasoning
The hybrid preserves optionality while giving procurement a defensible number.

## Premortem
It is 2027-09-30 and this failed. Procurement read the floor as a price rise,
three anchors renegotiated down, and margin fell with no retention gain.

## Decision
Ship the hybrid to the three Q3 renewals. Owner: pricing lead.

## Falsifier
If two of three Q3 renewals cite the floor as their main objection, revert.

## Predictions
- 2026-12-31 | 0.80 | At least 2 of 3 Q3 renewals close on the hybrid
- 2026-12-31 | 0.60 | Blended take rate holds within 50 basis points
- 2027-03-31 | 0.90 | No anchor institution churns citing pricing structure
"""

# The shape a decision takes without the skill: a call and a rationale.
BAD_DECISION = """# Decision record

## Decision
Move to the flat platform fee.

## Reasoning
It is simpler and institutions keep asking for predictability. I am confident
this is right.

## Alternatives Considered
- Flat fee
- Status quo
"""


GOOD_RICE = """# RICE — Q3 roadmap

## Items

- Bulk fee upload | reach=1200/quarter | impact=2 | confidence=0.8 | effort=3 | src: cohorts [^1]
- Autopay nudges | reach=9000/quarter | impact=0.5 | confidence=0.5 | effort=2 | src: analytics [^1]
- Dashboard v2 | reach=340/quarter | impact=3 | confidence=0.8 | effort=5 | src: CRM [^2]

## Sources
[^1]: https://example.test/a
[^2]: https://example.test/b
"""

GOOD_PLAN = """# Plan — FY27

## Drivers

- institutions = 120 | src: signed pipeline [^1]
- students_per_institution = 850 | src: enrolment data [^1]
- attach_rate = 0.06 | assumption
- avg_ticket = 62000 | src: rate card [^2]

## Top Down

- revenue = institutions * students_per_institution * attach_rate * avg_ticket

## Bottom Up

- revenue = 420000000 | src: sales capacity model [^3]

## Trajectory

- FY27-Q1 | 60000000
- FY27-Q2 | 90000000
- FY27-Q3 | 120000000
- FY27-Q4 | 150000000

## What Would Have To Be True

- [least likely] Attach rate holds at 6% as we move down-market
- Sales can onboard 30 institutions a quarter

## Sources
[^1]: https://example.test/pipeline
[^2]: https://example.test/rates
[^3]: https://example.test/capacity
"""

# Every driver from conviction, sizings 25x apart, growth nobody staffs.
FAIRYTALE_PLAN = """# Plan — FY27 expansion

## Drivers

- institutions = 900
- students_per_institution = 850
- attach_rate = 0.35
- avg_ticket = 62000

## Top Down

- revenue = institutions * students_per_institution * attach_rate * avg_ticket

## Bottom Up

- revenue = 640000000 | src: sales capacity [^1]

## Trajectory

- FY27-Q1 | 40000000
- FY27-Q2 | 110000000
- FY27-Q3 | 420000000

## What Would Have To Be True

- Every signed institution reaches 35% attach in year one

## Sources
[^1]: https://example.test/capacity
"""


def main():
    tmp = tempfile.mkdtemp(prefix="hcm-eval-")
    root = os.path.join(tmp, "decisions")
    try:
        # ── CONTROL: the un-coached decision is refused at both gates ────────
        r = kit(root, "new", "control", "--decision", QUESTION, "--by", BY)
        check("control decision scaffolds", r.returncode == 0, r.stderr.strip()[:80])
        write(os.path.join(root, "control", "brief.md"), BAD_BRIEF)

        r = kit(root, "lint", "control")
        failed = {l.split()[1] for l in r.stdout.splitlines()
                  if l.startswith("LINT:") and "— FAIL" in l}
        check("control: the brief fails lint",
              r.returncode == 1 and "LINT_RESULT: FAIL" in r.stdout)
        check("control: two options caught as a whether-or-not trap (B3)",
              "B3" in failed, str(sorted(failed)))
        check("control: figures with no citation caught (B4)", "B4" in failed,
              str(sorted(failed)))
        check("control: single comparator caught as no base rate (B5)",
              "B5" in failed, str(sorted(failed)))
        check("control: empty uncertainties caught (B6)", "B6" in failed,
              str(sorted(failed)))
        check("control: a statement rather than a dated question caught (B2)",
              "B2" in failed, str(sorted(failed)))

        write(os.path.join(root, "control", "decision.md"), BAD_DECISION)
        r = kit(root, "commit", "control")
        check("control: the decision record is refused", r.returncode == 2)
        for link in ("Frame", "Values", "Premortem", "Falsifier"):
            check("control: missing %s named in the refusal" % link,
                  link in r.stderr, r.stderr.strip()[:110])
        check("control: too few alternatives named in the refusal",
              "option set" in r.stderr, r.stderr.strip()[:110])
        check("control: absence of any forecast named in the refusal",
              "Predictions" in r.stderr, r.stderr.strip()[:110])
        check("control: stays uncommitted",
              json.loads(read(os.path.join(root, "control", "state.json")))["stage"]
              == "drafting")

        # ── TREATMENT: the coached decision runs the whole loop ──────────────
        kit(root, "new", "treatment", "--decision", QUESTION, "--by", BY)
        tdir = os.path.join(root, "treatment")
        write(os.path.join(tdir, "brief.md"), GOOD_BRIEF)
        write(os.path.join(tdir, "decision.md"), GOOD_DECISION)

        r = kit(root, "lint", "treatment")
        check("treatment: the prescribed brief passes every rule",
              r.returncode == 0 and "LINT_RESULT: PASS" in r.stdout,
              "; ".join(l for l in r.stdout.splitlines() if "FAIL" in l)[:100])

        r = kit(root, "commit", "treatment")
        check("treatment: the complete record commits", r.returncode == 0,
              r.stderr.strip()[:110])
        state = json.loads(read(os.path.join(tdir, "state.json")))
        check("treatment: three forecasts are recorded verbatim",
              [p["p"] for p in state["predictions"]] == [0.80, 0.60, 0.90],
              str(state.get("predictions"))[:80])
        check("treatment: the record is fingerprinted",
              len(state.get("decision_sha256", "")) == 64)

        r = kit(root, "commit", "treatment")
        check("treatment: a committed record cannot be silently swapped",
              r.returncode == 2 and "already committed" in r.stderr)

        # ── Resolution and scoring against hand-computed values ─────────────
        for n, outcome in ((1, "yes"), (2, "no"), (3, "yes")):
            kit(root, "resolve", "treatment", "--n", str(n), "--outcome", outcome)
        r = kit(root, "resolve", "treatment", "--n", "2", "--outcome", "yes")
        check("resolution cannot be flipped without --force",
              r.returncode == 2 and "--force" in r.stderr, r.stderr.strip()[:80])

        # (0.8-1)^2 + (0.6-0)^2 + (0.9-1)^2 = 0.04 + 0.36 + 0.01 = 0.41 / 3
        expected = round(0.41 / 3, 4)
        r = kit(root, "score", "treatment")
        check("score reports the hand-computed Brier value",
              ("%.4f" % expected) in r.stdout, "expected %.4f" % expected)
        check("score reports the calibration gap",
              "calibration gap:" in r.stdout and "hit rate:" in r.stdout)
        check("score refuses to grade the DQ chain itself",
              "coach's judgement" in r.stdout)

        r = kit(root, "profile")
        check("profile pools decisions and flags the small sample",
              r.returncode == 0 and "ACROSS" in r.stdout
              and "fewer than 10 resolved forecasts" in r.stdout)

        # ── MODE fixture: hindsight machinery is drill-only ──────────────────
        r = kit(root, "seal", "treatment")
        check("live decision: seal is refused (no ending exists to hide)",
              r.returncode == 2 and "no known ending to hide" in r.stderr,
              r.stderr.strip()[:80])
        r = kit(root, "reveal", "treatment")
        check("live decision: reveal is refused",
              r.returncode == 2 and "has not happened yet" in r.stderr,
              r.stderr.strip()[:80])
        r = kit(root, "lint", "treatment")
        check("live decision: no hindsight rules are run",
              "D1" not in r.stdout and "D2" not in r.stdout)

        kit(root, "new", "drill", "--decision", QUESTION, "--by", BY, "--drill")
        ddir = os.path.join(root, "drill")
        write(os.path.join(ddir, "brief.md"), GOOD_BRIEF)
        write(os.path.join(ddir, "decision.md"), GOOD_DECISION)
        write(os.path.join(ddir, "reveal.md"), "# Reveal\n\nThey shipped the hybrid.\n")
        r = kit(root, "lint", "drill")
        check("drill: hindsight rules ARE run", "D1" in r.stdout and "D2" in r.stdout)

        write(os.path.join(ddir, "brief.md"),
              GOOD_BRIEF.replace("## Situation",
                                 "## Situation\n\nBy 2030 the bet turned out to be right."))
        r = kit(root, "lint", "drill")
        leaked = {l.split()[1] for l in r.stdout.splitlines()
                  if l.startswith("LINT:") and "— FAIL" in l}
        check("drill: leaked future date caught (D2)", "D2" in leaked, str(sorted(leaked)))
        check("drill: leaked hindsight language caught (D1)", "D1" in leaked,
              str(sorted(leaked)))
        r = kit(root, "seal", "drill")
        check("drill: seal is refused while the brief leaks",
              r.returncode == 2 and "fails lint" in r.stderr)

        write(os.path.join(ddir, "brief.md"), GOOD_BRIEF)
        check("drill: seals once the brief is clean", kit(root, "seal", "drill").returncode == 0)
        r = kit(root, "reveal", "drill")
        check("drill: reveal is refused before a decision is committed",
              r.returncode == 2 and "commit a decision before revealing" in r.stderr)
        check("drill: the refused reveal leaks nothing", "hybrid" not in r.stdout)
        kit(root, "commit", "drill")
        r = kit(root, "reveal", "drill")
        check("drill: reveal returns the ending after a commit",
              r.returncode == 0 and "They shipped the hybrid" in r.stdout)

        # ── Forecast-hygiene fixture ────────────────────────────────────────
        kit(root, "new", "certainty", "--decision", QUESTION, "--by", BY)
        write(os.path.join(root, "certainty", "brief.md"), GOOD_BRIEF)
        write(os.path.join(root, "certainty", "decision.md"),
              GOOD_DECISION.replace("| 0.80 |", "| 1.0 |"))
        r = kit(root, "commit", "certainty")
        check("a probability of 1.0 is refused as not a forecast",
              r.returncode == 2 and "certainty is not a forecast" in r.stderr,
              r.stderr.strip()[:100])

        write(os.path.join(root, "certainty", "decision.md"),
              GOOD_DECISION.replace(
                  "- 2026-12-31 | 0.80 | At least 2 of 3 Q3 renewals close on the hybrid",
                  "- I reckon it goes fine by December"))
        r = kit(root, "commit", "certainty")
        check("an unparseable forecast line is reported, not silently dropped",
              r.returncode == 2 and "expected" in r.stderr, r.stderr.strip()[:100])

        # ── RICE: the prioritisation arm ────────────────────────────────────
        good_rice = os.path.join(tmp, "rice.md")
        write(good_rice, GOOD_RICE)
        r = rig("rice", good_rice)
        check("rice: a sourced, single-period sheet validates and ranks",
              r.returncode == 0 and "RICE_RESULT: PASS" in r.stdout
              and "Ranked" in r.stdout,
              "; ".join(l for l in r.stdout.splitlines() if "FAIL" in l)[:100])
        # 9000 * 0.5 * 0.5 / 2 = 1125 is the top score
        check("rice: ranks by the RICE formula, not by input size",
              r.stdout.split("Ranked")[1].strip().splitlines()[1].strip().startswith("1. Autopay"),
              r.stdout.split("Ranked")[1].strip().splitlines()[1][:60])
        check("rice: low-confidence items are flagged for evidence",
              "Confidence at or below 50%" in r.stdout
              and "not a discount factor" in r.stdout)

        bad_rice = os.path.join(tmp, "bad-rice.md")
        write(bad_rice, GOOD_RICE
              .replace("reach=9000/quarter", "reach=9000/month")
              .replace("impact=3", "impact=5")
              .replace("confidence=0.8 | effort=3 | src: cohorts [^1]", "confidence=80 | effort=3"))
        r = rig("rice", bad_rice)
        failed = {l.split()[1] for l in r.stdout.splitlines()
                  if l.startswith("RICE:") and "— FAIL" in l}
        check("rice: mixed reach periods caught (R2)", "R2" in failed, str(sorted(failed)))
        check("rice: unsourced reach caught (R3)", "R3" in failed, str(sorted(failed)))
        check("rice: off-scale impact caught (R4)", "R4" in failed, str(sorted(failed)))
        check("rice: confidence as a percentage caught (R5)", "R5" in failed,
              str(sorted(failed)))
        check("rice: an invalid sheet is NOT ranked",
              "No ranking printed" in r.stdout and "1." not in r.stdout.split("RICE_RESULT")[1])

        no_period = os.path.join(tmp, "no-period.md")
        write(no_period, GOOD_RICE.replace("reach=1200/quarter", "reach=1200"))
        r = rig("rice", no_period)
        check("rice: reach without a time period is refused",
              r.returncode == 1 and "needs a period" in r.stdout, r.stdout[:80])

        # ── PLAN: the feasibility arm ───────────────────────────────────────
        good_plan = os.path.join(tmp, "plan.md")
        write(good_plan, GOOD_PLAN)
        r = rig("plan", good_plan)
        check("plan: a reconciled, cross-checked plan passes",
              r.returncode == 0 and "PLAN_RESULT: PASS" in r.stdout,
              "; ".join(l for l in r.stdout.splitlines() if "FAIL" in l)[:120])

        fairytale = os.path.join(tmp, "fairytale.md")
        write(fairytale, FAIRYTALE_PLAN)
        r = rig("plan", fairytale)
        failed = {l.split()[1] for l in r.stdout.splitlines()
                  if l.startswith("PLAN:") and "— FAIL" in l}
        check("plan: unsourced, unflagged drivers caught (P2)", "P2" in failed,
              str(sorted(failed)))
        check("plan: sizings an order of magnitude apart caught (P6)", "P6" in failed,
              str(sorted(failed)))
        check("plan: the divergence is quantified, not just flagged",
              "× apart" in r.stdout and "fiction" in r.stdout)
        check("plan: hockey-stick growth caught (P7)", "P7" in failed, str(sorted(failed)))
        check("plan: too few what-would-have-to-be-true conditions caught (P8)",
              "P8" in failed, str(sorted(failed)))
        check("plan: unnamed weakest condition caught (P9)", "P9" in failed,
              str(sorted(failed)))
        check("plan: the refusal explains why it matters",
              "unfalsifiable" in r.stdout)

        r = rig("plan", good_plan, "--tolerance", "1.01")
        check("plan: tightening --tolerance can fail an otherwise-passing plan",
              r.returncode == 1)

        broken = os.path.join(tmp, "broken.md")
        write(broken, GOOD_PLAN.replace("attach_rate * avg_ticket", "attach_rate * churn"))
        r = rig("plan", broken)
        check("plan: a top line that does not reconcile is named with the driver",
              "churn" in r.stdout and "P4" in r.stdout)

        # Negative fixture: a plan formula is arithmetic or it is refused.
        hostile = os.path.join(tmp, "hostile.md")
        write(hostile, GOOD_PLAN.replace(
            "- revenue = institutions * students_per_institution * attach_rate * avg_ticket",
            "- revenue = __import__('os').getcwd()"))
        r = rig("plan", hostile)
        check("plan: a formula containing a call is refused, not evaluated",
              r.returncode == 1 and "P4" in r.stdout
              and "Traceback" not in r.stderr, r.stdout[:80])

        r = rig("plan", os.path.join(tmp, "missing.md"))
        check("a missing file fails cleanly, without a traceback",
              r.returncode == 2 and "Traceback" not in r.stderr)

        r = kit(root, "status", "no-such-decision")
        check("an unknown decision fails cleanly, without a traceback",
              r.returncode == 2 and "Traceback" not in r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed, total = sum(_checks), len(_checks)
    print("EVAL_RESULT: %s (%d/%d checks)"
          % ("PASS" if passed == total else "FAIL", passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
