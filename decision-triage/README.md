# decision-triage

Break decision paralysis with a **diagnosis-first protocol** rather than a menu of frameworks.
The skill classifies the decision, works out which of five paralysis triggers is actually live,
runs the one protocol that fits, and closes with a linted decision record that can later be
scored for calibration.

## Why

Two findings shaped this skill, and both cut against how decision-support tools are usually built.

**Paralysis is not a framework shortage.** People stuck on a choice are stuck for one of five
specific reasons — choice overload, perfectionism, regret avoidance, low confidence, ambiguous
ownership — each with a different fix. Handing someone a catalogue of methods produces
meta-paralysis. So this skill diagnoses first and prescribes one thing.

**An LLM decision coach is sycophantic by default.** Frontier models endorse a user's stated
position far more often than human advisors do, including on positions the user should be talked
out of — and users prefer the model that does it. A tool that hears the lean first will confirm
the lean and dress it in a framework. So the order of operations is the safeguard: criteria are
locked before options are scored, and `assets/decision_lint.py` **fails any record whose verdict
appears before its criteria**. The constraint survives a persuasive user because a script
enforces it, not a paragraph of instructions.

The kernel was distilled from 46 candidate methods across four passes (mechanism test, mechanism
clustering, chat-viability and lean-resistance, trigger coverage). Every kill decision, with
evidence grades, is recorded in `references/method-selection.md` — including the three inclusions
a reviewer should attack first.

## Install

```bash
npx skills add dhanesh/agent-skills --skill decision-triage
```

## What it ships

- `assets/decision_lint.py` — deterministic linter for a decision record: contract order
  (criteria and assessment before the verdict), complete score matrix, numeric confidence
  between 1% and 99%, a substantive pre-mortem and steelman on the full path, tripwires, and a
  review date that actually falls after the decision date.
- `assets/calibration.py` — Brier score and an over/under-confidence table across reviewed
  records. Below 0.25 beats always saying 50%; the bucket table is the more useful half, since
  it separates systematic overconfidence from noise.

Both are stdlib-only python3, offline, deterministic.

## Usage

Ask the agent for help with a decision — "should I", "torn between", "not sure whether", or a
list of options with no criteria. It runs triage in under a minute, routes to the fast path
(most decisions) or the full path, and produces a record. The tools also work by hand:

```bash
python3 assets/decision_lint.py my-decision.md          # FAIL lines + LINT_RESULT
python3 assets/calibration.py ~/decisions/              # Brier + bucket table
python3 assets/calibration.py ~/decisions/ --json       # machine handoff
```

Record skeleton: `references/decision-record-template.md`. Full method detail:
`references/protocols.md`. Triage wording, trigger signatures and anti-triggers:
`references/diagnostics.md`.

## Boundaries

Not a strategy consultant — it governs process, not domain judgment. Not for decisions already
made and merely being narrated (the anti-trigger rules are explicit about this; reopening a
settled call manufactures doubt). Not a substitute for disagree-and-commit once a record exists.
Not for decisions touching someone's safety or wellbeing, where the right move is to step out of
the protocol entirely.

On auto-detection it offers in one line and waits. A protocol that launches uninvited is how a
skill gets turned off, and the interruption does most of the work anyway.

## Tests and eval

```bash
cd decision-triage/assets && python3 test_decision_lint.py && python3 test_calibration.py
python3 decision-triage/eval/run_eval.py
make gate-skill SKILL=decision-triage   # from the repo root
```

The outcome eval is end-to-end: it lifts both record variants out of the shipped template, fills
them, and proves they pass the shipped linter — then seeds five defects (verdict-before-criteria
first among them) and requires each to be rejected, and checks the Brier arithmetic against a
hand-computed series.
