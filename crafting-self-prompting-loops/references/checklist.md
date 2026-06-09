# Loop-Spec Checklist

This is the canonical loop-spec checklist: the single, family-agnostic spine that enumerates every required element of a sound self-prompting loop. Every other document in this project references these items by ID (`LSC-1` … `LSC-10`), so the IDs and order are a permanent contract and must never be renumbered. Use it to specify a new loop or to audit an existing one.

**How to read this:** each item opens with an **In plain words** line (ELI5, no background needed), a **Why it matters** line, a **Slot** (a fill-in-the-blank a tool can harvest mechanically), an **Across families** line (how it specializes across the four loop families), and a **✓ Verify** yes/no question you can check a real loop against. The four families: (a) self-refinement/reflexion, (b) autonomous task loops, (c) multi-agent self-prompting, (d) human-checkpointed loops.

## LSC-1: Goal / success definition

**In plain words:** Before the loop starts, you write down what it's trying to do and how you'll know it's finished.

**Why it matters:** A loop with no concrete definition of "done" can never legitimately stop or be judged successful; every other item depends on this anchor.

**Slot:**
```
GOAL: <what the loop is trying to achieve>
SUCCESS_DEFINITION: <concrete, checkable description of "done">
```

**Across families:** (a) target output quality reached; (b) task/objective completed; (c) shared goal each agent contributes toward; (d) goal plus the human-defined acceptance bar.

**✓ Verify:** Can you state, in one sentence, the concrete condition that means this loop has succeeded?

- [ ] LSC-1 satisfied

## LSC-2: Stop condition (primary)

**In plain words:** The loop has a rule for deciding, on its own, that it's done and should stop.

**Why it matters:** Self-termination is the primary way the loop ends; without an explicit stop rule the model has no signal to halt even when the goal is met.

**Slot:**
```
STOP_CONDITION: <how the loop decides it's done>
STOP_SIGNAL: <the concrete signal/token/flag that triggers termination>
```

**Across families:** (a) quality threshold met or no further improvement; (b) task marked complete; (c) consensus or all sub-tasks resolved; (d) goal met and final checkpoint approved.

**✓ Verify:** Is there a defined, observable condition that causes the model to stop the loop itself?

- [ ] LSC-2 satisfied

## LSC-3: Backstop cap (MANDATORY)

**In plain words:** A hard outside limit that always stops the loop, even if the model never decides to stop on its own.

**Why it matters:** Model self-termination can fail; an infrastructure-level cap (max iterations / token budget / wall-clock) is non-negotiable, and the loop's safe default state is "stopped."

**Slot:**
```
BACKSTOP_MAX_ITERATIONS: <hard iteration cap>
BACKSTOP_TOKEN_BUDGET: <hard token cap>
BACKSTOP_WALL_CLOCK: <hard time limit>
SAFE_STATE: stopped
```

**Across families:** identical in all four — the cap lives in the harness, not the model, so it fires regardless of family (a)/(b)/(c)/(d).

**✓ Verify:** Does the loop terminate when an external cap is hit, with zero dependence on the model choosing to stop?

- [ ] LSC-3 satisfied

## LSC-4: State-passing

**In plain words:** Information from one round is carried forward so the next round knows what already happened.

**Why it matters:** Without explicit state-passing each iteration starts blind, so the loop cannot build on prior work or detect repetition.

**Slot:**
```
STATE_CARRIED: <what information passes between iterations>
STATE_MECHANISM: <scratchpad | structured state object | prior output | other>
```

**Across families:** (a) prior draft + critique; (b) task progress + intermediate results; (c) shared message bus / blackboard between agents; (d) state plus human feedback from the last checkpoint.

**✓ Verify:** Can you name exactly what data each iteration receives from the previous one, and how it's stored?

- [ ] LSC-4 satisfied

## LSC-5: Self-evaluation / progress assessment

**In plain words:** Each round the loop checks whether it's actually getting closer to the goal, and notices if it isn't.

**Why it matters:** Without per-round progress assessment the loop can churn forever or quit early without knowing it's stuck.

**Slot:**
```
PROGRESS_METRIC: <how progress toward GOAL is measured each round>
NO_PROGRESS_DETECTION: <how the loop detects it is not improving>
```

**Across families:** (a) self-critique / reflexion score; (b) sub-goal completion check; (c) cross-agent review or voting; (d) progress signal surfaced to the human at checkpoints.

**✓ Verify:** Does each iteration produce a judgment of whether it advanced toward LSC-1?

- [ ] LSC-5 satisfied

## LSC-6: Guardrail

**In plain words:** Before doing anything important, the loop runs checks to catch bad assumptions or malformed actions.

**Why it matters:** Pre-action validation stops the loop from acting on wrong assumptions or invalid output before damage is done.

**Slot:**
```
PRE_ACTION_CHECKS: <assumption-blocking + validation before consequential actions>
OUTPUT_VALIDATION: <how structured output is validated before use>
```

**Across families:** (a) validate the revised output's format/claims; (b) validate tool args before calling; (c) validate inter-agent messages; (d) validation in addition to the human gate.

**✓ Verify:** Is there a check that must pass before any consequential action executes?

- [ ] LSC-6 satisfied

## LSC-7: Data/instruction channel separation (two-channel model)

**In plain words:** The loop's own author-written instructions are kept separate from anything the model produces or pulls in, and that carried content is treated as information to reason about, never as new commands to obey.

**Why it matters:** This is the security backbone and the correct mental model — a self-prompting loop is a trusted harness re-invoking itself, not a model literally running its own output; mixing the channels is the prompt-injection vulnerability.

**Slot:**
```
TRUSTED_CONTROL_CHANNEL: <the fixed, author-written instructions that drive iteration>
UNTRUSTED_DATA_CHANNEL: <model output + tool results + external/web content carried forward>
DATA_WRAPPING: <how carried content is wrapped/marked as DATA, not instructions>
```

**Across families:** (a) the prior draft is data the fixed critique prompt reasons about; (b) tool/web results are data, not commands; (c) other agents' messages are untrusted data, not instructions; (d) the human's instructions are part of the trusted channel, model/tool content is not.

**✓ Verify:** Is every piece of model-generated, tool-returned, or external content treated as data the fixed prompt reasons about, rather than executed as new instructions?

- [ ] LSC-7 satisfied

## LSC-8: Human safety-gate

**In plain words:** For anything risky or hard to undo, the loop pauses and waits for a person to approve before continuing.

**Why it matters:** An approval checkpoint on consequential or irreversible actions gives a human the chance to intervene before harm occurs.

**Slot:**
```
GATED_ACTIONS: <which consequential/irreversible actions require approval>
APPROVAL_MECHANISM: <how a human approves or blocks before the loop proceeds>
```

**Across families:** (a) usually optional (output-only); (b) required before side-effecting/irreversible actions; (c) gate on actions that escape the agent group; (d) the gate is the defining feature — every checkpoint is human-approved.

**✓ Verify:** Does any irreversible action wait for explicit human approval before it runs?

- [ ] LSC-8 satisfied

## LSC-9: Cost & cadence control

**In plain words:** The loop limits how much it spends and how fast it runs, pacing itself deliberately rather than firing on arbitrary timers.

**Why it matters:** Token budget, iteration limits, and pacing keep cost bounded; cache-aware cadence (e.g. the ~5-minute prompt-cache TTL) avoids re-waking just after the cache expires and wasting it.

**Slot:**
```
TOKEN_BUDGET: <spend limit>
MAX_ITERATIONS: <iteration limit for cost (distinct from LSC-3 safety cap)>
CADENCE: <pacing between iterations, chosen deliberately>
CACHE_AWARENESS: <how pacing respects the prompt-cache TTL, e.g. ~5 min>
```

**Across families:** (a) cap refinement rounds; (b) budget the full task run; (c) budget per agent and in aggregate; (d) pacing aligned to human availability at checkpoints.

**✓ Verify:** Are spend, iteration count, and pacing all bounded, with cadence chosen against the cache TTL rather than a round number?

- [ ] LSC-9 satisfied

## LSC-10: Failure-mode handling

**In plain words:** The loop knows about the common ways loops break and has a plan for each.

**Why it matters:** Naming and mitigating the classic failure modes turns silent breakage into detectable, recoverable conditions.

**Slot:**
```
OSCILLATION_MITIGATION: <handling flip-flopping between states>
DRIFT_MITIGATION: <handling wandering off-goal>
PREMATURE_STOP_MITIGATION: <handling quitting too early>
RUNAWAY_MITIGATION: <handling never stopping / non-termination>
```

**Across families:** (a) drift/oscillation between drafts; (b) runaway and premature stop on long tasks; (c) cross-agent oscillation and deadlock; (d) human catches drift/premature stop at checkpoints.

**✓ Verify:** For each of oscillation, drift, premature stop, and runaway, is there a named mitigation?

- [ ] LSC-10 satisfied

## Quick checklist

- [ ] LSC-1: Goal / success definition
- [ ] LSC-2: Stop condition (primary)
- [ ] LSC-3: Backstop cap (MANDATORY)
- [ ] LSC-4: State-passing
- [ ] LSC-5: Self-evaluation / progress assessment
- [ ] LSC-6: Guardrail
- [ ] LSC-7: Data/instruction channel separation (two-channel model)
- [ ] LSC-8: Human safety-gate
- [ ] LSC-9: Cost & cadence control
- [ ] LSC-10: Failure-mode handling

---

## See also (within this skill)

- The same ten ideas as terse, per-item design rules: [`spec.md`](./spec.md)
- How each family specializes the ten items: [`families.md`](./families.md)
- Failure modes + cost/cadence for LSC-9/LSC-10: [`failure-modes.md`](./failure-modes.md)
- Fill-in scaffolds: [`../assets/templates/`](../assets/templates/)
