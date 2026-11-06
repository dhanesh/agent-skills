# Stage re-judgment after the autonomous-factory runs (2026-11-06)

> **Dated snapshot.** This records the two-judge verdicts that set the README stage
> statuses on 2026-11-06, on branch `docs/stage-rejudgment-2026-11-06` from `main` at
> `48a31da`. It is requirement R13 of
> [`2026-11-03-stage-coverage-spec.md`](2026-11-03-stage-coverage-spec.md), after the builds
> that R13 waits for. The first re-judgment, before those builds, is
> [`2026-11-03-stage-rejudgment.md`](2026-11-03-stage-rejudgment.md).

## What changed before the judgment

Runs 2 and 3 of the autonomous factory merged (#120, #121, #122). They added the
`factory-pipeline` and `incident-watch` skills, the pipeline grant, the command allowlist,
intake sweep and autopick, `growth auto`, and the gaps G1 and G2. The stage table still
described the skills as they were before those runs. Ten rows changed. Each claim was
checked against the code on `main` before the judgment.

- **Closed, so the limit is gone:**
  - Test / verify: verifier-installer now detects proof backends (G1).
  - Review: `conductor brief reviewer` prints the spec-anchored brief with no run (G2).
  - Release / deploy: `factory-pipeline` now starts `release stage` after your merge.
  - Operate → planning: intake is no longer "on demand only" (G4).
  - Operate / incident: `incident-watch` is built, and Q1 is decided. The owner decided
    on 2026-11-04 that the factory detects and triages, and the owner mitigates.
  - Stage handoffs: "no skill moves work from one stage to the next on its own" is gone.
- **New limits, found in the code:**
  - `[open]` G3 is still open: `factory-conductor` does not write the PR number into the
    run-result, so a squash merge does not resolve its intake item.
  - `[open]` nothing runs `incident probe` on a schedule.
  - `[open]` the pipeline sees a release merge only after a pull.
  - `[trust]` the planner tamper guard is a tripwire, not a sandbox.
  - `[trust]` the pipeline state folder, which holds the cap counts, is outside the tamper
    check.
  - `[decision]` at most one build starts per tick.
- **Tags the owner confirmed on 2026-11-06:**
  - Idea validation: "you record each result and make each decision" is `[decision]`. It
    was `[open]`.
  - Operate → planning: "rebase merges and partial runs need your `resolve`" is
    `[decision]`. It was `[open]`.
  - Build / execute: "one build per tick" is `[decision]`.

## Method

The method is the same as on 2026-11-03.

- **Blinded rows.** Each judge saw the rubric, the limit-tag rule, and each row's stage and
  skills text. The judges did not see the Status column, the earlier re-judgment, the idea
  brief or the spec.
- **Jev.** `jev-latest`, one batched call of 14 Choice questions, sent twice with the same
  payload. The owner approved the payload before it was sent. The payload is
  [`2026-11-06-stage-rejudgment-payload.json`](2026-11-06-stage-rejudgment-payload.json).
  It holds README text only, and one changed sentence about the repo: "a human orchestrates
  the stages" became "a pipeline skill can move work between stages on a timer; a human
  holds every merge, deploy, spend, external message and delete". It has no budget figures,
  no secrets and no customer data.
- **Claude.** A fresh subagent (Claude Opus 5.5) answered the same 14 questions from the
  same file. It was allowed to read each skill's SKILL.md and code to check each row's
  claims. It was not allowed to read any README, `docs/factory/`, `ideas/` or the git
  history.
- **The status** is the most conservative of the three answers (Claude, Jev run 1, Jev
  run 2), in the order `covered` < `covered, with limits` < `partly` < `missing`.

## Verdicts

| Stage | Before | Claude | Jev run 1 (conf.) | Jev run 2 (conf.) | Status now |
|---|---|---|---|---|---|
| Idea validation | covered, with limits | covered, with limits | covered, with limits (0.94) | covered, with limits (0.89) | covered, with limits |
| Requirements / spec | covered | covered | covered (0.70) | covered (0.74) | covered |
| Design / plan | covered, with limits | covered, with limits | covered, with limits (0.96) | covered, with limits (0.98) | covered, with limits |
| Build / execute | covered, with limits | covered, with limits | covered, with limits (0.95) | covered, with limits (0.94) | covered, with limits |
| Test / verify | covered, with limits | covered, with limits | covered, with limits (0.98) | covered, with limits (0.96) | covered, with limits |
| Review | covered, with limits | covered, with limits | covered, with limits (0.91) | covered, with limits (0.90) | covered, with limits |
| Release / deploy | covered, with limits | covered, with limits | covered, with limits (1.00) | covered, with limits (0.99) | covered, with limits |
| Operate → planning | covered, with limits | covered, with limits | covered, with limits (0.94) | covered, with limits (0.95) | covered, with limits |
| Operate / incident | partly | covered, with limits | covered, with limits (0.52) | covered, with limits (0.41) | covered, with limits |
| Support / feedback | covered, with limits | covered, with limits | covered, with limits (0.90) | covered, with limits (0.89) | covered, with limits |
| Growth / monetise | partly | covered, with limits | covered, with limits (0.61) | covered, with limits (0.62) | covered, with limits |
| Knowledge / docs | covered | covered | covered (0.74) | covered (0.81) | covered |
| Stage handoffs | covered, with limits | covered, with limits | covered, with limits (0.96) | covered, with limits (0.92) | covered, with limits |
| Governance | covered, with limits | covered, with limits | covered, with limits (0.92) | covered, with limits (0.92) | covered, with limits |

Jev gave the same status in both runs on 14 of 14 rows (assumption A6 of the idea brief:
the pass line is 13 or more).

## Honest limits

- **Operate / incident is close.** Jev put 0.37 and 0.46 on `partly`. The Claude judge
  said that a stricter judge could rate it `partly`, because nothing schedules
  `incident probe`, so detection is not continuous unless you schedule it. The fix is
  small: a pipeline stage or a timer for `incident probe`.
- **Growth moved on one added sentence.** On 2026-11-03 Jev split on Growth with low
  confidence. The only new text is that `growth auto` reviews on its own and the pipeline
  runs it each tick. Jev now puts 0.25 and 0.20 on `partly`. The Claude judge noted that
  the row is `covered, with limits` only because pricing and launch are owner decisions.
- **The row text leads the verdict.** The judges were blinded to the old statuses, but
  not to the wording. The author who wrote the new limits also proposed the outcome. The
  Claude judge read the code and found no false claim. It confirmed three of the new
  `[open]` items in the code (`incident.py` `OPEN_AFTER = 2`; no incident probe in
  `pipeline.py`; no PR number in the run-result). It did not check every Governance,
  Release or Support claim.
- **Claude judged Claude's text.** Jev is the only judge from a different model.
- **No row is `covered` because of the tags alone.** Every row that is not `covered` keeps
  at least one `[trust]` or `[open]` item, so the all-`[decision]` rule did not apply.
