# crafting-self-prompting-loops

An Agent Skill that helps Claude **design and build sound self-prompting loops** — loops where a model iterates toward a goal across turns (self-refinement, autonomous task loops, multi-agent orchestration, human-checkpointed loops). Given a goal, it picks the right loop family, fills a 10-item spec, emits a runnable scaffold, and bakes in the safety properties loops fail without: a mandatory hard-stop backstop and the trusted/untrusted two-channel boundary.

> This README is for humans browsing the folder. The agent-facing instructions live in [`SKILL.md`](./SKILL.md) — that's what Claude reads when the skill triggers.

## What it produces

For any "make a model keep doing X until Y" request, the skill returns:

1. **A filled loop spec** — the 10 LSC slots with concrete values + the chosen family and why.
2. **A runnable scaffold** — in the target runtime (Claude Code primitives, or framework-agnostic pseudocode), with the backstop and `<data>` wrapping visible, not implied.
3. **A failure-mode sanity pass** — a detector + recovery for each relevant failure mode.

It also runs in **audit mode**: score an existing loop against the checklist and flag what's missing (the usual culprit being a missing backstop or channel mixing).

## When it triggers

Building or fixing: "keep improving X until Y", self-refinement / reflexion cycles, an autonomous agent that works toward a goal unsupervised, an orchestrator that drives other agents, a recurring self-driven/watch-until-done job — even when the user never says "loop". Also for repairing a loop that runs away, never stops, oscillates, drifts, or obeys instructions hidden in its own inputs.

It deliberately does **not** trigger for ordinary code loops (`for`/`while`, retries), UI re-render bugs, fixed email/marketing schedules, or explaining RL training loops.

## Install & use

```bash
npx skills add dhanesh/agent-skills --skill crafting-self-prompting-loops
```

Once installed it auto-triggers on the contexts above. Because Claude tends to handle "design a loop" requests directly, the most *reliable* way to invoke it is to reach for it explicitly when you're about to design or audit a loop.

## Structure

```
crafting-self-prompting-loops/
├── SKILL.md                     the craft workflow (6 steps) + audit mode + output template
├── references/
│   ├── checklist.md             the canonical 10-item spine (LSC-1..LSC-10)
│   ├── spec.md                  per-item design rules + decision criteria (terse, the extraction source)
│   ├── families.md              family-selection decision tree + per-family table + research refinements
│   ├── failure-modes.md         9 failure modes + cost/cadence playbook
│   ├── claude-code-primitives.md  native primitives (/goal, /loop, Routines) → which LSC slots each covers/leaves open
│   └── literature.md            research grounding: each finding → citation → which LSC slot it hardens
├── assets/templates/            5 fill-in scaffolds (base + one per family)
│   ├── base-loop.template.md
│   ├── self-refinement.template.md
│   ├── autonomous.template.md
│   ├── multi-agent.template.md
│   └── human-checkpointed.template.md
└── README.md                    this file
```

## The 10-item spine (LSC-1 … LSC-10)

Every reference, template, and output is keyed to these. A sound loop accounts for all ten:

| ID | Item | ID | Item |
|----|------|----|------|
| LSC-1 | Goal / success definition | LSC-6 | Guardrail (pre-action validation) |
| LSC-2 | Stop condition (primary) | LSC-7 | Two-channel data/instruction separation |
| LSC-3 | **Backstop cap (mandatory)** | LSC-8 | Human safety-gate |
| LSC-4 | State-passing | LSC-9 | Cost & cadence control |
| LSC-5 | Self-evaluation | LSC-10 | Failure-mode handling |

The non-negotiable three are **LSC-3** (a hard stop the model can't override), **LSC-7** (carried output is data, never instructions — the prompt-injection defense), and **LSC-8** (a human gate on consequential actions).

## Provenance & validation

Extracted from the `self-prompting-loop` learning tool in this repo (`docs/spec.md` is the source), then hardened against the research literature — see [`references/literature.md`](./references/literature.md), which ties every non-obvious design rule to a citation (Reflexion, Self-Refine, CRITIC, the "LLMs Cannot Self-Correct Reasoning Yet" negative result, Self-Consistency, ReAct, ADaPT, Voyager, the MAST multi-agent failure taxonomy, Anthropic's multi-agent cost findings, CaMeL, Spotlighting, the lethal trifecta, and the secure-pattern ladder). Validated with a with-skill vs baseline benchmark across four family test cases:

| Metric | With skill | Baseline |
|---|---|---|
| Assertion pass rate | **100%** | 59% |
| Avg tokens / run | 44k (±4k) | 73k (±104k) |

The skill's measured value concentrates where baselines silently fail: the **two-channel injection defense** (baseline missed it in all 4 cases) and **backstops the user didn't explicitly ask for**.

### Delta eval — Claude Code primitives update (July 2026)

When [`references/claude-code-primitives.md`](./references/claude-code-primitives.md) was added (folding in the Claude Code team's loop taxonomy: `/goal`, `/loop`, Routines), the change was smoke-tested with a blind A/B eval: 3 loop-shaped requests (goal-based, unattended cloud routine, PR polling) × 2 skill variants (before/after, staged under neutral names), each output graded by a fresh-context judge against a fixed checklist — 3 *delta* assertions per case (what the update should add) + 2 *regression* assertions (backstop, two-channel).

| Metric | Before | After |
|---|---|---|
| All assertions | 14/15 | **15/15** |
| Delta assertions | 8/9 | **9/9** |
| Regression assertions | 6/6 | 6/6 |
| Generation tokens (3 cases) | ≈188k | ≈192k |

The one before-variant failure was exactly the targeted gap: on a "keep working until tests pass" request it built a sound Stop-hook loop but never surfaced native `/goal`; the after-variant presented `/goal` first-class with its two caveats (transcript-only evaluator → the condition must show the check; the turn clause is a soft stop → real caps behind it). Caveats: n=1 per cell, single judge per output — a smoke test, not a benchmark; and the before-variant isn't knowledge-free (the agent environment exposes some primitives), which sharpens rather than weakens the finding — ambient knowledge alone did *not* surface `/goal`.
