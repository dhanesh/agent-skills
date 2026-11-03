# Stage re-judgment under the status rubric (2026-11-03)

> **Dated snapshot.** This records the two-judge verdicts that set the README stage
> statuses on 2026-11-03, on branch `docs/factory-stage-coverage` from `main` at `0be6d02`.
> It is experiment 2 (assumption A2) and experiment 3 (assumption A6) of the idea brief
> `ideas/factory-stage-coverage.md`, and requirement R13 of
> [`2026-11-03-stage-coverage-spec.md`](2026-11-03-stage-coverage-spec.md).

## What changed before the judgment

Only README text changed. No skill code changed.

- A "Status rubric" paragraph above the stage table defines five values: `covered`,
  `covered, with limits`, `partly`, `missing` and `out of scope, by design`. A status judges
  the stage's core job only. A limit that the owner chose does not lower a row.
- The sentence "Each stage row stays partly, because you still choose the next skill and
  confirm each handoff" is gone. A new Stage handoffs row judges that point.
- The Review row names factory-conductor's reviewer, and no longer says "None reviews a diff
  against the spec".
- The Governance row names the autonomy grant as the gate policy, and no longer says "No
  gate policy".
- Design / plan, Build / execute, Review, Operate / incident and Governance gained a
  "Limits:" note. The other rows already had one, or are `covered`.

## Method

- **Blinded rows.** Each judge saw the rubric and each row's stage and skills text. The
  judges did not see the Status column, the dated assessment, the idea brief or the spec,
  so earlier verdicts could not anchor them. The repo removed past scores from the Jev
  payload on 2026-10-13 for the same reason.
- **Jev.** `jev-latest`, one batched call of 14 Choice questions, sent twice with the same
  payload. The payload is [`2026-11-03-stage-rejudgment-payload.json`](2026-11-03-stage-rejudgment-payload.json).
  It holds README text only: no budget figures, no secrets and no customer data.
- **Claude.** A fresh subagent (Claude Opus 5.5) answered the same 14 questions from the same
  file. It was allowed to read the skills' SKILL.md files to check each row's claims, and
  not allowed to read any README, `docs/factory/` or `ideas/`.
- **The status** is the most conservative of the three answers (Claude, Jev run 1, Jev run 2),
  in the order `covered` < `covered, with limits` < `partly` < `missing`.

## Verdicts

| Stage | Before | Claude | Jev run 1 (conf.) | Jev run 2 (conf.) | Status now |
|---|---|---|---|---|---|
| Idea validation | partly | covered, with limits | covered, with limits (0.95) | covered, with limits (0.94) | covered, with limits |
| Requirements / spec | covered | covered | covered (0.65) | covered (0.59) | covered |
| Design / plan | partly | covered, with limits | covered, with limits (0.87) | covered, with limits (0.89) | covered, with limits |
| Build / execute | partly | covered, with limits | covered, with limits (0.98) | covered, with limits (0.98) | covered, with limits |
| Test / verify | partly | covered, with limits | covered, with limits (0.94) | covered, with limits (0.95) | covered, with limits |
| Review | partly | covered, with limits | covered, with limits (0.95) | covered, with limits (0.96) | covered, with limits |
| Release / deploy | covered, with limits | covered, with limits | covered, with limits (0.99) | covered, with limits (0.98) | covered, with limits |
| Operate → planning | partly | covered, with limits | covered, with limits (0.94) | covered, with limits (0.94) | covered, with limits |
| Operate / incident | partly | partly | partly (0.69) | partly (0.46) | partly |
| Support / feedback | partly | covered, with limits | covered, with limits (0.74) | covered, with limits (0.73) | covered, with limits |
| Growth / monetise | partly | covered, with limits | partly (0.40) | covered, with limits (0.36) | partly |
| Knowledge / docs | covered | covered | covered (0.61) | covered (0.65) | covered |
| Stage handoffs | (new) | covered, with limits | covered, with limits (0.91) | covered, with limits (0.84) | covered, with limits |
| Governance | partly | covered, with limits | covered, with limits (0.76) | covered, with limits (0.77) | covered, with limits |

## Results for the idea brief

- **A2** (critical): 8 of the 10 `partly` rows moved to `covered, with limits` under the most
  conservative judge. The pass line is 3 or more. The agent ran this experiment, and the skill
  counts only an experiment result that the human reports. The owner read this file, accepted
  the re-judgment, and recorded A2 = 8 as an experiment result on 2026-11-03. A2 passes.
- **A6** (normal): Jev gave the same status in both runs on 13 of 14 rows. The pass line is
  13 or more. Recorded as desk evidence.

## Honest limits

- **The row text leads the verdict.** The judges were blinded to the old statuses, but not
  to the wording. The same author wrote the new "Limits:" notes and proposed the outcome.
  A "Limits:" note that leaves out a real gap would raise a row. The Claude judge read the
  SKILL.md files and found no false claim, but it did not look for gaps that the rows omit.
- **Claude judged Claude's text.** The Claude judge is a fresh session of the same model
  family as the author. Jev is the only judge from a different model.
- **Growth is close.** Jev's two runs split on Growth with low confidence (0.40 and 0.36),
  and Claude said `covered, with limits`. The conservative rule keeps it at `partly`.
- **Stage handoffs.** The author drafted this row as `partly`. All three answers said
  `covered, with limits`, so the status follows the judges. That status does not say that
  unattended mode is complete: the README's "Unattended mode: partial" section still applies,
  because a human still starts each stage after the first.
- **Limits are not tagged yet.** Spec requirement R16 tags each "Limits:" item `[decision]`,
  `[trust]` or `[open]`, and makes a row whose items are all `[decision]` read `covered`. The
  judges saw untagged text, so this file judges that text. Tagging, and a re-judgment of any
  row it changes, comes with the R5 and R16 gate slice.
- **No skill changed.** These statuses describe the same skills as before. The buildable gaps
  G1 to G4 in the spec are still open, and each one stays named in its row's "Limits:".
