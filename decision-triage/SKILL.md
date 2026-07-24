---
name: decision-triage
description: >-
  Break decision paralysis with a diagnosis-first protocol instead of a menu of frameworks —
  classify the decision (reversibility, stakes, cost of reversal), diagnose which of five
  paralysis triggers is actually live (choice overload, perfectionism, regret avoidance, low
  confidence, ambiguous ownership), then run the one protocol that fits and close with a
  linted decision record. Use this whenever someone is stuck on a choice, is weighing options,
  says "should I", "torn between", "not sure whether", "leaning towards but", "need to think
  about it more", "big call", lists three or more options without criteria, or raises the same
  choice a second time — and also when they explicitly ask for help deciding. Structurally
  resists confirming a stated preference: criteria are locked before options are scored, and a
  shipped linter fails any record where the verdict precedes the criteria. Ships a decision-record
  linter and a Brier calibration scorer. Not a strategy consultant, not a substitute for domain
  expertise, and not for decisions the user has already made and is merely narrating.
license: MIT
compatibility: Requires python3 (stdlib only) and a POSIX-like shell; fully offline, no network.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "decision-making,decision-paralysis,triage,pre-mortem,mediating-assessments,calibration,decision-record"
---

# Decision Triage

Most people stuck on a decision are not short of frameworks. They are stuck for one of five
specific reasons, and each reason has a different fix — so this skill diagnoses before it
prescribes, and runs one protocol rather than offering a catalogue.

The 46 candidate methods this kernel was distilled from, and why each survivor beat its
cluster rivals, are recorded in `references/method-selection.md`. Read it when you need to
justify an inclusion or are tempted to add a method.

## The hazard this skill is built around

Frontier models endorse a user's stated position far more often than human advisors do —
measurably so, including on positions the user should be talked out of. A decision assistant
that hears the lean first will confirm the lean and dress it in a framework.

So the order of operations is the safeguard, not a request to be objective:

- Criteria are elicited and locked **before** options are scored.
- If the user opens with a preference, acknowledge it, set it aside explicitly, and return
  to it only at step 6. Say so out loud: *"Noting you're leaning toward X — I'll park that
  until we've set criteria, otherwise I'll just agree with you."*
- Scores go on one dimension at a time, across all options, before any holistic verdict.
- The rejected option gets argued at its strongest, by you, in earnest.

`assets/decision_lint.py` enforces the parts of this that are mechanically checkable, so the
constraint survives a persuasive user.

## Workflow

### 1. Triage — always, under a minute

Establish four things before anything else. `references/diagnostics.md` has the question
wording and the anti-trigger rules for decisions that only *look* live.

1. **Reversibility and cost of reversal.** What does undoing this cost in money, time and
   credibility? Most decisions are far more reversible than they feel, and naming the
   reversal cost usually deflates the stakes on its own.
2. **Whose call is it?** One named decider. Where others hold a veto, bound it to specific
   grounds. Unbounded consultation is the most common cause of organizational drift.
3. **Information problem or values problem?** If no evidence would change the answer, more
   analysis is theatre — the user is choosing between things they want, and should be told
   so plainly.
4. **Which trigger is live?** Choice overload · perfectionism · regret avoidance · low
   confidence · ambiguous ownership. This selects the branch and the emphasis.

Then check for a sunk-cost distortion: would a newcomer inheriting this position today make
the same choice? If not, the past investment is doing the arguing.

### 2. Route

- **Reversible, low cost of reversal, or a domain the user knows well → the fast path
  (step 3).** Most decisions should exit here. Running a full protocol on a two-way door is
  itself a form of paralysis.
- **Irreversible, expensive to unwind, or genuinely unfamiliar → the full path (steps 4–7).**

When it's marginal, prefer the fast path and let the tripwires catch the error.

### 3. Fast path — bound the search

Set a "good enough" bar in observable terms, set a hard deadline, take the first option that
clears the bar, and go to step 8. Name the cost of *not* deciding by the deadline; delay is a
choice with a price, and pricing it is usually what breaks the loop.

Aiming for "good enough" rather than "the best" reliably reduces decision anxiety — though be
honest that the evidence is about how people *feel* about their choices, not about outcome
quality. Sell it as a paralysis-breaker, not a route to better answers.

### 4. Full path — collapse the option set

Enumerate the binding constraints first: budget, time, regulation, reversibility, people,
prior commitments. The feasible set often collapses to two or three, and sometimes to one,
at which point the decision has dissolved rather than been made.

Then run the vanishing-options test: *"none of these are available — now what?"* It exposes
false binaries better than any amount of comparison. If it produces a materially better
option, the original set was the problem.

### 5. Full path — decompose and score

Lock three to five criteria, each independently assessable, with the opportunity cost of the
next-best forgone option as one of them. Score every option on one criterion at a time,
across all options, before moving to the next. Hold the holistic verdict until the matrix is
complete. Full protocol in `references/protocols.md`.

### 6. Full path — outside view, then adversarial pass

- **Outside view.** What happened to the last several people or teams who did roughly this?
  Base rates beat inside-view estimates, particularly on cost and time.
- **Pre-mortem.** It is twelve months on and this failed — write the story of why. Treat the
  failure as established fact rather than a possibility; the certainty framing is what makes
  this outperform ordinary risk brainstorming.
- **Steelman.** Argue the rejected option's strongest case in earnest, not as a formality.
- **Staged commitment.** Can this one-way door be split into a sequence of two-way doors —
  a pilot, a trial period, a reversible first tranche? This is the single most underused move
  on irreversible decisions.

Only now surface the user's original lean, and say whether the matrix supports it.

### 7. Full path — decide against the bar

Pick, using the completed assessments plus judgment. Averaging the scores mechanically is
not the point; informed intuition applied *after* structured assessment is.

### 8. Close — the decision record

Fill `references/decision-record-template.md`, then verify:

```sh
python3 assets/decision_lint.py <record.md>     # repair every FAIL:, rerun until LINT_RESULT: PASS
```

The linter is the evaluate step: it rejects a verdict written before the criteria, an
incomplete score matrix, prose confidence ("high") in place of a number, a missing steelman
or pre-mortem, and a review date that is not after the decision date. Repair by supplying the
missing reasoning rather than by deleting the section that failed.

### 9. Review on the date, and score yourself

At the review date, reopen the record, judge the *decision* by what was knowable at the time
rather than by how it turned out, and record the outcome. Once several records have outcomes:

```sh
python3 assets/calibration.py <records-dir>     # Brier score + over/under-confidence report
```

Without this step the record is a diary. With it, it is a calibration instrument.

## Deliverable

A **linted decision record**: the decision stated in one sentence, the named decider,
reversibility and reversal cost, the locked criteria, the full score matrix, the outside
view, the pre-mortem failure modes, the steelman of the rejected option, a numeric confidence,
tripwire conditions, and a review date — passing `assets/decision_lint.py` with
`LINT_RESULT: PASS`. On the fast path the record is short but the required fields are the same.

## Boundaries

- **Not a strategy consultant.** This skill governs the *process*; the domain judgment stays
  with the user. Where you lack the domain knowledge to score a criterion, say so rather than
  producing a confident number.
- **Not for settled decisions.** When someone is narrating a choice already made, say so and
  stop — `references/diagnostics.md` lists the anti-trigger phrasings.
- **Not a substitute for disagree-and-commit.** Once a decision is recorded, re-litigating it
  without new information is what tripwires exist to prevent.
- **Not for decisions about a person's safety or wellbeing**, or where someone appears in
  distress. Step out of the protocol and respond to the person.
- **Offer, then wait.** On auto-detection, name the decision point in one line and ask before
  running anything. A false positive that hijacks a conversation gets the skill turned off,
  and the interruption itself does most of the work.
