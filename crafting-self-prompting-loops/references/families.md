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
