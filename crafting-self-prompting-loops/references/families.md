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
| **Multi-agent** | orchestrator spawns workers → collects → re-plans → loops | gate actions that escape the agent group | deadlock, cross-agent oscillation, over-fanout/cost, false consensus | `assets/templates/multi-agent.template.md` |
| **Human-checkpointed** | act → gate (human approve?) → continue / abort | the **defining feature** — every checkpoint is human-approved | stalls waiting on humans; premature stop; oversight degradation / rubber-stamping | `assets/templates/human-checkpointed.template.md` |

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
- *Give the loop an escape from local edits.* Plain draft→revise is incremental-only and can't recover from a fundamentally wrong draft; add a bounded redraft/explore branch (restart from scratch) triggered when the evaluator flags a fundamental defect or progress stalls — best-of-N keeps the prior best. The limiting factor is discriminative judgment about what merits a redraft, so anchor the trigger to the external signal, not the generator (§C).

**Autonomous task (b):**
- *Ground each step in a real observation* — reason→act→observe (ReAct) curbs drift and hallucination far better than reasoning from internal state (§C).
- *Decompose lazily.* Try to execute first; decompose a subtask only when it fails (ADaPT) — avoids the upfront task-list explosion that sank BabyAGI-style planners. Plan coarse up front for multi-step goals (Plan-and-Solve).
- *Reuse, don't re-derive.* Persist successful, self-verified action sequences as reusable skills and check the library first (Voyager); carry a memory of past failures into retries (Reflexion).
- *Escape local optima by restarting, not just nudging.* When the loop stalls in a local optimum, cold-restart the whole loop N times and select the best by the external metric (ComPilot's best-of-5 lifted 2.66×→3.54×). This is a *complement* to the loop, distinct from self-consistency's vote over a one-shot answer — and it's task-dependent: only worth it when a cheap selector exists and the space is genuinely multi-modal, else greedy hill-climbing is as good for less cost (*Greedy Is a Strong Default*). Budget the linear cost multiple (LSC-9). See `literature.md` §C.
- *Watch for give-up-stop, the autonomous-loop-specific premature stop.* Tool-grounded loops more often quit too early (abandon a reachable goal) than run away; when the no-progress detector hasn't tripped, nudge the model to keep exploring before accepting its stop (failure mode #3).
- *Make feedback diagnostic, not just minimal.* Carry the minimum *sufficient for correction* — pass/fail without the reason wastes iterations (ComPilot: ~64% of proposals invalid/illegal; richer failure reasons were the named efficiency lever).

**Multi-agent (c):**
- *Default to a single agent.* Multi-agent burns ~15× the tokens and wins only when subtasks are independent/parallel **and** cheaply verifiable; tightly-coupled work (most coding) stays single-agent (§D).
- *Most failures are structural* (~42% spec/design, ~37% inter-agent misalignment, ~21% verification; MAST) — design termination, role boundaries, and verification as architecture, not prompt patches.
- *Use typed/structured message contracts* between agents (MetaGPT), *re-assert role + objective each turn* and halt on role inversion (CAMEL), and *add an independent verification stage* — producers hallucinate success (MAST FC3).
- *Consensus is not correctness.* Cross-agent agreement can be correlated error: debate amplifies shared biases after round 1, and agents converge confidently on the same wrong rationale. Don't use agreement as a quality signal — prefer a meta-judge or evidence-based arbitration that can pick a well-supported minority over an open debate that ratifies the majority, and keep rounds few (§D).

**Human-checkpointed (d):**
- *The checkpoint is your drift/premature-stop catch* — surface the original `SUCCESS_DEFINITION` alongside current state at every gate so the human can spot drift.
- *The gate is a fallible component — assume it degrades.* An unaided human gate loses accuracy *exactly when it matters*: reviewers grow more confident even when wrong, and agree with the agent more as it looks more capable (*Confirmation bias*, AAAI 2025). The fix that empirically helped: a concrete pass/fail bar + the strongest **disconfirming** / both-sided evidence, not the model's preferred plan; and don't lean on the gate as the sole catch as models scale — pair with an external verifier. (Full treatment plus the deferral and corrigibility levers in §F.)
- *Defer selectively — don't gate everything or nothing.* Human attention is a depletable budget; over-gating trains click-through, under-gating removes the catch. Gate on calibrated model uncertainty *and* irreversibility/stakes; batch or skip low-risk steps (learning-to-defer). Budget human queries like tokens and align idle cadence to human availability (long idle ticks; eat the cache miss).
- *Stay interruptible between gates (corrigibility).* The loop must accept correction *between* preplanned checkpoints, not only at them, and never be structured to race past a pending human decision; on interrupt, fold the human's input into trusted state as ground truth (Off-Switch Game; §F).
