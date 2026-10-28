# Spec: Proof-tiered verification for the software factory

## Problem
The factory proves a change in one way only: a verifier drives the running app and records
SHA-bound evidence. That proof samples a few inputs. Agent-written code often passes the
sampled inputs and fails on the edges (overflow, empty input, a state transition nobody drove).
Nothing in a feature-map entry states the intent as a contract, so a reviewer must read code to
judge correctness, and an agent can make a proof pass by weakening what it proves. Microsoft
Research and GitHub Next announced the Proof-Driven Development Kit (PDD Kit) in October 2026 to
close this gap for Rust. That kit is a research preview tied to GitHub Next's Chopin, so we
build the same idea into our own skills, for all four languages we use.

## Research basis
<!-- Extra section: ignored by spec_lint.py and spec_to_tasks.py. -->
What PDD Kit does, and what this spec takes from it:

1. The agent reads the code and the plan, then proposes a **formal spec**. A human approves
   the spec. **Engineers review specs, not code.** We take this: the contract in each
   feature-map entry is the review surface, and only a human-merged commit can change it.
2. A **verifier, not the model, decides**. Verus (an SMT-backed verifier for Rust) proves the
   code meets the spec for every input. We take this: a tier counts only when a tool's exit
   status says so, and the evidence recorder stores that tool's output.
3. Where proof is not possible, it **falls back to empirical checks** (tests and computer-use
   agents). We take this: every clause states the tier it reached, and runtime evidence stays
   required for user-facing wiring. A proof of a function says nothing about whether the route
   calls it.
4. PDD Kit is **Rust-only and not public**. The building blocks are public: Verus, and
   Microsoft Research's `verus-proof-synthesis` (AutoVerus, VeruSAGE). We do not depend on the
   kit. We depend on the tools below.

Tier backends per language (v1):

| Tier | Meaning | Rust | Python | TypeScript | Go |
|---|---|---|---|---|---|
| `proved` | holds for every input | Verus | — | — | — |
| `bounded` | holds for every input up to a stated bound | Kani | CrossHair | — | — |
| `property` | holds for N generated inputs, shrunk on failure | proptest | Hypothesis | fast-check | rapid |
| `runtime` | the running app did it once, as a user would | verify-<app> | verify-<app> | verify-<app> | verify-<app> |

Order: `proved` > `bounded` > `property` > `runtime`. A `—` cell means no backend that we
trust for an unattended gate in v1. Gobra (Go) and Nagini (Python) exist but are research
tools; the forge MUST NOT claim a tier through them in v1.

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
- Every evidence record states, per contract clause, the tier reached and the tool that
  reached it, and the conductor checks that claim against the tool output.
- A plan can require a minimum tier per requirement, and the conductor refuses a merge below it.
- An agent cannot weaken a contract, switch off a proof, or pass a property that tests nothing,
  without the gate catching it.
- The run report shows the weakest tier per feature, so "proved" is never read where only
  "property" was reached.

## Non-goals
- Adopting the PDD Kit or Chopin. Neither is public; this spec uses the open tools directly.
- Generating proofs with a bundled model loop. The executor writes proofs and properties with
  its own model; this spec only defines how they are checked.
- Design-level model checking (TLA+, Apalache) in v1. It proves a model, not the code, so it
  needs its own tier semantics. It is an open question below.
- Replacing runtime evidence. Proof tiers add to `verify-evidence/v1`; they never remove the
  runtime check for a user-facing feature.
- A `proved` tier for Go, Python or TypeScript.

## Constraints
- T1 [invariant]: A tier counts only when the backend tool exits 0 on the recorded head and the recorder stored its output with a sha256.
- T2 [invariant]: The contract text at a task's head must equal the contract text at the run's base commit.
- T3 [boundary]: Shipped tooling stays Python 3.10+ stdlib-only and offline; the target repo supplies Verus, Kani, CrossHair, Hypothesis, proptest, fast-check and rapid.
- T4 [invariant]: A proof that uses an escape hatch the contract does not list is not a proof.
- T5 [invariant]: A `bounded` or `property` result counts only when the same check failed on a recorded mutant.
- S1 [invariant]: The verifier that records a tier is not the agent that wrote the code (writer ≠ verifier, as today).
- B1 [goal]: A human approves contracts, not diffs, for any feature the plan gates at `property` or above.
- U1 [boundary]: The run report states the weakest tier per feature in one line a human reads without opening files.
- O1 [boundary]: Plans with no tier requirement behave exactly as they do today.

## Required truths
- RT1 [NOT_SATISFIED]: A feature-map entry can state a contract whose clauses a checker parses and hashes. (parent: OUTCOME; maps_to: B1, T2; reqs: R1, R2; confidence: 0.85; check: {python} verification-skill-forge/assets/test_proof_tiers.py)
- RT2 [NOT_SATISFIED]: An evidence record names the tier and tool per clause and pins the tool output. (parent: OUTCOME; maps_to: T1, S1; reqs: R3, R4; confidence: 0.8; check: {python} verification-skill-forge/assets/test_proof_tiers.py)
- RT3 [NOT_SATISFIED]: The checker rejects a proof with an unlisted escape hatch and a property with no failing mutant. (parent: RT2; maps_to: T4, T5; reqs: R5, R6; confidence: 0.7; check: {python} verification-skill-forge/assets/test_proof_tiers.py)
- RT4 [NOT_SATISFIED]: The target repo can run each tier backend that its stack supports. (parent: RT2; maps_to: T3; reqs: R7; confidence: 0.75; check: {python} verifier-installer/assets/test_proof_backends.py)
- RT5 [NOT_SATISFIED]: A plan can carry a minimum tier and contract clause ids per requirement. (parent: OUTCOME; maps_to: B1, O1; reqs: R8; confidence: 0.85; check: {python} spec-first-planning/assets/test_spec_tiers.py)
- RT6 [NOT_SATISFIED]: The conductor refuses a merge below the required tier, or on a changed contract, and reports the weakest tier. (parent: OUTCOME; maps_to: T1, T2, S1, U1, O1; reqs: R9, R10, R11; confidence: 0.75; check: {python} factory-conductor/assets/test_conductor_assurance.py)

## Requirements
- R1: The feature-map format must accept an optional `## Contract` section of clause bullets `- C<n> [pre|post|inv|trans]: <statement>`, and an optional `- trusted: <escape hatch>, …` line. [where: verification-skill-forge/references/feature-map.md, verification-skill-forge/assets/forge.py]
- R2: `forge lint` must fail a contract with a duplicate or non-sequential clause id, an empty clause, or a clause that no `Proof` line or proof file names by id. [where: verification-skill-forge/assets/forge.py] [after: R1]
- R3: `verify_evidence.py record` must accept `--tier <proved|bounded|property|runtime>`, `--tool <name>`, `--clauses <C ids>` and `--tool-output <file>`, and must store the tool output as a pinned artifact. [where: verification-skill-forge/assets/verify_evidence.py] [after: R1]
- R4: `verify_evidence.py record` must refuse `--tier proved` with a tool other than `verus`, and `--tier bounded` with a tool other than `kani` or `crosshair`. [where: verification-skill-forge/assets/verify_evidence.py] [after: R3]
- R5: `forge check-proof <verify dir> --base <ref>` must fail when the diff adds a Verus `assume(`, `admit(`, `#[verifier::external_body]` or `#[verifier::external]`, or a `kani::assume(` that the feature's `trusted:` line does not list. [where: verification-skill-forge/assets/forge.py] [after: R1]
- R6: `verify_evidence.py record` must refuse `--tier bounded` and `--tier property` unless `--mutant <description>` and `--mutant-output <file>` show the same command exiting non-zero on a mutated copy of the code. [where: verification-skill-forge/assets/verify_evidence.py] [after: R3]
- R7: `verifier-installer` must detect, per stack, which tier backends the repo can run, and must print one `BACKEND: <tier> <tool> <present|missing>` line per row of the tier table. [where: verifier-installer/assets/detect_stack.py, verifier-installer/references/install-playbooks.md] [parallel-safe]
- R8: `spec_lint.py` must accept `[tier: <tier>]` and `[contract: <feature>:C<n>, …]` hints on a requirement, must strip them from the task title, and `spec_to_tasks.py` must copy them into the task's `verification` payload. [where: spec-first-planning/assets/spec_lint.py, spec-first-planning/assets/spec_to_tasks.py, spec-first-planning/references/spec-format.md] [parallel-safe]
- R9: `conductor evidence` must reject with `tier-below-required` when any clause the task names has no record at or above the required tier on the head. [where: factory-conductor/assets/conductor.py] [after: R3, R8]
- R10: `conductor evidence` must reject with `contract-changed` when the `## Contract` text of any touched feature differs between the run's base commit and the head, and must park the task for a human. [where: factory-conductor/assets/conductor.py] [after: R1]
- R11: `conductor status` and the run-result envelope must report one `ASSURANCE: <feature> <weakest tier> <clauses at that tier>` line per gated feature. [where: factory-conductor/assets/conductor.py, factory-conductor/assets/schemas/run-result.v1.json] [after: R9]

## Acceptance criteria
- R1: A fixture feature file with a `## Contract` of three clauses and a `trusted:` line parses into three clause records and one allowance; the same file without the section still lints as today. [cmd: {python} verification-skill-forge/assets/test_proof_tiers.py]
- R2: Negative fixtures for a duplicate id, a gap (C1, C3), an empty clause and an unreferenced clause each make `forge lint` print `LINT_RESULT: FAIL` with the clause id. [cmd: {python} verification-skill-forge/assets/test_proof_tiers.py]
- R3: A record with tier, tool, clauses and tool output writes those fields into `evidence.json`, and editing the stored tool output after recording makes the conductor's artifact check fail. [cmd: {python} verification-skill-forge/assets/test_proof_tiers.py]
- R4: `record --tier proved --tool hypothesis` exits non-zero and writes nothing under `.verify/`. [cmd: {python} verification-skill-forge/assets/test_proof_tiers.py]
- R5: A fixture diff that adds `#[verifier::external_body]` fails `forge check-proof`; the same diff passes once the feature lists that hatch under `trusted:`. [cmd: {python} verification-skill-forge/assets/test_proof_tiers.py]
- R6: `record --tier property` with no mutant, and with a mutant whose output shows exit 0, both exit non-zero; with a mutant output showing a non-zero exit it records. [cmd: {python} verification-skill-forge/assets/test_proof_tiers.py]
- R7: Fixture repos for Rust, Python, TypeScript and Go each print the expected `BACKEND:` lines, and a repo with no Cargo.toml prints no Verus or Kani line. [cmd: {python} verifier-installer/assets/test_proof_backends.py]
- R8: A spec with `[tier: property] [contract: notes-create:C1, notes-create:C2]` lints clean, the task title carries neither hint, and the plan JSON carries `min_tier` and `clauses` for that task; an unknown tier fails lint. [cmd: {python} spec-first-planning/assets/test_spec_tiers.py]
- R9: A gated task that needs `property` on C1 and C2, with only `runtime` evidence for C2, prints `EVIDENCE: <task> reject tier-below-required` and exits 3. [cmd: {python} factory-conductor/assets/test_conductor_assurance.py]
- R10: A task branch that edits C1's text in `features/notes-create.md` prints `EVIDENCE: <task> reject contract-changed` and the task status becomes parked, even when every record passes. [cmd: {python} factory-conductor/assets/test_conductor_assurance.py]
- R11: A finished gated run with C1 at `proved` and C2 at `property` prints `ASSURANCE: notes-create property C2`, and the envelope validates against the updated schema. [cmd: {python} factory-conductor/assets/test_conductor_assurance.py]

## Build order
<!-- Extra section. Same rule as the 2026-09-29 evidence-gate spec: the proof surface first,
     the gate last, so the gate never enforces something no skill can produce. -->
1. verification-skill-forge: R1–R6, a new `references/proof-tiers.md`, the tier table above,
   and negative fixtures in `eval/run_eval.py`.
2. verifier-installer: R7, plus a playbook per backend.
3. spec-first-planning: R8.
4. factory-conductor: R9–R11, new rows in "The evidence gate" and in the verifier brief.
5. `scripts/ab-validate.py`: one `SINCE_PROOF_TIERS` row per reject reason (tier, contract,
   escape hatch, vacuous property), plus a HELD row that an ungated plan is unchanged (O1).
6. `docs/skill-graph/graph.json`: the new verifier-installer → verification-skill-forge edge
   for backend detection, then `make skill-graph`.

## Residual risks
- A shell-capable agent can still forge a record by hand. T1, T5 and S1 raise the cost; they do
  not remove it. Merge to `main` stays with a human, as today.
- A contract can be complete and still wrong. T2 makes the human the only author of record;
  it does not make the human right.
- A mutant check proves the property can fail, not that it fails for the right reason. One
  mutant per clause is the floor, not the target.
- Verus proofs are slow to write. Expect agents to reach `property` far more often than
  `proved`, and plan Rust tiers with that in mind.

## Open questions
- Should design-level model checking (TLA+ or Apalache on a payment state machine) get its own
  `model` axis in v2, separate from the code tiers?
- Who writes the mutant: the verifier (stronger, slower) or a fixed operator set in the forge
  (cheaper, weaker)? This spec lets the verifier choose and records the choice.
- Should `contract-changed` allow a contract to get *stronger* inside a task (a new clause
  only), or must every contract change go through a human-merged commit?
- Is `--max-examples` for `property` worth a floor (Hypothesis defaults to 100)?

## Sources
Read on 2026-10-22. Primary posts are on X; the articles summarise them.
- Microsoft Research, PDD Kit announcement post on X.
- GitHub Next, PDD Kit announcement post on X (spec in Verus, fallback to tests and computer-use agents, Rust only).
- DEV Community, "GitHub's PDD Kit proves agent-written Rust correct: 5 steps and 3 limits" (AutoVerus and VeruSAGE in `verus-proof-synthesis`).
- AlphaSignal, "Microsoft's PDD Kit Makes Formal Math Proofs a Gate for AI Rust Code" (research preview status).
