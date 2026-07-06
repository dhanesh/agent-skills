# world-model-ledger — with/without outcome eval

**Date:** 2026-07-01 · **Agent under test:** Haiku (small model → room for context to matter)
· **Design:** 3 tasks × 2 arms × 2 trials × 2 context conditions = **24 agent runs** ·
**Graders:** deterministic (substring + code execution), no LLM judge.

## Question

Does surfacing the world model's pre-call context (validated rules + open contradictions for
the file about to be edited) *measurably* change agent outcomes — and does it cause harm when
it has nothing relevant?

## Method

A synthetic Python project with **latent, non-local** issues, and a world model seeded with
what a prior session/human established. Two arms:

- **control** — repo + a generic placebo note (length-matched, no world-model content).
- **treatment** — repo + the **real `precall()` output** for the target file.

Two context conditions, to separate "salience" from "knowledge":

- **full** — the agent is shown the *whole repo* inline (both arms can discover the convention).
- **local** — the agent is shown *only the target file* (realistic single-file edit); the
  non-local fact is knowable to control only by exploring, and to treatment via the world model.

Three tasks (2 where the model holds a relevant fact, 1 neutral **harm check**):

| Task | What it tests | Pass criterion (deterministic) |
|---|---|---|
| **1 hash** | a validated *forbid* rule (no md5/sha1) | solution avoids md5/sha1 |
| **2 config** | an open *contradiction* (imports a deprecated module; migrate to `db.config`) | solution uses `db.config`, not `legacy_config` |
| **3 slugify** | model has **nothing** relevant | `slugify()` is functionally correct |

## Results (pass rate, control → treatment)

**Full context** (whole repo visible to both):

| Task | control | treatment |
|---|---|---|
| 1 hash | 2/2 | 2/2 |
| 2 config | 2/2 | 2/2 |
| 3 slugify | 2/2 | 2/2 |
| **all** | **6/6** | **6/6** |

→ **Ceiling effect.** When the convention is already legible in the repo, both arms comply;
the world model adds nothing measurable. (Even control migrated off the deprecated import.)

**Local context** (only the target file visible):

| Task | control | treatment | effect |
|---|---|---|---|
| 1 hash | 2/2 | 2/2 | **none** — control's default (`sha256`) already satisfied the *negative* rule; a forbid only helps if the agent would otherwise use the forbidden thing |
| 2 config | **0/2** | **2/2** | **decisive** — control kept `from db.legacy_config import …` (it had no way to know `db.config` exists); treatment migrated, driven by the surfaced contradiction + fix |
| 3 slugify | 2/2 | 2/2 | **no harm** — surfacing irrelevant rules did not degrade correctness |
| **all** | **4/6** | **6/6** | +2, entirely from task 2 |

## Interpretation (what this does and does not show)

**It shows, empirically:** the world model produces a **real outcome improvement precisely
when it carries non-local knowledge the agent wouldn't otherwise have** — task 2, local
context, **0% → 100%**. Control, seeing only `report.py` (which imports the deprecated loader
and gives no hint a replacement exists), kept using the deprecated module every time; treatment
migrated every time because the pre-call surfaced *"imports db/legacy_config — deprecated,
migrate to db/config.py."* And crucially, when the model had nothing relevant (task 3) it did
**no harm**.

**It does not show a blanket win.** Two null results are as informative as the positive one:
- **When the info is already in front of the agent** (full context, all tasks), the world model
  is redundant — outcomes are identical.
- **When the surfaced rule is a *negative* one the agent wasn't going to violate anyway**
  (task 1: control defaulted to `sha256`, already compliant), it changes nothing. A forbid rule
  only moves outcomes if the agent would otherwise have used the forbidden thing; the world
  model would help more here if the rule were stated positively ("use bcrypt").

## Threats to validity (stated plainly)

- **Small N.** 2 trials/cell; Haiku was near-deterministic here, so within-cell variance was
  low, but this is illustrative, not statistically powered.
- **Seeded model.** The world model was hand-seeded with correct conventions; in production,
  capture quality gates everything (the hooks capture only touched files + explicit markers, so
  a thin/incorrect model would help less or mislead). This eval measures *retrieval value given
  a good model*, not *capture quality*.
- **Small synthetic repo.** Real repos have more distractors; retrieval precision/noise at scale
  is unmeasured.
- **Task 1 was a weak discriminator** by construction (negative rule + a compliant default) — a
  limitation of the test design, not evidence against the model.

## Bottom line

On this eval, the world model **measurably improved outcomes on the non-local-knowledge task
(0%→100%), was neutral where knowledge was already present or the rule was moot, and caused no
harm** where it had nothing to say. That is the honest shape of the answer: it aids outcomes
*specifically* by delivering knowledge the agent doesn't already have in context — which is
exactly the cross-session / tribal-knowledge case a persistent world model exists to serve —
and it is not a general-purpose uplift on tasks whose facts are already visible.

Reproduce: `world-model-ledger/eval/harness.py` (generates the seeded model, contexts, and
prompts) + `world-model-ledger/eval/grade.py` (deterministic graders). The 24 agent runs used
Haiku; graders are model-free.
</content>
