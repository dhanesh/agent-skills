# Spec: A feature-complete factory that runs every stage on its own, up to the human gates

## Problem
The software factory has a skill for each stage, but a human starts every stage after the
first. Build already runs unattended: factory-conductor takes an approved plan to an open
pull request under a grant. Every other step waits for the owner to type a command:

- Nothing sweeps the operational sources on a schedule (ops-intake is on demand only).
- Nothing picks a queue item, plans it and starts a build: the owner picks each item, approves
  each plan, reads each check command and writes one grant per plan.
- Nothing starts `release stage` after the owner merges the release pull request.
- No skill detects or triages a live incident (the Operate / incident row is `partly`).
- Growth reviews and idea follow-ups run only when the owner asks.
- The four build gaps of the 2026-11-03 stage-coverage spec (G1 to G4) are open, and the
  table has no gate that keeps it true (R5 and R16 of that spec).

So the factory is a set of tools that the owner drives, not a factory. The README says this
plainly: "Today it is a set of skills a human orchestrates, not yet autonomous."

This spec takes over the build slices of `docs/factory/2026-11-03-stage-coverage-spec.md`
(G1 to G4, the status gate and the limit tags) and adds the work that chains the stages.
It runs as two runs (D6): wave 0, in `docs/factory/2026-11-04-autonomous-factory-wave0-spec.md`,
adds characterisation tests, feature-map entries and the two skill skeletons; run 2 builds
this spec on top of it.
It does not change the idea brief `ideas/factory-stage-coverage.md`. Scheduling these slices
is evidence for that brief's assumption A3, which the owner records.

## Users
- The repository owner (Dhanesh Purohit), an indie developer who wants the factory to run
  while away and to stop only at the decisions that are the owner's.
- A person who installs a subset of the skills: each skill still works alone and attended.
- Claude and TypeSafe Jev, who re-judge the stage table after the slices land.

## Goals
- Every stage, build included, runs its mechanical part on a schedule with no human start,
  inside one standing grant that the owner accepts.
- The run stops only at the human gates: merge, production deploy, rollback, spend, external
  messages, delete, launch, pricing, the release grant, idea experiments and idea decisions.
- The owner sees every waiting human gate in one local digest.
- No check command that an agent wrote after reading untrusted text runs without a match in
  a committed allowlist or the owner's read.
- Every stage row reaches `covered`, or `covered, with limits` with no `[open]` limit.

## Non-goals
- Removing any human gate listed in the goals. The skill-contract never-grantable classes
  (`merge`, `deploy`, `spend`, `external_message`, `delete`) stay `ask` only.
- Running idea experiments, or deciding go, kill or pivot. Those stay human by design.
- Mitigating an incident on its own. The factory detects and triages; the owner rolls back.
- A hosted or remote scheduler. The pipeline runs on the owner's always-on machine, as the
  conductor's re-entry timer does today (AC10, 2026-10-13).
- `proved` or `bounded` proof backends for Python, TypeScript or Go (no candidate tool fits
  the stdlib-only, offline gate).
- Dedicated Jira, Linear or app-store readers (conversion recipes, AC10).
- Sending a notification to anyone but the owner's own machine.
- The stage re-judgment inside a run. It calls Jev over the network and needs the owner to
  accept it, so it runs attended after run 2 merges, as on 2026-11-03.

## Constraints
- B1 [invariant]: No change removes a human gate: merge, production deploy, rollback, spend, external messages, delete, launch, pricing, the release grant, idea experiments and idea decisions stay with the owner.
- B2 [goal]: Every stage, build included, runs its mechanical part with no human start, under one standing grant.
- B3 [goal]: Every stage row ends `covered`, or `covered, with limits` whose limits are tagged `[decision]` or `[trust]` only.
- B4 [invariant]: A README status never claims more than a fresh two-judge re-judgment shows.
- T1 [boundary]: New tools are stdlib-only Python or POSIX shell, and their tests and evals run offline inside `make gate`.
- T2 [boundary]: The schedule reuses the timer kinds of factory-conductor's re-entry (launchd or cron) on the owner's always-on machine.
- T3 [invariant]: Every handoff between stages is a skill-contract envelope that `contract_check.py check-envelope` accepts.
- S1 [invariant]: A check command in a plan made from an intake item or an idea runs unattended only when it matches the committed command allowlist; otherwise the plan waits for the owner's read.
- S2 [invariant]: The standing grant covers no never-grantable class, lasts at most 7 days, stays out of commits, and the kill switch revokes it and every grant derived from it.
- S3 [invariant]: The pipeline picks only queue items that match a pick policy the owner registered before the pick; a low-trust item never matches unless the policy names its format.
- S4 [invariant]: Incident probes run only the read-only commands the release recipe lists as `health`, `version_probe` and `prod_smoke`.
- O1 [goal]: Each slice lands as its own pull request with a passing `make gate` and at least one A/B row.
- O2 [invariant]: Pipeline caps (plans per day, open runs, dispatches per day) stop the pipeline when one is reached.
- O3 [goal]: Each human gate that waits appears in one local digest file within one tick.
- O4 [invariant]: At most one pipeline tick runs at a time in one repository.

## Tensions
- TN1 [trade_off]: Stages that chain with no human start conflict with the rule that the owner reads every check command a plan from untrusted text carries. (between: B2, S1; status: resolved; strategy: Transform; decision: D3)
- TN2 [trade_off]: A grant today pins one plan that the owner saw; a pipeline makes plans the owner has not seen. (between: B2, S2; status: resolved; strategy: Partition; decision: D4)
- TN3 [trade_off]: Incident response with no human start conflicts with rollback being a never-grantable `deploy`. (between: B2, B1; status: resolved; strategy: Partition; decision: D1)
- TN4 [trade_off]: The pipeline picking work conflicts with the owner choosing what the factory builds. (between: B2, S3; status: resolved; strategy: Transform; decision: D2)
- TN5 [hidden_dependency]: A scheduled tick needs a machine that is on, and the boundary allows no hosted scheduler. (between: B2, T2; status: accepted; strategy: Accept; decision: D5)
- TN6 [resource_tension]: Many plans per day spend model tokens that no human approves one by one. (between: B2, O2; status: resolved; strategy: Prioritize)
- TN7 [trade_off]: release-conductor writes the release grant only with the owner present, so a release cannot start on its own. (between: B2, B1; status: resolved; strategy: Partition)
- TN8 [hidden_dependency]: A status re-judgment is valid only after the slices that change a row have merged. (between: B3, B4; status: resolved; strategy: Prioritize)

## Tension resolutions
<!-- Extra section: ignored by spec_lint.py and spec_to_tasks.py. Each resolution with its
     propagation check (tighten, loosen or violate another constraint). -->
- TN1: the committed allowlist replaces the read for commands that match it (D3). A command
  outside it still waits. This tightens S1, and loosens no other constraint.
- TN2: the standing grant does not cover a plan. It lets `write_grant.py` derive a per-plan
  grant only for a plan that came from the pick policy, passed lint and the allowlist, and
  fits the caps (D4). Every derived grant names its standing grant, and the kill switch
  revokes both. S2 holds.
- TN3: detect and triage run on their own; the rollback command is shown in the digest and
  runs only after the owner's yes through release-conductor (D1). B1 holds.
- TN4: the owner registers the pick policy before the pick, as growth thresholds are
  registered before the data (D2). S3 holds.
- TN5: accepted. Without an always-on machine, the pipeline runs only when a session runs a
  tick by hand (D5).
- TN6: the caps in O2 come before throughput: the pipeline stops at a cap and records why.
- TN7: the owner's yes to the release grant stays at `release prep`. After the owner merges the
  release pull request, the tick starts `release stage` under that grant. B1 holds.
- TN8: the re-judgment requirement comes after every slice that changes a row.

## Required truths
- RT1 [SPECIFICATION_READY]: A standing grant exists as a checked envelope kind, and per-plan grants derive from it only inside its limits (parent: OUTCOME; maps_to: B1, B2, S2, T3; reqs: R1, R2, R3, R4; confidence: 0.75; check: {python} docs/skill-contract/conformance/run.py exits 0 with the pipeline-grant vectors)
- RT2 [SPECIFICATION_READY]: No untrusted check command runs unattended unless the committed allowlist matches it (parent: RT1; maps_to: S1, T1; reqs: R5, R6; confidence: 0.8; check: spec_to_tasks.py --allowlist exits 3 for a plan with an unlisted command)
- RT3 [SPECIFICATION_READY]: Work enters the pipeline only through sweeps and a registered pick policy (parent: OUTCOME; maps_to: B2, S3, T1; reqs: R7, R8, R9, R10, R11; confidence: 0.75; check: an autopick fixture picks only matching high-trust items)
- RT4 [SPECIFICATION_READY]: One scheduled, locked, capped tick drives every stage from sweep to an open PR and on to release staging (parent: OUTCOME; maps_to: B2, T2, T3, O2, O4; reqs: R12, R13, R14, R15, R16, R17, R18, R19, R20, R36; confidence: 0.65; check: the pipeline end-to-end fixture reaches an open-PR stub with no prompt)
- RT5 [SPECIFICATION_READY]: The owner sees every waiting human gate in one digest (parent: RT4; maps_to: O3, B1; reqs: R21; confidence: 0.85; check: the digest fixture lists each gate kind)
- RT6 [SPECIFICATION_READY]: Live incidents are detected and triaged by read-only probes, and rollback stays the owner's (parent: OUTCOME; maps_to: B1, B2, S4, T3; reqs: R22, R23, R24, R25, R26; confidence: 0.7; check: the incident fixture opens a record after 2 failed probes and runs no rollback)
- RT7 [SPECIFICATION_READY]: The remaining stage gaps G1 to G3 are closed (parent: OUTCOME; maps_to: B3, T1; reqs: R27, R28, R29; confidence: 0.8; check: make gate prints GATE_RESULT: PASS)
- RT8 [SPECIFICATION_READY]: The table stays true by gate, and its statuses come from a fresh re-judgment (parent: OUTCOME; maps_to: B3, B4, O1; reqs: R30, R31, R32, R33, R34, R35; confidence: 0.7; check: {python} scripts/gates/factory_status.py README.md exits 0)

## Solution options
- OPT-A: A new `factory-pipeline` skill whose `pipeline tick` reads one standing grant and walks the stages in order, calling each skill's own tool, and reuses factory-conductor's timer module for the schedule. (complexity: Medium; reversibility: REVERSIBLE_WITH_COST; satisfies: RT1, RT2, RT3, RT4, RT5, RT6, RT7, RT8)
- OPT-B: No central driver. Each skill gets its own timer and its own auto-handoff under per-skill standing grants. (complexity: High; reversibility: REVERSIBLE_WITH_COST; satisfies: RT1, RT2, RT3, RT6, RT7, RT8)
- OPT-C: factory-conductor grows into the pipeline driver, with intake, planning and release stages inside `conductor.py`. (complexity: High; reversibility: ONE_WAY; satisfies: RT1, RT2, RT3, RT4, RT5, RT6, RT7, RT8)

Recommended: OPT-A — it is the only Medium option that satisfies every truth. OPT-B spreads the caps, the lock and the digest over six timers, so no single place enforces O2 to O4 (RT4 and RT5 fail). OPT-C satisfies every truth, but couples the build tool to every stage and is hard to undo.

## Decisions
- D1: Does the factory own live incidents? -> Detect and triage on its own; the owner mitigates, and rollback stays a never-grantable deploy (source: owner answer, 2026-11-04)
- D2: Who picks the next queue item unattended? -> A pick policy the owner registers before use; items outside it wait for the owner (source: owner answer, 2026-11-04)
- D3: How do unattended plans from untrusted items handle check commands? -> A committed command allowlist; a plan with any unlisted command waits for the owner's read (source: owner answer, 2026-11-04)
- D4: What grant shape lets stages chain? -> A standing pipeline grant, at most 7 days, from which per-plan grants derive inside caps (source: owner answer, 2026-11-04)
- D5: Where does the schedule run? -> On the owner's always-on machine with the conductor's timer kinds; no hosted scheduler (source: AC10 owner decisions, 2026-10-13)
- D6: How does the work reach the owner as pull requests? -> Two runs: wave 0 (its own spec) first, then this spec after the owner merges wave 0 (source: owner answer, 2026-11-04)
- D7: May the work add a dependency? -> No. Stdlib-only Python and POSIX shell (source: CLAUDE.md house style; owner decision sweep, 2026-11-04)
- D8: May it change a public interface, schema or data format? -> Yes, by addition only: the new `pipeline-grant/v1` kind and new commands and flags. Existing schemas gain fields only, and no existing field or command changes meaning (source: owner decision sweep, 2026-11-04)
- D9: May it add a service or scheduled job? -> It may code `pipeline schedule install`; the run installs no timer and starts no service (source: owner decision sweep, 2026-11-04)
- D10: Expiry and budget? -> 3 days; max_parallel 2; max_repairs_per_task 2; max_dispatches derived by the conductor; wall clock 1440 minutes (source: owner answer, 2026-11-04, kept for run 2)
- D11: Enforce a token cap? -> No. Tokens are recorded, not enforced; the dispatch cap is the cost bound (source: owner answer, 2026-11-04)
- D12: Re-entry if the session dies? -> Yes, headless claude by absolute path, every 10 minutes, stall 30, at most 5 re-entries, no permission-bypass flag (source: owner answer, 2026-11-04)
- D13: May the run consult a System One model? -> Yes, Jev, for naming and test-case selection only, with requirement text and file names only (source: owner instruction "use jev where possible", 2026-11-03)
- D14: Which branches does the grant cover? -> factory/* (source: owner decision sweep, 2026-11-04)
- D15: What may an allowlist `*` match? -> One argument of letters, digits and `_./=-`, not starting with `-` and with no `..`, so a make variable cannot hold `$(shell ...)` and a Python flag cannot start a module (source: owner answer, 2026-11-04)
- D16: Who owns `.factory/check-commands.txt`? -> The owner. It is committed before run 2 from the draft the owner accepted; a run task may read it but MUST NOT edit it (source: owner answer, 2026-11-04)

## Requirements
- R1: The skill-contract SPEC must define a `pipeline-grant/v1` envelope kind with a gate policy, caps (`max_plans_per_day`, `max_open_runs`, `max_dispatches_per_day`), a pick-policy digest, an allowlist digest and an expiry of at most 7 days. [where: docs/skill-contract/SPEC.md] [feature: grant-check] [proof: an agent reads the SPEC section and sees the kind, each cap, both digests and the 7-day limit named] [parallel-safe]
- R2: `contract_check.py check-pipeline-grant` must refuse a pipeline grant that gives any never-grantable class a gate other than `ask`, expires more than 7 days after it was written, lacks a cap, or is tracked by git. [where: docs/skill-contract/reference/contract_check.py, docs/skill-contract/conformance] [after: R1] [feature: grant-check] [proof: an agent runs check-pipeline-grant on a valid grant and on four bad grants and sees one pass and four named refusals]
- R3: `write_grant.py --pipeline` must write a pipeline grant only with `--accepted-by` given, and must print `REFUSED:` with the reason for any input that R2 would refuse. [where: spec-first-planning/assets/write_grant.py] [after: R2] [feature: grant-check] [proof: an agent runs write_grant.py --pipeline without --accepted-by and with an 8-day expiry and sees exit 2 and a REFUSED: line, with no grant file written]
- R4: `write_grant.py --under-pipeline <grant>` must derive a per-plan autonomy grant that names its pipeline grant, only for a plan that came from an autopicked item or a `go` idea, passes `spec_lint.py`, passes the allowlist check and fits the caps, and must print `REFUSED:` otherwise. [where: spec-first-planning/assets/write_grant.py] [after: R3, R6, R10] [feature: grant-check] [proof: an agent derives a grant for an autopicked fixture plan and sees it name its pipeline grant, then sees REFUSED: for a hand-written plan and an unlisted command]
- R5: A committed file `.factory/check-commands.txt` must hold one allowed check-command pattern per line, where `*` matches exactly one argument made only of letters, digits and `_./=-` that does not start with `-` and holds no `..`, and a pattern that names an interpreter path or a shell must fail its lint. [where: spec-first-planning/assets, spec-first-planning/references/spec-format.md] [feature: spec-hints] [proof: an agent lints a fixture allowlist and sees `make test` pass and an interpreter path, a shell, a `$(` argument and a `-m` argument each fail] [parallel-safe]
- R6: `spec_to_tasks.py --allowlist <file>` must print `ALLOWLIST: pass <task>` or `ALLOWLIST: fail <task> <command>` for each check command, and must exit 3 when any command fails. [where: spec-first-planning/assets/spec_to_tasks.py] [after: R5] [feature: spec-hints] [proof: an agent runs spec_to_tasks.py --allowlist on two fixture specs and sees only ALLOWLIST: pass lines with exit 0, then one ALLOWLIST: fail line with exit 3]
- R7: ops-intake must register a pick policy (allowed kinds, minimum rank, allowed formats, maximum picks per day) before it is used, and an edit after registration must make the next autopick refuse until the owner registers again. [where: ops-intake/assets/intake.py, ops-intake/SKILL.md] [feature: intake-queue] [proof: an agent runs autopick before registration, after it and after a policy edit, and sees exit 2, a run, then exit 2 again] [parallel-safe]
- R8: A pick policy must never match an item with `trust: low` unless the policy names that item's format. [where: ops-intake/assets/intake.py] [after: R7] [feature: intake-queue] [proof: an agent runs autopick on a queue with a low-trust feedback item under two policies and sees it picked only by the policy that names its format]
- R9: `intake sweep` must run the sources that the intake config lists, import their output, and change no tracked file. [where: ops-intake/assets/intake.py, ops-intake/SKILL.md] [feature: intake-queue] [proof: an agent runs intake sweep in a fixture repo with two stub sources, sees their items in the queue and an empty git status] [after: R7]
- R10: `intake autopick` must pick at most the policy's daily maximum of matching items, highest rank first, write one `intake-item/v1` envelope per pick, and log each pick with `by: policy:<digest>`. [where: ops-intake/assets/intake.py] [after: R7, R8] [feature: intake-queue] [proof: an agent runs autopick with a daily maximum of 2 on 5 matching items and sees two envelopes for the top ranks and log lines by policy:]
- R11: `growth-review` must run its review on its own when the registered export file changed since the last review, and must send each breach to ops-intake as it does today. [where: growth-review/assets, growth-review/SKILL.md] [feature: growth-review] [proof: an agent runs the automatic review with a changed and an unchanged export and sees a breach reach the queue, then SKIP: export unchanged] [parallel-safe]
- R12: A new `factory-pipeline` skill must ship `pipeline tick`, which runs the stages in the order sweep, growth review, idea follow-up, autopick, plan, derive grant, build, release stage and digest, and prints one `STAGE: <name> <result>` line per stage. [where: factory-pipeline/assets/pipeline.py, factory-pipeline/SKILL.md] [after: R9, R10, R11, R36] [feature: pipeline-tick] [proof: an agent runs pipeline tick in a fixture repo with stub skills and sees nine STAGE: lines in order and exit 0]
- R13: `pipeline tick` must refuse to run, exit 2 and print `STOP: no-grant` when no live pipeline grant covers the repository. [where: factory-pipeline/assets/pipeline.py] [after: R2, R12] [feature: pipeline-tick] [proof: an agent runs a tick with no pipeline grant and with a revoked one and sees exit 2 and STOP: no-grant both times]
- R14: A second `pipeline tick` that starts while one runs in the same repository must exit 3 and print `locked`. [where: factory-pipeline/assets/pipeline.py] [feature: pipeline-tick] [proof: an agent holds the lock, starts a second tick and sees exit 3, locked, and an unchanged queue] [after: R13]
- R15: `pipeline tick` must stop with `STOP: cap <name>` when a pipeline-grant cap is reached, and start no new plan or run after it. [where: factory-pipeline/assets/pipeline.py] [feature: pipeline-tick] [proof: an agent runs a tick with max_plans_per_day 1 and two picked items and sees one plan and STOP: cap max_plans_per_day] [after: R14]
- R16: `pipeline schedule install` must install a timer at an interval of 15 to 1440 minutes, using factory-conductor's timer module, and must exit 2 for an interval outside that range. [where: factory-pipeline/assets, factory-conductor/assets/reentry_timer.py] [feature: pipeline-tick] [proof: an agent installs the schedule on a stub timer at 15, 1440, 10 and 1500 minutes and sees two installs and two exit-2 refusals] [after: R15]
- R17: The plan stage must start an agent that drafts the spec from the picked envelope, and must hand the plan on only when `spec_lint.py` and `spec_to_tasks.py --allowlist` both pass; otherwise it must list the plan in the digest as waiting for the owner's read. [where: factory-pipeline/assets/pipeline.py, factory-pipeline/SKILL.md] [feature: pipeline-tick] [proof: an agent runs the plan stage on a drafted plan with an unlisted command and sees it in the digest as waiting, then sees a passing plan handed on] [after: R4, R6, R16]
- R18: The build stage must start `conductor init` with the derived grant, and must turn the evidence gate on when the repository has a verify skill. [where: factory-pipeline/assets/pipeline.py] [feature: pipeline-tick] [proof: an agent runs the build stage with and without a verify skill and sees the conductor stub get the gated and the plain grant] [after: R4, R17]
- R19: The release stage must run `release stage` only after the release pull request that `release prep` opened is merged and a live release grant covers it. [where: factory-pipeline/assets/pipeline.py, release-conductor/SKILL.md] [feature: pipeline-tick] [proof: an agent runs ticks with the release pull request open and then merged and sees the release stage stub run once, only after the merge] [after: R18]
- R20: The idea stage must list each idea with an assumption due within 3 days or expired, and must start the plan stage for an idea whose `go` handoff envelope has no plan yet. [where: factory-pipeline/assets/pipeline.py] [feature: pipeline-tick] [proof: an agent runs a tick over fixture ideas due in 2 and 10 days and a go envelope and sees one idea listed and one plan stage started] [after: R19]
- R21: `pipeline tick` must write `.skill-contract/pipeline/digest.md` listing each waiting human gate (pull request to merge, release grant to accept, production deploy, rollback, plan waiting for a read, idea decision) with the exact command the owner runs next. [where: factory-pipeline/assets/pipeline.py] [feature: pipeline-tick] [proof: an agent runs a tick with an open run PR, a waiting release grant and a plan waiting for a read and reads all three in the digest, each with a command] [after: R20]
- R22: A new `incident-watch` skill must ship `incident probe`, which runs only the recipe's `health`, `version_probe` and `prod_smoke` commands and records each result. [where: incident-watch/assets/incident.py, incident-watch/SKILL.md] [feature: incident-probe] [proof: an agent runs incident probe on a fixture recipe whose health stub exits 0 and sees a recorded pass and no incident record] [after: R36]
- R23: `incident probe` must refuse any command that is not one of the recipe's `health`, `version_probe` or `prod_smoke` entries, and exit 2. [where: incident-watch/assets/incident.py] [after: R22] [feature: incident-probe] [proof: an agent asks the probe to run a command outside the recipe's three lists and sees exit 2 and no command run]
- R24: After 2 or more consecutive failed probes, `incident probe` must open one incident record with the probe output, the last release record and the recent default-branch CI runs, and must write one `intake-signals-jsonl` line of kind `incident`. [where: incident-watch/assets/incident.py] [feature: incident-probe] [proof: an agent runs two failing probes and sees no record after the first, one record after the second, and one incident line in the signals file] [after: R23]
- R25: An open incident record must show the recipe's rollback command word for word and the release-conductor command that asks the owner, and incident-watch must never run a rollback or deploy command. [where: incident-watch/assets/incident.py, incident-watch/SKILL.md] [feature: incident-probe] [proof: an agent reads the open record and sees the recipe's rollback argv word for word, and sees that the rollback stub was never called] [after: R24]
- R26: `incident close --by <name>` must close the record and print `NEXT: run bug-autopsy with <record path>`. [where: incident-watch/assets/incident.py] [feature: incident-probe] [proof: an agent runs incident close with and without --by and sees a NEXT: run bug-autopsy line, then exit 2] [after: R25]
- R27: factory-conductor must print a filled reviewer brief for a task of a plan envelope and a base ref without an initialised run, and exit 2 for an unknown task id (G2). [where: factory-conductor/assets/conductor.py, factory-conductor/SKILL.md] [feature: conductor-review-brief] [proof: an agent runs the brief command on a fixture plan with no run directory and reads the requirement, the verify commands and a diff command; an unknown task exits 2] [parallel-safe]
- R28: verifier-installer's stack detection must print one `BACKEND: <tool>` line for each proof backend it finds among Verus, Kani, proptest, Hypothesis, rapid and fast-check (G1). [where: verifier-installer/assets/detect_stack.py, verifier-installer/SKILL.md] [feature: installer-detect] [proof: an agent runs detect_stack.py on a Cargo fixture that names proptest and kani and sees two BACKEND: lines, and none on a fixture without them] [parallel-safe]
- R29: ops-intake must mark an item resolved when a verified release holds a squash-merge commit whose subject ends with the number of the pull request that the item's run opened (G3). [where: ops-intake/assets/intake.py] [feature: intake-queue] [proof: an agent runs resolve against fixture releases holding Add X (#42) and Add X (#43) and sees the PR-42 item resolved and the other still waiting] [after: R10, R9]
- R30: A gate script `scripts/gates/factory_status.py` must exit non-zero when a stage-table status is outside the rubric's five values, or when a row that is not `covered` has no "Limits:" text. [where: scripts/gates/factory_status.py, Makefile] [feature: factory-status-gate] [proof: an agent runs factory_status.py on README copies with a bad status and a missing Limits: note and sees exit 1 naming the row each time] [parallel-safe]
- R31: Each item in a row's "Limits:" note must carry one tag, `[decision]`, `[trust]` or `[open]`, and `factory_status.py` must exit non-zero for an untagged item and for a row whose items are all `[decision]` but whose status is not `covered`. [where: README.md, scripts/gates/factory_status.py] [after: R30] [feature: factory-status-gate] [proof: an agent runs factory_status.py on copies with an untagged limit and an all-decision row and sees exit 1, then exit 0 on the committed README]
- R32: `scripts/gates/test_gates.sh` must include one failing fixture for each refusal in R30 and R31. [where: scripts/gates/test_gates.sh] [after: R31] [feature: factory-status-gate] [proof: an agent runs test_gates.sh and reads one factory-status fixture line per refusal in its output]
- R33: Each new skill (`factory-pipeline`, `incident-watch`) must ship a README, a stdlib test suite and an `eval/run_eval.py` with negative fixtures, and appear in the root README catalog. [where: factory-pipeline, incident-watch, README.md] [feature: skill-catalog] [proof: an agent runs make readme and make gate-skill for both new skills and sees each pass] [after: R12, R22, R31]
- R34: `docs/skill-graph/graph.json` must hold an edge with quoted evidence for each new call between skills, and `make skill-graph-check` must pass. [where: docs/skill-graph/graph.json] [after: R33] [feature: skill-catalog] [proof: an agent runs make skill-graph-check and reads the new edges in the rendered README diagram]
- R35: Each change to a skill's behaviour in this spec must add at least one row to `scripts/ab-validate.py` labelled with the skill's directory name, and `make ab-validate` must print `AB_RESULT: PASS`. [where: scripts/ab-validate.py] [after: R4, R6, R10, R11, R21, R26, R27, R28, R29, R32, R34] [feature: ab-validate] [proof: an agent runs make ab-validate against the run base and sees an IMPROVED row for each changed skill and AB_RESULT: PASS]
- R36: The verify skill must gain feature-map entries `pipeline-tick` and `incident-probe`, anchored to the `factory-pipeline` and `incident-watch` skeleton files and listed in `features/README.md`, so the evidence gate can map R12 to R26; the tasks that build each command must move the anchors to the new code. [where: .claude/skills/verify-agent-skills/features] [feature: forge-feature-map] [proof: an agent runs forge lint on the verify skill and sees LINT_RESULT: PASS with pipeline-tick and incident-probe in the index] [parallel-safe]

## Acceptance criteria
- R1: The SPEC has a `pipeline-grant/v1` section that names each cap, the two digests and the 7-day limit. [cmd: grep -n "pipeline-grant/v1" docs/skill-contract/SPEC.md]
- R2: The conformance runner passes vectors that refuse a `merge: grant` policy, an 8-day expiry, a missing cap and a tracked grant file, and accepts a valid grant. [cmd: {python} docs/skill-contract/conformance/run.py]
- R3: Without `--accepted-by`, `write_grant.py --pipeline` exits 2 and writes no file; with an 8-day expiry it prints `REFUSED:` and the reason. [cmd: {python} spec-first-planning/assets/test_write_grant_pipeline.py]
- R4: For a plan from an autopicked item that passes lint and the allowlist, the derived grant names its pipeline grant; for a hand-written plan, a plan with an unlisted command, or a plan past `max_plans_per_day`, it prints `REFUSED:`. [cmd: {python} spec-first-planning/assets/test_write_grant_pipeline.py]
- R5: An allowlist line `make test` passes the lint, and the lines `/usr/bin/python3 x.py` and `sh -c make` fail it; against the pattern `make gate-skill *` the argument `SKILL=$(shell id)` does not match, and against `{python} *` the argument `-mhttp.server` does not match. [cmd: {python} spec-first-planning/assets/test_allowlist.py]
- R6: A plan whose commands all match prints only `ALLOWLIST: pass` lines and exits 0; a plan with one unlisted command prints `ALLOWLIST: fail` for it and exits 3. [cmd: {python} spec-first-planning/assets/test_allowlist.py]
- R7: An autopick before registration exits 2; after registration it runs; after an edit to the policy file it exits 2 until the owner registers again. [cmd: {python} ops-intake/assets/test_intake_policy.py]
- R8: A policy without `feedback-jsonl` never picks a `feedback-jsonl` item of any rank; a policy that names it can. [cmd: {python} ops-intake/assets/test_intake_policy.py]
- R9: In a fixture repo with two stub sources, `intake sweep` imports their items and `git status --porcelain` prints nothing. [cmd: {python} ops-intake/assets/test_intake_sweep.py]
- R10: With a daily maximum of 2 and 5 matching items, autopick writes 2 envelopes, for the two highest ranks, and each log line has `by` starting `policy:`. A second run on the same day picks none. [cmd: {python} ops-intake/assets/test_intake_policy.py]
- R11: With a changed export file the review runs and a breach reaches the intake queue; with an unchanged file it prints `SKIP: export unchanged`. [cmd: {python} growth-review/assets/test_growth_auto.py]
- R12: In a fixture repo with stub skills, one tick prints a `STAGE:` line for each of the nine stages in order and exits 0. [cmd: {python} factory-pipeline/assets/test_pipeline_tick.py]
- R13: With no pipeline grant, a tick exits 2 and prints `STOP: no-grant`; with a revoked grant it does the same. [cmd: {python} factory-pipeline/assets/test_pipeline_tick.py]
- R14: A tick started while the lock is held exits 3 and prints `locked`, and leaves the queue unchanged. [cmd: {python} factory-pipeline/assets/test_pipeline_lock.py]
- R15: With `max_plans_per_day` 1 and two picked items, the tick plans one, prints `STOP: cap max_plans_per_day` and starts no second plan. [cmd: {python} factory-pipeline/assets/test_pipeline_caps.py]
- R16: Install with 15 and 1440 succeeds on a stub timer; 10 and 1500 exit 2 and install nothing. [cmd: {python} factory-pipeline/assets/test_pipeline_schedule.py]
- R17: A drafted plan with an unlisted command is not handed on and appears in the digest as waiting for a read; a plan that passes is handed to the build stage. [cmd: {python} factory-pipeline/assets/test_pipeline_plan.py]
- R18: In a fixture repo with a verify skill, the conductor stub receives a grant whose evidence gate is on; without one, it receives the plain grant. [cmd: {python} factory-pipeline/assets/test_pipeline_build.py]
- R19: With an open release pull request the tick does not stage; after the stub marks it merged and a release grant covers it, the tick runs the `release stage` stub once. [cmd: {python} factory-pipeline/assets/test_pipeline_release.py]
- R20: An idea with an assumption due in 2 days is listed; an idea due in 10 days is not; a `go` envelope with no plan starts the plan stage once. [cmd: {python} factory-pipeline/assets/test_pipeline_ideas.py]
- R21: After a tick with a run PR open, a release grant waiting and a plan waiting for a read, the digest lists all three, each with a command. [cmd: {python} factory-pipeline/assets/test_pipeline_digest.py]
- R22: With a recipe whose health stub exits 0, `incident probe` records a pass and opens no record. [cmd: {python} incident-watch/assets/test_incident.py]
- R23: A probe asked to run a command that is not in the recipe's three lists exits 2 and runs nothing. [cmd: {python} incident-watch/assets/test_incident.py]
- R24: After one failed probe there is no record; after the second, one record exists with the probe output, and one `incident` line is in the signals file. [cmd: {python} incident-watch/assets/test_incident.py]
- R25: The open record holds the recipe's rollback argv word for word, and in the test the rollback stub is never called. [cmd: {python} incident-watch/assets/test_incident.py]
- R26: `incident close --by "Owner"` prints a `NEXT: run bug-autopsy with` line; without `--by` it exits 2. [cmd: {python} incident-watch/assets/test_incident.py]
- R27: In a fixture repo with a plan envelope and no run directory, the brief command exits 0 and prints the requirement, each verify command and a diff command against the base ref; with an unknown task it exits 2. [cmd: {python} factory-conductor/assets/test_conductor_review_brief.py]
- R28: On a fixture `Cargo.toml` that names `proptest` and `kani`, the detector prints `BACKEND: proptest` and `BACKEND: kani`; on a fixture with neither, it prints no `BACKEND:` line. [cmd: {python} verifier-installer/assets/test_detect_stack.py]
- R29: A release that holds `Add X (#42)` resolves the item whose run opened PR 42; `Add X (#43)` leaves it `planned+needs-resolve`. [cmd: {python} ops-intake/assets/test_intake_squash.py]
- R30: On a README copy with one status changed to `mostly` the script exits 1 and names the row; with "Limits:" removed from a `partly` row it exits 1. [cmd: bash scripts/gates/test_gates.sh]
- R31: On a copy with one untagged limit the script exits 1 and names the row; on a copy with an all-`[decision]` row marked `covered, with limits` it exits 1; on the committed README it exits 0. [cmd: {python} scripts/gates/factory_status.py README.md]
- R32: `test_gates.sh` passes, and its output names one factory-status fixture per refusal. [cmd: bash scripts/gates/test_gates.sh]
- R33: `make readme` passes, and `make gate-skill SKILL=factory-pipeline` and `make gate-skill SKILL=incident-watch` each print `GATE_RESULT: PASS`. [cmd: make readme]
- R34: `make skill-graph-check` exits 0 with edges from factory-pipeline to ops-intake, spec-first-planning, factory-conductor, release-conductor and growth-review, and from incident-watch to bug-autopsy and ops-intake. [cmd: make skill-graph-check]
- R35: `make ab-validate` prints an `IMPROVED` row for each changed skill and `AB_RESULT: PASS`. [cmd: make ab-validate]
- R36: `forge lint` on the verify skill prints `LINT_RESULT: PASS`, and the index lists `pipeline-tick` and `incident-probe`. [cmd: {python} verification-skill-forge/assets/forge.py lint .claude/skills/verify-agent-skills]

## Open questions

## Iterations
- I1: Light pass from the README stage table, the 2026-11-03 coverage spec and the grant action classes; four owner questions asked (incidents, picking, check commands, grant shape).
- I2: Owner answers became D1 to D4; build added to the scope at the owner's request (R18: pipeline runs turn the evidence gate on); pre-mortem added S3, S4, O2 and O4; the Q2 to Q5 of the 2026-11-03 spec closed (Q2 by the merged rubric, Q3 by R27 in factory-conductor, Q4 by R29's verified-release rule, Q5 as a non-goal).
- I3: Wave 0 split into its own spec (D6); the re-judgment (old R36) moved to an attended follow-up.
- I4: Made unattended after wave 0 merged (#119): every requirement got a feature, a proof and an independence marker; R36 adds the two new feature-map entries; R5 narrowed `*` to a safe charset (D15); the decision sweep answered D7 to D16.

## Verification
Verify skill: .claude/skills/verify-agent-skills

## Pre-mortem
<!-- Extra section: ignored by spec_lint.py and spec_to_tasks.py. -->
Imagine it is 2026-12-15 and the autonomous factory failed. Why?

- A GitHub issue held text that steered the planning agent to write a check command that read
  secrets. Defence: S1 and the allowlist (R5, R6); a command outside the list waits.
- The pipeline picked 40 feedback items in one night and spent the month's tokens. Defence:
  S3 (low trust excluded), O2 caps (R15) and the daily pick maximum (R10).
- The owner went on holiday and the standing grant kept planning for weeks. Defence: S2, a
  7-day expiry, and derived grants that die with it (R2, R4).
- Two ticks raced and planned the same item twice. Defence: O4 and the lock (R14).
- The incident probe ran a smoke test that wrote to production. Defence: S4, only the
  recipe's read-only lists (R22, R23), which release-conductor already requires to be
  read-only.
- The owner never saw that three PRs waited for a merge. Defence: O3 and the digest (R21).
- The README claimed `covered` before the code merged. Defence: B4 and the attended re-judgment, which runs after
  every slice.
