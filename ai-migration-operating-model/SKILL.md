---
name: ai-migration-operating-model
description: >-
  Run a code migration (language port, framework/SDK upgrade, service rewrite) as an
  operating model — rulebook + queues + reviewers + objective verification — instead of a
  pile of agent edits. Use when the user says "migrate this service", "port X to Y",
  "rewrite this in TypeScript/Go", "upgrade the framework", or wants agents to do a large
  rewrite. Builds the judge first (golden scenarios + a shipped parity differ), then a
  lintable Migration Control Pack (rulebook, dependency map, gap inventory, portability
  test plan, parity script, work queue, reviewer prompts, phase gates) that implementing
  agents execute against; includes a candidate filter that says "don't migrate yet" when
  behavior can't be captured. Callable as one whole workflow or via four standalone
  modes — economics, judge, pack, qualify — one per part of the model. Not the bulk
  executor of the migration itself — it sets up and verifies the machine; complements
  spec-first-planning and crafting-self-prompting-loops.
license: MIT
compatibility: Requires python3 (stdlib only) and a POSIX-like shell; fully offline, no network.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "migration,rewrite,port,parity,golden-tests,rulebook,agent-orchestration,phase-gates"
---

# AI Migration Operating Model

AI makes big migrations cheaper **only when the migration has strong control loops and
objective verification** — without those, agents just produce plausible-looking code
quickly, which is worse than slow. So this skill does not "rewrite the code"; it designs
the machine that rewrites the code: the old system as the spec, a rulebook as policy,
tests and diffs as the judge, and repeated failures as reasons to improve the process.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which must receive the literal absolute
path, never a relative form:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'ai-migration-operating-model' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

## Invocations

Invoked bare, run the full workflow below in order. Invoked with a mode argument
(`<mode> [target]`), run just that part — the operating model's four parts are
separately callable:

1. **`economics [target]`** — make the migration economics explicit. Deliverable: a
   short brief naming the measurable current pain, what agent leverage (parallel
   workers, old code as the spec, compiler/test feedback) changes for *this* target,
   and the honest cost frame — agentic migrations are serious engineering investment,
   not free — ending with whether the chronic pain clears the bar now that a migration
   no longer has to be existential. Grounding: the preamble of
   `references/candidate-filter.md`.
2. **`judge [target]`** — build the verification foundation, alone (workflow step 2).
   Deliverable: golden scenarios, a parity runner wired to `assets/parity_diff.py`,
   and the transcript proving the judge catches a deliberately broken case.
3. **`pack [target]`** — produce the Migration Control Pack, alone (workflow steps
   3–4). Deliverable: the eight artifacts of `references/control-pack.md` in a
   `migration/` directory, linted to `PACK_RESULT: PASS`.
4. **`qualify [target]`** — run the candidate filter, alone (workflow step 1).
   Deliverable: the row-by-row decision-table verdict — go, no-go, or the precursor
   work that would convert a no into a yes.

Standalone modes still respect order of operations: `pack` presumes a judge exists
(its gate 1 demands one) — when there isn't one, say so and offer to run `judge`
first rather than authoring a phase gate the pack can't honor.

## Doctrine

- **The loop is the strategy, not the agents.** Leverage comes from the process wrapped
  around the agents, not from the agents themselves.
- **Fix the loop, not the files.** When a batch of migrated files shares a defect,
  prefer the rule fix: name the pattern, add a `RULEBOOK.md` rule, regenerate the batch,
  rerun the judge. Hand-patching is for genuine one-offs — the same hand-edit made twice
  means the loop wants a rule (worked example in `references/pilot-playbook.md`).
- **The judge comes first.** Anything mechanical that tells you the new system behaves
  like the old one — compilers, tests, golden-scenario diffs — is the foundation.
  A judge that has not been seen to fail proves nothing.
- **Volume is not progress.** "We generated a lot of code" passes no gate.

## Workflow

1. **Qualify the candidate** (mode: `qualify`; fold in the `economics` brief when the
   business case is contested). Walk the decision table in
   `references/candidate-filter.md`. Every "no" is a finding; when the filter mostly
   fails, the deliverable is a recommendation *against* migrating now, plus the
   precursor work (usually: build golden tests or observability first). Report this
   verdict before writing anything else.
2. **Build the judge** (mode: `judge`). Capture golden scenarios from the old system (edge cases
   included: failures, retries, timeouts, reversals) and wire a parity check with
   `assets/parity_diff.py` (`--ignore` for timestamp/id noise, `--tolerance` for float
   drift; single-file or golden-corpus directory mode). Prove the judge catches a
   deliberately broken case before trusting any pass.
3. **Author the Migration Control Pack** (steps 3–4 = mode: `pack`) in a `migration/`
   directory next to the target,
   one artifact per section of `references/control-pack.md`: `RULEBOOK.md`,
   `DEPENDENCY_MAP.md`, `GAP_INVENTORY.md`, `PORTABILITY_TEST_PLAN.md`, a
   `parity_check.*` script, `AGENT_WORK_QUEUE.md`, `REVIEWER_PROMPTS.md`,
   `PHASE_GATES.md`. Mine the gap inventory from what the old system allowed implicitly
   (nullability, units, currencies, timezones) — those are the landmines.
4. **Lint and repair the pack** with `python3 "$SKILL_DIR/assets/control_pack_lint.py" <pack-dir>`.
   Fix every `FAIL:` line (each names the artifact and defect) and rerun until it
   prints `PACK_RESULT: PASS`. Repair by making the pack more explicit — resolving a
   gap with a `decision:`, tightening a rule's modal — not by deleting entries.
5. **Run a disposable pilot slice** per `references/pilot-playbook.md`: translate one
   small unit, run the judge, throw the code away, keep the lessons as new rulebook
   rules. Iterate until a fresh translation of the slice passes parity clean.
6. **Fan out under the pack.** Implementer agents claim units from the work queue
   (statuses machine-readable, queue rebuilt from facts so the run is resumable);
   reviewer agents attack output using the reviewer prompts and cite `MR` rule ids.
   Batch defects route through the doctrine: rule, regenerate, re-judge.
7. **Advance only through phase gates.** Each gate in `PHASE_GATES.md` is passed by its
   named check — gate 1 is the judge existing and catching broken code; the last gates
   are behavioral parity green (`PARITY_RESULT: PASS` across the golden corpus) and an
   approved rollout/rollback plan.

## Deliverable

A **candidate verdict plus a lint-clean Migration Control Pack plus a proven judge**:
the filter's row-by-row verdict, the pack directory passing `assets/control_pack_lint.py`
(`PACK_RESULT: PASS`), and a parity run showing the judge both catches a seeded break
and reports `PARITY_RESULT: PASS` on the pilot slice. Present these with the current
phase-gate status and the open gaps (`G<n>` entries still ending in `?`).

## Boundaries

- **Not the bulk executor.** This skill ends when the machine is built and the pilot
  slice passes; large-scale execution belongs to implementing sessions or loops built
  with crafting-self-prompting-loops, which consume the pack.
- **Honest about "no".** If no judge can be built, the answer is "don't migrate yet" —
  recommend the precursor work instead of proceeding on vibes.
- **Parity over aesthetics.** For money movement, fees, schedules, reconciliation, and
  edge-case state machines, behavioral parity is the bar; clean-looking code proves
  nothing. Use spec-first-planning when the ask turns out to be new behavior rather
  than preserved behavior — a migration that changes the contract is a feature.
