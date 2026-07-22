# Semantic review checklist — substance the gates can't see

Adapted from the **Agent review checklist** in this repo's
[`docs/prompting-playbook.md`](../../docs/prompting-playbook.md) (itself distilled from
"The Prompting Playbook" — Anthropic, Margot Van Laar). Read that doc for the full
grounding; this file is the operational version you run against one SKILL.md.

The mechanical gates verify **presence** — a heading exists, an output-word appears, a
verify-word appears. Every gate check is a deterministic *proxy*. This review closes
the gap by judging **substance**: a green `PLAYBOOK_RESULT: PASS` is the floor, not
the verdict.

## When to run it

After `make gate-skill SKILL=<dir>` is green and the authoring feels done — for a new
skill, and for any PR that touches a SKILL.md body. Findings are review notes, not
gate failures: most won't (and shouldn't) block CI, but each one deserves an explicit
keep-or-fix decision recorded in the PR.

## The checklist

Read the SKILL.md body and answer each row honestly. The "gate sees" column is what
already passed; the "you judge" column is what only a reader can decide.

| # | Layer | Gate sees (proxy) | You judge (substance) |
|---|-------|-------------------|------------------------|
| 1 | Prompt | ≥ 3 `## ` headings | Are the sections **orthogonal, single-concern labels** you could debug one at a time — or decorative headings on a wall of text? |
| 2 | Harness | ≥ 3 ordered units | Does each step do **one job**? Is verification a *separate* stage from generation, or does one step quietly fuse plan + execute + verify? |
| 3 | Harness | an output-word appears | Is there an **actual contract** — a named schema, fixed field list, or ordered format the downstream shares — or just the word "output"? |
| 4 | Loop | a verify-word appears | Is there a real **evaluator distinct from the generator, with a repair path** (generate → evaluate → repair) — or only a one-shot manual checklist? |
| 5 | Prompt | absolutist-word count | Is each `never`/`always` a **justified safety invariant**, or a **harmful overcorrection** that will make the model defensively refuse a legitimate request? (the Meridian distinction — purely semantic) |
| 6 | Context | body length + `references/` | Is the prose **actually redundant or bloated** (duplicate sentences, restated rules), or dense-but-necessary? Line count is not bloat. |

Two rows specific to this repo's authoring standard, beyond the playbook:

| # | Surface | You judge |
|---|---------|-----------|
| 7 | Description | Would the description **fire at the right moments and stay silent at the wrong ones**? It should front-load triggers (what it does, then "Use when …" with concrete user phrasings) and state boundaries ("Not for X — use `<sibling>`"). A description that only summarizes the skill under-triggers; one with no boundary over-triggers into a sibling's territory. |
| 8 | Eval | Does `eval/run_eval.py` grade the **skill's real promise**, or only the scaffolded contract surface it started with? Could a broken skill still pass it? The negative fixtures should encode the specific ways this skill can fail, not generic bad input. |

## The proxy is blind in both directions

A clean gate run can hide a real defect, and a failing one can be a false alarm —
keep these patterns from the playbook doc in mind while reviewing:

- **PP-5 false positive:** a safety skill stacks justified absolutes; the word-counter
  flags it, but the rule is correct. Confirm the absolute's *function* before acting.
- **PP-6 miss:** a short body can still carry duplicate sentences — real context
  bloat the line-counter can't see. Read for redundancy.
- **PP-4 keyword pass:** "verify"/"test" can appear for a *manual* checklist that
  isn't the automated evaluate→repair mechanism the convention asks for.
- **Inverse misses:** an overcorrection rule phrased without trigger words ("when
  unsure, decline") slips past PP-5; a rigorous protocol described as a "shape" or
  "contract" instead of PP-3's keywords can false-FAIL.

## Recording the outcome

Summarize in the PR (there is a checklist item for this in the repo's PR template):
one line per row that raised a finding — what you saw, and whether you fixed it or
are keeping it with a reason. "Rows 1–8 clean" is a fine summary when they are.
