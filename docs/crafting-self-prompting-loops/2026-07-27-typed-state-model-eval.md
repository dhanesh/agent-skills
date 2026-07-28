# crafting-self-prompting-loops — typed loop boundary (LSC-4): blind A/B model eval

**Date:** 2026-07-27 · **Design:** 3 loop-shaped requests × 2 blinded skill variants ×
2 model tiers = **12 generation runs + 12 judge runs** · **Judges:** fresh-context, one per
artifact, blind to arm identity · **Baseline:** `23dc485` (pre-change) vs the LSC-4
typed-state commit.

Per `docs/eval-standard.md`, gate evals stay model-free; this is the **manual, documented
protocol** that covers what a deterministic gate cannot — whether prompt guidance changes what
a model actually builds. `make ab-validate` measures the code changes; for this prompt-only
skill it can only confirm the templates carry the slots, which is why this eval exists.

## Question

The change added `STATE_SCHEMA` + `STATE_VALIDATION` to LSC-4: declare the shape of the state
carried between iterations, and check it at the boundary before it becomes the next round's
premise. Does that change what a model *produces* — and does it cost any existing safety
property?

## Method

Both variants were staged under neutral names (`variant-m`, `variant-k`) in temp directories.
No generator was told a comparison existed, which arm it had, or what the change was; each saw
only one skill directory and a user request. The arms were verified to differ **only** in the
LSC-4 change (9 files: SKILL.md, spec.md, checklist.md, failure-modes.md, 5 templates) with
nothing else varying.

Artifacts were then re-keyed to opaque hashes (`artifact_<sha1>`) before judging, so no judge
could infer provenance from a filename. Each judge graded exactly one artifact against five
fixed assertions with instructions to be strict and to treat gesturing prose as a FAIL. Arm
identity existed only in a mapping file held by the harness.

**Requests** (loop-shaped tasks where typed state is relevant but never asked for):

| | task |
|---|---|
| t1 | four agents auditing a codebase in parallel, merging into one shared findings list, until nothing new turns up |
| t2 | an unattended overnight loop working a dependency-upgrade checklist until every item is done |
| t3 | keep rewriting an onboarding doc until it's good |

**Assertions** — Δ = what the change should add, R = what must not be lost:

- **Δ A1** declares the SHAPE/type of state carried between iterations (not prose)
- **Δ A2** validates that state at the iteration boundary — *explicitly excluding* validation
  of a single tool result at its point of use, which LSC-6 already covered
- **Δ A3** names the on-violation path (repair / re-ask / halt)
- **R A4** hard-stop backstop enforced outside the model
- **R A5** trusted/untrusted two-channel boundary for carried content

## Results

Both model tiers produced the **same** table, independently judged:

| | Opus 5 baseline | Opus 5 candidate | Haiku 4.5 baseline | Haiku 4.5 candidate |
|---|---|---|---|---|
| Δ A1 declares state shape | 3/3 | 3/3 | 3/3 | 3/3 |
| Δ A2 validates at the boundary | **2/3** | **3/3** | **2/3** | **3/3** |
| Δ A3 names on-violation path | **2/3** | **3/3** | **2/3** | **3/3** |
| R A4 backstop | 3/3 | 3/3 | 3/3 | 3/3 |
| R A5 two-channel | 3/3 | 3/3 | 3/3 | 3/3 |
| **ALL** | **13/15** | **15/15** | **13/15** | **15/15** |

**Zero regressions on both tiers** — the new slots displaced neither mandatory safety property.

## What actually moved, and what didn't

**The delta is task-driven, not model-driven.** On t1 and t3 the arms **tied at 5/5 on both
tiers**: the baseline already produced typed inter-round state unprompted, and the judges'
evidence is specific — a typed envelope
`{scope, findings:[...], files_examined:[...], status:"more"|"exhausted"}` with an
`envelope_ok(env)` boundary check (t1), and `open_defects[]/fixed_defects[] with stable IDs +
line anchors` under reject-and-keep-prior (t3). For those loop shapes this guidance is
**redundant**; the model supplies it.

The entire +2 sits in **t2, the unattended long-running loop**, and both tiers failed it for
the *same* reason — the one the change was built around. Opus baseline:

> The only schema check is OUTPUT_VALIDATION of a single model reply at its point of use …
> which is excluded; `state.json` itself is written and re-read … but never validated.

Haiku baseline:

> The failure paths described … all concern tool/test output or task progress, never a
> malformed state.

Both arms' baselines validated *an output* and left *the state that compounds* unchecked —
exactly the LSC-6/LSC-4 distinction. The candidates ran a validator
"at the iteration boundary, before the state becomes the next round's premise" and named
repair-or-halt with "Never 'continue anyway'".

This lands precisely where `failure-modes.md` §6b predicts ("most prone: long autonomous
runs"), which is mild evidence the failure-mode analysis is calibrated rather than decorative.

**A hypothesis that did not survive.** The Haiku arm was added expecting a *wider* gap on a
smaller model — the reasoning that produced this repo's `world-model-ledger` eval ("small model
→ room for context to matter"). It did not replicate: Haiku scored identically to Opus, 13/15 →
15/15, failing the same task on the same two assertions. The value of this change tracks the
**loop family**, not model capability. Recorded because the prediction was wrong and the
correction is the more useful finding.

## Honest limits

- **n=1 per cell, single judge per output.** A smoke test, not a benchmark. The replication
  across two tiers is the main reason to trust the t2 finding; the t1/t3 ties are single
  observations each.
- **The judges are LLMs.** Assertions were written to be checkable against quoted text and the
  judges did quote specifics, but no deterministic grader backs them.
- **Ceiling effects.** 13/15 baselines leave little headroom; a harder task set would
  discriminate better.
- **The generators are not knowledge-free.** Both models know loop design independently, which
  sharpens rather than weakens the t2 result — ambient knowledge alone did *not* produce
  state-level validation there.

## Verdict

Keep the change. It is a **narrow, replicated** win — +2/15 on both tiers, concentrated in
unattended long-running loops, with zero regression to the backstop or two-channel properties.
It is **not** a broad improvement, and the record should not be read as one: for
self-refinement and multi-agent shapes, capable models already do this unprompted.

## Reproducing

Variants are `git show 23dc485:crafting-self-prompting-loops/…` vs the working tree. The
harness (staging, anonymisation, judge prompts, aggregation) is not committed — it is a
one-off manual protocol per `docs/eval-standard.md`; the assertion set and method above are
what matter for a re-run.
