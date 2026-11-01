# Spec: Proof-tiered verification for the software factory

## Problem
The factory proves a change in one way only: a verifier drives the running app and records
SHA-bound evidence. That proof samples a few inputs. Agent-written code often passes the
sampled inputs and fails on the edges (overflow, empty input, a state transition nobody drove).
Nothing in a feature-map entry states the intent as a contract, so a reviewer must read code to
judge correctness, and an agent can make a proof pass by weakening what it proves. Microsoft
Research and GitHub Next announced the Proof-Driven Development Kit (PDD Kit) in October 2026 to
close this gap for Rust. That kit is a research preview tied to GitHub Next's Chopin, so we
build the same idea into our own skills: a formal proof where Rust allows one, and lower,
clearly labelled tiers for Python, TypeScript and Go.

This spec is the first slice (decision D7). It builds the contract, the tier records with their
mutant check, the plan hints, and the conductor gate. The escape-hatch check and backend
detection come in later slices.

## Research basis
<!-- Extra section: ignored by spec_lint.py and spec_to_tasks.py. -->
What PDD Kit does, and what this spec takes from it:

1. The agent reads the code and the plan, then proposes a **formal spec**. A human approves
   the spec. **Engineers review specs, not code.** We take this: the contract in each
   feature-map entry is the review surface, and only a human-merged commit can change it.
2. A **verifier, not the model, decides**. Verus (an SMT-backed verifier for Rust) proves the
   code meets the spec for every input. We take this: a tier counts only when the recorder
   itself ran the tool on the recorded head and saw it exit 0 (decision D6).
3. Where proof is not possible, it **falls back to empirical checks** (tests and computer-use
   agents). We take this: every clause states the tier it reached, and runtime evidence stays
   required for every touched feature. A proof of a function says nothing about whether the
   route calls it.
4. PDD Kit is **Rust-only and not public**. The building blocks are public: Verus, and
   Microsoft Research's `verus-proof-synthesis` (AutoVerus, VeruSAGE; informative only, since
   generating proofs is a non-goal). We depend on the tools below, not on the kit.

Tier backends per language (v1):

| Tier | Meaning | Rust | Python | TypeScript | Go |
|---|---|---|---|---|---|
| `proved` | holds for every input | Verus | — | — | — |
| `bounded` | holds for every input up to a stated bound | Kani | — | — | — |
| `property` | holds for N generated inputs, shrunk on failure | proptest | Hypothesis | fast-check | rapid |
| `runtime` | the running app did it once, as a user would | verify-<app> | verify-<app> | verify-<app> | verify-<app> |

Order: `proved` > `bounded` > `property` > `runtime` > `none`. A `—` cell means no backend
that we trust for an unattended gate in v1. CrossHair is left out because its bound is a time
budget, not an input bound (decision D10). Gobra (Go) and Nagini (Python) are research tools;
no tier is claimed through them in v1.

Earlier work: the evidence gate this extends is specified in
`docs/factory/2026-09-29-evidence-gate.md`.

## Users
- A human owner (Dhanesh) who wants to approve a short contract instead of reading every
  agent-written diff, and who wants the run report to say how strongly each clause is proven.
- factory-conductor, which needs a mechanical rule to hold a merge until each clause reaches
  the tier the plan asked for.
- Executor and verifier agents, which read a feature-map entry cold and need to know which
  tool proves which clause, and what they may not touch.

## Goals
- Every feature-map entry can carry a language-agnostic contract: numbered clauses for
  preconditions, postconditions, invariants and allowed state transitions.
- Every proof record states the tier, the tool and the clause it covers, and holds the
  recorder's own run of the tool, so the claim is checked against what the tool did.
- A plan can require a minimum tier per requirement, and the conductor refuses a merge below it.
- An agent cannot weaken a contract or pass a property that tests nothing without the gate
  catching it.
- The run report shows the weakest tier per feature, so "proved" is never read where only
  "property" was reached.

## Non-goals
- Adopting the PDD Kit or Chopin. Neither is public; this spec uses the open tools directly.
- Generating proofs with a bundled model loop. The executor writes proofs and properties with
  its own model; this spec only defines how they are checked.
- Design-level model checking (TLA+, Apalache) in v1. It proves a model, not the code, so it
  needs its own tier semantics (decision D3).
- Replacing runtime evidence. Proof records add to `verify-evidence/v1`; they never remove the
  runtime check for a touched feature.
- A `proved` tier for Go, Python or TypeScript.
- The escape-hatch check (`forge check-proof` and its conductor reject), deferred to slice 2
  (decision D7, accepted risk D11).
- Tier-backend detection in verifier-installer, deferred to slice 3 (decisions D7 and D8).
- A floor on generated examples for `property` (decision D5).

## Constraints
- T1 [invariant]: A proof tier counts only when the recorder itself ran the tool command on the recorded head, saw exit 0, and stored the command, its exit status and its output with a sha256.
- T2 [invariant]: The `## Contract` text of every feature a gated task touches must equal its text at the run's base commit.
- T3 [boundary]: Shipped tooling stays Python 3.10+ stdlib-only and offline; the target repo supplies Verus, Kani, Hypothesis, proptest, fast-check and rapid.
- T5 [invariant]: A `bounded` or `property` record counts only when the same command failed with the tool's failure signature on a recorded mutant of the code.
- T6 [invariant]: Every vendored copy of `verify_evidence.py` in the repo stays byte-identical to `verification-skill-forge/assets/verify_evidence.py`.
- T7 [boundary]: After every task, the per-skill gate (`make gate-skill`) of each skill the task changed passes.
- S1 [invariant]: The verifier that records a tier is not the agent that wrote the code (writer ≠ verifier, as today).
- B1 [goal]: A human approves contracts, not diffs, for any feature the plan gates at `property` or above.
- U1 [boundary]: The run report states the weakest tier per feature in one line a human reads without opening files.
- O1 [boundary]: Plans with no tier requirement behave exactly as they do today.

## Tensions
- TN1 [trade_off]: The recorder has to run real proof tools for T1 and T5, yet shipped tooling must stay stdlib-only and offline. (between: T1, T5, T3; status: resolved; strategy: Partition; decision: D6)
- TN2 [trade_off]: The Research basis has agents propose contracts, but strict contract checking parks any task that writes one. (between: B1, T2; status: resolved; strategy: Prioritize; decision: D2)
- TN3 [hidden_dependency]: Each change to the recorder drifts its vendored copies, and the forge lint and eval fail on drift. (between: T6, T7; status: resolved; strategy: Transform)
- TN4 [resource_tension]: Shipping the core slice first leaves a `proved` record that can hide a Verus escape hatch until slice 2. (between: T1, B1; status: accepted; strategy: Accept; decision: D11)

## Required truths
- RT1 [SPECIFICATION_READY]: A feature-map entry can state a contract whose clauses a checker parses and hashes, and lint rejects a malformed one. (parent: OUTCOME; maps_to: B1, T2; reqs: R1, R2; confidence: 0.85; check: {python} verification-skill-forge/assets/test_contract.py)
- RT2 [SPECIFICATION_READY]: A proof record holds the recorder's own run of a tool allowed for its tier, on the head, by a named verifier. (parent: OUTCOME; maps_to: T1, T3, S1, T6; reqs: R3, R4; confidence: 0.8; check: {python} verification-skill-forge/assets/test_record_tier.py)
- RT3 [SPECIFICATION_READY]: A `bounded` or `property` record exists only when its command failed on a recorded mutant with the tool's failure signature. (parent: RT2; maps_to: T5; reqs: R5; confidence: 0.7; check: {python} verification-skill-forge/assets/test_record_mutant.py)
- RT4 [SPECIFICATION_READY]: A plan can carry a minimum tier and contract clause ids per requirement, and a plan without them is unchanged. (parent: OUTCOME; maps_to: B1, O1; reqs: R6; confidence: 0.85; check: {python} spec-first-planning/assets/test_spec_tiers.py)
- RT5 [SPECIFICATION_READY]: The conductor refuses a merge below the required tier or on a changed contract, and an ungated plan runs as before. (parent: OUTCOME; maps_to: T1, T2, S1, O1; reqs: R7, R8; confidence: 0.75; check: {python} factory-conductor/assets/test_conductor_tiers.py)
- RT6 [SPECIFICATION_READY]: `conductor status` and the run result report the weakest tier per gated feature. (parent: OUTCOME; maps_to: U1; reqs: R9; confidence: 0.8; check: {python} factory-conductor/assets/test_conductor_assurance.py)
- RT7 [SPECIFICATION_READY]: The tiers are documented, guarded by eval negatives and A/B rows, and every touched skill's gate stays green. (parent: OUTCOME; maps_to: T6, T7, O1; reqs: R10, R11, R12; confidence: 0.75; check: {python} verification-skill-forge/eval/run_eval.py)

## Requirements
- R1: `forge contract <feature file>` must print one JSON object with `clauses` (a list of `{id, kind, text}` from the `## Contract` section's `- C<n> [pre|post|inv|trans]: <statement>` bullets), `trusted` (the comma-separated entries of an optional `- trusted:` header line before the first `##`, else an empty list) and `sha256` (of the section text from the `## Contract` heading to the next `## ` heading, each line right-stripped), and `references/feature-map.md` must document that format. [where: verification-skill-forge/assets/forge.py, verification-skill-forge/references/feature-map.md, verification-skill-forge/assets/test_contract.py] [feature: forge-feature-map] [proof: on a fixture feature with a three-clause Contract and a trusted header, forge contract prints three clause records, one trusted entry and a 64-hex sha256; on a feature without the section it prints an empty clause list] [parallel-safe]
- R2: `forge lint` must fail a `## Contract` that has a duplicate clause id, a gap in the ids (C1 then C3), a clause with empty text, an unknown kind, or a clause whose id does not appear in the same feature's `## Proof` section, printing the clause id in each failure line. [where: verification-skill-forge/assets/forge.py, verification-skill-forge/assets/test_contract_lint.py] [feature: forge-feature-map] [proof: forge lint on fixture verify skills with each defect prints LINT_RESULT: FAIL with the clause id, and on a fixture whose Proof names every clause prints LINT_RESULT: PASS] [after: R1]
- R3: `verify_evidence.py record --tier <proved|bounded|property> --tool <name> --clauses <C id> --cmd <argv>` must run the argv itself (split with shlex, no shell, from the worktree root, with a timeout), refuse unless it exits 0, and write `<evidence dir>/<instance>/<feature>/<sha>/proof-<tier>-<tool>-<clause>.json` with the argv, exit status, the verifier and the pinned stdout and stderr in `artifacts`, without requiring `--action`, `--observed`, `--side-effect` or `--artifact`, and with every validation done before any write. [where: verification-skill-forge/assets/verify_evidence.py, verification-skill-forge/assets/test_record_tier.py, verification-skill-forge/eval/fixtures/notes-app/.claude/skills/verify-notes/scripts/verify_evidence.py, .claude/skills/verify-agent-skills/scripts/verify_evidence.py] [feature: evidence-recorder] [proof: record with --tier proved and a cmd that exits 0 writes a proof JSON whose artifacts hold stdout and stderr with matching sha256; with a cmd that exits 1 it exits non-zero and creates no file under the evidence dir] [parallel-safe]
- R4: `verify_evidence.py record` must refuse, with exit 2 and no write, a `--tool` outside its tier's allowlist (proved: verus; bounded: kani; property: proptest, hypothesis, fast-check, rapid) and a `--clauses` value that names more or less than one clause. [where: verification-skill-forge/assets/verify_evidence.py, verification-skill-forge/assets/test_record_tool.py, verification-skill-forge/eval/fixtures/notes-app/.claude/skills/verify-notes/scripts/verify_evidence.py, .claude/skills/verify-agent-skills/scripts/verify_evidence.py] [feature: evidence-recorder] [proof: record --tier proved --tool hypothesis and record --tier property --tool echo each exit 2 and leave the evidence dir unchanged] [after: R3]
- R5: `verify_evidence.py record --tier bounded` and `--tier property` must also require `--mutant-patch <diff file>` and `--mutant-author <verifier|forge>`, apply the patch to a temporary copy of the head, run the same argv there, and refuse unless that run exits non-zero and its output matches the tool's failure signature in `references/proof-tiers.md`, storing the patch, the mutant's exit status and output in the record's `artifacts`. [where: verification-skill-forge/assets/verify_evidence.py, verification-skill-forge/references/proof-tiers.md, verification-skill-forge/assets/test_record_mutant.py, verification-skill-forge/eval/fixtures/notes-app/.claude/skills/verify-notes/scripts/verify_evidence.py, .claude/skills/verify-agent-skills/scripts/verify_evidence.py] [feature: evidence-recorder] [proof: record --tier property with no mutant, with a mutant whose run exits 0, and with a mutant that only breaks the build each exit non-zero with no file written; with a mutant whose run fails the property it writes a record holding the patch and both runs] [after: R4]
- R6: `spec_lint.py` must accept a `tier:` hint (one of proved, bounded, property, runtime) and a `contract:` hint (`<feature>:C<n>` entries) on a requirement, fail an unknown tier and a `contract:` hint without a `tier:` hint, strip both from the task title, and `spec_to_tasks.py` must copy them into the task as top-level `min_tier` (string) and `clauses` (list of strings), which `task-plan.v1.json` must allow. [where: spec-first-planning/assets/spec_lint.py, spec-first-planning/assets/spec_to_tasks.py, spec-first-planning/references/spec-format.md, spec-first-planning/assets/schemas/task-plan.v1.json, spec-first-planning/assets/test_spec_tiers.py] [feature: spec-hints] [proof: a fixture spec whose requirement carries tier property and contract notes-create:C1, notes-create:C2 lints PASS and compiles to a task with min_tier and clauses and a title without the hints; a tier of fast fails lint] [parallel-safe]
- R7: `conductor evidence` must, for a task with `min_tier`, reject with `tier-below-required <feature>:<C id>` when a clause in its `clauses` has no proof record on the head at or above `min_tier` (a passing runtime `evidence.json` for the clause's feature counts as `runtime`), must keep the existing runtime-evidence check for every touched feature, and must send that reject back to the executor as a repair. [where: factory-conductor/assets/conductor.py, factory-conductor/assets/test_conductor_tiers.py] [feature: conductor-evidence-gate] [proof: a gated fixture run whose task needs property on C1 and C2, with a property record for C1 and only runtime evidence for C2, prints EVIDENCE: T1 reject tier-below-required notes-create:C2 and exits 3, and status shows the task sent back for repair] [after: R3, R6]
- R8: `conductor init` must record the base commit, and `conductor evidence` must, for a task with `min_tier` and before any other reject, reject with `contract-changed <feature>` and park the task when the `## Contract` text of any feature file under the verify skill differs between that base commit and the head, including a section added or removed. [where: factory-conductor/assets/conductor.py, factory-conductor/assets/test_conductor_contract.py] [feature: conductor-evidence-gate] [proof: a gated fixture run whose task edits the text of C1 in its feature file prints EVIDENCE: T1 reject contract-changed notes-create and the task status becomes parked even though every record passes] [after: R7]
- R9: `conductor status` must print one `ASSURANCE: <feature> <weakest tier> <clauses at that tier, ascending, comma-separated>` line per feature named in any task's `clauses`, after its `STATUS:` lines, with `none` for a clause with no record, and the run-result envelope must carry the same data as `assurance: [{feature, tier, clauses}]`, which `run-result.v1.json` must allow. [where: factory-conductor/assets/conductor.py, factory-conductor/assets/schemas/run-result.v1.json, factory-conductor/assets/test_conductor_assurance.py] [feature: conductor-evidence-gate] [proof: a finished gated fixture run with C1 at proved and C2 at property prints ASSURANCE: notes-create property C2, and its run-result envelope passes contract_check check-envelope] [after: R8, R5]
- R10: `verification-skill-forge/references/proof-tiers.md` must give the tier table, the tool allowlist, each tool's failure signature and the `record` usage for tiers, and `verification-skill-forge/eval/run_eval.py` must include negative fixtures for a tool outside its tier, a mutant that passes and a mutant that only breaks the build. [where: verification-skill-forge/references/proof-tiers.md, verification-skill-forge/SKILL.md, verification-skill-forge/eval/run_eval.py] [feature: evidence-recorder] [proof: run_eval.py prints a line for each of the three negative fixtures with the refusal it saw, then EVAL_RESULT: PASS] [after: R5]
- R11: factory-conductor's SKILL.md and `references/run-protocol.md` must list `tier-below-required` and `contract-changed` in the evidence gate with their outcomes, and its verifier brief must tell the verifier to record one proof record per clause the task names, with every new BCP 14 keyword sentence registered in `docs/rfc2119/2026-09-19-classification.md`. [where: factory-conductor/SKILL.md, factory-conductor/references/run-protocol.md, docs/rfc2119/2026-09-19-classification.md] [feature: conductor-evidence-gate] [proof: each reject reason that the gated fixture runs print appears in factory-conductor/SKILL.md's evidence gate list with its outcome, and the verifier brief names the record command for tiers] [after: R9]
- R12: `scripts/ab-validate.py` must carry a `SINCE_PROOF_TIERS` constant naming the first commit of this change after the base, IMPROVED rows for `tier-below-required`, `contract-changed`, a tool outside its tier and a mutant that passes, and a HELD row showing that a plan without `min_tier` merges exactly as before. [where: scripts/ab-validate.py] [feature: ab-validate] [proof: make ab-validate run in the task worktree prints the four new rows as IMPROVED and the ungated-plan row as HELD, with no WORSE or UNPROVEN row] [after: R9, R5]

## Acceptance criteria
- R1: A fixture feature file with a `## Contract` of three clauses and a `trusted:` header line prints three clause records with `id`, `kind` and `text`, one trusted entry and a 64-hex `sha256`; the same file without the section prints an empty clause list and still lints `LINT_RESULT: PASS`. [cmd: {python} verification-skill-forge/assets/test_contract.py]
- R2: Negative fixtures for a duplicate id, a gap (C1, C3), an empty clause, an unknown kind and a clause missing from `## Proof` each make `forge lint` print `LINT_RESULT: FAIL` and a line naming the clause id. [cmd: {python} verification-skill-forge/assets/test_contract_lint.py]
- R2: The forge's per-skill gate still passes. [cmd: make gate-skill SKILL=verification-skill-forge]
- R3: A record with `--tier proved --tool verus --clauses C1 --cmd` of a command that exits 0 writes `proof-proved-verus-C1.json` with the argv, exit status, verifier and pinned output; editing the stored stdout afterwards makes the conductor's artifact check report `evidence-artifact-altered`; a command that exits 1 writes nothing. [cmd: {python} verification-skill-forge/assets/test_record_tier.py]
- R4: `record --tier proved --tool hypothesis`, `record --tier property --tool echo` and `record --tier property --clauses C1,C2` each exit 2 and leave the evidence directory exactly as it was. [cmd: {python} verification-skill-forge/assets/test_record_tool.py]
- R5: `record --tier property` with no mutant, with a mutant whose run exits 0, and with a mutant whose output lacks the tool's failure signature each exit non-zero and write nothing; with a mutant whose run fails with the signature, it writes a record holding the patch, `mutant_author` and both runs. [cmd: {python} verification-skill-forge/assets/test_record_mutant.py]
- R5: The forge's per-skill gate still passes, so every vendored recorder copy matches. [cmd: make gate-skill SKILL=verification-skill-forge]
- R6: A spec whose requirement carries a `tier:` hint of `property` and a `contract:` hint of `notes-create:C1, notes-create:C2` lints clean, its task title carries neither hint, and the plan JSON carries `min_tier` and `clauses` for that task; an unknown tier and a `contract:` hint with no `tier:` hint each fail lint; a spec with neither hint compiles to a byte-identical plan. [cmd: {python} spec-first-planning/assets/test_spec_tiers.py]
- R6: spec-first-planning's per-skill gate still passes. [cmd: make gate-skill SKILL=spec-first-planning]
- R7: A gated task that needs `property` on C1 and C2, with a property record for C1 and only runtime evidence for C2, prints `EVIDENCE: T1 reject tier-below-required notes-create:C2`, exits 3 and is sent back for repair; with property records for both it passes; a task without `min_tier` passes on runtime evidence alone. [cmd: {python} factory-conductor/assets/test_conductor_tiers.py]
- R8: A task branch that edits C1's text in `features/notes-create.md` prints `EVIDENCE: T1 reject contract-changed notes-create` and the task status becomes `parked`, even when every record passes; the same edit in a task without `min_tier` does not reject. [cmd: {python} factory-conductor/assets/test_conductor_contract.py]
- R9: A finished gated run with C1 at `proved` and C2 at `property` prints `ASSURANCE: notes-create property C2`, a clause with no record reports `none`, and the envelope validates against the updated schema. [cmd: {python} factory-conductor/assets/test_conductor_assurance.py]
- R9: factory-conductor's per-skill gate still passes. [cmd: make gate-skill SKILL=factory-conductor]
- R10: The forge's eval runs the three new negative fixtures and ends with `EVAL_RESULT: PASS`. [cmd: {python} verification-skill-forge/eval/run_eval.py]
- R11: The BCP 14 register check passes with the new keyword sentences. [cmd: make bcp14]
- R11: factory-conductor's per-skill gate still passes with the new documentation. [cmd: make gate-skill SKILL=factory-conductor]
- R12: The A/B validation reports the new rows as IMPROVED or HELD and no row as WORSE or UNPROVEN. [cmd: make ab-validate]

## Verification
Verify skill: .claude/skills/verify-agent-skills

## Solution options
- OPT-A: Core slice: contract, recorder-run tier records with the mutant check, plan hints and the conductor gate, with the escape-hatch check and backend detection in later slices. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1, RT2, RT3, RT4, RT5, RT6, RT7)
- OPT-B: Everything in one run, the escape-hatch check and backend detection included. (complexity: High; reversibility: TWO_WAY; satisfies: RT1, RT2, RT3, RT4, RT5, RT6, RT7)
- OPT-C: Core slice, but the recorder stores agent-supplied tool and mutant output files instead of running the tools. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT4, RT6)

Recommended: OPT-A — it satisfies every required truth at a lower complexity than OPT-B; OPT-C is simpler but lets a forged log pass T1 and T5.

## Decisions
- D1: How does an unattended run get runtime proof in a repo with no app? -> A verify skill for this repo, `.claude/skills/verify-agent-skills`, drives the skill CLIs on fixtures; every requirement runs under the evidence gate. (source: decision sweep, 2026-10-28)
- D2: May a task make a contract stronger, for example add a clause? -> No. Any change to `## Contract` text in a gated task parks it; contracts reach the base branch through a human-merged commit before the run. (source: decision sweep, 2026-10-28)
- D3: Does TLA+ or Apalache model checking get a `model` axis now? -> No, it is a v1 non-goal, revisited in v2. (source: decision sweep, 2026-10-28)
- D4: Who writes the mutant? -> The verifier chooses, and `--mutant-author verifier|forge` records the choice. (source: decision sweep, 2026-10-28)
- D5: Is there a floor on generated examples for `property`? -> No floor in v1; the stored tool output shows the count. (source: decision sweep, 2026-10-28)
- D6: How does a tier record prove its tool ran? -> The recorder runs the command itself, at head and on the mutant, and pins argv, exit statuses and output; agent-supplied output files are not accepted. (source: EGM review B4, owner choice 2026-10-28)
- D7: How much does this run build? -> The core slice R1-R12; the escape-hatch check and backend detection come in later specs. (source: EGM review, owner choice 2026-10-28)
- D8: How will backend detection report, in its later slice? -> A `backends` key in detect_stack.py's JSON, one entry per non-blank table cell for each detected stack, present meaning declared in a manifest. (source: EGM review B10, owner choice 2026-10-28)
- D9: What happens on `tier-below-required`? -> A repair: the task goes back to its executor on the repair budget, because a missing tier usually means the proof code does not exist. (source: EGM review B8, owner choice 2026-10-28)
- D10: Does CrossHair count as `bounded`? -> No. Its bound is a time budget, so Python has no `bounded` backend in v1. (source: EGM review, conductor default shown in the grant summary)
- D11: Is it acceptable that a `proved` record can hide a Verus escape hatch until slice 2? -> Yes, as an accepted risk; the reviewer brief asks the reviewer to search the diff for `assume(`, `admit(` and `external_body`. (source: decision sweep, 2026-10-28)
- D12: May the work add a dependency? -> No. Shipped tooling stays stdlib-only; tests fake the proof tools with small scripts. (source: constraint T3)
- D13: May it change public schemas? -> Only additively: optional fields in `task-plan/v1` and `run-result/v1`, with no version bump. (source: decision sweep, 2026-10-28)

## Iterations
- I1: Light pass from the 2026-10-22 draft: constraints, required truths and requirements R1-R11.
- I2: Applied the EGM review and the owner's choices: separate proof records, recorder-run tools, top-level task fields, a recorded base commit, classified rejects, chained dependencies, support requirements, and the core-slice scope.

## Build order
<!-- Extra section. Same rule as the 2026-09-29 evidence-gate spec: the proof surface first,
     the gate last, so the gate never enforces something no skill can produce. -->
1. verification-skill-forge: R1, R2 (contract), R3, R4, R5 (records), R10 (docs and eval).
2. spec-first-planning: R6, in parallel with step 1.
3. factory-conductor: R7, R8, R9, then R11 (docs).
4. `scripts/ab-validate.py`: R12.
5. `docs/skill-graph/graph.json`: no change in this slice. No skill gains or loses a call.

## Residual risks
- A shell-capable agent can still forge a record by hand. T1, T5 and S1 raise the cost; they do
  not remove it. Merge to `main` stays with a human, as today.
- writer ≠ verifier rests on the verifier id that the conductor is given; the tool can only
  compare the ids it receives.
- A contract can be complete and still wrong. T2 makes the human the only author of record;
  it does not make the human right.
- A mutant check proves the property can fail, not that it fails for the right reason. One
  mutant per clause is the floor, not the target.
- Until slice 2, a `proved` record can hide an escape hatch (D11).
- Verus proofs are slow to write. Expect agents to reach `property` far more often than
  `proved`, and plan Rust tiers with that in mind.

## Open questions

## Sources
Read on 2026-10-22. Primary posts are on X; the articles summarise them.
- Microsoft Research, PDD Kit announcement post on X.
- GitHub Next, PDD Kit announcement post on X (spec in Verus, fallback to tests and computer-use agents, Rust only).
- DEV Community, "GitHub's PDD Kit proves agent-written Rust correct: 5 steps and 3 limits" (AutoVerus and VeruSAGE in `verus-proof-synthesis`).
- AlphaSignal, "Microsoft's PDD Kit Makes Formal Math Proofs a Gate for AI Rust Code" (research preview status).
