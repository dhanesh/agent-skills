# Spec: Pipeline stages from plan to digest (run 3)

## Problem
Run 2 (#120, merged into `factory/run2-base`) built 30 of the 36 requirements of
`docs/factory/2026-11-04-autonomous-factory-spec.md`. The plan stage (master R17) used its
two repairs and was parked. Each review found a new way for a planner to change a tool that
the tick runs later. The last gap: in the default `skills` CLI layout, each skill directory is
a symlink. The tamper baseline records only the link, so a planner can change
`write_grant.py` through the link and the tick then runs it.

Because the plan stage is not there, five stages of `pipeline tick` still print
`pending-R<n>`: plan, build, release stage, idea follow-up and digest. No A/B row measures
the behaviour that run 2 changed (master R35).

This spec is run 3. It builds master R17 to R21 and R35 on top of `factory/run2-base`. Its
R1 starts from the commits of the parked run-2 task T17 (D17).

## Users
- The repository owner (Dhanesh Purohit), who merges the run's pull request and then the
  pull request from `factory/run2-base` to `main`.
- The executors, reviewers and verifiers of this run.

## Goals
- `pipeline tick` runs every one of its nine stages, and no stage prints `pending-R<n>`.
- A planner that changes a tool file stops the tick, also when the file is under a symlink.
- Each skill whose behaviour run 2 or run 3 changed has an A/B row that moved.

## Non-goals
- Any human gate removed. Merge, production deploy, rollback, spend, external messages,
  delete, launch, pricing, the release grant, idea experiments and idea decisions stay with
  the owner.
- G3 (factory-conductor writes the PR number into the run result). It stays a follow-up.
- The stage re-judgment. It runs attended after `factory/run2-base` merges into `main`.
- Installing a timer or starting a service.
- A sandbox for the planner. The tamper checks are tripwires; the planner runs with the
  restricted tools that the pipeline grant names.

## Constraints
- B1 [invariant]: No change removes a human gate: merge, production deploy, rollback, spend, external messages, delete, launch, pricing, the release grant, idea experiments and idea decisions stay with the owner.
- B2 [goal]: Every stage, build included, runs its mechanical part with no human start, under one standing grant.
- T1 [boundary]: New code is stdlib-only Python or POSIX shell, and its tests and evals run offline inside `make gate`.
- T3 [invariant]: Every handoff between stages is a skill-contract envelope that `contract_check.py check-envelope` accepts.
- S1 [invariant]: A check command in a plan made from an intake item or an idea runs unattended only when it matches the committed command allowlist; otherwise the plan waits for the owner's read.
- S2 [invariant]: Every grant that a tick derives names its pipeline grant, and the kill switch revokes it with the pipeline grant.
- S5 [invariant]: When a planner changes a file of a tool that the tick runs, through any path that includes a symlink, the tick runs no tool of that skill again and stops.
- O1 [goal]: Each skill whose behaviour run 2 or run 3 changed has at least one A/B row, and `make ab-validate` passes.
- O2 [invariant]: Pipeline caps (plans per day, open runs, dispatches per day) stop the pipeline when one is reached.
- O3 [goal]: Each human gate that waits appears in one local digest file within one tick.

## Tensions
- TN1 [trade_off]: Stages that chain with no human start conflict with the rule that the owner reads every check command a plan from untrusted text carries. (between: B2, S1; status: resolved; strategy: Transform; decision: D3)
- TN2 [trade_off]: A planner agent with write access is needed to draft a spec, but it can change the tools that the tick runs after it. (between: B2, S5; status: resolved; strategy: Transform; decision: D18)
- TN3 [trade_off]: A release that starts with no human start conflicts with the release grant that the owner gives at `release prep`. (between: B2, B1; status: resolved; strategy: Partition; decision: D19)
- TN4 [resource_tension]: Builds that start on their own spend dispatches that no human approves one by one. (between: B2, O2; status: resolved; strategy: Prioritize; decision: D4)

## Required truths
- RT1 [SPECIFICATION_READY]: The plan stage hands on only a plan that passes lint and the allowlist, and lists every other plan as waiting for a read (parent: OUTCOME; maps_to: B2, S1, T3; reqs: R1; confidence: 0.75; check: {python} factory-pipeline/assets/test_pipeline_plan.py exits 0)
- RT2 [SPECIFICATION_READY]: A planner tamper through any path, a symlink included, stops the tick before the changed tool runs (parent: RT1; maps_to: S5, S2; reqs: R2; confidence: 0.7; check: the symlinked-skill fixture in test_pipeline_plan.py prints STOP: planner-tamper and derives no grant)
- RT3 [SPECIFICATION_READY]: Build, release stage and idea follow-up chain under derived grants inside the caps (parent: OUTCOME; maps_to: B1, B2, O2, T1, T3; reqs: R3, R4, R5; confidence: 0.7; check: the build, release and ideas suites exit 0)
- RT4 [SPECIFICATION_READY]: The owner sees every waiting human gate in one digest (parent: RT3; maps_to: O3, B1; reqs: R6; confidence: 0.85; check: {python} factory-pipeline/assets/test_pipeline_digest.py exits 0)
- RT5 [SPECIFICATION_READY]: Each changed skill has an A/B row that moved (parent: OUTCOME; maps_to: O1, T1; reqs: R7; confidence: 0.7; check: make ab-validate prints AB_RESULT: PASS)

## Solution options
- OPT-A: Continue from the parked T17 commits, and make the tamper baseline hash every tool file at its real path. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2, RT3, RT4, RT5)
- OPT-B: Write the plan stage again from the run-2 base. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1, RT2, RT3, RT4, RT5)
- OPT-C: Run the planner in a throwaway clone and copy only the spec back. (complexity: Medium; reversibility: REVERSIBLE_WITH_COST; satisfies: RT1, RT3, RT4, RT5)

Recommended: OPT-A — it satisfies every truth with the lowest complexity and keeps three reviewed rounds of work. OPT-C does not satisfy RT2: the symlinked skill directories are outside the repository, so a throwaway clone does not protect them.

## Decisions
- D1: Does the factory own live incidents? -> Detect and triage on its own; the owner mitigates, and rollback stays a never-grantable deploy (source: owner answer, 2026-11-04)
- D2: Who picks the next queue item unattended? -> A pick policy the owner registers before use; items outside it wait for the owner (source: owner answer, 2026-11-04)
- D3: How do unattended plans from untrusted items handle check commands? -> A committed command allowlist; a plan with any unlisted command waits for the owner's read (source: owner answer, 2026-11-04)
- D4: What grant shape lets stages chain? -> A standing pipeline grant, at most 7 days, from which per-plan grants derive inside caps (source: owner answer, 2026-11-04)
- D5: Where does the schedule run? -> On the owner's always-on machine with the conductor's timer kinds; no hosted scheduler (source: AC10 owner decisions, 2026-10-13)
- D6: How does the rest of the master spec reach the owner? -> Run 3 builds master R17 to R21 and R35 on `factory/run2-base` after #120 merged; then one pull request from `factory/run2-base` to `main` (source: owner answer "option 1", 2026-11-05)
- D7: May the work add a dependency? -> No. Stdlib-only Python and POSIX shell (source: CLAUDE.md house style; owner decision sweep, 2026-11-04)
- D8: May it change a public interface, schema or data format? -> Yes, by addition only: new commands, flags and digest lines. No existing field or command changes meaning (source: owner decision sweep, 2026-11-04)
- D9: May it add a service or scheduled job? -> No. The run installs no timer and starts no service (source: owner decision sweep, 2026-11-04)
- D10: Expiry and budget? -> 3 days; max_parallel 2; max_repairs_per_task 2; max_dispatches derived by the conductor; wall clock 1440 minutes (source: owner answer, 2026-11-04, kept for run 3)
- D11: Enforce a token cap? -> No. Tokens are recorded, not enforced; the dispatch cap is the cost bound (source: owner answer, 2026-11-04)
- D12: Re-entry if the session dies? -> Yes, headless claude by absolute path, every 10 minutes, stall 30, at most 5 re-entries, no permission-bypass flag (source: owner answer, 2026-11-04)
- D13: May the run consult a System One model? -> Yes, Jev, for naming and test-case selection only, with requirement text and file names only (source: owner instruction "use jev where possible", 2026-11-03)
- D14: Which branches does the grant cover? -> factory/* (source: owner decision sweep, 2026-11-04)
- D15: What may an allowlist `*` match? -> One argument of letters, digits and `_./=-`, not starting with `-` and with no `..` (source: owner answer, 2026-11-04)
- D16: Who owns `.factory/check-commands.txt`? -> The owner. A run task may read it but MUST NOT edit it (source: owner answer, 2026-11-04)
- D17: Where does R1 start? -> From the three commits of the parked run-2 task T17 (`a509e88`, `6b29c14`, `a5f46cb`) on branch `factory/a-feature-complete-factory-that-runs-every-stage-on-its-own-up-to-the-human-gates--T17`; R2 closes the symlink gap that its third review found (source: owner answer "option 1", 2026-11-05)
- D18: How does the tick stay safe from its own planner? -> A baseline taken before the planner runs, of every tool file at its real path and of every symlink target on the way; any change poisons the tick. This is a tripwire, not a sandbox (source: run-2 T17 reviews; owner answer "option 1", 2026-11-05)
- D19: Who starts `release stage`? -> The tick, only after the owner merged the release pull request and a live release grant that the owner accepted at `release prep` covers it (source: master spec TN7, 2026-11-04)

## Requirements
- R1: The plan stage must start the pipeline grant's planner on each picked envelope, and must hand the plan on only when `spec_lint.py --unattended` and `spec_to_tasks.py --allowlist` both pass; otherwise it must list the plan as waiting for the owner's read. [where: factory-pipeline/assets/pipeline.py, factory-pipeline/SKILL.md, factory-pipeline/assets/test_pipeline_plan.py] [feature: pipeline-tick] [proof: an agent runs a tick with a stub planner that drafts one plan with an unlisted command and one passing plan, and sees the first listed as waiting and the second handed to derive-grant] [parallel-safe]
- R2: The tamper baseline of the plan stage must hash each file of every skill whose tool the tick runs at its real path, and must record each symlink target on that path, so that a planner that changes a tool file or retargets a link makes the tick print `STOP: planner-tamper` and run no further tool of that skill. [where: factory-pipeline/assets/pipeline.py, factory-pipeline/assets/test_pipeline_plan.py] [feature: pipeline-tick] [proof: an agent installs spec-first-planning as a symlinked skill directory in a fixture, lets a stub planner change write_grant.py through the link, and sees STOP: planner-tamper and no derived grant; then retargets the link and sees the same stop] [after: R1]
- R3: The build stage must start `conductor init` with each grant that the tick derived, or that an earlier tick derived but never built, and must turn the evidence gate on when the repository has a verify skill. [where: factory-pipeline/assets/pipeline.py, factory-pipeline/assets/test_pipeline_build.py, docs/skill-graph/graph.json] [feature: pipeline-tick] [proof: an agent runs the build stage with and without a verify skill and sees the conductor stub get the gated and the plain init, and sees a grant left by an earlier tick built once] [after: R2]
- R4: The release stage must run `release stage` only after the release pull request that `release prep` opened is merged and a live release grant covers it. [where: factory-pipeline/assets/pipeline.py, factory-pipeline/assets/test_pipeline_release.py, release-conductor/SKILL.md, docs/skill-graph/graph.json] [feature: pipeline-tick] [proof: an agent runs ticks with the release pull request open and then merged and sees the release stage stub run once, only after the merge] [after: R3]
- R5: The idea stage must list each idea with an assumption due within 3 days or expired, and must start the plan stage for an idea whose `go` handoff envelope has no plan yet. [where: factory-pipeline/assets/pipeline.py, factory-pipeline/assets/test_pipeline_ideas.py] [feature: pipeline-tick] [proof: an agent runs a tick over fixture ideas due in 2 and 10 days and a go envelope and sees one idea listed and one plan stage started] [after: R4]
- R6: `pipeline tick` must write `.skill-contract/pipeline/digest.md` that lists each waiting human gate (pull request to merge, release grant to accept, production deploy, rollback, plan waiting for a read, idea decision) with the exact command the owner runs next. [where: factory-pipeline/assets/pipeline.py, factory-pipeline/assets/test_pipeline_digest.py] [feature: pipeline-tick] [proof: an agent runs a tick with an open run PR, a waiting release grant and a plan waiting for a read and reads all three in the digest, each with a command] [after: R5]
- R7: Each skill whose behaviour run 2 or run 3 changed (spec-first-planning, ops-intake, growth-review, factory-conductor, verifier-installer, factory-pipeline, incident-watch) must have at least one row in `scripts/ab-validate.py` labelled with its directory name, and `make ab-validate` must print `AB_RESULT: PASS`. [where: scripts/ab-validate.py] [feature: ab-validate] [proof: an agent runs make ab-validate in the task worktree and sees an IMPROVED row for each of the seven skills and AB_RESULT: PASS] [after: R6]

## Acceptance criteria
- R1: A stub planner's plan with an unlisted command is not handed on and is listed as waiting for a read; a plan that passes is handed to derive-grant. [cmd: {python} factory-pipeline/assets/test_pipeline_plan.py]
- R2: With spec-first-planning installed through a symlinked directory, a stub planner that changes `write_grant.py` through the link, and one that retargets the link, each make the tick print `STOP: planner-tamper` and derive no grant. [cmd: {python} factory-pipeline/assets/test_pipeline_plan.py]
- R3: With a verify skill, the conductor stub receives an init with the evidence gate on; without one, a plain init; a grant that an earlier tick derived and did not build is built once. [cmd: {python} factory-pipeline/assets/test_pipeline_build.py]
- R4: With an open release pull request the tick does not stage; after the stub marks it merged and a release grant covers it, the tick runs the `release stage` stub once. [cmd: {python} factory-pipeline/assets/test_pipeline_release.py]
- R5: An idea with an assumption due in 2 days is listed; an idea due in 10 days is not; a `go` envelope with no plan starts the plan stage once. [cmd: {python} factory-pipeline/assets/test_pipeline_ideas.py]
- R6: After a tick with a run PR open, a release grant waiting and a plan waiting for a read, the digest lists all three, each with a command. [cmd: {python} factory-pipeline/assets/test_pipeline_digest.py]
- R7: `make ab-validate` prints an `IMPROVED` row for each of the seven skills and `AB_RESULT: PASS`. [cmd: make ab-validate]

## Open questions

## Iterations
- I1: Cut from the master spec after run 2 (#120): master R17 to R21 and R35 kept; the symlink gap from T17's third review split out as R2; the build stage gained the derived-but-never-built grants from the run-2 carry notes; the decision sweep kept D1 to D16 and added D17 to D19.

## Verification
Verify skill: .claude/skills/verify-agent-skills
