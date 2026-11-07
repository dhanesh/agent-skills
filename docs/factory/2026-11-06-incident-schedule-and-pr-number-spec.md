# Spec: Incident probe on the tick, and the PR number in the run result (run 4)

## Problem
The stage re-judgment of 2026-11-06 (#123) left two `[open]` items in the README stage table
that this repository can close with code:

- Operate / incident: nothing runs `incident probe` on a schedule. `incident-watch` detects
  a failing production only when someone runs it, so detection is not continuous. The
  judges rated this row the weakest `covered, with limits` (Jev put 0.37 and 0.46 on
  `partly`).
- Operate → planning (G3): `factory-conductor` writes the run-result envelope before it
  opens the pull request, so the run-result never names the PR. `ops-intake` resolves a
  squash-merged item only when the run-result names its PR (`pr.number`), so the owner has
  to run `resolve` by hand after every squash merge. The pipeline digest also keeps a
  squash-merged run PR until the owner deletes the local run branch.

The security audit of 2026-11-06 (run-1, source-only) found two leads that weaken the
factory's own proof and grant boundaries, and the owner chose to fix them in this run:

- `conductor verify` and the finish-time integration re-run resolve `{skill_dir:<name>}` with
  the writer's checkout as the project root. Project skill roots rank before the owner's
  installed skills, so an executor can commit `.agents/skills/<dir>/SKILL.md` that claims an
  installed skill's name and replace the checker a pinned verify step runs.
- `conductor finish --pr-cmd` runs any argv after the `open_pr` gate. `--push-cmd` is
  allowlisted, but `--pr-cmd` is not, so a re-entry agent with the required conductor allowlist
  rule can run commands its allowlist was checked to deny.

## Users
- The repository owner (Dhanesh Purohit), who merges the run's pull request and then the
  pull request from `factory/run4-base` to `main`.
- The executors, reviewers and verifiers of this run.

## Goals
- Each `pipeline tick` probes production once with the recipe's read-only checks, before
  the planner runs, and an incident it opens reaches the ops-intake queue in the same tick.
- After a run opens its pull request, the newest run-result for the plan names the PR, and
  `intake sync` resolves the item after a squash merge with no `resolve` by the owner.

## Non-goals
- Any human gate removed. Merge, production deploy, rollback, spend, external messages and
  delete stay with the owner. The incident stage never mitigates.
- A timer for `incident probe` of its own. The probe runs inside `pipeline tick`, on the
  timer that the owner installs for the tick.
- A change to what `incident probe` runs, or to when it opens a record.
- Rebase merges and partial runs. They still need the owner's `resolve` (owner decision,
  2026-11-06).
- A new re-judgment of the stage table. The two rows keep their status.

## Constraints
- B1 [invariant]: No change removes a human gate: merge, production deploy, rollback, spend, external messages and delete stay with the owner.
- B2 [goal]: Incident detection and squash-merge resolution run with no human start.
- T1 [boundary]: New code is stdlib-only Python or POSIX shell, and its tests and evals run offline inside `make gate`.
- T2 [invariant]: Every public interface, schema and data format changes by addition only: no existing field, command or output line changes meaning.
- T3 [invariant]: Every envelope that the run writes passes `contract_check.py check-envelope`.
- S1 [invariant]: The incident stage runs only `incident probe` and `intake import`; it never runs a rollback, deploy or any recipe command other than the probes.
- S2 [invariant]: Verify and finish run only the commands that the pinned plan or the grant fixes; content that a writer commits cannot replace a pinned checker or the PR command.
- O1 [goal]: Each skill whose behaviour this run changes has an A/B row that moved, and `make ab-validate` passes.
- O2 [goal]: The README stage table names no `[open]` item that this run closes, and `make factory-status` passes.

## Tensions
- TN1 [trade_off]: A probe that runs on every tick touches production with no human start, against the rule that the owner holds production. (between: B2, B1; status: resolved; strategy: Partition; decision: D1)
- TN2 [hidden_dependency]: The recipe's probe commands need the owner's PATH, but every stage after the planner runs with a private PATH. (between: B2, S1; status: resolved; strategy: Prioritize; decision: D17)
- TN3 [trade_off]: The run-result has to exist before the push and the pull request, but only the pull request gives the number. (between: B2, T2; status: resolved; strategy: Transform; decision: D18)

## Required truths
- RT1 [SPECIFICATION_READY]: Every tick probes production with the recipe's read-only checks before the planner (parent: OUTCOME; maps_to: B1, B2, S1, T1; reqs: R1; confidence: 0.85; check: {python} factory-pipeline/assets/test_pipeline_incident.py exits 0)
- RT2 [SPECIFICATION_READY]: An incident that a tick opens is in the ops-intake queue when the tick ends (parent: RT1; maps_to: B2, S1; reqs: R2; confidence: 0.8; check: the import case of test_pipeline_incident.py)
- RT3 [SPECIFICATION_READY]: After the pull request opens, the newest run-result for the plan names it (parent: OUTCOME; maps_to: B2, T2, T3; reqs: R3; confidence: 0.75; check: {python} factory-conductor/assets/test_conductor_finish_pr.py exits 0)
- RT4 [SPECIFICATION_READY]: `intake sync` resolves a squash-merged item from that run-result with no owner resolve (parent: RT3; maps_to: B2, B1; reqs: R4; confidence: 0.75; check: {python} ops-intake/assets/test_intake_squash_e2e.py exits 0)
- RT5 [SPECIFICATION_READY]: The README and the call graph say what the code does (parent: OUTCOME; maps_to: O2, T1; reqs: R5, R6; confidence: 0.85; check: make factory-status and make skill-graph-check pass)
- RT6 [SPECIFICATION_READY]: Each changed skill has an A/B row that moved (parent: OUTCOME; maps_to: O1, T1; reqs: R7; confidence: 0.75; check: make ab-validate prints AB_RESULT: PASS)
- RT7 [SPECIFICATION_READY]: A writer-committed skill cannot shadow a pinned verify tool, and finish refuses a PR command outside its allowlist (parent: OUTCOME; maps_to: S2, B1, T2; reqs: R8, R9; confidence: 0.75; check: {python} factory-conductor/assets/test_conductor_skill_shadow.py and test_conductor_pr_cmd.py exit 0)

## Solution options
- OPT-A: A new tick stage right after sweep that runs `incident probe` and imports its signal; `finish` writes a second run-result with `pr.number` that names the first, after `gh pr create` prints the PR URL. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2, RT3, RT4, RT5, RT6, RT7)
- OPT-B: A separate timer for `incident probe`; `finish` calls `gh pr view` after the PR opens and edits the first run-result in place. (complexity: Medium; reversibility: REVERSIBLE_WITH_COST; satisfies: RT1, RT2, RT4, RT5, RT6)

Recommended: OPT-A — it satisfies every truth with the lowest complexity. OPT-B edits a pinned envelope in place, which breaks the digest that `finish --retry-remote` checks, so it does not satisfy RT3 under T2 and T3, and its timer is a second schedule the owner has to install.

## Decisions
- D1: Does the factory own live incidents? -> Detect and triage on its own; the owner mitigates, and rollback stays a never-grantable deploy (source: owner answer, 2026-11-04)
- D5: Where does the schedule run? -> On the owner's always-on machine with the conductor's timer kinds; no hosted scheduler (source: AC10 owner decisions, 2026-10-13)
- D7: May the work add a dependency? -> No. Stdlib-only Python and POSIX shell (source: CLAUDE.md house style; owner decision sweep, 2026-11-04)
- D8: May it change a public interface, schema or data format? -> Yes, by addition only: a new stage line, a new optional run-result field and a new envelope. No existing field or command changes meaning (source: owner decision sweep, 2026-11-04)
- D9: May it add a service or scheduled job? -> No. The run installs no timer and starts no service (source: owner decision sweep, 2026-11-04)
- D10: Expiry and budget? -> 3 days; max_parallel 2; max_repairs_per_task 2; max_dispatches derived by the conductor; wall clock 1440 minutes (source: owner answer, 2026-11-06, same as run 3)
- D11: Enforce a token cap? -> No. Tokens are recorded, not enforced; the dispatch cap is the cost bound (source: owner answer, 2026-11-06, same as run 3)
- D12: Re-entry if the session dies? -> Yes, headless claude by absolute path, every 10 minutes, stall 30, at most 5 re-entries, no permission-bypass flag (source: owner answer, 2026-11-06, same as run 3)
- D13: May the run consult a System One model? -> Yes, Jev, for naming and test-case selection only, with requirement text and file names only (source: owner answer, 2026-11-06, same as run 3)
- D14: Which branches does the grant cover? -> factory/* (source: owner decision sweep, 2026-11-04)
- D16: Who owns `.factory/check-commands.txt`? -> The owner. A run task may read it but MUST NOT edit it (source: owner answer, 2026-11-04)
- D17: Where does the incident stage run? -> Right after sweep, as the second stage, before the planner, so the recipe's commands run with the owner's PATH; with no `.release/recipe.json` it prints a skip (source: owner answer, 2026-11-06)
- D18: How does the run-result name the PR? -> After `gh pr create` prints the PR URL, `finish` writes a second run-result with `pr: {number: n}` that names the envelope it replaces; a PR command whose output has no PR URL gives no second envelope (source: owner answer, 2026-11-06)
- D19: Does the run update the README stage rows? -> Yes. It removes the two `[open]` items it closes; the statuses stay `covered, with limits` (source: owner answer, 2026-11-06)
- D20: Which tags stay `[decision]`? -> Rebase merges and partial runs need the owner's `resolve`, and one build starts per tick (source: owner answer, 2026-11-06)
- D21: How does verify treat a {skill_dir} that resolves inside the writer's checkout? -> It fails the step with `skill-shadow` when a skill of the same frontmatter name also exists outside the checkout; a project skill with no installed twin still resolves to the checkout copy, as today (source: owner answer to the security audit, 2026-11-06)
- D22: Which PR commands may finish run? -> `gh pr create` followed only by the options the default command uses, each carrying an expanded placeholder; anything else exits 2 before any remote step, mirroring push_cmd_problem; tests use a stub `gh` on PATH (source: owner answer to the security audit, 2026-11-06)

## Requirements
- R1: `pipeline tick` must run a new stage `incident-probe` right after `sweep` that runs `incident probe` once when `.release/recipe.json` exists, and prints `STAGE: incident-probe skip no-recipe` when it does not. [where: factory-pipeline/assets/pipeline.py, factory-pipeline/SKILL.md, factory-pipeline/README.md, factory-pipeline/assets/test_pipeline_incident.py, .claude/skills/verify-agent-skills/features/pipeline-tick.md] [feature: pipeline-tick, incident-probe] [proof: an agent runs a tick with a fixture recipe whose health check passes, then one with no recipe, and sees `STAGE: incident-probe` as the second stage line with a probe result, then the skip line] [parallel-safe]
- R2: When `incident probe` opens an incident record inside a tick, the `incident-probe` stage must import the incident signal into ops-intake in the same tick, with `intake import --format intake-signals-jsonl --source incident-watch`, run as an argv subprocess. [where: factory-pipeline/assets/pipeline.py, factory-pipeline/assets/test_pipeline_incident.py] [feature: pipeline-tick, incident-probe, intake-queue] [proof: an agent runs two ticks against a fixture recipe whose health check fails, and after the second sees `INCIDENT: opened` and one incident item in `intake status`] [after: R1]
- R3: After `gh pr create` prints a pull request URL, `conductor finish` must write a second run-result envelope whose payload carries `pr: {"number": n}` and names the envelope it replaces, and `conductor status`, `conductor trail` and `finish --retry-remote` must read that newest envelope. [where: factory-conductor/assets/conductor.py, factory-conductor/assets/schemas/run-result.v1.json, factory-conductor/SKILL.md, factory-conductor/references/run-protocol.md, factory-conductor/assets/test_conductor_finish_pr.py] [feature: conductor-evidence-gate, intake-queue] [proof: an agent finishes a fixture run with a stub `gh` that prints a PR URL, and sees two run-result envelopes, the newer with `pr.number` and passing `check-envelope`; with a stub that prints no URL, one envelope] [parallel-safe]
- R4: `intake sync` must mark an item resolved when the newest run-result of its plan names a PR whose squash-merge commit a verified release holds, with no `resolve` by the owner. [where: ops-intake/assets/test_intake_squash_e2e.py, ops-intake/SKILL.md] [feature: intake-queue] [proof: an agent drives a fixture: conductor finish with a stub gh, a squash-merge commit ending `(#n)`, a verified release record, then `intake sync`, and sees the item resolved] [after: R3]
- R5: The README stage table must drop the items "nothing runs `incident probe` on a schedule" and the G3 squash-merge item, and `make factory-status` must pass with both rows still `covered, with limits`. [where: README.md] [feature: factory-status-gate] [proof: an agent reads the two rows, sees neither item, and runs make factory-status and sees FACTORY_STATUS_RESULT: PASS] [after: R2, R4]
- R6: `docs/skill-graph/graph.json` must hold an edge from `factory-pipeline` to `incident-watch` with quoted evidence, and `make skill-graph-check` must pass. [where: docs/skill-graph/graph.json, README.md, factory-pipeline/README.md, incident-watch/README.md] [feature: skill-catalog] [proof: an agent runs make skill-graph and make skill-graph-check and sees SKILL_GRAPH_RESULT: PASS and the new edge in the factory-pipeline diagram] [after: R5]
- R7: `scripts/ab-validate.py` must gain a new `SINCE_` constant and at least one row each for `factory-pipeline`, `factory-conductor` (the PR number, the skill-shadow refusal and the PR-command allowlist) and `ops-intake` that measures this run's change, and `make ab-validate` must print `AB_RESULT: PASS` with each new row `IMPROVED`. [where: scripts/ab-validate.py] [feature: ab-validate] [proof: an agent runs make ab-validate in the task worktree and sees an IMPROVED row for each of the three skills and AB_RESULT: PASS] [after: R6, R8, R9]
- R8: `conductor verify` and the finish-time integration re-run must fail a verify step with the reason `skill-shadow` when a `{skill_dir:<name>}` in it resolves to a directory inside the verify checkout while a skill with the same frontmatter name exists outside the checkout. [where: factory-conductor/assets/conductor.py, factory-conductor/SKILL.md, factory-conductor/references/run-protocol.md, factory-conductor/assets/test_conductor_skill_shadow.py] [feature: conductor-evidence-gate] [proof: an agent runs verify on a fixture task whose commit adds .agents/skills/zz/SKILL.md naming an installed skill, and sees VERIFY fail with skill-shadow; a project-only skill still passes] [parallel-safe]
- R9: `conductor finish` must exit 2 before any push or PR step when `--pr-cmd` is not `gh pr create` followed only by the allowed options, and must check it again right before the PR step runs. [where: factory-conductor/assets/conductor.py, factory-conductor/SKILL.md, factory-conductor/assets/test_conductor_pr_cmd.py, factory-conductor/assets/test_conductor_finish.py] [feature: conductor-evidence-gate] [proof: an agent runs finish with --pr-cmd '["touch","x"]' and sees exit 2 and no x, then with a stub gh on PATH and the default command and sees the PR step run] [after: R3]

## Acceptance criteria
- R1: With a passing fixture recipe the second `STAGE:` line is `incident-probe` with a probe result; with no recipe it is `STAGE: incident-probe skip no-recipe`; the stage runs no other recipe command. [cmd: {python} factory-pipeline/assets/test_pipeline_incident.py]
- R2: After two ticks with a failing fixture health check, the queue holds exactly one incident item from source `incident-watch`, and a third tick adds no second item. [cmd: {python} factory-pipeline/assets/test_pipeline_incident.py]
- R3: A stub `gh` that prints a PR URL gives a second run-result with `pr.number` that passes `check-envelope`; a stub that prints no URL gives one envelope; `finish --retry-remote` after a failed PR step writes the second envelope once. [cmd: {python} factory-conductor/assets/test_conductor_finish_pr.py]
- R4: The fixture item is `resolved` after `intake sync`, with no `resolve` command run. [cmd: {python} ops-intake/assets/test_intake_squash_e2e.py]
- R5: Neither item is in the two rows, and the gate passes. [cmd: make factory-status]
- R6: The call graph check passes with the new edge. [cmd: make skill-graph-check]
- R7: `make ab-validate` prints the new rows as `IMPROVED` and `AB_RESULT: PASS`. [cmd: make ab-validate]
- R8: A committed shadow of an installed skill fails verify with skill-shadow; an unshadowed project skill still passes. [cmd: {python} factory-conductor/assets/test_conductor_skill_shadow.py]
- R9: A non-gh or extra-option --pr-cmd exits 2 with no command run; the default command with a stub gh still opens the PR. [cmd: {python} factory-conductor/assets/test_conductor_pr_cmd.py]

## Open questions

## Iterations
- I1: Cut from the two `[open]` items of the 2026-11-06 re-judgment; the decision sweep kept D1, D5, D7 to D14 and D16 from runs 2 and 3, and added D17 to D20 (owner answers, 2026-11-06).
- I2: The owner added R8 and R9 from the 2026-11-06 security audit (run-1 leads factory-conductor/assets/conductor.py:_run_steps:skill_index-cwd-writer-checkout-shadows-installed-skill and factory-conductor/assets/conductor.py:cmd_finish:pr-cmd-unvalidated-argv); new constraint S2, truth RT7, decisions D21 and D22.

## Verification
Verify skill: .claude/skills/verify-agent-skills
