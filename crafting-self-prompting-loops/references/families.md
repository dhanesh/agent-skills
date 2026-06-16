# Loop families — selection guide

Four families cover essentially all self-prompting loops. They are *specializations of the same 10-item checklist* (`references/checklist.md`), not different beasts — picking a family just tells you how each LSC slot tends to be filled and which failure modes to watch.

## Pick a family (decision tree)

```
Is a human required in the loop's path before it may act?
├── YES → human-checkpointed
└── NO
    ├── Does the work happen inside ONE model's repeated self-critique of its own output?
    │   └── YES → self-refinement / reflexion
    └── NO
        ├── Does ONE agent generate prompts/tasks for OTHER agents (fan-out, judge panel, orchestrator)?
        │   └── YES → multi-agent
        └── Otherwise (one agent drives itself toward a goal across turns) → autonomous task
```

A loop can blend families (e.g. an autonomous loop with a human gate on deploys). Pick the *dominant* shape for the scaffold, then add the relevant slot from the other family.

## Family reference

| Family | Loop shape | LSC-8 gate posture | Dominant failure modes (LSC-10) | Template |
|---|---|---|---|---|
| **Self-refinement / reflexion** | draft → critique → revise, until rubric passes or no improvement | usually optional (output-only) | oscillation, over-editing, drift from rubric | `assets/templates/self-refinement.template.md` |
| **Autonomous task** | goal → act → assess → (self-pace) → repeat until done/budget | **required** before side-effecting/irreversible actions | runaway cost, premature stop, drift | `assets/templates/autonomous.template.md` |
| **Multi-agent** | orchestrator spawns workers → collects → re-plans → loops | gate actions that escape the agent group | deadlock, cross-agent oscillation, over-fanout/cost | `assets/templates/multi-agent.template.md` |
| **Human-checkpointed** | act → gate (human approve?) → continue / abort | the **defining feature** — every checkpoint is human-approved | stalls waiting on humans; premature stop | `assets/templates/human-checkpointed.template.md` |

## Slot tendencies by family

- **State-passing (LSC-4):** self-refinement carries the prior draft + critique; autonomous carries task progress + intermediate results; multi-agent uses a shared blackboard/message bus; human-checkpointed adds the human's last decision.
- **Self-evaluation (LSC-5):** self-refinement = critique score; autonomous = sub-goal completion; multi-agent = cross-agent review/voting; human-checkpointed = progress surfaced to the human.
- **Stop condition (LSC-2):** self-refinement = quality threshold met or no improvement; autonomous = task complete; multi-agent = consensus / all sub-tasks resolved; human-checkpointed = goal met and final checkpoint approved.

Always start from `base-loop.template.md` if unsure — it carries the non-negotiables (LSC-3 backstop, LSC-7 two-channel) family-agnostically.

## Research-grounded refinements by family

Each rule below traces to a finding in [`literature.md`](./literature.md); apply them on top of the generic checklist.

**Self-refinement / reflexion (a):**
- *Before building the loop, check you need one.* For verifiable/aggregatable answers, parallel sampling + majority vote (self-consistency) often beats iterative refinement at equal compute — baseline it first (§B).
- *The evaluator needs external leverage.* Intrinsic self-critique degrades objective-task performance and self-bias compounds each round — prefer a tool/verifier, or a separate/blinded evaluator; never let the generator be its own judge (§A). This is the family's defining risk (failure mode #9).
- *Scope intrinsic-only refinement to subjective/stylistic outputs with a capable base model;* for reasoning, switch to tool-grounded critique.

**Autonomous task (b):**
- *Ground each step in a real observation* — reason→act→observe (ReAct) curbs drift and hallucination far better than reasoning from internal state (§C).
- *Decompose lazily.* Try to execute first; decompose a subtask only when it fails (ADaPT) — avoids the upfront task-list explosion that sank BabyAGI-style planners. Plan coarse up front for multi-step goals (Plan-and-Solve).
- *Reuse, don't re-derive.* Persist successful, self-verified action sequences as reusable skills and check the library first (Voyager); carry a memory of past failures into retries (Reflexion).

**Multi-agent (c):**
- *Default to a single agent.* Multi-agent burns ~15× the tokens and wins only when subtasks are independent/parallel **and** cheaply verifiable; tightly-coupled work (most coding) stays single-agent (§D).
- *Most failures are structural* (~42% spec/design, ~37% inter-agent misalignment, ~21% verification; MAST) — design termination, role boundaries, and verification as architecture, not prompt patches.
- *Use typed/structured message contracts* between agents (MetaGPT), *re-assert role + objective each turn* and halt on role inversion (CAMEL), and *add an independent verification stage* — producers hallucinate success (MAST FC3).

**Human-checkpointed (d):**
- *The checkpoint is your drift/premature-stop catch* — surface the original `SUCCESS_DEFINITION` alongside current state at every gate so the human can spot drift.
- *Guard against rubber-stamping* — give the human a concrete pass/fail bar to check, not "looks good?", and align idle cadence to human availability (long idle ticks; eat the cache miss).
