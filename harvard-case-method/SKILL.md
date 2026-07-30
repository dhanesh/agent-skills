---
name: harvard-case-method
description: >-
  Run a real Harvard-style business case study with an AI as the case writer and
  discussion leader. Use whenever someone wants to "analyze company X", "do a case
  study on Y", "teach me strategy through examples", "what can I learn from how Z
  did it", or wants MBA-style case practice without the MBA. Builds a
  decision-forcing case — the record as of a specific decision date, outcome
  withheld — makes the learner commit a decision in the protagonist's shoes, and
  only then reveals what happened and scores the call. Shipped tooling refuses a
  case that leaks the ending, cites no sources for its numbers, or names no
  comparator that failed, because an AI asked to "analyze a company" defaults to
  retrospective success narration, which is a post-mortem, not a case, and trains
  hindsight and survivorship bias instead of judgment.
license: MIT
compatibility: Requires python3 (stdlib only) for casekit.py — case scaffolding, hindsight lint, and the seal/commit/reveal ordering. Research quality depends on the host agent's web or document access; conversation-only environments can still run the full method with the learner supplying the source material.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "case-study,business-analysis,decision-making,hbs,mba,strategy,teaching,hindsight-bias"
---

# harvard-case-method

Teach judgment under uncertainty by putting the learner in a real decision seat.

The instinct — "analyze Stripe using the Harvard case study method" — reaches for the
right tradition and lands on the wrong artifact. Asked that, a model narrates the whole
company story with the ending in hand: the decisions that worked, the bets that paid off,
the pattern that made it succeed. That is a **post-mortem of a winner**. The HBS case
method is nearly its inverse: a case presents only what was knowable at a decision point,
**withholds the outcome**, makes you commit a recommendation in the protagonist's shoes,
and reveals what happened only afterwards ("the B-case"). The withholding *is* the
pedagogy — remove it and you are practising hindsight, not judgment. The evidence for
that reading, with sources, is in
[references/case-method-evidence.md](references/case-method-evidence.md).

This skill runs the real shape, and puts the retrospective pattern-extraction where it
belongs: in the debrief, after the learner has already committed.

## The three failure modes this skill exists to block

Each is a default an AI case study falls into, and each has a mechanical check in
[assets/casekit.py](assets/casekit.py) rather than a good intention:

- **Hindsight leak** — the A-case quietly narrates the future ("their bet on X turned out
  to be right", a 2014 fact in a 2011 case). `lint` rules L1/L2 fail the case on
  post-decision-date dates and outcome language.
- **Invented precision** — confident financials recalled from model memory with no source.
  Rule L4 fails any unsourced figure, so numbers must carry a citation or come out.
- **Survivorship** — one famous winner studied alone teaches the traits of survivors as
  if they were causes of survival. Rule L5 requires a named comparator that faced the same
  situation, and the debrief requires one that failed.

## Workflow

1. **Scope and select.** Establish what the learner will *use* the judgment for (their
   own business, an interview, a domain they're entering), then pick a company **and a
   decision moment** — not a company alone. Three tests a candidate must pass, in
   priority order:
   - **The learner must not already know the outcome.** This is the binding constraint,
     and it disqualifies most famous companies — nobody is surprised by how Netflix's
     2011 split went. Ask them directly what they already know, and pick around it:
     a company that failed, a mid-sized firm in their own sector, a decision inside a
     famous company that isn't the famous one.
   - The record shows **real disagreement at the time**, so the answer isn't obvious.
   - A **comparator** exists — someone who faced the same situation and did not survive.
     Pick it now, in the same breath, not later.
2. **Fix the decision date.** `python3 assets/casekit.py new <slug>
   --company "<Company>" --decision-date YYYY-MM-DD` scaffolds `case.md`, `reveal.md`,
   and `decision.md` under `cases/`. Everything after that date is now contraband in
   the A-case.
3. **Research and write the A-case.** Fill `case.md`: the situation, the protagonist and
   what they control, what was known (each figure cited), what was genuinely uncertain,
   the comparators, and a decision section that ends in a question. Prefer contemporaneous
   sources — reporting, filings, interviews from around the date — over retrospectives;
   retrospectives may be *cited* but not narrated. Write `reveal.md` separately: what they
   decided, what happened, what went wrong, and what was unknowable.
4. **Verify, then repair.** `casekit.py lint <slug>` grades the A-case against L0–L5 and
   names every offending line. Fix and re-lint until `LINT_RESULT: PASS`. This loop is
   not optional politeness — it is the only thing standing between a case and a story.
5. **Seal.** `casekit.py seal <slug>` hides the B-case and refuses to run while lint
   fails. Sealing is a speed bump, not encryption; the real guard is the recorded
   ordering in `state.json`.
6. **Run the discussion.** Present the A-case, then facilitate rather than answer:
   surface the competing readings, make the learner argue a position and defend it
   against the strongest counter. The moves — opening question, cold call, the
   disagreement pump, handling "just tell me the answer" — are in
   [references/facilitation-playbook.md](references/facilitation-playbook.md).
7. **Make them commit.** The learner writes `decision.md`: the call, the reasoning, the
   disconfirming evidence they weighed, and what would change their mind. `casekit.py
   commit <slug>` refuses a decision missing any of those four and records its checksum.
   No commit, no reveal — `casekit.py reveal` enforces it.
8. **Reveal and score.** `casekit.py reveal <slug>` prints the B-case. Score the learner's
   call on **process, not outcome**: did they see the real uncertainty, weigh the
   evidence that was actually available, and name a falsifier? A right call for a lucky
   reason is not a good decision, and the scoring rubric in the facilitation playbook
   says so explicitly.
9. **Debrief — extract the transferable pattern.** Only now is retrospective analysis
   safe, because the learner's own reasoning is already on record to compare against.
   Ask what generalizes, then immediately stress it: did the comparator that failed do
   the same thing? What in this outcome was luck? Close with the one lesson that
   transfers to the learner's actual context, stated as a testable claim rather than a
   maxim.

## The context boundary — write and run in separate sessions

The seal protects the *file*. It cannot protect a *context window*: an agent that just
researched the outcome in order to write `reveal.md` knows it while facilitating, and
knowing leaks through emphasis, ordering, and which option gets the follow-up question.
The tooling can enforce ordering on disk; it cannot make you forget.

So when the learner and the case-writer are not the same person, split the work across
two sessions:

- **Session A (authoring)** — steps 1–5. Research, write, lint, seal. End the session.
- **Session B (running)** — steps 6–9, started fresh. Read `case.md` and `state.json`
  only. **Do not open `reveal.sealed` until `casekit.py reveal` prints it.** A
  facilitator that has not decoded the blob genuinely does not know the ending, and the
  discussion is honest rather than performed.

When one person is both author and learner, say plainly that the commitment is on the
honour system — they can decode the blob or simply ask you — and that the exercise is
worth roughly what their discipline is worth. Prefer, in order: someone else writes the
case; a fresh session runs it; solo with a genuinely unknown outcome. Solo, one session,
famous company is the configuration where this skill adds ceremony and little else — say
so rather than running it.

## Deliverable

A case directory per study, and a debrief:

- `case.md` — the A-case, lint-clean, sourced, honest about what was unknown;
- `reveal.sealed` — the B-case, unreadable until a decision is on record;
- `decision.md` — the learner's committed call, with its disconfirming evidence and
  falsifier;
- `state.json` — the audit trail: decision date, stage, and the checksums proving the
  decision was committed before the outcome was seen;
- the **debrief** — process score, the luck attribution, and one transferable claim the
  learner can test in their own context.

Report the lint result and the stage transitions plainly. A case sealed with
`--skip-lint` is a case with known defects; say which rules it failed.

## How to behave

- **Withhold the ending, including from yourself.** Once you have researched the outcome
  you know it; the discipline is not writing it into the A-case, not hinting at it in
  facilitation, and not steering the learner toward the historical answer. The historical
  decision is one option among several, and frequently not the best one.
- **Uncertainty is the content.** "What Is Uncertain" is the section that makes the case
  hard. If you can't populate it, you have chosen a moment where the answer was obvious —
  pick a different one.
- **Cite or cut.** A number you cannot source does not belong in the case. Prefer an
  honest "the reported range was wide and I could not pin it" to a confident figure from
  memory.
- **Facilitate, don't lecture.** By default answer a question with the question behind it.
  The escape hatch: when the learner is genuinely stuck on a *fact* rather than a
  judgment, give the fact and move on — withholding data is not the same as withholding
  the outcome.
- **Be honest about what this replaces.** It reproduces the analytical loop of the case
  method and the discipline of committing before knowing. It does not reproduce the
  cohort, the credential, or a room of classmates who will disagree with you from
  experience you don't have. Say so once if the learner frames it as an MBA substitute.

## Extending this skill

Add domain playbooks under `references/` and link them from step 6 — a regulated-industry
case needs different facilitation than a consumer-growth one.
[references/facilitation-playbook.md](references/facilitation-playbook.md) is the shape to
copy. `casekit.py` flags and lint-rule semantics, including how to justify a
`--skip-lint` seal, are documented in [references/parameters.md](references/parameters.md).
