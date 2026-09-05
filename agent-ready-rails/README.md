# agent-ready-rails

An Agent Skill that **audits how ready a repository is for coding agents** — and optionally installs the rails it's missing. It grades six "build" rails (author → merge) from observed repo evidence and, when the goal is agents running unattended against production, four optional "operate" rails past the merge boundary. It emits a severity-ranked readiness scorecard and a prioritized, concrete fix list. Read-only by default; it only writes when you ask it to build the rails.

> This README is for humans browsing the folder. The agent-facing instructions live in [`SKILL.md`](./SKILL.md) — that's what Claude reads when the skill triggers.

## The thesis

From the best-documented production case — Spotify's *Honk* background coding agent over a ~20M-line codebase — the lesson is **not** "use more/smarter agents." It's that **a powerful agent in a weak engineering system creates faster chaos; in a disciplined system it creates leverage.** The differential is the *rails* around the agent, not the model. The headline data point: adding a verify→repair loop the agent can't stop short of took PR success from ~20–30% to ~80% with no model change.

This skill finds which rail your repo is missing *before* you spend effort in the wrong place.

## The rails — two tiers

**Tier 1 — Build rails (author → merge).** Always audited; they decide whether an agent can produce a verified, reviewable PR at all.

| ID | Rail | One-line test | Load-bearing? |
|----|------|---------------|---------------|
| R1 | Runnable verifiers | one discoverable command runs format+lint+build+test | **yes — it *is* the loop** |
| R2 | Green-CI ground truth | CI runs those checks, gates merge, is reproducible | **yes** |
| R3 | House style to copy | enforced formatter/linter + conventions doc + idiomatic exemplars |  |
| R4 | Navigable context | a current map + responsibility-named boundaries |  |
| R5 | Scoped trusted tools | least-privilege tools, gated destructive actions, lethal-trifecta broken |  |
| R6 | Human checkpoints | PR + meaningful review gate + small reversible diffs + cheap rollback |  |

**Tier 2 — Operate rails (merge → production).** Audited only when the goal is agents running *unattended against a production system*. For a repo just onboarding agents to open PRs, Tier 1 is the whole audit.

| ID | Rail | One-line test |
|----|------|---------------|
| R7 | Runtime observability & audit | agent runs traced + attributable to a distinct identity + replayable |
| R8 | Blast-radius containment | ephemeral least-privilege env, egress allowlisted, no standing prod creds/data |
| R9 | Deploy-path safety & kill switch | staged rollout + automated rollback + spend/rate caps + tested fleet stop |
| R10 | Continuous re-verification | rails re-checked over time + program metrics + autonomy pauses on regression |

Each rail scores **0 (absent) / 1 (partial) / 2 (agent-grade)**. The two tiers are reported as **separate subtotals** (Build 0–12, Operate 0–8) and never averaged together — a repo can be perfectly ready for PR work yet unsafe to run unattended. Within Tier 1 the **minimum rail matters more than the sum** — R1/R2 are load-bearing. Tier 2 leans more on established operational-safety/security practice than on direct Honk quotes; the grounding doc tags each finding accordingly.

## What it produces

A readiness scorecard: per-rail scores with the path/line evidence behind each, a verdict driven by the load-bearing R1/R2 status (and, when in scope, a plain yes/no on whether it is safe to run unattended), a leverage-ordered fix list, and an `Unverified` section for rails claimed in docs but not backed by observable evidence. On request, it also installs the highest-leverage rails through your repo's own PR flow and re-verifies them.

## When it triggers

"Is our repo ready for agents / Claude Code / a background coding agent?", "why do agents keep producing broken or messy PRs here?", "how do we get an agent to merge verified work unattended?", or setting a codebase up for autonomous/background agents.

It deliberately does **not** cover: code correctness against real-world norms (use [`base-in-reality`](../base-in-reality/)), designing or repairing a single agent loop (use [`crafting-self-prompting-loops`](../crafting-self-prompting-loops/)), or in-session context management (use [`context-hygiene-kit`](../context-hygiene-kit/)). It audits the *environment* those run inside.

## Install & use

```bash
npx skills add dhanesh/agent-skills --skill agent-ready-rails
```

Once installed it auto-triggers on the contexts above; you can also invoke it explicitly when you're about to onboard agents into a codebase.

## Structure

```
agent-ready-rails/
├── SKILL.md                 the audit workflow (6 steps) + two-tier scorecard template
├── references/
│   ├── rubric.md            all ten rails: probe / 0-1-2 scoring / fix, per rail (Tier 1 R1–R6, Tier 2 R7–R10)
│   └── grounding.md         the Honk case + research grounding + sibling-skill map
└── README.md                this file
```

## Related

[**Manifold**](https://github.com/dhanesh/manifold) is one concrete way to raise the
runnable-verifier rails this audit scores (R1/R2). It stores a feature's constraints in the
repo as typed, checkable objects, then proves each one is actually satisfied —
`manifold verify --verify-evidence` fails when a requirement has no evidence behind it, which
is exactly the "the agent can't stop short of the loop" property the Honk case turns on.

## Provenance

The rails and their evidence come from Spotify Engineering's *Background Coding Agents* series (*Context Engineering*, Nov 2025; *Feedback Loops*, Dec 2025) and the June 2026 Niklas Gustavsson × Boris Cherny interview ([youtu.be/9DHZLw5653E](https://youtu.be/9DHZLw5653E)), cross-referenced against the research grounding already in this repo's [`crafting-self-prompting-loops`](../crafting-self-prompting-loops/) and [`context-hygiene-kit`](../context-hygiene-kit/) skills. Headline metrics are vendor-reported — a strong practitioner signal, not an independently replicated result. See [`references/grounding.md`](./references/grounding.md) for the per-rail citations and tags.
</content>
