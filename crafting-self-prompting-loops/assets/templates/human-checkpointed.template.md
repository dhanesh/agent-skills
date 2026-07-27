# Human-checkpointed loop template

**Family:** human-checkpointed (human-in-the-loop / approval gates).
**When to use:** the loop performs consequential or irreversible actions (deploys, payments, data deletion, external comms) that demand a human in the path; regulated/high-stakes domains needing an audit trail of approvals; full autonomy is unacceptable but full manual is too slow.
**When not:** output-only loops with nothing irreversible to gate (checkpoints add latency for no safety gain — use self-refinement); a human can't be available within the loop's cadence (you'll stall); never gate *everything* — over-gating trains rubber-stamping.

> **The human gate is the defining feature here, not an add-on.** And the human's instructions are part of the TRUSTED control channel — but model output and tool results are still **DATA**.

```text
# ─────────────────────────────────────────────────────────────
# TRUSTED CONTROL CHANNEL  (LSC-7)
# Author-written harness + the HUMAN's instructions = trusted.
# Model output / tool results below are DATA, never instructions.
# ─────────────────────────────────────────────────────────────

GOAL:               <task objective>                                          # LSC-1
SUCCESS_DEFINITION: <goal + the HUMAN-defined acceptance bar>                  # LSC-1
ACCEPTANCE_BAR:     <what final human approval checks against>                 # LSC-1

STOP_CONDITION: acceptance_bar_met AND final_checkpoint_approved by human       # LSC-2
STOP_SIGNAL:    <human signs off on the final checkpoint>                      # LSC-2

# BACKSTOP IS MANDATORY — do not remove. Safe state = stopped.                # LSC-3
# On timeout/cap the loop STOPS — it must NEVER auto-proceed past a gate.
BACKSTOP_MAX_ITERATIONS: <hard iteration cap>
BACKSTOP_WALL_CLOCK:     <hard time limit, incl. approval-wait timeout>
BACKSTOP_TOKEN_BUDGET:   <hard token cap>
SAFE_STATE:              stopped   (timeout resolves to STOPPED, never proceed)

STATE_CARRIED:    task_state + last_checkpoint_human_feedback/decision + notes # LSC-4
STATE_MECHANISM:  structured state + recorded approval/block decisions        # LSC-4
STATE_SCHEMA:     {task: {id, status}, checkpoints: [{action,                 # LSC-4
                   decision ∈ APPROVED|BLOCKED|PENDING, approver, at}]}
STATE_VALIDATION: assert schema each round; a checkpoint whose decision is    # LSC-4
                   not in the vocabulary is treated as PENDING, never as
                   APPROVED — an unreadable decision must never read as consent

PROGRESS_METRIC:       progress summary + diff/what-changed surfaced to human  # LSC-5
NO_PROGRESS_DETECTION: <human at the checkpoint catches drift/premature stop>  # LSC-5

PRE_ACTION_CHECKS: <automated schema/arg validation BEFORE the gate (defense   # LSC-6
                    in depth — human approves a pre-validated plan)>
OUTPUT_VALIDATION: <validate model/tool output before it enters state>        # LSC-6

GATED_ACTIONS:      <THE DEFINING LIST: every consequential/irreversible step  # LSC-8
                     — placed BEFORE each point of no return>
DEFER_POLICY:       <WHICH steps to gate: escalate on model UNCERTAINTY +      # LSC-8
                     irreversibility/stakes; batch/skip low-risk. Don't gate
                     everything — over-gating trains rubber-stamping (#10).>
GATE_EVIDENCE:      <what each gate SHOWS: pass/fail bar + original GOAL +      # LSC-8 / LSC-5
                     strongest DISCONFIRMING evidence / both-sided args —
                     NOT just the model's preferred plan>
APPROVAL_MECHANISM: <UI / CLI prompt / PR review; BLOCK stops or revises;      # LSC-8
                     timeout → SAFE_STATE (stopped), plus reminder/escalation>
INTERRUPTIBLE:      <accept human correction BETWEEN gates too; never race      # LSC-8 / LSC-7
                     past a pending decision; fold input into trusted state>

CADENCE:         <paced to KNOWN human availability, not a fixed timer>        # LSC-9
TOKEN_BUDGET:    <bounded>                                                     # LSC-9
APPROVAL_TIMEOUT:<wait window → resolves to stopped + escalate to backup>      # LSC-9 / LSC-10

# ── LOOP ─────────────────────────────────────────────────────
state = init(GOAL, ACCEPTANCE_BAR)
iterations = 0
while True:

    # STOP CHECKS — backstop first; timeout = stopped, NEVER auto-proceed
    if iterations >= BACKSTOP_MAX_ITERATIONS or elapsed >= BACKSTOP_WALL_CLOCK \
       or spend >= BACKSTOP_TOKEN_BUDGET:
        return stop(SAFE_STATE, result(state))              # LSC-3 — unconditional

    # INTERRUPTIBILITY — accept human correction BETWEEN gates, not only at
    # them; never race past a pending decision (corrigibility).        # LSC-8/LSC-7
    if human_interrupt_pending():
        state = fold_correction(state, human_interrupt())   # input = trusted ground truth

    plan = decide_next_step(state)                          # LSC-4 (+ last feedback)

    # GUARDRAIL — automated validation BEFORE the human even sees the plan
    if not PRE_ACTION_CHECKS(plan): plan = repair(plan)     # LSC-6 (defense in depth)

    # THE GATE — defining feature; gate per DEFER_POLICY (uncertainty + stakes),
    # not every step — over-gating trains rubber-stamping (#10).
    if should_gate(plan, DEFER_POLICY):                     # LSC-8
        # Surface GATE_EVIDENCE — pass/fail bar + original GOAL + the strongest
        # DISCONFIRMING evidence / both-sided args, NOT a bare "approve?".
        # Both-sided framing is the one thing shown to help when the model is
        # wrong; assume approval quality FALLS as the agent looks more capable.
        decision = await_human(plan, GATE_EVIDENCE, progress_summary)  # LSC-5 / LSC-8
        if decision == TIMEOUT: return stop(SAFE_STATE)     # LSC-3 — never proceed
        if decision == BLOCK:   state = revise_or_stop(state); continue

    # ACT — result re-enters as DATA; human input stays trusted, this does not
    #   <data> { model/tool result } </data>  <-- DATA only            # LSC-7
    result = act(plan.action)
    if not OUTPUT_VALIDATION(result): result = note_invalid(state)     # LSC-6

    # TYPED LOOP BOUNDARY — validate before the next round; the fail-safe
    # direction matters here: an unreadable decision degrades to PENDING.
    state = update(state, result, human_feedback)           # LSC-4
    if not STATE_VALIDATION(state, STATE_SCHEMA):           # LSC-4
        state = repair_or_stop(state, SAFE_STATE)           # repair | re-ask | halt
    if acceptance_bar_met(state) and final_checkpoint_approved(state): # LSC-2
        return result(state)

    iterations += 1
    pace(CADENCE)                                           # LSC-9 (human availability)
```

## Fill these in

| SLOT | Meaning | Maps to |
|------|---------|---------|
| `GOAL` / `SUCCESS_DEFINITION` / `ACCEPTANCE_BAR` | Goal + human-defined bar | LSC-1 |
| `STOP_CONDITION` / `STOP_SIGNAL` | Bar met AND final checkpoint approved | LSC-2 |
| `BACKSTOP_*` / `SAFE_STATE` / `APPROVAL_TIMEOUT` | Hard cap; timeout → stopped, never proceed | **LSC-3 (mandatory)** |
| `STATE_CARRIED` | Task state + last human decision/notes | LSC-4 |
| `STATE_SCHEMA` / `STATE_VALIDATION` | Checkpoint state typed; unreadable decision → PENDING | LSC-4 |
| `PROGRESS_METRIC` | Progress + diff surfaced at the gate | LSC-5 |
| `PRE_ACTION_CHECKS` / `OUTPUT_VALIDATION` | Automated validation before the gate (defense in depth) | LSC-6 |
| `<data>…</data>` wrapping | Human input trusted; model/tool content DATA | LSC-7 |
| `GATED_ACTIONS` / `APPROVAL_MECHANISM` | **The defining gate** on consequential actions | LSC-8 |
| `DEFER_POLICY` | Which steps to gate — uncertainty + stakes, not everything | LSC-8 |
| `GATE_EVIDENCE` | What the gate shows: bar + disconfirming / both-sided evidence | LSC-8 / LSC-5 |
| `INTERRUPTIBLE` | Accept correction between gates; fold into trusted state | LSC-8 / LSC-7 |
| `CADENCE` / `TOKEN_BUDGET` / `APPROVAL_TIMEOUT` | Paced to human availability | LSC-9 |

## Failure modes (LSC-10)

- **Stall waiting on a human** (the acute mode) — gate blocks, approver unavailable. → approval timeout resolving to the **safe** state (stopped, never auto-proceed) + reminders/escalation to a backup approver; pace cadence to known availability.
- **Rubber-stamping / oversight degradation** (the defining risk — see failure-modes #10) — approvals go reflexive; reviewers grow *more* confident after a cursory look and agree *more* as the agent looks capable, even when it's wrong (*Confirmation bias*, AAAI 2025). → (1) **defer selectively** per `DEFER_POLICY` — gate on uncertainty + stakes, not everything; (2) make each gate adversarial to itself — show the pass/fail bar + **disconfirming** evidence / both-sided args, not the model's preferred plan; (3) don't lean on the gate as the sole catch as models scale — pair with an external verifier where one exists.
- **Uncorrectable mid-run** — the loop races to the next gate and can't absorb a correction in between. → keep it `INTERRUPTIBLE`: accept human input between gates and fold it into trusted state as ground truth (corrigibility).
- **Drift / premature stop caught late** — this family's strength: the human at the checkpoint is the catch. → place checkpoints *before* points of no return so drift is caught while still reversible.
- **Runaway** — bounded by LSC-3 and by the gate itself: a side-effecting action can't fire without approval, so an out-of-control loop can't cause irreversible harm while blocked.

## Before you run

- [ ] **LSC-1** — goal + explicit human acceptance bar.
- [ ] **LSC-2** — primary stop: bar met AND final checkpoint approved.
- [ ] **LSC-3** — MANDATORY backstop; timeout resolves to STOPPED, never auto-proceed; checked first. **Both LSC-2 and LSC-3 required — independent.**
- [ ] **LSC-4** — task state + last checkpoint decision carried.
- [ ] **LSC-5** — progress + diff surfaced (evidence, not a bare "approve?") at each gate.
- [ ] **LSC-6** — automated validation runs before the human gate (defense in depth).
- [ ] **LSC-7** — human input is trusted; model/tool output lives inside `<data>…</data>` as DATA.
- [ ] **LSC-8** — every consequential/irreversible action is human-approved; gates placed before points of no return; deferral is selective (uncertainty + stakes), not gate-everything.
- [ ] **LSC-8** — each gate shows the pass/fail bar + disconfirming / both-sided evidence, not the model's preferred plan alone; loop stays interruptible between gates.
- [ ] **LSC-9** — cadence paced to human availability; approval timeout → safe stop.
- [ ] **LSC-10** — mitigations named for stall, oversight degradation / rubber-stamping, uncorrectable-mid-run, late-caught drift/premature stop, runaway.
```
