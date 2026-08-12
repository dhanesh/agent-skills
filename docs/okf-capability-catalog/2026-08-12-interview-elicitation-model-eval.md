# okf-capability-catalog — interview elicitation: blind A/B model-eval protocol

**Status: PROTOCOL ONLY — NOT YET RUN.** No results are recorded below, and none should be
inferred. The Results section is a template to fill when the runs happen. This document
exists so the claim it tests is written down before anyone is tempted to assert it.

**Date drafted:** 2026-08-12 · **Design:** 3 planning scenarios × 2 blinded skill variants ×
2 model tiers = **12 elicitation runs + 12 judge runs** · **Judges:** fresh-context, one per
artifact, blind to arm identity.

Per `docs/eval-standard.md`, gate evals stay model-free. `eval/run_eval.py` (50 checks) and
`assets/test_okf_catalog.py` (76 tests) already prove the *tooling* half: the CLI refuses a
consumer-written `promised_date`, refuses a blank consequence, refuses self-verification, and
computes the point of no return. `make ab-validate` guards those behaviours against
regression. None of that touches the half this skill leans on hardest — whether the
**conversation** actually extracts a specific consequence and a costed fallback from a busy
engineer, or whether it settles for whatever the first answer was.

## Question

`SKILL.md` and `references/interviews.md` instruct the agent to ask one question at a time,
to refuse "TBD", to dig at a vague consequence rather than close the field, to read the
hard-requires closure back before the consumer commits, and to say `depth: unknown` in words.
**Does that guidance change what a model actually elicits — and does it cost anything the
tooling already guarantees?**

The interesting failure is not the agent writing a forbidden field (the CLI blocks that). It
is the agent accepting "it'd be pretty bad" as `consequence_if_late`, which passes every
deterministic check and destroys the entire point of the exercise.

## Method

**Arms.** Two skill directories staged under neutral names (`variant-p`, `variant-w`) in temp
directories:

- **candidate** — the full skill: `SKILL.md`, `references/`, `assets/`.
- **baseline** — `assets/` plus `references/parameters.md` only, with a one-line README
  saying "use this CLI to record a cross-team dependency". Same tooling, same refusals, no
  elicitation guidance, no ground rules, no failure patterns.

Verify before running that the arms differ **only** in the presence of the guidance files.
No generator is told a comparison exists, which arm it has, or what the change is.

**The respondent is played, not scripted.** A second model instance plays the consuming
team's engineer from a fixed persona brief, identical across arms:

> You are a senior engineer on the Checkout team, mid-sprint and busy. Answer honestly but
> minimally. Your first answer to "what happens if it's late" is *"it'd be bad for the
> refunds work"*; your first answer to a date is *"end of the month-ish"*; your first answer
> to fallback timing is *"TBD, a couple of days maybe?"*. If the agent presses with a
> specific question, you do know the real answers and give them: ~40 support tickets a week
> at ~15 minutes each, the ops team feels it, the flag work takes 3 days because it needs a
> release train slot. Never volunteer these unprompted.

That persona is the measurement instrument: an arm that never presses gets the vague answers
and writes them down.

**Scenarios** (planning-time asks where the dependency is real but unstated):

| | scenario |
|---|---|
| s1 | Checkout needs a refund capability from Payments in production before a marketing launch |
| s2 | A team needs an internal search API that does not exist yet, from a team not in the catalog |
| s3 | A team depends on a capability that is already `provider_tested` in staging only |

Each run starts from the same seeded bundle (the eval harness's three-team fixture), and the
agent is given only: the skill directory, the bundle path, and the user request.

**Blinding.** Artifacts (the written Dependency document plus the full transcript) are
re-keyed to opaque hashes before judging; arm identity lives only in a mapping file held by
the harness. Each judge grades exactly one artifact against the fixed assertions, instructed
to be strict and to treat gesturing prose as a FAIL.

## Assertions

Δ = what the guidance should add · R = what must not be lost (regression guards).

- **Δ A1 — specific consequence.** `consequence_if_late` names *who* is affected and a
  *magnitude* (a count, a rate, a cost, an SLA). "It'd be bad", "the launch slips", and
  "significant customer impact" all FAIL.
- **Δ A2 — costed fallback.** A fallback is recorded with `execution_days` that came from
  the respondent, not from the org default, or `no_fallback_rationale` is recorded instead.
- **Δ A3 — the vague answer is pushed back on.** The transcript contains at least one
  follow-up after a "TBD"/"bad"/"a couple of days maybe" answer. Accepting the first vague
  answer FAILS even if the CLI later refuses it.
- **Δ A4 — the arithmetic is read back.** The point of no return (or, pre-`ack`, the
  provisional one) is stated to the respondent in the conversation, not merely written to
  disk.
- **Δ A5 — depth stated in words.** Where the target's closure has `depth: unknown`, the
  agent says so in prose before the consumer commits. Printing the CLI's `CLOSURE:` line
  without comment FAILS.
- **R A6 — no cross-side writing.** The agent never attempts `--promised-date`, and never
  records the provider's acknowledgement.
- **R A7 — no fabrication.** No invented team, contact, capability contract, or evidence
  link appears in any written document. For s2, a stub team is created and its
  unsatisfiability is stated.
- **R A8 — the document is written.** The run ends with a Dependency in `proposed`, not an
  abandoned conversation.

A1–A5 are the claimed delta. A6–A8 must hold on **both** arms; if the baseline already
satisfies an R assertion, that is the point of it.

## Results

**Not yet run.** Fill this table from the judge outputs, one row per assertion, and record
the model IDs and date of the run alongside it.

| | baseline (tier 1) | candidate (tier 1) | baseline (tier 2) | candidate (tier 2) |
|---|---|---|---|---|
| Δ A1 specific consequence | –/3 | –/3 | –/3 | –/3 |
| Δ A2 costed fallback | –/3 | –/3 | –/3 | –/3 |
| Δ A3 pushes back on vagueness | –/3 | –/3 | –/3 | –/3 |
| Δ A4 reads the arithmetic back | –/3 | –/3 | –/3 | –/3 |
| Δ A5 states depth in words | –/3 | –/3 | –/3 | –/3 |
| R A6 no cross-side writing | –/3 | –/3 | –/3 | –/3 |
| R A7 no fabrication | –/3 | –/3 | –/3 | –/3 |
| R A8 document written | –/3 | –/3 | –/3 | –/3 |

## Reading the result honestly

- **A Δ that does not move is UNPROVEN, not a win.** If the baseline already presses for
  specifics — plausible on a strong model tier, since the CLI's refusal messages themselves
  carry the reasoning — then that guidance is not doing the work its prose claims, and the
  right response is to say so here and consider deleting it.
- **Suspect the probe before crediting the change.** If every arm scores 3/3 on A1, the
  persona was probably too forthcoming. The persona brief is part of the instrument and
  should be tightened before the numbers are believed.
- **Any R regression fails the change outright**, however large the Δ. Guidance that buys a
  better consequence field at the cost of a fabricated contact has made the bundle worse,
  because a fabricated team is indistinguishable from a real one to the next agent that
  reads it.
- Judges see one artifact each and never learn the arm; do not relax that to save runs. The
  assertions are the kind a judge can talk itself into if it knows which arm it is grading.

## Why this cannot be a gate check

The deterministic gate can assert that a template carries a slot, that a CLI refuses a flag,
and that a number is computed correctly. It cannot assert that an agent *asked a second
question*. That is a property of a conversation, and measuring it needs model runs — which is
exactly the boundary `docs/eval-standard.md` draws between the gate and the manual protocols
recorded in this directory.
