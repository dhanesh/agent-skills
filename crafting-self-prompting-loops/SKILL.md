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

Follow these steps in order. The canonical 10-item spine is in `references/checklist.md` (items `LSC-1`…`LSC-10`); the terse per-item design rules are in `references/spec.md`; the research grounding for every non-obvious rule (citations → slot) is in `references/literature.md`. Read them if you need the full definitions — the summaries below are enough for most loops.

### 1. Nail the goal and the "done" test (LSC-1) — before anything else

Ask the user (or infer, then state your assumption): *what is this loop trying to achieve, and what concretely counts as "done"?* If you cannot write the success condition as one sentence an outside party could check, the loop is not ready — surface that gap rather than papering over it. A loop with no checkable "done" can never legitimately stop, and every later slot leans on this anchor.

**Then ask: does this even need a loop?** If the task has a verifiable or aggregatable answer, parallel sampling + majority vote (self-consistency) often beats iterative refinement at equal compute, and a one-shot call sidesteps every loop failure mode. Baseline that before committing to a loop — the cheapest sound loop is sometimes no loop.

### 2. Pick the family

Use the decision tree in `references/families.md`. Briefly: human in the path → **human-checkpointed**; one model critiquing its own output → **self-refinement**; one agent driving other agents → **multi-agent**; one agent driving itself toward a goal → **autonomous**. State which family you picked and why. When blended, pick the dominant shape and graft the extra slot.

### 3. Fill the loop spec — all 10 slots

Start from the family's template in `assets/templates/` (or `base-loop.template.md` if unsure). Fill every slot. Don't leave a slot as "TODO" — an unfilled slot is a latent failure. The ten:

| # | Slot | What to decide |
|---|------|----------------|
| LSC-1 | Goal / success definition | the checkable "done" from step 1 |
| LSC-2 | Stop condition (primary) | how the model signals "done" (a flag/token the harness observes — never infer from free text) |
| LSC-3 | **Backstop cap (mandatory)** | a hard outside limit (max iterations / token budget / wall-clock) that fires regardless of the model |
| LSC-4 | State-passing | the *minimum sufficient for correction* carried forward — enough to build on the last round and detect repetition, with feedback diagnostic enough to fix the next attempt (minimal noise, not minimal signal) |
| LSC-5 | Self-evaluation | a per-round progress judgment (with **external leverage** — tool/verifier or a separate evaluator, not pure self-grading) + a no-progress detector |
| LSC-6 | Guardrail | validation that runs *before* any consequential action |
| LSC-7 | **Two-channel separation** | trusted control (your fixed scaffold) vs untrusted data (model output, tool results, external text) |
| LSC-8 | Human safety-gate | which consequential/irreversible actions need approval (may be N/A for output-only loops) |
| LSC-9 | Cost & cadence | token budget, cost-iteration limit, deliberate pacing |
| LSC-10 | Failure-mode handling | a named mitigation for oscillation, drift, premature stop, runaway |

### 4. Enforce the three non-negotiables

These are the constraints loops most often skip and most often die on. Never ship a loop without them:

- **A mandatory backstop (LSC-3).** Model self-termination (LSC-2) *can fail* — the model may never decide to stop. So the harness must hold a hard cap that fires regardless. The safe state is always `stopped`: on any cap trip or uncertainty, halt. "The model will stop itself" is not a termination strategy.
- **The two-channel boundary (LSC-7).** Anything the model produces, a tool returns, or comes from outside (web, files, other agents) is **untrusted data** — wrap it (e.g. in a delimited `<data>…</data>` block) and have the fixed prompt reason *about* it. Never splice it into the control channel as new instructions. This is the prompt-injection defense; it's also just the correct model of what a loop is. But **wrapping is necessary, not sufficient** — delimiting only lowers injection probability, it doesn't remove it (Spotlighting; CaMeL). Back it architecturally: derive control flow from the trusted prompt before touching untrusted data, scope tools to least-privilege, and run the **lethal-trifecta check** — if the loop has private-data access + untrusted-content exposure + external-comms ability, break one leg. For tool/web/agent loops, reach for a secure pattern (Action-Selector → Plan-Then-Execute → Dual-LLM → …; see `references/spec.md` LSC-7).
- **A human gate where it matters (LSC-8).** Any irreversible or externally-visible action (spending, deletion, posting, deploys, real-world effects) waits for explicit human approval. Output-only loops may legitimately skip this — say so explicitly rather than silently omitting it, so a reader knows it was a decision, not an oversight.

### 5. Emit the deliverable

Produce two things:

1. **The filled spec** — the 10 slots with concrete values, plus the chosen family and a one-line rationale.
2. **A runnable scaffold** — in the user's target runtime. For Claude Code, that's the real primitives: `/goal` (condition-driven — a separate evaluator judges the condition each turn, but it only reads the transcript, so the condition needs a stated check; and it has **no native backstop**, so add a turn clause plus a real cap), `/loop` (recurring, omit the interval to self-pace; auto-expires after 7 days), `/schedule`/Routines (durable cloud cadence — runs with **no permission prompts**, so design the LSC-8 gate back in), `ScheduleWakeup` (self-paced cadence; respect the ~5-min prompt-cache TTL — poll <270s, idle 1200–1800s, avoid exactly 300s), `Workflow` (multi-agent fan-out/pipeline), `AskUserQuestion` (the human gate). The per-primitive slot coverage — what each fills and what it leaves open — is in `references/claude-code-primitives.md`. If the runtime is unknown or generic, emit framework-agnostic pseudocode and say so. Always make the backstop and the `<data>` wrapping *visible* in the scaffold, not implied.

### 6. Sanity pass against the failure modes

Before you call it done, walk `references/failure-modes.md` and check the loop against each mode relevant to its family: oscillation, drift, premature stop, runaway, prompt-injection, context/state bloat, cost blowout, multi-agent deadlock, evaluation degradation/sycophancy, and oversight degradation/rubber-stamping (human-checkpointed). For each real risk, confirm the spec has a detector and a recovery. This is cheap and catches the problems that only show up at round 20.

## Audit mode (existing loops)

If the user has a loop already and it misbehaves, run steps 3–6 as a *checklist audit*: score the loop against LSC-1…LSC-10, and report which items are missing. The usual culprits, in order: **no backstop (LSC-3)** → runaway; **channel mixing (LSC-7)** → injection/derailment; **self-grading with no external leverage (LSC-5)** → silent quality degradation/sycophancy (a model judging its own work ratifies it — Huang et al. 2023, Xu et al. 2024); **no no-progress detector (LSC-5)** → oscillation/churn; **vague success test (LSC-1)** → premature or never-stopping. For a misbehaving *multi-agent* loop, score against the MAST buckets (spec/design, inter-agent misalignment, verification — see `references/failure-modes.md` #8): the fix is almost always structural, not a better prompt. Name the gap and the specific slot to add. And when a single iteration's output fails the bar, don't stop at fixing that output — encode the lesson into the system (the verifier, skill, or spec slot) so every future iteration inherits it.

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
