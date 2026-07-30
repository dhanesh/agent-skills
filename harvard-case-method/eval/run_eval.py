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
    hand-computed values, including that confident-and-wrong scores worse.

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
