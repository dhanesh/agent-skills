# Spec: Autonomous factory, wave 0 (prepare the repository for the build)

## Problem
`docs/factory/2026-11-04-autonomous-factory-spec.md` plans 35 changes across ops-intake,
verifier-installer, growth-review, factory-conductor, spec-first-planning and two new skills.
Before factory-conductor builds them unattended, three things are missing:

- The current behaviour of the surfaces that change has no characterisation tests, so a
  slice that breaks it could pass its own checks.
- The verify skill `.claude/skills/verify-agent-skills` has no feature-map entry for most of
  those surfaces, so the evidence gate parks their tasks as `feature-unmapped`.
- The two new skills (`factory-pipeline`, `incident-watch`) do not exist, so their tasks have
  no directory, catalog row or graph node to build on.

This spec is wave 0: the first of two runs that the owner chose (decision D4). Run 2 builds
the master spec after this run's pull request merges.

## Users
- The repository owner (Dhanesh Purohit), who reviews and merges this run's pull request.
- The executors, reviewers and verifiers of run 2, which read the tests, the feature map and
  the scaffolds that this run adds.

## Goals
- Each surface that run 2 changes has a characterisation test that passes on today's code.
- Each surface that run 2 changes has a feature-map entry that `forge lint` accepts.
- `factory-pipeline` and `incident-watch` exist as gate-passing skeletons that claim no
  command they do not have.

## Non-goals
- Any change to the behaviour of a shipped skill. That is run 2.
- The re-judgment of the stage table. It runs attended after run 2 merges, because it calls
  Jev over the network and needs the owner to accept it.
- Installing a timer. Run 2 codes the schedule; nothing is installed in either run.

## Constraints
- B1 [invariant]: Wave 0 changes no behaviour of a shipped skill: every existing test suite and eval gives the same result before and after.
- B2 [goal]: Run 2's tasks find a test, a feature entry and a skill directory for each surface they change.
- T1 [boundary]: New tests are stdlib-only Python, offline and deterministic, and run inside `make gate`.
- S1 [invariant]: A scaffolded skill claims no command or capability that it does not yet have.
- O1 [goal]: Wave 0 lands as one pull request with a passing `make gate`.

## Tensions
- TN1 [trade_off]: Scaffolding the new skills now puts two skills in the catalog before they do anything, against the rule that a skill claims nothing it lacks. (between: B2, S1; status: resolved; strategy: Transform; decision: D10)

## Required truths
- RT1 [SPECIFICATION_READY]: The current behaviour of each changing surface is pinned by a test that passes today (parent: OUTCOME; maps_to: B1, T1; reqs: R2, R3, R4; confidence: 0.8; check: make test prints no failure)
- RT2 [SPECIFICATION_READY]: The verify skill maps every surface that run 2 changes (parent: OUTCOME; maps_to: B2; reqs: R1; confidence: 0.8; check: forge lint on the verify skill prints LINT_RESULT: PASS)
- RT3 [SPECIFICATION_READY]: The two new skills exist as honest, gate-passing skeletons (parent: OUTCOME; maps_to: B2, S1, O1; reqs: R5, R6; confidence: 0.75; check: make gate prints GATE_RESULT: PASS)

## Solution options
- OPT-A: Characterisation tests as new `test_*_characterization.py` files beside each skill's suites, pinning output lines on fixtures. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2, RT3)
- OPT-B: A shared golden-output directory with a snapshot runner that every skill's tests read. (complexity: Medium; reversibility: REVERSIBLE_WITH_COST; satisfies: RT1, RT2, RT3)

Recommended: OPT-A — it satisfies every truth with the lowest complexity, and each skill keeps its own stdlib suite as the house style asks.

## Decisions
- D1: May the work add a dependency? -> No. Stdlib-only Python and POSIX shell (source: CLAUDE.md house style; owner decision sweep, 2026-11-04)
- D2: May it change a public interface, schema or data format? -> Not in wave 0. Run 2 may add the `pipeline-grant/v1` kind and new commands; existing schemas change only by adding fields (source: master spec R1; owner decision sweep, 2026-11-04)
- D3: May it add a service or scheduled job? -> It may code a schedule in run 2; neither run installs a timer (source: owner decision sweep, 2026-11-04)
- D4: How does the plan reach the owner as pull requests? -> Two runs: wave 0 first, then waves 1 to 6 after the owner merges wave 0 (source: owner answer, 2026-11-04)
- D5: Expiry and budget? -> 3 days; max_parallel 2; max_repairs_per_task 2; max_dispatches derived by the conductor; wall clock 1440 minutes (source: owner answer, 2026-11-04)
- D6: Enforce a token cap? -> No. Tokens are recorded, not enforced; the dispatch cap is the cost bound (source: owner answer, 2026-11-04)
- D7: Re-entry if the session dies? -> Yes, headless claude by absolute path, every 10 minutes, stall 30, at most 5 re-entries, no permission-bypass flag (source: owner answer, 2026-11-04)
- D8: May the run consult a System One model? -> Yes, Jev, for naming and test-case selection only, with requirement text and file names only (source: owner instruction "use jev where possible", 2026-11-03)
- D9: Which branches does the grant cover? -> factory/* (source: owner decision sweep, 2026-11-04)
- D10: How does a skeleton avoid claiming what it lacks? -> Its SKILL.md and README say "in development: no command ships yet", and its catalog row says the same; run 2 replaces that text (source: owner decision sweep, 2026-11-04)

## Requirements
- R1: The verify skill must gain feature-map entries `intake-queue`, `grant-check`, `installer-detect`, `growth-review`, `conductor-review-brief`, `factory-status-gate` and `skill-catalog`, each anchored to files that exist at this commit and listed in `features/README.md`. [where: .claude/skills/verify-agent-skills/features] [feature: forge-feature-map] [proof: forge lint on the verify skill prints LINT_RESULT: PASS and the index lists each of the seven ids] [parallel-safe]
- R2: A new `ops-intake/assets/test_intake_characterization.py` must pin the output lines of `pick`, `sync`, `resolve` and `status` on a fixture queue, and must pass on the current `intake.py`. [where: ops-intake/assets] [feature: intake-queue] [proof: an agent drives intake.py pick, sync, resolve and status on the fixture queue and sees the lines the test pins] [after: R1]
- R3: A new `verifier-installer/assets/test_detect_stack_characterization.py` must pin the detector's output lines on a Python, a Rust and a Node fixture, and must pass on the current `detect_stack.py`. [where: verifier-installer/assets] [feature: installer-detect] [proof: an agent runs detect_stack.py on each of the three fixtures and sees the lines the test pins] [after: R1]
- R4: A new `growth-review/assets/test_growth_characterization.py` must pin the review output lines for a registered plan and one export, and must pass on the current `growth.py`. [where: growth-review/assets] [feature: growth-review] [proof: an agent registers the fixture plan, runs the review on the fixture export and sees the lines the test pins] [after: R1]
- R5: `factory-pipeline` must exist as a skill skeleton made with `repo2skill/assets/scaffold_skill.py`, whose SKILL.md and README say "in development: no command ships yet", with a root README catalog row and a `docs/skill-graph/graph.json` node, and `make gate-skill SKILL=factory-pipeline` must pass. [where: factory-pipeline, README.md, docs/skill-graph/graph.json] [feature: skill-catalog] [proof: an agent runs make gate-skill for factory-pipeline and make readme and sees both pass, and reads the in-development line in its SKILL.md] [after: R1]
- R6: `incident-watch` must exist as a skill skeleton made with `repo2skill/assets/scaffold_skill.py`, whose SKILL.md and README say "in development: no command ships yet", with a root README catalog row and a `docs/skill-graph/graph.json` node, and `make gate-skill SKILL=incident-watch` must pass. [where: incident-watch, README.md, docs/skill-graph/graph.json] [feature: skill-catalog] [proof: an agent runs make gate-skill for incident-watch and make skill-graph-check and sees both pass, and reads the in-development line in its SKILL.md] [after: R5]

## Acceptance criteria
- R1: `forge lint` on the verify skill prints `LINT_RESULT: PASS`, and each of the seven ids has a file and an index line. [cmd: {python} verification-skill-forge/assets/forge.py lint .claude/skills/verify-agent-skills]
- R2: The new suite passes on the unchanged `intake.py`. [cmd: {python} ops-intake/assets/test_intake_characterization.py]
- R3: The new suite passes on the unchanged `detect_stack.py`. [cmd: {python} verifier-installer/assets/test_detect_stack_characterization.py]
- R4: The new suite passes on the unchanged `growth.py`. [cmd: {python} growth-review/assets/test_growth_characterization.py]
- R5: The skeleton passes its per-skill gate. [cmd: make gate-skill SKILL=factory-pipeline]
- R6: The skeleton passes its per-skill gate, and the call graph stays in step. [cmd: make gate-skill SKILL=incident-watch]

## Open questions

## Iterations
- I1: Split out of the master spec as run 1 of 2 (D4); the re-judgment moved to an attended follow-up; the decision sweep answered D1 to D10.

## Verification
Verify skill: .claude/skills/verify-agent-skills
