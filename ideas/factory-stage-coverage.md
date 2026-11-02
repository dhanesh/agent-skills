# Idea: Move the factory stage rows from partly to covered

## Problem
The root README's stage table marks 10 of 13 factory stages `partly`. The owner and the people who install the skills cannot tell from it what each stage does for them, or which work would close a gap. There is no written rubric. One blanket rule caps every row, and several rows describe the skills as they were on 2026-09-19. So the table cannot steer the next work. Spec: `docs/factory/2026-11-03-stage-coverage-spec.md`.

## Target user
The repository owner, who uses the stage table to choose the next factory work.

## Value proposition
A table in which each status follows a written rubric and each non-covered row names the one gap that blocks it. The owner picks the next work from the table, and the gate keeps the table true.

## Riskiest assumptions
- A1 [critical] [problem]: The table misleads today: many partly rows give a reason that is stale or misplaced at current main. (test: the owner reads the gap analysis in the spec and checks each partly row against main; metric: partly rows whose stated reason the owner confirms as stale or misplaced; pass: >= 4; kill: <= 1; by: 2026-11-10)
- A2 [critical] [solution]: The rubric and the text fixes alone, with no new code, move rows out of partly in a fresh two-judge re-judgment. (test: re-judge a draft README that has only the rubric, the Stage handoffs row and the stale-text fixes, with Claude and Jev, the owner approving the Jev payload; metric: rows that move from partly to covered or covered with limits under the more conservative judge; pass: >= 3; kill: < 1; by: 2026-11-17)
- A3 [critical] [demand]: The owner uses the per-row gaps to choose the next work. (test: count the spec gaps G1 to G4 and Q1 that the owner schedules or decides; metric: gaps scheduled or decided by the owner; pass: >= 2; kill: < 1; by: 2026-12-03)
- A4 [normal] [feasibility]: Each build gap G1 to G4 fits in one pull request of a normal size for this repo. (test: desk estimate from the code each gap touches and from earlier pull requests of the same kind; metric: largest estimated changed lines for one gap; pass: <= 600; kill: >= 1500; by: 2026-11-10)
- A5 [normal] [solution]: A status gate would have caught real drift. (test: desk replay of the README commits that changed the stage table, checking each status cell against the R5 status-value rule only, because the Limits rule is new and every old commit fails it; metric: commits whose status cell the R5 status-value rule refuses; pass: >= 1; kill: < 1; by: 2026-11-10)
- A6 [normal] [solution]: A Jev re-judgment is stable enough to set statuses. (test: send the same re-judgment payload to Jev twice; metric: stage rows with the same status in both runs, out of 14; pass: >= 13; kill: <= 10; by: 2026-11-17)

## Experiments
- The owner reads the spec's gap table and marks each partly row as confirmed stale or misplaced, or not (A1).
- Draft the rubric and text-fix README on a branch, then re-judge it with Claude and with Jev after the owner approves the Jev payload (A2).
- Re-run the same Jev payload a second time and compare the per-row statuses (A6).
- Over four weeks, the owner schedules or decides the spec's gaps, or declines them (A3).
- Desk: estimate the size of G1 to G4 from the code and from earlier pull requests (A4).
- Desk: replay the README history of the stage table against the R5 rules (A5).

## Out of scope
- Building G1 to G4 before this brief has a verdict.
- Removing any human gate: merge, deploy, spend, external messages, delete, launch and pricing.
- Deciding Q1 (live incident response). That is the owner's decision, not an experiment.
