---
name: agent-ready-rails
description: >-
  Read-only audit that scores how ready a repository is for coding agents to work in it safely and
  successfully, then optionally installs the missing rails. Use when someone asks "is our repo ready
  for agents / Claude Code / a background coding agent?", "why do agents keep failing / making messy
  PRs in this codebase?", "how do we get agents to actually merge working changes?", or wants to set a
  codebase up so autonomous/background agents produce verified, reviewable work instead of fast chaos.
  It grades six rails — runnable verifiers (the format/build/test feedback loop), green-CI ground
  truth, a copyable house style, navigable context, scoped trusted tools, and human checkpoints with
  reversibility — from observed repo evidence, and emits a severity-ranked readiness scorecard with a
  prioritized fix list. Grounded in Spotify's Honk case and this repo's loop/context skills. Not a
  linter, SAST, or CI-config generator; it audits the engineering system agents run inside, not the
  code's correctness (use base-in-reality for that) or a single loop's soundness (use
  crafting-self-prompting-loops for that).
---

# agent-ready-rails

A read-only audit of whether a codebase is **ready for coding agents** — and a scaffolder for the rails it is missing.

The thesis, from the best-documented production case (Spotify's Honk agent over ~20M lines; see `references/grounding.md`): **a powerful agent in a weak engineering system creates faster chaos; the same agent in a disciplined system creates leverage.** The differential is not model capability — it is the *rails* around the agent. This skill scores those rails and tells you which to build first.

Default mode is **read-only**: observe, score, report. It only writes files when explicitly asked to install rails (step 5).

## When to use

Reach for this when the question is about the *environment* agents work in, not a specific change: "is our repo agent-ready?", "why do agents keep producing broken or messy PRs here?", "what do we fix so a background agent can merge verified work unattended?". It complements the sibling skills rather than overlapping them — see the relationship table in `references/grounding.md`. Do **not** use it to audit code correctness (that's `base-in-reality`) or to design/repair one agent loop (that's `crafting-self-prompting-loops`); this skill audits the *system* those run inside.

## The six rails

Full probe/score/fix detail is in `references/rubric.md`; the evidence for each is in `references/grounding.md`. In brief:

| ID | Rail | One-line test | Load-bearing? |
|----|------|---------------|---------------|
| R1 | Runnable verifiers | one discoverable command runs format+lint+build+test | **yes** — it *is* the loop |
| R2 | Green-CI ground truth | CI runs those checks, gates merge, is reproducible | **yes** |
| R3 | House style to copy | enforced formatter/linter + conventions doc + idiomatic exemplars |  |
| R4 | Navigable context | a current map + responsibility-named boundaries |  |
| R5 | Scoped trusted tools | least-privilege tools, gated destructive actions, trifecta broken |  |
| R6 | Human checkpoints | PR + meaningful review gate + small reversible diffs + cheap rollback |  |

Each rail scores **0 (absent) / 1 (partial) / 2 (agent-grade)**. The total is 0–12, but the **minimum rail matters more than the sum** — R1/R2 are load-bearing, so a repo strong everywhere except the verify→repair loop is still not agent-ready. Lead the report with the weakest rail.

## Workflow

Follow these steps in order. Steps 1–4 are read-only and always run; step 5 writes only on explicit request; step 6 verifies the writes.

### 1. Scope and detect the stack

Establish the languages, build system, and where an agent would look for instructions (`README`, `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, task runner). State what you found in one line. If the repo is a monorepo, pick the target package(s) and say so.

### 2. Probe each rail from observed evidence

For each of R1…R6, gather the concrete signals named in `references/rubric.md`. Read configs and CI files; check that named commands actually exist. **Run nothing destructive** — confirm a verifier *exists* and is wired, don't execute migrations or deploys. Any rail you cannot back with a file/line is scored as absent and tagged `UNVERIFIED`; never credit a rail on a README claim alone.

### 3. Score against the rubric

Assign each rail 0/1/2 strictly from step-2 evidence, citing the path (and line where useful) that justifies the score. When evidence is mixed, score to the weakest blocking signal, not the average — a build command that needs an unobtainable secret to run is a 1, not a 2.

### 4. Emit the readiness scorecard

Produce the deliverable below: the per-rail scores, the load-bearing minimum called out first, and a **prioritized fix list** ordered by leverage (load-bearing rails first, then lowest-scoring). Each fix is concrete and tied to the rail's `Fix` guidance. This is the default output — stop here unless asked to build.

### 5. (Optional) Install the rails — only on request

If the user asks you to *fix* or *set up* the rails, implement the prioritized fixes: e.g. add a single `verify` entrypoint (R1), wire CI to it (R2), write a conventions doc + exemplars (R3), add an architecture map (R4). Keep each change small and reviewable, and route it through the repo's own PR flow. Defer R5/R6 loop-design specifics to `crafting-self-prompting-loops` rather than reinventing them.

### 6. Re-verify what you installed

After any install, re-run the relevant probes and confirm the rail now scores higher — and that the new `verify` entrypoint actually runs and gates. Report the before/after scores. Do not claim a rail is fixed without re-checking it.

## Output template

ALWAYS structure the scorecard like this:

```
## Agent-readiness: <repo> — <total>/12  (weakest: R<n> <rail>)

| Rail | Score | Evidence | Gap |
|------|-------|----------|-----|
| R1 Runnable verifiers | 0/1/2 | <path:line> | <one line> |
| R2 Green-CI ground truth | … | … | … |
| R3 House style | … | … | … |
| R4 Navigable context | … | … | … |
| R5 Scoped trusted tools | … | … | … |
| R6 Human checkpoints | … | … | … |

### Verdict
<one line: is this repo agent-ready? the load-bearing R1/R2 status decides it>

### Prioritized fixes (highest leverage first)
1. <rail> — <concrete fix> — <why it moves the needle>
2. …

### Unverified
<rails claimed in docs but not backed by observable evidence>
```

## Why this matters

The instinct when agents underperform in a codebase is to reach for a smarter model or a cleverer prompt. The Honk evidence says the durable wins came from the engineering system instead: a verify→repair loop the agent can't stop short of (R1) took success from ~20–30% to ~80% with no model change. This skill exists to find which rail is missing *before* you spend that effort in the wrong place — and to make the fix concrete rather than aspirational. Hold the line on the read-only default: score honestly from evidence, and let the scorecard, not a hunch, drive what gets built.
</content>
