---
name: crafting-self-prompting-loops
description: >-
  Design and build sound self-prompting loops — loops where a model iterates toward a goal across turns
  (draft→critique→revise, autonomous task loops, multi-agent orchestration, human-checkpointed loops).
  Use this whenever the user wants to make a model "keep improving X until Y", build a self-refinement or
  reflexion loop, an autonomous agent that works toward a goal with little supervision, a loop that
  orchestrates or fans out to other agents, a recurring self-driven/scheduled task, or any agentic loop —
  even if they don't say the word "loop". Also use it to audit or fix an existing loop that runs away,
  never stops, oscillates, drifts off-goal, or is vulnerable to prompt injection. It produces a filled
  loop spec, a runnable scaffold, and bakes in the mandatory safety properties (a hard stop backstop and
  the trusted/untrusted two-channel boundary) that loops fail without.
---

# Crafting Self-Prompting Loops

A self-prompting loop is a **trusted harness that re-invokes a model with updated context each round** — it is *not* the model executing its own output as commands. The harness calls the model, reads the answer as **data**, decides whether to call again, and if so folds new information into the next prompt. Internalize that framing before building: it is both the correct mental model and the security model. Everything below follows from it.

Your job with this skill: turn a fuzzy "make it keep going until it's good" request into a **sound** loop — one that always terminates, never drifts unnoticed, never executes its own output as instructions, and stays within budget. Skipping these is how loops burn money, never stop, or get hijacked.

## The workflow

Follow these steps in order. The canonical 10-item spine is in `references/checklist.md` (items `LSC-1`…`LSC-10`); the terse per-item design rules are in `references/spec.md`. Read them if you need the full definitions — the summaries below are enough for most loops.

### 1. Nail the goal and the "done" test (LSC-1) — before anything else

Ask the user (or infer, then state your assumption): *what is this loop trying to achieve, and what concretely counts as "done"?* If you cannot write the success condition as one sentence an outside party could check, the loop is not ready — surface that gap rather than papering over it. A loop with no checkable "done" can never legitimately stop, and every later slot leans on this anchor.

### 2. Pick the family

Use the decision tree in `references/families.md`. Briefly: human in the path → **human-checkpointed**; one model critiquing its own output → **self-refinement**; one agent driving other agents → **multi-agent**; one agent driving itself toward a goal → **autonomous**. State which family you picked and why. When blended, pick the dominant shape and graft the extra slot.

### 3. Fill the loop spec — all 10 slots

Start from the family's template in `assets/templates/` (or `base-loop.template.md` if unsure). Fill every slot. Don't leave a slot as "TODO" — an unfilled slot is a latent failure. The ten:

| # | Slot | What to decide |
|---|------|----------------|
| LSC-1 | Goal / success definition | the checkable "done" from step 1 |
| LSC-2 | Stop condition (primary) | how the model signals "done" (a flag/token the harness observes — never infer from free text) |
| LSC-3 | **Backstop cap (mandatory)** | a hard outside limit (max iterations / token budget / wall-clock) that fires regardless of the model |
| LSC-4 | State-passing | the *minimum* carried forward to build on the last round and detect repetition |
| LSC-5 | Self-evaluation | a per-round progress judgment + a no-progress detector |
| LSC-6 | Guardrail | validation that runs *before* any consequential action |
| LSC-7 | **Two-channel separation** | trusted control (your fixed scaffold) vs untrusted data (model output, tool results, external text) |
| LSC-8 | Human safety-gate | which consequential/irreversible actions need approval (may be N/A for output-only loops) |
| LSC-9 | Cost & cadence | token budget, cost-iteration limit, deliberate pacing |
| LSC-10 | Failure-mode handling | a named mitigation for oscillation, drift, premature stop, runaway |

### 4. Enforce the three non-negotiables

These are the constraints loops most often skip and most often die on. Never ship a loop without them:

- **A mandatory backstop (LSC-3).** Model self-termination (LSC-2) *can fail* — the model may never decide to stop. So the harness must hold a hard cap that fires regardless. The safe state is always `stopped`: on any cap trip or uncertainty, halt. "The model will stop itself" is not a termination strategy.
- **The two-channel boundary (LSC-7).** Anything the model produces, a tool returns, or comes from outside (web, files, other agents) is **untrusted data** — wrap it (e.g. in a delimited `<data>…</data>` block) and have the fixed prompt reason *about* it. Never splice it into the control channel as new instructions. This is the prompt-injection defense; it's also just the correct model of what a loop is.
- **A human gate where it matters (LSC-8).** Any irreversible or externally-visible action (spending, deletion, posting, deploys, real-world effects) waits for explicit human approval. Output-only loops may legitimately skip this — say so explicitly rather than silently omitting it, so a reader knows it was a decision, not an oversight.

### 5. Emit the deliverable

Produce two things:

1. **The filled spec** — the 10 slots with concrete values, plus the chosen family and a one-line rationale.
2. **A runnable scaffold** — in the user's target runtime. For Claude Code, that's the real primitives: `/loop` (recurring, omit the interval to self-pace), `ScheduleWakeup` (self-paced cadence; respect the ~5-min prompt-cache TTL — poll <270s, idle 1200–1800s, avoid exactly 300s), `Workflow` (multi-agent fan-out/pipeline), `AskUserQuestion` (the human gate). If the runtime is unknown or generic, emit framework-agnostic pseudocode and say so. Always make the backstop and the `<data>` wrapping *visible* in the scaffold, not implied.

### 6. Sanity pass against the failure modes

Before you call it done, walk `references/failure-modes.md` and check the loop against each mode relevant to its family: oscillation, drift, premature stop, runaway, prompt-injection, context/state bloat, cost blowout, multi-agent deadlock. For each real risk, confirm the spec has a detector and a recovery. This is cheap and catches the problems that only show up at round 20.

## Audit mode (existing loops)

If the user has a loop already and it misbehaves, run steps 3–6 as a *checklist audit*: score the loop against LSC-1…LSC-10, and report which items are missing. The usual culprits, in order: **no backstop (LSC-3)** → runaway; **channel mixing (LSC-7)** → injection/derailment; **no no-progress detector (LSC-5)** → oscillation/churn; **vague success test (LSC-1)** → premature or never-stopping. Name the gap and the specific slot to add.

## Output template

ALWAYS structure the result like this:

```
## Loop: <one-line goal>
Family: <family> — <why>

### Spec
LSC-1  Goal / success:    <...>
LSC-2  Stop condition:    <...>
LSC-3  Backstop:          <hard cap — MANDATORY>
LSC-4  State-passing:     <...>
LSC-5  Self-evaluation:   <...>
LSC-6  Guardrail:         <...>
LSC-7  Two-channel:       <trusted: ... | untrusted: ... | wrapping: ...>
LSC-8  Human gate:        <... or "N/A — output-only">
LSC-9  Cost & cadence:    <...>
LSC-10 Failure handling:  <oscillation / drift / premature-stop / runaway mitigations>

### Scaffold
<runnable code in the target runtime, backstop + <data> wrapping visible>

### Sanity check
<one line per relevant failure mode: detector + recovery>
```

## Why this matters

Self-prompting loops are easy to start and easy to get subtly wrong in ways that surface only after they've run a while or been fed adversarial input. The discipline here isn't ceremony — each slot maps to a real failure that has bitten real loops. The mandatory three (backstop, two-channel, gate) are mandatory because their absence is silent until it's expensive. Hold the line on them even when the user just wants something quick; a quick loop with a backstop is still quick.
