# Spec: Proof-tier gate for the software factory

## Problem
The first slice of proof-tiered verification (`docs/factory/2026-10-22-proof-tiered-verification-spec.md`)
built the proof surface: a contract in each feature-map entry, tier records that the recorder
runs itself, the mutant check, and the `[tier:]` and `[contract:]` plan hints. Run
`run-20261028T204242Z-4c4a06` proved and merged those parts (tasks T1 to T6 and T10). The
conductor does not yet read the new plan fields, so a plan can ask for `property` on a clause
and still merge on runtime evidence alone.

That run parked the gate task, T7, after three reviews. Each review found a different hole in
how the conductor reads the plan's tier fields:

1. The first draft added `init` refusals that the spec did not ask for.
2. A `clauses` value written as a string, not a list, skipped the tier check.
3. A non-string entry (`[5]`) crashed `conductor evidence` with a `TypeError`, a `[null]`
   entry printed a reject with no clause, and `"min_tier": null` turned the check off.

The plan fields are data from a file that the conductor pins but does not validate. This spec
makes the conductor validate them once, at `init`, and fail closed when it reads them. It then
builds the rest of the gate on that base.

## Users
- A human owner (Dhanesh) who approves contracts, not diffs, and reads the run report to see
  how strongly each clause is proven.
- factory-conductor, which needs a mechanical rule to hold a merge until each clause reaches
  the tier the plan asked for.
- Executor and verifier agents, which read the conductor's reject lines and the evidence gate
  documentation cold.

## Goals
- A plan with a malformed tier field never starts a run.
- A gated task merges only when every clause it names has a record at or above its tier.
- A task that changes a contract parks, so only a human-merged commit changes a contract.
- The run report states the weakest tier per feature.
- A plan with no tier field runs exactly as before.

## Non-goals
- The escape-hatch check (`forge check-proof`) and tier-backend detection. They stay in later
  slices, as in decision D7 of the first slice.
- A `model` tier, a floor on generated examples, or a new proof tool.
- Changes to the recorder (`verify_evidence.py`) or to the tier records it writes. The first
  slice owns them.
- A meaning for a `tier:` hint without a `contract:` hint. This spec refuses it (decision D3).

## Terms
<!-- Extra section: ignored by spec_lint.py and spec_to_tasks.py. -->
- **Gated plan:** a plan with a `verification` block. The evidence gate runs for its tasks.
- **Tiered plan:** a gated plan in which at least one task carries `min_tier`.
- **Tiered task:** a task that carries `min_tier` and `clauses`.
- **Clause reference:** a `clauses` entry, `<feature id>:C<n>`, for example `notes-create:C2`.
- **Run base:** `git merge-base <run branch> <verified head>` for the task being checked.

## Constraints
- T1 [invariant]: `conductor evidence` and `conductor merge` read `min_tier` and `clauses` only from the pinned plan, never from `state.json`, and never end with a traceback.
- T2 [invariant]: In a tiered plan, the `## Contract` text of every feature file under the verify skill is the same at a task's verified head as at its run base.
- T3 [boundary]: Shipped tooling stays Python 3.10+ stdlib-only and offline.
- T4 [invariant]: `conductor init` refuses a plan whose tier fields are malformed, and writes no run state and creates no branch when it does.
- T5 [boundary]: After every task, the per-skill gate (`make gate-skill`) of each skill the task changed passes.
- S1 [invariant]: A proof record counts only when the verifier that recorded it is the task's verifier and is not the task's owner.
- U1 [boundary]: The run report states the weakest tier per feature in one line a human reads without opening files.
- O1 [boundary]: A plan with no tier field behaves exactly as it does today: no new refusal, reject, output line or envelope key.

## Tensions
- TN1 [trade_off]: Refusing malformed tier fields at `init` adds refusals to a command whose refusals the first slice did not change, and the first review of the old gate task rejected uninvited ones. (between: T4, O1; status: resolved; strategy: Prioritize; decision: D1)
- TN2 [hidden_dependency]: The lint accepts a `tier:` hint without a `contract:` hint, and the conductor cannot check such a task, so the two skills disagree on what a valid plan is. (between: T4, T1; status: resolved; strategy: Transform; decision: D3)
- TN3 [hidden_dependency]: An ungated task in a tiered plan can merge a contract edit, and every later tiered task would then park for an edit it did not make. (between: T2, O1; status: resolved; strategy: Transform; decision: D5)

## Required truths
- RT1 [SPECIFICATION_READY]: A plan with a malformed `min_tier` or `clauses`, one of the two alone, or tier fields with no evidence gate, never reaches run state, and the planner refuses the same plans. (parent: OUTCOME; maps_to: T4, O1; reqs: R1, R2; confidence: 0.85; check: {python} factory-conductor/assets/test_conductor_plan_tiers.py)
- RT2 [SPECIFICATION_READY]: The conductor refuses a merge below the required tier, counts only checked records by the task's verifier, and never crashes on a tier field. (parent: OUTCOME; maps_to: T1, S1, O1; reqs: R3; confidence: 0.75; check: {python} factory-conductor/assets/test_conductor_tiers.py)
- RT3 [SPECIFICATION_READY]: The conductor parks any task in a tiered plan whose head changes a contract. (parent: OUTCOME; maps_to: T2; reqs: R4; confidence: 0.8; check: {python} factory-conductor/assets/test_conductor_contract.py)
- RT4 [SPECIFICATION_READY]: `conductor status` and the run result report the weakest tier per feature, and only for a tiered plan. (parent: OUTCOME; maps_to: U1, O1; reqs: R5; confidence: 0.8; check: {python} factory-conductor/assets/test_conductor_assurance.py)
- RT5 [SPECIFICATION_READY]: The gate is documented and guarded by A/B rows, and every touched skill's gate stays green. (parent: OUTCOME; maps_to: T3, T5, O1; reqs: R6, R7; confidence: 0.75; check: make ab-validate)

## Requirements
- R1: `conductor init` must refuse a plan, after it reads the plan's tasks and before it checks verify commands and the grant, when a task matches one of these four cases, and these must be its only new refusals: (1) a `min_tier` key whose value is not one of the exact strings proved, bounded, property or runtime, null included; (2) a `clauses` key whose value is not a non-empty list of distinct strings that each match `[a-z][a-z0-9]*(-[a-z0-9]+)*:C[1-9][0-9]*` in full and whose feature part is in the task's `features`, null included; (3) exactly one of the two keys; (4) either key in a plan with no `verification` block. Each refusal must print `FAIL: task <id> <field>: <why>` to stderr (for case 3, the missing key), exit 2, and create no run directory and no branch, and `init` must read no feature file for this check. [where: factory-conductor/assets/conductor.py, factory-conductor/assets/test_conductor_plan_tiers.py, .claude/skills/verify-agent-skills/features/conductor-evidence-gate.md, .claude/skills/verify-agent-skills/scripts/drive.py] [feature: conductor-evidence-gate] [proof: conductor init on fixture plans for each case (min_tier null, min_tier fast, clauses null, clauses a string, clauses [5], clauses [null], clauses [], a duplicate clause, notes-create:C0, a clause whose feature the task does not name, min_tier alone, clauses alone, and tier fields with no verification block) each exits 2 with a FAIL line naming the task and the field and leaves no run directory and no new branch; a plan with valid fields and a plan with neither each start a run; the verify skill's conductor-evidence-gate recipe documents how to drive and read an init refusal] [parallel-safe]
- R2: `spec_lint.py` must fail a requirement that carries a `tier:` hint without a `contract:` hint and a `contract:` entry that does not match `<feature id>:C[1-9][0-9]*` in full, naming the requirement id, `spec_to_tasks.py` and `task-plan.v1.json` must accept only the same clause form, and the test that asserts a lone `tier:` hint lints clean must be replaced by one that asserts it fails. [where: spec-first-planning/assets/spec_lint.py, spec-first-planning/assets/spec_to_tasks.py, spec-first-planning/assets/schemas/task-plan.v1.json, spec-first-planning/references/spec-format.md, spec-first-planning/assets/test_spec_tiers.py, .claude/skills/verify-agent-skills/features/spec-hints.md] [feature: spec-hints] [proof: spec_lint.py on a fixture spec whose requirement has tier property and no contract hint prints LINT_RESULT: FAIL with that requirement id, and on contract notes-create:C0 the same; the spec with contract notes-create:C1 added lints PASS; the verify skill's spec-hints recipe drives the lone-tier case] [parallel-safe]
- R3: `evidence_verdict` must, for a tiered task and only after every existing check would pass, return `tier-below-required <feature>:C<n>` for the first clause, in `clauses` order, that is not met at `min_tier`, where a clause `<f>:C<n>` is met at tier X when a file `<evidence dir>/*/<f>/<verified head>/proof-<tier>-<tool>-C<n>.json` passes the existing record check for the task's verifier, its `tier` field equals the tier in its file name and is at or above X in the order proved > bounded > property > runtime, its `tool` is in that tier's allowlist, its `clauses` is `["C<n>"]`, and for bounded and property it holds a non-zero mutant exit status and its mutant artifacts, or when X is runtime and the accepted `evidence.json` for `<f>` exists; it must read `min_tier` and `clauses` only from the pinned plan and, when a value there is unreadable, return `tier-field-unreadable <min_tier|clauses>`, which parks the task, never raising; and `tier-below-required` must take the `evidence-failed` route: a repair, `resume` printing `NEXT: <task> dispatch-repair evidence`, and `verify` refusing the commit that was rejected. [where: factory-conductor/assets/conductor.py, factory-conductor/assets/test_conductor_tiers.py, factory-conductor/assets/test_conductor_evidence.py, .claude/skills/verify-agent-skills/features/conductor-evidence-gate.md, .claude/skills/verify-agent-skills/scripts/drive.py] [feature: conductor-evidence-gate] [proof: a gated fixture run whose task needs property on notes-create:C1 and notes-create:C2, with a property record for C1 and only runtime evidence for C2, prints EVIDENCE: T1 reject tier-below-required notes-create:C2, exits 3, and resume prints NEXT: T1 dispatch-repair evidence; with property records for both it passes; a record written by the owner and a record whose stdout was edited each fail to count; the verify skill's recipe documents how to record a property record in the fixture with an allowlisted fake tool and a mutant] [after: R1]
- R4: For every task in a tiered plan, after the review and grant checks and before `evidence_verdict`, `conductor evidence` must compare the `## Contract` section, as `verification-skill-forge/references/feature-map.md` defines it, of each `<verification skill>/features/<id>.md` other than `README.md` between the run base and the verified head, count a file or section that exists on one side only as changed, and on a difference print `EVIDENCE: <task> reject contract-changed <first changed feature id in sorted order>`, exit 3 and park the task with that reason. [where: factory-conductor/assets/conductor.py, factory-conductor/assets/test_conductor_contract.py, .claude/skills/verify-agent-skills/features/conductor-evidence-gate.md] [feature: conductor-evidence-gate] [proof: a tiered fixture run whose task edits the text of C1 in features/notes-create.md prints EVIDENCE: T1 reject contract-changed notes-create and the task status becomes parked even though every record passes; an ungated task in the same tiered plan that makes the same edit also parks; the same edit in a plan with no min_tier does not reject] [after: R3]
- R5: When at least one task carries `clauses`, `conductor status` must print, after its last `STATUS:` line, one `ASSURANCE: <feature> <tier> <clause ids>` line per feature named in any task's `clauses`, in feature-id order, where a clause's tier is the strongest tier recorded at pass for it by a merged task that names it, else `none`, the feature's tier is its weakest clause's tier in the order proved > bounded > property > runtime > none, and the clause ids are those at that tier in numeric order joined by commas; the conductor must store the tier each clause met in the task's evidence record at pass; the run-result envelope must carry the same data as `assurance: [{feature, tier, clauses}]` only in that case; and `run-result.v1.json` must declare `assurance` under `properties`. [where: factory-conductor/assets/conductor.py, factory-conductor/assets/schemas/run-result.v1.json, factory-conductor/assets/test_conductor_assurance.py, .claude/skills/verify-agent-skills/features/conductor-evidence-gate.md, .claude/skills/verify-agent-skills/scripts/drive.py] [feature: conductor-evidence-gate] [proof: a finished tiered fixture run with C1 at proved and C2 at property prints ASSURANCE: notes-create property C2, and its run-result envelope carries the same item; a run with C2 and C10 at property prints them as C2,C10; a finished plan with no tier field prints no ASSURANCE line and its envelope has no assurance key; the verify skill's recipe documents how to drive a gated run through finish] [after: R4]
- R6: factory-conductor's SKILL.md and `references/run-protocol.md` must list `tier-below-required` (a repair), `tier-field-unreadable` and `contract-changed` (each parks) in step 7, the evidence gate list, "What parks a task", the run protocol's check table and its `NEXT:` table, and the four `init` refusals of R1; the verifier brief must gain `<min_tier>` and `<clauses>` slots and tell the verifier to run `verify_evidence.py record --tier <tier> --tool <tool> --clauses C<n> --cmd <argv>` once per clause; every new BCP 14 keyword sentence must be registered in `docs/rfc2119/2026-09-19-classification.md`; and `CONDUCTOR_VERSION` must move from 1.5.4 to 1.6.0. [where: factory-conductor/SKILL.md, factory-conductor/references/run-protocol.md, factory-conductor/assets/conductor.py, docs/rfc2119/2026-09-19-classification.md] [feature: conductor-evidence-gate] [proof: each of the three new reasons appears with its outcome in SKILL.md's step 7, evidence gate list and What parks a task, and in run-protocol.md's check table; the verifier brief names the record command for tiers with the clause slot; conductor.py prints version 1.6.0] [after: R5]
- R7: `scripts/ab-validate.py` must carry a `SINCE_PROOF_TIER_GATE` constant set to the first line of `git rev-list --first-parent --reverse <run base>..HEAD` (repinned if that commit's sha changes on the way into main) and these rows: IMPROVED, the `tier-below-required` reject count for a fixture with C2 at runtime only, 0 on the baseline and 1 on the new tree; IMPROVED, the exit of `init` on a plan with `min_tier: null` and clauses, 0 then 2; IMPROVED, the `contract-changed` reject count for a fixture that edits C1, 0 then 1; IMPROVED, the lint result for a lone `tier:` hint, PASS then FAIL; and HELD, the triple (evidence verdict, merge exit, `assurance` key present) for a plan with no tier field. [where: scripts/ab-validate.py, .claude/skills/verify-agent-skills/features/ab-validate.md] [feature: ab-validate] [proof: make ab-validate run in the task worktree prints the four new rows as IMPROVED and the ungated-plan row as HELD, with no WORSE or UNPROVEN row] [after: R4, R5, R2]

## Acceptance criteria
- R1: `conductor init` on each of the thirteen fixture plans in R1's proof exits 2, prints a `FAIL: task T1 <field>:` line, and leaves no run directory and no new branch; a plan with valid fields and a plan with neither field each start a run. [cmd: {python} factory-conductor/assets/test_conductor_plan_tiers.py]
- R1: factory-conductor's per-skill gate still passes. [cmd: make gate-skill SKILL=factory-conductor]
- R2: A requirement with a `tier:` hint and no `contract:` hint, and one with `contract: notes-create:C0`, each fail lint with the requirement id, and `contract: notes-create:C1` makes it pass; the old lone-tier test is replaced. [cmd: {python} spec-first-planning/assets/test_spec_tiers.py]
- R2: spec-first-planning's per-skill gate still passes. [cmd: make gate-skill SKILL=spec-first-planning]
- R3: A tiered task that needs `property` on C1 and C2, with a property record for C1 and only runtime evidence for C2, prints `EVIDENCE: T1 reject tier-below-required notes-create:C2`, exits 3, and `resume` prints `NEXT: T1 dispatch-repair evidence`; with property records for both it passes; a record written by the owner and a record with edited stdout each fail to count; a pinned plan whose `clauses` is `[5]`, `[null]` or `"x"`, or whose `min_tier` is null, built by hand so that it bypasses `init`, prints `EVIDENCE: T1 reject tier-field-unreadable <field>`, parks, and writes no traceback; the route test covers all three new reasons. [cmd: {python} factory-conductor/assets/test_conductor_tiers.py]
- R3: factory-conductor's per-skill gate still passes. [cmd: make gate-skill SKILL=factory-conductor]
- R4: A task branch that edits C1's text in `features/notes-create.md` prints `EVIDENCE: T1 reject contract-changed notes-create` and parks, even when every record passes; an ungated task in the same tiered plan that makes the same edit also parks; the same edit in a plan with no `min_tier` does not reject. [cmd: {python} factory-conductor/assets/test_conductor_contract.py]
- R4: factory-conductor's per-skill gate still passes. [cmd: make gate-skill SKILL=factory-conductor]
- R5: A finished tiered run with C1 at `proved` and C2 at `property` prints `ASSURANCE: notes-create property C2`; C2 and C10 print as `C2,C10`; a clause with no record reports `none`; the envelope's `assurance` items equal the printed lines; a plan with no tier field prints no `ASSURANCE:` line and has no `assurance` key. [cmd: {python} factory-conductor/assets/test_conductor_assurance.py]
- R5: factory-conductor's per-skill gate still passes. [cmd: make gate-skill SKILL=factory-conductor]
- R6: The BCP 14 register check passes with the new keyword sentences. [cmd: make bcp14]
- R6: factory-conductor's per-skill gate still passes with the new documentation. [cmd: make gate-skill SKILL=factory-conductor]
- R7: The A/B validation reports the four new rows as IMPROVED and the ungated-plan row as HELD, with no row WORSE or UNPROVEN. [cmd: make ab-validate]

## Verification
Verify skill: .claude/skills/verify-agent-skills

## Solution options
- OPT-A: Validate the tier fields once at `init`, read them only from the pinned plan, and make `evidence` fail closed on any field it cannot read. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2, RT3, RT4, RT5)
- OPT-B: Validate only in `evidence`, and reject a malformed field there as `tier-below-required`. (complexity: Low; reversibility: TWO_WAY; satisfies: RT2, RT3, RT4, RT5)
- OPT-C: Validate the plan against a stricter `task-plan/v1` schema in `contract_check.py`. (complexity: Medium; reversibility: ONE_WAY; satisfies: RT1, RT2, RT3, RT4, RT5)

Recommended: OPT-A — it satisfies every required truth at low complexity. OPT-B sends a plan defect to the executor as a repair, but no executor can fix a pinned plan, so the task burns its repair budget. OPT-C changes a shared schema that every consumer reads.

## Decisions
- D1: Where are malformed tier fields refused? -> At `conductor init`, because the plan is pinned and no executor can repair it. `evidence` also fails closed, with `tier-field-unreadable`, which parks for the same reason. (source: reviews of the parked gate task in run-20261028T204242Z-4c4a06, owner choice 2026-10-29)
- D2: What form does a clause reference take? -> `<feature id>:C<n>`, with n from 1 and no leading zero, the form the recorder accepts. Its feature must be one of the task's features. (source: first slice R6; EGM review B8)
- D3: What does a `tier:` hint without a `contract:` hint mean? -> Nothing yet: lint and `init` both refuse it, so no task carries a tier that no check reads. (source: run follow-up 4, owner choice 2026-10-29)
- D4: What happens on `tier-below-required`? -> A repair on the `evidence-failed` route, as in the first slice's D9. (source: first slice D9; EGM review B6)
- D5: Which tasks does the contract check cover, and against what? -> Every task in a tiered plan, against the run base, so an ungated task cannot merge a contract edit that parks later tiered tasks. No base commit is recorded, so older runs still load. (source: first slice D2; EGM review B5)
- D6: May the work add a dependency or change public schemas? -> No dependency. Schemas change only additively: the optional `assurance` field in `run-result/v1` and the tighter clause pattern in `task-plan/v1`. (source: first slice D12, D13)
- D7: Does a clause that the feature's contract does not define get its own reject? -> No, it is out of scope: it shows as `tier-below-required`, because no record can name it. (source: EGM review B8)
- D8: What does a top-level `min_tier`, outside a task, do? -> Nothing: the conductor reads tier fields only per task. (source: EGM review)

## Iterations
- I1: Split from the first slice after run-20261028T204242Z-4c4a06 parked its gate task. Folded the three review findings of that task into R1 and R3, and run follow-up 4 into R2.
- I2: Applied the EGM review (11 blockers): tier fields read from the pinned plan only, a defined record check, the `tier-field-unreadable` park, routes for the new reasons, the contract check against the run base for every task in a tiered plan, a defined ASSURANCE line, a closed list of `init` refusals, measurable A/B rows, and verify-skill recipes for each proof.

## Build order
<!-- Extra section. -->
1. factory-conductor: R1, then R3, R4, R5, then R6 (docs).
2. spec-first-planning: R2, in parallel with step 1.
3. `scripts/ab-validate.py`: R7.
4. `docs/skill-graph/graph.json`: no change. No skill gains or loses a call.

## Residual risks
- A shell-capable agent can still forge a record by hand. Merge to `main` stays with a human.
- `init` validates the plan it pins. A run whose plan was pinned before this change is outside
  that check; T1 makes `evidence` fail closed on it.
- A missing proof record is a repair (D4), so a verifier that does not record a clause costs
  an executor dispatch. The verifier brief asks for one record per clause to keep that rare.
- The contract check (R4) cannot fire on this run's own features, which have no contracts.
  The gate is proven on fixtures only.
- The first slice's A/B rows for the recorder are not repeated here. They landed with it.
- The escape-hatch risk of the first slice (its D11) is still open until slice 2.

## Open questions
